"""Queued requests recheck authorization and refused content never reaches replay."""

import asyncio
from types import SimpleNamespace

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import conversations, sessions, turns
from condor.runtime.events import EventType
from condor.runtime.models import PromptImage


class Model:
    alive = True

    def __init__(self):
        self.inputs = []
        self.started = asyncio.Event()
        self.gate = None

    async def prompt_stream(self, text, **kwargs):
        self.inputs.append(text)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        yield TextChunk(text="An authorized answer.")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def chat(monkeypatch):
    meta = conversations.new_conversation(7, "tg")
    session = sessions.AgentSession(
        SessionKey.telegram(7),
        "claude-code",
        Model(),
        user_id=7,
        conversation_id=meta.id,
    )
    monkeypatch.setattr(sessions, "_sessions", {str(session.key): session})
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(turns, "_turns", {})
    monkeypatch.setattr(turns, "_sinks", [])
    return session


async def collect(chat, text, *, request_id="", can_execute=None, **kwargs):
    return [
        event
        async for event in runtime.prompt(
            chat.key,
            PromptRequest(text=text, request_id=request_id, **kwargs),
            on_busy="queue",
            can_execute=can_execute,
        )
    ]


def assert_released(chat):
    assert not turns._turns
    assert not conversations._live_recorders
    assert not chat.is_busy
    assert not chat._lock.locked()


@pytest.mark.asyncio
async def test_denial_preserves_audit_and_dedup_but_never_calls_model(chat):
    events = await collect(
        chat,
        "FORBIDDEN REQUEST",
        request_id="telegram:7:1",
        can_execute=lambda: False,
        images=[PromptImage(data=b"image", mime="image/png", id="blocked.png")],
    )
    assert [event.type for event in events] == [
        EventType.ACCEPTED,
        EventType.ERROR,
        EventType.DONE,
    ]
    assert events[-1].stop_reason == "access_denied"
    assert "not sent to the agent" in events[-2].field("message")
    assert chat.client.inputs == []
    saved = conversations.read_transcript(7, chat.conversation_id)
    assert [(entry.role, entry.kind, entry.request_id) for entry in saved] == [
        ("user", "", "telegram:7:1"),
        ("system", "access_denied", "telegram:7:1"),
    ]
    assert saved[0].text == "FORBIDDEN REQUEST"
    assert saved[0].attachments[0]["id"] == "blocked.png"
    assert conversations.has_request(7, "telegram:7:1")
    replay = conversations.replay_context(7, chat.conversation_id)
    assert "FORBIDDEN REQUEST" not in replay
    assert "blocked.png" not in replay
    assert_released(chat)


@pytest.mark.asyncio
async def test_access_is_rechecked_after_queue_and_refused_request_is_not_replayed(
    chat,
):
    access = SimpleNamespace(allowed=True)
    queued = asyncio.Event()
    chat.client.gate = asyncio.Event()
    first = asyncio.create_task(collect(chat, "First authorized request"))
    await chat.client.started.wait()

    async def second():
        events = []
        async for event in runtime.prompt(
            chat.key,
            PromptRequest(text="FORBIDDEN QUEUED INPUT", request_id="telegram:7:2"),
            on_busy="queue",
            can_execute=lambda: access.allowed,
        ):
            events.append(event)
            if event.type == EventType.QUEUED:
                queued.set()
        return events

    waiting = asyncio.create_task(second())
    await queued.wait()
    access.allowed = False
    chat.client.gate.set()
    await first
    result = await waiting
    assert result[-1].stop_reason == "access_denied"
    assert chat.client.inputs == ["First authorized request"]
    assert_released(chat)

    access.allowed = True
    await collect(
        chat, "Now answer this authorized question", can_execute=lambda: access.allowed
    )
    assert len(chat.client.inputs) == 2
    assert "FORBIDDEN QUEUED INPUT" not in chat.client.inputs[-1]
    assert "First authorized request" in chat.client.inputs[-1]
    assert "Now answer this authorized question" in chat.client.inputs[-1]
    assert_released(chat)


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_at", ["refresh", "reload_delivery"])
async def test_guard_runs_after_refresh_await_and_reload_delivery(
    chat, monkeypatch, revoke_at
):
    access = SimpleNamespace(allowed=True)
    refreshing, resume = asyncio.Event(), asyncio.Event()
    checked = []

    async def refresh(key):
        refreshing.set()
        await resume.wait()
        return ["model"]

    def check():
        checked.append(access.allowed)
        return access.allowed

    monkeypatch.setattr(sessions, "refresh_if_stale", refresh)

    async def consume():
        events = []
        async for event in runtime.prompt(
            chat.key,
            PromptRequest(text="Do not run after revocation"),
            can_execute=check,
        ):
            events.append(event)
            if revoke_at == "reload_delivery" and event.type == EventType.RELOADED:
                access.allowed = False
        return events

    task = asyncio.create_task(consume())
    await refreshing.wait()
    if revoke_at == "refresh":
        access.allowed = False
    resume.set()
    events = await task
    assert checked == [False]
    assert events[-1].stop_reason == "access_denied"
    assert chat.client.inputs == []
    assert_released(chat)


