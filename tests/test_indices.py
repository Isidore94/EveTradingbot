"""FORGE, FORGE-EW and the sector indices (plan.md §19 Part 1)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from evescreener.indices import (
    FORGE,
    FORGE_EW,
    IndexSet,
    Sector,
    SectorConfigError,
    build_index_set,
    load_sectors,
    rotation_table,
    sector_for_type,
    sector_members,
)
from evescreener.signals.composite import EQUAL, TURNOVER, build_composite

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = json.loads((FIXTURES / "golden_signals.json").read_text())
REPO_SECTORS = Path(__file__).resolve().parents[1] / "config" / "sectors.jsonl"


def lake(type_ids, *, bars=120, seed=7, unit_volume=10_000.0, price=100.0):
    rng = np.random.default_rng(seed)
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    rows = []
    for offset, type_id in enumerate(type_ids):
        close = price * (offset + 1) * np.exp(np.cumsum(rng.normal(0.0, 0.01, bars)))
        for position, stamp in enumerate(stamps):
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": close[position] * 1.01,
                    "low": close[position] * 0.99,
                    "close": close[position],
                    "volume": unit_volume,
                    "order_count": 50,
                    "isk_value": close[position] * unit_volume,
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    return pd.DataFrame(rows)


# -- the engine -------------------------------------------------------------


def test_the_index_series_matches_the_golden_fixture():
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from generate_golden import index_lake

    expected = GOLDEN["indices"]
    forge = build_composite(index_lake(), members=100, weighting=TURNOVER, ticker="FORGE")
    assert [float(v) for v in forge.frame["close"].tail(5)] == pytest.approx(
        expected["forge_tail"], rel=1e-12
    )
    assert list(forge.member_ids) == expected["forge_members"]
    assert forge.diagnostics["top_weight"] == pytest.approx(expected["forge_top_weight"])


def test_chain_link_holds_the_level_flat_through_composition_churn():
    """A 1,000x-priced member joining mid-series must print as NOTHING.

    Without chain-linking, admitting it at a rebalance would move the index
    enormously — composition masquerading as a market move, which is exactly
    what §9 R8 says a self-built index must never do.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from generate_golden import churn_lake

    churn = build_composite(churn_lake(), members=100, rebalance_days=30, ticker="CHURN")
    levels = [float(value) for value in churn.frame["close"]]
    assert churn.diagnostics["rebalances"] >= 2, "the test needs a rebalance to have happened"
    assert churn.diagnostics["members"] == 9, "the interloper must have joined the basket"
    assert min(levels) == pytest.approx(1000.0, abs=1e-9)
    assert max(levels) == pytest.approx(1000.0, abs=1e-9)


def test_turnover_weighting_not_unit_volume():
    """Raw units would make the index ~100% Tritanium; turnover must not."""
    stamps = pd.date_range("2026-01-01 11:00", periods=60, freq="D", tz="UTC")
    rows = []
    for type_id, price, units in ((34, 4.0, 5_000_000_000.0), (44992, 5_000_000.0, 200.0)):
        for stamp in stamps:
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": units,
                    "order_count": 50,
                    "isk_value": price * units,
                    "fetched_at": "x",
                }
            )
    composite = build_composite(pd.DataFrame(rows), members=10, min_members=2, single_cap=1.0)
    # Tritanium turns over 20B ISK/day, the injector 1B — a 20:1 turnover split,
    # not the 25,000,000:1 split raw units would have produced.
    weights = composite.diagnostics
    assert weights["members"] == 2
    assert weights["top_weight"] == pytest.approx(20 / 21, rel=1e-6)


def test_equal_weight_inherits_membership_exactly():
    frame = lake(range(1000, 1012))
    forge = build_composite(frame, members=100, weighting=TURNOVER)
    equal = build_composite(
        frame, members=100, single_cap=1.0, weighting=EQUAL, member_ids=forge.member_ids
    )
    assert set(equal.member_ids) == set(forge.member_ids)
    assert equal.diagnostics["top_weight"] == pytest.approx(1 / len(forge.member_ids))


