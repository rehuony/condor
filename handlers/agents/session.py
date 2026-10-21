"""Telegram agent sessions: keys, creation, destruction, start and new-session actions."""

import logging

import condor.runtime.client as runtime
from condor.runtime import (
    SessionInfo,
    SessionKey,
    SessionSpec,
)
from condor.telegram.approvals import (
    permission_callback,
)
from condor.telegram.errors import NetworkFailure
from condor.telegram.types import (
    Context,
    Update,
)

from ._shared import (
    AGENT_OPTIONS,
    forget_chat_conversation,
    remember_chat_conversation,
    resolve_chat_binding,
    stale_binding_notice,
    stored_chat_conversation,
)
from .availability import (
    _is_agent_available,
    _reclaim_default_agent,
    _report_agent_unavailable,
    _try_telegram_message,
)

log = logging.getLogger(__name__)


def _tg_key(chat_id: int) -> SessionKey:
    return SessionKey.telegram(chat_id)


def _tg_permission_callback(bot, chat_id: int, user_id: int):
    """Confirmation gate for this chat, delivered as an inline keyboard."""
    return permission_callback(bot, chat_id, user_id, session_key=str(_tg_key(chat_id)))


async def get_session(chat_id: int) -> SessionInfo | None:
    """Serializable view of a chat's session, or None."""
    return await runtime.get_info(_tg_key(chat_id))


async def destroy_session(chat_id: int) -> bool:
    """Destroy a chat's session. Returns True if one existed."""
    return await runtime.destroy(_tg_key(chat_id))


async def _create_tg_session(
    chat_id: int,
    agent_key: str,
    user_id: int,
    permission_callback,
    user_data: dict | None,
    extra_context: str = "",
    agent_slug: str = "",
    conversation_id: str = "",
) -> SessionInfo:
    """Provision the session for a Telegram chat.

    An empty ``agent_key`` with ``agent_slug`` set lets the bound Agent's own
    configured model win; pass both to override it deliberately.

    An empty ``conversation_id`` starts a fresh conversation, which only the
    deliberate new-chat verbs want; the respawn paths pass the one the chat was
    already in. The key ``tg:{chat_id}`` is unchanged either way, so Telegram
    carries the conversation on the session record rather than in the key.

    Whichever conversation the session ends up on is recorded on the chat before
    returning. That single write is what makes every respawn path resumable
    without each of them having to remember to stash anything — including the
    ones that just minted a fresh conversation, which the chat is now in.
    """
    info = await runtime.create_session(
        SessionSpec(
            key=str(_tg_key(chat_id)),
            agent_key=agent_key,
            user_id=user_id,
            chat_id=chat_id,
            platform="telegram",
            extra_context=extra_context,
            agent_slug=agent_slug,
            conversation_id=conversation_id,
        ),
        permission_callback=permission_callback,
        user_data=user_data,
    )
    remember_chat_conversation(user_data, info.conversation_id)
    return info


async def _chat_conversation_id(chat_id: int, user_data: dict | None) -> str:
    """The conversation a respawn of this chat should continue.

    The live session is the authority while it exists — another surface can move
    the key onto a different conversation, and ARCH-089's resolver is the one
    place that knows how to ask. The recorded id is the answer once the
    subprocess is gone, which is precisely when a respawn needs it.
    """
    live = await runtime.conversation_for_session(str(_tg_key(chat_id)))
    return live or stored_chat_conversation(user_data)


