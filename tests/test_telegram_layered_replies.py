"""Synthetic events only: no Telegram, model, exchange or account connections."""

import asyncio
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest

from condor.runtime.events import EventType, RuntimeEvent
from handlers.agents.stream import TelegramStreamer


class Bot:
    def __init__(self):
        self.messages = []
        self.edits = []
        self.deleted = []
        self.actions = []
        self.edit_failure = None
        self.send_failure = None

    async def send_message(self, **kwargs):
        if self.send_failure:
            raise self.send_failure
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.messages))

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)
        if self.edit_failure:
            raise self.edit_failure
        return True

    async def delete_message(self, **kwargs):
        self.deleted.append(kwargs)
        return True

    async def send_chat_action(self, **kwargs):
        self.actions.append(kwargs)
        return True

    async def edit_message_reply_markup(self, **kwargs):
        return True


async def feed(target, text):
    await target.process_event(RuntimeEvent(type=EventType.TEXT, data={"text": text}))


@pytest.mark.asyncio
async def test_default_accumulates_text_and_finalizes_once():
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "Synthetic ")
    await target._flush(final=False)
    await feed(target, "answer")
    await target._flush(final=False)
    assert all("Synthetic" not in call["text"] for call in bot.edits + bot.messages)
    await target.process_event(RuntimeEvent.done())
    await asyncio.gather(target.finalize(), target.finalize())
    finals = [
        call for call in bot.messages if call.get("disable_notification") is False
    ]
    assert [call["text"] for call in finals] == ["Synthetic answer"]
    assert bot.deleted


@pytest.mark.asyncio
async def test_deleted_placeholder_does_not_prevent_final_delivery():
    bot = Bot()
    bot.edit_failure = BadRequest("Message to edit not found")
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
    assert all("SECRET" not in call["text"] for call in bot.messages + bot.edits)


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
        "PRIVATE_MARKER" not in call["text"] for call in bot.messages + bot.edits
    )
    assert target.tools["t"] == "in_progress"


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_running_typing_and_queue_do_not_animate(monkeypatch):
    from handlers.agents import stream

    monkeypatch.setattr(stream, "TYPING_INTERVAL", 0.02)
    bot = Bot()
    target = TelegramStreamer(bot, 42, request_id="r", user_id=42, conversation_id="c")
    target.start_edit_loop()
    await target.process_event(RuntimeEvent.queued())
    await until(lambda: bot.messages)
    await asyncio.sleep(0.04)
    assert not bot.actions and not bot.edits
    assert len(bot.messages) == 1
    assert bot.messages[0]["disable_notification"] is True
    await target.process_event(RuntimeEvent(type=EventType.HEARTBEAT))
    await until(lambda: bot.actions and bot.edits)
    count = len(bot.edits)
    await asyncio.sleep(0.05)
    assert len(bot.edits) == count
    assert len(bot.actions) > 1
    await feed(target, "Result")
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert target._edit_task.done() and target._typing_task.done()


@pytest.mark.asyncio
async def test_edit_coalesces_without_accumulating_old_frames():
    bot = Bot()
    target = TelegramStreamer(bot, 42, mode="edit", edit_interval=0.03)
    target.start_edit_loop()
    await feed(target, "Start")
    await until(lambda: bot.messages)
    for piece in [" ", "one", " two", " three"]:
        await feed(target, piece)
    await until(lambda: bot.edits)
    assert [call["text"] for call in bot.edits] == ["Start one two three"]
    assert all(call["disable_notification"] for call in bot.messages)
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert bot.messages[-1]["text"] == "Start one two three"
    assert bot.messages[-1]["disable_notification"] is False


@pytest.mark.asyncio
async def test_missing_progress_target_replaced_once_and_cached():
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10)
    bot.edit_failure = BadRequest("Message to edit not found")
    await feed(target, "answer")
    await target._flush()
    await target._flush()
    assert len(bot.edits) == 1 and len(bot.messages) == 1
    assert target._message_id == 101
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert target.delivery.status == "complete"


