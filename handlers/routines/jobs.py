"""Running routines: one-shot, continuous and scheduled jobs, and restoring them on startup."""

import asyncio
import logging
import time
from datetime import time as dt_time

import condor.reports
from condor import primitives, routine_hooks
from condor.routine_store import _agent_of, get_routine_store
from condor.scheduler import ScheduledJob, get_scheduler
from condor.telegram.transport import send_text
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from routines.base import (
    RoutineContext,
    get_routine,
    normalize_result,
)

from .formatting import _display_name, _format_duration, _format_schedule
from .storage import (
    _bind_launch_server,
    _continuous_tasks,
    _generate_instance_id,
    _get_instances,
    _get_routine_instances,
    _owner_data,
    _sync_instance_to_store,
)
from .views import _refresh_detail_msg

logger = logging.getLogger(__name__)


def _share_keyboard(report_id: str | None):
    if not report_id:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Share Report",
                    callback_data=f"routines:share:{report_id}",
                ),
            ]
        ]
    )


async def _send_routine_result(bot, chat_id: int, markdown: str, report_id: str | None):
    await send_text(
        bot,
        chat_id,
        markdown,
        parse_mode="Markdown",
        reply_markup=_share_keyboard(report_id),
    )


def _job_name(chat_id: int, instance_id: str) -> str:
    """Build the scheduler job name for one instance."""
    return f"routine_{chat_id}_{instance_id}"


def _find_job(context: Context, chat_id: int, instance_id: str):
    """Find a job by instance ID."""
    jobs = get_scheduler().jobs(_job_name(chat_id, instance_id))
    return jobs[0] if jobs else None


class _JobCaller:
    """The caller's side of a scheduled tick: where the application and bot are.

    A Telegram ``/routines`` run passes its ``Context`` here; a job has
    no update, so it passes this instead. Both only need ``application`` (for
    the owner's bucket) and ``bot`` (for the result message).
    """

    def __init__(self, application):
        self.application = application
        self.bot = application.bot


def _job_context(job: ScheduledJob) -> _JobCaller:
    return _JobCaller(job.data["application"])


def _routine_context(
    application,
    chat_id: int,
    instance_id: str,
    owner_key: int,
    active_server: str | None,
    *,
    bot,
    mint: bool,
) -> RoutineContext:
    """The context a routine gets from a Telegram-started run.

    ``owner_key`` names the ``user_data`` bucket the routine reads, kept
    distinct from ``chat_id`` because in a group chat they differ. ``mint``
    says whether a missing bucket is created (continuous runs, which write
    their instance there) or left as a throwaway dict (one-shot ticks).
    """
    if mint:
        user_data = _user_bucket(application, owner_key)
    else:
        user_data = _owner_data(application, owner_key, chat_id)
    # Inject the active server so group chats connect to the correct server
    # (CORR-221).
    _bind_launch_server(user_data, active_server)
    return RoutineContext(
        chat_id=chat_id,
        owner_id=owner_key,
        instance_id=instance_id,
        user_data=user_data,
        bot=bot,
    )


def _stop_instance(context: Context, chat_id: int, instance_id: str) -> bool:
    """Stop a job/task and remove instance. Returns True if found."""
    # Try to stop the scheduled job (for scheduled one-shots)
    job = _find_job(context, chat_id, instance_id)
    if job:
        job.remove()
        logger.info(f"Removed scheduled job for instance {instance_id}")

    # Try to cancel asyncio task (for continuous routines)
    task = _continuous_tasks.pop(instance_id, None)
    if task and not task.done():
        task.cancel()
        logger.info(f"Cancelled continuous task for instance {instance_id}")

    # Remove from instances
    instances = _get_instances(context)
    if instance_id in instances:
        routine_name = instances[instance_id].get("routine_name", "unknown")

        # Call routine cleanup if available
        routine = get_routine(routine_name)
        if routine and routine.cleanup_fn:
            asyncio.create_task(routine.cleanup_fn(context, instance_id, chat_id))

        del instances[instance_id]
        # Sync removal to shared store
        try:
            get_routine_store().remove_instance(instance_id)
        except Exception:
            pass
        logger.info(f"Stopped instance {instance_id} ({routine_name})")
        return True
    return False


