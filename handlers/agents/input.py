"""Free-text agent input: armed prompts, message handler and streaming a turn."""

import asyncio
import logging
from contextlib import aclosing

import condor.runtime.client as runtime
from condor.runtime import (
    EventType,
    PromptRequest,
    secrets,
)
from condor.runtime.events import RuntimeEvent
from condor.telegram.types import (
    Context,
    Update,
)

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
# "Enter model manually", then picking a different LLM) leaves the flag set and
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
        await update.message.answer("Session reset. Send a message to start fresh.")
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
    from condor.telegram.ingress import get_ingress
    from condor.telegram.input import InputError
    from config_manager import UserRole, get_config_manager

    if not update.message or update.effective_chat.type != "private":
        return
    role = get_config_manager().get_user_role(update.effective_user.id)
    if role not in (UserRole.ADMIN, UserRole.USER):
        return
    try:
        async with get_ingress().receive(update) as admission:
            if admission is not None:
                await _process_agent_input(update, context, admission)
    except InputError as exc:
        log.warning("Telegram input rejected code=%s", exc.code)
        await _try_telegram_message(
            update, "input_error", update.message.answer, text=exc.user_message
        )


async def _process_agent_input(update, context, admission) -> None:
    from condor.preferences import get_voice_prefs
    from condor.runtime import attachments, conversations
    from condor.runtime.models import PromptImage
    from condor.telegram.input import (
        InputError,
        ResolvedInput,
        extract_input,
        get_resolver,
        message_text,
        rich_message_payload,
    )
    from config_manager import UserRole, get_config_manager

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if get_config_manager().get_user_role(user_id) not in (
        UserRole.ADMIN,
        UserRole.USER,
    ):
        return
    # Typed controls act on this message, never on quoted text or a transcript.
    input_error = None
    try:
        own_text = message_text(update.message)
    except InputError as exc:
        own_text = ""
        input_error = exc
    # Captions remain prompt context, never typed reset or configuration controls.
    if not update.message.text and not rich_message_payload(update.message):
        own_text = ""
    armed = any(context.user_data.get(key) for key in _ARMED_TEXT_INPUT_KEYS)
    if own_text and (own_text.strip() == "-" or armed):
        from condor.telegram.input import input_has_attachments

        if armed and input_has_attachments(update.message):
            raise InputError(
                "text_expected",
                "Please send text for the current input, or use /cancel before sending attachments.",
            )
        await _notice_input_secrets(update, context, own_text)
        if await _consume_agent_text_input(update, context, own_text):
            admission.accepted = True
            return
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
        return
    incoming = extract_input(admission.messages)

    # Avoid downloading/retranscribing a replay after a restart, which the
    # ingress tombstones (in memory) cannot cover. A set lookup against the
    # request index; the runtime remains the final deduplication gate.
    if incoming.media and conversations.has_request(user_id, admission.request_id):
        admission.accepted = True
        return

    if incoming.media and any(
        context.user_data.get(key) for key in _ARMED_TEXT_INPUT_KEYS
    ):
        raise InputError(
            "text_expected",
            "Please send text for the current input, or use /cancel before sending attachments.",
        )

    session = await _ensure_chat_session(update, context)
    if session is None:
        return
    conversation_id = getattr(session, "conversation_id", "")
    status = None
    resolved = ResolvedInput(incoming.text, (), ())
    if incoming.media:
        status = await _try_telegram_message(
            update,
            "input_status",
            update.message.answer,
            text="Reading attachments… /stop cancels this message.",
            disable_notification=True,
        )
        prefs = get_voice_prefs(context.user_data)
        try:
            resolved = await get_resolver(context.bot).resolve(
                incoming,
                language=prefs.get("language"),
                model_size=prefs.get("whisper_model", "base"),
            )
        except InputError as exc:
            await _try_telegram_message(
                update,
                "input_error",
                status.edit_text if status else update.message.answer,
                text=exc.user_message,
            )
            return
        # Resolution preserves incoming text and appends document/transcript
        # content. Do not deliver the same caption warning twice.
        decoded_text = resolved.text[len(incoming.text) :]
        if decoded_text.strip():
            await _notice_input_secrets(
                update, context, decoded_text, tuple(sources.values())
            )
        # Commands and other surfaces may switch conversations during downloads
        # or notices. This is the last await before saving/accepting the input.
        current = await get_session(chat_id)
        if (
            current is None
            or not current.alive
            or getattr(current, "conversation_id", "") != conversation_id
            or get_config_manager().get_user_role(user_id)
            not in (UserRole.ADMIN, UserRole.USER)
        ):
            await _try_telegram_message(
                update,
                "input_changed",
                status.edit_text if status else update.message.answer,
                text="The chat or your access changed while reading attachments. Please send the message again.",
            )
            return

    if get_config_manager().get_user_role(user_id) not in (
        UserRole.ADMIN,
        UserRole.USER,
    ):
        return
    saved = []
    duplicate = False
    try:
        images = []
        for image in resolved.images:
            attachment = await asyncio.to_thread(
                attachments.save, user_id, conversation_id, image.data
            )
            saved.append(attachment)
            images.append(
                PromptImage(data=image.data, mime=attachment.mime, id=attachment.id)
            )
        request = PromptRequest(
            text=resolved.text,
            images=images,
            request_id=admission.request_id,
        )
        prefix = "\n".join(
            f"🎙 {_voice_display(text)}" for text in resolved.transcriptions
        )
        duplicate = await _stream_agent_turn(
            update,
            context,
            admission,
            request,
            status,
            prefix,
            conversation_id=conversation_id,
        )
    except (attachments.AttachmentError, OSError):
        await _try_telegram_message(
            update,
            "attachment_save",
            status.edit_text if status else update.message.answer,
            text="Could not save the attachments in this conversation. Please send the message again.",
        )
    finally:
        if not admission.accepted or duplicate:
            for attachment in saved:
                (
                    attachments.attachments_dir(user_id, conversation_id)
                    / attachment.id
                ).unlink(missing_ok=True)


async def _stream_agent_turn(
    update, context, admission, request, status, prefix, *, conversation_id
) -> bool:
    """Delivery consumes accepted events and never retries the underlying turn."""
    from config_manager import UserRole, get_config_manager

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    streamer = TelegramStreamer(
        bot=context.bot,
        chat_id=chat_id,
        message_id=status.message_id if status else None,
        prefix=prefix,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=request.request_id,
        input_message_id=update.message.message_id,
    )
    edit_started = False
    last_event = None
    try:
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
        await streamer.stop_edit_loop()
    return False
