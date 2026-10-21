"""Ordinary message progress and final formatting reuse acknowledged messages."""

import asyncio

import pytest

from condor.runtime.events import RuntimeEvent
from condor.telegram.errors import bad_request
from condor.telegram.output.renderer import TelegramStreamer
from tests.support.telegram import Bot, feed, message_text


@pytest.mark.asyncio
async def test_progress_and_final_reuse_one_message():
    bot = Bot()
    target = TelegramStreamer(bot, 42, request_id="r")
    await feed(target, "hello")
    await target._deliver(False)
    await feed(target, " world")
    await target._deliver(False)
    assert len(bot.messages) == 1
    assert bot.edits[-1]["text"].startswith("hello world\n\n")
    assert all(call["message_id"] == 101 for call in bot.edits)
    await target.process_event(RuntimeEvent.done())
    first, second = await asyncio.gather(target.finalize(), target.finalize())
    assert first is second and first.status == "complete"
    assert first.message_ids == [101]
    assert len(bot.messages) == 1
    assert message_text(bot.edits[-1]) == "hello world"
    assert bot.edits[-1]["reply_markup"] is None


@pytest.mark.asyncio
async def test_parse_failure_falls_back_in_place_without_replaying_body():
    bot = Bot()
    original = bot.edit_message_text
    attempts = []

    async def edit(**kwargs):
        attempts.append(kwargs)
        if kwargs.get("rich_message") is not None:
            raise bad_request("can't parse entities")
        return await original(**kwargs)

    target = TelegramStreamer(bot, 42)
    await feed(target, "Answer **ready**")
    await target._deliver(False)
    bot.edit_message_text = edit
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    assert len(bot.messages) == 1
    assert len(attempts) == 2
    assert {call["message_id"] for call in attempts} == {101}
    assert bot.edits[-1]["text"] == "Answer **ready**"
    assert bot.edits[-1]["parse_mode"] is None
