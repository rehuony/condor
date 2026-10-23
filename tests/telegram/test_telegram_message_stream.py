"""The receipt-to-final lifecycle, including slow delivery races."""

import asyncio
import logging

import pytest

from condor.runtime.events import EventType, RuntimeEvent
from condor.telegram.output.renderer import RichReplyStreamer
from tests.support.telegram import Bot, feed, message_text


async def wait_until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_receipt_body_tool_and_final_edit_the_same_message(caplog):
    caplog.set_level(logging.INFO, logger="condor.telegram.output")
    bot = Bot()
    stream = RichReplyStreamer(bot, 10, user_id=1, request_id="r1", input_message_id=50)
    stream.begin_preparation("Received. Preparing your request…", lambda: None)
    await wait_until(lambda: bot.messages)
    assert bot.messages[0]["text"]
    assert bot.messages[0]["reply_parameters"].message_id == 50
    assert (
        bot.messages[0]["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.endswith(stream.target.token)
    )
    await stream.process_event(RuntimeEvent.accepted())
    await feed(stream, "**Answer**\n\n")
    await stream.process_event(
        RuntimeEvent(
            type=EventType.TOOL_CALL,
            data={
                "kind": "read",
                "title": "Read secret /tmp/key",
                "tool_call_id": "t",
                "status": "in_progress",
            },
        )
    )
    await wait_until(lambda: bot.edits)
    assert bot.edits[-1]["text"] == "**Answer**\n\n\n\nReading…"
    await stream.process_event(RuntimeEvent.done())
    result = await stream.finalize()
    assert result.status == "complete"
    assert len(bot.messages) == 1
    assert bot.edits[-1]["message_id"] == 101
    assert bot.edits[-1]["text"] == "Answer"
    assert bot.edits[-1]["rich_message"].html == "<b>Answer</b>"
    assert bot.edits[-1]["reply_markup"] is None
    assert not bot.deleted
    timings = [
        r.message for r in caplog.records if "Telegram reply timing" in r.message
    ]
    stages = [line.split("stage=")[1].split()[0] for line in timings]
    assert len(stages) == len(set(stages))
    assert stages.index("first_send_attempt") < stages.index("first_send_ack")
    assert stages.index("first_text") < stages.index("first_body_ack")
    assert stages.index("execution_done") < stages.index("final_ack")
    assert stages.index("final_ack") < stages.index("delivery_finished")
    assert all("request_id=r1" in line for line in timings)
    assert "Answer" not in caplog.text and "/tmp/key" not in caplog.text


@pytest.mark.asyncio
async def test_final_waits_for_inflight_edit_and_never_overwrites_with_old_body():
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowBot(Bot):
        async def edit_message_text(self, **kwargs):
            if not kwargs.get("rich_message"):
                entered.set()
                await release.wait()
            return await super().edit_message_text(**kwargs)

    bot = SlowBot()
    stream = RichReplyStreamer(bot, 11, request_id="r2")
    stream.begin_preparation("Preparing…", lambda: None)
    await wait_until(lambda: bot.messages)
    await feed(stream, "First")
    await entered.wait()
    await feed(stream, " and last")
    await stream.process_event(RuntimeEvent.done())
    final = asyncio.create_task(stream.finalize())
    await asyncio.sleep(0)
    assert not final.done()
    release.set()
    assert (await final).status == "complete"
    assert [message_text(edit) for edit in bot.edits] == [
        "First\n\nWriting response…",
        "First and last",
    ]


@pytest.mark.asyncio
async def test_queued_request_gets_its_receipt_while_first_is_still_running():
    bot = Bot()
    first = RichReplyStreamer(bot, 12, request_id="first")
    second = RichReplyStreamer(bot, 12, request_id="second")
    first.begin_preparation("Starting…", lambda: None)
    await wait_until(lambda: len(bot.messages) == 1)
    second.begin_preparation("Waiting for preparation…", lambda: None)
    await wait_until(lambda: len(bot.messages) == 2)
    assert bot.messages[1]["text"] == "Waiting for preparation…"
    assert not first._done
    await asyncio.gather(first.stop_edit_loop(), second.stop_edit_loop())


@pytest.mark.asyncio
async def test_stopping_freezes_display_but_settles_partial_body():
    bot = Bot()
    stream = RichReplyStreamer(bot, 13, request_id="stop")
    await feed(stream, "Partial")
    stream.target.stopped = True
    stream.target.on_stop()
    await feed(stream, " late output")
    await stream.process_event(RuntimeEvent.done("cancelled"))
    assert (await stream.finalize()).status == "complete"
    assert stream.body.body.startswith("Partial\n\n")
    assert "late output" not in stream.body.body
    assert bot.messages[0]["reply_markup"] is None


@pytest.mark.asyncio
async def test_transcription_stays_literal_during_final_formatting():
    bot = Bot()
    stream = RichReplyStreamer(bot, 14, prefix="🎙 **literal words**")
    await feed(stream, "Answer")
    await stream.process_event(RuntimeEvent.done())
    assert (await stream.finalize()).status == "complete"
    assert bot.messages[0]["text"] == "🎙 **literal words**\n\nAnswer"
    assert bot.messages[0].get("rich_message") is None


@pytest.mark.asyncio
async def test_sdk_encodes_rich_final_as_edit_of_plain_receipt(tmp_path):
    import json

    from bs4 import BeautifulSoup

    from tests.support.telegram_offline import offline_app

    app = offline_app(tmp_path)
    stream = RichReplyStreamer(app.bot, 15, request_id="sdk", input_message_id=50)
    literal = "  **literal** <value> & 中文😀\n" * 13
    source = "## Complete\n\n- First\n- Second\n\n```text\n" + literal + "```"
    try:
        stream.begin_preparation("Preparing…", lambda: None)
        await wait_until(lambda: stream.target.message_ids)
        receipt = next(iter(stream.target.message_ids))
        await feed(stream, source)
        await stream.process_event(RuntimeEvent.done())
        result = await stream.finalize()
        assert result.status == "complete"
        assert result.payloads == [(source, "rich")]
        requests = app.bot.session.requests
        assert requests[0][0] == "sendMessage"
        assert json.loads(requests[0][1]["reply_parameters"])["message_id"] == 50
        assert all(method == "editMessageText" for method, _ in requests[1:])
        final = requests[-1][1]
        assert final["message_id"] == str(receipt)
        rich = json.loads(final["rich_message"])
        assert set(rich) == {"html"}
        soup = BeautifulSoup(rich["html"], "html.parser")
        assert soup.b.get_text() == "Complete"
        assert soup.find("br") is not None
        assert soup.details.pre.get_text() == literal
        assert not soup.details.has_attr("open")
        assert "text" not in final
        assert "reply_markup" not in final
    finally:
        await stream.stop_edit_loop()
        await app.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,attempts,expected,replacement",
    [
        ("unchanged", 1, "complete", False),
        ("deleted", 1, "complete", True),
        ("parse", 2, "complete", False),
        ("timeout_once", 2, "complete", False),
        ("timeout", 3, "delivery_unknown", False),
        ("missing_ack", 3, "delivery_unknown", False),
    ],
)
async def test_final_edit_recovery_keeps_the_known_message_identity(
    failure, attempts, expected, replacement
):
    from condor.telegram.errors import bad_request, network_failure

    bot = Bot()
    calls = []
    original = bot.edit_message_text

    async def edit(**kwargs):
        calls.append(kwargs)
        if failure == "missing_ack":
            return None
        if failure == "unchanged":
            raise bad_request("Message is not modified")
        if failure == "deleted":
            raise bad_request("Message to edit not found")
        if failure == "parse" and kwargs.get("rich_message"):
            raise bad_request("Can't parse entities")
        if failure == "timeout" or (failure == "timeout_once" and len(calls) == 1):
            raise network_failure("Request timeout")
        return await original(**kwargs)

    bot.edit_message_text = edit
    stream = RichReplyStreamer(bot, 16, message_id=99)
    await feed(stream, "**Answer**")
    await stream.process_event(RuntimeEvent.done())
    result = await stream.finalize()
    assert result.status == expected
    assert len(calls) == attempts
    assert all(call["message_id"] == 99 for call in calls)
    assert len(bot.messages) == int(replacement)
    assert not bot.deleted
    if failure == "parse":
        assert calls[-1]["text"] == "**Answer**"
        assert "rich_message" not in calls[-1]
        assert result.payloads == [("**Answer**", None)]
