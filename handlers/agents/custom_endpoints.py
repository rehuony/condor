"""Custom OpenAI-compatible endpoints: add, validate, pick, manage, delete."""

import logging
import time

from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils import telegram_ui as ui

from ._shared import (
    AGENT_OPTIONS,
    DEFAULT_AGENT,
)
from .availability import _reclaim_default_agent, set_active_llm
from .session import destroy_session

log = logging.getLogger(__name__)


# How long a fetched model list stays usable before we re-hit /models.
CUSTOM_MODEL_CACHE_TTL = 600


_CUSTOM_INPUT_KEYS = (
    "_custom_typing_url",
    "_custom_typing_key",
    "_custom_typing_search",
    "_custom_pending_url",
    "_custom_pending_name",
    "_custom_rekey_provider",
)


def _current_custom_model(context: Context) -> str | None:
    """Model id of the current agent_llm when it points at a custom endpoint."""
    from condor.preferences import parse_custom_agent_key

    _, model_id = parse_custom_agent_key(_reclaim_default_agent(context))
    return model_id or None


def _clear_custom_input(context: Context) -> None:
    """Disarm any half-finished typing step in this flow."""
    for key in _CUSTOM_INPUT_KEYS:
        context.user_data.pop(key, None)


_TITLE = ui.title("🔌", "Custom Endpoints")


async def _show(
    update: Update,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    placeholder=None,
):
    """Render a screen, whichever way the user got here.

    ``text`` is plain prose; every screen of this flow shows it escaped under
    the flow's title. Callback taps edit the message in place; typed input has
    no message of ours to edit, so it gets a fresh one (or reuses an explicit
    placeholder).
    """
    text = ui.screen(_TITLE, ui.esc(text))
    if placeholder is not None:
        await placeholder.edit_text(
            text, parse_mode="MarkdownV2", reply_markup=keyboard
        )
        return placeholder
    query = update.callback_query
    if query is not None:
        await query.message.edit_text(
            text, parse_mode="MarkdownV2", reply_markup=keyboard
        )
        return query.message
    return await update.effective_chat.send_message(
        text, parse_mode="MarkdownV2", reply_markup=keyboard
    )


async def _handle_custom_list(
    update: Update,
    context: Context,
    notice: str = "",
    placeholder=None,
) -> None:
    """Landing screen — the user's saved endpoints."""
    from condor.preferences import get_custom_providers

    from .menu import _custom_endpoints_keyboard

    _clear_custom_input(context)
    providers = get_custom_providers(context.user_data)

    if providers:
        body = "Pick an OpenAI-compatible endpoint to choose a model from it."
    else:
        body = (
            "Connect any OpenAI-compatible API — Venice AI, Together, "
            "Fireworks, or your own vLLM / LM Studio server."
        )

    text = f"ℹ️ {notice}\n\n{body}" if notice else body
    await _show(
        update,
        text,
        _custom_endpoints_keyboard(providers, _reclaim_default_agent(context)),
        placeholder=placeholder,
    )


def _provider_named(context: Context, name: str) -> dict | None:
    """Resolve the endpoint a `cu_*` button was rendered for.

    Buttons carry the sanitized nickname rather than a list position: the saved
    list is shared state (the web dashboard writes it too), so an index taken at
    render time can point at a different endpoint by the time it's tapped —
    which for delete and re-key means destroying the wrong credential.
    """
    from condor.preferences import find_custom_provider

    return find_custom_provider(context.user_data, name)


async def _handle_custom_url_prompt(update: Update, context: Context) -> None:
    """Arm URL-input mode: the next text message is the endpoint base URL."""
    from .menu import _custom_input_keyboard

    _clear_custom_input(context)
    context.user_data["_custom_typing_url"] = True
    await _show(
        update,
        "Send the base URL of the OpenAI-compatible API as a message.\n\n"
        "Examples:\n"
        "• https://api.venice.ai/api/v1\n"
        "• http://localhost:8000/v1\n\n"
        "Send /cancel to cancel.",
        _custom_input_keyboard(),
    )


