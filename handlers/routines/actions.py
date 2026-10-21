"""Run, start and schedule actions from the detail screen."""

import logging

from condor.telegram.types import (
    Context,
    Update,
)
from routines.base import (
    get_routine,
)

from .formatting import _format_schedule
from .jobs import _create_continuous_instance, _create_scheduled_instance
from .storage import _get_draft, _starter_id
from .views import _show_detail

logger = logging.getLogger(__name__)


async def _run_once(
    update: Update,
    context: Context,
    routine_name: str,
    background: bool = False,
) -> None:
    """Run one-shot routine once with current draft config."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    if routine.is_continuous:
        await update.callback_query.answer("Use Start for continuous routines")
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    msg_id = context.user_data.get("routines_msg_id")
    schedule = {"type": "once"}

    _create_scheduled_instance(
        context,
        chat_id,
        routine_name,
        draft,
        schedule,
        msg_id,
        background,
        user_id=_starter_id(update),
    )

    if background:
        await update.callback_query.answer("🔄 Running in background...")
        await _show_detail(update, context, routine_name)
    else:
        # Don't refresh detail view - job callback will update it with result
        await update.callback_query.answer("▶️ Running...")


async def _start_continuous(
    update: Update,
    context: Context,
    routine_name: str,
) -> None:
    """Start a continuous routine."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    if not routine.is_continuous:
        await update.callback_query.answer("Not a continuous routine")
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    instance_id = _create_continuous_instance(
        context, chat_id, routine_name, draft, user_id=_starter_id(update)
    )
    logger.info(f"Started continuous routine {instance_id}: {routine_name}")

    await update.callback_query.answer("▶️ Started")
    await _show_detail(update, context, routine_name)


async def _start_interval(
    update: Update,
    context: Context,
    routine_name: str,
    interval_sec: int,
) -> None:
    """Start one-shot routine with interval schedule."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    schedule = {"type": "interval", "interval_sec": interval_sec}
    instance_id = _create_scheduled_instance(
        context, chat_id, routine_name, draft, schedule, user_id=_starter_id(update)
    )
    logger.info(
        f"Created interval schedule {instance_id} for {routine_name}: every {interval_sec}s"
    )

    await update.callback_query.answer(f"⏱️ Scheduled: {_format_schedule(schedule)}")
    await _show_detail(update, context, routine_name)


async def _start_daily(
    update: Update,
    context: Context,
    routine_name: str,
    time_str: str,
) -> None:
    """Start routine with daily schedule."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        await update.callback_query.answer("Routine not found")
        return

    draft = _get_draft(context, routine_name)

    try:
        routine.config_class(**draft)
    except Exception as e:
        await update.callback_query.answer(f"Config error: {e}")
        return

    # Validate time format
    try:
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError()
    except (ValueError, AttributeError):
        await update.callback_query.answer(f"Invalid time: {time_str}")
        return

    schedule = {"type": "daily", "daily_time": time_str}
    instance_id = _create_scheduled_instance(
        context, chat_id, routine_name, draft, schedule, user_id=_starter_id(update)
    )
    logger.info(
        f"Created daily schedule {instance_id} for {routine_name}: at {time_str}"
    )

    await update.callback_query.answer(f"📅 Scheduled daily at {time_str}")
    await _show_detail(update, context, routine_name)
