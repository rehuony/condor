"""Native streams deliver before completion and settle cleanly on every exit."""

import asyncio

import pytest
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from condor.acp.client import (
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
)
from condor.acp.pydantic_ai_client import PydanticAIClient


def _client(stream, *, tools=None, callback=None):
    client = PydanticAIClient("openai:gpt-4o", permission_callback=callback)
    client._agent = Agent(
        FunctionModel(stream_function=stream),
        toolsets=client._gate_toolsets([tools]) if tools else [],
    )
    client._request_semaphore = asyncio.Semaphore(1)
    return client


def _history_text(client):
    return "".join(
        p.content
        for m in client._message_history
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, TextPart)
    )


def test_text_and_thought_deltas_arrive_before_the_model_finishes_without_duplicates():
    async def run():
        continue_response = asyncio.Event()

        async def model(messages, info):
            yield {0: DeltaThinkingPart(content="consider ")}
            yield {0: DeltaThinkingPart(content="this")}
            yield "first"
            await continue_response.wait()
            yield " second"

        client = _client(model)
        events = []
        async for event in client.prompt_stream("go"):
            events.append(event)
            if isinstance(event, TextChunk) and event.text == "first":
                assert not continue_response.is_set()
                continue_response.set()
        assert [e.text for e in events if isinstance(e, TextChunk)] == [
            "first",
            " second",
        ]
        assert (
            "".join(e.text for e in events if isinstance(e, ThoughtChunk))
            == "consider this"
        )
        assert _history_text(client) == "first second"
        assert events[-1] == PromptDone("end_turn")

    asyncio.run(asyncio.wait_for(run(), timeout=2))


@pytest.mark.parametrize(
    "exit_kind", ["stop", "close", "timeout", "error", "cancel_task"]
)
def test_partial_stream_exits_keep_delivered_text_usage_and_release_every_task(
    exit_kind,
):
    async def run():
        before = asyncio.all_tasks()
        first = asyncio.Event()
        waiting = asyncio.Event()
        closed = asyncio.Event()
        fail = asyncio.Event()
        requests = 0

        async def model(messages, info):
            nonlocal requests
            requests += 1
            if requests > 1:
                yield "continued"
                return
            try:
                yield "visible"
                waiting.set()
                await fail.wait()
                if exit_kind == "error":
                    raise RuntimeError("provider failed")
                yield "must not be drained"
            finally:
                closed.set()

        client = _client(model)
        events = []
        stream = client.prompt_stream(
            "go", timeout_s=0.05 if exit_kind == "timeout" else None
        )

        async def consume():
            async for event in stream:
                events.append(event)
                if isinstance(event, TextChunk):
                    first.set()
                    if exit_kind == "close":
                        await stream.aclose()
                        return

        consumer = asyncio.create_task(consume())
        await first.wait()
        await waiting.wait()
        if exit_kind == "stop":
            await client.abort_prompt()
        elif exit_kind == "close":
            await consumer
        elif exit_kind == "error":
            fail.set()
        elif exit_kind == "cancel_task":
            consumer.cancel()
        if exit_kind == "cancel_task":
            with pytest.raises(asyncio.CancelledError):
                await consumer
        else:
            await consumer
        assert closed.is_set()
        assert _history_text(client) == "visible"
        assert client.usage.input_tokens == 50
        assert client.usage.output_tokens == 1
        assert client.usage.unpriced_turns == 1
        assert client._model_stream_state is None
        assert not client._slot_held
        assert not client._request_semaphore.locked()
        expected = {"stop": "cancelled", "timeout": "timeout", "error": "error"}
        if exit_kind in expected:
            assert events[-1] == PromptDone(expected[exit_kind])
        assert "must not be drained" not in "".join(
            e.text for e in events if isinstance(e, TextChunk)
        )
        await stream.aclose()
        followup = [e async for e in client.prompt_stream("continue")]
        assert followup[-1] == PromptDone("end_turn")
        assert client.usage.input_tokens == 100
        assert client.usage.output_tokens == 2
        await asyncio.sleep(0)
        assert asyncio.all_tasks() <= before

    asyncio.run(asyncio.wait_for(run(), timeout=2))