def _stop_all_routine(context: Context, chat_id: int, routine_name: str) -> int:
    """Stop all instances of a routine. Returns count."""
    instances = _get_routine_instances(context, routine_name)
    count = 0
    for iid, _ in instances:
        if _stop_instance(context, chat_id, iid):
            count += 1
    return count


async def _execute_routine(
    context: Context | _JobCaller,
    instance_id: str,
    routine_name: str,
    config_dict: dict,
    chat_id: int,
    active_server: str | None = None,
    owner_id: int | None = None,
) -> tuple[str, float, str | None, bool]:
    """Execute a routine and return (result, duration, report_id, silent).

    ``silent`` is the routine's own ``RoutineResult.silent``: a schedule skips
    its per-run chat message for it. A failed run is never silent.

    ``chat_id`` is where the result is delivered; ``owner_id`` is whose
    ``user_data`` (and therefore whose preferences) the routine runs against.
    """
    routine = get_routine(routine_name)
    if not routine:
        return "Routine not found", 0, None, False

    start = time.time()

    # The owner, recorded explicitly: in a group chat it differs from
    # ``chat_id``, and a nested call_routine() must be attributed to the person
    # who started the run, not to the group (CORR-205).
    owner_key = owner_id if owner_id is not None else chat_id
    rctx = _routine_context(
        context.application,
        chat_id,
        instance_id,
        owner_key,
        active_server,
        bot=context.bot,
        mint=False,
    )

    try:
        # The one attribution scope every runner enters (ARCH-217). Reports
        # belong to the user whose user_data the run executes against (the
        # starter), falling back to the chat — the same identity the hooks
        # record (SEC-152) — so the web can authorize reads by it (SEC-196);
        # they name the routine's own library owner as their producer, as the
        # store runner does, so a nested call_routine() inherits it; and they
        # carry a fallback source so they stay visible on the Routines page
        # (CORR-211). The context is published so a nested call_routine() inside
        # this routine inherits its server, bot and user (FEAT-052, CORR-205).
        # ``routine_name`` is the bare library name here — agent-prefixed keys
        # never reach this path.
        with (
            condor.reports.run_scope(
                owner=owner_key,
                agent=_agent_of(routine),
                source_type="routine",
                source_name=routine_name,
            ),
            primitives.bind_context(rctx),
        ):
            config = routine.config_class(**config_dict)
            raw_result = await routine.run_fn(config, rctx)
        rich_result = normalize_result(raw_result)
        result_text = rich_result.text[:500] if rich_result.text else "Completed"
    except Exception as e:
        result_text = f"Error: {e}"
        rich_result = None
        logger.error(f"Routine {routine_name}[{instance_id}] failed: {e}")

    duration = time.time() - start
    report_id = condor.reports.get_last_report_id()

    # Bridge to shared store so web dashboard can see Telegram-triggered results
    if rich_result:
        try:
            store = get_routine_store()
            store.store_result(instance_id, rich_result)
        except Exception:
            pass

    # Fire post-execution hooks (Telegram notification).
    # On failure rich_result is None — pass the error text so the notification
    # reflects the failure instead of a misleading "Completed".
    failed = rich_result is None
    hook_result = (
        rich_result if rich_result is not None else normalize_result(result_text)
    )
    # Built outside the try so a signature mismatch surfaces instead of being
    # logged as a delivered hook — see RoutineStore._fire_hooks (CORR-175).
    hook_coro = routine_hooks.dispatch(
        routine_name,
        hook_result,
        report_id,
        failed=failed,
        bot=context.bot,
        # The chat that started the run owns its hooks — the same identity
        # user_data is keyed by, and the one the store records (SEC-152).
        owner_id=chat_id,
    )
    try:
        await hook_coro
    except Exception as e:
        logger.error(
            f"Post-execution hooks failed for {routine_name}[{instance_id}]: {e}"
        )

    silent = bool(rich_result and rich_result.silent)
    return result_text, duration, report_id, silent


