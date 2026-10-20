"""Received messages survive transport failures and share an ordered history."""

import asyncio

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import conversations, sessions, turns


class Model:
    alive = True

    def __init__(self):
        self.inputs = []
        self.started = asyncio.Event()
        self.gate = None
        self.cancelled = False

    async def prompt_stream(self, text):
        self.inputs.append(text)
        self.started.set()
        yield TextChunk(text="model reply")
        if self.gate:
            await self.gate.wait()
        yield PromptDone(stop_reason="cancelled" if self.cancelled else "end_turn")

    async def abort_prompt(self):
        self.cancelled = True
        if self.gate:
            self.gate.set()


@pytest.fixture
def chat(monkeypatch):
    monkeypatch.setattr(sessions, "_sessions", {})
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(turns, "_turns", {})
    monkeypatch.setattr(turns, "_sinks", [])
    meta = conversations.new_conversation(7, "tg")
    tg = sessions.AgentSession(
        SessionKey.telegram(7),
        "claude-code",
        Model(),
        user_id=7,
        conversation_id=meta.id,
    )
    web = sessions.AgentSession(
        SessionKey.web(7, meta.id),
        "claude-code",
        Model(),
        user_id=7,
        conversation_id=meta.id,
    )
    sessions._sessions.update({str(tg.key): tg, str(web.key): web})
    return tg, web


async def collect(session, text, **kwargs):
    return [
        event
        async for event in runtime.prompt(
            session.key, PromptRequest(text=text, **kwargs), on_busy="queue"
        )
    ]


def test_accepted_input_is_on_disk_before_model_runs(chat):
    tg, _ = chat

    async def run():
        stream = runtime.prompt(
            tg.key, PromptRequest(text="remember this", request_id="telegram:7:1")
        )
        event = await anext(stream)
        assert event.type.value == "accepted"
        assert tg.client.inputs == []
        assert (
            conversations.read_transcript(7, tg.conversation_id)[0].text
            == "remember this"
        )
        await stream.aclose()
        assert not turns.is_busy(7, tg.conversation_id)

    asyncio.run(run())
    assert conversations.has_request(7, "telegram:7:1")
    assert conversations.read_transcript(7, tg.conversation_id)[-1].kind == "error"


def test_concurrent_arrivals_of_one_request_execute_once(chat, monkeypatch):
    """Durable acceptance runs off the loop; the second arrival must still lose.

    Two deliveries of the same transport message race through ``has_request``
    before either transcript append lands. One of them is accepted and the
    other is a duplicate — never two model turns, never two transcript lines.
    """
    tg, web = chat
    accept = conversations.Recorder.accept
    first_in = asyncio.Event()
    release = None

    def slow_accept(self):
        first_in.set()
        release.wait()
        accept(self)

    monkeypatch.setattr(conversations.Recorder, "accept", slow_accept)

    async def run():
        nonlocal release
        import threading

        release = threading.Event()
        first = asyncio.create_task(collect(tg, "same words", request_id="tg:7:5"))
        await first_in.wait()
        second = asyncio.create_task(collect(web, "same words", request_id="tg:7:5"))
        await asyncio.sleep(0.05)
        release.set()
        results = await asyncio.gather(first, second)
        stops = sorted(e.stop_reason for r in results for e in r if e.stop_reason)
        assert stops == ["duplicate", "end_turn"]
        assert tg.client.inputs + web.client.inputs == ["same words"]
        assert not runtime._in_flight

    asyncio.run(run())
    openings = [
        t
        for t in conversations.read_transcript(7, tg.conversation_id)
        if t.role == "user"
    ]
    assert len(openings) == 1


def test_duplicate_message_is_not_executed_after_registry_restart_or_chat_switch(chat):
    tg, web = chat

    async def run():
        await collect(tg, "one operation", request_id="telegram:7:9")
        turns._turns.clear()
        web.conversation_id = conversations.new_conversation(7, "web").id
        duplicate = await collect(web, "one operation", request_id="telegram:7:9")
        assert [e.stop_reason for e in duplicate] == ["duplicate"]

    asyncio.run(run())
    assert tg.client.inputs == ["one operation"]
    assert web.client.inputs == []


def test_cross_surface_queue_reads_completed_history_without_future_inputs(chat):
    tg, web = chat

    async def run():
        tg.client.gate = asyncio.Event()
        first = asyncio.create_task(collect(tg, "first question"))
        await tg.client.started.wait()
        second = asyncio.create_task(collect(web, "second question"))
        third = asyncio.create_task(collect(tg, "third question"))
        await asyncio.sleep(0.02)
        assert web.client.inputs == []
        assert [
            t.text for t in conversations.read_transcript(7, tg.conversation_id)
        ] == ["first question", "second question", "third question"]
        tg.client.gate.set()
        await asyncio.gather(first, second, third)
        assert "first question" in web.client.inputs[0]
        assert "model reply" in web.client.inputs[0]
        assert "third question" not in web.client.inputs[0]
        assert web.client.inputs[0].count("second question") == 1
        assert "second question" in tg.client.inputs[1]
        assert tg.client.inputs[1].count("third question") == 1
        assert not turns.is_busy(7, tg.conversation_id)

    asyncio.run(run())


def test_web_stop_cancels_telegram_turn_in_same_conversation(chat):
    tg, web = chat

    async def run():
        tg.client.gate = asyncio.Event()
        task = asyncio.create_task(collect(tg, "long question"))
        await tg.client.started.wait()
        assert await runtime.abort(web.key)
        result = await task
        assert result[-1].stop_reason == "cancelled"
        assert tg.client.cancelled
        assert not web.client.cancelled

    asyncio.run(run())


