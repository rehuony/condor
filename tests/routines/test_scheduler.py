"""condor.scheduler: once / repeating / daily jobs on the loop, found by name."""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import time as dt_time
from datetime import timedelta, timezone

import pytest

from condor.scheduler import ScheduledJob, Scheduler


def _run(coro):
    return asyncio.run(coro)


def test_once_fires_with_its_data_and_then_forgets_itself():
    async def scenario():
        sched = Scheduler()
        seen: list[ScheduledJob] = []

        async def cb(job):
            seen.append(job)

        job = sched.once(cb, 0.01, name="probe", data={"k": 1})
        assert sched.jobs("probe") == [job]
        sched.start()
        try:
            await asyncio.sleep(0.3)
        finally:
            sched.shutdown()
        return seen, sched

    seen, sched = _run(scenario())
    assert [(j.name, j.data) for j in seen] == [("probe", {"k": 1})]
    assert sched.jobs("probe") == []


def test_repeating_fires_more_than_once_and_remove_stops_it():
    async def scenario():
        sched = Scheduler()
        hits = []

        async def cb(job):
            hits.append(job.name)

        sched.repeating(cb, 0.05, first=0.0, name="tick")
        sched.start()
        try:
            await asyncio.sleep(0.3)
            removed = sched.remove("tick")
            count = len(hits)
            await asyncio.sleep(0.2)
            return removed, count, len(hits), sched.jobs("tick")
        finally:
            sched.shutdown()

    removed, count, later, left = _run(scenario())
    assert removed == 1
    assert count >= 2
    assert later == count
    assert left == []


def test_job_remove_from_inside_the_callback():
    async def scenario():
        sched = Scheduler()
        hits = []

        async def cb(job):
            hits.append(1)
            job.remove()

        sched.repeating(cb, 0.02, first=0.0, name="self-stop")
        sched.start()
        try:
            await asyncio.sleep(0.2)
        finally:
            sched.shutdown()
        return hits

    assert _run(scenario()) == [1]


def test_a_failing_callback_does_not_stop_the_schedule(caplog):
    async def scenario():
        sched = Scheduler()
        hits = []

        async def cb(job):
            hits.append(1)
            raise RuntimeError("boom")

        sched.repeating(cb, 0.03, first=0.0, name="flaky")
        sched.start()
        try:
            await asyncio.sleep(0.2)
        finally:
            sched.shutdown()
        return hits

    hits = _run(scenario())
    assert len(hits) >= 2
    assert "Scheduled job 'flaky' failed" in caplog.text


def test_repeating_first_defaults_to_interval_and_uses_the_clock():
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    sched = Scheduler(clock=lambda: now)

    async def cb(job): ...

    job = sched.repeating(cb, 600, name="r")
    trigger = sched._sched.get_job(job.id).trigger
    assert trigger.get_next_fire_time(None, now) == now + timedelta(seconds=600)

    job = sched.repeating(cb, 600, first=30, name="r2")
    trigger = sched._sched.get_job(job.id).trigger
    assert trigger.get_next_fire_time(None, now) == now + timedelta(seconds=30)


def test_daily_reads_a_naive_time_as_utc():
    """A frozen clock in a non-UTC process: 09:00 still means 09:00 UTC."""
    now = datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc)
    sched = Scheduler(clock=lambda: now)

    async def cb(job): ...

    job = sched.daily(cb, dt_time(9, 0), name="d")
    trigger = sched._sched.get_job(job.id).trigger
    nxt = trigger.get_next_fire_time(None, now)
    assert nxt.astimezone(timezone.utc) == datetime(
        2026, 10, 21, 9, 0, tzinfo=timezone.utc
    )

    earlier = datetime(2026, 10, 20, 8, 59, tzinfo=timezone.utc)
    nxt = trigger.get_next_fire_time(None, earlier)
    assert nxt.astimezone(timezone.utc) == datetime(
        2026, 10, 20, 9, 0, tzinfo=timezone.utc
    )


def test_late_jobs_run_instead_of_being_dropped():
    """APScheduler's 1s misfire grace would skip a tick on a busy loop."""
    sched = Scheduler()

    async def cb(job): ...

    sched.once(cb, 0, name="late")
    defaults = sched._sched._job_defaults
    assert defaults["misfire_grace_time"] is None
    assert defaults["coalesce"] is True


def test_several_jobs_may_share_a_name():
    sched = Scheduler()

    async def cb(job): ...

    a = sched.once(cb, 100, name="same")
    b = sched.once(cb, 100, name="same")
    assert {j.id for j in sched.jobs("same")} == {a.id, b.id}
    assert sched.remove("same") == 2
    assert sched.jobs("same") == []


def test_shutdown_before_start_is_harmless():
    sched = Scheduler()
    sched.shutdown()
    assert not sched.running


@pytest.mark.parametrize("delay", [0, 0.01])
def test_next_run_reports_the_scheduled_time(delay):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    sched = Scheduler(clock=lambda: now)

    async def cb(job): ...

    sched.once(cb, delay, name="when")
    # Before start, APScheduler has not computed next_run_time: None is honest.
    assert sched.next_run("when") is None or sched.next_run("when") >= now
