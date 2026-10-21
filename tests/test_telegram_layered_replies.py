"""Synthetic events only: no Telegram, model, exchange or account connections."""

import asyncio

import aiohttp
import pytest

from condor.runtime.events import EventType, RuntimeEvent
from condor.telegram.errors import bad_request, network_failure, rate_limited
from condor.telegram.output.presentation import BODY_MAX_LEN
from handlers.agents.stream import TelegramStreamer
from tests.support.telegram import Bot, feed, message_text
from tests.support.telegram_offline import offline_app

OVERFLOW = "x" * (BODY_MAX_LEN - 2) + "\n\ntail"


def connect_error(message="synthetic"):
    """What aiohttp raises when the socket never opened."""
    key = aiohttp.client_reqrep.ConnectionKey(
        "api.telegram.org", 443, True, True, None, None, None
    )
    return aiohttp.ClientConnectorError(key, OSError(message))


@pytest.mark.asyncio
async def test_late_events_and_error_done_preserve_terminal_state():
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await feed(target, "Partial")
    await target.process_event(RuntimeEvent.error("Authorization: Bearer SECRET"))
    await target.process_event(RuntimeEvent.done())
    await target.finalize()
    before = target.body.body
    await feed(target, "LATE")
    assert target.body.body == before
    assert target.state == "failed"
    assert all("SECRET" not in message_text(call) for call in bot.messages + bot.edits)


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
        "PRIVATE_MARKER" not in message_text(call) for call in bot.messages + bot.edits
    )
    assert target.tools["t"].status == "in_progress"


@pytest.mark.asyncio
async def test_current_tool_merges_late_targets_and_ignores_passive_updates():
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    target.set_phase("Starting the agent…")
    passive = [
        RuntimeEvent(type=EventType.HEARTBEAT),
        RuntimeEvent(type=EventType.THOUGHT, data={"text": "PRIVATE_THOUGHT"}),
    ]
    for event in passive:
        await target.process_event(event)
    assert target._status() == "Starting the agent…"
    await target.process_event(RuntimeEvent.accepted())
    await target.process_event(RuntimeEvent.queued())
    await target.process_event(passive[0])
    assert target._status() == "Waiting for the previous request…"
    await target.process_event(passive[1])
    assert target._status() == "Preparing response…"

    async def tool(event_type, call_id, **data):
        await target.process_event(
            RuntimeEvent(type=event_type, data={"tool_call_id": call_id, **data})
        )

    await tool(EventType.TOOL_CALL, "read", name="Read", kind="read", status="pending")
    assert target._status() == "Preparing to read…"
    await tool(
        EventType.TOOL_UPDATE,
        "read",
        status="in_progress",
        input={"path": "/private/input.py"},
    )
    assert target._status() == "Reading input.py"
    await tool(
        EventType.TOOL_CALL,
        "prices",
        name="get_prices",
        kind="mcp",
        status="in_progress",
        input={"trading_pairs": ["BTC-USDT"]},
    )
    await tool(EventType.TOOL_UPDATE, "read", output="PRIVATE_OUTPUT")
    assert target._status() == "Fetching BTC-USDT prices"

    # A real target change takes focus; empty metadata cannot erase that target.
    await tool(
        EventType.TOOL_UPDATE,
        "read",
        title="Read PRIVATE_DESCRIPTION",
        input={"path": "/private/output.py"},
    )
    await tool(EventType.TOOL_UPDATE, "read", title="", name="", kind="", input={})
    assert target._status() == "Reading output.py"
    assert target.tools["read"].kind == "read"
    assert target.tools["read"].name == "Read"
    await target._deliver(False)
    assert message_text(bot.messages[-1]) == "Reading output.py"

    await tool(EventType.TOOL_UPDATE, "read", status="completed")
    assert target._status() == "Fetching BTC-USDT prices"
    await tool(EventType.TOOL_UPDATE, "prices", status="blocked")
    assert target._status() == "Processing tool results…"
    for event in passive:
        await target.process_event(event)
    assert target._status() == "Processing tool results…"
    await feed(target, "Visible answer")
    assert target._status() == "Writing response…"
    await tool(EventType.TOOL_UPDATE, "read", input={"path": "/private/late.py"})
    assert target._status() == "Writing response…"
    for event in passive:
        await target.process_event(event)
    await target._deliver(False)
    assert message_text(bot.edits[-1]) == "Visible answer\n\nWriting response…"
    assert target.body.body == "Visible answer"
    assert all("PRIVATE" not in message_text(call) for call in bot.messages + bot.edits)
    await target.stop_edit_loop()


