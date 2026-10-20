"""Synthetic events only: no Telegram, model, exchange or account connections."""

import asyncio

import aiohttp
import pytest

from condor.runtime.events import EventType, RuntimeEvent
from condor.telegram.errors import bad_request, network_failure, rate_limited
from condor.telegram.output.renderer import RICH_MAX_LEN
from handlers.agents.stream import QUEUED_LABEL, TelegramStreamer
from tests.support.telegram import Bot, feed, message_text
from tests.support.telegram_offline import offline_app

OVERFLOW = "x" * RICH_MAX_LEN + "tail"


def connect_error(message="synthetic"):
    """What aiohttp raises when the socket never opened."""
    key = aiohttp.client_reqrep.ConnectionKey(
        "api.telegram.org", 443, True, True, None, None, None
    )
    return aiohttp.ClientConnectorError(key, OSError(message))


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


def _finals(bot):
    return [
        call
        for call in bot.messages
        if call.get("rich_message") is not None
        or call.get("disable_notification") is False
    ]


@pytest.mark.asyncio
async def test_default_accumulates_text_and_finalizes_once():
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "Synthetic ")
    await target._flush(final=False)
    await feed(target, "answer")
    await target._flush(final=False)
    assert {call["draft_id"] for call in bot.drafts} == {target.draft_id}
    assert bot.drafts[-1]["text"] == "Synthetic answer"
    assert all(call.get("can_stop") is True for call in bot.drafts)
    assert all(call.get("keep_on_stop") is True for call in bot.drafts)
    await target.process_event(RuntimeEvent.done())
    await asyncio.gather(target.finalize(), target.finalize())
    assert [call["text"] for call in _finals(bot)] == ["Synthetic answer"]
    assert bot.deleted == [{"chat_id": 42, "message_id": 10}]


@pytest.mark.asyncio
async def test_deleted_placeholder_does_not_prevent_final_delivery():
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "Synthetic final")
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert any(call["text"] == "Synthetic final" for call in bot.messages)
    assert result.status == "complete"


@pytest.mark.asyncio
async def test_late_events_and_error_done_preserve_terminal_state():
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await feed(target, "Partial")
    await target.process_event(RuntimeEvent.error("Authorization: Bearer SECRET"))
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    before = target._buffer
    await feed(target, "LATE")
    assert target._buffer == before
    assert target.state == "failed"
    assert all("SECRET" not in call["text"] for call in bot.messages + bot.drafts)


@pytest.mark.asyncio
async def test_thought_remains_hidden_and_tools_are_not_fabricated_success():
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await target.process_event(
        RuntimeEvent(type=EventType.THOUGHT, data={"text": "PRIVATE_MARKER"})
    )
    await target.process_event(
        RuntimeEvent(
            type=EventType.TOOL_CALL,
            data={"tool_call_id": "t", "status": "in_progress"},
        )
    )
    await feed(target, "Answer")
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert all(
        "PRIVATE_MARKER" not in call["text"] for call in bot.messages + bot.drafts
    )
    assert target.tools["t"] == "in_progress"


@pytest.mark.asyncio
async def test_queued_draft_does_not_type_or_edit():
    bot = Bot()
    target = TelegramStreamer(bot, 42, request_id="r", user_id=42, conversation_id="c")
    target.start_edit_loop()
    await target.process_event(RuntimeEvent.queued())
    await until(lambda: bot.drafts)
    await asyncio.sleep(0.04)
    assert not bot.actions and not bot.edits
    assert [call["text"] for call in bot.drafts] == [QUEUED_LABEL]
    await target.process_event(RuntimeEvent(type=EventType.HEARTBEAT))
    await feed(target, "Result")
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert target._edit_task.done()
    assert bot.messages[-1]["text"] == "Result"


