"""§20.3 — the strongest names, ranked on completed bars only.

**A week in EVE is seven days.** §20.3 originally specified 5- and 20-bar
windows, which is the *equity* convention: five trading days is a week only
because the exchange shuts at the weekend. EVE's market never closes, so a
week is 7 completed bars and a month is 30. Ranking on 5 and 20 would have
measured five days and called it a week — a ported habit, not a decision.

The rest is the usual discipline: completed bars only, UNKNOWN when the
history is too short, stale bars price nothing, THIN badged rather than
quietly mixed, and a volume floor so a single print on a dead item cannot top
the table.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from evescreener.performers import (
    ENDPOINT_BARS,
    MONTH_BARS,
    WEEK_BARS,
    top_performers,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def series(type_id, closes, *, region_id=10000002, end="2026-08-19", fetched=None):
    days = pd.date_range(end=pd.Timestamp(end, tz="UTC"), periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "type_id": type_id,
            "region_id": region_id,
            "datetime": [day.replace(hour=11) for day in days],
            "high": np.array(closes) * 1.01,
            "low": np.array(closes) * 0.99,
            "close": np.array(closes, dtype="float64"),
            "volume": 10_000.0,
            "order_count": 30,
            "isk_value": np.array(closes) * 10_000.0,
            "fetched_at": fetched or "2026-08-20T11:30:00+00:00",
        }
    )


def flat(value, count):
    return [float(value)] * count


# -- 1. the windows are EVE weeks, not equity weeks -------------------------


def test_a_week_is_seven_completed_bars_and_a_month_is_thirty():
    """EVE's market never closes; 5 and 20 are an equity habit (§20.3)."""
    assert WEEK_BARS == 7
    assert MONTH_BARS == 30


def test_a_sustained_move_is_measured_over_exactly_seven_bars():
    closes = flat(100.0, 40)
    for index in range(len(closes) - WEEK_BARS, len(closes)):
        closes[index] = 110.0
    frame = top_performers(series(34, closes), now=NOW, min_units=0)
    row = frame.iloc[0]
    assert row["week_pct"] == pytest.approx(10.0)


def test_a_move_older_than_the_window_is_outside_it():
    closes = flat(100.0, 40)
    for index in range(len(closes) - 20, len(closes)):
        closes[index] = 110.0
    row = top_performers(series(34, closes), now=NOW, min_units=0).iloc[0]
    assert row["week_pct"] == pytest.approx(0.0), "the whole week already sits at 110"
    assert row["month_pct"] == pytest.approx(10.0)


# -- the print guard --------------------------------------------------------


def test_a_single_fat_fingered_print_does_not_become_a_week_of_performance():
    """The real lake's worst raw reading was +49,699,900% (§20.3)."""
    closes = flat(100.0, 40)
    closes[-1] = 5_000_000.0  # one absurd daily average
    row = top_performers(series(34, closes), now=NOW, min_units=0).iloc[0]
    assert row["week_pct"] == pytest.approx(0.0), "a median of three survives one print"
    assert row["week_pct_raw"] > 1_000_000, "the raw number is still reported"


def test_a_print_at_the_start_of_the_window_is_survived_too():
    closes = flat(100.0, 40)
    closes[-(WEEK_BARS + ENDPOINT_BARS)] = 0.01  # the window's far endpoint
    row = top_performers(series(34, closes), now=NOW, min_units=0).iloc[0]
    assert row["week_pct"] == pytest.approx(0.0)
    assert row["week_pct_raw"] == pytest.approx(0.0), "raw uses a different bar here"


def test_the_raw_and_robust_numbers_are_both_reported_so_they_can_disagree():
    """No threshold is invented: the operator sees two numbers that differ."""
    closes = flat(100.0, 40)
    closes[-1] = 1000.0
    row = top_performers(series(34, closes), now=NOW, min_units=0).iloc[0]
    assert "week_pct" in row and "week_pct_raw" in row
    assert row["week_pct_raw"] > row["week_pct"] * 10

    # Where the data is sound the two agree, so the guard costs nothing.
    steady = flat(100.0, 40)
    for index in range(len(steady) - WEEK_BARS, len(steady)):
        steady[index] = 120.0
    sound = top_performers(series(35, steady), now=NOW, min_units=0).iloc[0]
    assert sound["week_pct"] == pytest.approx(sound["week_pct_raw"])


def test_the_ranked_column_is_the_robust_one():
    printed = flat(100.0, 40)
    printed[-1] = 1_000_000.0  # enormous raw, zero robust
    real = flat(100.0, 40)
    for index in range(len(real) - WEEK_BARS, len(real)):
        real[index] = 150.0
    frame = top_performers(
        pd.concat([series(34, printed), series(35, real)], ignore_index=True),
        now=NOW,
        min_units=0,
    )
    assert frame["type_id"].tolist() == [35, 34], "a real move outranks a print"


