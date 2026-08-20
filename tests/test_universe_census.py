"""Universe floors and the census opportunity map."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evescreener.bars import frame_from_history
from evescreener.census import (
    FLOOR_GRID_ISK,
    FLOOR_GRID_ORDERS,
    derive_floor,
    score_floor_grid,
)
from evescreener.sde import cohort_scope, market_group_members, resolve_watchlist
from evescreener.store.lake import BarLake
from evescreener.universe import (
    apply_floor,
    classify_tier,
    dropped_type_ids,
    index_eligible_type_ids,
    liquidity_table,
    seed_watchlist,
    sync_universe,
    thin_type_ids,
    tier_badge,
    tracked_type_ids,
)


def synth_history(
    type_id: int,
    *,
    close: float,
    volume: float,
    order_count: int,
    days: int = 40,
    pinned: bool = False,
):
    """A synthetic series. `pinned=True` never moves — an NPC-seeded price.

    The unpinned ramp is symmetric about `close`, so every median in the
    liquidity table is exactly `close` and the tests can still assert on
    round numbers.
    """
    rows = []
    centre = (days - 1) / 2
    for offset in range(days):
        day = pd.Timestamp("2026-07-01", tz="UTC") + pd.Timedelta(days=offset)
        price = close if pinned else close * (1.0 + 0.001 * (offset - centre) / days)
        rows.append(
            {
                "average": price,
                "date": day.strftime("%Y-%m-%d"),
                "highest": price * 1.02,
                "lowest": price * 0.98,
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
    assert tritanium["median_isk_value"] == pytest.approx(4.0 * 5_000_000_000, rel=1e-3)
    assert tritanium["median_order_count"] == 1500
    assert tritanium["median_unit_volume"] == 5_000_000_000
    assert not tritanium["price_pinned"]


def test_membership_is_decided_on_unit_volume_not_turnover(seeded_lake):
    """The gate is units. Turnover is the weighting input, not the gate.

    Type 36 prints 10,000 ISK a day on 10 units — real money, no way out. It
    must not be tradeable. Type 35 moves 100,000 units and is, even though 34
    dwarfs it on every ISK measure.
    """
    table = liquidity_table(seeded_lake, 10000002, lookback_days=30)
    scored = apply_floor(table, min_unit_volume=1000.0, absolute_min_unit_volume=100.0)
    by_type = scored.set_index("type_id")
    assert by_type.loc[34, "tier"] == "OK"
    assert by_type.loc[35, "tier"] == "OK"
    assert by_type.loc[36, "tier"] == "BELOW"
    assert not by_type.loc[36, "tracked"]
    assert set(scored[scored["index_eligible"]]["type_id"]) == {34, 35}


def test_the_thin_band_is_carried_but_never_an_index_member(paths):
    lake = BarLake(paths)
    lake.write(synth_history(700, close=50.0, volume=400, order_count=8))
    table = liquidity_table(lake, 10000002, lookback_days=30)
    scored = apply_floor(table, min_unit_volume=1000.0, absolute_min_unit_volume=100.0)
    row = scored.iloc[0]
    assert row["tier"] == "THIN"
    assert row["tracked"], "THIN is measurable and chartable"
    assert not row["index_eligible"], "THIN never moves FORGE"
    assert tier_badge("THIN") == "THIN"
    assert tier_badge("OK") == ""


def test_an_unmeasured_type_is_below_not_ok():
    """Uncertainty never reads as a pass (§4)."""
    assert classify_tier(None, default_floor=1000.0, absolute_floor=100.0) == "BELOW"
    assert classify_tier(float("nan"), default_floor=1000.0, absolute_floor=100.0) == "BELOW"


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
    scored = apply_floor(table, min_unit_volume=1000.0, absolute_min_unit_volume=100.0)
    assert scored.iloc[0]["tier"] == "BELOW", (
        "one 10-billion-unit day must not lift a 10-unit-a-day item over the floor"
    )


def test_floor_grid_is_scored_across_the_whole_grid(seeded_lake):
    table = liquidity_table(seeded_lake, 10000002, lookback_days=30)
    grid = score_floor_grid(table)
    assert len(grid) == len(FLOOR_GRID_ISK) * len(FLOOR_GRID_ORDERS)
    loosest = grid[0]
    assert loosest["types"] >= grid[-1]["types"]
    assert loosest["share_of_turnover"] == pytest.approx(1.0), (
        "the grid must contain a no-floor corner or the derive rule cannot resolve"
    )


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


FLOORS = {"min_unit_volume": 1000.0, "absolute_min_unit_volume": 100.0}


def test_sync_universe_flags_drops_instead_of_deleting(db, seeded_lake):
    table = liquidity_table(seeded_lake, 10000002, lookback_days=30)
    sync_universe(db, 10000002, [34, 35, 36], table, **FLOORS)
    assert tracked_type_ids(db, 10000002) == [34, 35]
    # Volume collapses: 34 stops trading entirely.
    quiet = table.copy()
    quiet.loc[quiet["type_id"] == 34, "median_unit_volume"] = 1.0
    snapshot = sync_universe(db, 10000002, [34, 35, 36], quiet, **FLOORS)
    assert snapshot.dropped == [34]
    assert tracked_type_ids(db, 10000002) == [35]
    assert dropped_type_ids(db, 10000002) == [34]
    row = db.conn.execute("SELECT * FROM universe WHERE type_id=34").fetchone()
    assert row is not None, "a dropped type is flagged, never deleted"


def test_sync_universe_separates_index_members_from_the_thin_band(db, paths):
    lake = BarLake(paths)
    lake.write(synth_history(34, close=4.0, volume=5_000_000_000, order_count=1500))
    lake.write(synth_history(700, close=50.0, volume=400, order_count=8))
    lake.write(synth_history(701, close=9e9, volume=3, order_count=1))
    table = liquidity_table(lake, 10000002, lookback_days=30)
    snapshot = sync_universe(db, 10000002, [34, 700, 701], table, **FLOORS)

    assert index_eligible_type_ids(db, 10000002) == [34]
    assert thin_type_ids(db, 10000002) == [700]
    assert tracked_type_ids(db, 10000002) == [34, 700]
    assert snapshot.index_eligible == 1
    assert snapshot.thin == 1
    assert snapshot.below == 1
    # 701 prints 27 billion ISK a day on three units. Real money, no way out.
    assert 701 not in tracked_type_ids(db, 10000002)


def test_an_old_database_gains_the_new_columns_without_losing_its_rows(paths, tmp_path):
    """The state db holds the paper ledger; it is migrated, never rebuilt."""
    import sqlite3

    from evescreener.store.db import Database

    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript(
        "CREATE TABLE universe ("
        " type_id INTEGER NOT NULL, region_id INTEGER NOT NULL,"
        " first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,"
        " median_isk_value REAL, median_order_count REAL,"
        " tracked INTEGER NOT NULL DEFAULT 0, dropped_at TEXT,"
        " source TEXT NOT NULL DEFAULT 'census',"
        " PRIMARY KEY (type_id, region_id));"
        "INSERT INTO universe VALUES (34, 10000002, 'x', 'y', 1.0, 2.0, 1, NULL, 'census');"
    )
    legacy.commit()
    legacy.close()

    with Database(path) as db:
        columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(universe)")}
        assert {"median_unit_volume", "tier"} <= columns
        row = db.conn.execute("SELECT * FROM universe WHERE type_id=34").fetchone()
        assert row is not None, "migration never drops the operator's rows"
        assert row["tier"] is None, "an unmeasured tier is NULL, not a fabricated OK"


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


def test_an_npc_seeded_price_is_tradeable_but_never_an_index_member(paths):
    """A price that has not moved in 30 days is set by a vendor, not a market.

    It stays chartable — the operator may well want to look at one — but it
    contributes a flat line that absorbs index weight and reports nothing.
    """
    lake = BarLake(paths)
    lake.write(synth_history(34, close=4.0, volume=5_000_000_000, order_count=1500))
    lake.write(synth_history(3300, close=90_000.0, volume=4_000, order_count=60, pinned=True))
    table = liquidity_table(lake, 10000002, lookback_days=30)
    scored = apply_floor(table, **FLOORS).set_index("type_id")

    assert scored.loc[3300, "price_pinned"]
    assert scored.loc[3300, "tier"] == "OK", "it clears the volume floor honestly"
    assert scored.loc[3300, "tracked"], "and stays tradeable and chartable"
    assert not scored.loc[3300, "index_eligible"], "but it may not move FORGE"
    assert scored.loc[34, "index_eligible"]


def test_a_short_sample_is_not_called_pinned(paths):
    """'The price never moved' needs enough bars to be about the item."""
    lake = BarLake(paths)
    lake.write(synth_history(3301, close=100.0, volume=5_000, order_count=10, days=3, pinned=True))
    table = liquidity_table(lake, 10000002, lookback_days=30)
    assert not table.iloc[0]["price_pinned"]


def test_a_pinned_price_is_persisted_and_kept_out_of_the_index(db, paths):
    lake = BarLake(paths)
    lake.write(synth_history(34, close=4.0, volume=5_000_000_000, order_count=1500))
    lake.write(synth_history(3300, close=90_000.0, volume=4_000, order_count=60, pinned=True))
    table = liquidity_table(lake, 10000002, lookback_days=30)
    snapshot = sync_universe(db, 10000002, [34, 3300], table, **FLOORS)

    assert snapshot.index_eligible == 1
    assert snapshot.price_pinned == 1
    assert snapshot.tracked == 2, "both are carried"
    assert index_eligible_type_ids(db, 10000002) == [34]