def test_an_unknown_weighting_is_a_loud_error():
    with pytest.raises(ValueError, match="unknown weighting"):
        build_composite(lake(range(1000, 1010)), weighting="capweighted")


def test_member_ids_restrict_the_pool():
    frame = lake(range(1000, 1012))
    restricted = build_composite(frame, members=100, min_members=3, member_ids=[1000, 1001, 1002])
    assert set(restricted.member_ids) == {1000, 1001, 1002}


def test_no_bars_for_the_requested_members_is_unknown():
    result = build_composite(lake(range(1000, 1010)), member_ids=[999999])
    assert not result.known
    assert "requested members" in result.diagnostics["reason"]


# -- the sector map ---------------------------------------------------------


def test_the_committed_sector_map_parses():
    sectors = load_sectors(REPO_SECTORS)
    assert len(sectors) >= 9
    tickers = {sector.ticker for sector in sectors}
    assert {"MIN", "SHIP", "MOD", "AMMO", "DRONE", "MFG", "FUEL", "PLEX", "IMP"} <= tickers
    assert all(sector.roots for sector in sectors)


def test_a_malformed_sector_row_names_itself(tmp_path):
    path = tmp_path / "sectors.jsonl"
    path.write_text('{"ticker": "A", "name": "A", "roots": [1]}\nnot json\n', encoding="utf-8")
    with pytest.raises(SectorConfigError, match=r"sectors.jsonl:2"):
        load_sectors(path)


def test_a_missing_field_is_refused(tmp_path):
    path = tmp_path / "sectors.jsonl"
    path.write_text('{"ticker": "A", "name": "A"}\n', encoding="utf-8")
    with pytest.raises(SectorConfigError, match="missing roots"):
        load_sectors(path)


def test_the_market_tickers_are_reserved(tmp_path):
    path = tmp_path / "sectors.jsonl"
    path.write_text('{"ticker": "FORGE", "name": "x", "roots": [1]}\n', encoding="utf-8")
    with pytest.raises(SectorConfigError, match="reserved"):
        load_sectors(path)


def test_duplicate_tickers_are_refused(tmp_path):
    path = tmp_path / "sectors.jsonl"
    path.write_text(
        '{"ticker": "A", "name": "A", "roots": [1]}\n{"ticker": "A", "name": "B", "roots": [2]}\n',
        encoding="utf-8",
    )
    with pytest.raises(SectorConfigError, match="duplicate"):
        load_sectors(path)


def test_a_missing_sector_file_is_an_empty_map(tmp_path):
    assert load_sectors(tmp_path / "nope.jsonl") == []


# -- membership -------------------------------------------------------------


@pytest.fixture
def sector_db(db):
    db.replace_market_groups(
        [(4, None, "Ships"), (100, 4, "Cruisers"), (9, None, "Ship Equipment"), (11, None, "Ammo")]
    )
    db.replace_types(
        [
            (600, "Ishtar", 100, 1.0, 1.0, 1),
            (601, "Vexor", 100, 1.0, 1.0, 1),
            (602, "Thorax", 100, 1.0, 1.0, 1),
            (700, "Damage Control II", 9, 1.0, 1.0, 1),
            (800, "Antimatter L", 11, 1.0, 1.0, 1),
        ]
    )
    return db


def test_sector_membership_walks_the_subtree(sector_db):
    ships = Sector(ticker="SHIP", name="Ships", roots=(4,))
    assert sorted(sector_members(sector_db, ships, [600, 601, 700, 800])) == [600, 601]


def test_sector_for_type_returns_none_rather_than_the_market(sector_db):
    sectors = [Sector(ticker="SHIP", name="Ships", roots=(4,))]
    assert sector_for_type(sector_db, sectors, 600).ticker == "SHIP"
    assert sector_for_type(sector_db, sectors, 800) is None, (
        "an unresolvable scope is UNKNOWN, never a silent substitution"
    )
    assert sector_for_type(sector_db, sectors, 999999) is None


