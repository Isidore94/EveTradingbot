"""FORGE must survive an unfiltered ESI print (plan.md §17 D-22).

These fixtures were regenerated *before* the engine changed, which is the
§11 D5 rule: a detector/scoring change lands behind fixtures, never in front
of them. The real-data fixture carries the incident that started this — the
2026-08-02 FORGE day — and the two synthetic ones pin the behaviours the
real one cannot isolate: a spike that reverts, and a member returning after
a long gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from evescreener.signals.composite import (
    TURNOVER,
    build_composite,
    winsorized_member_returns,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def forge_incident() -> pd.DataFrame:
    frame = pd.read_csv(FIXTURES / "forge_outlier_2026-08-02.csv")
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    return frame


@pytest.fixture(scope="module")
def incident_meta() -> dict:
    return json.loads((FIXTURES / "forge_outlier_2026-08-02.json").read_text(encoding="utf-8"))


def _daily_moves(composite) -> pd.Series:
    return composite.frame.set_index("datetime")["close"].pct_change().dropna()


# -- 1. the real incident ---------------------------------------------------


def test_the_fixture_still_contains_the_raw_outlier_print(forge_incident, incident_meta):
    """If this ever fails, the fixture stopped carrying the thing it exists for."""
    incident = incident_meta["incident"]
    member = forge_incident[forge_incident["type_id"] == incident["type_id"]].sort_values(
        "datetime"
    )
    closes = member.set_index("datetime")["close"]
    day = pd.Timestamp(incident["date"] + " 11:00:00+00:00")
    position = list(closes.index).index(day)
    assert closes.iloc[position - 1] == pytest.approx(incident["close_before"])
    assert closes.iloc[position] == pytest.approx(incident["close_after"])
    raw_return = closes.iloc[position] / closes.iloc[position - 1] - 1.0
    assert raw_return > 2_000, "the print is a >200,000% one-day 'return'"


def test_without_the_clamp_one_member_still_moves_the_index_thousands_of_percent(
    forge_incident,
):
    """The pre-fix behaviour, reproduced on demand.

    This is what makes the clamp demonstrably the fix rather than a
    coincidence: same fixture, same engine, clamp disabled.
    """
    unclamped = build_composite(
        forge_incident, members=100, weighting=TURNOVER, return_clamp_k=None
    )
    moves = _daily_moves(unclamped)
    assert moves.max() > 10.0, "expected a >1,000% day with no clamp"
    assert str(moves.idxmax().date()) == "2026-08-02"


def test_the_clamp_holds_the_real_incident_to_a_plausible_day(forge_incident):
    """Acceptance, stated before the fix: no day beyond the clamp's reach."""
    composite = build_composite(forge_incident, members=100, weighting=TURNOVER)
    moves = _daily_moves(composite)
    largest = moves.abs().max()
    assert largest < 0.25, f"largest day {largest:.4%} is still not a market move"
    assert moves.abs().median() < 0.01, "median |daily move| must be sub-percent"


def test_the_clamped_days_are_counted_in_the_diagnostics(forge_incident):
    """Clamping is visible, never silent."""
    composite = build_composite(forge_incident, members=100, weighting=TURNOVER)
    diagnostics = composite.diagnostics
    assert diagnostics["clamped_member_days"] > 0
    assert diagnostics["measured_member_days"] > diagnostics["clamped_member_days"]
    assert 0.0 < diagnostics["clamped_share"] < 0.5
    assert diagnostics["return_clamp_k"] == 8.0


def test_power_index_against_the_fixed_composite_is_sane(forge_incident):
    """The RRS reference term is what the broken index actually poisoned.

    `power_index = Δref / ATR_ref` printed **1,478** on the live desk, which
    swamped every type's own term and left every RRS in a −1,479 band. On a
    healthy index it belongs in single digits.
    """
    from evescreener.signals.atr import atr_last

    composite = build_composite(forge_incident, members=100, weighting=TURNOVER)
    frame = composite.frame
    closes = pd.to_numeric(frame["close"]).to_numpy(dtype="float64")
    move = closes[-1] - closes[-1 - 20]
    reference_atr = atr_last(frame.iloc[:-1], length=20)
    assert reference_atr, "the composite must have a measurable ATR"
    power_index = move / reference_atr
    assert abs(power_index) < 25.0, f"power_index {power_index:,.1f} — the index is broken again"


# -- 2. spike and revert ----------------------------------------------------