@pytest.mark.asyncio
async def test_ambiguous_final_has_no_automatic_send_retry_and_keeps_progress(caplog):
    from telegram.error import TimedOut

    bot = Bot()
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "answer")
    await target._flush()
    attempts = []

    async def timeout(**kwargs):
        attempts.append(kwargs)
        raise TimedOut("https://api.telegram.org/botSECRET/sendMessage")

    bot.send_message = timeout
    await target.process_event(RuntimeEvent.done())
    first, second = await asyncio.gather(target.finalize(), target.finalize())
    assert first is second and first.status == "delivery_unknown"
    assert len(attempts) == 1
    assert not bot.deleted
    assert "could not be confirmed" in bot.edits[-1]["text"]
    assert "SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_partial_delivery_keeps_confirmed_cursor_and_only_first_notifies():
    from telegram.error import TimedOut

    bot = Bot()
    original = bot.send_message
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 2:
            raise TimedOut()
        return await original(**kwargs)

    bot.send_message = send
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "x" * 9000)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    await target.finalize()
    assert result.status == "delivery_unknown" and result.confirmed == 1
    assert result.message_ids == [101]
    assert len(attempts) == 2 and not bot.deleted
    assert [call["disable_notification"] for call in attempts] == [False, True]


@pytest.mark.asyncio
async def test_definite_connect_failure_retries_only_unconfirmed_page():
    import httpx

    bot = Bot()
    original = bot.send_message
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 2:
            raise httpx.ConnectTimeout("synthetic")
        return await original(**kwargs)

    bot.send_message = send
    target = TelegramStreamer(bot, 42)
    await feed(target, "x" * 4100 + "tail")
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete" and result.confirmed == 2
    assert len(attempts) == 3 and attempts[1] == attempts[2]
    assert "".join(call["text"] for call in bot.messages).endswith("tail")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type,retry",
    [
        ("ConnectError", True),
        ("ConnectTimeout", True),
        ("ReadError", False),
        ("ReadTimeout", False),
        ("WriteError", False),
        ("WriteTimeout", False),
    ],
)
async def test_real_ptb_retries_only_preconnection_failures(error_type, retry):
    import httpx
    from telegram import Bot as TelegramBot
    from telegram.request import HTTPXRequest

    attempts = []

    async def transport(request):
        assert request.url.path.endswith("/sendMessage")
        attempts.append(request.content)
        if len(attempts) == 2:
            raise getattr(httpx, error_type)("synthetic failure", request=request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 100 + len(attempts),
                    "date": 1,
                    "chat": {"id": 42, "type": "private"},
                    "text": "answer",
                },
            },
        )

    request = HTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(transport)})
    bot = TelegramBot("123:offline-test", request=request)
    target = TelegramStreamer(bot, 42)
    try:
        await feed(target, "x" * 4100 + "tail")
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
        await request.shutdown()


@pytest.mark.asyncio
async def test_none_ack_is_not_success():
    bot = Bot()

    async def send(**kwargs):
        return None

    bot.send_message = send
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "answer")
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "delivery_unknown" and not result.message_ids
    assert not bot.deleted


@pytest.mark.asyncio
async def test_parse_failure_falls_back_without_losing_code_or_links():
    bot = Bot()
    original = bot.send_message

    async def send(**kwargs):
        if kwargs.get("parse_mode"):
            raise BadRequest("Can't parse entities")
        return await original(**kwargs)

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
    assert "&lt;" not in text and all(
        call["parse_mode"] is None for call in bot.messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [1, __import__("datetime").timedelta(seconds=1)])