# -- the index set ----------------------------------------------------------


def test_a_thin_sector_is_unknown_never_merged(config, sector_db):
    frame = lake([600, 601, 700, 800])
    sectors = [Sector(ticker="SHIP", name="Ships", roots=(4,), min_members=8)]
    result = build_index_set(config, sector_db, frame, sectors=sectors)
    assert "SHIP" not in result.sectors
    meta = result.sector_meta["SHIP"]
    assert meta["status"] == "UNKNOWN"
    assert "below the sector's minimum" in meta["reason"]
    assert meta["candidate_members"] == 2


def test_forge_and_its_equal_weight_twin_share_membership(config, sector_db):
    frame = lake(range(1000, 1012))
    result = build_index_set(config, sector_db, frame, sectors=[])
    assert result.known
    assert set(result.forge_ew.member_ids) == set(result.forge.member_ids)
    assert result.forge.diagnostics["ticker"] == FORGE
    assert result.forge_ew.diagnostics["ticker"] == FORGE_EW


def test_breadth_is_the_equal_weight_spread(config, sector_db):
    frame = lake(range(1000, 1012))
    result = build_index_set(config, sector_db, frame, sectors=[])
    breadth = result.breadth()
    assert not breadth.empty
    assert breadth.iloc[0] == pytest.approx(0.0, abs=1e-9), "both indices start at the same base"


def test_breadth_is_empty_not_zero_when_an_index_is_unknown(config, sector_db):
    empty = IndexSet(
        forge=build_composite(pd.DataFrame()), forge_ew=build_composite(pd.DataFrame())
    )
    assert empty.breadth().empty
    assert not empty.known


def test_rotation_table_keeps_unknown_sectors_visible(config, sector_db):
    frame = lake([600, 601, 700, 800])
    sectors = [
        Sector(ticker="SHIP", name="Ships", roots=(4,), min_members=8),
        Sector(ticker="MOD", name="Modules", roots=(9,), min_members=1),
    ]
    result = build_index_set(config, sector_db, frame, sectors=sectors)
    rows = rotation_table(result)
    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["SHIP"]["status"] == "UNKNOWN"
    assert by_ticker["SHIP"]["reason"]
    assert rows[-1]["ticker"] == "SHIP", "UNKNOWN sorts to the bottom"


def test_index_set_diagnostics_are_publishable(config, sector_db):
    result = build_index_set(config, sector_db, lake(range(1000, 1012)), sectors=[])
    payload = result.as_dict()
    assert payload["forge"]["members"] > 0
    assert payload["forge"]["top_weight"] is not None
    assert payload["forge"]["weight_entropy"] is not None
    assert payload["forge_ew"]["weighting"] == "equal"


def test_a_sector_floor_removes_members_and_says_how_many(config, sector_db):
    frame = lake([600, 601, 602, 700])
    sectors = [
        Sector(ticker="SHIP", name="Ships", roots=(4,), min_members=2, min_unit_volume=500.0)
    ]
    volumes = {600: 10_000.0, 601: 10_000.0, 602: 10.0}
    result = build_index_set(config, sector_db, frame, sectors=sectors, unit_volume=volumes)
    meta = result.sector_meta["SHIP"]
    assert meta["candidate_members"] == 2
    assert meta["excluded_by_sector_floor"] == 1


def test_a_sector_floor_with_no_measurements_is_unknown_not_ignored(config, sector_db):
    frame = lake([600, 601, 602, 700])
    sectors = [
        Sector(ticker="SHIP", name="Ships", roots=(4,), min_members=2, min_unit_volume=500.0)
    ]
    result = build_index_set(config, sector_db, frame, sectors=sectors)
    meta = result.sector_meta["SHIP"]
    assert meta["status"] == "UNKNOWN"
    assert "cannot be applied" in meta["reason"]
    assert "SHIP" not in result.sectors
