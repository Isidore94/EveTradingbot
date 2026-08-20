"""The asyncio daemon that owns every cadence — plan.md §3.2, §11 D3.

One process, one scheduler, no writer leases. Each job is individually
try/excepted so one failing feed never takes the others down, and a failure
never destroys the last verified output (the source repo's failed-publish
invariant, kept).

The cadences it owns, all UTC:

| job | when |
|---|---|
| history ingest | daily 11:20 (after the 11:05 roll and downtime) |
| universe refresh | daily 11:00 |
| Forge book sweep | every cache window inside the 15:00–17:00 HOT window; hourly otherwise |
| secondary hubs | hourly (WARM) |
| digest | daily 16:00 |
| paper mark | daily, with the digest |
| killmail poll | every 5 minutes (R2Z2) |
| patch-notes watcher | daily, with the universe refresh |

Nothing here fetches before `Expires`; the client refuses to, and the schedule
simply keeps the process from asking pointlessly.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import Config
from .timeutil import ensure_utc, in_window, iso, next_daily_run, parse_hhmm, utcnow

__all__ = ["Job", "Scheduler", "build_jobs", "run_daemon"]


@dataclass(slots=True)
class Job:
    """One scheduled unit of work with its own next-run clock."""

    name: str
    run: object
    interval: timedelta | None = None
    daily_at: str | None = None
    hot_window: tuple[str, str] | None = None
    hot_interval: timedelta | None = None
    next_run: datetime | None = None
    last_run: datetime | None = None
    last_error: str | None = None
    runs: int = 0
    failures: int = 0

    def schedule(self, now: datetime) -> datetime:
        """When should this job next fire? Pure — the caller assigns it."""
        if self.daily_at is not None:
            return next_daily_run(parse_hhmm(self.daily_at), now)
        interval = self.interval or timedelta(hours=1)
        if self.hot_window and self.hot_interval:
            start, end = (parse_hhmm(value) for value in self.hot_window)
            if in_window(now, start, end):
                interval = self.hot_interval
        return now + interval


@dataclass(slots=True)
class Scheduler:
    """A tiny cooperative scheduler. Deterministic, so it is testable offline."""

    jobs: list[Job] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)

    def prime(self, now: datetime) -> None:
        for job in self.jobs:
            if job.next_run is None:
                # Interval jobs run once at start-up so a cold process has data;
                # daily jobs wait for their hour rather than firing on boot.
                job.next_run = now if job.daily_at is None else job.schedule(now)

    def due(self, now: datetime) -> list[Job]:
        return [job for job in self.jobs if job.next_run is not None and job.next_run <= now]

    async def tick(self, now: datetime | None = None) -> list[dict]:
        """Run everything due. One job's failure never stops the others."""
        now = ensure_utc(now or utcnow())
        self.prime(now)
        outcomes: list[dict] = []
        for job in self.due(now):
            entry = {"job": job.name, "at": iso(now)}
            try:
                result = job.run()
                if asyncio.iscoroutine(result):
                    result = await result
                job.runs += 1
                job.last_error = None
                entry["ok"] = True
                entry["result"] = _summarize(result)
            except Exception as exc:  # noqa: BLE001 - a daemon never dies of one job
                job.failures += 1
                job.last_error = f"{type(exc).__name__}: {exc}"
                entry["ok"] = False
                entry["error"] = job.last_error
            job.last_run = now
            job.next_run = job.schedule(now)
            outcomes.append(entry)
            self.log.append(entry)
        return outcomes

    def status(self) -> list[dict]:
        return [
            {
                "job": job.name,
                "next_run": iso(job.next_run),
                "last_run": iso(job.last_run),
                "runs": job.runs,
                "failures": job.failures,
                "last_error": job.last_error,
            }
            for job in self.jobs
        ]


def _summarize(result) -> object:
    for attribute in ("as_dict", "asdict"):
        method = getattr(result, attribute, None)
        if callable(method):
            return method()
    if isinstance(result, dict | list | str | int | float | bool) or result is None:
        return result
    return repr(result)[:200]


def build_jobs(config: Config, handlers: dict) -> list[Job]:
    """Wire the locked §11 D3 cadences to the supplied handlers.

    `handlers` maps job name -> callable. A name with no handler is simply not
    scheduled, so a partial daemon (say, no Discord webhook) still runs.
    """
    cadence = config.cadence
    definitions = [
        Job(
            name="universe",
            run=handlers.get("universe"),
            daily_at=cadence.universe_refresh_utc,
        ),
        Job(
            name="history",
            run=handlers.get("history"),
            daily_at=cadence.history_job_utc,
        ),
        Job(
            name="books_home",
            run=handlers.get("books_home"),
            interval=timedelta(minutes=cadence.book_cold_interval_minutes),
            hot_window=(cadence.book_hot_start_utc, cadence.book_hot_end_utc),
            # Inside the HOT window: one sweep per cache generation (~5 min).
            hot_interval=timedelta(minutes=5),
        ),
        Job(
            name="books_secondary",
            run=handlers.get("books_secondary"),
            interval=timedelta(minutes=cadence.secondary_hub_interval_minutes),
        ),
        Job(name="digest", run=handlers.get("digest"), daily_at=cadence.digest_utc),
        Job(
            name="killmails",
            run=handlers.get("killmails"),
            interval=timedelta(seconds=cadence.killmail_poll_interval_seconds),
        ),
        # The patch-notes tripwire (§9 R9). It appends candidates only; nothing
        # in this daemon can confirm an anchor.
        Job(
            name="patch_notes",
            run=handlers.get("patch_notes"),
            daily_at=cadence.universe_refresh_utc,
        ),
    ]
    return [job for job in definitions if job.run is not None]


async def run_daemon(
    config: Config,
    handlers: dict,
    *,
    stop_after: int | None = None,
    poll_seconds: float = 15.0,
    sleep=asyncio.sleep,
    now=utcnow,
    on_tick=None,
) -> Scheduler:
    """Run the scheduler until cancelled (or `stop_after` ticks, for tests)."""
    scheduler = Scheduler(jobs=build_jobs(config, handlers))
    ticks = 0
    with contextlib.suppress(asyncio.CancelledError):
        while stop_after is None or ticks < stop_after:
            outcomes = await scheduler.tick(now())
            if on_tick is not None:
                on_tick(outcomes, scheduler)
            ticks += 1
            await sleep(poll_seconds)
    return scheduler
