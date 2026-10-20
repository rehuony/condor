"""Stop, cancel and close the agent session."""

import logging

import condor.runtime.client as runtime
from condor.telegram.types import (
    Context,
    Update,
)
from utils.auth import restricted

from .session import _tg_key, destroy_session, get_session

log = logging.getLogger(__name__)


@restricted
async def stop_command(update: Update, context: Context) -> None:
    """Handle /stop — interrupt the answer in flight, keep the session.

    This is the dashboard's Stop button, as a command: it aborts the running
    turn at the agent (ACP ``session/cancel``) and leaves the session and its
    context intact. Deliberately *not* what the menu's "End session" button
    does, which kills the subprocess and the conversation with it.

    A message queued behind the aborted turn still runs — stopping the answer
    you are watching is not the same as discarding what you asked next.
    """
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.answer("The agent is only available in private chats.")
        return

    chat_id = update.effective_chat.id
    from condor.telegram.ingress import get_ingress

    preparing = get_ingress().cancel(chat_id)
    session = await get_session(chat_id)
    if preparing and (not session or not session.is_busy):
        await update.message.answer(
            "⏹ Stopped reading attachments. The session is kept."
        )
        return

    if not session or not session.alive:
        await update.message.answer("No active session.")
        return

    if not session.is_busy:
        await update.message.answer("Nothing to stop — the agent is idle.")
        return

    # Awaits the agent's own cancel (bounded by TIMEOUTS.prompt_cancel), so by
    # the time this replies the generation has actually stopped. The streamed
    # message finalizes itself on the DONE/cancelled event.
    await runtime.abort(_tg_key(chat_id))
    await update.message.answer("⏹ Stopped. The session and its context are kept.")


async def _handle_stop(update: Update, context: Context) -> None:
    """End the active session — subprocess and conversation both go.

    Not to be confused with /stop, which only interrupts the turn in flight.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    destroyed = await destroy_session(chat_id)

    if destroyed:
        await query.message.edit_text("Agent session ended.")
    else:
        await query.message.edit_text("No active session.")


async def handle_stopped_generation(update: Update, context: Context) -> None:
    """Native composer stop: abort the turn that owns this ``draft_id``.

    Stale or repeated events (a draft from a finished turn, a second tap) do
    not touch a later request: lookup is ``(chat, draft_id)`` and a missing
    or already-stopped target is ignored.
    """
    from condor.telegram.output.runs import run_for_draft, unregister_run

    event = update.stopped_message_generation
    if event is None:
        return
    target = run_for_draft(event.chat.id, event.draft_id)
    if target is None or target.stopped:
        return
    target.stopped = True
    unregister_run(target)
    if target.user_id is None or not target.conversation_id or not target.request_id:
        return
    await runtime.abort_turn(target.user_id, target.conversation_id, target.request_id)


async def _handle_close(update: Update, context: Context) -> None:
    """Close the agent menu (keep session alive if running)."""
    query = update.callback_query

    await query.message.delete()
