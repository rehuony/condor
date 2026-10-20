"""Which agent key is active, whether its backend is reachable, and how to say so when it is not."""

import logging
import shutil
import time

from condor.acp import ACP_COMMANDS
from condor.acp.pydantic_ai_client import is_pydantic_ai_model
from condor.telegram.errors import TelegramFailure
from condor.telegram.transport import telegram_call
from condor.telegram.types import (
    Context,
)

from ._shared import (
    DEFAULT_AGENT,
)

log = logging.getLogger(__name__)


# Recheck installation changes without probing PATH on every Telegram message.
_cli_available_cache: dict[str, tuple[float, bool]] = {}


def _is_agent_available(agent_key: str) -> bool:
    """Check if the agent backend is available.

    For ACP agents (claude-code, gemini): checks if the CLI binary is in PATH.
    For pydantic-ai agents (ollama:*, openai:*, etc.): always available
    (pydantic-ai handles connection errors at runtime).
    """
    # Pydantic-ai models don't need a CLI binary
    if is_pydantic_ai_model(agent_key):
        return True

    now = time.monotonic()
    cached = _cli_available_cache.get(agent_key)
    if cached is not None and now < cached[0]:
        return cached[1]

    cmd = ACP_COMMANDS.get(agent_key, ACP_COMMANDS.get("claude-code", ""))
    # The command may have flags (e.g. "gemini --experimental-acp"), check the binary
    binary = cmd.split()[0] if cmd else ""
    available = shutil.which(binary) is not None
    _cli_available_cache[agent_key] = (now + 60, available)

    if not available:
        log.warning("Agent CLI %r not found in PATH (agent_key=%s)", binary, agent_key)

    return available


async def _try_telegram_message(update, stage: str, method, **kwargs):
    """A status or notice must not decide whether the AI receives an input."""
    try:
        return await telegram_call(method, **kwargs)
    except TelegramFailure as exc:
        log.warning(
            "Telegram delivery failed stage=%s update_id=%s error_type=%s",
            stage,
            getattr(update, "update_id", None),
            type(exc).__name__,
        )
        return None


async def _report_agent_unavailable(update, agent_key: str) -> None:
    await _try_telegram_message(
        update,
        "agent_unavailable",
        update.message.answer,
        text=(
            f"The AI backend for {agent_key} is not available on this server. "
            "Choose another model with /agent, or install its command-line backend "
            "and retry after a minute."
        ),
    )


def set_active_llm(context: Context, agent_key: str) -> None:
    """Save a model choice for both surfaces and update Telegram's cache."""
    from condor.preferences import set_active_agent_key

    context.user_data["agent_llm"] = agent_key
    try:
        set_active_agent_key(context.user_data, agent_key)
    except Exception:
        log.debug("Could not save the preferred model", exc_info=True)


def _reclaim_default_agent(context: Context) -> str:
    """Read the shared preference, keeping a temporary CLI fallback local to TG."""
    from condor.preferences import get_agent_prefs

    cached = context.user_data.get("agent_llm")
    saved = get_agent_prefs(context.user_data).get("active_agent_key")
    fallback_for = context.user_data.get("agent_llm_auto")
    if fallback_for is True:
        # Older pickles mirrored the automatic fallback as if it were a choice.
        # Preserve their recovery behavior unless Web has since picked a model.
        fallback_for = DEFAULT_AGENT
        if not saved or saved == cached:
            saved = DEFAULT_AGENT
            set_active_llm(context, saved)
    if not saved:
        saved = cached or DEFAULT_AGENT
        set_active_llm(context, saved)
    if (
        fallback_for == saved
        and cached
        and cached != saved
        and not _is_agent_available(saved)
    ):
        context.user_data["agent_llm"] = cached
        context.user_data["agent_llm_auto"] = saved
        return cached
    context.user_data.pop("agent_llm_auto", None)
    context.user_data["agent_llm"] = saved
    return saved
