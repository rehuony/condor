"""Compact the conversation, automatically or with custom instructions."""

import logging

import condor.runtime.client as runtime
from condor.telegram.types import (
    Context,
    Update,
)
from utils import telegram_ui as ui

from ._shared import (
    COMPACT_CONTEXT_TEMPLATE,
    COMPACT_PROMPT_AUTO,
    COMPACT_PROMPT_CUSTOM_TEMPLATE,
)
from .session import (
    _create_tg_session,
    _tg_key,
    _tg_permission_callback,
    destroy_session,
    get_session,
)

log = logging.getLogger(__name__)

_NOTHING_TO_COMPACT = ui.notice(
    "info", "Nothing to compact", "There is no active session."
)


async def _handle_compact_menu(update: Update, context: Context) -> None:
    """Show compact options sub-menu."""
    from .menu import _compact_menu_keyboard

    query = update.callback_query
    chat_id = update.effective_chat.id
    session = await get_session(chat_id)

    if not session or not session.alive:
        await query.message.edit_text(_NOTHING_TO_COMPACT, parse_mode="MarkdownV2")
        return

    await query.message.edit_text(
        ui.screen(
            ui.title("🗜", "Compact Context"),
            [
                ui.bullet("Auto", "summarize everything"),
                ui.bullet("Custom", "specify what to keep"),
            ],
            ui.hint("The summary replaces the transcript in a new session."),
        ),
        parse_mode="MarkdownV2",
        reply_markup=_compact_menu_keyboard(),
    )


def _edit_render(message):
    """Render every step by editing one message in place (callback surface)."""

    async def render(text: str) -> None:
        await message.edit_text(text, parse_mode="MarkdownV2")

    return render


def _reply_render(message):
    """Reply once, then edit that reply in place (typed-message surface)."""
    placeholder = None

    async def render(text: str) -> None:
        nonlocal placeholder
        if placeholder is None:
            placeholder = await message.answer(text, parse_mode="MarkdownV2")
        else:
            await placeholder.edit_text(text, parse_mode="MarkdownV2")

    return render


async def _compact(
    update: Update,
    context: Context,
    *,
    instructions: str | None,
    render,
    keep_custom_flag_on_busy: bool = False,
) -> None:
    """Summarize context → destroy session → recreate with the summary reinjected.

    The single implementation behind both compact surfaces: the menu button
    (which edits its own callback message) and the typed custom instructions
    (which replies, then edits that reply). ``render`` is what abstracts the
    two apart — everything else about the sequence is identical, and keeping it
    identical is the point: a fix here can no longer land in only one copy.
    """
    chat_id = update.effective_chat.id
    session = await get_session(chat_id)

    if not session or not session.alive:
        await render(_NOTHING_TO_COMPACT)
        return

    if session.is_busy:
        await render(
            ui.notice("warning", "Agent is busy", "Wait for it to finish first.")
        )
        if keep_custom_flag_on_busy:
            # Stay armed so the user can resend the same instructions.
            context.user_data["agent_compact_custom"] = True
        return

    await render("⏳ " + ui.esc("Compacting context…"))

    if instructions:
        prompt = COMPACT_PROMPT_CUSTOM_TEMPLATE.format(instructions=instructions)
    else:
        prompt = COMPACT_PROMPT_AUTO

    try:
        summary = await runtime.prompt_once(_tg_key(chat_id), prompt)
    except Exception as e:
        log.exception("Failed to get compact summary")
        await render(ui.notice("error", "Compact failed", str(e)))
        return

    if not summary or not summary.strip():
        await render(
            ui.notice(
                "warning", "Compact aborted", "The agent returned an empty summary."
            )
        )
        return

    agent_key = session.agent_key
    # Compact is a respawn like any other, so it has to carry the identity over:
    # keeping only the model would summarize the specialist's context and then
    # hand it to the coordinator.
    agent_slug = session.agent_slug
    await destroy_session(chat_id)

    try:
        user_id = update.effective_user.id
        bot = context.bot

        _perm_cb = _tg_permission_callback(bot, chat_id, user_id)

        await _create_tg_session(
            chat_id=chat_id,
            agent_key=agent_key,
            user_id=user_id,
            permission_callback=_perm_cb,
            user_data=context.user_data,
            agent_slug=agent_slug,
            # No conversation_id on purpose: compact is the one respawn that
            # must NOT resume, or the replay would re-inject the very transcript
            # the summary just replaced. The fresh conversation minted here is
            # recorded as the chat's, so later respawns resume the compacted one.
        )

        compact_context = COMPACT_CONTEXT_TEMPLATE.format(summary=summary)
        await runtime.prompt_once(_tg_key(chat_id), compact_context)

    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await render(ui.notice("error", "Compact failed during session reset", str(e)))
        return

    word_count = len(summary.split())
    await render(
        ui.notice(
            "success",
            "Context compacted",
            f"{word_count} words carried over. Send a message to continue chatting.",
        )
    )


async def _handle_compact(
    update: Update,
    context: Context,
    custom_instructions: str | None = None,
) -> None:
    """Compact: summarize context → destroy session → recreate with summary."""
    await _compact(
        update,
        context,
        instructions=custom_instructions,
        render=_edit_render(update.callback_query.message),
    )


async def _handle_compact_custom_prompt(update: Update, context: Context) -> None:
    """Prompt user to type custom compact instructions."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = await get_session(chat_id)

    if not session or not session.alive:
        await query.message.edit_text(_NOTHING_TO_COMPACT, parse_mode="MarkdownV2")
        return

    context.user_data["agent_compact_custom"] = True
    await query.message.edit_text(
        ui.prompt(
            "What should the summary keep?",
            "Type your instructions as a message.",
            "keep the portfolio analysis and SOL trade setup",
        ),
        parse_mode="MarkdownV2",
    )


async def _do_compact_from_message(
    update: Update, context: Context, instructions: str
) -> None:
    """Execute custom compact from user's text input."""
    await _compact(
        update,
        context,
        instructions=instructions,
        render=_reply_render(update.message),
        keep_custom_flag_on_busy=True,
    )
