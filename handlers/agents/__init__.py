"""Telegram agent chat: sessions, settings, text input and the agent:* callback router.

Each feature lives in its own module; this package wires the Telegram handlers
and re-exports the names the rest of the application and the tests use.
"""

import logging
from functools import partial

import condor.runtime.client as runtime
from condor.acp.pydantic_ai_client import is_pydantic_ai_model
from condor.telegram.approvals import parse_approval, resolve_confirmation
from condor.telegram.types import Context, Update
from handlers import clear_all_input_states
from handlers._routing import HandlerFunc, PrefixRoute, dispatch_action, noop
from utils.auth import restricted

from ._shared import (
    COMPACT_PROMPT_AUTO,
    remember_chat_conversation,
    resolve_chat_binding,
    selectable_agent_options,
)
from .agent_arg import (
    _ARG_LIST_LIMIT,
    _CONDOR_ALIASES,
    _handle_agent_argument,
    _normalize_agent_query,
    _resolve_agent_argument,
)
from .availability import (
    _cli_available_cache,
    _is_agent_available,
    _reclaim_default_agent,
    _report_agent_unavailable,
    _try_telegram_message,
    set_active_llm,
)
from .compact import (
    _compact,
    _do_compact_from_message,
    _edit_render,
    _handle_compact,
    _handle_compact_custom_prompt,
    _handle_compact_menu,
    _reply_render,
)
from .conversations import (
    _handle_conversation_delete,
    _handle_conversation_delete_confirm,
    _handle_conversation_pick,
    _handle_conversations,
)
from .custom_endpoints import (
    _CUSTOM_INPUT_KEYS,
    CUSTOM_MODEL_CACHE_TTL,
    _cache_models,
    _cached_models,
    _clear_custom_input,
    _current_custom_model,
    _handle_custom_cancel,
    _handle_custom_clear_filter,
    _handle_custom_delete,
    _handle_custom_delete_confirm,
    _handle_custom_list,
    _handle_custom_manage,
    _handle_custom_no_key,
    _handle_custom_page,
    _handle_custom_pick,
    _handle_custom_rekey,
    _handle_custom_retry,
    _handle_custom_search_prompt,
    _handle_custom_url_prompt,
    _handle_custom_use,
    _provider_named,
    _resolve_custom_key,
    _resolve_custom_search,
    _resolve_custom_url,
    _show,
    _validate_and_save,
)
from .input import (
    _ARMED_TEXT_INPUT_KEYS,
    _consume_agent_text_input,
    _disarm_text_input,
    _process_agent_input,
    _stream_agent_turn,
    _voice_display,
    agent_message_handler,
)
from .llm_settings import (
    _handle_set_llm,
    _handle_settings,
    _probe_llm_readiness,
)
from .menu import show_agent_menu
from .openrouter import (
    _handle_openrouter_pick,
    _handle_openrouter_picker,
    _handle_openrouter_type_cancel,
    _handle_openrouter_type_confirm,
    _handle_openrouter_type_prompt,
    _resolve_openrouter_typed_slug,
)
from .secrets import (
    _AMBIGUOUS_NOTICE,
    _AMBIGUOUS_TAIL,
    _CERTAIN_NOTICE,
    _SECRET_SEEN_KEY,
    _handle_pasted_secrets,
    _notice_input_secrets,
)
from .session import (
    _chat_conversation_id,
    _create_tg_session,
    _ensure_chat_session,
    _handle_new_session,
    _handle_start,
    _tg_key,
    _tg_permission_callback,
    destroy_session,
    get_session,
)
from .stop import (
    _handle_close,
    _handle_stop,
    handle_stopped_generation,
    stop_command,
)
from .stream import TelegramStreamer
from .talk_to import (
    _handle_talk_pick,
    _handle_talk_to,
    _raise_agent,
)

log = logging.getLogger(__name__)