def test_a_sparse_series_measures_calendar_days_not_rows():
    """The real defect: 07-22, 07-27, 07-28, 07-31 is not four consecutive days.

    Counting seven ROWS back on a thin type spans nearly a month — the same
    error §21 R5 fixed in the lead-lag study (§20.3).
    """
    days = [
        "2026-07-22",
        "2026-07-27",
        "2026-07-28",
        "2026-07-31",
        "2026-08-02",
        "2026-08-04",
        "2026-08-09",
        "2026-08-11",
        "2026-08-13",
        "2026-08-14",
        "2026-08-15",
        "2026-08-18",
    ]
    closes = [
        2400.0,
        3500.0,
        3500.0,
        2401.0,
        0.01,
        3999.0,
        2400.0,
        2400.0,
        2400.0,
        2400.0,
        2400.0,
        4970.0,
    ]
    frame = pd.DataFrame(
        {
            "type_id": 34,
            "region_id": 10000002,
            "datetime": [pd.Timestamp(day, tz="UTC").replace(hour=11) for day in days],
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 5000.0,
            "order_count": 2,
            "isk_value": 1.0,
            "fetched_at": "2026-08-20T11:30:00+00:00",
        }
    )
    row = top_performers(frame, now=NOW, min_units=0, max_bar_age_days=5).iloc[0]
    # This name traded ONCE in the three days to 2026-08-18, so there is no
    # median to take at the near endpoint and therefore no defensible return.
    # UNKNOWN is the true answer; the old code reported +49,699,900%.
    assert not np.isfinite(row["week_pct"])
    assert row["state"] == "UNKNOWN"


def test_a_two_observation_median_is_a_mean_and_is_not_print_resistant():
    """§22 S5d, reproduced: Aug 10 = 0.01, Aug 12/17/19 = 100.

    Both raw seven-day endpoints are 100, so the raw return is 0%. The ranked
    "robust" value read **+99.98%** with state OK, because the far endpoint
    window held exactly two bars and the median of two values is their
    arithmetic MEAN — which one 0.01 ISK print drags almost as far as it drags
    the raw number. Three observations is the smallest window in which a single
    bad print is outvoted.
    """
    days = ["2026-08-10", "2026-08-12", "2026-08-17", "2026-08-19"]
    closes = [0.01, 100.0, 100.0, 100.0]
    frame = pd.DataFrame(
        {
            "type_id": 34,
            "region_id": 10000002,
            "datetime": [pd.Timestamp(day, tz="UTC").replace(hour=11) for day in days],
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 5000.0,
            "order_count": 2,
            "isk_value": 1.0,
            "fetched_at": "2026-08-20T11:30:00+00:00",
        }
    )
    row = top_performers(frame, now=NOW, min_units=0).iloc[0]
    assert row["week_pct_raw"] == pytest.approx(0.0)
    assert not np.isfinite(row["week_pct"]), "two observations cannot be called robust"
    assert row["state"] == "UNKNOWN"


def test_three_observations_is_the_declared_minimum():
    from evescreener.performers import MIN_ENDPOINT_BARS

    assert MIN_ENDPOINT_BARS == 3


def test_a_lone_bar_in_an_endpoint_window_cannot_be_its_own_median():
    """A median over one observation is that observation (§20.3)."""
    days = ["2026-08-05", "2026-08-06", "2026-08-07", "2026-08-12"]
    closes = [100.0, 100.0, 100.0, 0.01]  # the last day is a print, alone
    frame = pd.DataFrame(
        {
            "type_id": 34,
            "region_id": 10000002,
            "datetime": [pd.Timestamp(day, tz="UTC").replace(hour=11) for day in days],
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 5000.0,
            "order_count": 1,
            "isk_value": 1.0,
            "fetched_at": "2026-08-20T11:30:00+00:00",
        }
    )
    row = top_performers(frame, now=NOW, min_units=0, max_bar_age_days=10).iloc[0]
    assert not np.isfinite(row["week_pct"]), "one bar cannot defend against itself"


def test_a_type_with_nothing_trading_near_the_far_endpoint_is_unknown():
    """No bar near day-7 means no measurable week, not a wrong-window number."""
    days = ["2026-07-01", "2026-07-02", "2026-08-17", "2026-08-18", "2026-08-19"]
    closes = [100.0, 100.0, 200.0, 200.0, 200.0]
    frame = pd.DataFrame(
        {
            "type_id": 34,
            "region_id": 10000002,
            "datetime": [pd.Timestamp(day, tz="UTC").replace(hour=11) for day in days],
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 5000.0,
            "order_count": 2,
            "isk_value": 1.0,
            "fetched_at": "2026-08-20T11:30:00+00:00",
        }
    )
    row = top_performers(frame, now=NOW, min_units=0).iloc[0]
    assert not np.isfinite(row["week_pct"]), "nothing traded near 2026-08-12"