@pytest.mark.parametrize("exit_kind", ["stop", "timeout", "cancel_task"])
def test_stop_before_first_token_cancels_transport_setup_without_orphan_waiters(
    exit_kind,
):
    async def run():
        before = asyncio.all_tasks()
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def model(messages, info):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield "never"
            finally:
                closed.set()

        client = _client(model)

        async def consume():
            return [
                e
                async for e in client.prompt_stream(
                    "go", timeout_s=0.05 if exit_kind == "timeout" else None
                )
            ]

        consumer = asyncio.create_task(consume())
        await entered.wait()
        if exit_kind == "stop":
            await client.abort_prompt()
        elif exit_kind == "cancel_task":
            consumer.cancel()
        if exit_kind == "cancel_task":
            with pytest.raises(asyncio.CancelledError):
                await consumer
        else:
            events = await consumer
            assert events[-1] == PromptDone(
                "timeout" if exit_kind == "timeout" else "cancelled"
            )
        assert closed.is_set()
        assert not client._request_semaphore.locked()
        await asyncio.sleep(0)
        assert asyncio.all_tasks() <= before

    asyncio.run(asyncio.wait_for(run(), timeout=2))


@pytest.mark.parametrize("exit_kind", ["stop", "timeout", "cancel_task"])
def test_cancellation_during_tool_preparation_stops_before_opening_transport(exit_kind):
    async def run():
        before = asyncio.all_tasks()
        preparing = asyncio.Event()
        closed = asyncio.Event()
        contacted = []
        tools = FunctionToolset()

        async def prepare(ctx, definition):
            try:
                preparing.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

        @tools.tool_plain(prepare=prepare)
        async def lookup() -> str:
            return "found"

        async def model(messages, info):
            contacted.append(True)
            yield "never"

        client = _client(model, tools=tools)

        async def consume():
            return [
                e
                async for e in client.prompt_stream(
                    "go", timeout_s=0.05 if exit_kind == "timeout" else None
                )
            ]

        consumer = asyncio.create_task(consume())
        await preparing.wait()
        if exit_kind == "stop":
            await client.abort_prompt()
        elif exit_kind == "cancel_task":
            consumer.cancel()
        if exit_kind == "cancel_task":
            with pytest.raises(asyncio.CancelledError):
                await consumer
        else:
            events = await consumer
            assert events[-1] == PromptDone(
                "timeout" if exit_kind == "timeout" else "cancelled"
            )
        assert closed.is_set()
        assert contacted == []
        assert not client._request_semaphore.locked()
        await asyncio.sleep(0)
        assert asyncio.all_tasks() <= before

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_stop_racing_transport_task_start_does_not_orphan_sdk_readiness_waiter(
    monkeypatch,
):
    async def run():
        before = asyncio.all_tasks()
        contacted = []

        async def model(messages, info):
            contacted.append(True)
            yield "never"

        client = _client(model)
        create_task = asyncio.create_task

        def stop_before_transport_runs(coro, *args, **kwargs):
            task = create_task(coro, *args, **kwargs)
            if getattr(coro, "__name__", "") == "wrap_model_request":
                client._abort_requested = True
                client._model_stream_state.cancel()
            return task

        monkeypatch.setattr(asyncio, "create_task", stop_before_transport_runs)
        events = [e async for e in client.prompt_stream("go")]
        assert events[-1] == PromptDone("cancelled")
        assert contacted == []
        assert not client._request_semaphore.locked()
        await asyncio.sleep(0)
        assert asyncio.all_tasks() <= before

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_stop_does_not_replay_a_completed_response_still_waiting_in_the_queue():
    async def run():
        async def model(messages, info):
            yield "visible"
            yield " hidden"

        client = _client(model)
        events = []
        async for event in client.prompt_stream("go"):
            events.append(event)
            if isinstance(event, TextChunk):
                state = client._model_stream_state
                await state.task
                assert state.completed
                await client.abort_prompt()
        assert [e.text for e in events if isinstance(e, TextChunk)] == ["visible"]
        assert _history_text(client) == "visible"
        # Generated but undelivered text still consumed provider tokens.
        assert client.usage.output_tokens == 2

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_interrupted_second_model_request_counts_the_first_request_only_once():
    async def run():
        tools = FunctionToolset()

        @tools.tool_plain
        async def lookup() -> str:
            return "found"

        async def model(messages, info):
            if any(isinstance(p, ToolCallPart) for m in messages for p in m.parts):
                yield "visible"
                await asyncio.Event().wait()
            else:
                yield {
                    0: DeltaToolCall(
                        name="lookup", json_args="{}", tool_call_id="call-1"
                    )
                }

        client = _client(model, tools=tools)
        async for event in client.prompt_stream("go"):
            if isinstance(event, TextChunk):
                await client.abort_prompt()
        assert client.usage.input_tokens == 100
        assert client.usage.output_tokens == 2
        assert _history_text(client) == "visible"
        assert any(
            isinstance(p, ToolReturnPart) and p.content == "found"
            for m in client._message_history
            for p in m.parts
        )

    asyncio.run(asyncio.wait_for(run(), timeout=2))


