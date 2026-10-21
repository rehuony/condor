"""Free-text agent input: armed prompts, message handler and streaming a turn."""

import asyncio
import logging
import time
from contextlib import aclosing

import condor.runtime.client as runtime
from condor.runtime import (
    EventType,
    PromptRequest,
    secrets,
)
from condor.runtime.events import RuntimeEvent
from condor.telegram.output.timing import ReplyTiming
from condor.telegram.types import (
    Context,
    Update,
)
from utils import telegram_ui as ui

from ._shared import (
    forget_chat_conversation,
)
from .availability import _try_telegram_message
from .compact import _do_compact_from_message
from .custom_endpoints import (
    _resolve_custom_key,
    _resolve_custom_search,
    _resolve_custom_url,
)
from .openrouter import _resolve_openrouter_typed_slug
from .secrets import _notice_input_secrets
from .session import _ensure_chat_session, _tg_key, destroy_session, get_session
from .stream import TelegramStreamer

log = logging.getLogger(__name__)


# Flags that make the next plain message mean something other than "talk to the
# agent". Tapping any button is a fresh intent, so they are all disarmed before
# the callback router dispatches: otherwise walking away from a prompt (e.g.
# "Enter Model Manually", then picking a different LLM) leaves the flag set and
# the user's next task is parsed as a model slug / URL / compact instruction.
# Handlers that genuinely want an armed mode set their own flag after this runs.
_ARMED_TEXT_INPUT_KEYS = (
    "_openrouter_typing_slug",
    "_custom_typing_url",
    "_custom_typing_key",
    "_custom_typing_search",
    "agent_compact_custom",
)


def _disarm_text_input(context: Context) -> None:
    """Drop every armed "next message is the answer" mode."""
    for key in _ARMED_TEXT_INPUT_KEYS:
        context.user_data.pop(key, None)


def _voice_display(text: str) -> str:
    """What a transcription is allowed to look like on screen.

    The raw transcript still flows on: ``_handle_pasted_secrets`` needs it to
    detect the phrase and delete the voice message, and ``runtime.prompt``
    redacts itself before the model or the disk sees anything. But nothing the
    bot *renders* may carry a spoken recovery phrase — otherwise the notice
    ("I removed a recovery phrase ... and deleted the message") is a lie while
    the phrase is still legible in the bot's own message, first as the
    retained transcription in the final answer (SEC-281).
    """
    return secrets.redact(text)[0]


async def _consume_agent_text_input(update, context, text: str) -> bool:
    """Consume explicit text controls before creating an ordinary AI turn."""
    chat_id = update.effective_chat.id
    # "-" resets the ACP session: destroy current and let the next block auto-create a new one
    if text.strip() == "-":
        session = await get_session(chat_id)
        if session:
            await destroy_session(chat_id)
        # "Fresh" is the whole point of the verb: without this the auto-create
        # below would replay the transcript the user just asked to leave.
        forget_chat_conversation(context.user_data)
        await update.message.answer(
            ui.notice("success", "Session reset", "Send a message to start fresh."),
            parse_mode="MarkdownV2",
        )
        return True

    # Handle custom compact input
    if context.user_data.pop("agent_compact_custom", None):
        await _do_compact_from_message(update, context, text)
        return True

    # Handle typed OpenRouter slug input
    if context.user_data.pop("_openrouter_typing_slug", None):
        await _resolve_openrouter_typed_slug(update, context, text)
        return True

    # Handle custom endpoint input (base URL, then API key) and model search
    if context.user_data.pop("_custom_typing_url", None):
        await _resolve_custom_url(update, context, text)
        return True
    if context.user_data.pop("_custom_typing_key", None):
        await _resolve_custom_key(update, context, text)
        return True
    if context.user_data.pop("_custom_typing_search", None):
        await _resolve_custom_search(update, context, text)
        return True

    return False