@restricted
async def agent_command(update: Update, context: Context) -> None:
    """Handle /agent command — manage agent settings and session.

    With an argument it names who to talk to and skips the menu entirely; with
    none it opens the menu as it always has.
    """
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.answer("The agent is only available in private chats.")
        return

    clear_all_input_states(context)

    agent_key = _reclaim_default_agent(context)

    # Warn if no agent CLI is available
    if not _is_agent_available(agent_key):
        # Sentinels are excluded: they open a picker rather than naming a
        # model, so auto-switching onto one would park the user on a key that
        # can't start a session.
        available = [k for k in selectable_agent_options() if _is_agent_available(k)]
        if not available:
            await update.message.answer(
                "No agent CLI found.\n\n"
                "Install one of:\n"
                "• claude-agent-acp (Claude Agent)\n"
                "• gemini (Gemini CLI)\n"
                "• npx @agentclientprotocol/codex-acp (ChatGPT Codex ACP bridge)\n\n"
                "Then restart the bot."
            )
            return
        # A temporary fallback is not a new shared preference. Remember which
        # choice to restore once its CLI is installed.
        context.user_data["agent_llm"] = available[0]
        context.user_data["agent_llm_auto"] = agent_key

    # Checked after the CLI fallback above, so a named agent is raised on a
    # model that can actually start rather than refused for the default's sake.
    if context.args:
        await _handle_agent_argument(update, context, " ".join(context.args))
        return

    await show_agent_menu(update, context)


async def _secret_notices_off(update: Update, context: Context) -> None:
    """The button on the ambiguous-shape notice (FEAT-056).

    Nothing about the certain shapes is silenceable — those are removed
    whatever this says.
    """
    from condor.preferences import set_secret_notices

    set_secret_notices(context.user_data, False)
    await update.callback_query.message.edit_text(
        "I will not flag key-shaped values again. It is back on from "
        "/agent → Settings whenever you want it."
    )


async def _secret_notices_toggle(update: Update, context: Context) -> None:
    from condor.preferences import secret_notices_enabled, set_secret_notices

    set_secret_notices(context.user_data, not secret_notices_enabled(context.user_data))
    await _handle_settings(update, context)


async def _set_llm(update: Update, context: Context, llm_key: str) -> None:
    """A model key, or a sentinel that opens a picker instead."""
    if llm_key == "openrouter:":
        await _handle_openrouter_picker(update, context, page=0)
    elif llm_key == "custom:":
        await _handle_custom_list(update, context)
    else:
        await _handle_set_llm(update, context, llm_key)


# ============================================
# ROUTING TABLES
# ============================================


#: ``agent:<action>`` callbacks that take no parameter.
SIMPLE_ACTIONS: dict[str, HandlerFunc] = {
    "start": _handle_start,
    "secret_notices_off": _secret_notices_off,
    "secret_notices_toggle": _secret_notices_toggle,
    # Settings
    "settings": _handle_settings,
    "or_type": _handle_openrouter_type_prompt,
    "or_type_confirm": _handle_openrouter_type_confirm,
    "or_type_cancel": _handle_openrouter_type_cancel,
    "or_noop": noop,
    # Custom OpenAI-compatible endpoints
    "cu_list": _handle_custom_list,
    "cu_add": _handle_custom_url_prompt,
    "cu_nokey": _handle_custom_no_key,
    "cu_retry": _handle_custom_retry,
    "cu_cancel": _handle_custom_cancel,
    "cu_manage": _handle_custom_manage,
    "cu_search": _handle_custom_search_prompt,
    "cu_clear": _handle_custom_clear_filter,
    "cu_noop": noop,
    # Session management
    "stop": _handle_stop,
    "close": _handle_close,
    "menu": show_agent_menu,
    "compact": _handle_compact_menu,
    "compact_auto": partial(_handle_compact, custom_instructions=None),
    "compact_custom": _handle_compact_custom_prompt,
    "new": _handle_new_session,
    # Agent binding
    "talk_to": partial(_handle_talk_to, page=0),
    "talk_noop": noop,
    # Conversations
    "conv_list": partial(_handle_conversations, page=0),
    "conv_noop": noop,
}

#: ``agent:<prefix><value>`` callbacks: the text after the prefix is parsed
#: and passed as the handler's third argument. Order matters where one prefix
#: starts with another ("conv_delok:" before "conv_del:").
PREFIX_ACTIONS: tuple[PrefixRoute, ...] = (
    # Buttons rendered before the persona axis was deleted (FEAT-033) send
    # "mode:<value>"; the value is ignored and a session starts.
    ("mode:", lambda u, c, _value: _handle_start(u, c), str),
    ("set_llm:", _set_llm, str),
    ("or_page:", lambda u, c, page: _handle_openrouter_picker(u, c, page=page), int),
    ("or_pick:", _handle_openrouter_pick, int),
    ("cu_use:", _handle_custom_use, str),
    ("cu_page:", _handle_custom_page, int),
    ("cu_pick:", _handle_custom_pick, str),
    ("cu_key:", _handle_custom_rekey, str),
    ("cu_delok:", _handle_custom_delete, str),
    ("cu_del:", _handle_custom_delete_confirm, str),
    ("talk_page:", lambda u, c, page: _handle_talk_to(u, c, page=page), int),
    ("talk_pick:", _handle_talk_pick, int),
    ("conv_page:", lambda u, c, page: _handle_conversations(u, c, page=page), int),
    ("conv_pick:", _handle_conversation_pick, str),
    ("conv_delok:", _handle_conversation_delete, str),
    ("conv_del:", _handle_conversation_delete_confirm, str),
)