def _user_bucket(application, owner_key: int) -> dict:
    """The ``user_data`` bucket for ``owner_key``, minted when absent."""
    buckets = application.user_data
    if isinstance(buckets, dict):
        return buckets.setdefault(owner_key, {})
    return buckets[owner_key]


async def _run_continuous_routine(
    application,
    instance_id: str,
    routine_name: str,
    config_dict: dict,
    chat_id: int,
    active_server: str | None = None,
    owner_id: int | None = None,
) -> None:
    """Run a continuous routine as an asyncio task.

    ``chat_id`` addresses the messages; ``owner_id`` keys the ``user_data`` the
    routine reads and the instance bucket cleaned up when the task ends.
    """
    routine = get_routine(routine_name)
    if not routine:
        logger.error(f"Routine {routine_name} not found")
        return

    owner_key = owner_id if owner_id is not None else chat_id

    context = _routine_context(
        application,
        chat_id,
        instance_id,
        owner_key,
        active_server,
        bot=application.bot,
        mint=True,
    )

    try:
        logger.info(f"Starting continuous routine {routine_name}[{instance_id}]")
        # The same scope one-shot runs enter (ARCH-217): reports this loop saves
        # belong to the user the instance runs for (SEC-196), name the routine's
        # library owner as their producer, and carry the routine as their source
        # even without an explicit .source() (CORR-211).
        with (
            condor.reports.run_scope(
                owner=owner_key,
                agent=_agent_of(routine),
                source_type="routine",
                source_name=routine_name,
            ),
            primitives.bind_context(context),
        ):
            config = routine.config_class(**config_dict)
            result = await routine.run_fn(config, context)
        logger.info(f"Continuous routine {routine_name}[{instance_id}] ended: {result}")
    except asyncio.CancelledError:
        logger.info(f"Continuous routine {routine_name}[{instance_id}] cancelled")
    except Exception as e:
        logger.error(f"Continuous routine {routine_name}[{instance_id}] error: {e}")

    # Clean up instance when task ends
    instances = _owner_data(application, owner_id, chat_id).get("routine_instances", {})
    if instance_id in instances:
        del instances[instance_id]
    try:
        get_routine_store().remove_instance(instance_id)
    except Exception:
        pass


async def _interval_job_callback(job: ScheduledJob) -> None:
    """Job callback for interval-scheduled one-shot routines. Sends message each run."""
    context = _job_context(job)
    data = job.data
    instance_id = data["instance_id"]
    routine_name = data["routine_name"]
    config_dict = data["config_dict"]
    chat_id = data["chat_id"]
    owner_id = data.get("user_id")

    # Check if instance still exists (may have been stopped)
    instances = _owner_data(context.application, owner_id, chat_id).get(
        "routine_instances", {}
    )
    if instance_id not in instances:
        logger.warning(f"Instance {instance_id} no longer exists, skipping execution")
        return

    result, duration, report_id, silent = await _execute_routine(
        context,
        instance_id,
        routine_name,
        config_dict,
        chat_id,
        active_server=data.get("active_server"),
        owner_id=owner_id,
    )

    # Re-check instance exists after execution (may have been stopped during run)
    instances = _owner_data(context.application, owner_id, chat_id).get(
        "routine_instances", {}
    )
    if instance_id not in instances:
        logger.warning(f"Instance {instance_id} was removed during execution")
        return

    instances[instance_id]["last_run_at"] = time.time()
    instances[instance_id]["last_result"] = result
    instances[instance_id]["last_duration"] = duration
    run_count = instances[instance_id].get("run_count", 0) + 1
    instances[instance_id]["run_count"] = run_count

    # Sync to shared store
    _sync_instance_to_store(instance_id, instances[instance_id])

    if silent:
        logger.info(f"Routine {routine_name}[{instance_id}] run #{run_count} (silent)")
        return

    # Send result message for scheduled one-shot routines
    schedule = instances[instance_id].get("schedule", {})
    interval_str = _format_schedule(schedule)
    icon = "✅" if not result.startswith("Error") else "❌"
    markdown = (
        f"{icon} **{_display_name(routine_name)}** `{instance_id}`\n"
        f"{interval_str} · Run #{run_count} · {_format_duration(duration)}\n\n"
        f"{result}"
    )
    try:
        await _send_routine_result(context.bot, chat_id, markdown, report_id)
    except Exception as e:
        logger.error(f"Failed to send interval result: {e}")

    logger.info(
        f"Routine {routine_name}[{instance_id}] run #{run_count}: {result[:50]}..."
    )


