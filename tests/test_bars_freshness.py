"""R2 — completed bars at ingestion, and freshness that is not the book's.

Two contracts:

**Only completed EVE days enter the lake.** `frame_from_history` accepted every
date ESI returned. A provider or fixture supplying today's partial daily bar
could therefore confirm a signal with a day that has not finished happening.

**Bar freshness and book freshness are separate facts.** `brief.freshness` was
`"fresh" if sell_row is not None and not stale_reason` — derived entirely from
the order book. So if history ingestion failed for a week while book sweeps
kept running, a week-old signal rendered as fresh.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from evescreener.bars import bar_freshness, frame_from_history
from evescreener.timeutil import iso, last_completed_bar_date

# 2026-08-20 12:00 UTC is after the 11:05 history roll, so the newest
# completed bar is 2026-08-19.
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def history_row(day: str, *, average=100.0, volume=1000.0):
    return {
        "date": day,
        "average": average,
        "highest": average * 1.05,
        "lowest": average * 0.95,
        "volume": volume,
        "order_count": 42,
    }


# -- 1. incomplete days never enter the lake --------------------------------


def test_the_newest_completed_bar_is_the_day_before_the_roll():
    assert last_completed_bar_date(NOW).isoformat() == "2026-08-19"
    # Before the 11:05 roll the newest completed bar is a day older still.
    early = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
    assert last_completed_bar_date(early).isoformat() == "2026-08-18"


def test_ingestion_refuses_a_bar_for_a_day_that_has_not_finished():
    """Today's partial bar cannot confirm anything (§21 R2)."""
    rows = [history_row("2026-08-18"), history_row("2026-08-19"), history_row("2026-08-20")]
    frame = frame_from_history(rows, type_id=34, region_id=10000002, now=NOW)
    days = [str(pd.Timestamp(value).date()) for value in frame["datetime"]]
    assert days == ["2026-08-18", "2026-08-19"]
    assert "2026-08-20" not in days


def test_ingestion_refuses_a_future_dated_bar():
    rows = [history_row("2026-08-19"), history_row("2026-09-01")]
    frame = frame_from_history(rows, type_id=34, region_id=10000002, now=NOW)
    assert len(frame) == 1
    assert str(pd.Timestamp(frame["datetime"].iloc[0]).date()) == "2026-08-19"


def test_the_boundary_is_the_history_roll_not_midnight():
    """At 10:00 UTC the 11:05 roll has not happened, so 08-19 is not published."""
    rows = [history_row("2026-08-18"), history_row("2026-08-19")]
    early = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
    frame = frame_from_history(rows, type_id=34, region_id=10000002, now=early)
    days = [str(pd.Timestamp(value).date()) for value in frame["datetime"]]
    assert days == ["2026-08-18"]


def test_dropping_an_incomplete_bar_is_counted_not_silent():
    rows = [history_row("2026-08-19"), history_row("2026-08-20")]
    frame = frame_from_history(rows, type_id=34, region_id=10000002, now=NOW)
    assert frame.attrs["incomplete_dropped"] == 1
    assert frame.attrs["last_completed_bar_date"] == "2026-08-19"


def test_a_frame_of_only_incomplete_bars_is_empty_not_repaired():
    frame = frame_from_history([history_row("2026-08-20")], type_id=34, region_id=10000002, now=NOW)
    assert frame.empty


# -- 2. bar freshness is its own fact ---------------------------------------


def _frame(days, *, fetched_at):
    rows = [history_row(day) for day in days]
    frame = frame_from_history(rows, type_id=34, region_id=10000002, now=NOW, fetched_at=fetched_at)
    return frame


def test_a_lake_refreshed_today_with_yesterdays_bar_is_fresh():
    frame = _frame(["2026-08-18", "2026-08-19"], fetched_at=NOW)
    state = bar_freshness(frame, now=NOW, max_bar_age_days=3, max_refresh_age_hours=36)
    assert state.known
    assert not state.stale
    assert state.bar_age_days == 0
    assert state.reason == ""


