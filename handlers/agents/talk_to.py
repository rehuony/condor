"""Agent binding: which agent is on the other end of the chat."""

import logging

from condor.telegram.types import (
    Context,
    Update,
)
from utils import telegram_ui as ui

from ._shared import (
    bind_chat_to_agent,
    resolve_chat_binding,
)
from .availability import _reclaim_default_agent
from .session import (
    _chat_conversation_id,
    _create_tg_session,
    _tg_permission_callback,
    destroy_session,
    get_session,
)

log = logging.getLogger(__name__)


async def _handle_talk_to(update: Update, context: Context, page: int = 0) -> None:
    """Show the picker of domain Agents this chat can talk to."""
    from condor.agents.agent import AgentStore

    from .menu import _talk_to_keyboard

    query = update.callback_query
    session = await get_session(update.effective_chat.id)
    # Between respawns there is no session, but the chat is still bound -- tick
    # what it will come back as, not what happens to be running.
    bound, _ = resolve_chat_binding(context.user_data, drop_stale=False)
    current_slug = session.agent_slug if session else (bound.slug if bound else "")
    # The coordinator is the keyboard's own first row, so listing it here too
    # would offer Condor twice (FEAT-033 put it in the registry).
    agents = AgentStore().list_specialists()

    if not agents:
        await query.message.edit_text(
            ui.screen(
                ui.title("👤", "Talk To"),
                ui.esc(
                    "No agents defined yet. Create one with /agent → Condor, "
                    "or from the dashboard's Agents page."
                ),
            ),
            parse_mode="MarkdownV2",
            reply_markup=_talk_to_keyboard([], 0, current_slug),
        )
        return

    # Indices are resolved against this snapshot, so the pick handler must
    # re-read the same list (AgentStore.list_specialists is directory order).
    await query.message.edit_text(
        ui.screen(
            ui.title("👤", "Talk To"),
            ui.esc(
                "A domain agent answers with its own identity, tools and memory. "
                "Condor is the general coordinator."
            ),
            ui.hint("Who do you want to talk to?"),
        ),
        parse_mode="MarkdownV2",
        reply_markup=_talk_to_keyboard(agents, page, current_slug),
    )


async def _handle_talk_pick(update: Update, context: Context, index: int) -> None:
    """Bind (or unbind) the chat's session to the Agent at ``index``.

    The index is a position in the list the keyboard was built from, so this
    re-reads that same list; the work of actually standing the agent up is
    :func:`_raise_agent`, which ``/agent <name>`` reaches by another door.
    """
    from condor.agents.agent import AgentStore

    query = update.callback_query

    agent_slug = ""
    label = "Condor"
    if index >= 0:
        # Same list the keyboard was built from — the indices in callback_data
        # are positions in it, so the two must not diverge.
        agents = AgentStore().list_specialists()
        if index >= len(agents):
            await query.message.edit_text(
                ui.notice(
                    "warning",
                    "Agent not found",
                    "It may have been deleted. " "Open Talk To again.",
                ),
                parse_mode="MarkdownV2",
            )
            return
        agent_slug = agents[index].slug
        label = agents[index].name or agent_slug

    await _raise_agent(update, context, agent_slug, label, status=query.message)


async def _raise_agent(
    update: Update,
    context: Context,
    agent_slug: str,
    label: str,
    status=None,
) -> None:
    """Put this chat in front of ``agent_slug``, spawning it if need be.

    ACP cannot hot-swap an identity, so this reaps the subprocess and spawns a
    new one under the same key. The conversation is carried across that reap and
    the handover is marked in the transcript, so switching happens *inside* one
    chat — the same semantics the dashboard's ``switch`` action already has.

    With nothing running the same call *raises* the specialist instead, which is
    what makes "start this chat as the funding desk" expressible at all: the
    picker used to hang off a live session, so the only way to reach a
    specialist was to boot the coordinator and immediately switch away from it.

    ``status`` is the message that narrates the swap. The picker owns one
    already — the menu it was tapped from, edited in place — while a command has
    none yet and lets this reply with one, so the first thing on screen is the
    verb rather than a placeholder.
    """
    from condor.runtime import conversations

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    agent_key = _reclaim_default_agent(context)
    bot = context.bot

    _perm_cb = _tg_permission_callback(bot, chat_id, user_id)

    # Both read before the teardown — afterwards there is no session left to ask
    # either what conversation it was in or whether it was ever there.
    conversation_id = await _chat_conversation_id(chat_id, context.user_data)
    session = await get_session(chat_id)
    switching = bool(session and session.alive)
    verb = "Switching to" if switching else "Starting"

    waiting = "⏳ " + ui.esc(f"{verb} {label}…")
    if status is None:
        status = await update.message.answer(waiting, parse_mode="MarkdownV2")
    else:
        await status.edit_text(waiting, parse_mode="MarkdownV2")
    await destroy_session(chat_id)

    try:
        info = await _create_tg_session(
            chat_id=chat_id,
            agent_key="" if agent_slug else agent_key,
            user_id=user_id,
            permission_callback=_perm_cb,
            user_data=context.user_data,
            agent_slug=agent_slug,
            conversation_id=conversation_id,
        )
    except Exception as e:
        log.exception("Failed to bind session to agent %r", agent_slug)
        await status.edit_text(
            ui.notice(
                "error",
                f"Could not {'switch to' if switching else 'start'} {label}",
                str(e),
            ),
            parse_mode="MarkdownV2",
        )
        return

    # Only once the session actually stands up: a chat must never be left
    # pointing at an agent it could not spawn.
    bind_chat_to_agent(context.user_data, agent_slug)

    # The handover, marked in the transcript rather than only in the header, so
    # the resumed agent reads a divider between the two identities' turns.
    # Recorded after the respawn: a switch that failed to spawn leaves no
    # divider claiming it happened.
    carried = bool(conversation_id) and info.conversation_id == conversation_id
    if carried:
        conversations.record_system(
            user_id, conversation_id, f"Switched to {label}", kind="switch"
        )

    await status.edit_text(
        ui.notice(
            "success",
            f"Now talking to {label}",
            ("The conversation so far is carried over. " if carried else "")
            + "Send a message to continue.",
        ),
        parse_mode="MarkdownV2",
    )
