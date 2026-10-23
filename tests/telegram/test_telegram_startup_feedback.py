"""Editable receipts precede preparation and preserve request identity."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Update

import config_manager
from condor.runtime.events import EventType, RuntimeEvent
from condor.telegram.ingress import get_ingress
from condor.telegram.output import runs
from condor.telegram.update import TgUpdate
from handlers.agents import input as agent_input
from handlers.agents.stop import handle_stop_callback
from tests.support.telegram import Bot


@pytest.fixture
def startup(monkeypatch):
    monkeypatch.setattr(
        config_manager,
        "get_config_manager",
        lambda: SimpleNamespace(get_user_role=lambda _: config_manager.UserRole.ADMIN),
    )
    monkeypatch.setattr("condor.telegram.ingress._instance", None)
    monkeypatch.setattr(runs, "_runs", {})
    monkeypatch.setattr("condor.telegram.output.writer.UPDATE_INTERVAL", 0.01)
    monkeypatch.setattr("condor.runtime.conversations.has_request", lambda *_: False)
    monkeypatch.setattr(agent_input, "_notice_input_secrets", AsyncMock())
    ready = asyncio.Event()
    entered = asyncio.Event()
    answered = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def ensure(update, context, **kwargs):
        entered.set()
        await ready.wait()
        return SimpleNamespace(conversation_id="test-conversation", alive=True)

    async def prompt(key, request, **kwargs):
        calls.append(request)
        yield RuntimeEvent(
            type=EventType.ACCEPTED,
            data={
                "request_id": request.request_id,
                "conversation_id": "test-conversation",
            },
        )
        yield RuntimeEvent(type=EventType.TEXT, data={"text": "The answer"})
        answered.set()
        await finish.wait()
        yield RuntimeEvent.done()

    monkeypatch.setattr(agent_input, "_ensure_chat_session", ensure)
    monkeypatch.setattr(agent_input.runtime, "prompt", prompt)
    update = TgUpdate.from_aiogram(
        Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "date": 1,
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Test"},
                    "text": "Read this",
                },
            }
        )
    )
    bot = Bot()
    return SimpleNamespace(
        update=update,
        context=SimpleNamespace(bot=bot, user_data={}, chat_data={}),
        bot=bot,
        ready=ready,
        entered=entered,
        answered=answered,
        finish=finish,
        calls=calls,
    )


async def wait_for(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


def visible_text(bot, message_id=101):
    from tests.support.telegram import message_text

    edits = [edit for edit in bot.edits if edit["message_id"] == message_id]
    return message_text(edits[-1]) if edits else bot.messages[message_id - 101]["text"]


async def press_stop(bot, message_id=101):
    target = next(
        target for target in runs._runs.values() if message_id in target.message_ids
    )
    query = SimpleNamespace(
        data=f"agent:stop_run:{target.token}",
        message=SimpleNamespace(message_id=message_id),
        answer=AsyncMock(),
    )
    await handle_stop_callback(
        SimpleNamespace(
            effective_chat=SimpleNamespace(id=42),
            effective_user=SimpleNamespace(id=42),
            callback_query=query,
        ),
        None,
    )


@pytest.mark.asyncio
async def test_cold_session_receipt_precedes_acceptance_and_is_edited_in_place(startup):
    task = asyncio.create_task(
        agent_input.agent_message_handler(startup.update, startup.context)
    )
    try:
        await wait_for(startup.entered.is_set)
        await wait_for(lambda: startup.bot.messages)
        assert not startup.calls
        assert startup.bot.messages[0]["reply_markup"]
        assert (
            "agent" in visible_text(startup.bot).lower()
            or "preparing" in visible_text(startup.bot).lower()
        )
        startup.ready.set()
        await wait_for(startup.answered.is_set)
        await wait_for(lambda: "The answer" in visible_text(startup.bot))
        startup.finish.set()
        await task
        assert len(startup.calls) == 1
        assert len(startup.bot.messages) == 1
        assert visible_text(startup.bot) == "The answer"
        assert not runs._runs
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [True, False], ids=["inline-stop", "ingress-stop"])
async def test_stop_during_startup_finishes_receipt_without_executing(startup, inline):
    task = asyncio.create_task(
        agent_input.agent_message_handler(startup.update, startup.context)
    )
    try:
        await wait_for(startup.entered.is_set)
        await wait_for(lambda: startup.bot.messages)
        if inline:
            await press_stop(startup.bot)
        else:
            assert get_ingress().cancel(42)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert not startup.calls and not get_ingress()._pending and not runs._runs
        assert len(startup.bot.messages) == 1
        assert "Stopped" in visible_text(startup.bot)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_known_text_replay_stays_silent_before_session_startup(
    startup, monkeypatch
):
    monkeypatch.setattr("condor.runtime.conversations.has_request", lambda *_: True)
    await agent_input.agent_message_handler(startup.update, startup.context)
    assert not startup.entered.is_set() and not startup.calls
    assert not startup.bot.messages and not runs._runs
    assert not get_ingress()._pending


@pytest.mark.asyncio
async def test_accepted_queue_stop_uses_runtime_cancellation_without_cancelling_owner(
    startup, monkeypatch
):
    waiting = asyncio.Event()
    cancelled = asyncio.Event()
    claimed = []

    async def queued(key, request, **kwargs):
        yield RuntimeEvent(
            type=EventType.ACCEPTED,
            data={
                "request_id": request.request_id,
                "conversation_id": "test-conversation",
            },
        )
        yield RuntimeEvent.queued()
        waiting.set()
        await cancelled.wait()
        yield RuntimeEvent.done("cancelled")

    async def cancel_requests(requests):
        claimed.extend(requests)
        cancelled.set()

    monkeypatch.setattr(agent_input.runtime, "prompt", queued)
    monkeypatch.setattr(agent_input.runtime, "cancel_requests", cancel_requests)
    task = asyncio.create_task(
        agent_input.agent_message_handler(startup.update, startup.context)
    )
    try:
        await wait_for(startup.entered.is_set)
        await wait_for(lambda: startup.bot.messages)
        startup.ready.set()
        await wait_for(waiting.is_set)
        await press_stop(startup.bot)
        await asyncio.wait_for(task, 1)
        assert not task.cancelled() and not startup.calls
        assert claimed == [(42, "test-conversation", "telegram:42:1")]
        assert "Stopped" in visible_text(startup.bot)
        assert not runs._runs
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_each_preparation_gets_a_receipt_before_fifo_admission(startup):
    first = asyncio.create_task(
        agent_input.agent_message_handler(startup.update, startup.context)
    )
    second_update = TgUpdate.from_aiogram(
        Update.model_validate(
            {
                "update_id": 2,
                "message": {
                    "message_id": 2,
                    "date": 1,
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Test"},
                    "text": "And this",
                },
            }
        )
    )
    second = None
    try:
        await wait_for(startup.entered.is_set)
        second = asyncio.create_task(
            agent_input.agent_message_handler(second_update, startup.context)
        )
        await wait_for(lambda: len(startup.bot.messages) == 2)
        assert "Waiting" in visible_text(startup.bot, 102)
        assert len(get_ingress()._pending) == 2
        await press_stop(startup.bot)
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert all("Stopped" in visible_text(startup.bot, mid) for mid in (101, 102))
        assert not startup.calls and not get_ingress()._pending and not runs._runs
    finally:
        first.cancel()
        if second is not None:
            second.cancel()
        await asyncio.gather(
            first, *([second] if second else []), return_exceptions=True
        )


@pytest.mark.asyncio
async def test_receipt_is_visible_while_secret_notice_is_waiting(startup, monkeypatch):
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def notice(*args):
        entered.set()
        await resume.wait()

    monkeypatch.setattr(agent_input, "_notice_input_secrets", notice)
    task = asyncio.create_task(
        agent_input.agent_message_handler(startup.update, startup.context)
    )
    try:
        await wait_for(entered.is_set)
        await wait_for(lambda: startup.bot.messages)
        assert not startup.entered.is_set()
        resume.set()
        startup.ready.set()
        startup.finish.set()
        await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_session_start_updates_the_existing_receipt(startup, monkeypatch):
    from aiogram.types import Message

    from handlers.agents import session

    async def fail(**kwargs):
        startup.entered.set()
        await startup.ready.wait()
        raise RuntimeError("synthetic startup failure")

    reply = AsyncMock()
    monkeypatch.setattr(Message, "answer", reply)
    monkeypatch.setattr(session, "get_session", AsyncMock(return_value=None))
    monkeypatch.setattr(session, "_reclaim_default_agent", lambda _: "claude-code")
    monkeypatch.setattr(session, "resolve_chat_binding", lambda _: (None, ""))
    monkeypatch.setattr(session, "_is_agent_available", lambda _: True)
    monkeypatch.setattr(session, "_create_tg_session", fail)
    monkeypatch.setattr(
        agent_input, "_ensure_chat_session", session._ensure_chat_session
    )
    task = asyncio.create_task(
        agent_input.agent_message_handler(startup.update, startup.context)
    )
    try:
        await wait_for(startup.entered.is_set)
        await wait_for(lambda: startup.bot.messages)
        startup.ready.set()
        await asyncio.wait_for(task, 1)
        assert not startup.calls and not runs._runs
        assert len(startup.bot.messages) == 1
        assert "Could not start the agent" in visible_text(startup.bot)
        reply.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_does_not_wait_for_the_chat_delivery_cooldown(startup):
    from condor.telegram.output.writer import chat_writer

    writer = chat_writer(42)
    deadline = asyncio.get_running_loop().time() + 3600
    writer.next_send = deadline
    task = asyncio.create_task(
        agent_input.agent_message_handler(startup.update, startup.context)
    )
    try:
        await wait_for(startup.entered.is_set)
        await asyncio.wait_for(get_ingress().close(), 0.5)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert get_ingress().closed
        assert not get_ingress()._pending and not runs._runs
        assert not startup.bot.messages and not startup.bot.edits
        assert writer.next_send == deadline
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
