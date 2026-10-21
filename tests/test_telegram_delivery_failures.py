"""Telegram delivery failures never own the lifetime of an accepted AI turn."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config_manager
from condor.runtime.events import EventType, RuntimeEvent
from condor.telegram.errors import network_failure
from handlers import agents
from handlers.agents import stream
from tests.conftest import patch_agents
from tests.support.telegram import message_text


def _output_calls(bot):
    return [
        call.kwargs
        for method in (bot.send_message, bot.send_rich_message, bot.edit_message_text)
        for call in method.await_args_list
    ]


def _bot(**overrides):
    methods = dict(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        send_rich_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        delete_message=AsyncMock(),
        edit_message_text=AsyncMock(return_value=True),
    )
    methods.update(overrides)
    return SimpleNamespace(**methods)


def _approved_private_chat(monkeypatch, *, mock_session=True):
    monkeypatch.setattr(
        config_manager,
        "get_config_manager",
        lambda: SimpleNamespace(
            get_user_role=lambda _: config_manager.UserRole.ADMIN,
        ),
    )
    if mock_session:
        patch_agents(
            monkeypatch,
            "get_session",
            AsyncMock(return_value=SimpleNamespace(alive=True)),
        )
    patch_agents(monkeypatch, "_handle_pasted_secrets", AsyncMock())
    update = SimpleNamespace(
        update_id=123,
        effective_chat=SimpleNamespace(id=4242, type="private"),
        effective_user=SimpleNamespace(id=4242),
        message=SimpleNamespace(
            message_id=17,
            chat=SimpleNamespace(id=4242),
            text="Continue the analysis",
            caption=None,
            photo=(),
            sticker=None,
            voice=None,
            audio=None,
            document=None,
            media_group_id=None,
            reply_to_message=None,
            external_reply=None,
            answer=AsyncMock(side_effect=AssertionError("premature delivery")),
        ),
    )
    return update


@pytest.mark.parametrize("failure_kind", ["connect", "ambiguous"])
def test_progress_failure_does_not_abort_or_replay_the_turn(
    monkeypatch, caplog, failure_kind
):
    import aiohttp

    update = _approved_private_chat(monkeypatch)
    requests, completed, sends, progress = [], [], [], []
    initial_delivery_failed = asyncio.Event()

    async def send_message(**kwargs):
        progress.append(kwargs)
        initial_delivery_failed.set()
        cause = aiohttp.ConnectionTimeoutError() if failure_kind == "connect" else None
        raise network_failure(
            "https://api.telegram.org/botSECRET/sendMessage", cause=cause
        )

    async def send_rich_message(**kwargs):
        sends.append(kwargs)
        return SimpleNamespace(message_id=99)

    async def prompt(key, request, **kwargs):
        requests.append(request.request_id)
        yield RuntimeEvent.accepted()
        yield RuntimeEvent(type=EventType.HEARTBEAT)
        await initial_delivery_failed.wait()
        yield RuntimeEvent(type=EventType.TEXT, data={"text": "**The answer**"})
        yield RuntimeEvent.done("end_turn")
        completed.append(True)

    monkeypatch.setattr(agents.runtime, "prompt", prompt)
    context = SimpleNamespace(
        user_data={},
        chat_data={},
        bot=_bot(send_message=send_message, send_rich_message=send_rich_message),
    )
    asyncio.run(agents.agent_message_handler(update, context))
    assert requests == ["telegram:4242:17"]
    assert completed == [True]
    assert not update.message.answer.called
    assert progress
    if failure_kind == "connect":
        assert len(sends) == 1
        assert message_text(sends[0]) == "The answer"
    else:
        assert len(progress) == 1 and not sends
        assert "status=delivery_unknown" in caplog.text
    assert "SECRET" not in caplog.text and "4242" not in caplog.text


def test_failed_progress_edit_does_not_prevent_in_place_final_delivery():
    from tests.support.telegram import Bot

    bot = Bot()
    original = bot.edit_message_text
    attempts = []

    async def fail(**kwargs):
        attempts.append(kwargs)
        raise network_failure("simulated edit outage")

    bot.edit_message_text = fail
    streamer = stream.TelegramStreamer(bot, 4242, 10)

    async def drive():
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "First "})
        )
        await streamer._deliver(False)
        assert len(attempts) == 3
        bot.edit_message_text = original
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "and final."})
        )
        await streamer.process_event(RuntimeEvent.done())
        result = await streamer.finalize()
        assert result.status == "complete" and result.message_ids == [10]

    asyncio.run(drive())
    assert not bot.messages
    assert message_text(bot.edits[-1]) == "First and final."


def test_runtime_duplicate_settles_existing_receipt_without_another_reply(monkeypatch):
    update = _approved_private_chat(monkeypatch)
    seen = []

    async def prompt(key, request, **kwargs):
        seen.append(request.request_id)
        yield RuntimeEvent.done("duplicate")

    monkeypatch.setattr(agents.runtime, "prompt", prompt)
    bot = _bot()
    context = SimpleNamespace(user_data={}, chat_data={}, bot=bot)
    asyncio.run(agents.agent_message_handler(update, context))
    assert seen == ["telegram:4242:17"]
    assert not bot.send_rich_message.called
    assert bot.send_message.await_count == 1
    final = bot.edit_message_text.await_args.kwargs
    assert final["message_id"] == 99
    assert message_text(final) == "This request was already received."
    assert final["reply_markup"] is None
    assert not update.message.answer.called


@pytest.mark.parametrize("cancel_during", ["prompt", "render"])
def test_cancelled_handler_closes_prompt_and_stops_background_edits(
    monkeypatch, cancel_during
):
    update = _approved_private_chat(monkeypatch)
    ready = asyncio.Event()
    turn_lock = asyncio.Lock()
    streams = []
    streamers = []

    async def prompt_events():
        async with turn_lock:
            yield RuntimeEvent.accepted()
            if cancel_during == "render":
                yield RuntimeEvent(type=EventType.TEXT, data={"text": "Partial answer"})
            ready.set()
            await asyncio.Event().wait()

    def prompt(*args, **kwargs):
        generator = prompt_events()
        # Retaining the stream makes cleanup deterministic, independent of GC.
        streams.append(generator)
        return generator

    class ObservedStreamer(stream.TelegramStreamer):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            streamers.append(self)

        async def process_event(self, event):
            await super().process_event(event)
            if cancel_during == "render" and event.type == EventType.TEXT:
                ready.set()
                await asyncio.Event().wait()

    monkeypatch.setattr(agents.runtime, "prompt", prompt)
    patch_agents(monkeypatch, "TelegramStreamer", ObservedStreamer)
    bot = _bot()
    context = SimpleNamespace(user_data={}, chat_data={}, bot=bot)

    async def drive():
        task = asyncio.create_task(agents.agent_message_handler(update, context))
        await asyncio.wait_for(ready.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not turn_lock.locked(), "the consumer must close its prompt generator"
        assert streamers[0]._edit_task.done(), "the cancelled turn must stop editing"

    asyncio.run(drive())


def test_a_failed_secret_notice_does_not_prevent_the_runtime_redaction(monkeypatch):
    update = _approved_private_chat(monkeypatch)
    patch_agents(
        monkeypatch,
        "_handle_pasted_secrets",
        AsyncMock(side_effect=network_failure("simulated notice failure")),
    )
    requests = []

    async def prompt(key, request, **kwargs):
        requests.append(request.text)
        yield RuntimeEvent(type=EventType.ACCEPTED)
        yield RuntimeEvent(type=EventType.TEXT, data={"text": "Processed safely."})
        yield RuntimeEvent.done("end_turn")

    monkeypatch.setattr(agents.runtime, "prompt", prompt)
    bot = _bot()
    context = SimpleNamespace(user_data={}, chat_data={}, bot=bot)
    asyncio.run(agents.agent_message_handler(update, context))
    assert requests == [update.message.text]
    assert any(message_text(call) == "Processed safely." for call in _output_calls(bot))


def test_real_runtime_persists_before_model_and_deduplicates_telegram_updates(
    monkeypatch,
):
    """Only the model and Telegram are doubles; acceptance and recording are real."""
    from condor.acp.client import Heartbeat, PromptDone, TextChunk
    from condor.runtime import SessionKey, conversations, sessions, turns

    update = _approved_private_chat(monkeypatch, mock_session=False)
    owner = update.effective_user.id
    key = SessionKey.telegram(update.effective_chat.id)
    meta = conversations.new_conversation(owner, "tg", agent_key="claude-code")
    expected_request = "telegram:4242:17"
    placeholder_failed = asyncio.Event()
    finish_model = asyncio.Event()
    prompts = []
    sends = []
    progress = []

    def saved_user_turn():
        saved = conversations.read_transcript(owner, meta.id)
        assert len(saved) == 1
        assert saved[0].role == "user"
        assert saved[0].text == update.message.text
        assert saved[0].request_id == expected_request

    class ScriptedClient:
        alive = True

        async def prompt_stream(self, text):
            saved_user_turn()
            prompts.append(text)
            yield Heartbeat(elapsed_seconds=0)
            await finish_model.wait()
            yield TextChunk(text="The recorded answer.")
            yield PromptDone(stop_reason="end_turn")

    session = sessions.AgentSession(
        key=key,
        agent_key="claude-code",
        client=ScriptedClient(),
        user_id=owner,
        conversation_id=meta.id,
    )
    monkeypatch.setattr(sessions, "_sessions", {str(key): session})
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(turns, "_turns", {})
    monkeypatch.setattr(turns, "_sinks", [])

    async def send_message(**kwargs):
        progress.append(kwargs)
        placeholder_failed.set()
        raise network_failure("simulated progress outage")

    async def send_rich_message(**kwargs):
        sends.append(kwargs)
        return SimpleNamespace(message_id=99)

    context = SimpleNamespace(
        user_data={},
        chat_data={},
        bot=_bot(
            send_message=send_message,
            send_rich_message=send_rich_message,
        ),
    )

    async def drive():
        running = asyncio.create_task(agents.agent_message_handler(update, context))
        await asyncio.wait_for(placeholder_failed.wait(), timeout=2)
        # A repeated update while its original turn is still active is ignored.
        await agents.agent_message_handler(update, context)
        assert not sends
        finish_model.set()
        await asyncio.wait_for(running, timeout=2)
        delivered = len(sends)
        # The durable request identity also suppresses a later duplicate.
        await agents.agent_message_handler(update, context)
        assert len(sends) == delivered

    asyncio.run(drive())
    assert prompts == [update.message.text]
    assert len(progress) == 1
    assert not sends, "An unacknowledged send must not create another message"
    saved = conversations.read_transcript(owner, meta.id)
    assert [(entry.role, entry.text) for entry in saved] == [
        ("user", update.message.text),
        ("assistant", "The recorded answer."),
    ]
    assert not session.is_busy
    assert not turns._turns
    assert not conversations._live_recorders


@pytest.mark.parametrize("failed_stage", ["initial_send", "final_edit"])
def test_voice_display_failure_still_hands_transcription_to_the_ai(
    monkeypatch, caplog, failed_stage
):
    import condor.telegram.input as telegram_input

    update = _approved_private_chat(monkeypatch)
    update.message.text = None
    update.message.voice = SimpleNamespace(
        file_id="voice", file_unique_id="voice", file_size=3
    )
    outage = network_failure("https://api.telegram.org/botSECRET/sendMessage")
    progress_ready = asyncio.Event()

    async def send(**kwargs):
        progress_ready.set()
        if failed_stage == "initial_send":
            raise outage
        return SimpleNamespace(message_id=99)

    async def edit(**kwargs):
        if kwargs.get("reply_markup") is None:
            raise outage
        return True

    async def resolve_input(*args, **kwargs):
        await progress_ready.wait()
        return telegram_input.ResolvedInput(
            "How is the market?", (), ("How is the market?",)
        )

    resolve = AsyncMock(side_effect=resolve_input)
    monkeypatch.setattr(
        telegram_input, "get_resolver", lambda bot: SimpleNamespace(resolve=resolve)
    )
    requests = []

    async def prompt(key, request, **kwargs):
        requests.append(request)
        yield RuntimeEvent.accepted()
        yield RuntimeEvent(type=EventType.TEXT, data={"text": "The answer."})
        yield RuntimeEvent.done("end_turn")

    monkeypatch.setattr(agents.runtime, "prompt", prompt)
    bot = _bot(
        send_message=AsyncMock(side_effect=send),
        edit_message_text=AsyncMock(side_effect=edit),
    )
    context = SimpleNamespace(user_data={}, chat_data={}, bot=bot)
    asyncio.run(agents.agent_message_handler(update, context))
    resolve.assert_awaited_once()
    assert resolve.await_args.args[0].media[0].kind == "audio"
    assert [(req.text, req.request_id) for req in requests] == [
        ("How is the market?", "telegram:4242:17")
    ]
    assert not context.chat_data
    assert bot.send_message.await_count == 1 and not bot.send_rich_message.called
    if failed_stage == "final_edit":
        final_edits = [
            call.kwargs
            for call in bot.edit_message_text.await_args_list
            if call.kwargs.get("reply_markup") is None
        ]
        assert len(final_edits) == 3
        assert all(call["message_id"] == 99 for call in final_edits)
        assert message_text(final_edits[-1]).startswith("🎙 How is the market?")
        assert "The answer." in message_text(final_edits[-1])
    else:
        assert not bot.edit_message_text.called
        assert "status=delivery_unknown" in caplog.text
    assert "Telegram final delivery status=" in caplog.text
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize(
    "code", ["download_failed", "transcription_failed", "empty_transcription"]
)
def test_voice_input_failure_does_not_invent_an_ai_prompt(monkeypatch, caplog, code):
    import condor.telegram.input as telegram_input

    update = _approved_private_chat(monkeypatch)
    update.message.text = None
    update.message.voice = SimpleNamespace(
        file_id="voice", file_unique_id="voice", file_size=3
    )
    error = telegram_input.InputError(
        code, "Could not read the audio. Please try again."
    )
    error.__cause__ = network_failure("https://api.telegram.org/botSECRET/getFile")
    resolve = AsyncMock(side_effect=error)
    monkeypatch.setattr(
        telegram_input, "get_resolver", lambda bot: SimpleNamespace(resolve=resolve)
    )
    prompt = AsyncMock()
    monkeypatch.setattr(agents.runtime, "prompt", prompt)
    bot = _bot()
    context = SimpleNamespace(user_data={}, chat_data={}, bot=bot)
    asyncio.run(agents.agent_message_handler(update, context))

    assert not prompt.called
    assert not context.chat_data
    resolve.assert_awaited_once()
    assert any(message_text(call) == error.user_message for call in _output_calls(bot))
    assert "SECRET" not in caplog.text


def test_cli_availability_recovers_after_installation_without_restart(monkeypatch):
    now = [0]
    probes = []
    patch_agents(monkeypatch, "_cli_available_cache", {})
    patch_agents(monkeypatch, "is_pydantic_ai_model", lambda _: False)
    monkeypatch.setattr(agents.availability.time, "monotonic", lambda: now[0])

    def which(binary):
        probes.append(binary)
        return "/installed/backend" if now[0] >= 60 else None

    monkeypatch.setattr(agents.availability.shutil, "which", which)
    assert not agents._is_agent_available("claude-code")
    now[0] = 59
    assert not agents._is_agent_available("claude-code")
    assert len(probes) == 1
    now[0] = 60
    assert agents._is_agent_available("claude-code")
    assert len(probes) == 2


@pytest.mark.parametrize("delivery_fails", [False, True])
def test_missing_cli_is_explained_and_notice_failure_is_logged(
    monkeypatch, caplog, delivery_fails
):
    update = _approved_private_chat(monkeypatch)
    patch_agents(monkeypatch, "get_session", AsyncMock(return_value=None))
    patch_agents(monkeypatch, "_reclaim_default_agent", lambda _: "claude-code")
    patch_agents(monkeypatch, "resolve_chat_binding", lambda _: (None, ""))
    patch_agents(monkeypatch, "_is_agent_available", lambda _: False)
    prompt = AsyncMock()
    monkeypatch.setattr(agents.runtime, "prompt", prompt)
    bot = _bot(
        edit_message_text=AsyncMock(
            side_effect=network_failure("unavailable") if delivery_fails else None,
            return_value=True,
        )
    )
    context = SimpleNamespace(user_data={}, chat_data={}, bot=bot)
    asyncio.run(agents.agent_message_handler(update, context))

    assert not prompt.called
    notice = next(
        message_text(call)
        for call in _output_calls(bot)
        if "not available" in message_text(call)
    )
    assert "claude-code" in notice and "/agent" in notice
    assert "not available" in notice
    if delivery_fails:
        assert "Telegram final delivery status=" in caplog.text


@pytest.mark.parametrize("has_update", [False, True])
def test_global_network_error_identifies_phase_without_claiming_retry(
    caplog, has_update
):
    import main

    update = SimpleNamespace(update_id=123) if has_update else None
    context = SimpleNamespace(
        error=network_failure("https://api.telegram.org/botSECRET/sendMessage")
    )
    asyncio.run(main.error_handler(update, context))
    phase = "update" if has_update else "background"
    assert f"stage={phase}" in caplog.text
    assert "does not retry the update" in caplog.text
    assert "will retry" not in caplog.text
    assert "SECRET" not in caplog.text


def test_unhandled_error_tells_the_chat():
    import main
    from tests.support.markdown_v2 import assert_valid

    bot = SimpleNamespace(send_message=AsyncMock())
    update = SimpleNamespace(update_id=1, effective_chat=SimpleNamespace(id=42))
    context = SimpleNamespace(error=RuntimeError("boom"), bot=bot)
    asyncio.run(main.error_handler(update, context))
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42 and kwargs["parse_mode"] == "MarkdownV2"
    assert_valid(kwargs["text"])
    assert "boom" not in kwargs["text"]
