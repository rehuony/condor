"""/memory — review and prune what each assistant remembers about the user.

Memory is **per-assistant** (FEAT-003): the chat ``condor`` and every trading
agent keep their own isolated memory. This view shows one section per assistant
that has a non-empty memory, with inline buttons to delete each entry. Deletes go
through MemoryStore.delete(..., source="user") so they are audited in that
assistant's own audit.log. (Skills are NOT shown here — they are read-only
playbooks general to the assistant, authored in the repo, not learned per user.)

Delete callbacks reference a memory by a short integer index into a per-render
map (``memory:del:{idx}``) instead of embedding the ``(agent_slug, name)`` pair
directly: assistant slugs and memory names are uncapped, so embedding both could
push ``callback_data`` past Telegram's hard 64-byte limit and make the whole view
fail to render. The map is stored in ``context.user_data`` and rebuilt on every
render, so it always matches the buttons currently on screen.
"""

import logging

from condor.memory import MemoryStore, iter_user_stores
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils import telegram_ui as ui
from utils.auth import restricted
from utils.telegram_ui import is_current_menu, remember_menu

logger = logging.getLogger(__name__)

# Per-assistant entry caps, plus a global cap so many trading agents keep the
# message under Telegram's 4096 characters. Every listed entry has its button.
_MAX_BUTTONS_PER_STORE = 15
_MAX_BUTTONS_TOTAL = 20
_MAX_AUDIT = 3

# Key under which the per-render delete map ({idx: (agent_slug, name)}) is cached
# in context.user_data so delete callbacks can stay a tiny ``memory:del:{idx}``.
_DEL_MAP_KEY = "memory_del_map"


#: Section order and headings for the memory types MemoryStore accepts.
_TYPE_SECTIONS = {
    "preference": "Preferences",
    "feedback": "Feedback",
    "fact": "Facts",
    "reference": "References",
}
_CHANGES = {
    "create": ("➕", "created"),
    "update": ("✏️", "updated"),
    "delete": ("🗑", "deleted"),
}


def _by_type(memories: list[dict]) -> list[tuple[str, list[dict]]]:
    """Memories grouped under their type heading, known types first."""
    order = [
        *_TYPE_SECTIONS,
        *sorted({m["type"] for m in memories} - _TYPE_SECTIONS.keys()),
    ]
    groups = [
        (
            _TYPE_SECTIONS.get(key, key.title()),
            [m for m in memories if m["type"] == key],
        )
        for key in order
    ]
    return [(heading, group) for heading, group in groups if group]


