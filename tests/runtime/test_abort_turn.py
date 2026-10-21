"""A response's Stop control cannot cancel a later or unrelated request."""

import asyncio

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import PromptRequest, SessionKey
from condor.runtime import client as runtime
from condor.runtime import confirmations, conversations, sessions, turns

USER = 7


class Model:
    alive = True

    def __init__(self):
        self.started = asyncio.Queue()
        self.releases = []
        self.cancelled = set()
        self.current = None
        self.abort_release = None

    async def prompt_stream(self, text):
        index = len(self.releases)
        release = asyncio.Event()
        self.releases.append(release)
        self.current = index
        self.started.put_nowait(index)
        yield TextChunk(text=f"reply {index}")
        await release.wait()
        yield PromptDone(
            stop_reason="cancelled" if index in self.cancelled else "end_turn"
        )

    async def abort_prompt(self):
        index = self.current
        self.cancelled.add(index)
        self.releases[index].set()
        if self.abort_release is not None:
            await self.abort_release.wait()


@pytest.fixture
def chat(monkeypatch):
    monkeypatch.setattr(sessions, "_sessions", {})
    monkeypatch.setattr(turns, "_turns", {})
    monkeypatch.setattr(turns, "_sinks", [])
    monkeypatch.setattr(
        confirmations, "_registry", confirmations.ConfirmationRegistry()
    )
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


async def collect(session, request_id):
    return [
        event
        async for event in runtime.prompt(
            session.key,
            PromptRequest(text=request_id, request_id=request_id),
            on_busy="queue",
        )
    ]


def approval(session_key):
    return confirmations.get_registry().register(session_key, USER, "Approve?", {}, [])


@pytest.mark.parametrize("stopped", [False, True])
def test_finished_request_rejects_late_permissions_but_preserves_direct_prompts(
    chat, stopped
):
    async def run():
        task = asyncio.create_task(collect(chat, "finished"))
        await chat.client.started.get()
        if stopped:
            await runtime.cancel_request(USER, chat.conversation_id, "finished")
        else:
            chat.client.releases[0].set()
        await task
        assert not turns.is_busy(USER, chat.conversation_id)
        registry = confirmations.get_registry()
        pending = approval(str(chat.key))
        assert pending.request_id == "finished"
        assert pending.status is confirmations.ConfirmationStatus.DENIED
        assert not await registry.resolve(pending.id, approved=True, by_user_id=USER)
        callback = confirmations.build_permission_callback(str(chat.key), USER)
        options = [{"optionId": "allow", "kind": "allow_once"}]
        safe = {"tool": "get_market_data", "input": {}}
        assert await callback(safe, options) == confirmations.CANCELLED

        async def direct_prompt(text):
            assert (await callback(safe, options))["outcome"]["optionId"] == "allow"
            return "summary"

        chat.client.prompt = direct_prompt
        assert await runtime.prompt_once(chat.key, "summarize") == "summary"
        assert await callback(safe, options) == confirmations.CANCELLED

        next_turn = None

        async def overlapping_prompt(text):
            nonlocal next_turn
            next_turn = asyncio.create_task(collect(chat, "next"))
            await chat.client.started.get()
            return "summary"

        chat.client.prompt = overlapping_prompt
        assert await runtime.prompt_once(chat.key, "summarize") == "summary"
        assert (await callback(safe, options))["outcome"]["optionId"] == "allow"
        chat.client.releases[1].set()
        await next_turn
        assert await callback(safe, options) == confirmations.CANCELLED

    asyncio.run(run())


def test_current_request_stops_and_denies_only_its_session_confirmations(chat):
    async def run():
        task = asyncio.create_task(collect(chat, "current"))
        await chat.client.started.get()
        pending = approval(str(chat.key))
        unrelated = approval(str(SessionKey.web(USER, "other")))

        assert await runtime.abort_turn(USER, chat.conversation_id, "current")
        assert pending.status is confirmations.ConfirmationStatus.DENIED
        assert unrelated.status is confirmations.ConfirmationStatus.PENDING
        result = await asyncio.wait_for(task, 1)
        assert result[-1].stop_reason == "cancelled"
        assert chat.client.cancelled == {0}
        assert not turns.is_busy(USER, chat.conversation_id)
        assert not await runtime.abort_turn(USER, chat.conversation_id, "current")

    asyncio.run(run())
    transcript = conversations.read_transcript(USER, chat.conversation_id)
    assert transcript[-1].text == "reply 0"
    assert transcript[-1].stop_reason == "cancelled"


@pytest.mark.parametrize(
    "owner,conversation_id,request_id",
    [(8, None, "current"), (USER, "other", "current"), (USER, None, "stale")],
)
def test_wrong_owner_conversation_or_request_does_not_touch_current_turn(
    chat, owner, conversation_id, request_id
):
    async def run():
        task = asyncio.create_task(collect(chat, "current"))
        await chat.client.started.get()
        pending = approval(str(chat.key))

        assert not await runtime.abort_turn(
            owner, conversation_id or chat.conversation_id, request_id
        )
        assert pending.status is confirmations.ConfirmationStatus.PENDING
        assert not chat.client.cancelled
        assert not chat._abort_event.is_set()
        chat.client.releases[0].set()
        assert (await asyncio.wait_for(task, 1))[-1].stop_reason == "end_turn"

    asyncio.run(run())


def test_accepted_but_not_started_request_is_not_the_current_turn(chat):
    async def run():
        stream = runtime.prompt(
            chat.key, PromptRequest(text="waiting", request_id="waiting")
        )
        try:
            assert (await anext(stream)).type.value == "accepted"
            assert not await runtime.abort_turn(USER, chat.conversation_id, "waiting")
            assert not chat._abort_event.is_set()
        finally:
            await stream.aclose()

    asyncio.run(run())


def test_stale_stop_during_later_turn_does_not_cancel_it_or_a_queued_turn(chat):
    async def run():
        first = asyncio.create_task(collect(chat, "first"))
        await chat.client.started.get()
        second = runtime.prompt(
            chat.key,
            PromptRequest(text="second", request_id="second"),
            on_busy="queue",
        )
        assert (await anext(second)).type.value == "accepted"
        assert (await anext(second)).type.value == "queued"
        assert not await runtime.abort_turn(USER, chat.conversation_id, "second")
        assert not chat.client.cancelled

        # Let the next turn begin while the old provider's cancel call settles.
        chat.client.abort_release = asyncio.Event()
        stopping = asyncio.create_task(
            runtime.abort_turn(USER, chat.conversation_id, "first")
        )
        assert (await asyncio.wait_for(first, 1))[-1].stop_reason == "cancelled"
        assert (await anext(second)).type.value == "heartbeat"
        assert (await anext(second)).text == "reply 1"
        assert not await runtime.abort_turn(USER, chat.conversation_id, "first")
        assert chat.client.cancelled == {0}
        assert not chat._abort_event.is_set()

        chat.client.abort_release.set()
        assert await asyncio.wait_for(stopping, 1)
        chat.client.releases[1].set()
        result = [event async for event in second]
        assert result[-1].stop_reason == "end_turn"
        assert chat.client.cancelled == {0}
        assert not turns.is_busy(USER, chat.conversation_id)

    asyncio.run(run())


def test_terminal_event_finishes_stop_target_before_consumer_closes_generator(chat):
    async def run():
        stream = runtime.prompt(
            chat.key, PromptRequest(text="finished", request_id="finished")
        )
        try:
            assert (await anext(stream)).type.value == "accepted"
            assert (await anext(stream)).type.value == "heartbeat"
            assert (await anext(stream)).text == "reply 0"
            chat.client.releases[0].set()
            assert (await anext(stream)).stop_reason == "end_turn"
            assert not await runtime.abort_turn(USER, chat.conversation_id, "finished")
            assert not await runtime.cancel_request(
                USER, chat.conversation_id, "finished"
            )
            assert not chat.client.cancelled
        finally:
            await stream.aclose()
        assert not await runtime.abort_turn(USER, chat.conversation_id, "finished")

    asyncio.run(run())


def test_rebound_telegram_key_does_not_stop_replacement_or_deny_its_confirmations(chat):
    async def run():
        stream = runtime.prompt(
            chat.key, PromptRequest(text="original", request_id="original")
        )
        try:
            assert (await anext(stream)).type.value == "accepted"
            assert (await anext(stream)).type.value == "heartbeat"
            assert (await anext(stream)).text == "reply 0"
            meta = conversations.new_conversation(USER, "tg")
            replacement = sessions.AgentSession(
                chat.key,
                "claude-code",
                Model(),
                user_id=USER,
                conversation_id=meta.id,
                is_busy=True,
            )
            sessions._sessions[str(chat.key)] = replacement
            pending = approval(str(chat.key))

            assert await runtime.abort_turn(USER, chat.conversation_id, "original")
            assert not replacement.client.cancelled
            assert not replacement._abort_event.is_set()
            assert pending.status is confirmations.ConfirmationStatus.PENDING
            assert not await runtime.abort_turn(USER, meta.id, "original")
        finally:
            await stream.aclose()

    asyncio.run(run())


def test_stop_during_refresh_prevents_execution_without_aborting_idle_client(
    chat, monkeypatch
):
    async def run():
        refreshing, resume = asyncio.Event(), asyncio.Event()

        async def refresh(key):
            refreshing.set()
            await resume.wait()
            return []

        monkeypatch.setattr(sessions, "refresh_if_stale", refresh)
        task = asyncio.create_task(collect(chat, "refreshing"))
        await refreshing.wait()
        assert await runtime.abort_turn(USER, chat.conversation_id, "refreshing")
        assert not await runtime.abort_turn(USER, chat.conversation_id, "refreshing")
        assert not chat.client.cancelled
        resume.set()
        assert (await asyncio.wait_for(task, 1))[-1].stop_reason == "cancelled"
        assert not chat.client.releases

    asyncio.run(run())


@pytest.mark.parametrize("policy", ["queue", "steer"])
def test_cancel_queued_request_wakes_its_consumer_without_cancelling_the_producer(
    chat, policy
):
    async def run():
        current = asyncio.create_task(collect(chat, "current"))
        await chat.client.started.get()
        queued = runtime.prompt(
            chat.key, PromptRequest(text="queued", request_id="queued"), on_busy=policy
        )
        assert (await anext(queued)).type.value == "accepted"
        if policy == "queue":
            assert (await anext(queued)).type.value == "queued"

        async def producer():
            events = [event async for event in queued]
            return events[-1].stop_reason, "producer continued"

        waiting = asyncio.create_task(producer())
        assert await runtime.cancel_request(USER, chat.conversation_id, "queued")
        assert await asyncio.wait_for(waiting, 1) == ("cancelled", "producer continued")
        assert chat.client.cancelled == set()
        assert turns.pending_requests(USER, chat.conversation_id) == {"current"}
        chat.client.releases[0].set()
        assert (await current)[-1].stop_reason == "end_turn"

    asyncio.run(run())
    transcript = conversations.read_transcript(USER, chat.conversation_id)
    assert any(
        t.request_id == "queued" and t.stop_reason == "cancelled" for t in transcript
    )


def test_batch_cancel_claims_existing_requests_and_preserves_new_input(chat):
    async def run():
        first = asyncio.create_task(collect(chat, "first"))
        await chat.client.started.get()
        second = runtime.prompt(
            chat.key, PromptRequest(text="second", request_id="second"), on_busy="queue"
        )
        assert (await anext(second)).type.value == "accepted"
        assert (await anext(second)).type.value == "queued"

        async def finish_second():
            return [event async for event in second]

        second_task = asyncio.create_task(finish_second())
        chat.client.abort_release = asyncio.Event()
        stop = asyncio.create_task(
            runtime.cancel_requests(
                [
                    (USER, chat.conversation_id, "first"),
                    (USER, chat.conversation_id, "second"),
                ]
            )
        )
        assert (await asyncio.wait_for(first, 1))[-1].stop_reason == "cancelled"
        assert (await asyncio.wait_for(second_task, 1))[-1].stop_reason == "cancelled"
        assert not stop.done()

        later = asyncio.create_task(collect(chat, "later"))
        assert await asyncio.wait_for(chat.client.started.get(), 1) == 1
        chat.client.abort_release.set()
        assert await stop == 2
        assert chat.client.cancelled == {0}
        chat.client.releases[1].set()
        assert (await later)[-1].stop_reason == "end_turn"

    asyncio.run(run())
