"""/delegations — monitor and stop background agent tasks (DELEGATE mode).

A DELEGATE task is a detached Agent instance launched via the ``delegate`` MCP
tool or the web dashboard (see :mod:`condor.agents.delegate`). It runs unattended
until done and pings the chat with its result. This command is the in-chat window
into that ephemeral, in-process registry: list what's running/finished, read a
result, or stop a runaway task.

It is read/act only — starting a delegation stays with the MCP tool / web UI,
which already pick an agent and task. The Telegram bot shares the main.py process
with the agent runtime, so we read :func:`get_all_delegations` directly (no HTTP).

Callbacks reference tasks by their index in the last rendered snapshot
(``_deleg_ids`` in ``user_data``) rather than the raw ``task_id``, keeping
``callback_data`` well under Telegram's 64-byte cap regardless of slug length.
"""

import logging

from condor.agents.delegate import get_all_delegations, get_delegation, stop_delegation
from condor.telegram.errors import is_not_modified
from condor.telegram.format import RICH_MAX_LEN, split_text
from condor.telegram.transport import send_text
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from handlers import clear_all_input_states
from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

log = logging.getLogger(__name__)

_STATUS_EMOJI = {
    "running": "🟢",
    "done": "✅",
    "error": "❌",
    "stopped": "⏹",
}


def _ordered_delegations() -> list:
    """Live delegations, running ones first, otherwise insertion order."""
    items = list(get_all_delegations().values())
    items.sort(key=lambda dt: 0 if dt.status == "running" else 1)
    return items


def _list_text_and_keyboard(context: Context):
    """Render the delegation list, caching the task_id order for callbacks."""
    items = _ordered_delegations()
    # Snapshot the id order so index-based callbacks resolve back to a task_id.
    context.user_data["_deleg_ids"] = [dt.task_id for dt in items]

    if not items:
        text = (
            "🤖 *Background Tasks*\n\n"
            "No delegations in this session\\.\n\n"
            "_Start one with the_ `delegate` _tool or the web dashboard\\._"
        )
        keyboard = [
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="deleg:list")]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=keyboard)

    lines = ["🤖 *Background Tasks*", ""]
    keyboard = []
    for idx, dt in enumerate(items):
        emoji = _STATUS_EMOJI.get(dt.status, "•")
        task_preview = dt.task.strip().splitlines()[0] if dt.task.strip() else "—"
        if len(task_preview) > 60:
            task_preview = task_preview[:60] + "…"
        lines.append(
            f"{emoji} *{escape_markdown_v2(dt.agent_slug)}* — "
            f"{escape_markdown_v2(dt.status)}\n"
            f"   {escape_markdown_v2(task_preview)}"
        )
        # One button row per task: open its detail view.
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{emoji} {dt.agent_slug}", callback_data=f"deleg:view:{idx}"
                )
            ]
        )

    keyboard.append(
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="deleg:list")]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


def _detail_text_and_keyboard(dt, idx: int, page: int = 0):
    """Render one delegation's full status + result/error."""
    emoji = _STATUS_EMOJI.get(dt.status, "•")
    body = dt.error if dt.status == "error" else dt.result
    body = (body or "").strip() or "no output yet"
    if dt.status == "error" and dt.result:
        body += f"\n\nPartial progress (task incomplete):\n{dt.result}"
    pages = split_text(body, RICH_MAX_LEN) or ["no output yet"]
    page = max(0, min(page, len(pages) - 1))
    # Standard Markdown: the result body is model-authored Markdown.
    heading = f"{emoji} **{dt.agent_slug.replace('_', ' ')}**\n_{dt.status}_"
    text = f"{heading}\n\n{pages[page]}"

    buttons = []
    if dt.status == "running":
        buttons.append(
            InlineKeyboardButton(text="🛑 Stop", callback_data=f"deleg:stop:{idx}")
        )
    buttons.append(InlineKeyboardButton(text="« Back", callback_data="deleg:list"))
    keyboard = []
    if len(pages) > 1:
        navigation = []
        if page:
            navigation.append(
                InlineKeyboardButton(
                    text="‹ Prev", callback_data=f"deleg:view:{idx}:{page - 1}"
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{len(pages)}",
                callback_data=f"deleg:view:{idx}:{page}",
            )
        )
        if page + 1 < len(pages):
            navigation.append(
                InlineKeyboardButton(
                    text="Next ›", callback_data=f"deleg:view:{idx}:{page + 1}"
                )
            )
        keyboard.append(navigation)
    keyboard.append(buttons)
    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


def _resolve_task_id(context: Context, idx_str: str) -> str | None:
    """Map a callback index back to a task_id from the last rendered snapshot."""
    ids = context.user_data.get("_deleg_ids") or []
    try:
        idx = int(idx_str)
    except ValueError:
        return None
    if 0 <= idx < len(ids):
        return ids[idx]
    return None


async def _replace_menu(query, text, keyboard) -> None:
    """Put the MarkdownV2 list back, even when the current message is rich."""
    try:
        await query.message.edit_text(
            text, reply_markup=keyboard, parse_mode="MarkdownV2"
        )
        return
    except Exception as exc:
        if is_not_modified(exc):
            return
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.message.answer(text, reply_markup=keyboard, parse_mode="MarkdownV2")


@restricted
async def delegations_command(update: Update, context: Context) -> None:
    """Handle /delegations — list background agent tasks."""
    clear_all_input_states(context)
    text, keyboard = _list_text_and_keyboard(context)
    await update.message.answer(text, reply_markup=keyboard, parse_mode="MarkdownV2")


@restricted
async def delegations_callback_handler(update: Update, context: Context) -> None:
    """Route deleg:* callbacks (list / view / stop)."""
    query = update.callback_query
    action = query.data.split(":", 1)[1] if ":" in query.data else query.data

    if action == "list":
        await query.answer()
        text, keyboard = _list_text_and_keyboard(context)
        await _replace_menu(query, text, keyboard)
        return

    if action.startswith("view:"):
        await query.answer()
        parts = action.split(":")
        idx_str = parts[1]
        try:
            page = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            page = 0
        task_id = _resolve_task_id(context, idx_str)
        dt = get_delegation(task_id) if task_id else None
        if dt is None:
            text, keyboard = _list_text_and_keyboard(context)
            await _replace_menu(query, text, keyboard)
            return
        text, keyboard = _detail_text_and_keyboard(dt, int(idx_str), page)
        await send_text(
            context.bot,
            query.message.chat.id,
            text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if action.startswith("stop:"):
        idx_str = action.split(":", 1)[1]
        task_id = _resolve_task_id(context, idx_str)
        if not task_id:
            await query.answer("Task no longer available", show_alert=True)
        else:
            stopped = await stop_delegation(task_id)
            await query.answer(
                "Stopped" if stopped else "Not running", show_alert=not stopped
            )
        text, keyboard = _list_text_and_keyboard(context)
        await _replace_menu(query, text, keyboard)
        return

    await query.answer()
