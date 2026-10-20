"""Draft updates share one draft_id; parse failures still deliver a final."""

import pytest

from condor.runtime.events import RuntimeEvent
from condor.telegram.errors import bad_request
from condor.telegram.output.renderer import RICH_MAX_LEN, TelegramStreamer
from tests.support.telegram import Bot, feed


@pytest.mark.asyncio
async def test_progress_reuses_one_draft_id():
    bot = Bot()
    target = TelegramStreamer(bot, 42, request_id="r")
    await feed(target, "hello")
    await target._flush()
    await feed(target, " world")
    await target._flush()
    assert {c["draft_id"] for c in bot.drafts} == {target.draft_id}
    assert bot.drafts[-1]["text"] == "hello world"
    await target.stop_edit_loop()


@pytest.mark.asyncio
async def test_native_stop_flags_disable_further_drafts():
    bot = Bot()
    target = TelegramStreamer(bot, 42, request_id="r")
    await feed(target, "partial")
    await target._flush()
    target.target.stopped = True
    await feed(target, " more")
    await target._flush()
    assert len(bot.drafts) == 1
    await target.stop_edit_loop()


@pytest.mark.asyncio
async def test_parse_failure_falls_back_to_plain_final():
    bot = Bot()
    original = bot.send_rich_message

    async def send_rich(**kw):
        raise bad_request("can't parse entities")

    bot.send_rich_message = send_rich
    target = TelegramStreamer(bot, 42)
    await feed(target, "plain enough")
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    assert bot.messages[-1]["text"] == "plain enough"
    assert "rich_message" not in bot.messages[-1]


@pytest.mark.asyncio
async def test_overlong_final_is_split():
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await feed(target, "x" * (RICH_MAX_LEN + 50))
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    assert result.confirmed == 2
    assert bot.messages[0].get("disable_notification") is False
    assert bot.messages[1].get("disable_notification") is True