def test_a_week_of_failed_history_refresh_is_stale_however_current_the_book():
    """The R2 defect: freshness was read off the book and nothing else."""
    stale_fetch = NOW - timedelta(days=7)
    frame = _frame(["2026-08-11", "2026-08-12"], fetched_at=stale_fetch)
    state = bar_freshness(frame, now=NOW, max_bar_age_days=3, max_refresh_age_hours=36)
    assert state.stale
    assert not state.known
    assert state.bar_age_days == 7
    assert "7 completed day(s) behind" in state.reason


def test_a_stale_refresh_is_stale_even_when_the_newest_bar_looks_current():
    """A lake that stopped updating still holds a bar dated yesterday."""
    frame = _frame(["2026-08-19"], fetched_at=NOW - timedelta(hours=72))
    state = bar_freshness(frame, now=NOW, max_bar_age_days=3, max_refresh_age_hours=36)
    assert state.stale
    assert "last refreshed" in state.reason


def test_an_empty_lake_is_unknown_not_fresh():
    state = bar_freshness(pd.DataFrame(), now=NOW, max_bar_age_days=3, max_refresh_age_hours=36)
    assert not state.known
    assert state.stale
    assert state.bar_age_days is None
    assert "no bars" in state.reason


def test_bar_freshness_never_consults_the_order_book():
    """Independence is the point: the two facts must not be derived from one."""
    import inspect

    from evescreener import bars

    source = inspect.getsource(bars.bar_freshness)
    for forbidden in ("sweep_ts", "sell_row", "buy_row", "best_price", "spread"):
        assert forbidden not in source, f"bar freshness must not read {forbidden!r}"


# -- 3. the screen reports both, separately ---------------------------------


def test_the_brief_carries_bar_freshness_independently_of_the_book(config):
    """A brief must be able to say 'book fresh, bars stale' (§21 R2)."""
    from evescreener.brief import TypeBrief

    brief = TypeBrief(type_id=34, type_name="Tritanium")
    assert hasattr(brief, "bar_freshness")
    assert hasattr(brief, "bar_stale_reason")
    payload = brief.as_dict()
    assert "bar_freshness" in payload
    assert "freshness" in payload, "the book's own freshness stays its own field"


def test_stale_bars_make_the_measurable_gate_unknown(config):
    """Missing or stale analytical input cannot confirm a recommendation (§4)."""
    from evescreener.brief import TypeBrief

    brief = TypeBrief(type_id=34, type_name="Tritanium")
    brief.bar_freshness = "stale"
    brief.bar_stale_reason = "bars 7 completed day(s) behind"
    brief.gates = {"measurable": "PASS", "clears_costs": "PASS"}
    brief.apply_bar_freshness()
    assert set(brief.gates.values()) == {"UNKNOWN"}, "no gate survives a stale bar"
    assert "bars 7 completed day(s) behind" in brief.flags

    # A fresh read is left exactly as it was found.
    fresh = TypeBrief(type_id=34, type_name="Tritanium")
    fresh.bar_freshness = "fresh"
    fresh.gates = {"measurable": "PASS"}
    fresh.apply_bar_freshness()
    assert fresh.gates == {"measurable": "PASS"}
    assert fresh.flags == []


def test_config_parity_treats_a_defaulted_key_as_optional():
    """R2 added optional keys; selftest must not call a valid config broken."""
    from evescreener.selftest import optional_config_keys

    optional = optional_config_keys()
    assert "screen.max_bar_age_days" in optional
    assert "screen.max_refresh_age_hours" in optional
    # A field with no default is still required, and still fails loudly.
    assert "screen.max_candidates" not in optional


def test_iso_roundtrip_of_the_refresh_stamp():
    frame = _frame(["2026-08-19"], fetched_at=NOW)
    assert frame["fetched_at"].iloc[0] == iso(NOW)
