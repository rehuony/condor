"""Writer isolation and delivery worker ownership."""

import asyncio

import pytest

from condor.runtime.events import RuntimeEvent
from condor.telegram.output import renderer
from condor.telegram.output.writer import chat_writer
from handlers.agents.stream import TelegramStreamer
from tests.support.telegram import Bot, feed


@pytest.mark.asyncio
async def test_cancelled_before_worker_runs_releases_request():
    target = TelegramStreamer(Bot(), 42)
    await feed(target, "answer")
    target.start_edit_loop()
    await target.stop_edit_loop()
    assert target not in renderer._live
    assert target._edit_task.done()


@pytest.mark.asyncio
async def test_owner_cancellation_does_not_leave_an_orphan_final_sender():
    bot = Bot()
    entered = asyncio.Event()

    async def send(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42)
    await feed(target, "answer")
    await target.process_event(RuntimeEvent.done())
    waiter = asyncio.create_task(target.finalize())
    await entered.wait()
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    await target.stop_edit_loop()
    assert target._final_task.done()
    assert not chat_writer(42).lock.locked()
    assert target.delivery.status == "delivery_unknown"


@pytest.mark.asyncio
async def test_slow_chat_does_not_block_other_chat():
    blocked = chat_writer(42)
    async with blocked.lock:
        next_chat = TelegramStreamer(Bot(), 43)
        await feed(next_chat, "other chat")
        await next_chat.process_event(RuntimeEvent.done())
        result = await asyncio.wait_for(next_chat.finalize(), 1)
        assert result.status == "complete"