@pytest.mark.asyncio
async def test_drafts_coalesce_to_the_latest_snapshot(monkeypatch):
    from condor.telegram.output import renderer

    monkeypatch.setattr(renderer, "DRAFT_INTERVAL", 0.03)
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    target.start_edit_loop()
    await feed(target, "Start")
    await until(lambda: bot.drafts)
    for piece in [" ", "one", " two", " three"]:
        await feed(target, piece)
    await until(
        lambda: any(call["text"] == "Start one two three" for call in bot.drafts)
    )
    assert {call["draft_id"] for call in bot.drafts} == {target.draft_id}
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert bot.messages[-1]["text"] == "Start one two three"
    assert bot.messages[-1]["disable_notification"] is False


@pytest.mark.asyncio
async def test_groups_skip_drafts_and_still_send_the_final():
    bot = Bot()
    target = TelegramStreamer(bot, -100)
    await feed(target, "group answer")
    await target._flush()
    assert not bot.drafts
    assert target.draft_id == 0
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    assert bot.messages[-1]["text"] == "group answer"


@pytest.mark.asyncio
async def test_interrupt_resends_the_same_draft_id(monkeypatch):
    from condor.telegram.output import renderer

    monkeypatch.setattr(renderer, "DRAFT_INTERVAL", 10)
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    target.start_edit_loop()
    await feed(target, "hello")
    await until(lambda: bot.drafts)
    count = len(bot.drafts)
    await asyncio.sleep(0.05)
    assert len(bot.drafts) == count
    target.nudge()
    await until(lambda: len(bot.drafts) > count)
    assert {call["draft_id"] for call in bot.drafts} == {target.draft_id}
    await target.stop_edit_loop()


@pytest.mark.asyncio
async def test_failed_draft_does_not_prevent_final_delivery():
    bot = Bot()
    bot.draft_failure = bad_request("RICH_MESSAGE_UNAVAILABLE")
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "answer")
    await target._flush()
    await target._flush()
    assert target._progress_disabled
    assert len(bot.drafts) == 0
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert target.delivery.status == "complete"
    assert bot.messages[-1]["text"] == "answer"


@pytest.mark.asyncio
async def test_ambiguous_final_has_no_automatic_send_retry(caplog):
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "answer")
    await target._flush()
    attempts = []

    async def timeout(**kwargs):
        attempts.append(kwargs)
        raise network_failure("https://api.telegram.org/botSECRET/sendMessage")

    bot.send_rich_message = timeout
    await target.process_event(RuntimeEvent.done())
    first, second = await asyncio.gather(target.finalize(), target.finalize())
    assert first is second and first.status == "delivery_unknown"
    assert len(attempts) == 1
    assert "SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_partial_delivery_keeps_confirmed_cursor_and_only_first_notifies():
    bot = Bot()
    original = bot.send_rich_message
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 2:
            raise network_failure("Request timeout error")
        return await original(**kwargs)

    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, OVERFLOW)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    await target.finalize()
    assert result.status == "delivery_unknown" and result.confirmed == 1
    assert result.message_ids == [101]
    assert len(attempts) == 2
    assert [call["disable_notification"] for call in attempts] == [False, True]


@pytest.mark.asyncio
async def test_definite_connect_failure_retries_only_unconfirmed_page():
    bot = Bot()
    original = bot.send_rich_message
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 2:
            raise network_failure("connect", cause=connect_error())
        return await original(**kwargs)

    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42)
    await feed(target, OVERFLOW)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete" and result.confirmed == 2
    assert len(attempts) == 3 and attempts[1] == attempts[2]
    assert bot.messages[-1]["text"].endswith("tail")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,retry",
    [
        (connect_error(), True),
        (aiohttp.ConnectionTimeoutError("connect timed out"), True),
        (aiohttp.SocketTimeoutError("read timed out"), False),
        (aiohttp.ClientOSError("write failed"), False),
        (aiohttp.ClientPayloadError("response truncated"), False),
        (asyncio.TimeoutError(), False),
    ],
    ids=["connect", "connect-timeout", "read-timeout", "os", "payload", "timeout"],
)
async def test_real_library_retries_only_preconnection_failures(
    tmp_path, failure, retry
):
    attempts = []

    def respond(method, fields):
        assert method == "sendRichMessage"
        attempts.append(fields)
        if len(attempts) == 2:
            return failure
        return {
            "message_id": 100 + len(attempts),
            "date": 1,
            "chat": {"id": 42, "type": "private"},
            "text": "answer",
        }

    app = offline_app(tmp_path, respond)
    target = TelegramStreamer(app.bot, 42)
    try:
        await feed(target, OVERFLOW)
        await target.process_event(RuntimeEvent.done())
        result = await target.finalize()
        if retry:
            assert result.status == "complete" and result.confirmed == 2
            assert len(attempts) == 3 and attempts[1] == attempts[2]
            assert result.message_ids == [101, 103]
        else:
            assert result.status == "delivery_unknown" and result.confirmed == 1
            assert len(attempts) == 2 and result.message_ids == [101]
    finally:
        await target.stop_edit_loop()
        await app.close()


