"""OpenRouter model picker and typed-slug flow."""

import logging

from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils import telegram_ui as ui

from .availability import _reclaim_default_agent, set_active_llm
from .session import destroy_session

log = logging.getLogger(__name__)

_EXPIRED = ui.notice("stale", "Selection expired", "Reopen the picker via Change LLM.")
_CANCELLED = ui.notice("cancelled", "Cancelled", "Use /agent to continue.")


def _model_rows(model) -> list[str]:
    """Slug and pricing as bullet rows, for the confirm and result screens."""
    rows = [ui.bullet("Slug", model.slug, code_value=True)]
    if model.prompt_price or model.completion_price:
        rows.append(
            ui.bullet(
                "Pricing",
                f"${model.prompt_price:.2f}/M input, "
                f"${model.completion_price:.2f}/M output",
            )
        )
    return rows


async def _handle_openrouter_picker(
    update: Update, context: Context, page: int
) -> None:
    """Show paginated OpenRouter model picker.

    Fetches the model list (cached for 1h), filters to tool-calling models, and
    stores the resolved list in user_data so or_pick:N can resolve the index.
    """
    from condor.llm.openrouter_models import fetch_models

    from .menu import _openrouter_picker_keyboard

    query = update.callback_query

    # The catalog is public, so browse it without a key — same as the web picker
    # (condor/web/routes/chat_ws.py). The key is only needed to RUN a model; that
    # is enforced with a clear error at session start (pydantic_ai_client.py).
    if page == 0:
        await query.message.edit_text(
            "⏳ " + ui.esc("Loading OpenRouter models…"), parse_mode="MarkdownV2"
        )

    try:
        models = await fetch_models()
    except Exception as e:
        log.exception("Failed to fetch OpenRouter models")
        await query.message.edit_text(
            ui.notice("error", "Could not load OpenRouter models", str(e)),
            parse_mode="MarkdownV2",
        )
        return

    if not models:
        await query.message.edit_text(
            ui.notice(
                "warning",
                "No OpenRouter models available",
                "Check your network or try again later.",
            ),
            parse_mode="MarkdownV2",
        )
        return

    # Cache for or_pick:N to resolve. Models list is sorted deterministically and
    # cached for an hour, so the index is stable across paging within a session.
    context.user_data["_openrouter_models"] = models

    current = _reclaim_default_agent(context)
    current_slug = (
        current.split(":", 1)[1]
        if current.startswith("openrouter:") and current != "openrouter:"
        else None
    )

    keyboard = _openrouter_picker_keyboard(models, page=page, current_slug=current_slug)
    await query.message.edit_text(
        ui.screen(
            ui.title("🧭", "OpenRouter", f"{len(models)} models with tool calling"),
            ui.hint("Select the model for new sessions."),
        ),
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )


async def _handle_openrouter_pick(update: Update, context: Context, idx: int) -> None:
    """Resolve picker index → set agent_llm to 'openrouter:<slug>'."""
    query = update.callback_query
    models = context.user_data.get("_openrouter_models") or []
    if not models or idx < 0 or idx >= len(models):
        await query.message.edit_text(_EXPIRED, parse_mode="MarkdownV2")
        return

    model = models[idx]
    agent_key = f"openrouter:{model.slug}"
    set_active_llm(context, agent_key)
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    await destroy_session(update.effective_chat.id)

    await query.message.edit_text(
        ui.screen(
            f"✅ {ui.bold(f'LLM set to OpenRouter — {model.name}')}",
            _model_rows(model),
            ui.hint("New sessions will use this model. Use /agent to continue."),
        ),
        parse_mode="MarkdownV2",
    )


async def _handle_openrouter_type_prompt(update: Update, context: Context) -> None:
    """Arm slug-input mode: next text message is parsed as an OpenRouter slug."""
    from .menu import _openrouter_input_keyboard

    query = update.callback_query
    context.user_data["_openrouter_typing_slug"] = True
    await query.message.edit_text(
        ui.prompt(
            "Enter OpenRouter model",
            "Send the exact model slug as a message.",
            "anthropic/claude-sonnet-4.5",
        ),
        parse_mode="MarkdownV2",
        reply_markup=_openrouter_input_keyboard(),
    )


async def _resolve_openrouter_typed_slug(
    update: Update, context: Context, text: str
) -> None:
    """Validate a typed slug; on match, prompt confirmation."""
    from condor.llm.openrouter_models import fetch_models, find_model_by_slug

    from .menu import _openrouter_input_keyboard

    slug = text.strip()
    if slug.lower() == "cancel":
        await update.message.answer(_CANCELLED, parse_mode="MarkdownV2")
        return

    try:
        models = await fetch_models()
    except Exception as e:
        log.exception("Failed to fetch OpenRouter models")
        await update.message.answer(
            ui.notice("error", "Could not load OpenRouter models", str(e)),
            parse_mode="MarkdownV2",
        )
        return

    model = find_model_by_slug(models, slug)
    if not model:
        # Re-arm so the user can retype without hunting for the button again —
        # with a Cancel button, so a mistyped slug can't trap them in a loop of
        # "that isn't a model either" with no visible way out.
        context.user_data["_openrouter_typing_slug"] = True
        await update.message.answer(
            ui.screen(
                ui.notice(
                    "warning",
                    "No matching model",
                    f"No tool-calling OpenRouter model matches '{slug}'. The slug "
                    "must be exact, for example anthropic/claude-sonnet-4.5.",
                ),
                ui.CANCEL_HINT,
            ),
            parse_mode="MarkdownV2",
            reply_markup=_openrouter_input_keyboard(),
        )
        return

    context.user_data["_openrouter_typed_slug"] = model.slug

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Use This Model", callback_data="agent:or_type_confirm"
                ),
                ui.cancel_button("agent:or_type_cancel"),
            ]
        ]
    )
    await update.message.answer(
        ui.screen(
            ui.title("🧭", f"Use OpenRouter — {model.name}?"), _model_rows(model)
        ),
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )


async def _handle_openrouter_type_confirm(update: Update, context: Context) -> None:
    """Apply the typed slug as the active agent_llm."""
    query = update.callback_query
    slug = context.user_data.pop("_openrouter_typed_slug", None)
    if not slug:
        await query.message.edit_text(_EXPIRED, parse_mode="MarkdownV2")
        return

    set_active_llm(context, f"openrouter:{slug}")
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    await destroy_session(update.effective_chat.id)

    await query.message.edit_text(
        ui.notice(
            "success",
            f"LLM set to OpenRouter — {slug}",
            "New sessions will use this model. Use /agent to continue.",
        ),
        parse_mode="MarkdownV2",
    )


async def _handle_openrouter_type_cancel(update: Update, context: Context) -> None:
    """Discard the typed slug without changing the active LLM."""
    query = update.callback_query
    context.user_data.pop("_openrouter_typed_slug", None)
    await query.message.edit_text(_CANCELLED, parse_mode="MarkdownV2")