async def _resolve_custom_url(update: Update, context: Context, text: str) -> None:
    """Validate the typed base URL, then prompt for the API key."""
    from condor.llm.custom_models import normalize_base_url
    from condor.preferences import suggest_provider_name, unique_provider_name

    from .menu import _custom_input_keyboard, _custom_key_prompt_keyboard

    raw = text.strip()
    if raw.lower() == "cancel":
        await _handle_custom_list(update, context, notice="Cancelled.")
        return

    try:
        base_url = normalize_base_url(raw)
    except ValueError as e:
        # Re-arm so the user can retype — with a Cancel button, so a failed
        # parse can't trap them in a loop of "that isn't a URL either".
        context.user_data["_custom_typing_url"] = True
        await _show(
            update,
            f"⚠️ {e}\n\nSend another URL, or /cancel to cancel.",
            _custom_input_keyboard(),
        )
        return

    name = unique_provider_name(context.user_data, suggest_provider_name(base_url))
    context.user_data["_custom_pending_url"] = base_url
    context.user_data["_custom_pending_name"] = name
    context.user_data["_custom_typing_key"] = True

    await _show(
        update,
        f"• Endpoint: {base_url}\n• Saved as: {name}\n\n"
        "Now send the API key as a message. Your message is deleted from the "
        "chat as soon as it's read; the key is stored on this bot's host so it "
        "can sign requests.\n\nSend /cancel to cancel.",
        _custom_key_prompt_keyboard(),
    )


async def _handle_custom_no_key(update: Update, context: Context) -> None:
    """'No API Key Needed' — validate the pending endpoint unauthenticated."""
    await _validate_and_save(update, context, api_key="")


async def _resolve_custom_key(update: Update, context: Context, text: str) -> None:
    """Consume the typed API key and validate the endpoint with it."""
    key = text.strip()
    if key.lower() == "cancel":
        await _handle_custom_list(update, context, notice="Cancelled.")
        return

    # Best effort: get the secret out of the visible chat history
    try:
        await update.message.delete()
    except Exception:
        log.debug(
            "Could not delete API key message in chat %s", update.effective_chat.id
        )

    await _validate_and_save(update, context, api_key=key)


async def _validate_and_save(update: Update, context: Context, api_key: str) -> None:
    """Fetch /models to prove the endpoint works, then persist it."""
    from condor.llm.custom_models import CustomProviderError, fetch_models
    from condor.preferences import save_custom_provider

    from .menu import _custom_error_keyboard, _custom_picker_keyboard

    base_url = context.user_data.get("_custom_pending_url")
    name = context.user_data.get("_custom_pending_name")
    if not base_url or not name:
        await _handle_custom_list(
            update, context, notice="That setup step expired — start again."
        )
        return

    placeholder = await _show(update, f"⏳ Checking {base_url}…")

    try:
        resolved_url, models = await fetch_models(base_url, api_key)
    except CustomProviderError as e:
        await _show(
            update,
            f"❌ Couldn't use that endpoint: {e}",
            _custom_error_keyboard("agent:cu_retry"),
            placeholder=placeholder,
        )
        return

    try:
        saved = save_custom_provider(context.user_data, name, resolved_url, api_key)
    except ValueError as e:
        await _show(
            update,
            f"❌ {e}",
            _custom_error_keyboard("agent:cu_retry"),
            placeholder=placeholder,
        )
        return

    _clear_custom_input(context)
    _cache_models(context, saved["name"], models)

    await _show(
        update,
        f"✅ '{saved['name']}' saved — {len(models)} chat models available at "
        f"{resolved_url}.\n\nSelect the model for new sessions:",
        _custom_picker_keyboard(
            models, page=0, current_id=_current_custom_model(context)
        ),
        placeholder=placeholder,
    )


async def _handle_custom_retry(update: Update, context: Context) -> None:
    """Re-run validation for the endpoint the user was just adding."""
    if not context.user_data.get("_custom_pending_url"):
        await _handle_custom_list(
            update, context, notice="That setup step expired — start again."
        )
        return
    context.user_data["_custom_typing_key"] = True
    from .menu import _custom_key_prompt_keyboard

    await _show(
        update,
        f"• Endpoint: {context.user_data['_custom_pending_url']}\n\n"
        "Send the API key again, or continue without one.\n\n"
        "Send /cancel to cancel.",
        _custom_key_prompt_keyboard(),
    )