@pytest.mark.asyncio
async def test_none_ack_is_not_success():
    bot = Bot()

    async def send(**kwargs):
        return None

    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "answer")
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "delivery_unknown" and not result.message_ids


@pytest.mark.asyncio
async def test_parse_failure_falls_back_without_losing_code_or_links():
    bot = Bot()
    original = bot.send_message

    async def send_rich(**kwargs):
        raise bad_request("Can't parse entities")

    async def send(**kwargs):
        return await original(**kwargs)

    bot.send_rich_message = send_rich
    bot.send_message = send
    target = TelegramStreamer(bot, 42)
    await feed(
        target, "中文 **answer** [source](https://example.com)\n\n```\nx < y & z\n```"
    )
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    text = "".join(call["text"] for call in bot.messages)
    assert "https://example.com" in text and "x < y & z" in text
    assert all(call.get("rich_message") is None for call in bot.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [1, 2])
async def test_retry_after_waits_and_never_replays_confirmed_page(monkeypatch, delay):
    bot = Bot()
    original = bot.send_rich_message
    attempts, sleeps = [], []

    async def send(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 2:
            raise rate_limited(delay)
        return await original(**kwargs)

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("condor.telegram.transport.asyncio.sleep", sleep)
    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42)
    await feed(target, OVERFLOW)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete" and sleeps == [delay]
    assert len(attempts) == 3 and attempts[1] == attempts[2]
    assert [call["disable_notification"] for call in bot.messages] == [False, True]


@pytest.mark.asyncio
async def test_rate_limited_draft_uses_latest_snapshot(monkeypatch):
    from condor.telegram.output import renderer

    monkeypatch.setattr(renderer, "DRAFT_INTERVAL", 0.01)
    bot = Bot()
    original = bot.send_rich_message_draft
    attempts = []

    async def draft(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise rate_limited(0.05)
        return await original(**kwargs)

    bot.send_rich_message_draft = draft
    target = TelegramStreamer(bot, 42, 10)
    target.start_edit_loop()
    await feed(target, "old")
    await until(lambda: attempts)
    await feed(target, " latest")
    await until(lambda: any(message_text(a) == "old latest" for a in attempts))
    await target.process_event(RuntimeEvent.done())
    await target.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "中文😀_ * & < > " * 900,
        "```python\n" + "  print('<&😀>')\n" * 800 + "```",
        "# Title\n\n| A | B |\n|---|---|\n| 一 | 二 |\n\n" + "x" * 10000,
        "[长链接](https://example.com/" + "x" * 6000 + ")",
        "<!--" + "😀" * 4500 + "-->",
        pytest.param(("**" + "&" * 1500 + "**\n\n") * 5, id="escaped-paragraphs"),
    ],
)
async def test_formatting_wire_budget_and_complete_tail(source):
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await feed(target, source)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    for call in bot.messages:
        assert len(call["text"].encode("utf-16-le")) // 2 <= RICH_MAX_LEN
    assert "".join(call["text"] for call in bot.messages) == source