async def _oneshot_job_callback(job: ScheduledJob) -> None:
    """Job callback for one-time runs."""
    context = _job_context(job)
    data = job.data
    instance_id = data["instance_id"]
    routine_name = data["routine_name"]
    config_dict = data["config_dict"]
    chat_id = data["chat_id"]
    msg_id = data.get("msg_id")
    background = data.get("background", False)
    owner_id = data.get("user_id")

    result, duration, report_id, silent = await _execute_routine(
        context,
        instance_id,
        routine_name,
        config_dict,
        chat_id,
        active_server=data.get("active_server"),
        owner_id=owner_id,
    )

    # Remove one-shot instance after completion
    instances = _owner_data(context.application, owner_id, chat_id).get(
        "routine_instances", {}
    )
    if instance_id in instances:
        del instances[instance_id]
        try:
            get_routine_store().remove_instance(instance_id)
        except Exception:
            pass

    if background:
        # Send result as new message
        icon = "✅" if not result.startswith("Error") else "❌"
        markdown = (
            f"{icon} **{_display_name(routine_name)}**\n"
            f"Duration: {_format_duration(duration)}\n\n"
            f"{result}"
        )
        try:
            await _send_routine_result(context.bot, chat_id, markdown, report_id)
        except Exception as e:
            logger.error(f"Failed to send result: {e}")
    elif msg_id:
        # Update the detail view
        await _refresh_detail_msg(
            context,
            chat_id,
            msg_id,
            routine_name,
            result,
            duration,
            report_id,
            owner_id=owner_id,
        )


async def _daily_job_callback(job: ScheduledJob) -> None:
    """Job callback for daily-scheduled routines."""
    context = _job_context(job)
    data = job.data
    instance_id = data["instance_id"]
    routine_name = data["routine_name"]
    config_dict = data["config_dict"]
    chat_id = data["chat_id"]
    owner_id = data.get("user_id")

    result, duration, report_id, silent = await _execute_routine(
        context,
        instance_id,
        routine_name,
        config_dict,
        chat_id,
        active_server=data.get("active_server"),
        owner_id=owner_id,
    )

    # Update instance state
    instances = _owner_data(context.application, owner_id, chat_id).get(
        "routine_instances", {}
    )
    if instance_id in instances:
        instances[instance_id]["last_run_at"] = time.time()
        instances[instance_id]["last_result"] = result
        instances[instance_id]["last_duration"] = duration
        instances[instance_id]["run_count"] = (
            instances[instance_id].get("run_count", 0) + 1
        )
        # Sync to shared store
        _sync_instance_to_store(instance_id, instances[instance_id])

    if silent:
        return

    # Send notification
    icon = "✅" if not result.startswith("Error") else "❌"
    markdown = f"{icon} **Daily: {_display_name(routine_name)}**\n\n{result}"
    try:
        await _send_routine_result(context.bot, chat_id, markdown, report_id)
    except Exception as e:
        logger.error(f"Failed to send daily result: {e}")