@pytest.mark.asyncio
async def test_blocked_tool_is_not_reactivated_by_a_late_start():
    target = TelegramStreamer(Bot(), 42)
    for status in ("blocked", "in_progress"):
        await target.process_event(
            RuntimeEvent(
                type=EventType.TOOL_CALL,
                data={"tool_call_id": "t", "name": "get_prices", "status": status},
            )
        )
    assert target._status() == "Processing tool results…"
    assert target.tools["t"].status == "blocked"
    await target.stop_edit_loop()


@pytest.mark.asyncio
async def test_ambiguous_final_has_no_automatic_send_retry(caplog):
    bot = Bot()
    target = TelegramStreamer(bot, 42)
    await feed(target, "answer")
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
async def test_partial_delivery_keeps_confirmed_cursor_and_does_not_replay():
    bot = Bot()
    original = bot.send_rich_message
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 2:
            raise network_failure("Request timeout error")
        return await original(**kwargs)

    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42)
    await feed(target, OVERFLOW)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    await target.finalize()
    assert result.status == "delivery_unknown" and result.confirmed == 1
    assert result.message_ids == [101]
    assert len(attempts) == 2
    assert all(call["disable_notification"] for call in attempts)


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
    target = TelegramStreamer(bot, 42)
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
    text = "".join(message_text(call) for call in bot.messages)
    assert "https://example.com" in text and "x < y & z" in text
    assert all(call.get("rich_message") is None for call in bot.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [0.01, 0.02])
async def test_retry_after_waits_and_never_replays_confirmed_page(monkeypatch, delay):
    bot = Bot()
    original = bot.send_rich_message
    attempts, times = [], []

    async def send(**kwargs):
        attempts.append(kwargs)
        times.append(asyncio.get_running_loop().time())
        if len(attempts) == 2:
            raise rate_limited(delay)
        return await original(**kwargs)

    monkeypatch.setattr("condor.telegram.output.writer.retry_delay", lambda exc: delay)
    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42)
    await feed(target, OVERFLOW)
    await target.process_event(RuntimeEvent.done())
    result = await target.finalize()
    assert result.status == "complete"
    assert times[2] - times[1] >= delay
    assert len(attempts) == 3 and attempts[1] == attempts[2]
    assert all(call["disable_notification"] for call in bot.messages)


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
        assert len(message_text(call).encode("utf-16-le")) // 2 <= BODY_MAX_LEN
    assert "".join(text for text, _ in result.payloads) == source
    assert len(bot.messages) == len(result.payloads) == result.confirmed


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
    monkeypatch.setattr(turns, "_turns", {})
    bot = Bot()
    active = TelegramStreamer(bot, 42, user_id=42, conversation_id="c", request_id="r")
    queued = TelegramStreamer(
        bot, 42, user_id=42, conversation_id="c", request_id="next"
    )
    await active.process_event(RuntimeEvent.accepted())
    await active.process_event(RuntimeEvent(type=EventType.HEARTBEAT))
    await queued.process_event(RuntimeEvent.accepted())
    await queued.process_event(RuntimeEvent.queued())
    state = turns.state(42, "c")
    state.active_key, state.active_request = "tg:42", "r"
    state.pending["r"] = "tg:42"
    registry = ConfirmationRegistry()
    pending = registry.register("tg:42", 42, "Synthetic action", {}, [])
    await TelegramChannel(bot, 42).deliver(pending)
    await active._deliver(False)
    await queued._deliver(False)
    assert active.state == "waiting_approval" and queued.state == "queued"
    assert pending.status == ConfirmationStatus.PENDING
    assert bot.messages[0]["disable_notification"] is False
    assert any(
        "confirmation" in message_text(call).lower()
        for call in bot.messages + bot.edits
    )
    assert not await registry.resolve(pending.id, approved=True, by_user_id=43)
    assert pending.status == ConfirmationStatus.PENDING
    await registry.resolve(pending.id, approved=False, by_user_id=42)
    await active._deliver(False)
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
    await target._deliver(False)
    await target.process_event(RuntimeEvent.done("error"))
    await target.finalize()
    assert all("SYNTHETIC_SECRET" not in c["text"] for c in bot.messages + bot.edits)
    assert target.state == "failed"


@pytest.mark.asyncio
async def test_terminal_ack_and_duplicate_done_cannot_change_body():
    bot = Bot()
    entered, release = asyncio.Event(), asyncio.Event()
    original = bot.send_rich_message

    async def send(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    bot.send_rich_message = send
    target = TelegramStreamer(bot, 42)
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
    target = TelegramStreamer(bot, 42, prefix=spoken)
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
    target = TelegramStreamer(bot, 42)
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
