"""Stop, cancel and close the agent session."""

import logging

import condor.runtime.client as runtime
from condor.telegram.output.runs import (
    RunTarget,
    active_run,
    runs_for_chat,
    unregister_run,
)
from condor.telegram.types import (
    Context,
    Update,
)
from utils import telegram_ui as ui
from utils.auth import restricted

from ._shared import NO_SESSION, PRIVATE_ONLY
from .session import destroy_session

log = logging.getLogger(__name__)


def _claim_stop(chat_id: int, user_id: int) -> tuple[RunTarget, ...]:
    """Freeze and retire this chat's current requests before any network await."""
    targets = runs_for_chat(chat_id, user_id)
    for target in targets:
        target.stopped = True
        unregister_run(target)
    for target in targets:
        if target.on_stop is not None:
            target.on_stop()
        cancel, target.cancel_preparation = target.cancel_preparation, None
        if not target.accepted and cancel is not None:
            cancel()
    return targets


async def _cancel_snapshot(targets: tuple[RunTarget, ...]) -> None:
    await runtime.cancel_requests(
        (target.user_id, target.conversation_id, target.request_id)
        for target in targets
        if target.user_id is not None and target.conversation_id and target.request_id
    )


@restricted
async def stop_command(update: Update, context: Context) -> None:
    """Stop the requests already in this chat; preserve later input and context."""
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.answer(PRIVATE_ONLY, parse_mode="MarkdownV2")
        return

    targets = _claim_stop(update.effective_chat.id, update.effective_user.id)
    if not targets:
        await update.message.answer(
            ui.notice("info", "Nothing to stop", "The agent is idle."),
            parse_mode="MarkdownV2",
        )
        return
    await _cancel_snapshot(targets)
    await update.message.answer(
        ui.notice(
            "stopped",
            "Stopped",
            "The current requests ended and the queue is clear. The session is kept.",
        ),
        parse_mode="MarkdownV2",
    )


async def _handle_stop(update: Update, context: Context) -> None:
    """End the active session — subprocess and conversation both go.

    Not to be confused with /stop, which clears requests but keeps the session.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    destroyed = await destroy_session(chat_id)

    if destroyed:
        await query.message.edit_text(
            ui.notice(
                "info", "Session ended", "Your next message starts a new session."
            ),
            parse_mode="MarkdownV2",
        )
    else:
        await query.message.edit_text(NO_SESSION, parse_mode="MarkdownV2")


async def handle_stop_callback(
    update: Update, context: Context, token: str | None = None
) -> None:
    """An active response button authorizes one chat-wide cancellation snapshot."""
    query = update.callback_query
    if token is None:
        prefix = "agent:stop_run:"
        data = query.data or ""
        token = data[len(prefix) :] if data.startswith(prefix) else ""
    target = active_run(token, update.effective_chat.id, update.effective_user.id)
    message_id = getattr(query.message, "message_id", None)
    if target is None or message_id not in target.message_ids:
        await query.answer("This response has already finished.")
        return
    targets = _claim_stop(target.chat_id, target.user_id)
    await _cancel_snapshot(targets)
    await query.answer("Stopped the current requests and cleared the queue.")


async def _handle_close(update: Update, context: Context) -> None:
    """Close the agent menu (keep session alive if running)."""
    query = update.callback_query

    await query.message.delete()