def _create_scheduled_instance(
    context: Context,
    chat_id: int,
    routine_name: str,
    config_dict: dict,
    schedule: dict,
    msg_id: int | None = None,
    background: bool = False,
    user_id: int | None = None,
) -> str:
    """Create a scheduled one-shot instance. Returns instance_id.

    ``user_id`` is the Telegram user who started it — the owner the dashboard
    checks (SEC-150). It is not ``chat_id``: in a group those differ, and the
    run belongs to the person, not to the room.
    """
    instance_id = _generate_instance_id()
    job_name_str = _job_name(chat_id, instance_id)

    # Store instance
    instances = _get_instances(context)
    inst_meta = {
        "routine_name": routine_name,
        "config": config_dict.copy(),
        "schedule": schedule.copy(),
        "status": "running",
        "source": "telegram",
        "user_id": user_id,
        "created_at": time.time(),
        "last_run_at": None,
        "last_result": None,
        "last_duration": None,
        "run_count": 0,
    }
    instances[instance_id] = inst_meta

    # Sync to shared store for web visibility
    try:
        get_routine_store().add_instance(instance_id, inst_meta.copy())
    except Exception:
        pass

    # Capture the active server at creation time so routines in groups
    # connect to the correct server (user_data is keyed by user_id,
    # not chat_id, so group chats can't look it up later).
    active_server = None
    try:
        from config_manager import get_effective_server

        active_server = get_effective_server(chat_id, context.user_data)
    except Exception:
        pass

    job_data = {
        "instance_id": instance_id,
        "routine_name": routine_name,
        "config_dict": config_dict.copy(),  # Copy to isolate from draft changes
        "chat_id": chat_id,
        # Carried for the same reason as active_server: the tick has no update
        # to derive it from, and user_data is keyed by user_id, not chat_id.
        "user_id": user_id,
        "msg_id": msg_id,
        "background": background,
        "active_server": active_server,
        # In memory only: the tick builds its run context from this.
        "application": context.application,
    }

    stype = schedule.get("type", "once")
    scheduler = get_scheduler()

    if stype == "once":
        scheduler.once(_oneshot_job_callback, 0.1, name=job_name_str, data=job_data)
    elif stype == "interval":
        interval = schedule.get("interval_sec", 60)
        scheduler.repeating(
            _interval_job_callback,
            interval,
            first=0.5,
            name=job_name_str,
            data=job_data,
        )
    elif stype == "daily":
        time_str = schedule.get("daily_time", "09:00")
        hour, minute = map(int, time_str.split(":"))
        scheduler.daily(
            _daily_job_callback,
            dt_time(hour=hour, minute=minute),
            name=job_name_str,
            data=job_data,
        )

    return instance_id


def _create_continuous_instance(
    context: Context,
    chat_id: int,
    routine_name: str,
    config_dict: dict,
    user_id: int | None = None,
) -> str:
    """Create a continuous routine instance. Returns instance_id.

    ``user_id`` is the starting Telegram user (the dashboard owner), which in a
    group chat is not ``chat_id``.
    """
    instance_id = _generate_instance_id()

    # Capture active server at creation time (same fix as scheduled instances)
    active_server = None
    try:
        from config_manager import get_effective_server

        active_server = get_effective_server(chat_id, context.user_data)
    except Exception:
        pass

    # Store instance
    instances = _get_instances(context)
    inst_meta = {
        "routine_name": routine_name,
        "config": config_dict.copy(),
        "schedule": {"type": "continuous"},
        "status": "running",
        "source": "telegram",
        "user_id": user_id,
        "created_at": time.time(),
        "last_run_at": None,
        "last_result": None,
        "last_duration": None,
        "run_count": 0,
    }
    instances[instance_id] = inst_meta

    # Sync to shared store for web/MCP visibility
    try:
        get_routine_store().add_instance(instance_id, inst_meta.copy())
    except Exception:
        pass

    # Create and store asyncio task (copy config to isolate from draft changes)
    frozen_config = config_dict.copy()
    task = asyncio.create_task(
        _run_continuous_routine(
            context.application,
            instance_id,
            routine_name,
            frozen_config,
            chat_id,
            active_server=active_server,
            owner_id=user_id,
        )
    )
    _continuous_tasks[instance_id] = task

    return instance_id


