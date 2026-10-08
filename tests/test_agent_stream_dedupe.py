"""The streamer only edits what actually changed (PERF-092).

``_flush`` rebuilds the whole answer and rewrites every chunk twice a second.
Once an answer passes 4096 chars the split point stops moving, so chunk 0 and
every continuation but the last are byte-identical on each tick — edits that
Telegram rejects as "not modified" while still spending the per-chat rate
limit the *changed* chunk is waiting for.
"""

import asyncio
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest

from condor.runtime.events import EventType, RuntimeEvent
from handlers.agents.stream import MAX_MESSAGE_LEN, TelegramStreamer

CHAT_ID = 4242
MAIN_ID = 1


class _RecordingBot:
    """Records every call that actually reached the Bot API."""

    def __init__(self, fail_parse_mode: bool = False):
        self.edits: list[tuple[int, str, str | None]] = []
        self.sends: list[str] = []
        self._fail_parse_mode = fail_parse_mode
        self._next_id = 100

    async def edit_message_text(self, **kw):
        if self._fail_parse_mode and kw.get("parse_mode"):
            raise BadRequest("Can't parse entities: unmatched '_'")
        self.edits.append((kw["message_id"], kw["text"], kw.get("parse_mode")))
        return None

    async def send_message(self, **kw):
        self.sends.append(kw["text"])
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)


def _streamer(**kw):
    bot = _RecordingBot(**kw)
    return bot, TelegramStreamer(bot=bot, chat_id=CHAT_ID, message_id=MAIN_ID)


def _paragraphs(n: int) -> str:
    """Text that splits at stable paragraph boundaries."""
    return "\n\n".join(f"paragraph {i} " + "x" * 200 for i in range(n))


async def _feed(streamer: TelegramStreamer, text: str) -> None:
    await streamer.process_event(RuntimeEvent(type=EventType.TEXT, data={"text": text}))


def test_a_long_answer_only_re_edits_the_chunk_that_grew():
    """The whole point: 3 messages, 1 edit per tick — not 3."""
    bot, streamer = _streamer()

    async def drive():
        await _feed(streamer, _paragraphs(45))  # > 8000 chars -> 3 messages
        await streamer._flush(final=False)
        opening = len(bot.edits)
        for _ in range(4):
            await _feed(streamer, "\n\nand a little more")
            await streamer._flush(final=False)
        return opening

    opening = asyncio.run(drive())

    assert len(streamer._continuation_ids) == 2, "expected a 3-message answer"
    # First flush edits the placeholder and sends the two continuations.
    assert opening == 1
    tail_id = streamer._continuation_ids[-1]
    # Four more ticks, four edits — all of them to the message that grew.
    assert [mid for mid, _, _ in bot.edits[opening:]] == [tail_id] * 4


def test_an_unchanged_screen_is_not_re_edited():
    bot, streamer = _streamer()

    async def drive():
        await _feed(streamer, "the whole answer")
        for _ in range(5):
            await streamer._flush(final=False)

    asyncio.run(drive())

    assert [text for _, text, _ in bot.edits] == ["the whole answer"]


def test_finalize_re_sends_identical_text_as_markdown():
    """The formatting pass must land even when the characters don't change."""
    bot, streamer = _streamer()

    async def drive():
        await _feed(streamer, "plain answer with no markup")
        await streamer._flush(final=False)
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())

    assert bot.edits == [
        (MAIN_ID, "plain answer with no markup", None),
        (MAIN_ID, "plain answer with no markup", "HTML"),
    ]


@pytest.mark.parametrize("fail_parse_mode", [False, True])
def test_final_reply_has_an_absolute_research_link(fail_parse_mode, monkeypatch):
    monkeypatch.setattr("utils.config.WEB_URL", "https://dashboard.example.com")
    bot, streamer = _streamer(fail_parse_mode=fail_parse_mode)

    async def drive():
        await _feed(streamer, "[查看报告](/research/topic/report.md)")
        await streamer._flush(final=False)
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())

    url = "https://dashboard.example.com/research/topic/report.md"
    expected = (
        f"查看报告 ({url})" if fail_parse_mode else f'<a href="{url}">查看报告</a>'
    )
    assert bot.edits[-1] == (MAIN_ID, expected, None if fail_parse_mode else "HTML")


def test_finalize_reformats_every_chunk_of_a_long_answer():
    bot, streamer = _streamer()

    async def drive():
        await _feed(streamer, _paragraphs(45))
        await streamer._flush(final=False)
        opening = len(bot.edits)
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()
        return opening

    opening = asyncio.run(drive())

    ids = [MAIN_ID] + streamer._continuation_ids
    final_edits = bot.edits[opening:]
    assert [mid for mid, _, _ in final_edits] == ids
    assert all(mode == "HTML" for _, _, mode in final_edits)


def test_the_thinking_pulse_still_animates_every_tick():
    bot, streamer = _streamer()

    async def drive():
        for tick in range(3):
            streamer._tick = tick
            await streamer._flush(final=False)

    asyncio.run(drive())

    texts = [text for _, text, _ in bot.edits]
    assert texts == ["Thinking.", "Thinking..", "Thinking..."]


def test_a_parse_failure_still_retries_without_parse_mode():
    """The plain-text fallback carries the same text — it must not be deduped."""
    bot, streamer = _streamer(fail_parse_mode=True)

    async def drive():
        await _feed(streamer, "answer with a stray _ underscore")
        await streamer._flush(final=False)
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())

    text = "answer with a stray _ underscore"
    # Streaming edit, then the Markdown attempt fails and comes back as plain.
    assert bot.edits == [(MAIN_ID, text, None), (MAIN_ID, text, None)]


