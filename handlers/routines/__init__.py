"""Routines handler: run, schedule and monitor routine scripts from Telegram.

Storage, formatting, jobs, views, actions and input processing live in their
own modules; this package wires the Telegram handlers and re-exports the names
the rest of the application and the tests use.
"""

import logging

from condor import routine_hooks
from condor.routine_store import get_routine_store
from condor.telegram.types import Context, Update
from handlers import clear_all_input_states
from routines.base import discover_routines, get_routine, get_routine_by_state
from utils.auth import restricted

from .actions import (
    _run_once,
    _start_continuous,
    _start_daily,
    _start_interval,
)
from .formatting import (
    _config_preview,
    _display_name,
    _format_ago,
    _format_duration,
    _format_schedule,
)
from .input import (
    _process_config,
    _process_daily_time,
)
from .jobs import (
    _create_continuous_instance,
    _create_scheduled_instance,
    _daily_job_callback,
    _execute_routine,
    _find_job,
    _interval_job_callback,
    _job_context,
    _job_name,
    _JobCaller,
    _oneshot_job_callback,
    _routine_context,
    _run_continuous_routine,
    _stop_all_routine,
    _stop_instance,
    _user_bucket,
    restore_scheduled_jobs,
)
from .storage import (
    DAILY_PRESETS,
    SCHEDULE_PRESETS,
    _bind_launch_server,
    _continuous_tasks,
    _generate_instance_id,
    _get_draft,
    _get_drafts,
    _get_instances,
    _get_routine_instances,
    _owner_data,
    _set_draft,
    _starter_id,
    _sync_instance_to_store,
)
from .views import (
    _delete_after,
    _edit_or_send,
    _refresh_detail_msg,
    _share_report,
    _show_daily_menu,
    _show_detail,
    _show_help,
    _show_menu,
    _show_schedule_menu,
    _show_tasks,
)

logger = logging.getLogger(__name__)


@restricted
async def routines_command(update: Update, context: Context) -> None:
    """Handle /routines command."""
    clear_all_input_states(context)
    await _show_menu(update, context)


@restricted
async def routines_callback_handler(update: Update, context: Context) -> None:
    """Handle callback queries."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    parts = query.data.split(":")

    if len(parts) < 2:
        await query.answer()
        return

    action = parts[1]

    if action == "menu":
        await query.answer()
        context.user_data.pop("routines_state", None)
        context.user_data.pop("routines_editing", None)
        await _show_menu(update, context)

    elif action == "reload":
        await query.answer("Reloading...")
        discover_routines(force_reload=True)
        await _show_menu(update, context)

    elif action == "tasks":
        await query.answer()
        await _show_tasks(update, context)

    elif action == "select" and len(parts) >= 3:
        await query.answer()
        await _show_detail(update, context, parts[2])

    elif action == "run" and len(parts) >= 3:
        await _run_once(update, context, parts[2], background=False)

    elif action == "bg" and len(parts) >= 3:
        await _run_once(update, context, parts[2], background=True)

    elif action == "start" and len(parts) >= 3:
        # Start continuous routine
        await _start_continuous(update, context, parts[2])

    elif action == "sched" and len(parts) >= 3:
        await query.answer()
        await _show_schedule_menu(update, context, parts[2])

    elif action == "interval" and len(parts) >= 4:
        routine_name = parts[2]
        interval_sec = int(parts[3])
        await _start_interval(update, context, routine_name, interval_sec)

    elif action == "daily" and len(parts) >= 3:
        await query.answer()
        await _show_daily_menu(update, context, parts[2])

    elif action == "dailyat" and len(parts) >= 4:
        routine_name = parts[2]
        time_str = parts[3]
        await _start_daily(update, context, routine_name, time_str)

    elif action == "stop" and len(parts) >= 3:
        instance_id = parts[2]
        logger.info(f"User {chat_id} stopping instance {instance_id}")
        if _stop_instance(context, chat_id, instance_id):
            await query.answer("⏹ Stopped")
        else:
            await query.answer("Not found")
        await _show_tasks(update, context)

    elif action == "stopall" and len(parts) >= 3:
        routine_name = parts[2]
        logger.info(f"User {chat_id} stopping all instances of {routine_name}")
        count = _stop_all_routine(context, chat_id, routine_name)
        await query.answer(f"⏹ Stopped {count}")
        await _show_detail(update, context, routine_name)

    elif action == "stopall":
        logger.info(f"User {chat_id} stopping ALL instances")
        instances = _get_instances(context)
        count = 0
        for iid in list(instances.keys()):
            if _stop_instance(context, chat_id, iid):
                count += 1
        await query.answer(f"⏹ Stopped {count}")
        await _show_tasks(update, context)

    elif action == "share" and len(parts) >= 3:
        await query.answer()
        await _share_report(update, context, parts[2])

    elif action == "help" and len(parts) >= 3:
        await query.answer()
        await _show_help(update, context, parts[2])

    else:
        # Check for routine-specific callbacks
        # Pattern: routines:{routine_name}:{action}:{params...}
        routine = get_routine(action)
        if routine and routine.callback_handler and len(parts) >= 3:
            routine_action = parts[2]
            routine_params = parts[3:] if len(parts) > 3 else []
            await routine.callback_handler(
                update, context, routine_action, routine_params
            )
        else:
            await query.answer()


async def routines_message_handler(update: Update, context: Context) -> bool:
    """Handle text input for config editing, daily time, or routine-specific messages."""
    state = context.user_data.get("routines_state")

    if state == "editing":
        await _process_config(update, context, update.message.text.strip())
        return True
    elif state == "daily_time":
        await _process_daily_time(update, context, update.message.text.strip())
        return True

    # Check for routine-specific message handlers
    routine = get_routine_by_state(state)
    if routine and routine.message_handler:
        return await routine.message_handler(update, context)

    return False


__all__ = [
    "discover_routines",
    "get_routine",
    "get_routine_by_state",
    "get_routine_store",
    "routine_hooks",
    "DAILY_PRESETS",
    "SCHEDULE_PRESETS",
    "_JobCaller",
    "_bind_launch_server",
    "_config_preview",
    "_continuous_tasks",
    "_create_continuous_instance",
    "_create_scheduled_instance",
    "_daily_job_callback",
    "_delete_after",
    "_display_name",
    "_edit_or_send",
    "_execute_routine",
    "_find_job",
    "_format_ago",
    "_format_duration",
    "_format_schedule",
    "_generate_instance_id",
    "_get_draft",
    "_get_drafts",
    "_get_instances",
    "_get_routine_instances",
    "_interval_job_callback",
    "_job_context",
    "_job_name",
    "_oneshot_job_callback",
    "_owner_data",
    "_process_config",
    "_process_daily_time",
    "_refresh_detail_msg",
    "_routine_context",
    "_run_continuous_routine",
    "_run_once",
    "_set_draft",
    "_share_report",
    "_show_daily_menu",
    "_show_detail",
    "_show_help",
    "_show_menu",
    "_show_schedule_menu",
    "_show_tasks",
    "_start_continuous",
    "_start_daily",
    "_start_interval",
    "_starter_id",
    "_stop_all_routine",
    "_stop_instance",
    "_sync_instance_to_store",
    "_user_bucket",
    "restore_scheduled_jobs",
    "routines_callback_handler",
    "routines_command",
    "routines_message_handler",
]