async def agent_message_handler(update: Update, context: Context) -> None:
    """Prepare one complete Telegram turn, then hand execution to the runtime.

    The ingress owns batching, bounded admission and preprocessing order. The
    runtime owns durable deduplication, the conversation queue and model work.
    No media payload, download task or voice hand-off enters the user-state store.
    """
    from condor.runtime import conversations
    from condor.telegram.ingress import get_ingress
    from condor.telegram.input import (
        InputError,
        input_has_attachments,
        message_text,
        rich_message_payload,
    )
    from config_manager import UserRole, get_config_manager

    if not update.message or update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    if get_config_manager().get_user_role(user_id) not in (
        UserRole.ADMIN,
        UserRole.USER,
    ):
        return
    ingress = get_ingress()
    received_at = time.monotonic()
    ticket = None
    streamer = None
    timing = None
    try:
        # Explicit controls do not create a conversation turn or progress receipt.
        own_text = ""
        if update.message.text or rich_message_payload(update.message):
            try:
                own_text = message_text(update.message)
            except InputError:
                pass  # The ordinary input path reports malformed rich content.
        armed = any(context.user_data.get(key) for key in _ARMED_TEXT_INPUT_KEYS)
        if armed and input_has_attachments(update.message):
            raise InputError(
                "text_expected",
                "Please send text for the current input, or use /cancel before sending attachments.",
            )
        is_control = bool(own_text and (own_text.strip() == "-" or armed))
        ticket = ingress.reserve(update)
        if ticket is None:
            return
        timing = ReplyTiming(ticket.request_id, received_at)
        timing.mark("received")
        # Durable acceptance survives restart, unlike the ingress tombstones.
        timing.mark("dedupe_start")
        duplicate = conversations.has_request(user_id, ticket.request_id)
        timing.mark("dedupe_end")
        if duplicate:
            timing.mark("duplicate")
            ticket.accepted = True
            return

        def begin_reply():
            reply = TelegramStreamer(
                bot=context.bot,
                chat_id=update.effective_chat.id,
                user_id=user_id,
                request_id=ticket.request_id,
                input_message_id=update.message.message_id,
                timing=timing,
            )
            owner = asyncio.current_task()
            assert owner is not None
            reply.begin_preparation(
                (
                    "Waiting for earlier messages…"
                    if ticket.waiting
                    else "Preparing your message…"
                ),
                owner.cancel,
            )
            return reply

        if not is_control:
            streamer = begin_reply()
        async with ingress.admit(ticket) as admission:
            timing.mark("preparation_admitted")
            if is_control:
                await _notice_input_secrets(update, context, own_text)
                if await _consume_agent_text_input(update, context, own_text):
                    admission.accepted = True
                    return
                # A menu callback may disarm a typed form while admission waits.
                streamer = begin_reply()
            streamer.set_phase("Preparing your message…")
            await _process_agent_input(update, context, admission, streamer)
    except InputError as exc:
        log.warning("Telegram input rejected code=%s", exc.code)
        if streamer is not None:
            await streamer.process_event(
                RuntimeEvent(
                    type=EventType.ERROR,
                    data={"code": "telegram_input", "message": exc.user_message},
                )
            )
            await streamer.process_event(RuntimeEvent.done("error"))
            await streamer.finalize()
        else:
            await _try_telegram_message(
                update, "input_error", update.message.answer, text=exc.user_message
            )
    except asyncio.CancelledError:
        # Shutdown must not wait for Telegram's delivery cooldown. The finally
        # closes delivery workers after runtime and attachment cleanup settle.
        if streamer is not None and not ingress.closed:
            await streamer.process_event(RuntimeEvent.done("cancelled"))
            await streamer.finalize()
        raise
    except Exception as exc:
        if streamer is None:
            raise
        log.warning("Telegram input failed error_type=%s", type(exc).__name__)
        await streamer.process_event(
            RuntimeEvent.error("Could not finish this turn. Please try again.")
        )
        await streamer.process_event(RuntimeEvent.done("error"))
        await streamer.finalize()
    finally:
        if ticket is not None:
            ingress.release(ticket)
        if streamer is not None:
            await streamer.stop_edit_loop()
        if timing is not None:
            timing.mark("request_released")


