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


def _markdown(kwargs):
    rich = kwargs.get("rich_message")
    return getattr(rich, "markdown", None) or kwargs.get("text")


def _bot(**overrides):
    methods = dict(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        send_rich_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        send_rich_message_draft=AsyncMock(return_value=True),
        delete_message=AsyncMock(),
        edit_message_text=AsyncMock(),
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


@pytest.mark.parametrize("recover_for_final", [False, True])
def test_progress_failure_does_not_abort_or_replay_the_turn(
    monkeypatch, caplog, recover_for_final
):
    update = _approved_private_chat(monkeypatch)
    requests = []
    completed = []
    sends = []
    drafts = []
    initial_delivery_failed = asyncio.Event()

    async def send_rich_message_draft(**kwargs):
        # This proves output starts after the runtime has accepted the request.
        assert requests
        drafts.append(kwargs)
        initial_delivery_failed.set()
        raise network_failure("https://api.telegram.org/botSECRET/sendMessage")

    async def send_rich_message(**kwargs):
        sends.append(kwargs)
        if not recover_for_final:
            raise network_failure("https://api.telegram.org/botSECRET/sendMessage")
        return SimpleNamespace(message_id=99)

    async def prompt(key, request, **kwargs):
        requests.append(request.request_id)
        yield RuntimeEvent(type=EventType.ACCEPTED)
        yield RuntimeEvent(type=EventType.HEARTBEAT)
        # The AI keeps consuming its turn while background output fails.
        await initial_delivery_failed.wait()
        yield RuntimeEvent(type=EventType.TEXT, data={"text": "**The answer**"})
        yield RuntimeEvent.done("end_turn")
        completed.append(True)

    monkeypatch.setattr(agents.runtime, "prompt", prompt)
    context = SimpleNamespace(
        user_data={},
        chat_data={},
        bot=_bot(
            send_rich_message_draft=send_rich_message_draft,
            send_rich_message=send_rich_message,
        ),
    )
    asyncio.run(agents.agent_message_handler(update, context))

    assert requests == ["telegram:4242:17"]
    assert completed == [True]
    assert not update.message.answer.called
    assert drafts
    assert len(sends) == 1, "an ambiguous final send must not be blindly retried"
    assert _markdown(sends[-1]) == "**The answer**"
    assert "SECRET" not in caplog.text
    assert "4242" not in caplog.text
    if not recover_for_final:
        assert "status=delivery_unknown" in caplog.text


def test_failed_progress_does_not_prevent_independent_final_delivery():
    from tests.support.telegram import Bot

    bot = Bot()
    bot.draft_failure = network_failure("simulated outage")
    streamer = stream.TelegramStreamer(bot, 4242, 10)

    async def drive():
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "First "})
        )
        await streamer._flush()
        assert streamer._progress_disabled
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "and final."})
        )
        await streamer.process_event(RuntimeEvent.done())
        result = await streamer.finalize()
        assert result.status == "complete"

    asyncio.run(drive())
    assert bot.messages[-1]["text"] == "First and final."


def test_duplicate_update_does_not_create_a_second_telegram_reply(monkeypatch):
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
    assert not bot.send_message.called
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
    assert _markdown(bot.send_rich_message.await_args.kwargs) == "Processed safely."


def test_real_runtime_persists_before_delivery_and_deduplicates_telegram_updates(
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
    drafts = []

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

    async def send_rich_message_draft(**kwargs):
        drafts.append(kwargs)
        saved_user_turn()
        placeholder_failed.set()
        raise network_failure("simulated progress outage")

    async def send_rich_message(**kwargs):
        sends.append(kwargs)
        return SimpleNamespace(message_id=99)

    context = SimpleNamespace(
        user_data={},
        chat_data={},
        bot=_bot(
            send_rich_message_draft=send_rich_message_draft,
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
    assert drafts
    assert len(sends) == 1
    assert _markdown(sends[-1]) == "The recorded answer."
    saved = conversations.read_transcript(owner, meta.id)
    assert [(entry.role, entry.text) for entry in saved] == [
        ("user", update.message.text),
        ("assistant", "The recorded answer."),
    ]
    assert not session.is_busy
    assert not turns._turns
    assert not conversations._live_recorders


@pytest.mark.parametrize("failed_stage", ["input_status", "final_send"])
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
    status = SimpleNamespace(
        message_id=22,
        edit_text=AsyncMock(),
    )
    update.message.answer = AsyncMock(
        side_effect=outage if failed_stage == "input_status" else None,
        return_value=status,
    )
    resolve = AsyncMock(
        return_value=telegram_input.ResolvedInput(
            "How is the market?", (), ("How is the market?",)
        )
    )
    monkeypatch.setattr(
        telegram_input, "get_resolver", lambda bot: SimpleNamespace(resolve=resolve)
    )
    requests = []

    async def prompt(key, request, **kwargs):
        requests.append(request)
        yield RuntimeEvent(type=EventType.ACCEPTED)
        yield RuntimeEvent(type=EventType.TEXT, data={"text": "The answer."})
        yield RuntimeEvent.done("end_turn")

    monkeypatch.setattr(agents.runtime, "prompt", prompt)
    bot = _bot(
        send_rich_message=AsyncMock(
            return_value=SimpleNamespace(message_id=99),
            side_effect=outage if failed_stage == "final_send" else None,
        ),
    )
    context = SimpleNamespace(user_data={}, chat_data={}, bot=bot)
    asyncio.run(agents.agent_message_handler(update, context))

    resolve.assert_awaited_once()
    assert resolve.await_args.args[0].media[0].kind == "audio"
    assert [(req.text, req.request_id) for req in requests] == [
        ("How is the market?", "telegram:4242:17")
    ]
    assert not context.chat_data
    markdown = _markdown(bot.send_rich_message.await_args.kwargs)
    assert markdown.startswith("🎙 How is the market?")
    assert "The answer." in markdown
    assert (
        "stage=input_status"
        if failed_stage == "input_status"
        else "status=delivery_unknown"
    ) in caplog.text
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
    status = SimpleNamespace(message_id=22, edit_text=AsyncMock())
    update.message.answer = AsyncMock(return_value=status)
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
    context = SimpleNamespace(user_data={}, chat_data={}, bot=object())
    asyncio.run(agents.agent_message_handler(update, context))

    assert not prompt.called
    assert not context.chat_data
    assert "Please try again" in status.edit_text.await_args.kwargs["text"]
    resolve.assert_awaited_once()
    assert status.edit_text.await_args.kwargs["text"] == error.user_message
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
    update.message.answer = AsyncMock(
        side_effect=network_failure("unavailable") if delivery_fails else None
    )
    context = SimpleNamespace(user_data={}, chat_data={}, bot=object())
    asyncio.run(agents.agent_message_handler(update, context))

    assert not prompt.called
    notice = update.message.answer.await_args.kwargs["text"]
    assert "claude-code" in notice and "/agent" in notice
    assert "not available" in notice
    if delivery_fails:
        assert "stage=agent_unavailable" in caplog.text


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