def test_cancelling_waiter_preserves_input_and_does_not_unlock_running_turn(chat):
    tg, web = chat

    async def run():
        tg.client.gate = asyncio.Event()
        task = asyncio.create_task(collect(tg, "long question"))
        await tg.client.started.wait()
        waiting = asyncio.create_task(collect(web, "saved while waiting"))
        await asyncio.sleep(0.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert turns.is_busy(7, tg.conversation_id)
        assert turns.active_key(7, tg.conversation_id) == str(tg.key)
        tg.client.gate.set()
        await task
        assert web.client.inputs == []
        assert not turns._turns

    asyncio.run(run())
    assert any(
        t.text == "saved while waiting"
        for t in conversations.read_transcript(7, tg.conversation_id)
    )


def test_storage_failure_prevents_ai_execution(chat, monkeypatch):
    tg, _ = chat
    append_turn = conversations.append_turn

    def fail(*args, **kwargs):
        if kwargs.get("durable"):
            raise OSError("disk unavailable")
        return append_turn(*args, **kwargs)

    monkeypatch.setattr(conversations, "append_turn", fail)
    result = asyncio.run(collect(tg, "must not execute"))
    assert [e.type.value for e in result] == ["error", "done"]
    assert tg.client.inputs == []
    assert not turns._turns
    assert not conversations._live_recorders


def test_slow_display_does_not_block_model_or_durable_completion(chat):
    tg, _ = chat
    changes = []

    async def sink(change):
        changes.append(change)
        await asyncio.Event().wait()

    turns.register_change_sink(sink)

    async def run():
        await asyncio.wait_for(collect(tg, "answer now"), timeout=0.2)
        await asyncio.sleep(0)
        assert [c["phase"] for c in changes] == ["accepted", "completed"]
        assert [c["is_busy"] for c in changes] == [True, False]

    asyncio.run(run())
    assert (
        conversations.read_transcript(7, tg.conversation_id)[-1].text == "model reply"
    )


def test_stop_after_acceptance_prevents_telegram_execution_without_a_web_session(chat):
    tg, web = chat
    sessions._sessions.pop(str(web.key))

    async def run():
        stream = runtime.prompt(tg.key, PromptRequest(text="cancel before execution"))
        assert (await anext(stream)).type.value == "accepted"
        assert await runtime.abort(web.key)
        result = [event async for event in stream]
        assert result[-1].stop_reason == "cancelled"
        assert not turns.is_busy(7, tg.conversation_id)

    asyncio.run(run())
    assert tg.client.inputs == []
    transcript = conversations.read_transcript(7, tg.conversation_id)
    assert transcript[0].text == "cancel before execution"
    assert transcript[-1].kind == "error"
    assert "cancelled" in transcript[-1].text


def test_stop_during_session_refresh_is_not_cleared_when_prompt_starts(
    chat, monkeypatch
):
    tg, web = chat

    async def run():
        refreshing, resume = asyncio.Event(), asyncio.Event()

        async def refresh(key):
            refreshing.set()
            await resume.wait()
            return []

        monkeypatch.setattr(sessions, "refresh_if_stale", refresh)
        task = asyncio.create_task(collect(tg, "cancel while preparing"))
        await asyncio.wait_for(refreshing.wait(), timeout=1)
        assert not tg.is_busy
        assert await runtime.abort(web.key)
        resume.set()
        result = await asyncio.wait_for(task, timeout=1)
        assert result[-1].stop_reason == "cancelled"
        assert not turns._turns

    asyncio.run(run())
    assert tg.client.inputs == []


def test_stop_from_an_unattached_web_conversation_is_scoped_to_its_owner(chat):
    tg, web = chat
    sessions._sessions.pop(str(web.key))

    async def run():
        tg.client.gate = asyncio.Event()
        task = asyncio.create_task(collect(tg, "only my owner can stop this"))
        await asyncio.wait_for(tg.client.started.wait(), timeout=1)

        assert not await runtime.abort(SessionKey.web(8, tg.conversation_id))
        assert not tg.client.cancelled
        assert not task.done()
        assert await runtime.abort(web.key)
        result = await asyncio.wait_for(task, timeout=1)
        assert result[-1].stop_reason == "cancelled"
        assert not turns._turns

    asyncio.run(run())


def test_stop_cannot_cancel_a_new_conversation_reusing_the_telegram_key(chat):
    tg, web = chat
    replacement_conversation = conversations.new_conversation(7, "tg")
    replacement = sessions.AgentSession(
        tg.key,
        "claude-code",
        Model(),
        user_id=7,
        conversation_id=replacement_conversation.id,
        is_busy=True,
    )

    async def run():
        old_turn = runtime.prompt(tg.key, PromptRequest(text="old conversation"))
        assert (await anext(old_turn)).type.value == "accepted"
        assert (await anext(old_turn)).type.value == "heartbeat"
        assert (await anext(old_turn)).type.value == "text"
        # /new can reuse tg:chat while the old consumer is still draining an
        # event. The old conversation's active key must not address this model.
        sessions._sessions[str(tg.key)] = replacement
        try:
            assert await runtime.abort(web.key)
            assert not replacement.client.cancelled
            assert not replacement._abort_event.is_set()
        finally:
            await old_turn.aclose()

    asyncio.run(run())


def test_a_refresh_that_changes_conversation_does_not_run_the_accepted_input(
    chat, monkeypatch
):
    tg, _ = chat
    replacement_conversation = conversations.new_conversation(7, "tg")
    replacement = sessions.AgentSession(
        tg.key,
        "claude-code",
        Model(),
        user_id=7,
        conversation_id=replacement_conversation.id,
    )

    async def refresh(key):
        # A rebuild can resolve a new conversation if the original is deleted
        # during its handshake. The input was accepted for the original only.
        sessions._sessions[str(key)] = replacement
        return ["model"]

    monkeypatch.setattr(sessions, "refresh_if_stale", refresh)
    result = asyncio.run(collect(tg, "belongs to the original conversation"))

    assert result[-1].stop_reason == "error"
    assert tg.client.inputs == replacement.client.inputs == []
    assert not turns._turns
    assert (
        conversations.read_transcript(7, tg.conversation_id)[0].text
        == "belongs to the original conversation"
    )
    assert conversations.read_transcript(7, replacement.conversation_id) == []


def test_a_slow_accepted_event_consumer_keeps_its_place_in_the_conversation_queue(chat):
    tg, web = chat

    async def run():
        first = runtime.prompt(
            tg.key, PromptRequest(text="accepted first"), on_busy="queue"
        )
        assert (await anext(first)).type.value == "accepted"
        second = asyncio.create_task(collect(web, "accepted second"))
        await asyncio.sleep(0.02)
        # The first consumer has not asked for its next event yet. Reservation
        # happens on acceptance, so the second turn cannot overtake it.
        assert tg.client.inputs == []
        assert web.client.inputs == []
        assert turns.pending_count(7, tg.conversation_id, str(web.key)) == 1

        first_result = [event async for event in first]
        second_result = await asyncio.wait_for(second, timeout=1)
        assert (
            first_result[-1].stop_reason == second_result[-1].stop_reason == "end_turn"
        )
        assert not turns._turns

    asyncio.run(run())
    assert tg.client.inputs == ["accepted first"]
    assert "accepted first" in web.client.inputs[0]
    assert web.client.inputs[0].count("accepted second") == 1


def test_a_note_arriving_mid_turn_is_read_on_the_same_sessions_next_turn(chat):
    tg, _ = chat

    async def run():
        tg.client.gate = asyncio.Event()
        first = asyncio.create_task(collect(tg, "first question"))
        await asyncio.wait_for(tg.client.started.wait(), timeout=1)
        conversations.record_system(
            7,
            tg.conversation_id,
            "The background task found three pools.",
            kind="routine",
        )
        tg.client.gate.set()
        await first
        await collect(tg, "what did the background task find?")

    asyncio.run(run())
    assert "three pools" not in tg.client.inputs[0]
    assert "three pools" in tg.client.inputs[1]


@pytest.mark.parametrize("next_surface", ["tg", "web"])
def test_an_image_notification_refreshes_existing_model_context(chat, next_surface):
    tg, web = chat
    destination = tg if next_surface == "tg" else web

    async def run():
        tg.client.gate = asyncio.Event()
        first = asyncio.create_task(collect(tg, "Draw the plan"))
        await asyncio.wait_for(tg.client.started.wait(), timeout=1)
        conversations.record_system(
            7,
            tg.conversation_id,
            "",
            kind="notification",
            attachments=[{"id": "plan.png", "mime": "image/png", "bytes": 100}],
            durable=True,
        )
        tg.client.gate.set()
        await first
        await collect(destination, "Which image did you save?")

    asyncio.run(run())
    assert "plan.png" not in tg.client.inputs[0]
    refreshed = destination.client.inputs[-1]
    assert "Updated conversation history" in refreshed
    assert f"conversation={tg.conversation_id}, attachment=plan.png" in refreshed
    assert "content not included in replay" in refreshed
    assert refreshed.count("Which image did you save?") == 1


def test_background_note_notifies_displays_without_starting_a_model(chat):
    tg, web = chat
    changes = []

    async def sink(change):
        changes.append(change)
        assert (
            conversations.read_transcript(7, tg.conversation_id)[-1].text
            == "Task finished."
        )

    turns.register_change_sink(sink)

    async def run():
        conversations.record_system(
            7, tg.conversation_id, "Task finished.", kind="delegate"
        )
        await asyncio.gather(*turns._deliveries)

    asyncio.run(run())
    assert len(changes) == 1
    assert changes[0]["conversation_id"] == tg.conversation_id
    assert changes[0]["user_id"] == 7
    assert not changes[0]["is_busy"]
    assert tg.client.inputs == web.client.inputs == []


def test_background_note_can_be_saved_without_an_event_loop(chat):
    tg, _ = chat

    async def sink(change):
        pytest.fail("No socket delivery should run without an event loop")

    turns.register_change_sink(sink)
    conversations.record_system(7, tg.conversation_id, "Offline note.", kind="delegate")
    assert (
        conversations.read_transcript(7, tg.conversation_id)[-1].text == "Offline note."
    )