async def _process_agent_input(update, context, admission, streamer) -> None:
    from condor.preferences import get_voice_prefs
    from condor.runtime import attachments, conversations
    from condor.runtime.models import PromptImage
    from condor.telegram.input import (
        InputError,
        ResolvedInput,
        extract_input,
        get_resolver,
        message_text,
    )
    from config_manager import UserRole, get_config_manager

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if get_config_manager().get_user_role(user_id) not in (
        UserRole.ADMIN,
        UserRole.USER,
    ):
        raise InputError(
            "access_changed",
            "Your access changed. This message was not sent to the agent.",
        )
    input_error = None
    sources = {}
    for message in admission.messages:
        sources[(message.chat.id, message.message_id)] = message
        reply = getattr(message, "reply_to_message", None)
        if reply is not None:
            sources[(reply.chat.id, reply.message_id)] = reply

    # Protect text already delivered by Telegram even if its attachment later
    # fails validation or download. Scan decoded text separately below.
    text_sources = list(sources.values())
    for message in admission.messages:
        external = getattr(message, "external_reply", None)
        if external is not None and not getattr(message, "reply_to_message", None):
            text_sources.append(external)
    source_texts = []
    for message in text_sources:
        try:
            source_texts.append(message_text(message))
        except InputError as exc:
            # A refused rich quote must not skip protection of the user's own
            # text. Report the refusal after scanning the readable sources.
            if input_error is None:
                input_error = exc
    source_text = "\n\n".join(source_texts)
    if source_text:
        await _notice_input_secrets(
            update, context, source_text, tuple(sources.values())
        )
    if input_error is not None:
        raise input_error
    if get_config_manager().get_user_role(user_id) not in (
        UserRole.ADMIN,
        UserRole.USER,
    ):
        raise InputError(
            "access_changed",
            "Your access changed. This message was not sent to the agent.",
        )
    incoming = extract_input(admission.messages)

    if incoming.media and any(
        context.user_data.get(key) for key in _ARMED_TEXT_INPUT_KEYS
    ):
        raise InputError(
            "text_expected",
            "Please send text for the current input, or use /cancel before sending attachments.",
        )

    async def startup_error(text):
        raise InputError("session_start", text)

    streamer.set_phase("Starting agent…")
    streamer.timing.mark("session_start")
    session = await _ensure_chat_session(update, context, on_error=startup_error)
    if session is None:
        raise InputError(
            "session_start",
            "Could not start the agent. Please try again or choose another model with /agent.",
        )
    streamer.timing.mark("session_ready")
    conversation_id = getattr(session, "conversation_id", "")
    resolved = ResolvedInput(incoming.text, (), ())
    if incoming.media:
        streamer.set_phase("Reading attachments…")
        streamer.timing.mark("attachments_start")
        prefs = get_voice_prefs(context.user_data)
        resolved = await get_resolver(context.bot).resolve(
            incoming,
            language=prefs.get("language"),
            model_size=prefs.get("whisper_model", "base"),
        )
        # Resolution preserves incoming text and appends document/transcript
        # content. Do not deliver the same caption warning twice.
        decoded_text = resolved.text[len(incoming.text) :]
        if decoded_text.strip():
            await _notice_input_secrets(
                update, context, decoded_text, tuple(sources.values())
            )
        # Commands and other surfaces may switch conversations during downloads
        # or notices. Reject those changes before saving files unnecessarily.
        current = await get_session(chat_id)
        if (
            current is None
            or not current.alive
            or getattr(current, "conversation_id", "") != conversation_id
            or get_config_manager().get_user_role(user_id)
            not in (UserRole.ADMIN, UserRole.USER)
        ):
            raise InputError(
                "input_changed",
                "The chat or your access changed while reading attachments. Please send the message again.",
            )

    if get_config_manager().get_user_role(user_id) not in (
        UserRole.ADMIN,
        UserRole.USER,
    ):
        raise InputError(
            "access_changed",
            "Your access changed. This message was not sent to the agent.",
        )
    saved = []
    duplicate = False
    try:
        images = []
        for image in resolved.images:
            saving = asyncio.create_task(
                asyncio.to_thread(
                    attachments.save, user_id, conversation_id, image.data
                )
            )
            try:
                attachment = await asyncio.shield(saving)
            except asyncio.CancelledError:
                # A worker thread can finish its write after cancellation.
                # Recover its receipt so the finally below can remove it.
                try:
                    saved.append(await saving)
                except (attachments.AttachmentError, OSError):
                    pass
                raise
            saved.append(attachment)
            images.append(
                PromptImage(data=image.data, mime=attachment.mime, id=attachment.id)
            )
        if saved:
            # Saving runs in worker threads, so the chat may have changed since
            # resolution. From this local snapshot through runtime.prompt's
            # initial session capture, there is no further scheduling boundary.
            current = await get_session(chat_id)
            if (
                current is None
                or not current.alive
                or getattr(current, "conversation_id", "") != conversation_id
                or get_config_manager().get_user_role(user_id)
                not in (UserRole.ADMIN, UserRole.USER)
            ):
                raise InputError(
                    "input_changed",
                    "The chat or your access changed while preparing attachments. Please send the message again.",
                )
        request = PromptRequest(
            text=resolved.text,
            images=images,
            request_id=admission.request_id,
        )
        prefix = "\n".join(
            f"🎙 {_voice_display(text)}" for text in resolved.transcriptions
        )
        streamer.timing.mark("input_ready")
        duplicate = await _stream_agent_turn(
            update,
            context,
            admission,
            request,
            prefix,
            conversation_id=conversation_id,
            streamer=streamer,
        )
    except (attachments.AttachmentError, OSError) as exc:
        raise InputError(
            "attachment_save",
            "Could not save the attachments in this conversation. Please send the message again.",
        ) from exc
    finally:
        # Cancellation can arrive while the runtime's acceptance write is
        # finishing, before it yields ACCEPTED. Keep files already referenced
        # by that durable turn, even though this handler saw no event.
        accepted = admission.accepted or conversations.has_request(
            user_id, admission.request_id
        )
        if not accepted or duplicate:
            for attachment in saved:
                (
                    attachments.attachments_dir(user_id, conversation_id)
                    / attachment.id
                ).unlink(missing_ok=True)