def _synthetic(members: int = 8, days: int = 120, price: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01 11:00:00+00:00", periods=days, freq="D")
    rows = []
    generator = np.random.default_rng(20260820)
    for type_id in range(1, members + 1):
        walk = price * np.cumprod(1.0 + generator.normal(0.0, 0.004, days))
        for stamp, close in zip(dates, walk, strict=True):
            rows.append(
                {
                    "type_id": type_id,
                    "datetime": stamp,
                    "close": float(close),
                    "isk_value": 1_000_000.0,
                }
            )
    return pd.DataFrame(rows)


def _with_spike(frame: pd.DataFrame, multiple: float = 1000.0) -> pd.DataFrame:
    spike_day = pd.Timestamp("2026-03-01 11:00:00+00:00")
    victim = (frame["type_id"] == 1) & (frame["datetime"] == spike_day)
    frame.loc[victim, "close"] = float(frame.loc[victim, "close"].iloc[0]) * multiple
    return frame


def test_a_1000x_print_that_reverts_leaves_the_level_where_it_found_it():
    """The asymmetry that makes this fatal: +100,000% up, only −100% back.

    An arithmetic weighted-return index cannot give back what an outlier
    hands it, so "it reverts tomorrow" is not a defence — it is why the level
    ratchets. With the clamp, both legs are bounded and the level returns.
    """
    clean = build_composite(_synthetic(), members=8, single_cap=1.0, weighting=TURNOVER)
    dirty = build_composite(
        _with_spike(_synthetic()), members=8, single_cap=1.0, weighting=TURNOVER
    )

    clean_end = float(clean.frame["close"].iloc[-1])
    dirty_end = float(dirty.frame["close"].iloc[-1])
    assert dirty_end == pytest.approx(clean_end, rel=0.05), (
        f"one reverting print moved the end level {clean_end:,.2f} -> {dirty_end:,.2f}"
    )
    moves = _daily_moves(dirty)
    assert moves.abs().max() < 0.20, "the spike day itself must stay bounded"


def test_the_same_print_without_the_clamp_ratchets_the_level():
    """Guards the guard: the test above must be testing something."""
    clean = build_composite(
        _synthetic(), members=8, single_cap=1.0, weighting=TURNOVER, return_clamp_k=None
    )
    dirty = build_composite(
        _with_spike(_synthetic()),
        members=8,
        single_cap=1.0,
        weighting=TURNOVER,
        return_clamp_k=None,
    )
    assert float(dirty.frame["close"].iloc[-1]) > 10.0 * float(clean.frame["close"].iloc[-1])


# -- 3. gap reappearance ----------------------------------------------------


def test_a_member_returning_after_a_gap_does_not_book_its_whole_re_rating_in_a_day():
    """45 missing days across a 5x re-rating must not land as one return.

    pandas 3.0's `pct_change` no longer pads, but pandas 2.x did, and
    `winsorized_member_returns` computes the returns explicitly so the answer
    does not depend on which one is installed.
    """
    frame = _synthetic(days=150)
    absent = (
        (frame["type_id"] == 1)
        & (frame["datetime"] >= pd.Timestamp("2026-02-01 11:00:00+00:00"))
        & (frame["datetime"] < pd.Timestamp("2026-03-18 11:00:00+00:00"))
    )
    assert absent.sum() == 45
    returning = (frame["type_id"] == 1) & (
        frame["datetime"] >= pd.Timestamp("2026-03-18 11:00:00+00:00")
    )
    frame.loc[returning, "close"] = frame.loc[returning, "close"] * 5.0
    frame = frame[~absent]

    closes = frame.pivot_table(index="datetime", columns="type_id", values="close")
    returns, _ = winsorized_member_returns(closes, k=None)
    first_day_back = pd.Timestamp("2026-03-18 11:00:00+00:00")
    assert pd.isna(returns.loc[first_day_back, 1]), (
        "a member with no bar on t-1 must contribute nothing on t"
    )

    composite = build_composite(frame, members=8, single_cap=1.0, weighting=TURNOVER)
    moves = _daily_moves(composite)
    assert moves.abs().max() < 0.20


def test_a_zero_previous_close_is_never_divided_by():
    closes = pd.DataFrame(
        {1: [10.0, 0.0, 5.0, 6.0]},
        index=pd.date_range("2026-01-01 11:00:00+00:00", periods=4, freq="D"),
    )
    returns, _ = winsorized_member_returns(closes, k=None)
    assert pd.isna(returns.iloc[2, 0]), "0 -> 5 is not a return, it is a missing denominator"
    assert np.isfinite(returns.iloc[3, 0])


def test_an_unmeasurable_ceiling_clamps_rather_than_passing_through():
    """UNKNOWN never becomes permission (plan.md §4)."""
    closes = pd.DataFrame(
        {1: [100.0, 101.0, 100_000.0]},
        index=pd.date_range("2026-01-01 11:00:00+00:00", periods=3, freq="D"),
    )
    returns, clamped = winsorized_member_returns(closes, k=8.0, window=60, floor=0.05)
    assert bool(clamped.iloc[2, 0]), "too little history is not a reason to accept a 1000x print"
    assert returns.iloc[2, 0] == pytest.approx(0.05)
