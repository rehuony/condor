"""Socket delivery cannot retain the real runtime's conversation/session locks."""

import asyncio
import json

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import SessionKey, conversations, sessions, turns
from condor.web.routes import chat_ws

USER = 344


class _Socket:
    def __init__(self):
        self.sent = []

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))


class _StalledSocket(_Socket):
    def __init__(self, *, event_type=None):
        super().__init__()
        self.event_type = event_type
        self.attempts = 0
        self.sending = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def send_text(self, raw):
        if self.event_type and json.loads(raw)["event"] != self.event_type:
            await super().send_text(raw)
            return
        self.attempts += 1
        self.sending.set()
        await self.release.wait()
        await super().send_text(raw)

    async def close(self, **kwargs):
        self.closed.set()


class _Model:
    alive = True

    def __init__(self):
        self.finished = False

    async def prompt_stream(self, text):
        for index in range(20):
            yield TextChunk(text=f"part {index};")
        self.finished = True
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def chat(monkeypatch):
    monkeypatch.setattr(sessions, "_sessions", {})
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(turns, "_turns", {})
    monkeypatch.setattr(turns, "_sinks", [])
    monkeypatch.setattr(chat_ws, "_attached_sockets", {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_pending_spawns", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    monkeypatch.setattr(chat_ws, "_SOCKET_SEND_TIMEOUT", 0.02, raising=False)
    meta = conversations.new_conversation(USER, "web")
    session = sessions.AgentSession(
        SessionKey.web(USER, meta.id),
        "claude-code",
        _Model(),
        user_id=USER,
        conversation_id=meta.id,
    )
    sessions._sessions[str(session.key)] = session
    # Keep the real generators alive: GC must not make a missing aclose pass.
    streams = []
    prompt = chat_ws.runtime.prompt

    def capture(*args, **kwargs):
        stream = prompt(*args, **kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(chat_ws.runtime, "prompt", capture)
    return session, streams


async def _finish_test(task, stalled, streams):
    stalled.release.set()
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    for stream in streams:
        await stream.aclose()


def test_stalled_origin_is_retired_once_and_answer_finishes_on_other_tab(chat):
    session, streams = chat

    async def scenario():
        stalled, healthy = _StalledSocket(), _Socket()
        chat_ws._attached_sockets[USER] = {stalled, healthy}
        task = asyncio.create_task(
            chat_ws._handle_send_message(
                stalled,
                USER,
                {
                    "slot_id": session.conversation_id,
                    "text": "hello",
                    "client_id": "origin",
                },
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
            assert stalled.attempts == 1
            await asyncio.wait_for(stalled.closed.wait(), timeout=0.5)
            assert session.client.finished
            assert not session.is_busy and not session._lock.locked()
            assert not turns.is_busy(USER, session.conversation_id)
            assert [e["text"] for e in healthy.sent if e["event"] == "text_chunk"] == [
                f"part {index};" for index in range(20)
            ]
            assert healthy.sent[-1]["stop_reason"] == "end_turn"
            assert healthy.sent[-1]["client_id"] == "origin"
            transcript = conversations.read_transcript(USER, session.conversation_id)
            assert [turn.text for turn in transcript] == [
                "hello",
                "".join(f"part {index};" for index in range(20)),
            ]
        finally:
            await _finish_test(task, stalled, streams)

    asyncio.run(scenario())


def test_cancel_releases_real_runtime_before_sending_terminal_frame(chat, monkeypatch):
    session, streams = chat

    async def scenario():
        stalled, healthy = _StalledSocket(event_type="text_chunk"), _Socket()
        chat_ws._attached_sockets[USER] = {stalled, healthy}
        # Cancel during the first text write, independently of its deadline.
        monkeypatch.setattr(chat_ws, "_SOCKET_SEND_TIMEOUT", 10)
        send_turn = chat_ws._send_turn

        async def checked_send(ws, user_id, event, *, client_id=None):
            if event["event"] == "prompt_done":
                assert not session._lock.locked()
                assert not turns.is_busy(USER, session.conversation_id)
                assert not chat_ws._slot_gate(
                    f"{USER}:{session.conversation_id}"
                ).locked()
                assert not chat_ws._active_prompt_tasks
            await send_turn(ws, user_id, event, client_id=client_id)

        monkeypatch.setattr(chat_ws, "_send_turn", checked_send)
        task = asyncio.create_task(
            chat_ws._handle_send_message(
                stalled,
                USER,
                {
                    "slot_id": session.conversation_id,
                    "text": "stop me",
                    "client_id": "origin",
                },
            )
        )
        try:
            await asyncio.wait_for(stalled.sending.wait(), timeout=0.5)
            assert session._lock.locked()
            task.cancel()
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
            assert not session._lock.locked()
            assert not turns.is_busy(USER, session.conversation_id)
            assert healthy.sent[-1]["stop_reason"] == "cancelled"
            assert healthy.sent[-1]["client_id"] == "origin"
            assert (
                conversations.read_transcript(USER, session.conversation_id)[1].text
                == "part 0;"
            )
        finally:
            await _finish_test(task, stalled, streams)

    asyncio.run(scenario())


@pytest.mark.parametrize("cleanup_error", [False, True])
def test_done_delivery_observes_settled_history_even_if_cleanup_raises(
    chat, monkeypatch, cleanup_error
):
    session, _ = chat
    if cleanup_error:
        prompt = chat_ws.runtime.prompt

        class ClosingStream:
            def __init__(self, stream):
                self.stream = stream

            def __aiter__(self):
                return self

            async def __anext__(self):
                return await anext(self.stream)

            async def aclose(self):
                await self.stream.aclose()
                raise RuntimeError("cleanup failed after closing")

        monkeypatch.setattr(
            chat_ws.runtime, "prompt", lambda *a, **kw: ClosingStream(prompt(*a, **kw))
        )

    class CheckingSocket(_Socket):
        async def send_text(self, raw):
            if json.loads(raw)["event"] == "prompt_done":
                assert not session._lock.locked()
                assert not turns.is_busy(USER, session.conversation_id)
                assert not chat_ws._active_prompt_tasks
                transcript = conversations.read_transcript(
                    USER, session.conversation_id
                )
                assert transcript[-1].text.endswith("part 19;")
            await super().send_text(raw)

    async def scenario():
        ws = CheckingSocket()
        await chat_ws._handle_send_message(
            ws,
            USER,
            {
                "slot_id": session.conversation_id,
                "text": "hello",
                "client_id": "origin",
            },
        )
        assert ws.sent[-1]["stop_reason"] == "end_turn"
        assert ws.sent[-1]["client_id"] == "origin"
        assert not any(e["event"] == "error" for e in ws.sent)

    asyncio.run(scenario())


def test_wake_fanout_delivers_to_healthy_tab_without_waiting_for_stalled_tab(chat):
    from condor.runtime.events import RuntimeEvent

    session, _ = chat

    async def scenario():
        stalled, healthy = _StalledSocket(), _Socket()
        chat_ws._attached_sockets[USER] = {stalled, healthy}
        sink = chat_ws._WakeSink(USER, session.conversation_id)
        task = asyncio.create_task(
            sink.on_event(RuntimeEvent.from_acp(TextChunk("hello")))
        )
        try:
            await asyncio.wait_for(stalled.sending.wait(), timeout=0.5)
            await asyncio.sleep(0)
            assert healthy.sent == [
                {
                    "event": "text_chunk",
                    "slot_id": session.conversation_id,
                    "text": "hello",
                }
            ]
            assert not task.done()
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        finally:
            stalled.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
