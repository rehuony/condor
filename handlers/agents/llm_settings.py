"""Settings screen and active LLM selection."""

import logging

from condor.telegram.types import (
    Context,
    Update,
)
from utils import telegram_ui as ui

from ._shared import (
    AGENT_OPTIONS,
)
from .availability import _reclaim_default_agent, set_active_llm
from .session import destroy_session

log = logging.getLogger(__name__)


async def _probe_llm_readiness() -> dict:
    """Which providers this machine can run right now, or ``{}`` if unaskable.

    The same probe ``condor doctor``, the setup wizard and the dashboard's
    picker make, so no surface can disagree with another about what is
    runnable. A probe that fails marks nothing rather than blanking the menu.
    """
    from condor.llm import readiness

    try:
        return await readiness.probe_all([readiness.base_of(k) for k in AGENT_OPTIONS])
    except Exception:  # noqa: BLE001 - a menu that cannot probe still opens
        log.warning("LLM readiness probe failed", exc_info=True)
        return {}


async def _handle_settings(update: Update, context: Context) -> None:
    """Show settings sub-menu with LLM picker."""
    from condor.preferences import secret_notices_enabled

    from .menu import _settings_keyboard

    query = update.callback_query
    current_llm = _reclaim_default_agent(context)
    await query.message.edit_text(
        ui.screen(
            ui.title("⚙️", "LLM Settings"), ui.hint("Select the LLM for new sessions.")
        ),
        parse_mode="MarkdownV2",
        reply_markup=_settings_keyboard(
            current_llm,
            secret_notices_enabled(context.user_data),
            await _probe_llm_readiness(),
        ),
    )


async def _handle_set_llm(update: Update, context: Context, llm_key: str) -> None:
    """Update the preferred LLM."""
    query = update.callback_query
    if llm_key not in AGENT_OPTIONS:
        await query.message.edit_text(ui.STALE_BUTTON, parse_mode="MarkdownV2")
        return

    set_active_llm(context, llm_key)
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    chat_id = update.effective_chat.id
    await destroy_session(chat_id)

    label = AGENT_OPTIONS[llm_key]["label"]
    # Said at the pick, not at the next message: the session spawns on whatever
    # the user types next, and a model that cannot start would fail there with
    # the reason buried in an error. Not a refusal — someone who is about to
    # run `ollama serve` is entitled to select it first.
    from condor.llm import readiness

    state = (await _probe_llm_readiness()).get(readiness.base_of(llm_key))
    warning = (
        ui.notice("warning", "Not usable yet", state.detail)
        if state is not None and not state.usable
        else None
    )
    await query.message.edit_text(
        ui.screen(
            ui.notice(
                "success",
                f"LLM set to {label}",
                "New sessions will use this model.",
            ),
            warning,
            ui.hint("Use /agent to continue."),
        ),
        parse_mode="MarkdownV2",
    )