async def test_retry_after_waits_and_never_replays_confirmed_page(monkeypatch, delay):
    from telegram.error import RetryAfter

    bot = Bot()
    original = bot.send_message
    attempts, sleeps = [], []

    async def send(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 2:
            raise RetryAfter(delay)
        return await original(**kwargs)

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("condor.telegram_text.asyncio.sleep", sleep)
    bot.send_message = send
    target = TelegramStreamer(bot, 42)
    await feed(target, "x" * 4100)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete" and sleeps == [1]
    assert len(attempts) == 3 and attempts[1] == attempts[2]
    assert [call["disable_notification"] for call in bot.messages] == [False, True]


@pytest.mark.asyncio
async def test_rate_limited_preview_uses_latest_snapshot():
    from telegram.error import RetryAfter

    bot = Bot()
    original = bot.edit_message_text
    attempts = []

    async def edit(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise RetryAfter(0.05)
        return await original(**kwargs)

    bot.edit_message_text = edit
    target = TelegramStreamer(bot, 42, 10, mode="edit", edit_interval=0.01)
    target.start_edit_loop()
    await feed(target, "old")
    await until(lambda: attempts)
    await feed(target, " latest")
    await until(lambda: len(attempts) == 2)
    assert attempts[1]["text"] == "old latest"
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
    from condor.telegram_text import plain_text, render_markdown
    from tests.test_telegram_text import BalancedHTML

    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await feed(target, source)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    for call in bot.messages:
        assert len(call["text"].encode("utf-16-le")) // 2 <= 4096
        if call["parse_mode"] == "HTML":
            BalancedHTML(call["text"])
    actual = "".join(
        plain_text(c["text"]) if c["parse_mode"] else c["text"] for c in bot.messages
    )
    expected = "".join(plain_text(p.html) for p in render_markdown(source)) or source
    assert actual == expected


@pytest.mark.asyncio
async def test_final_cleanup_never_touches_foreign_messages_or_transcription():
    bot = Bot()
    target = TelegramStreamer(
        bot, 42, 10, prefix="🎙 synthetic spoken question", mode="edit"
    )
    await feed(target, "Long answer " * 1000)
    await target._flush()
    owned = {target._message_id, *target._continuation_ids}
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert {c["message_id"] for c in bot.deleted} == owned
    assert any(
        "🎙 synthetic spoken question" in c["text"]
        for c in bot.messages
        if c.get("disable_notification") is False
    )
    assert set(target.delivery.message_ids).isdisjoint(owned)


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
    assert bot.messages[0]["parse_mode"] is None
    assert bot.messages[0]["text"] == prefix + "\n\n" + body


@pytest.mark.asyncio
async def test_failed_cleanup_does_not_resend_final_and_removes_stop():
    bot = Bot()

    async def fail(**kwargs):
        raise BadRequest("cannot delete")

    bot.delete_message = fail
    target = TelegramStreamer(bot, 42, 10, user_id=42, request_id="r")
    await feed(target, "answer")
    await target._flush()
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    await target.finalize()
    assert len(bot.messages) == 1
    assert bot.edits[-1]["reply_markup"] is None
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


@pytest.mark.parametrize(
    "env",
    [
        {"TELEGRAM_AGENT_REPLY_MODE": "draft"},
        {"TELEGRAM_AGENT_EDIT_INTERVAL": "nan"},
        {"TELEGRAM_AGENT_EDIT_INTERVAL": "inf"},
        {"TELEGRAM_AGENT_EDIT_INTERVAL": "0"},
        {"TELEGRAM_AGENT_EDIT_INTERVAL": "-1"},
        {"TELEGRAM_AGENT_EDIT_INTERVAL": "bad"},
    ],
)
def test_invalid_reply_config_is_rejected(env):
    from utils.config import ConfigError, telegram_agent_reply_settings

    with pytest.raises(ConfigError):
        telegram_agent_reply_settings(env)


def test_default_and_edit_config():
    from utils.config import telegram_agent_reply_settings

    assert telegram_agent_reply_settings({}) == ("final", 1.0)
    assert telegram_agent_reply_settings(
        {"TELEGRAM_AGENT_REPLY_MODE": "edit", "TELEGRAM_AGENT_EDIT_INTERVAL": "2"}
    ) == ("edit", 2.0)


@pytest.mark.asyncio
async def test_approval_waiting_is_bound_to_active_request_and_never_approves(
    monkeypatch,
):
    from condor import telegram_output
    from condor.runtime import turns
    from condor.runtime.channels import TelegramChannel
    from condor.runtime.confirmations import ConfirmationRegistry, ConfirmationStatus

    monkeypatch.setattr(telegram_output, "_runs", {})
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
    target = TelegramStreamer(bot, 42, mode="edit")
    await target.process_event(diagnostic)
    await target._flush()
    await target.process_event(RuntimeEvent.done("error"))
    await target.finalize()
    assert all("SYNTHETIC_SECRET" not in c["text"] for c in bot.messages + bot.edits)
    assert target.state == "failed"


@pytest.mark.asyncio
async def test_http_fallback_preserves_notifications_markup_and_typing(monkeypatch):
    from condor.routine_store import _HttpBot
    from handlers.agents.menu import stop_generating_keyboard

    bot = _HttpBot()
    calls = []

    async def post(method, data):
        calls.append((method, data))
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(bot, "_post", post)
    await bot.send_message(
        chat_id=42,
        text="Synthetic",
        disable_notification=True,
        reply_markup=stop_generating_keyboard("test"),
    )
    await bot.send_chat_action(chat_id=42, action="typing")
    await bot.edit_message_reply_markup(chat_id=42, message_id=1, reply_markup=None)
    assert calls[0][1]["disable_notification"] is True
    assert (
        calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        == "agent:cancel:test"
    )
    assert calls[1] == ("sendChatAction", {"chat_id": 42, "action": "typing"})
    assert calls[2][1]["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_terminal_ack_gates_cleanup_and_duplicate_done_cannot_change_body():
    bot = Bot()
    entered, release = asyncio.Event(), asyncio.Event()
    original = bot.send_message

    async def send(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    bot.send_message = send
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "first")
    await target.process_event(RuntimeEvent.done())
    finalizing = asyncio.create_task(target.finalize())
    await entered.wait()
    await feed(target, "late")
    await target.process_event(RuntimeEvent.done("error"))
    assert not bot.deleted
    release.set()
    await finalizing
    assert bot.messages[-1]["text"] == "first" and target.delivery.status == "complete"
    assert target.state == "completed"


@pytest.mark.asyncio
async def test_voice_transcription_is_literal_even_when_it_contains_markup():
    from condor.telegram_text import plain_text

    spoken = "🎙 **what is** <price> & [this](https://example.com)?"
    bot = Bot()
    target = TelegramStreamer(bot, 42, 10, prefix=spoken)
    await feed(target, "**answer**")
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    assert plain_text(bot.messages[-1]["text"]).startswith(spoken)
    assert "<b>answer</b>" in bot.messages[-1]["text"]


@pytest.mark.asyncio
async def test_definite_rejection_after_first_page_is_partial_not_success():
    bot = Bot()
    original = bot.send_message

    async def send(**kwargs):
        if bot.messages:
            raise BadRequest("synthetic rejection")
        return await original(**kwargs)

    bot.send_message = send
    target = TelegramStreamer(bot, 42, 10)
    await feed(target, "x" * 4100)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "partial" and result.confirmed == 1
    assert not bot.deleted


@pytest.mark.asyncio
async def test_rendering_failure_falls_back_to_complete_original_text(monkeypatch):
    def fail(body):
        raise ValueError("synthetic renderer error")

    monkeypatch.setattr("handlers.agents.stream.render_markdown", fail)
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    source = "中文😀**literal**" * 700
    await feed(target, source)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    assert "".join(c["text"] for c in bot.messages) == source
    assert all(c["parse_mode"] is None for c in bot.messages)


@pytest.mark.asyncio
async def test_connect_retry_followed_by_parse_failure_still_downgrades():
    import httpx

    bot = Bot()
    original = bot.send_message
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise httpx.ConnectTimeout("synthetic")
        if kwargs.get("parse_mode"):
            raise BadRequest("cannot parse entities")
        return await original(**kwargs)

    bot.send_message = send
    target = TelegramStreamer(bot, 42)
    await feed(target, "**answer**")
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete" and len(attempts) == 3
    assert [c["text"] for c in bot.messages] == ["answer"]


@pytest.mark.asyncio
async def test_unchanged_first_page_does_not_reset_rate_limit_budget():
    from telegram.error import RetryAfter

    bot = Bot()
    target = TelegramStreamer(bot, 42, 10, mode="edit")
    await feed(target, "x" * 4100)
    await target._flush()
    tail = target._continuation_ids[-1]
    original = bot.edit_message_text
    attempts = []

    async def edit(**kwargs):
        if kwargs["message_id"] == tail:
            attempts.append(kwargs)
            raise RetryAfter(0.01)
        return await original(**kwargs)

    bot.edit_message_text = edit
    await feed(target, "tail")
    for _ in range(5):
        await target._flush()
    assert len(attempts) == 3 and target._progress_disabled
    await target.stop_edit_loop()


@pytest.mark.asyncio
async def test_typing_rate_limit_has_a_finite_budget(monkeypatch):
    from telegram.error import RetryAfter

    monkeypatch.setattr("handlers.agents.stream.TYPING_INTERVAL", 0.001)
    bot = Bot()
    attempts = []

    async def typing(**kwargs):
        attempts.append(kwargs)
        raise RetryAfter(0.001)

    bot.send_chat_action = typing
    target = TelegramStreamer(bot, 42)
    await feed(target, "answer")
    await asyncio.wait_for(target._typing_loop(), 1)
    assert len(attempts) == 3
    await target.stop_edit_loop()
