"""Timed work: one-shots, repeating jobs and daily jobs on the event loop.

A thin, project-shaped layer over APScheduler's ``AsyncIOScheduler``. It has
nothing to do with Telegram, which is the point: scheduled routines, update
checks and the reflection sweep keep running whether or not a bot is polling,
and the callback signature does not change with the chat library.

Every callback is ``async def cb(job: ScheduledJob)``; ``job.data`` is
whatever the scheduler was handed, in memory only (nothing here is pickled,
jobs are rebuilt from the user state at boot). Several jobs may share a
``name``; that is how a routine instance's job is found and removed.

Times are UTC. ``daily`` takes a naive ``time`` and reads it as UTC, which is
what the previous scheduler did and what the stored schedules assume.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dt_time
from datetime import timedelta, timezone
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)

Callback = Callable[["ScheduledJob"], Awaitable[Any]]


@dataclass
class ScheduledJob:
    """What a callback gets: the job's name, its data, and a way to end it."""

    name: str
    data: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _scheduler: "Scheduler | None" = field(default=None, repr=False, compare=False)

    def remove(self) -> None:
        """Stop this job from running again (a no-op if it already ended)."""
        if self._scheduler is not None:
            self._scheduler._remove_id(self.id)


class Scheduler:
    """One asyncio scheduler for the process; see :func:`get_scheduler`."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None):
        # Late jobs run rather than being dropped: the loop can be busy for
        # more than APScheduler's one-second default grace, and a missed
        # interval tick is folded into one run instead of a burst.
        self._sched = AsyncIOScheduler(
            timezone=timezone.utc,
            job_defaults={"misfire_grace_time": None, "coalesce": True},
        )
        self._jobs: dict[str, ScheduledJob] = {}
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- lifecycle --------------------------------------------------------

    @property
    def running(self) -> bool:
        return bool(self._sched.running)

    def start(self) -> None:
        """Start ticking. Needs a running event loop; jobs added before start are kept."""
        if not self._sched.running:
            self._sched.start()

    def shutdown(self) -> None:
        """Stop ticking; jobs already running on the loop finish on their own."""
        if self._sched.running:
            self._sched.shutdown(wait=False)
        self._jobs.clear()

    # -- scheduling -------------------------------------------------------

    def once(
        self, cb: Callback, delay: float, *, name: str, data: dict | None = None
    ) -> ScheduledJob:
        """Run ``cb`` once, ``delay`` seconds from now."""
        trigger = DateTrigger(run_date=self._clock() + timedelta(seconds=delay))
        return self._add(cb, trigger, name, data)

    def repeating(
        self,
        cb: Callback,
        interval: float,
        *,
        first: float | None = None,
        name: str,
        data: dict | None = None,
    ) -> ScheduledJob:
        """Run ``cb`` every ``interval`` seconds, the first time after ``first`` seconds.

        ``first`` defaults to ``interval``.
        """
        start = self._clock() + timedelta(seconds=interval if first is None else first)
        trigger = IntervalTrigger(seconds=interval, start_date=start)
        return self._add(cb, trigger, name, data)

    def daily(
        self, cb: Callback, time_utc: dt_time, *, name: str, data: dict | None = None
    ) -> ScheduledJob:
        """Run ``cb`` every day at ``time_utc`` (a naive time, read as UTC)."""
        trigger = CronTrigger(
            hour=time_utc.hour,
            minute=time_utc.minute,
            second=time_utc.second,
            timezone=timezone.utc,
        )
        return self._add(cb, trigger, name, data)

    # -- lookup -----------------------------------------------------------

    def jobs(self, name: str) -> list[ScheduledJob]:
        """Every live job scheduled under ``name``."""
        return [job for job in self._jobs.values() if job.name == name]

    def remove(self, name: str) -> int:
        """Remove every job under ``name``; returns how many there were."""
        found = self.jobs(name)
        for job in found:
            self._remove_id(job.id)
        return len(found)

    def next_run(self, name: str) -> datetime | None:
        """When the first job under ``name`` runs next, if it is still scheduled."""
        for job in self.jobs(name):
            scheduled = self._sched.get_job(job.id)
            if scheduled is not None:
                return getattr(scheduled, "next_run_time", None)
        return None

    # -- internals --------------------------------------------------------

    def _add(self, cb: Callback, trigger, name: str, data: dict | None) -> ScheduledJob:
        job = ScheduledJob(name=name, data=dict(data or {}), _scheduler=self)
        self._jobs[job.id] = job
        self._sched.add_job(self._fire, trigger, args=(cb, job), id=job.id, name=name)
        return job

    def _remove_id(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
        try:
            self._sched.remove_job(job_id)
        except Exception:  # already ran out (a one-shot) or never existed
            pass

    async def _fire(self, cb: Callback, job: ScheduledJob) -> None:
        if job.id not in self._jobs:
            return  # removed after this run was already dispatched
        try:
            await cb(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduled job %r failed", job.name)
        finally:
            # A one-shot is gone from APScheduler once it fired; forget it too.
            if self._sched.get_job(job.id) is None:
                self._jobs.pop(job.id, None)


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    """The process-wide scheduler, created on first use."""
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler
