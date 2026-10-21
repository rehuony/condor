"""OpenRouter model picker and typed-slug flow."""

import logging

from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

from .availability import _reclaim_default_agent, set_active_llm
from .session import destroy_session

log = logging.getLogger(__name__)


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
        await query.message.edit_text("Loading OpenRouter models...")

    try:
        models = await fetch_models()
    except Exception as e:
        log.exception("Failed to fetch OpenRouter models")
        await query.message.edit_text(f"Failed to fetch OpenRouter models: {e}")
        return

    if not models:
        await query.message.edit_text(
            "No OpenRouter models available. Check your network or try again later."
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
        f"Select an OpenRouter model ({len(models)} with tool-calling support):",
        reply_markup=keyboard,
    )


async def _handle_openrouter_pick(update: Update, context: Context, idx: int) -> None:
    """Resolve picker index → set agent_llm to 'openrouter:<slug>'."""
    query = update.callback_query
    models = context.user_data.get("_openrouter_models") or []
    if not models or idx < 0 or idx >= len(models):
        await query.message.edit_text(
            "Selection expired. Reopen the picker via Change LLM."
        )
        return

    model = models[idx]
    agent_key = f"openrouter:{model.slug}"
    set_active_llm(context, agent_key)
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    await destroy_session(update.effective_chat.id)

    pricing = ""
    if model.prompt_price or model.completion_price:
        pricing = (
            f"\nPricing: ${model.prompt_price:.2f}/M input, "
            f"${model.completion_price:.2f}/M output"
        )

    await query.message.edit_text(
        f"LLM set to OpenRouter — {model.name}.\n"
        f"Slug: {model.slug}{pricing}\n\n"
        "New sessions will use this model. Use /agent to continue."
    )


async def _handle_openrouter_type_prompt(update: Update, context: Context) -> None:
    """Arm slug-input mode: next text message is parsed as an OpenRouter slug."""
    from .menu import _openrouter_input_keyboard

    query = update.callback_query
    context.user_data["_openrouter_typing_slug"] = True
    await query.message.edit_text(
        "Send the OpenRouter model slug as a message.\n"
        "Example: anthropic/claude-sonnet-4.5\n\n"
        "Send /cancel, or tap Cancel, to abort.",
        reply_markup=_openrouter_input_keyboard(),
    )


async def _resolve_openrouter_typed_slug(
    update: Update, context: Context, text: str
) -> None:
    """Validate a typed slug; on match, prompt confirmation."""
    from condor.llm.openrouter_models import fetch_models, find_model_by_slug

    from .menu import _openrouter_input_keyboard

    slug = text.strip()
    if slug.lower() in ("/cancel", "cancel"):
        await update.message.answer("Cancelled. Use /agent to continue.")
        return

    try:
        models = await fetch_models()
    except Exception as e:
        log.exception("Failed to fetch OpenRouter models")
        await update.message.answer(f"Failed to fetch OpenRouter models: {e}")
        return

    model = find_model_by_slug(models, slug)
    if not model:
        # Re-arm so the user can retype without hunting for the button again —
        # with a Cancel button, so a mistyped slug can't trap them in a loop of
        # "that isn't a model either" with no visible way out.
        context.user_data["_openrouter_typing_slug"] = True
        await update.message.answer(
            f"No tool-calling OpenRouter model matches '{slug}'.\n"
            "The slug must be exact (e.g. anthropic/claude-sonnet-4.5).\n"
            "Try again, send /cancel, or tap Cancel.",
            reply_markup=_openrouter_input_keyboard(),
        )
        return

    context.user_data["_openrouter_typed_slug"] = model.slug

    pricing = ""
    if model.prompt_price or model.completion_price:
        pricing = (
            f"\nPricing: ${model.prompt_price:.2f}/M input, "
            f"${model.completion_price:.2f}/M output"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Use This Model", callback_data="agent:or_type_confirm"
                ),
                InlineKeyboardButton(
                    text="Cancel", callback_data="agent:or_type_cancel"
                ),
            ]
        ]
    )
    await update.message.answer(
        f"Use OpenRouter — {model.name}?\nSlug: {model.slug}{pricing}",
        reply_markup=keyboard,
    )


async def _handle_openrouter_type_confirm(update: Update, context: Context) -> None:
    """Apply the typed slug as the active agent_llm."""
    query = update.callback_query
    slug = context.user_data.pop("_openrouter_typed_slug", None)
    if not slug:
        await query.message.edit_text(
            "Selection expired. Reopen the picker via Change LLM."
        )
        return

    set_active_llm(context, f"openrouter:{slug}")
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    await destroy_session(update.effective_chat.id)

    await query.message.edit_text(
        f"LLM set to OpenRouter — {slug}.\n\n"
        "New sessions will use this model. Use /agent to continue."
    )


async def _handle_openrouter_type_cancel(update: Update, context: Context) -> None:
    """Discard the typed slug without changing the active LLM."""
    query = update.callback_query
    context.user_data.pop("_openrouter_typed_slug", None)
    await query.message.edit_text("Cancelled. Use /agent to continue.")
