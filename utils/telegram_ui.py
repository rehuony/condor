"""The visual grammar every Telegram screen shares, written as MarkdownV2.

A screen is a bold title with an optional italic meta line, then blocks
separated by blank lines: bold section headings, ``•`` bullets, ``1.`` indexed
rows and an italic hint at the bottom. Notices (error, warning, success, info)
and input prompts have one fixed shape each, so every module reads alike.

Text builders escape what they are given; pass ``raw=True`` only for a fragment
that is already MarkdownV2 (for example a value that carries its own ``code``
entity). Buttons come from the same vocabulary: navigation labels are fixed
strings and actions lead with one emoji from :data:`ACTION_EMOJI`.
``tests/test_telegram_style.py`` holds the source to these labels.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from condor.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup
from utils.telegram_formatters import escape_markdown_v2, escape_markdown_v2_code

# -- text -------------------------------------------------------------------

NOTICE_EMOJI = {
    "error": "❌",
    "warning": "⚠️",
    "success": "✅",
    "info": "ℹ️",
    "cancelled": "✕",
    "stopped": "🛑",
    "rejected": "❌",
    "stale": "⚠️",
}

#: The prompt footer; every "next message is the answer" screen ends with it.
CANCEL_HINT = "_Send /cancel to cancel\\._"


def esc(value: object) -> str:
    return escape_markdown_v2(str(value))


def _text(value: object, raw: bool) -> str:
    return str(value) if raw else esc(value)


def bold(value: object, *, raw: bool = False) -> str:
    return f"*{_text(value, raw)}*"


def italic(value: object, *, raw: bool = False) -> str:
    return f"_{_text(value, raw)}_"


def code(value: object) -> str:
    return f"`{escape_markdown_v2_code(value)}`"


def title(emoji: str, text: str, meta: str | None = None, *, raw: bool = False) -> str:
    """``{emoji} *Title*`` with an optional italic meta line beneath."""
    head = f"{emoji} {bold(text, raw=raw)}" if emoji else bold(text, raw=raw)
    if meta:
        head += f"\n{italic(meta, raw=raw)}"
    return head


def server_meta(server_name: str | None, status: str | None = None) -> str | None:
    """The ``Server: name 🟢`` meta line most screens carry, unescaped."""
    if not server_name:
        return None
    icon = {
        "online": "🟢",
        "offline": "🔴",
        "auth_error": "🟠",
        "error": "⚠️",
        "loading": "⏳",
    }.get(status or "", "")
    return f"Server: {server_name} {icon}".rstrip()


def section(name: str, *, raw: bool = False) -> str:
    return bold(name, raw=raw)


def bullet(
    label: str, value: object = None, *, code_value: bool = False, raw: bool = False
) -> str:
    """``• Label: *value*``; ``• Label`` without a value."""
    text = f"• {_text(label, raw)}"
    if value is None:
        return text
    shown = code(value) if code_value else bold(value, raw=raw)
    return f"{text}: {shown}"


def numbered(index: int, text: str, *, raw: bool = False) -> str:
    return f"{index}\\. {_text(text, raw)}"


def hint(text: str, *, raw: bool = False) -> str:
    return italic(text, raw=raw)


def screen(*blocks: str | Iterable[str] | None) -> str:
    """Join blocks with blank lines; a list block is joined line by line."""
    parts = []
    for block in blocks:
        if not block:
            continue
        if not isinstance(block, str):
            block = "\n".join(line for line in block if line)
            if not block:
                continue
        parts.append(block)
    return "\n\n".join(parts)


def notice(
    kind: str, heading: str, body: str | None = None, *, raw: bool = False
) -> str:
    """``❌ *Heading*`` then the body after a blank line."""
    return screen(
        f"{NOTICE_EMOJI[kind]} {bold(heading)}", _text(body, raw) if body else None
    )


def prompt(
    heading: str,
    instruction: str,
    example: str | None = None,
    *,
    raw: bool = False,
) -> str:
    """An input prompt: what to send, an optional example, and the /cancel hint."""
    return screen(
        f"✏️ {bold(heading)}",
        _text(instruction, raw),
        f"Example: {code(example)}" if example else None,
        CANCEL_HINT,
    )


STALE_BUTTON = notice(
    "stale", "Button no longer available", "Open the menu again to continue."
)
STALE_INPUT = notice(
    "stale", "Input no longer expected", "Open the menu again to continue."
)
SOMETHING_WRONG = notice(
    "error",
    "Something went wrong",
    "The action could not finish. Try again, or reopen the menu.",
)


# -- buttons ----------------------------------------------------------------

BACK = "« Back"
CLOSE = "✕ Close"
CANCEL = "✕ Cancel"
REFRESH = "🔄 Refresh"
PREV = "‹ Prev"
NEXT = "Next ›"
SELECTED = "✓ "

ACTION_EMOJI = {
    "add": "➕",
    "edit": "✏️",
    "delete": "🗑",
    "save": "💾",
    "confirm": "✅",
    "reject": "❌",
    "start": "▶️",
    "stop": "🛑",
    "deploy": "🚀",
    "retry": "🔁",
    "history": "📜",
    "chart": "📊",
    "search": "🔍",
    "settings": "⚙️",
    "help": "❓",
    "share": "📤",
    "quick": "⌨️",
    "close_position": "🔻",
}


def button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def back_button(callback_data: str, to: str | None = None) -> InlineKeyboardButton:
    return button(f"{BACK} to {to}" if to else BACK, callback_data)


def close_button(callback_data: str) -> InlineKeyboardButton:
    """Dismiss the screen; its handler deletes the message."""
    return button(CLOSE, callback_data)


def cancel_button(callback_data: str) -> InlineKeyboardButton:
    """Abandon a prompt and return to its parent screen."""
    return button(CANCEL, callback_data)


def refresh_button(callback_data: str) -> InlineKeyboardButton:
    return button(REFRESH, callback_data)


def action_button(kind: str, text: str, callback_data: str) -> InlineKeyboardButton:
    return button(f"{ACTION_EMOJI[kind]} {text}", callback_data)


def selected(text: str, is_selected: bool) -> str:
    return f"{SELECTED}{text}" if is_selected else text


def pager_row(
    indicator: str,
    noop_callback: str,
    prev_callback: str | None = None,
    next_callback: str | None = None,
) -> list[InlineKeyboardButton]:
    """``‹ Prev`` · indicator · ``Next ›``; a missing side keeps its slot blank."""
    return [
        button(PREV, prev_callback) if prev_callback else button(" ", noop_callback),
        button(indicator, noop_callback),
        button(NEXT, next_callback) if next_callback else button(" ", noop_callback),
    ]


def keyboard(
    *rows: Sequence[InlineKeyboardButton] | None,
) -> InlineKeyboardMarkup:
    """A markup from rows, skipping empty ones."""
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows if row])


# -- stale menus --------------------------------------------------------------


STALE_MENU = notice(
    "stale",
    "This menu is out of date",
    "A newer copy replaced it, so its buttons could act on different data. "
    "Tap Refresh to load the current one here.",
)


def remember_menu(user_data: dict, key: str, message_id: int | None) -> None:
    """Record which message shows the menu ``key``'s current snapshot."""
    if message_id is not None:
        user_data[f"_menu:{key}"] = message_id


def is_current_menu(user_data: dict, key: str, message) -> bool:
    """False when a button comes from an older copy of the menu ``key``.

    A menu that never recorded itself is treated as current, so screens that
    are not guarded keep working.
    """
    recorded = user_data.get(f"_menu:{key}")
    return recorded is None or recorded == getattr(message, "message_id", None)


async def refuse_stale_menu(query, refresh_callback: str) -> None:
    """Replace an outdated menu with a notice that reloads it in place."""
    markup = keyboard([refresh_button(refresh_callback)])
    try:
        await query.message.edit_text(
            STALE_MENU, parse_mode="MarkdownV2", reply_markup=markup
        )
    except Exception:
        # A photo menu has a caption, not text; say it in a new message.
        await query.message.answer(
            STALE_MENU, parse_mode="MarkdownV2", reply_markup=markup
        )
