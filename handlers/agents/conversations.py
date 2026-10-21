"""Conversation list, switching and deletion."""

import logging

from condor.telegram.types import (
    Context,
    Update,
)

from ._shared import (
    bind_chat_to_agent,
    find_conversation,
    forget_chat_conversation,
    stale_binding_notice,
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


async def _handle_conversations(
    update: Update, context: Context, page: int = 0, notice: str = ""
) -> None:
    """Show the picker of conversations this chat can come back to.

    The store is one keyspace across surfaces, so this lists chats held on the
    dashboard exactly like chats held here (ARCH-102). Read fresh on every
    render and never cached in ``user_data``: the same list is being written by
    the other surface, and a stale snapshot is what would make a tap land on the
    wrong conversation.
    """
    from condor.runtime import conversations

    from .menu import _conversations_keyboard

    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    metas = conversations.list_conversations(user_id)
    if not metas:
        await query.message.edit_text(
            f"{notice}No conversations yet. Send a message and this chat becomes one.",
            reply_markup=_conversations_keyboard([], 0, ""),
        )
        return

    # Marks the row this chat is in right now, which is the one question the
    # list cannot answer on its own — every other surface's session is in here.
    current = await _chat_conversation_id(chat_id, context.user_data)
    await query.message.edit_text(
        f"{notice}Your conversations, newest first — including the ones you held "
        "on the dashboard.\n\nPick one to continue it here, or 🗑 to forget it.",
        reply_markup=_conversations_keyboard(metas, page, current),
    )


async def _handle_conversation_pick(
    update: Update, context: Context, token: str
) -> None:
    """Continue an existing conversation in this chat.

    The whole resume is one argument: ``conversation_id`` on the spawn, and the
    runtime replays the transcript into the new session's opening context
    (ARCH-101). What is left to decide here is *who* picks it up — the agent the
    conversation was held with, so that resuming reads as returning to that
    chat rather than as handing its history to whoever answered last. The chat's
    binding follows, or the next respawn would silently swap the identity back.
    """
    from condor.agents.agent import AgentStore
    from condor.runtime import conversations

    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    meta = find_conversation(conversations.list_conversations(user_id), token)
    if meta is None:
        await _handle_conversations(
            update, context, notice="That conversation is no longer there.\n\n"
        )
        return

    session = await get_session(chat_id)
    if session and session.alive and session.conversation_id == meta.id:
        await query.message.edit_text(
            "This chat is already in that conversation.\n\nSend a message to continue."
        )
        return

    agent = AgentStore().get(meta.agent_slug) if meta.agent_slug else None
    notice = ""
    if meta.agent_slug and agent is None:
        # The transcript outlived the agent that wrote half of it. Condor can
        # still read it, but saying so beats resuming under a silent stand-in.
        notice = f"{stale_binding_notice(meta.agent_slug)}\n\n"
    agent_slug = agent.slug if agent else ""
    label = (agent.name or agent.slug) if agent else "Condor"

    agent_key = _reclaim_default_agent(context)
    _perm_cb = _tg_permission_callback(context.bot, chat_id, user_id)

    await query.message.edit_text(f"{notice}Resuming with {label}...")
    await destroy_session(chat_id)

    try:
        info = await _create_tg_session(
            chat_id=chat_id,
            # A bound Agent brings its own configured model, exactly as it does
            # when the binding is made in the "Talk To" picker.
            agent_key="" if agent_slug else agent_key,
            user_id=user_id,
            permission_callback=_perm_cb,
            user_data=context.user_data,
            agent_slug=agent_slug,
            conversation_id=meta.id,
        )
    except Exception as e:
        log.exception("Failed to resume conversation %r", meta.id)
        await query.message.edit_text(f"Could not resume that conversation: {e}")
        return

    # Only once the session stands up — a chat must never be left pointing at an
    # agent it could not spawn.
    bind_chat_to_agent(context.user_data, agent_slug)

    if info.conversation_id == meta.id:
        headline = f"Resumed with {label}: {meta.title or 'untitled'}"
        body = f"{label} has read the conversation so far."
    else:
        # Deleted between the listing and the spawn: the runtime falls through
        # to a new conversation rather than stranding the chat, so say which one
        # the user actually got.
        headline = f"That conversation is gone. Started a fresh one with {label}."
        body = "Nothing was carried over."

    await query.message.edit_text(
        f"{headline}\n\n{body}\n\nSend a message to continue."
    )


async def _handle_conversation_delete_confirm(
    update: Update, context: Context, token: str
) -> None:
    """Ask before forgetting a transcript — it goes for every surface at once."""
    from condor.runtime import conversations

    from .menu import _ago, _conversation_delete_keyboard

    query = update.callback_query
    user_id = update.effective_user.id

    meta = find_conversation(conversations.list_conversations(user_id), token)
    if meta is None:
        await _handle_conversations(
            update, context, notice="That conversation is no longer there.\n\n"
        )
        return

    await query.message.edit_text(
        f"Delete this conversation?\n\n" f"{meta.title or 'Untitled'}\n"
        # Same three facts in the same order as the row that was tapped, so the
        # confirmation is recognisably about *that* one.
        f"{_ago(meta.updated_at)} · {meta.agent_slug or 'Condor'} · "
        f"{meta.turn_count} turns\n\n"
        "The transcript goes for the dashboard too, and cannot be recovered.",
        reply_markup=_conversation_delete_keyboard(meta),
    )


async def _handle_conversation_delete(
    update: Update, context: Context, token: str
) -> None:
    """Forget a conversation, and let go of it if this chat was in it.

    Mirrors the dashboard's DELETE, which also reaps whatever session is still
    answering the conversation: leaving the chat pointing at a deleted id would
    make its next message spawn onto a transcript that no longer exists.
    """
    from condor.runtime import conversations

    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    meta = find_conversation(conversations.list_conversations(user_id), token)
    if meta is None:
        await _handle_conversations(
            update, context, notice="That conversation was already gone.\n\n"
        )
        return

    was_current = await _chat_conversation_id(chat_id, context.user_data) == meta.id
    deleted = conversations.delete_conversation(user_id, meta.id)
    if not deleted:
        await query.message.edit_text("Could not delete that conversation.")
        return

    if was_current:
        await destroy_session(chat_id)
        forget_chat_conversation(context.user_data)

    notice = f"Deleted: {meta.title or 'untitled'}"
    if was_current:
        notice += ". This chat starts fresh on your next message"
    await _handle_conversations(update, context, notice=f"{notice}.\n\n")