async def _stream_agent_turn(
    update,
    context,
    admission,
    request,
    prefix,
    *,
    conversation_id,
    streamer=None,
) -> bool:
    """Delivery consumes accepted events and never retries the underlying turn."""
    from config_manager import UserRole, get_config_manager

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    owns_streamer = streamer is None
    if streamer is None:
        streamer = TelegramStreamer(
            bot=context.bot,
            chat_id=chat_id,
            user_id=user_id,
            request_id=request.request_id,
            input_message_id=update.message.message_id,
        )
    streamer.bind_input(
        conversation_id=conversation_id,
        prefix=prefix,
    )
    edit_started = False
    last_event = None
    try:
        streamer.timing.mark("runtime_start")
        async with aclosing(
            runtime.prompt(
                _tg_key(chat_id),
                request,
                on_busy="queue",
                can_execute=lambda: get_config_manager().get_user_role(user_id)
                in (UserRole.ADMIN, UserRole.USER),
            )
        ) as events:
            async for event in events:
                if event.type == EventType.ACCEPTED:
                    admission.accepted = True
                admission.release()
                if event.type == EventType.DONE and event.stop_reason == "duplicate":
                    admission.accepted = True
                    await streamer.process_event(event)
                    await streamer.finalize()
                    return True
                await streamer.process_event(event)
                if not edit_started:
                    streamer.start_edit_loop()
                    edit_started = True
                last_event = event
        stop_reason = (
            last_event.stop_reason
            if last_event is not None and last_event.type == EventType.DONE
            else ""
        )
        if stop_reason == "disconnected":
            await destroy_session(chat_id)
        elif stop_reason == "timeout":
            await destroy_session(chat_id)
        await streamer.finalize()
    except Exception as exc:
        # Preserve the conversation on a transport/rendering failure. Log only
        # the type: third-party exceptions can contain credential-bearing URLs.
        log.warning("Telegram turn failed error_type=%s", type(exc).__name__)
        await streamer.process_event(
            RuntimeEvent.error("Could not finish this turn. Please try again.")
        )
        await streamer.finalize()
    finally:
        if owns_streamer:
            await streamer.stop_edit_loop()
    return False
