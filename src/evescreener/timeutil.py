"""UTC-only time helpers.

EVE time *is* UTC and every internal timestamp is tz-aware UTC (plan.md §11
D1). The only session boundary that exists is daily downtime at ~11:00 UTC;
market history rolls at 11:05 UTC. There is no market calendar, no session
machinery, and nothing in this module may grow one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

DOWNTIME_HOUR_UTC = 11
HISTORY_ROLL_MINUTE_UTC = 5


def utcnow() -> datetime:
    """Current time, tz-aware UTC."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return `value` as tz-aware UTC; a naive datetime is *assumed* UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


ESI_COMPATIBILITY_CLOCK_OFFSET_HOURS = 11
"""CCP evaluates `X-Compatibility-Date` against a UTC-11 clock, not UTC."""


def esi_compatibility_today(moment: datetime | None = None) -> date:
    """The date CCP's compatibility clock reads at `moment`.

    Measured against live ESI on 2026-08-18 (branch
    `claude/phase-0-gate-checklist-oucoil`, commit a7f5872): a pin of
    `2026-08-18` was rejected on every route with
    `HTTP 400 {"error":"Compatibility date (2026-08-18) is in the future.
    Current date (UTC-11) is 2026-08-17."}`. A date is only sendable once it
    has passed on *this* clock, which lags UTC by eleven hours.
    """
    moment = ensure_utc(moment or utcnow())
    return (moment - timedelta(hours=ESI_COMPATIBILITY_CLOCK_OFFSET_HOURS)).date()


def bar_datetime(day: date | str) -> datetime:
    """The canonical tz-aware timestamp of an ESI history date.

    ESI dates are labelled by the day; the bar's boundary is downtime, so the
    lake stamps every bar at 11:00 UTC on its own date (plan.md §4).
    """
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return datetime.combine(day, time(DOWNTIME_HOUR_UTC, 0), tzinfo=UTC)


def eve_date(moment: datetime | None = None) -> date:
    """The EVE market day that `moment` belongs to.

    Before downtime the completed-bar day is still yesterday's.
    """
    moment = ensure_utc(moment or utcnow())
    if moment.hour < DOWNTIME_HOUR_UTC:
        return (moment - timedelta(days=1)).date()
    return moment.date()


def last_completed_bar_date(moment: datetime | None = None) -> date:
    """Newest date whose bar is complete and published.

    History expires daily at 11:05 UTC; before that the newest published bar is
    the day before yesterday's roll. Completed bars only — never a partial day.
    """
    moment = ensure_utc(moment or utcnow())
    roll = moment.replace(
        hour=DOWNTIME_HOUR_UTC, minute=HISTORY_ROLL_MINUTE_UTC, second=0, microsecond=0
    )
    reference = moment if moment >= roll else moment - timedelta(days=1)
    return (reference - timedelta(days=1)).date()


def parse_hhmm(text: str) -> time:
    """Parse a `HH:MM` config value into a UTC-naive wall time."""
    hour, _, minute = text.partition(":")
    return time(int(hour), int(minute or 0))


def next_daily_run(at: time, moment: datetime | None = None) -> datetime:
    """Next tz-aware UTC datetime at which wall time `at` occurs."""
    moment = ensure_utc(moment or utcnow())
    candidate = moment.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= moment:
        candidate += timedelta(days=1)
    return candidate


def in_window(moment: datetime, start: time, end: time) -> bool:
    """Is `moment`'s wall clock inside [start, end)? Wraps past midnight."""
    current = ensure_utc(moment).timetz().replace(tzinfo=None)
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def iso(moment: datetime | None) -> str | None:
    """ISO-8601 rendering used everywhere a timestamp is written or displayed."""
    if moment is None:
        return None
    return ensure_utc(moment).isoformat(timespec="seconds")


def parse_iso(text: str | None) -> datetime | None:
    """Inverse of `iso`, tolerant of the `Z` suffix ESI uses."""
    if not text:
        return None
    return ensure_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
