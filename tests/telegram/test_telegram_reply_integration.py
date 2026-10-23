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
from condor.telegram.output import runs as output_runs
from condor.telegram.output import writer as output_writer
from config_manager import UserRole
from handlers import agents
from tests.support.telegram import Bot as RecordingBot

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


class Bot(RecordingBot):
    def __init__(self):
        super().__init__()
        self.final_started = asyncio.Event()
        self.final_release = None

    async def edit_message_text(self, **kwargs):
        if kwargs.get("reply_markup") is None:
            self.final_started.set()
            if self.final_release is not None:
                await self.final_release.wait()
        return await super().edit_message_text(**kwargs)


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
    monkeypatch.setattr(output_writer, "_writers", {})
    monkeypatch.setattr(output_writer, "UPDATE_INTERVAL", 0)
    monkeypatch.setattr(output_runs, "_runs", {})
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
            "",
            conversation_id=chat.conversation_id,
        )
    )
    return task, admission


def stop_update(run_token, message_id):
    async def answer(*args, **kwargs):
        pass

    return SimpleNamespace(
        callback_query=SimpleNamespace(
            data=f"agent:stop_run:{run_token}",
            message=SimpleNamespace(message_id=message_id),
            answer=answer,
        ),
        effective_chat=SimpleNamespace(id=USER, type="private"),
        effective_user=SimpleNamespace(id=USER),
    )


def token(call):
    return call["reply_markup"].inline_keyboard[0][0].callback_data.split(":")[-1]


async def stop(run_token, message_id=101):
    await agents.handle_stop_callback(stop_update(run_token, message_id), None)


@pytest.mark.asyncio
async def test_inline_stop_preserves_partial_history_clears_queue_and_keeps_later_input(
    chat,
):
    bot = Bot()
    first, _ = start_turn(chat, bot, "first")
    await until(lambda: any("answer 0" in m["text"] for m in bot.current.values()))
    first_id = token(bot.messages[0])
    second, accepted = start_turn(chat, bot, "second")
    await until(lambda: accepted.accepted)
    assert output_runs.active_run(first_id, USER, USER).request_id == "first"
    assert len(chat.client.releases) == 1

    await stop(first_id)
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert len(chat.client.releases) == 1
    later, _ = start_turn(chat, bot, "later")
    await until(lambda: len(chat.client.releases) == 2)
    await until(lambda: any("answer 1" in m["text"] for m in bot.current.values()))
    # Telegram can redeliver the same Stop after the next response is visible.
    await stop(first_id)
    assert chat.client.cancelled == {0}
    chat.client.releases[1].set()
    await asyncio.wait_for(later, 1)

    final_texts = [message["text"] for message in bot.current.values()]
    assert final_texts == [
        "answer 0\n\n⏹ Stopped generating.",
        "Stopped before answering.",
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
    assert all(not writer.lock.locked() for writer in output_writer._writers.values())


@pytest.mark.asyncio
async def test_done_retires_stop_target_while_final_edit_owns_the_chat_writer(
    chat,
):
    first_bot, second_bot = Bot(), Bot()
    first_bot.final_release = asyncio.Event()
    first, _ = start_turn(chat, first_bot, "first")
    await until(
        lambda: any("answer 0" in m["text"] for m in first_bot.current.values())
    )
    first_id = token(first_bot.messages[0])
    chat.client.releases[0].set()
    await first_bot.final_started.wait()
    assert output_runs.active_run(first_id, USER, USER) is None
    assert not turns.is_busy(USER, chat.conversation_id)

    second, _ = start_turn(chat, second_bot, "second")
    await until(lambda: len(chat.client.releases) == 2)
    assert not second_bot.messages
    await stop(first_id)
    assert not chat.client.cancelled
    first_bot.final_release.set()
    await asyncio.wait_for(first, 1)
    await until(lambda: second_bot.messages)
    chat.client.releases[1].set()
    await asyncio.wait_for(second, 1)
    assert not output_runs._runs
    assert all(not writer.lock.locked() for writer in output_writer._writers.values())


@pytest.mark.asyncio
async def test_silent_acp_gets_inline_stop_and_clears_queued_turn_without_running_it(
    chat,
):
    acp = SilentACP()
    chat.client = acp.client
    bot = Bot()
    first, _ = start_turn(chat, bot, "silent-first")
    await until(lambda: bot.messages and acp.prompt_ids)
    first_id = token(bot.messages[0])
    assert bot.messages[0]["text"] == "Preparing response…"
    assert bot.messages[0]["parse_mode"] is None

    second, accepted = start_turn(chat, bot, "silent-second")
    await until(lambda: accepted.accepted)
    assert len(acp.prompt_ids) == 1
    assert output_runs.active_run(first_id, USER, USER).request_id == "silent-first"

    await stop(first_id)
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert len(acp.prompt_ids) == 1
    later, _ = start_turn(chat, bot, "silent-later")
    await until(lambda: len(acp.prompt_ids) == 2)
    await stop(first_id)
    assert len([m for m in acp.sent if m.get("method") == "session/cancel"]) == 1

    await acp.finish(acp.prompt_ids[1])
    await asyncio.wait_for(later, 1)
    assert any("Stopped" in message["text"] for message in bot.current.values())
    assert not chat.is_busy
    assert not turns.is_busy(USER, chat.conversation_id)
    assert not output_runs._runs
    assert all(not writer.lock.locked() for writer in output_writer._writers.values())
