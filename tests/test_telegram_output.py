"""Output leases preserve FIFO even when a waiting UI is cancelled."""

import asyncio

import pytest

from condor import telegram_output
from condor.runtime.events import RuntimeEvent
from condor.telegram_output import OutputLease
from handlers.agents.stream import TelegramStreamer
from tests.test_telegram_layered_replies import Bot, feed, until


@pytest.mark.asyncio
async def test_cancelled_output_waiter_does_not_let_successor_overtake():
    first, cancelled, last = OutputLease(42), OutputLease(42), OutputLease(42)
    await first.acquire()
    cancelled.release()
    waiting = asyncio.create_task(last.acquire())
    await asyncio.sleep(0)
    assert not waiting.done()
    first.release()
    await waiting
    last.release()


@pytest.mark.asyncio
async def test_cancelled_before_worker_runs_releases_owned_output():
    target = TelegramStreamer(Bot(), 42)
    await feed(target, "answer")
    target.start_edit_loop()
    await target.stop_edit_loop()
    assert not telegram_output._tails


@pytest.mark.asyncio
async def test_final_stops_inflight_progress_before_sending_and_ignores_late_events():
    bot = Bot()
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def edit(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    bot.edit_message_text = edit
    target = TelegramStreamer(bot, 42, 10, mode="edit")
    target.start_edit_loop()
    await feed(target, "answer")
    await entered.wait()
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    await feed(target, "late")
    assert cancelled.is_set() and target._edit_task.done()
    assert bot.messages[-1]["text"] == "answer"
    assert target._buffer == "answer"


@pytest.mark.asyncio
async def test_owner_cancellation_does_not_leave_an_orphan_final_sender():
    bot = Bot()
    entered = asyncio.Event()

    async def send(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    bot.send_message = send
    target = TelegramStreamer(bot, 42)
    await feed(target, "answer")
    await target.process_event(RuntimeEvent.done())
    waiter = asyncio.create_task(target.finalize())
    await entered.wait()
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    await target.stop_edit_loop()
    assert target._final_task.done() and not telegram_output._tails


@pytest.mark.asyncio
async def test_slow_chat_does_not_block_other_chat():
    blocked = OutputLease(42)
    next_chat = TelegramStreamer(Bot(), 43)
    await feed(next_chat, "other chat")
    await next_chat.process_event(RuntimeEvent.done())
    result = await asyncio.wait_for(next_chat.finalize(), 1)
    assert result.status == "complete"
    blocked.release()