def _build_view(user_id: int, context: Context) -> tuple[str, InlineKeyboardMarkup]:
    """Build the /memory message text + keyboard, grouped by assistant.

    Each assistant gets a bold heading with its count, then its memories grouped
    by type and numbered across the whole view; the delete buttons carry the
    same numbers. Populates ``context.user_data[_DEL_MAP_KEY]`` with a fresh
    ``{idx: (agent_slug, name)}`` map so each delete button can carry only a
    short index instead of the (uncapped) slug + name pair.
    """
    stores = iter_user_stores(user_id)

    blocks: list[str | list[str]] = []
    keyboard: list[list[InlineKeyboardButton]] = []
    del_map: dict[int, tuple[str | None, str]] = {}
    next_idx = 0
    buttons_left = _MAX_BUTTONS_TOTAL
    shown = 0
    total = 0
    number = 0  # the on-screen number, shared by the text and its button

    for label, agent_slug, _root in stores:
        mem = MemoryStore(user_id, agent_slug)
        limit = min(_MAX_BUTTONS_PER_STORE, buttons_left)
        memories = mem.search("", limit=limit) if limit > 0 else []
        if not memories:
            continue  # skip stores that exist but hold nothing
        shown += 1
        count = len(mem.catalog())
        total += count
        counted = f"{len(memories)} of {count}" if count > len(memories) else count

        # Number in display order (grouped by type) so text and buttons agree.
        numbered: list[tuple[int, dict]] = []
        section = [f"💬 {ui.bold(label)} · {ui.esc(counted)}"]
        for heading, group in _by_type(memories):
            section.append(ui.hint(heading))
            for m in group:
                number += 1
                numbered.append((number, m))
                section.append(f"{number}\\. {ui.code(m['name'])}")
                if m["description"]:
                    section.append(f"    {ui.esc(m['description'])}")

        audit = mem.audit(limit=_MAX_AUDIT)
        if audit:
            section.append(ui.hint("Recent changes"))
            for e in reversed(audit):  # newest first
                action = e.get("action", "")
                icon, verb = _CHANGES.get(action, ("•", action))
                target = (
                    e.get("target", "").replace("memory:", "").replace("skill:", "")
                )
                source = e.get("source", "")
                section.append(
                    f"{icon} {ui.code(target)} {ui.hint(f'{verb} by {source}')}"
                )
        blocks.append(section)

        # Delete buttons for this assistant (bounded globally). callback_data
        # carries only a short index into del_map so it can never exceed
        # Telegram's 64-byte limit regardless of slug/name length.
        for number_shown, m in numbered:
            if buttons_left <= 0:
                break
            del_map[next_idx] = (agent_slug or None, m["name"])
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"🗑 {number_shown} · {m['name']}",
                        callback_data=f"memory:del:{next_idx}",
                    )
                ]
            )
            next_idx += 1
            buttons_left -= 1

    if shown == 0:
        body = ui.esc(
            "No memories yet. Each assistant (the chat and each trading agent) "
            "builds its own as it learns about you."
        )
        meta = None
    else:
        body = None
        meta = f"{total} memories · {shown} assistant{'s' if shown != 1 else ''}"

    text = ui.screen(
        ui.title("🧠", "Memory", meta),
        body,
        *blocks,
        ui.hint("Tap a numbered 🗑 button to delete that memory.") if shown else None,
    )

    keyboard.append([ui.refresh_button("memory:refresh")])

    context.user_data[_DEL_MAP_KEY] = del_map

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


@restricted
async def memory_command(update: Update, context: Context) -> None:
    """Handle /memory — show each assistant's memory, grouped."""
    from handlers import clear_all_input_states

    clear_all_input_states(context)

    user_id = update.effective_user.id
    text, keyboard = _build_view(user_id, context)
    sent = await update.message.answer(
        text, parse_mode="MarkdownV2", reply_markup=keyboard
    )
    remember_menu(context.user_data, "memory", sent.message_id)


@restricted
async def memory_callback_handler(update: Update, context: Context) -> None:
    """Handle memory:* callbacks (delete a memory in a store, refresh)."""
    query = update.callback_query
    user_id = update.effective_user.id

    action = query.data.split(":", 1)[1] if ":" in query.data else query.data

    if action.startswith("del:") and not is_current_menu(
        context.user_data, "memory", query.message
    ):
        # Indexes belong to the newest view; an older copy could delete the
        # wrong memory. Rebuild this copy instead.
        await query.answer("This list was out of date. It is refreshed now.")
    elif action.startswith("del:"):
        # del:{idx} — idx is a key into the map built by the last _build_view.
        del_map = context.user_data.get(_DEL_MAP_KEY) or {}
        try:
            idx = int(action.split(":", 1)[1])
            agent_slug, name = del_map[idx]
        except (ValueError, KeyError):
            # Stale/unknown button (e.g. after a restart) — nothing to delete.
            await query.answer("Not found", show_alert=False)
        else:
            deleted = MemoryStore(user_id, agent_slug).delete(name, source="user")
            await query.answer("Deleted" if deleted else "Not found", show_alert=False)
    else:
        await query.answer()

    text, keyboard = _build_view(user_id, context)
    remember_menu(context.user_data, "memory", query.message.message_id)
    try:
        await query.message.edit_text(
            text, parse_mode="MarkdownV2", reply_markup=keyboard
        )
    except Exception:
        # Telegram raises if the message is unchanged (e.g. refresh with no diff).
        pass


__all__ = ["memory_command", "memory_callback_handler"]