async def restore_scheduled_jobs(application) -> int:
    """
    Restore scheduled jobs from persisted instances after bot restart.
    Call this during application startup (post_init).
    Returns count of restored jobs.
    """
    restored = 0
    removed = 0

    for chat_id, user_data in application.user_data.items():
        instances = user_data.get("routine_instances", {})
        if not instances:
            continue

        to_remove = []

        for instance_id, inst in instances.items():
            if inst.get("status") != "running":
                continue

            # Adopt instances persisted before owners were stamped: the bot keys
            # ``user_data`` by user id, so the bucket holding the instance is
            # its starter — a fact, not a guess. Anything still ownerless after
            # this stays admin-only in the dashboard (SEC-150 fails closed).
            if not inst.get("user_id"):
                inst["user_id"] = chat_id

            # The bucket this instance was found in is its owner, so this is
            # the key every restored job must read itself back through.
            owner_id = inst.get("user_id") or chat_id

            routine_name = inst.get("routine_name")
            config_dict = inst.get("config", {})
            schedule = inst.get("schedule", {})
            stype = schedule.get("type", "once")

            # Check if routine still exists
            routine = get_routine(routine_name)
            if not routine:
                logger.warning(
                    f"Routine {routine_name} no longer exists, removing instance {instance_id}"
                )
                to_remove.append(instance_id)
                continue

            # One-time jobs that didn't complete - remove them
            if stype == "once":
                to_remove.append(instance_id)
                continue

            # Resolve server for this chat's routines
            _active_server = None
            try:
                from config_manager import get_effective_server

                _active_server = get_effective_server(chat_id, user_data)
            except Exception:
                pass

            # Continuous routines need to be restarted as asyncio tasks
            if stype == "continuous":
                try:
                    task = asyncio.create_task(
                        _run_continuous_routine(
                            application,
                            instance_id,
                            routine_name,
                            config_dict,
                            chat_id,
                            active_server=_active_server,
                            owner_id=owner_id,
                        )
                    )
                    _continuous_tasks[instance_id] = task
                    _sync_instance_to_store(instance_id, inst)
                    restored += 1
                    logger.info(
                        f"Restored continuous routine {instance_id}: {routine_name}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to restore continuous routine {instance_id}: {e}"
                    )
                    to_remove.append(instance_id)
                continue

            # Re-create scheduled jobs
            job_name_str = _job_name(chat_id, instance_id)
            job_data = {
                "instance_id": instance_id,
                "routine_name": routine_name,
                "config_dict": config_dict,
                "chat_id": chat_id,
                "user_id": owner_id,
                "active_server": _active_server,
                "application": application,
            }
            scheduler = get_scheduler()

            try:
                if stype == "interval":
                    interval = schedule.get("interval_sec", 60)
                    scheduler.repeating(
                        _interval_job_callback,
                        interval,
                        first=min(interval, 10),
                        name=job_name_str,
                        data=job_data,
                    )
                    _sync_instance_to_store(instance_id, inst)
                    restored += 1
                    logger.info(
                        f"Restored interval job {instance_id} for {routine_name} (every {interval}s)"
                    )

                elif stype == "daily":
                    time_str = schedule.get("daily_time", "09:00")
                    hour, minute = map(int, time_str.split(":"))
                    scheduler.daily(
                        _daily_job_callback,
                        dt_time(hour=hour, minute=minute),
                        name=job_name_str,
                        data=job_data,
                    )
                    _sync_instance_to_store(instance_id, inst)
                    restored += 1
                    logger.info(
                        f"Restored daily job {instance_id} for {routine_name} (at {time_str})"
                    )

                else:
                    to_remove.append(instance_id)

            except Exception as e:
                logger.error(f"Failed to restore job {instance_id}: {e}")
                to_remove.append(instance_id)

        # Clean up stale instances
        for instance_id in to_remove:
            del instances[instance_id]
            removed += 1

    if restored > 0 or removed > 0:
        logger.info(f"Routine jobs: restored {restored}, removed {removed} stale")

    return restored