@pytest.mark.asyncio
async def test_final_cleanup_never_touches_foreign_messages_or_transcription():
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10, prefix="🎙 synthetic spoken question")
    await feed(target, "Long answer " * 1000)
    await target._flush()
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert {c["message_id"] for c in bot.deleted} == {10}
    assert any(
        "🎙 synthetic spoken question" in c["text"]
        for c in bot.messages
        if c.get("disable_notification") is False
    )
    assert 10 not in target.delivery.message_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["<!-- synthetic literal answer -->", "---"])
async def test_transcription_does_not_mask_an_empty_markdown_body(body):
    bot = Bot()
    prefix = "🎙 question <with> & characters"
    target = TelegramStreamer(bot, 42, prefix=prefix)
    await feed(target, body)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    assert prefix in bot.messages[0]["text"]
    assert body in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_failed_cleanup_does_not_resend_final():
    bot = Bot()

    async def fail(**kwargs):
        raise bad_request("cannot delete")

    bot.delete_message = fail
    target = TelegramStreamer(bot, 42, 10, user_id=42, request_id="r")
    await feed(target, "answer")
    await target._flush()
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    await target.finalize()
    assert [call["text"] for call in _finals(bot)] == ["answer"]
    assert target.delivery.status == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["cancelled", "timeout", "disconnected", "error", "access_denied"]
)
async def test_terminal_failure_or_cancellation_keeps_partial_body(reason):
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await feed(target, "synthetic partial")
    await target.process_event(RuntimeEvent.done(reason))
    await target.finalize()
    assert target.state == ("cancelled" if reason == "cancelled" else "failed")
    assert "Partial result:" in bot.messages[0]["text"]
    assert "synthetic partial" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_no_done_is_not_success_and_unlabelled_commentary_is_preserved():
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await feed(target, "I will check. ")
    await target.process_event(
        RuntimeEvent(type=EventType.TOOL_CALL, data={"tool_call_id": "t"})
    )
    await feed(target, "Completed is just a word, not a final boundary.")
    await target.finalize()
    assert target.state == "failed"
    assert "I will check. Completed is just a word" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_approval_waiting_is_bound_to_active_request_and_never_approves(
    monkeypatch,
):
    from condor.runtime import turns
    from condor.runtime.confirmations import ConfirmationRegistry, ConfirmationStatus
    from condor.telegram.approvals import TelegramChannel
    from condor.telegram.output import runs as output_runs

    monkeypatch.setattr(output_runs, "_runs", {})
    monkeypatch.setattr(output_runs, "_by_draft", {})
    monkeypatch.setattr(turns, "_turns", {})
    bot = Bot()
    active = TelegramStreamer(
        bot, 42, 10, user_id=42, conversation_id="c", request_id="r"
    )
    queued = TelegramStreamer(
        bot, 42, user_id=42, conversation_id="c", request_id="next"
    )
    await active.process_event(RuntimeEvent(type=EventType.HEARTBEAT))
    await queued.process_event(RuntimeEvent.queued())
    state = turns.state(42, "c")
    state.active_key, state.active_request = "tg:42", "r"
    registry = ConfirmationRegistry()
    pending = registry.register("tg:42", 42, "Synthetic action", {}, [])
    await TelegramChannel(bot, 42).deliver(pending)
    await active._flush()
    await queued._flush()
    assert active.state == "waiting_approval" and queued.state == "queued"
    assert pending.status == ConfirmationStatus.PENDING
    assert bot.messages[0]["disable_notification"] is False
    assert any(call["text"] == "Waiting for your approval." for call in bot.drafts)
    assert not await registry.resolve(pending.id, approved=True, by_user_id=43)
    assert pending.status == ConfirmationStatus.PENDING
    await registry.resolve(pending.id, approved=False, by_user_id=42)
    await active._flush()
    assert active.state == "running"
    assert pending.status == ConfirmationStatus.DENIED
    await active.stop_edit_loop()
    await queued.stop_edit_loop()


