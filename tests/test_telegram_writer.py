"""Writer pacing must protect delivery without delaying idle requests."""

import asyncio
from types import SimpleNamespace

import pytest

from condor.runtime.events import RuntimeEvent
from condor.telegram.errors import rate_limited
from condor.telegram.output import writer as output_writer
from condor.telegram.output.renderer import RichReplyStreamer
from condor.telegram.output.writer import ChatWriter, MessageUpdate
from tests.support.telegram import Bot, feed


async def started_write(writer, bot, latest, started):
    started.set()
    return await writer.write(bot, latest)


@pytest.fixture
def pacing(monkeypatch):
    """Expose the writer's pacing wait without changing the test event loop."""
    clock = SimpleNamespace(now=100.0, entered=asyncio.Queue(), release=asyncio.Event())

    async def sleep(delay):
        if delay > 0:
            clock.entered.put_nowait(delay)
            await clock.release.wait()
            clock.now += delay
        else:
            await asyncio.sleep(0)

    monkeypatch.setattr(
        output_writer,
        "asyncio",
        SimpleNamespace(
            Lock=asyncio.Lock,
            get_running_loop=lambda: SimpleNamespace(time=lambda: clock.now),
            sleep=sleep,
        ),
    )
    return clock


@pytest.mark.asyncio
async def test_no_update_returns_while_another_request_holds_the_writer(pacing):
    writer, bot = ChatWriter(10), Bot()
    writer.next_send = pacing.now + 60
    await writer.lock.acquire()
    try:
        assert await asyncio.wait_for(writer.write(bot, lambda: None), 1) == (
            None,
            None,
        )
        assert writer.lock.locked()
        assert writer.next_send == pacing.now + 60
        assert pacing.entered.empty()
        assert not bot.messages
    finally:
        writer.lock.release()


@pytest.mark.asyncio
async def test_update_withdrawn_while_waiting_for_lock_skips_the_cooldown(pacing):
    writer, bot = ChatWriter(11), Bot()
    writer.next_send = pacing.now + 60
    snapshot = MessageUpdate("Preparing…")
    started = asyncio.Event()
    await writer.lock.acquire()
    pending = asyncio.create_task(started_write(writer, bot, lambda: snapshot, started))
    try:
        await asyncio.wait_for(started.wait(), 1)
        snapshot = None
        writer.lock.release()
        assert await asyncio.wait_for(pending, 1) == (None, None)
        assert pacing.entered.empty()
        assert writer.next_send == pacing.now + 60
        assert not bot.messages
    finally:
        if writer.lock.locked():
            writer.lock.release()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "waiting_for,new_text",
    [("lock", "Latest answer"), ("cooldown", "Latest answer"), ("cooldown", None)],
)
async def test_waiting_request_uses_the_latest_snapshot(pacing, waiting_for, new_text):
    writer, bot = ChatWriter(12), Bot()
    snapshot = MessageUpdate("Obsolete answer")
    started = asyncio.Event()
    if waiting_for == "lock":
        await writer.lock.acquire()
    else:
        writer.next_send = pacing.now + 1
    pending = asyncio.create_task(started_write(writer, bot, lambda: snapshot, started))
    try:
        if waiting_for == "lock":
            await asyncio.wait_for(started.wait(), 1)
        else:
            assert await asyncio.wait_for(pacing.entered.get(), 1) == 1
        snapshot = MessageUpdate(new_text) if new_text is not None else None
        if waiting_for == "lock":
            writer.lock.release()
        else:
            pacing.release.set()
        update, result = await asyncio.wait_for(pending, 1)
        if new_text is None:
            assert (update, result) == (None, None)
            assert not bot.messages
        else:
            assert result.status == "confirmed"
            assert [message["text"] for message in bot.messages] == [new_text]
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        if writer.lock.locked():
            writer.lock.release()


@pytest.mark.asyncio
async def test_final_ack_completes_without_waiting_for_another_send_slot(
    pacing, monkeypatch
):
    monkeypatch.setattr(output_writer, "UPDATE_INTERVAL", 1.0)
    bot = Bot()
    stream = RichReplyStreamer(bot, 13, request_id="final-ack")
    try:
        await feed(stream, "Answer")
        await stream.process_event(RuntimeEvent.done())
        result = await asyncio.wait_for(stream.finalize(), 1)
        assert result.status == "complete"
        assert len(bot.messages) == 1
        assert pacing.entered.empty()
    finally:
        await stream.stop_edit_loop()


@pytest.mark.asyncio
async def test_retry_after_blocks_other_requests_and_coalesces_pending_edits(
    pacing, monkeypatch
):
    monkeypatch.setattr(output_writer, "UPDATE_INTERVAL", 1.0)
    writer, bot = ChatWriter(14), Bot()
    bot.send_failure = rate_limited(3)
    _, result = await writer.write(bot, lambda: MessageUpdate("First request"))
    assert result.reason == "rate_limit"
    deadline = writer.next_send
    bot.send_failure = None
    snapshot = MessageUpdate("Still running", message_id=101)
    attempts = []
    pending = asyncio.create_task(
        writer.write(
            bot,
            lambda: snapshot,
            request_id="other-request",
            on_attempt=lambda: attempts.append(pacing.now),
        )
    )
    try:
        assert await asyncio.wait_for(pacing.entered.get(), 1) == 3
        assert not bot.edits
        assert not attempts
        assert not pending.done()
        snapshot = MessageUpdate("Stopped", message_id=101)
        pacing.release.set()
        _, result = await asyncio.wait_for(pending, 1)
        assert result.status == "confirmed"
        assert pacing.now >= deadline
        assert attempts == [deadline]
        assert [edit["text"] for edit in bot.edits] == ["Stopped"]
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_waiting_request_gets_a_slot_before_an_active_request_sends_again():
    writer, bot = ChatWriter(15), Bot()
    entered, release, second_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    send = bot.send_message

    async def delayed_send(**kwargs):
        if kwargs["text"] == "First page":
            entered.set()
            await release.wait()
        return await send(**kwargs)

    bot.send_message = delayed_send

    async def first_request():
        await writer.write(bot, lambda: MessageUpdate("First page"))
        await writer.write(bot, lambda: MessageUpdate("Next page"))

    first = asyncio.create_task(first_request())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(
            started_write(
                writer,
                bot,
                lambda: MessageUpdate("Queued request receipt"),
                second_waiting,
            )
        )
        await asyncio.wait_for(second_waiting.wait(), 1)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert [message["text"] for message in bot.messages] == [
            "First page",
            "Queued request receipt",
            "Next page",
        ]
    finally:
        for task in (first, second):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (first, second) if task), return_exceptions=True
        )