@restricted
async def agent_callback_handler(update: Update, context: Context) -> None:
    """Route agent:* callbacks."""
    query = update.callback_query
    await query.answer()

    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await query.message.edit_text("Agent mode is only available in private chats.")
        return

    # A tap supersedes any half-finished typing prompt. Cleared before dispatch
    # so handlers that want an armed mode can re-arm it below.
    _disarm_text_input(context)

    data = query.data
    action = data.split(":", 1)[1] if ":" in data else data

    if await dispatch_action(update, context, action, SIMPLE_ACTIONS, PREFIX_ACTIONS):
        return

    # Trade confirmations. "Request expired" also covers "already answered
    # elsewhere" — resolve() is idempotent, so a dashboard click that beat this
    # tap is not an error.
    if (approval := parse_approval(action)) is not None:
        request_id, approved = approval
        resolved = await resolve_confirmation(
            request_id, approved=approved, by_user_id=update.effective_user.id
        )
        if not resolved:
            text = "Request expired."
        else:
            text = "Approved." if approved else "Rejected."
        await query.message.edit_text(text)


__all__ = [
    "runtime",
    "COMPACT_PROMPT_AUTO",
    "TelegramStreamer",
    "is_pydantic_ai_model",
    "remember_chat_conversation",
    "resolve_chat_binding",
    "show_agent_menu",
    "CUSTOM_MODEL_CACHE_TTL",
    "_AMBIGUOUS_NOTICE",
    "_AMBIGUOUS_TAIL",
    "_ARG_LIST_LIMIT",
    "_ARMED_TEXT_INPUT_KEYS",
    "_CERTAIN_NOTICE",
    "_CONDOR_ALIASES",
    "_CUSTOM_INPUT_KEYS",
    "_SECRET_SEEN_KEY",
    "_cache_models",
    "_cached_models",
    "_chat_conversation_id",
    "_clear_custom_input",
    "_cli_available_cache",
    "_compact",
    "_consume_agent_text_input",
    "_create_tg_session",
    "_current_custom_model",
    "_disarm_text_input",
    "_do_compact_from_message",
    "_edit_render",
    "_ensure_chat_session",
    "_handle_agent_argument",
    "handle_stopped_generation",
    "_handle_close",
    "_handle_compact",
    "_handle_compact_custom_prompt",
    "_handle_compact_menu",
    "_handle_conversation_delete",
    "_handle_conversation_delete_confirm",
    "_handle_conversation_pick",
    "_handle_conversations",
    "_handle_custom_cancel",
    "_handle_custom_clear_filter",
    "_handle_custom_delete",
    "_handle_custom_delete_confirm",
    "_handle_custom_list",
    "_handle_custom_manage",
    "_handle_custom_no_key",
    "_handle_custom_page",
    "_handle_custom_pick",
    "_handle_custom_rekey",
    "_handle_custom_retry",
    "_handle_custom_search_prompt",
    "_handle_custom_url_prompt",
    "_handle_custom_use",
    "_handle_new_session",
    "_handle_openrouter_pick",
    "_handle_openrouter_picker",
    "_handle_openrouter_type_cancel",
    "_handle_openrouter_type_confirm",
    "_handle_openrouter_type_prompt",
    "_handle_pasted_secrets",
    "_handle_set_llm",
    "_handle_settings",
    "_handle_start",
    "_handle_stop",
    "_handle_talk_pick",
    "_handle_talk_to",
    "_is_agent_available",
    "_normalize_agent_query",
    "_notice_input_secrets",
    "_probe_llm_readiness",
    "_process_agent_input",
    "_provider_named",
    "_raise_agent",
    "_reclaim_default_agent",
    "_reply_render",
    "_report_agent_unavailable",
    "_resolve_agent_argument",
    "_resolve_custom_key",
    "_resolve_custom_search",
    "_resolve_custom_url",
    "_resolve_openrouter_typed_slug",
    "_show",
    "_stream_agent_turn",
    "_tg_key",
    "_tg_permission_callback",
    "_try_telegram_message",
    "_validate_and_save",
    "_voice_display",
    "agent_callback_handler",
    "agent_command",
    "agent_message_handler",
    "destroy_session",
    "get_session",
    "set_active_llm",
    "stop_command",
]