@pytest.mark.asyncio
async def test_failed_authorization_lookup_is_safe_and_fails_closed(chat, caplog):
    def broken_lookup():
        raise RuntimeError("https://example.test/TOP_SECRET_TOKEN")

    events = await collect(chat, "Never execute", can_execute=broken_lookup)
    assert events[-1].stop_reason == "access_denied"
    assert chat.client.inputs == []
    assert "TOP_SECRET_TOKEN" not in caplog.text
    assert "TOP_SECRET_TOKEN" not in repr(events)
    assert "error_type=RuntimeError" in caplog.text
    assert_released(chat)


@pytest.mark.asyncio
async def test_closing_after_denial_error_still_writes_replay_tombstone(chat):
    stream = runtime.prompt(
        chat.key,
        PromptRequest(text="FORBIDDEN INTERRUPTED INPUT", request_id="telegram:7:3"),
        can_execute=lambda: False,
    )
    assert (await anext(stream)).type == EventType.ACCEPTED
    assert (await anext(stream)).type == EventType.ERROR
    await stream.aclose()
    assert_released(chat)
    assert (
        conversations.read_transcript(7, chat.conversation_id)[-1].kind
        == "access_denied"
    )
    assert "FORBIDDEN INTERRUPTED INPUT" not in conversations.replay_context(
        7, chat.conversation_id
    )


@pytest.mark.asyncio
async def test_denial_survives_session_restart_and_does_not_consume_future_access(
    chat, monkeypatch
):
    await collect(
        chat,
        "FORBIDDEN STORED INPUT",
        request_id="telegram:7:4",
        can_execute=lambda: False,
    )
    replacement = sessions.AgentSession(
        chat.key,
        "claude-code",
        Model(),
        user_id=7,
        conversation_id=chat.conversation_id,
    )
    monkeypatch.setattr(sessions, "_sessions", {str(chat.key): replacement})
    duplicate = await collect(
        replacement,
        "FORBIDDEN STORED INPUT",
        request_id="telegram:7:4",
        can_execute=lambda: True,
    )
    assert duplicate[-1].stop_reason == "duplicate"
    assert replacement.client.inputs == []
    await collect(replacement, "New authorized request", can_execute=lambda: True)
    assert len(replacement.client.inputs) == 1
    assert "FORBIDDEN STORED INPUT" not in replacement.client.inputs[0]
    assert "New authorized request" in replacement.client.inputs[0]
    assert_released(replacement)


@pytest.mark.asyncio
async def test_callers_without_access_guard_keep_normal_behavior(chat):
    events = await collect(chat, "Ordinary surface request")
    assert chat.client.inputs == ["Ordinary surface request"]
    assert events[-1].stop_reason == "end_turn"
    assert_released(chat)


@pytest.mark.asyncio
async def test_access_refusal_precedes_other_input_validation(chat):
    chat.client.accepts_images = False
    events = await collect(
        chat,
        "FORBIDDEN IMAGE CAPTION",
        can_execute=lambda: False,
        images=[PromptImage(data=b"image", mime="image/png", id="blocked.png")],
    )
    assert events[-1].stop_reason == "access_denied"
    assert chat.client.inputs == []
    assert "FORBIDDEN IMAGE CAPTION" not in conversations.replay_context(
        7, chat.conversation_id
    )
    assert_released(chat)