@pytest.mark.asyncio
async def test_adapter_diagnostic_text_is_not_treated_as_assistant_prose():
    from condor.acp.client import TextChunk

    diagnostic = RuntimeEvent.from_acp(
        TextChunk("Authorization: Bearer SYNTHETIC_SECRET", is_error=True)
    )
    assert diagnostic.type == EventType.TEXT and diagnostic.field("is_error") is True
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await target.process_event(diagnostic)
    await target._flush()
    await target.process_event(RuntimeEvent.done("error"))
    await target.finalize()
    assert all("SYNTHETIC_SECRET" not in c["text"] for c in bot.messages + bot.drafts)
    assert target.state == "failed"


@pytest.mark.asyncio
async def test_wire_format_preserves_native_draft_flags(tmp_path):
    import json

    from aiogram.types import InputRichMessage

    app = offline_app(tmp_path)
    bot = app.bot
    await bot.send_rich_message_draft(
        chat_id=42,
        draft_id=7,
        rich_message=InputRichMessage(markdown="Synthetic"),
        can_stop=True,
        keep_on_stop=True,
    )
    await bot.send_rich_message(
        chat_id=42,
        rich_message=InputRichMessage(markdown="Final"),
        disable_notification=False,
    )
    await app.close()
    methods = [name for name, _ in bot.session.requests]
    assert methods[0] == "sendRichMessageDraft"
    assert methods[1] == "sendRichMessage"
    draft_fields = bot.session.requests[0][1]
    assert draft_fields["draft_id"] == "7"
    assert draft_fields["can_stop"] == "true"
    assert draft_fields["keep_on_stop"] == "true"
    payload = json.loads(draft_fields["rich_message"])
    assert payload["markdown"] == "Synthetic"


@pytest.mark.asyncio
async def test_terminal_ack_gates_cleanup_and_duplicate_done_cannot_change_body():
    bot = Bot()
    entered, release = asyncio.Event(), asyncio.Event()
    original = bot.send_rich_message

    async def send(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "first")
    await target.process_event(RuntimeEvent.done())
    finalizing = asyncio.create_task(target.finalize())
    await entered.wait()
    await feed(target, "late")
    await target.process_event(RuntimeEvent.done("error"))
    release.set()
    await finalizing
    assert bot.messages[-1]["text"] == "first" and target.delivery.status == "complete"
    assert target.state == "completed"


@pytest.mark.asyncio
async def test_voice_transcription_keeps_the_spoken_prefix():
    spoken = "🎙 **what is** <price> & [this](https://example.com)?"
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10, prefix=spoken)
    await feed(target, "**answer**")
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    text = bot.messages[-1]["text"]
    assert text.startswith(spoken)
    assert "**answer**" in text


@pytest.mark.asyncio
async def test_definite_rejection_after_first_page_is_partial_not_success():
    bot = Bot()
    original = bot.send_rich_message

    async def send(**kwargs):
        if bot.messages:
            raise bad_request("synthetic rejection")
        return await original(**kwargs)

    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, OVERFLOW)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "partial" and result.confirmed == 1


@pytest.mark.asyncio
async def test_connect_retry_followed_by_parse_failure_still_downgrades():
    bot = Bot()
    original = bot.send_message
    attempts = []

    async def send_rich(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise network_failure("connect", cause=connect_error())
        raise bad_request("cannot parse entities")

    bot.send_rich_message = send_rich
    bot.send_message = original
    target = TelegramStreamer(bot, 42)
    await feed(target, "**answer**")
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete" and len(attempts) == 2
    assert bot.messages[-1]["text"] == "**answer**"
    assert bot.messages[-1].get("rich_message") is None


@pytest.mark.asyncio
async def test_draft_rate_limit_has_a_finite_budget():
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "hello")
    attempts = []

    async def draft(**kwargs):
        attempts.append(kwargs)
        raise rate_limited(0.01)

    bot.send_rich_message_draft = draft
    for _ in range(5):
        await target._flush()
    assert len(attempts) == 3 and target._progress_disabled
    await target.stop_edit_loop()
