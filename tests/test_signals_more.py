"""RRS, the Forge Composite, and the anchor calendar."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from evescreener.signals.anchors import (
    Anchor,
    anchor_index,
    append_candidate,
    applicable_anchors,
    load_anchors,
    pick_current_anchor,
    seed_anchors_into_db,
)
from evescreener.signals.composite import build_composite
from evescreener.signals.rrs import cross_sectional_percentile, real_relative_strength

REPO_ANCHORS = Path(__file__).resolve().parents[1] / "config" / "anchors.jsonl"


def trend_frame(slope: float, bars: int = 60, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(slope, 0.01, bars)))
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC"),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(bars, 1000.0),
            "order_count": np.full(bars, 50),
        }
    )


# -- RRS --------------------------------------------------------------------


def test_rrs_is_positive_when_the_type_outruns_the_benchmark():
    strong = trend_frame(0.004)
    flat = trend_frame(0.0)
    result = real_relative_strength(strong, flat, length=20)
    assert result.known
    assert result.rrs > 0


def test_rrs_is_negative_when_the_type_lags():
    weak = trend_frame(-0.004)
    flat = trend_frame(0.0)
    assert real_relative_strength(weak, flat, length=20).rrs < 0


def test_rrs_is_benchmark_agnostic():
    """Any reference series works — the SPY coupling lived in callers."""
    symbol = trend_frame(0.002)
    first = real_relative_strength(symbol, trend_frame(0.0, seed=1), length=20)
    second = real_relative_strength(symbol, trend_frame(0.0, seed=2), length=20)
    assert first.known and second.known
    assert first.rrs != second.rrs


def test_rrs_of_a_short_series_is_unknown_not_zero():
    result = real_relative_strength(trend_frame(0.0, bars=10), trend_frame(0.0), length=20)
    assert not result.known
    assert result.rrs is None
    assert "bars" in result.unknown_reason


def test_rrs_with_a_missing_benchmark_is_unknown_never_substituted():
    result = real_relative_strength(trend_frame(0.0), None, length=20)
    assert not result.known
    assert result.unknown_reason == "missing series"


def test_cross_sectional_percentile_keeps_unknowns_unknown():
    ranked = cross_sectional_percentile({1: 2.0, 2: -1.0, 3: None, 4: 0.5})
    assert ranked[1] == pytest.approx(1.0)
    assert ranked[2] == pytest.approx(1 / 3)
    assert ranked[3] is None, "an unmeasurable type must not rank as measured weakness"


def test_cross_sectional_percentile_of_all_unknowns():
    assert cross_sectional_percentile({1: None, 2: None}) == {1: None, 2: None}


# -- composite --------------------------------------------------------------


def lake_frame(type_ids, *, bars=90, turnover=1e9) -> pd.DataFrame:
    rows = []
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    rng = np.random.default_rng(11)
    for index, type_id in enumerate(type_ids):
        close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, bars)))
        for position, stamp in enumerate(stamps):
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": close[position] * 1.01,
                    "low": close[position] * 0.99,
                    "close": close[position],
                    "volume": 1000.0,
                    "order_count": 50,
                    "isk_value": turnover * (index + 1),
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    return pd.DataFrame(rows)


def test_composite_produces_a_usable_reference_series():
    composite = build_composite(lake_frame(range(10)), members=10, min_members=5)
    assert composite.known
    assert list(composite.frame.columns) == [
        "datetime",
        "high",
        "low",
        "close",
        "volume",
        "order_count",
    ]
    assert "open" not in composite.frame.columns


def test_single_type_weight_cap_stops_plex_becoming_the_market():
    # Type 9's turnover is 10x type 0's; without the cap it would dominate.
    composite = build_composite(lake_frame(range(10)), members=10, single_cap=0.10, min_members=5)
    assert composite.diagnostics["top_weight"] == pytest.approx(0.10, abs=1e-6)


def test_composite_publishes_auditable_diagnostics():
    diagnostics = build_composite(lake_frame(range(10)), members=10, min_members=5).diagnostics
    assert diagnostics["members"] == 10
    assert 0.0 <= diagnostics["weight_entropy"] <= 1.0
    assert diagnostics["rebalances"] >= 1
    assert "level_last" in diagnostics


def test_composite_is_unknown_rather_than_thin():
    result = build_composite(lake_frame(range(2)), members=10, min_members=5)
    assert not result.known
    assert "members" in result.diagnostics["reason"]


def test_composite_reweight_does_not_print_as_a_market_move():
    """Chain-linking: composition churn must not fake an index move."""
    frame = lake_frame(range(12), bars=120)
    composite = build_composite(frame, members=6, rebalance_days=30, min_members=5)
    level = composite.frame["close"]
    daily = level.pct_change().dropna()
    assert daily.abs().max() < 0.5, "no rebalance day may jump the index"


def test_empty_lake_gives_an_honest_empty_composite():
    result = build_composite(pd.DataFrame())
    assert not result.known
    assert result.diagnostics["reason"] == "no bars"


# -- anchors ----------------------------------------------------------------


def test_committed_calendar_parses_and_ships_unconfirmed():
    anchors = load_anchors(REPO_ANCHORS)
    assert anchors, "the committed anchor calendar must parse"
    assert all(not anchor.confirmed for anchor in anchors), (
        "seeded placeholder dates must not be treated as verified anchors"
    )


def test_only_confirmed_anchors_are_applicable():
    anchors = [
        Anchor(date(2026, 1, 1), "confirmed", "global", True),
        Anchor(date(2026, 2, 1), "candidate", "global", False),
    ]
    visible = applicable_anchors(anchors, as_of=date(2026, 3, 1))
    assert [anchor.label for anchor in visible] == ["confirmed"]


def test_anchors_are_point_in_time_and_never_look_forward():
    anchors = [Anchor(date(2026, 6, 1), "future", "global", True)]
    assert applicable_anchors(anchors, as_of=date(2026, 3, 1)) == []


def test_scoped_anchors_only_reach_their_own_subtree():
    anchors = [Anchor(date(2026, 1, 1), "cruiser rebalance", "100", True)]
    assert applicable_anchors(anchors, market_group_chain=[100, 4], as_of=date(2026, 3, 1))
    assert applicable_anchors(anchors, market_group_chain=[999], as_of=date(2026, 3, 1)) == []


def test_fresh_anchor_ambiguity_is_flagged_not_resolved():
    anchors = [
        Anchor(date(2026, 1, 1), "old", "global", True),
        Anchor(date(2026, 3, 5), "new", "global", True),
    ]
    picked, ambiguous = pick_current_anchor(anchors, as_of=date(2026, 3, 8), fresh_days=10)
    assert picked.label == "new"
    assert ambiguous, "a 3-day-old anchor leaves both arguably live"
    _, settled = pick_current_anchor(anchors, as_of=date(2026, 4, 1), fresh_days=10)
    assert not settled


def test_anchor_index_marks_truncation_against_the_history_horizon():
    frame = trend_frame(0.0, bars=30)
    old = Anchor(date(2020, 1, 1), "pre-history", "global", True)
    index, truncated = anchor_index(frame, old)
    assert index == 0
    assert truncated, "an anchor older than the lake must declare itself truncated"


def test_anchor_index_finds_the_bar():
    frame = trend_frame(0.0, bars=30)
    anchor = Anchor(date(2026, 1, 10), "mid", "global", True)
    index, truncated = anchor_index(frame, anchor)
    assert index == 9
    assert not truncated


def test_watcher_may_append_candidates_but_never_anchors(tmp_path):
    path = tmp_path / "anchors.jsonl"
    append_candidate(path, Anchor(date(2026, 9, 1), "rss candidate", "global", True, "rss"))
    loaded = load_anchors(path)
    assert len(loaded) == 1
    assert not loaded[0].confirmed, "an appended candidate is never auto-confirmed"
    assert applicable_anchors(loaded, as_of=date(2026, 10, 1)) == []


def test_anchors_persist_into_the_database(db):
    count = seed_anchors_into_db(db, load_anchors(REPO_ANCHORS))
    assert count > 0
    stored = db.conn.execute("SELECT COUNT(*) AS n FROM anchors").fetchone()["n"]
    assert stored == count


def test_missing_calendar_is_an_empty_calendar(tmp_path):
    assert load_anchors(tmp_path / "nope.jsonl") == []