def _cache_models(context: Context, provider_name: str, models: list[str]) -> None:
    context.user_data["_custom_models"] = {
        "provider": provider_name,
        "models": models,
        "ts": time.time(),
    }
    context.user_data.pop("_custom_filter", None)


def _cached_models(context: Context, provider_name: str) -> list[str] | None:
    """Model list for `provider_name` if we fetched it recently enough."""
    cache = context.user_data.get("_custom_models") or {}
    if cache.get("provider") != provider_name:
        return None
    if time.time() - cache.get("ts", 0) > CUSTOM_MODEL_CACHE_TTL:
        return None
    return cache.get("models") or None


async def _handle_custom_use(
    update: Update, context: Context, provider_name: str
) -> None:
    """Open the model picker for a saved endpoint, refetching if stale."""
    from condor.llm.custom_models import CustomProviderError, fetch_models
    from condor.preferences import sanitize_provider_name

    from .menu import _custom_error_keyboard, _custom_picker_keyboard

    provider = _provider_named(context, provider_name)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    name = provider["name"]
    key = sanitize_provider_name(name)
    models = _cached_models(context, name)

    if models is None:
        placeholder = await _show(update, f"⏳ Fetching models from {name}…")
        try:
            resolved_url, models = await fetch_models(
                provider["base_url"], provider.get("api_key", "")
            )
        except CustomProviderError as e:
            await _show(
                update,
                f"❌ '{name}' isn't responding: {e}",
                _custom_error_keyboard(
                    f"agent:cu_use:{key}",
                    secondary=("✏️ Change API Key", f"agent:cu_key:{key}"),
                ),
                placeholder=placeholder,
            )
            return
        _cache_models(context, name, models)
        if resolved_url != provider["base_url"]:
            from condor.preferences import save_custom_provider

            save_custom_provider(
                context.user_data, name, resolved_url, provider.get("api_key", "")
            )
    else:
        placeholder = None

    await _show(
        update,
        f"{name} — {len(models)} chat models.\nSelect the model for new sessions:",
        _custom_picker_keyboard(
            models, page=0, current_id=_current_custom_model(context)
        ),
        placeholder=placeholder,
    )


async def _handle_custom_page(update: Update, context: Context, page: int) -> None:
    """Page through the cached model list."""
    from .menu import _custom_picker_keyboard

    cache = context.user_data.get("_custom_models") or {}
    models = cache.get("models") or []
    if not models:
        await _handle_custom_list(update, context, notice="That model list expired.")
        return

    query = context.user_data.get("_custom_filter", "")
    await _show(
        update,
        f"{cache.get('provider', 'Endpoint')} — {len(models)} chat models."
        + (f"\nFilter: '{query}'" if query else "")
        + "\nSelect the model for new sessions:",
        _custom_picker_keyboard(
            models, page=page, current_id=_current_custom_model(context), query=query
        ),
    )


async def _handle_custom_search_prompt(update: Update, context: Context) -> None:
    """Arm search-input mode for the current model list."""
    from .menu import _custom_input_keyboard

    if not (context.user_data.get("_custom_models") or {}).get("models"):
        await _handle_custom_list(update, context, notice="That model list expired.")
        return
    context.user_data["_custom_typing_search"] = True
    await _show(
        update,
        "Send a search term to filter the model list (e.g. 'llama' or '70b').\n\n"
        "Send /cancel to cancel.",
        _custom_input_keyboard(),
    )


async def _resolve_custom_search(update: Update, context: Context, text: str) -> None:
    """Apply a typed search term and re-render page 1."""
    term = text.strip()
    if term.lower() == "cancel":
        term = ""
    context.user_data["_custom_filter"] = term
    await _handle_custom_page(update, context, page=0)


async def _handle_custom_clear_filter(update: Update, context: Context) -> None:
    context.user_data.pop("_custom_filter", None)
    await _handle_custom_page(update, context, page=0)


