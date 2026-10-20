"""Synthetic terminal events include accounting settled during stream closure."""

import asyncio

import pytest

from condor.acp.client import PromptDone, TextChunk, ToolCallEvent, ToolCallUpdate
from condor.acp.usage import TokenUsage
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import conversations, sessions, turns


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["cancelled", "timeout"])
async def test_terminal_usage_is_published_after_partial_stream_cleanup(
    monkeypatch, reason
):
    class Model:
        alive = True
        usage = TokenUsage()

        def __init__(self):
            self.finish = asyncio.Event()
            self.closed = False

        async def prompt_stream(self, text):
            try:
                yield TextChunk(text="Visible partial answer")
                await self.finish.wait()
                yield TextChunk(text="Never shown")
            finally:
                self.usage = TokenUsage(input_tokens=12, output_tokens=3)
                self.closed = True

        async def abort_prompt(self):
            self.finish.set()

    monkeypatch.setattr(turns, "_turns", {})
    monkeypatch.setattr(turns, "_sinks", [])
    meta = conversations.new_conversation(42, "tg")
    model = Model()
    session = sessions.AgentSession(
        key=SessionKey.telegram(42),
        agent_key="claude-code",
        client=model,
        user_id=42,
        conversation_id=meta.id,
    )
    monkeypatch.setattr(sessions, "_sessions", {str(session.key): session})
    if reason == "timeout":
        monkeypatch.setattr(sessions, "PROMPT_OVERALL_TIMEOUT", -1)
    stream = runtime.prompt(
        session.key, PromptRequest(text="Answer", request_id="turn")
    )
    seen = []
    async for event in stream:
        seen.append(event)
        if event.type.value == "text":
            if reason == "cancelled":
                assert await runtime.abort_turn(42, meta.id, "turn")
        if event.type.value == "done":
            assert model.closed
            assert event.stop_reason == reason
            assert event.field("usage")["output_tokens"] == 3
            assert event.field("usage")["input_tokens"] == 12
    assert [e.text for e in seen if e.type.value == "text"] == [
        "Visible partial answer"
    ]
    assert not session.is_busy
    assert not session._lock.locked()


@pytest.mark.asyncio
async def test_stop_retains_settled_tool_batch_results(monkeypatch):
    class Model:
        alive = True

        def __init__(self):
            self.finish = asyncio.Event()

        async def prompt_stream(self, text):
            yield TextChunk(text="Checking results")
            yield ToolCallEvent("one", "get_prices", "in_progress")
            yield ToolCallEvent("two", "get_positions", "in_progress")
            await self.finish.wait()
            yield ToolCallUpdate("one", status="completed", output="known price")
            yield ToolCallUpdate("two", status="failed", output="unavailable")
            yield PromptDone(stop_reason="cancelled")

        async def abort_prompt(self):
            self.finish.set()

    monkeypatch.setattr(turns, "_turns", {})
    monkeypatch.setattr(turns, "_sinks", [])
    meta = conversations.new_conversation(42, "tg")
    session = sessions.AgentSession(
        key=SessionKey.telegram(42),
        agent_key="claude-code",
        client=Model(),
        user_id=42,
        conversation_id=meta.id,
    )
    monkeypatch.setattr(sessions, "_sessions", {str(session.key): session})
    seen = []
    async for event in runtime.prompt(
        session.key, PromptRequest(text="Check", request_id="turn")
    ):
        seen.append(event)
        if event.type.value == "tool_call" and event.field("tool_call_id") == "two":
            assert await runtime.abort_turn(42, meta.id, "turn")

    assert seen[-1].stop_reason == "cancelled"
    assistant = conversations.read_transcript(42, meta.id)[-1]
    assert assistant.text == "Checking results"
    assert [(call["status"], call["output"]) for call in assistant.tool_calls] == [
        ("completed", "known price"),
        ("failed", "unavailable"),
    ]
    assert not session._lock.locked()