async def _handle_start(
    update: Update,
    context: Context,
) -> None:
    """Start a fresh session for this chat, under whoever it is bound to.

    "New session" means a new conversation, not a new interlocutor: a chat bound
    to a specialist restarts as that specialist, but with an empty transcript.
    Use "Talk To → Condor" to go back to the coordinator.
    """
    query = update.callback_query
    message = query.message if query else update.message
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    agent_key = _reclaim_default_agent(context)
    llm_label = AGENT_OPTIONS.get(agent_key, {}).get("label", agent_key)

    agent, stale_slug = resolve_chat_binding(context.user_data)
    label = (agent.name or agent.slug) if agent else "Condor"

    status_text = (
        f"Starting {label} session..."
        if agent
        else f"Starting Condor session ({llm_label})..."
    )
    if stale_slug:
        status_text = f"{stale_binding_notice(stale_slug)}\n\n{status_text}"
    if query:
        await message.edit_text(status_text)
    else:
        message = await message.answer(status_text)

    # Destroy existing session
    await destroy_session(chat_id)
    # Dropped before the spawn, not after: if the spawn fails there is no
    # session and the next message auto-creates one, which must still honour
    # the "new session" the user asked for rather than resuming the old chat.
    forget_chat_conversation(context.user_data)

    try:
        bot = context.bot

        _perm_cb = _tg_permission_callback(bot, chat_id, user_id)

        await _create_tg_session(
            chat_id=chat_id,
            # A bound Agent's own configured model wins, exactly as it does when
            # the binding is first made in the "Talk To" picker.
            agent_key="" if agent else agent_key,
            user_id=user_id,
            permission_callback=_perm_cb,
            user_data=context.user_data,
            agent_slug=agent.slug if agent else "",
        )

        await message.edit_text(
            f"{label} is ready. Send a message to start chatting.\n\n"
            "Use /agent to see options or any other command to exit."
        )
    except Exception as e:
        log.exception("Failed to start agent session")
        await message.edit_text(f"Failed to start agent: {e}")


async def _handle_new_session(update: Update, context: Context) -> None:
    """Destroy the current session and start a fresh one."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = await get_session(chat_id)

    if not session or not session.alive:
        await query.message.edit_text("No active session.")
        return

    await _handle_start(update, context)


async def _ensure_chat_session(update, context, *, on_error=None) -> SessionInfo | None:
    """Reuse or restore this chat's selected conversation and model."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    session = await get_session(chat_id)

    # Auto-create session if none exists (always-on agent)
    if not session or not session.alive:
        # Reclaim the configured default after an auto-switch — same healing the
        # /agent command does, so users who only ever type messages benefit too.
        agent_key = _reclaim_default_agent(context)

        # The subprocess is gone but the chat's choice is not: respawn under the
        # Agent it was bound to, or this silently answers as the coordinator --
        # other identity, other tools, other server, other memory (CORR-090).
        agent, stale_slug = resolve_chat_binding(context.user_data)
        if stale_slug:
            try:
                await update.message.answer(stale_binding_notice(stale_slug))
            except NetworkFailure as exc:
                log.warning(
                    "Telegram delivery failed stage=binding_notice update_id=%s error_type=%s",
                    getattr(update, "update_id", None),
                    type(exc).__name__,
                )

        # Check if the CLI binary is installed before attempting to spawn. A
        # bound Agent runs on its own configured model, so that is the one whose
        # CLI has to be there.
        probe_key = (agent.agent_key or agent_key) if agent else agent_key
        if not _is_agent_available(probe_key):
            if on_error is not None:
                await on_error(
                    f"The AI backend for {probe_key} is not available on this server. "
                    "Choose another model with /agent, or install its command-line backend "
                    "and retry after a minute."
                )
            else:
                await _report_agent_unavailable(update, probe_key)
            return None

        try:
            bot = context.bot

            _perm_cb = _tg_permission_callback(bot, chat_id, user_id)

            session = await _create_tg_session(
                chat_id=chat_id,
                agent_key="" if agent else agent_key,
                user_id=user_id,
                permission_callback=_perm_cb,
                user_data=context.user_data,
                agent_slug=agent.slug if agent else "",
                # The subprocess died, the chat did not: resume the transcript
                # it was in. Without this every restart, health-monitor reap or
                # LRU detach silently hands the user an agent with no memory of
                # the conversation they are in the middle of (ARCH-101).
                conversation_id=stored_chat_conversation(context.user_data),
            )

        except Exception as exc:
            log.warning(
                "Failed to create agent session error_type=%s", type(exc).__name__
            )
            text = "Could not start the agent. Please try again or choose another model with /agent."
            if on_error is not None:
                await on_error(text)
            else:
                await _try_telegram_message(
                    update, "session_start", update.message.answer, text=text
                )
            return None

    return session
