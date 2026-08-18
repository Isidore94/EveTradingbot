"""Time helpers. Every timestamp in this repo is tz-aware UTC (plan.md §11 D1).

EVE trades 23/7. The day boundary is daily downtime (~11:00-11:15 UTC) and the
market history endpoint's own expiry rolls at 11:05 UTC, which is what makes a
bar *completed*.
"""

from __future__ import annotations

import datetime as dt

UTC = dt.UTC

# The ESI history endpoint expires daily at this wall-clock time (§0).
HISTORY_ROLL = dt.time(11, 5, tzinfo=UTC)

# Downtime boundary that stamps a daily bar's datetime (§4).
BAR_STAMP = dt.time(11, 0, tzinfo=UTC)


def now_utc() -> dt.datetime:
    """Current instant, tz-aware UTC."""
    return dt.datetime.now(tz=UTC)


def ensure_utc(value: dt.datetime) -> dt.datetime:
    """Return ``value`` as tz-aware UTC, rejecting naive datetimes.

    Naive datetimes are a bug, not something to guess at: silently assuming a
    timezone is exactly the "uncertainty laundered into confirmation" pattern
    this repo refuses (§4).
    """
    if value.tzinfo is None:
        raise ValueError(f"naive datetime is not allowed: {value!r}")
    return value.astimezone(UTC)


def last_completed_bar_date(now: dt.datetime | None = None) -> dt.date:
    """The most recent date whose daily bar is finished and published.

    A bar dated ``D`` covers the EVE day ending at downtime on ``D+1``, and it
    is only published once history rolls at 11:05 UTC on ``D+1``. So before
    11:05 today the newest completed bar is the day before yesterday.

    Verified against live ESI on 2026-08-18 02:20 UTC: the newest history row
    for type 34 in The Forge was dated 2026-08-16, which is what this returns.
    """
    moment = ensure_utc(now) if now is not None else now_utc()
    rolled_today = moment.timetz() >= HISTORY_ROLL
    return moment.date() - dt.timedelta(days=1 if rolled_today else 2)


def bar_datetime(bar_date: dt.date) -> dt.datetime:
    """Stamp an ESI history ``date`` at the downtime boundary, tz-aware UTC."""
    return dt.datetime.combine(bar_date, BAR_STAMP)


def parse_http_date(value: str) -> dt.datetime:
    """Parse an RFC 7231 ``Expires``/``Last-Modified`` header into UTC."""
    from email.utils import parsedate_to_datetime

    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_hhmm(value: str) -> dt.time:
    """Parse a ``"HH:MM"`` config value into a tz-aware UTC time."""
    hour, _, minute = value.partition(":")
    return dt.time(int(hour), int(minute), tzinfo=UTC)
