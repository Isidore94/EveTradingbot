"""Universe floors and the census opportunity map."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evescreener.bars import frame_from_history
from evescreener.census import derive_floor, score_floor_grid
from evescreener.sde import cohort_scope, market_group_members, resolve_watchlist
from evescreener.store.lake import BarLake
from evescreener.universe import (
    apply_floor,
    dropped_type_ids,
    liquidity_table,
    seed_watchlist,
    sync_universe,
    tracked_type_ids,
)


def synth_history(type_id: int, *, close: float, volume: float, order_count: int, days: int = 40):
    rows = []
    for offset in range(days):
        day = pd.Timestamp("2026-07-01", tz="UTC") + pd.Timedelta(days=offset)
        rows.append(
            {
                "average": close,
                "date": day.strftime("%Y-%m-%d"),
                "highest": close * 1.02,
                "lowest": close * 0.98,
                "order_count": order_count,
                "volume": volume,
            }
        )
    return frame_from_history(rows, type_id=type_id, region_id=10000002)


@pytest.fixture
def seeded_lake(paths):
    lake = BarLake(paths)
    lake.write(synth_history(34, close=4.0, volume=5_000_000_000, order_count=1500))
    lake.write(synth_history(35, close=8.0, volume=100_000, order_count=40))
    lake.write(synth_history(36, close=1000.0, volume=10, order_count=2))
    return lake


def test_liquidity_table_uses_medians(seeded_lake):
    table = liquidity_table(seeded_lake, 10000002, lookback_days=30)
    assert set(table["type_id"]) == {34, 35, 36}
    tritanium = table[table["type_id"] == 34].iloc[0]
    assert tritanium["median_isk_value"] == pytest.approx(4.0 * 5_000_000_000)
    assert tritanium["median_order_count"] == 1500


def test_floor_requires_both_turnover_and_order_count(seeded_lake):
    table = liquidity_table(seeded_lake, 10000002, lookback_days=30)
    scored = apply_floor(table, min_isk_value=100_000_000, min_order_count=30)
    tracked = set(scored[scored["tracked"]]["type_id"])
    assert tracked == {34}  # 35 has turnover 800k; 36 has order_count 2


def test_median_resists_a_single_wash_trade_day(paths):
    lake = BarLake(paths)
    rows = []
    for offset in range(30):
        day = pd.Timestamp("2026-07-01", tz="UTC") + pd.Timedelta(days=offset)
        spike = offset == 15
        rows.append(
            {
                "average": 10.0,
                "date": day.strftime("%Y-%m-%d"),
                "highest": 10.0,
                "lowest": 10.0,
                "order_count": 500 if spike else 1,
                "volume": 10_000_000_000 if spike else 10,
            }
        )
    lake.write(frame_from_history(rows, type_id=99, region_id=10000002))
    table = liquidity_table(lake, 10000002, lookback_days=30)
    scored = apply_floor(table, min_isk_value=100_000_000, min_order_count=30)
    assert not scored.iloc[0]["tracked"], "one wash-trade day must not lift a dead item"


def test_floor_grid_is_scored_across_the_whole_grid(seeded_lake):
    table = liquidity_table(seeded_lake, 10000002, lookback_days=30)
    grid = score_floor_grid(table)
    assert len(grid) == 7 * 5
    loosest = grid[0]
    assert loosest["types"] >= grid[-1]["types"]
    assert 0.0 <= loosest["share_of_turnover"] <= 1.0


def test_derived_floor_follows_the_stated_rule():
    grid = [
        {
            "min_median_isk_value": 10e6,
            "min_median_order_count": 5,
            "types": 900,
            "share_of_types": 0.9,
            "captured_daily_isk": 1e12,
            "share_of_turnover": 0.999,
        },
        {
            "min_median_isk_value": 100e6,
            "min_median_order_count": 30,
            "types": 300,
            "share_of_types": 0.3,
            "captured_daily_isk": 0.97e12,
            "share_of_turnover": 0.97,
        },
        {
            "min_median_isk_value": 1e9,
            "min_median_order_count": 100,
            "types": 40,
            "share_of_types": 0.04,
            "captured_daily_isk": 0.5e12,
            "share_of_turnover": 0.5,
        },
    ]
    derived = derive_floor(grid, target_turnover_share=0.95)
    assert derived["resolved"]
    assert derived["min_median_isk_value"] == 100e6
    assert derived["types"] == 300


def test_derived_floor_is_unresolved_rather_than_guessed():
    grid = [
        {
            "min_median_isk_value": 1e9,
            "min_median_order_count": 100,
            "types": 4,
            "share_of_types": 0.01,
            "captured_daily_isk": 1e9,
            "share_of_turnover": 0.10,
        }
    ]
    derived = derive_floor(grid, target_turnover_share=0.95)
    assert not derived["resolved"]
    assert "min_median_isk_value" not in derived


def test_sync_universe_flags_drops_instead_of_deleting(db, seeded_lake):
    table = liquidity_table(seeded_lake, 10000002, lookback_days=30)
    sync_universe(db, 10000002, [34, 35, 36], table, min_isk_value=100e6, min_order_count=30)
    assert tracked_type_ids(db, 10000002) == [34]
    # Turnover collapses: 34 falls out of the floor.
    thin = table.copy()
    thin.loc[thin["type_id"] == 34, "median_isk_value"] = 1.0
    snapshot = sync_universe(
        db, 10000002, [34, 35, 36], thin, min_isk_value=100e6, min_order_count=30
    )
    assert snapshot.dropped == [34]
    assert tracked_type_ids(db, 10000002) == []
    assert dropped_type_ids(db, 10000002) == [34]
    row = db.conn.execute("SELECT * FROM universe WHERE type_id=34").fetchone()
    assert row is not None, "a dropped type is flagged, never deleted"


def test_watchlist_resolution_names_the_unresolvable(db):
    db.replace_types([(34, "Tritanium", 1857, 0.01, 0.01, 1)])
    resolved, unresolved = resolve_watchlist(db, ["Tritanium", "Rifter Mk III"])
    assert resolved == {"Tritanium": 34}
    assert unresolved == ["Rifter Mk III"]


def test_watchlist_entries_are_never_auto_removed(db, config):
    db.replace_types([(34, "Tritanium", 1857, 0.01, 0.01, 1)])
    resolved, _ = resolve_watchlist(db, list(config.universe.watchlist))
    seed_watchlist(db, config, resolved)
    count = db.conn.execute("SELECT COUNT(*) AS n FROM watchlist").fetchone()["n"]
    assert count == 50, "every operator name is recorded, resolvable or not"
    seed_watchlist(db, config, {})
    assert db.conn.execute("SELECT COUNT(*) AS n FROM watchlist").fetchone()["n"] == 50


def test_cohort_scope_walks_to_the_nearest_big_enough_ancestor(db):
    db.replace_market_groups([(4, None, "Ships"), (100, 4, "Cruisers"), (101, 100, "Tech II")])
    db.replace_types([(600, "Ishtar", 101, 100.0, 100.0, 1)])
    members = {101: 2, 100: 12, 4: 400}
    assert cohort_scope(db, 600, members, min_members=8) == (100, 12)
    assert cohort_scope(db, 600, members, min_members=200) == (4, 400)


def test_cohort_scope_returns_none_rather_than_substituting_the_market(db):
    db.replace_market_groups([(4, None, "Ships")])
    db.replace_types([(600, "Ishtar", 4, 100.0, 100.0, 1)])
    assert cohort_scope(db, 600, {4: 2}, min_members=8) is None
    assert cohort_scope(db, 999, {}, min_members=1) is None


def test_market_group_members_maps_every_ancestor(db):
    db.replace_market_groups([(4, None, "Ships"), (100, 4, "Cruisers")])
    db.replace_types([(600, "Ishtar", 100, 1.0, 1.0, 1), (601, "Vexor", 100, 1.0, 1.0, 1)])
    members = market_group_members(db, [600, 601])
    assert sorted(members[100]) == [600, 601]
    assert sorted(members[4]) == [600, 601]


def test_market_group_chain_survives_a_cycle(db):
    db.replace_market_groups([(1, 2, "A"), (2, 1, "B")])
    assert db.market_group_chain(1) == [1, 2]


def test_percentiles_of_an_empty_table_are_empty(paths):
    assert liquidity_table(BarLake(paths), 10000002, lookback_days=30).empty
    assert score_floor_grid(pd.DataFrame()) == []


def test_liquidity_table_ignores_bars_outside_the_window(paths):
    lake = BarLake(paths)
    old = synth_history(70, close=10.0, volume=1_000_000_000, order_count=500, days=5)
    lake.write(old)
    recent = synth_history(70, close=10.0, volume=1, order_count=1, days=5)
    recent["datetime"] = recent["datetime"] + pd.Timedelta(days=200)
    lake.write(recent)
    table = liquidity_table(lake, 10000002, lookback_days=30)
    assert table[table["type_id"] == 70].iloc[0]["median_order_count"] == 1
    assert np.isfinite(table[table["type_id"] == 70].iloc[0]["median_isk_value"])
