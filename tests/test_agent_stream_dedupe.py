"""Edit-mode dedupe retained; final answers now use a separate acknowledged send."""

import pytest
from telegram.error import BadRequest

from condor.runtime.events import RuntimeEvent
from handlers.agents.stream import TelegramStreamer
from tests.test_telegram_layered_replies import Bot, feed


@pytest.mark.asyncio
async def test_long_answer_only_edits_the_chunk_that_grew():
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10, mode="edit")
    await feed(target, "\n\n".join(f"paragraph {i} " + "x" * 200 for i in range(45)))
    await target._flush()
    opening = len(bot.edits)
    assert len(target._continuation_ids) == 2
    for _ in range(4):
        await feed(target, "\n\nand a little more")
        await target._flush()
    assert [c["message_id"] for c in bot.edits[opening:]] == [
        target._continuation_ids[-1]
    ] * 4
    await target.stop_edit_loop()


@pytest.mark.asyncio
async def test_unchanged_text_dedupes_but_markup_changes_do_not():
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10, mode="edit", request_id="r")
    await feed(target, "same")
    await target._flush()
    await target._flush()
    assert len(bot.edits) == 1
    result = await target._io(
        "edit", text="same", mode="HTML", message_id=10, markup=None
    )
    assert result.status == "confirmed"
    assert len(bot.edits) == 2 and bot.edits[-1]["reply_markup"] is None
    await target.stop_edit_loop()


@pytest.mark.asyncio
async def test_not_modified_is_cached_with_exact_requested_markup():
    bot = Bot()
    bot.edit_failure = BadRequest("Message is not modified")
    target = TelegramStreamer(bot, 42, 10, mode="edit")
    await feed(target, "same")
    await target._flush()
    await target._flush()
    assert len(bot.edits) == 1
    await target.stop_edit_loop()


@pytest.mark.asyncio
async def test_parse_failure_keeps_readable_preview_and_final_source_links(monkeypatch):
    monkeypatch.setattr("utils.config.WEB_URL", "https://dashboard.example.com")
    bot = Bot()
    original = bot.edit_message_text

    async def edit(**kw):
        if kw.get("parse_mode"):
            raise BadRequest("can't parse entities")
        return await original(**kw)

    bot.edit_message_text = edit
    target = TelegramStreamer(bot, 42, 10, mode="edit")
    await feed(target, "[查看报告](/research/topic/report.md)")
    await target._flush()
    assert (
        bot.edits[-1]["text"]
        == "查看报告 (https://dashboard.example.com/research/topic/report.md)"
    )
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert (
        'href="https://dashboard.example.com/research/topic/report.md"'
        in bot.messages[-1]["text"]
    )


@pytest.mark.asyncio
async def test_shorter_final_removes_all_owned_preview_continuations():
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10, mode="edit")
    await feed(target, "x" * 9000)
    await target._flush()
    owned = {10, *target._continuation_ids}
    # Exercise cleanup independently of how a provider supplies its body.
    target._buffer = "short final"
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert {c["message_id"] for c in bot.deleted} == owned
    assert target.delivery.confirmed == 1