# -- 2. too little history is UNKNOWN, never zero ---------------------------


def test_a_type_with_fewer_bars_than_the_window_reports_unknown():
    frame = top_performers(series(34, flat(100.0, 5)), now=NOW, min_units=0)
    row = frame.iloc[0]
    assert not np.isfinite(row["week_pct"])
    assert not np.isfinite(row["month_pct"])
    assert row["state"] == "UNKNOWN"


def test_a_type_with_a_week_but_not_a_month_reports_the_week_only():
    frame = top_performers(series(34, flat(100.0, 10)), now=NOW, min_units=0)
    row = frame.iloc[0]
    assert np.isfinite(row["week_pct"])
    assert not np.isfinite(row["month_pct"])
    assert row["state"] == "OK", "a known week is a real answer"


def test_an_empty_lake_produces_an_empty_table_not_a_zero_row():
    assert top_performers(pd.DataFrame(), now=NOW).empty


# -- 3. stale bars price nothing (§21 R2) -----------------------------------


def test_a_type_whose_bars_stopped_a_month_ago_is_stale_not_a_performer():
    stale = series(34, flat(100.0, 40), end="2026-07-01")
    frame = top_performers(stale, now=NOW, min_units=0)
    row = frame.iloc[0]
    assert row["state"] == "STALE"
    assert not np.isfinite(row["week_pct"])
    assert not np.isfinite(row["month_pct"])


def test_a_lake_that_stopped_being_refreshed_is_stale_however_recent_the_bar():
    frame = top_performers(
        series(34, flat(100.0, 40), fetched="2026-08-01T00:00:00+00:00"),
        now=NOW,
        min_units=0,
    )
    assert frame.iloc[0]["state"] == "STALE"


# -- 4. ranking, badges and the volume floor --------------------------------


def test_the_table_is_ranked_by_the_chosen_window():
    up = flat(100.0, 40)
    mild = flat(100.0, 40)
    for index in range(len(up) - WEEK_BARS, len(up)):
        up[index] = 130.0
        mild[index] = 105.0
    frame = top_performers(
        pd.concat([series(34, mild), series(35, up)], ignore_index=True),
        now=NOW,
        min_units=0,
        rank_by="week_pct",
    )
    assert frame["type_id"].tolist() == [35, 34]


def test_thin_names_are_badged_rather_than_quietly_mixed():
    frame = top_performers(
        series(34, flat(100.0, 40)),
        now=NOW,
        min_units=0,
        tiers={34: "THIN"},
    )
    assert frame.iloc[0]["tier"] == "THIN"


def test_a_volume_floor_keeps_a_single_print_off_the_top(caplog):
    """A dead item with one lucky trade is not the strongest name."""
    spike = flat(1.0, 40)
    for index in range(len(spike) - WEEK_BARS, len(spike)):
        spike[index] = 100.0  # +9,900% on nothing
    real = flat(100.0, 40)
    for index in range(len(real) - WEEK_BARS, len(real)):
        real[index] = 110.0
    frame = top_performers(
        pd.concat([series(34, spike), series(35, real)], ignore_index=True),
        now=NOW,
        volumes={34: 3.0, 35: 50_000.0},
        min_units=100.0,
    )
    assert frame["type_id"].tolist() == [35], "the 3-units/day name is excluded"


def test_the_floor_is_a_control_not_a_hidden_constant():
    spike = flat(1.0, 40)
    for index in range(len(spike) - WEEK_BARS, len(spike)):
        spike[index] = 100.0
    frame = top_performers(series(34, spike), now=NOW, volumes={34: 3.0}, min_units=0.0)
    assert len(frame) == 1, "dropping the floor shows what the floor removed"


def test_names_and_tiers_are_carried_through():
    frame = top_performers(
        series(34, flat(100.0, 40)),
        now=NOW,
        min_units=0,
        names={34: "Tritanium"},
        tiers={34: "OK"},
    )
    row = frame.iloc[0]
    assert row["name"] == "Tritanium"
    assert row["tier"] == "OK"


# -- 5. regions do not mix (§21 R8) -----------------------------------------


def test_a_frame_holding_two_regions_is_refused_rather_than_pooled():
    """Two regions' prices are two markets; ranking them together is nonsense."""
    both = pd.concat(
        [series(34, flat(100.0, 40)), series(34, flat(100.0, 40), region_id=10000043)],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="one region"):
        top_performers(both, now=NOW, min_units=0)


def test_a_single_region_frame_is_fine():
    frame = top_performers(series(34, flat(100.0, 40)), now=NOW, min_units=0)
    assert len(frame) == 1