def test_a_not_modified_rejection_is_remembered():
    """Telegram saying "already there" is the strongest possible cache fill."""
    bot, streamer = _streamer()
    calls = []

    async def edit(**kw):
        calls.append(kw["text"])
        raise BadRequest("Message is not modified")

    bot.edit_message_text = edit

    async def drive():
        await _feed(streamer, "same")
        await streamer._flush(final=False)
        await streamer._flush(final=False)

    asyncio.run(drive())

    assert calls == ["same"]


def test_a_failed_edit_is_retried_on_the_next_tick():
    """Nothing reached the screen, so nothing should be cached."""
    bot, streamer = _streamer()
    attempts = []

    async def edit(**kw):
        attempts.append(kw["text"])
        raise BadRequest("Bad Request: message to edit not found")

    bot.edit_message_text = edit

    async def drive():
        await _feed(streamer, "same")
        await streamer._flush(final=False)
        await streamer._flush(final=False)

    asyncio.run(drive())

    assert attempts == ["same", "same"]


def test_chunking_assumption_holds():
    """Guards the premise: the split point does not move as the answer grows."""
    from handlers.agents.stream import _split_text

    base = _paragraphs(45)
    first = _split_text(base, MAX_MESSAGE_LEN)
    grown = _split_text(base + "\n\nand a little more", MAX_MESSAGE_LEN)

    assert len(first) == 3
    assert grown[:-1] == first[:-1]


def test_reasoning_and_tool_details_do_not_bury_the_answer():
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(
            RuntimeEvent(type=EventType.THOUGHT, data={"text": "private thought"})
        )
        await streamer.process_event(
            RuntimeEvent(
                type=EventType.TOOL_CALL,
                data={"tool_call_id": "1", "title": "mcp__condor__run_code"},
            )
        )
        await streamer._flush(final=False)
        await streamer.process_event(
            RuntimeEvent(
                type=EventType.TOOL_UPDATE,
                data={"tool_call_id": "1", "status": "completed"},
            )
        )
        await _feed(streamer, "**结论：等待。**\n\n保留重要依据。")
        await streamer.finalize()

    asyncio.run(drive())
    assert bot.edits[0][1] == "Working..."
    assert bot.edits[-1][1] == "<b>结论：等待。</b>\n\n保留重要依据。"
    assert all(
        "private thought" not in text and "run_code" not in text
        for _, text, _ in bot.edits
    )


def test_a_failed_continuation_does_not_shift_later_pages():
    bot, streamer = _streamer()
    attempts = 0
    original = bot.send_message

    async def send(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BadRequest("temporary send failure")
        return await original(**kwargs)

    bot.send_message = send
    source = _paragraphs(45)

    async def drive():
        await _feed(streamer, source)
        await streamer._flush(final=False)
        assert not streamer._continuation_ids
        await streamer._flush(final=False)

    asyncio.run(drive())
    assert bot.edits[0][1] + "".join(bot.sends) == source


def test_http_continuations_keep_their_ids_and_stale_tails_are_removed():
    from condor.telegram_text import plain_text

    screen = {}
    deleted = []
    next_id = 10

    async def edit(**kwargs):
        screen[kwargs["message_id"]] = kwargs["text"]
        return {"ok": True}

    async def send(**kwargs):
        nonlocal next_id
        next_id += 1
        screen[next_id] = kwargs["text"]
        return {"ok": True, "result": {"message_id": next_id}}

    async def delete(**kwargs):
        deleted.append(kwargs["message_id"])
        screen.pop(kwargs["message_id"])
        return {"ok": True}

    bot = SimpleNamespace(
        edit_message_text=edit, send_message=send, delete_message=delete
    )
    streamer = TelegramStreamer(bot, CHAT_ID, MAIN_ID)
    source = ("[来源](https://example.com/evidence)\n\n" * 150) + "最终条件"

    async def drive():
        await _feed(streamer, source)
        await streamer._flush(final=False)
        assert streamer._continuation_ids
        await streamer.finalize()

    asyncio.run(drive())
    assert deleted
    visible = "".join(plain_text(html) for html in screen.values())
    assert visible.count("https://example.com/evidence") == 150
    assert visible.endswith("最终条件")


def test_plain_stream_fallback_keeps_clickable_source_destinations():
    bot, streamer = _streamer(fail_parse_mode=True)

    async def drive():
        await _feed(streamer, "结论\n\n[来源](https://example.com/evidence)")
        await streamer.finalize()

    asyncio.run(drive())
    assert "https://example.com/evidence" in bot.edits[-1][1]


def test_final_send_failure_is_retried_after_the_live_loop_has_ended(monkeypatch):
    bot, streamer = _streamer()
    original = bot.send_message
    attempts = 0

    async def send(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BadRequest("temporary send failure")
        return await original(**kwargs)

    async def sleep(_):
        pass

    bot.send_message = send
    monkeypatch.setattr("handlers.agents.stream.asyncio.sleep", sleep)
    source = _paragraphs(45)

    async def drive():
        await _feed(streamer, source)
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())
    assert len(bot.sends) == 2
    assert bot.edits[0][1] + "".join(bot.sends) == source
    assert not streamer._needs_edit