@pytest.mark.parametrize("fails", [False, True])
def test_tools_finish_only_after_actual_results_and_authorize_complete_args(fails):
    async def run():
        started = asyncio.Event()
        finish = asyncio.Event()
        authorized = []
        tools = FunctionToolset()

        @tools.tool_plain
        async def lookup(value: int) -> str:
            assert value == 12
            started.set()
            await finish.wait()
            if fails:
                raise ModelRetry("lookup failed")
            return "found"

        async def allow(call, options):
            pending = [event for event in events if isinstance(event, ToolCallEvent)]
            assert [
                (event.tool_call_id, event.name, event.status) for event in pending
            ] == [("call-1", "lookup", "pending")]
            authorized.append(call["input"])
            return {"outcome": {"outcome": "selected", "optionId": "allow"}}

        async def model(messages, info):
            if any(isinstance(p, ToolCallPart) for m in messages for p in m.parts):
                yield "done"
            else:
                yield {
                    0: DeltaToolCall(
                        name="lookup", json_args='{"value":', tool_call_id="call-1"
                    )
                }
                yield {0: DeltaToolCall(json_args="12}")}

        client = _client(model, tools=tools, callback=allow)
        events = []

        async def consume():
            async for event in client.prompt_stream("go"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        await started.wait()
        assert authorized == [{"value": 12}]
        assert [
            e.status for e in events if isinstance(e, (ToolCallEvent, ToolCallUpdate))
        ] == ["pending", "in_progress"]
        finish.set()
        await consumer
        updates = [e for e in events if isinstance(e, ToolCallUpdate)]
        assert len(updates) == 1
        assert updates[0].status == ("failed" if fails else "completed")
        assert updates[0].name == "lookup"
        assert events[-1] == PromptDone("end_turn")

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_stop_during_a_tool_preserves_its_actual_result_without_cancelling_the_tool():
    async def run():
        started = asyncio.Event()
        finish = asyncio.Event()
        tools = FunctionToolset()

        @tools.tool_plain
        async def mutate() -> str:
            started.set()
            await finish.wait()
            return "mutation completed"

        async def model(messages, info):
            yield {
                0: DeltaToolCall(name="mutate", json_args="{}", tool_call_id="call-1")
            }

        client = _client(model, tools=tools)

        async def consume():
            return [e async for e in client.prompt_stream("go")]

        consumer = asyncio.create_task(consume())
        await started.wait()
        await client.abort_prompt()
        assert not consumer.done()
        finish.set()
        events = await consumer
        assert events[-1] == PromptDone("cancelled")
        updates = [e for e in events if isinstance(e, ToolCallUpdate)]
        assert [(e.status, e.output) for e in updates] == [
            ("completed", "mutation completed")
        ]
        returns = [
            p
            for m in client._message_history
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, ToolReturnPart)
        ]
        assert [p.content for p in returns] == ["mutation completed"]

    asyncio.run(asyncio.wait_for(run(), timeout=2))


def test_cancelled_tool_argument_stream_is_never_replayed_or_executed():
    async def run():
        received_arguments = asyncio.Event()
        tools = FunctionToolset()
        calls = []

        @tools.tool_plain
        async def mutate(value: int) -> str:
            calls.append(value)
            return "changed"

        async def model(messages, info):
            yield "visible"
            yield {
                0: DeltaToolCall(
                    name="mutate", json_args='{"value":', tool_call_id="call-1"
                )
            }
            received_arguments.set()
            await asyncio.Event().wait()

        client = _client(model, tools=tools)

        async def consume():
            return [e async for e in client.prompt_stream("go")]

        consumer = asyncio.create_task(consume())
        await received_arguments.wait()
        await client.abort_prompt()
        assert (await consumer)[-1] == PromptDone("cancelled")
        assert calls == []
        assert not any(
            isinstance(p, ToolCallPart)
            for m in client._message_history
            for p in m.parts
        )

    asyncio.run(asyncio.wait_for(run(), timeout=2))