async def _handle_custom_pick(update: Update, context: Context, token: str) -> None:
    """Resolve a picker token → set agent_llm to custom@<endpoint>:<model-id>."""
    from condor.llm.custom_models import find_by_token
    from condor.preferences import build_custom_agent_key

    cache = context.user_data.get("_custom_models") or {}
    models = cache.get("models") or []
    provider_name = cache.get("provider")
    model_id = find_by_token(models, token) if models else None

    if not model_id or not provider_name:
        await _handle_custom_list(
            update, context, notice="That selection expired — pick again."
        )
        return

    set_active_llm(context, build_custom_agent_key(provider_name, model_id))
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    await destroy_session(update.effective_chat.id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Change Model", callback_data="agent:cu_page:0"
                )
            ],
            [ui.back_button("agent:menu", "Agent")],
        ]
    )
    await _show(
        update,
        f"✅ LLM set to {model_id} via '{provider_name}'.\n\n"
        "New sessions will use this model.",
        keyboard,
    )


async def _handle_custom_manage(update: Update, context: Context) -> None:
    """List endpoints with per-endpoint maintenance actions."""
    from condor.preferences import get_custom_providers

    from .menu import _custom_manage_keyboard

    _clear_custom_input(context)
    providers = get_custom_providers(context.user_data)
    if not providers:
        await _handle_custom_list(update, context, notice="No saved endpoints.")
        return

    lines = [
        f"• {p['name']} — {p['base_url']} "
        f"({'API key saved' if p.get('api_key') else 'no API key'})"
        for p in providers
    ]
    await _show(
        update,
        "Manage your saved endpoints:\n\n" + "\n".join(lines),
        _custom_manage_keyboard(providers),
    )


async def _handle_custom_rekey(
    update: Update, context: Context, provider_name: str
) -> None:
    """Replace the API key on a saved endpoint without re-entering its URL."""
    from .menu import _custom_key_prompt_keyboard

    provider = _provider_named(context, provider_name)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    _clear_custom_input(context)
    context.user_data["_custom_pending_url"] = provider["base_url"]
    context.user_data["_custom_pending_name"] = provider["name"]
    context.user_data["_custom_rekey_provider"] = provider["name"]
    context.user_data["_custom_typing_key"] = True
    context.user_data.pop("_custom_models", None)  # force a refetch with the new key

    await _show(
        update,
        f"Send the new API key for '{provider['name']}' ({provider['base_url']}).\n\n"
        "Your message is deleted as soon as it's read.\n\nSend /cancel to cancel.",
        _custom_key_prompt_keyboard(),
    )


async def _handle_custom_delete_confirm(
    update: Update, context: Context, provider_name: str
) -> None:
    from .menu import _custom_delete_keyboard

    provider = _provider_named(context, provider_name)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    await _show(
        update,
        f"🗑 Forget '{provider['name']}' ({provider['base_url']})?\n\n"
        "The saved URL and API key are deleted from this bot.",
        _custom_delete_keyboard(provider["name"]),
    )


async def _handle_custom_delete(
    update: Update, context: Context, provider_name: str
) -> None:
    """Forget an endpoint, clearing agent_llm if it pointed at that endpoint."""
    from condor.preferences import parse_custom_agent_key, remove_custom_provider

    provider = _provider_named(context, provider_name)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    name = provider["name"]
    remove_custom_provider(context.user_data, name)
    context.user_data.pop("_custom_models", None)

    # Don't leave agent_llm pointing at an endpoint that no longer resolves
    active_provider, _ = parse_custom_agent_key(_reclaim_default_agent(context))
    notice = f"Forgot '{name}'."
    if active_provider == name:
        set_active_llm(context, DEFAULT_AGENT)
        context.user_data.pop("agent_llm_auto", None)
        await destroy_session(update.effective_chat.id)
        notice += f" Active LLM reset to {AGENT_OPTIONS[DEFAULT_AGENT]['label']}."

    await _handle_custom_list(update, context, notice=notice)


async def _handle_custom_cancel(update: Update, context: Context) -> None:
    """Cancel button on any armed input step."""
    await _handle_custom_list(update, context, notice="Cancelled.")
