"""Free-text config and daily-time input processing."""

import asyncio
import logging

from condor.telegram.types import (
    Context,
    Update,
)
from routines.base import (
    get_routine,
)

from .jobs import _create_scheduled_instance
from .storage import _get_draft, _set_draft, _starter_id
from .views import _delete_after, _refresh_detail_msg

logger = logging.getLogger(__name__)


async def _process_config(update: Update, context: Context, text: str) -> None:
    """Process key=value config input."""
    editing = context.user_data.get("routines_editing", {})
    routine_name = editing.get("routine")
    fields = editing.get("fields", {})

    if not routine_name:
        return

    # Delete user message
    try:
        await update.message.delete()
    except Exception:
        pass

    routine = get_routine(routine_name)
    if not routine:
        return

    if not fields:
        fields = routine.get_fields()

    draft = _get_draft(context, routine_name)
    updates = {}
    errors = []

    for line in text.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()

        if key not in fields:
            errors.append(f"Unknown: {key}")
            continue

        field_type = fields[key]["type"]
        try:
            if field_type == "int":
                value = int(value)
            elif field_type == "float":
                value = float(value)
            elif field_type == "bool":
                value = value.lower() in ("true", "yes", "1", "on")
        except ValueError:
            errors.append(f"Invalid: {key}")
            continue

        updates[key] = value

    if errors:
        msg = await update.message.answer(f"⚠️ {', '.join(errors)}")
        asyncio.create_task(_delete_after(msg, 3))

    if not updates:
        msg = await update.message.answer("❌ Use: `key=value`", parse_mode="Markdown")
        asyncio.create_task(_delete_after(msg, 3))
        return

    draft.update(updates)
    _set_draft(context, routine_name, draft)

    msg = await update.message.answer(
        f"✅ {', '.join(f'`{k}={v}`' for k, v in updates.items())}",
        parse_mode="Markdown",
    )
    asyncio.create_task(_delete_after(msg, 2))

    # Refresh detail view
    msg_id = context.user_data.get("routines_msg_id")
    chat_id = context.user_data.get("routines_chat_id")
    if msg_id and chat_id:
        await _refresh_detail_msg(
            context, chat_id, msg_id, routine_name, owner_id=_starter_id(update)
        )


async def _process_daily_time(update: Update, context: Context, text: str) -> None:
    """Process custom daily time input (HH:MM)."""
    editing = context.user_data.get("routines_editing", {})
    routine_name = editing.get("routine")

    if not routine_name:
        return

    # Delete user message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Validate time format
    text = text.strip()
    try:
        hour, minute = map(int, text.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError()
        time_str = f"{hour:02d}:{minute:02d}"
    except (ValueError, AttributeError):
        msg = await update.message.answer(
            f"❌ Invalid time. Use `HH:MM` format.", parse_mode="Markdown"
        )
        asyncio.create_task(_delete_after(msg, 3))
        return

    # Create daily schedule
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)
    if not routine:
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        msg = await update.message.answer(f"❌ Config error: {e}")
        asyncio.create_task(_delete_after(msg, 3))
        return

    schedule = {"type": "daily", "daily_time": time_str}
    _create_scheduled_instance(
        context, chat_id, routine_name, draft, schedule, user_id=_starter_id(update)
    )

    msg = await update.message.answer(f"📅 Scheduled daily at {time_str}")
    asyncio.create_task(_delete_after(msg, 2))

    context.user_data["routines_state"] = "editing"

    # Refresh detail
    msg_id = context.user_data.get("routines_msg_id")
    if msg_id:
        await _refresh_detail_msg(
            context, chat_id, msg_id, routine_name, owner_id=_starter_id(update)
        )
