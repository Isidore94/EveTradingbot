"""All timestamps are tz-aware UTC; the only boundary is downtime."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from evescreener.timeutil import (
    bar_datetime,
    ensure_utc,
    eve_date,
    in_window,
    iso,
    last_completed_bar_date,
    next_daily_run,
    parse_iso,
)


def test_bar_datetime_stamps_downtime_boundary():
    moment = bar_datetime("2026-08-18")
    assert moment == datetime(2026, 8, 18, 11, 0, tzinfo=UTC)
    assert moment.tzinfo is not None


def test_eve_date_rolls_at_downtime():
    assert eve_date(datetime(2026, 8, 18, 10, 59, tzinfo=UTC)) == date(2026, 8, 17)
    assert eve_date(datetime(2026, 8, 18, 11, 1, tzinfo=UTC)) == date(2026, 8, 18)


def test_last_completed_bar_date_never_returns_a_partial_day():
    # Before the 11:05 roll the newest published bar is two days back.
    assert last_completed_bar_date(datetime(2026, 8, 20, 9, 0, tzinfo=UTC)) == date(2026, 8, 18)
    assert last_completed_bar_date(datetime(2026, 8, 20, 12, 0, tzinfo=UTC)) == date(2026, 8, 19)


def test_ensure_utc_assumes_utc_for_naive():
    assert ensure_utc(datetime(2026, 1, 1, 0, 0)).tzinfo is UTC


def test_iso_round_trips_including_z_suffix():
    moment = datetime(2026, 8, 18, 11, 5, tzinfo=UTC)
    assert parse_iso(iso(moment)) == moment
    assert parse_iso("2026-08-18T11:05:00Z") == moment


def test_next_daily_run_is_always_in_the_future():
    now = datetime(2026, 8, 18, 17, 0, tzinfo=UTC)
    assert next_daily_run(time(16, 0), now) == datetime(2026, 8, 19, 16, 0, tzinfo=UTC)
    assert next_daily_run(time(18, 0), now) == datetime(2026, 8, 18, 18, 0, tzinfo=UTC)


def test_in_window_handles_midnight_wrap():
    assert in_window(datetime(2026, 8, 18, 16, 0, tzinfo=UTC), time(15, 0), time(17, 0))
    assert not in_window(datetime(2026, 8, 18, 18, 0, tzinfo=UTC), time(15, 0), time(17, 0))
    assert in_window(datetime(2026, 8, 18, 23, 30, tzinfo=UTC), time(23, 0), time(1, 0))
