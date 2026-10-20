"""Run-bound inline controls and output ordering across real runtime turns."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from condor.acp.client import ACPClient, PromptDone, TextChunk
from condor.runtime import (
    PromptRequest,
    SessionKey,
    confirmations,
    conversations,
    sessions,
    turns,
)
from condor.telegram.output import lease as output_lease
from condor.telegram.output import runs as output_runs
from config_manager import UserRole
from handlers import agents
from handlers.agents import stream

USER = 42


class Model:
    alive = True

    def __init__(self):
        self.releases = []
        self.cancelled = set()

    async def prompt_stream(self, text):
        index = len(self.releases)
        release = asyncio.Event()
        self.releases.append(release)
        yield TextChunk(text=f"answer {index}")
        await release.wait()
        yield PromptDone(
            stop_reason="cancelled" if index in self.cancelled else "end_turn"
        )

    async def abort_prompt(self):
        index = len(self.releases) - 1
        self.cancelled.add(index)
        self.releases[index].set()


class Bot:
    def __init__(self):
        self.progress = []
        self.messages = []
        self.final_started = asyncio.Event()
        self.final_release = None

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=len(self.messages))

    async def send_rich_message_draft(self, **kwargs):
        payload = dict(kwargs)
        payload["text"] = getattr(kwargs.get("rich_message"), "markdown", "")
        self.progress.append(payload)
        return True

    async def send_rich_message(self, **kwargs):
        payload = dict(kwargs)
        payload["text"] = getattr(kwargs.get("rich_message"), "markdown", "")
        self.messages.append(payload)
        if kwargs.get("disable_notification") is False:
            self.final_started.set()
            if self.final_release is not None:
                await self.final_release.wait()
        return SimpleNamespace(message_id=len(self.messages))

    async def edit_message_text(self, **kwargs):
        return True

    async def send_chat_action(self, **kwargs):
        return True

    async def delete_message(self, **kwargs):
        return True


class SilentACP:
    """A real ACP client whose subprocess emits no chunks before completion."""

    def __init__(self):
        self.client = ACPClient(command="fake-agent")
        self.client._process = SimpleNamespace(stdin=self, returncode=None)
        self.client._session_id = "silent-session"
        self.sent = []
        self.prompt_ids = []

    def write(self, data):
        message = json.loads(data)
        self.sent.append(message)
        if message.get("method") == "session/prompt":
            self.prompt_ids.append(message["id"])
        elif message.get("method") == "session/cancel":
            asyncio.create_task(self.finish(self.prompt_ids[-1], "cancelled"))

    async def drain(self):
        pass

    async def finish(self, request_id, stop_reason="end_turn"):
        await self.client._peer.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"stopReason": stop_reason},
                }
            ),
            self,
        )


@pytest.fixture
def chat(monkeypatch):
    monkeypatch.setattr(sessions, "_sessions", {})
    monkeypatch.setattr(turns, "_turns", {})
    monkeypatch.setattr(turns, "_sinks", [])
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(
        confirmations, "_registry", confirmations.ConfirmationRegistry()
    )
    monkeypatch.setattr(output_lease, "_tails", {})
    monkeypatch.setattr(output_runs, "_runs", {})
    monkeypatch.setattr(output_runs, "_by_draft", {})
    manager = SimpleNamespace(get_user_role=lambda user_id: UserRole.USER)
    monkeypatch.setattr("config_manager.get_config_manager", lambda: manager)
    monkeypatch.setattr("utils.auth.get_config_manager", lambda: manager)
    meta = conversations.new_conversation(USER, "tg")
    session = sessions.AgentSession(
        SessionKey.telegram(USER),
        "claude-code",
        Model(),
        user_id=USER,
        conversation_id=meta.id,
    )
    sessions._sessions[str(session.key)] = session
    return session


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


def start_turn(chat, bot, request_id):
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=USER),
        message=SimpleNamespace(message_id=10),
        effective_user=SimpleNamespace(id=USER),
    )
    admission = SimpleNamespace(accepted=False, release=lambda: None)
    task = asyncio.create_task(
        agents._stream_agent_turn(
            update,
            SimpleNamespace(bot=bot),
            admission,
            PromptRequest(text=request_id, request_id=request_id),
            None,
            "",
            conversation_id=chat.conversation_id,
        )
    )
    return task, admission


def stop_update(draft_id):
    return SimpleNamespace(
        stopped_message_generation=SimpleNamespace(
            chat=SimpleNamespace(id=USER),
            draft_id=draft_id,
            message_thread_id=None,
        ),
        effective_chat=SimpleNamespace(id=USER, type="private"),
        effective_user=SimpleNamespace(id=USER),
    )


def token(call):
    return call["draft_id"]


async def stop(draft_id):
    await agents.handle_stopped_generation(stop_update(draft_id), None)


@pytest.mark.asyncio
async def test_inline_stop_preserves_partial_history_and_runs_queued_successor(chat):
    bot = Bot()
    first, _ = start_turn(chat, bot, "first")
    await until(lambda: bot.progress)
    first_id = token(bot.progress[0])
    second, accepted = start_turn(chat, bot, "second")
    await until(lambda: accepted.accepted)
    assert output_runs.run_for_draft(USER, first_id).request_id == "first"
    assert len(chat.client.releases) == 1

    await stop(first_id)
    await asyncio.wait_for(first, 1)
    await until(lambda: len(chat.client.releases) == 2)
    await until(lambda: any(token(call) != first_id for call in bot.progress))
    # Telegram can redeliver the same Stop after the next response is visible.
    await stop(first_id)
    assert chat.client.cancelled == {0}
    chat.client.releases[1].set()
    await asyncio.wait_for(second, 1)

    final_texts = [
        message["text"]
        for message in bot.messages
        if message.get("rich_message") is not None
    ]
    assert final_texts == [
        "⏹ Stopped generating.\n\nPartial result:\nanswer 0",
        "answer 1",
    ]
    answers = [
        turn
        for turn in conversations.read_transcript(USER, chat.conversation_id)
        if turn.role == "assistant"
    ]
    assert [(turn.text, turn.stop_reason) for turn in answers] == [
        ("answer 0", "cancelled"),
        ("answer 1", "end_turn"),
    ]
    assert not turns.is_busy(USER, chat.conversation_id)
    assert not output_runs._runs
    assert not output_lease._tails


@pytest.mark.asyncio
async def test_done_retires_stop_target_and_next_progress_waits_for_formal_handoff(
    chat,
):
    first_bot, second_bot = Bot(), Bot()
    first_bot.final_release = asyncio.Event()
    first, _ = start_turn(chat, first_bot, "first")
    await until(lambda: first_bot.progress)
    first_id = token(first_bot.progress[0])
    chat.client.releases[0].set()
    await first_bot.final_started.wait()
    assert output_runs.run_for_draft(USER, first_id) is None
    assert not turns.is_busy(USER, chat.conversation_id)

    second, _ = start_turn(chat, second_bot, "second")
    await until(lambda: len(chat.client.releases) == 2)
    assert not second_bot.progress
    await stop(first_id)
    assert not chat.client.cancelled
    first_bot.final_release.set()
    await asyncio.wait_for(first, 1)
    await until(lambda: second_bot.progress)
    chat.client.releases[1].set()
    await asyncio.wait_for(second, 1)
    assert not output_runs._runs
    assert not output_lease._tails


@pytest.mark.asyncio
async def test_silent_acp_gets_immediate_inline_stop_without_queued_takeover(chat):
    acp = SilentACP()
    chat.client = acp.client
    bot = Bot()
    first, _ = start_turn(chat, bot, "silent-first")
    await until(lambda: bot.progress and acp.prompt_ids)
    first_id = token(bot.progress[0])
    assert bot.progress[0]["text"] == "Processing…"

    second, accepted = start_turn(chat, bot, "silent-second")
    await until(lambda: accepted.accepted)
    assert len(acp.prompt_ids) == 1
    assert output_runs.run_for_draft(USER, first_id).request_id == "silent-first"
    assert {token(call) for call in bot.progress} == {first_id}

    await stop(first_id)
    await asyncio.wait_for(first, 1)
    await until(lambda: len(acp.prompt_ids) == 2)
    await until(lambda: any(token(call) != first_id for call in bot.progress))
    second_id = next(token(call) for call in bot.progress if token(call) != first_id)
    assert output_runs.run_for_draft(USER, second_id).request_id == "silent-second"
    await stop(first_id)
    assert len([m for m in acp.sent if m.get("method") == "session/cancel"]) == 1

    await acp.finish(acp.prompt_ids[1])
    await asyncio.wait_for(second, 1)
    assert any("Stopped" in message["text"] for message in bot.messages)
    assert not chat.is_busy
    assert not turns.is_busy(USER, chat.conversation_id)
    assert not output_runs._runs
    assert not output_lease._tails
