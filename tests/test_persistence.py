"""Persistence: a snapshot measured against the snapshots before it (§23.21).

The tab's standing caveat is "a snapshot is not a tape". Measured on the real
lake (2026-09-04 analysis): 44.5% of plans survived from one generation to the
next 46.5 h later, and the top-25's net re-priced to 36%. These tests pin the
arithmetic that turns stored generations into a survival rate and a
persistence-weighted objective — and that too few generations is UNKNOWN,
never a comfortable 100%.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from evescreener.books import DepthCurve, DepthSnapshot
from evescreener.costs import CostModel
from evescreener.hauling import (
    PERSISTENT_ISK_PER_ACTIVE_MINUTE,
    HaulProfile,
    ShipProfile,
    Station,
    scan_hauls,
)
from evescreener.persistence import PERSISTENCE_UNKNOWN, persistence_attachment, persistence_of
from evescreener.routes import RouteGraph
from evescreener.store.lake import DEPTH_COLUMNS, DepthLake

NOW = datetime(2026, 8, 28, 22, 0, tzinfo=UTC)
FORGE, DOMAIN = 10000002, 10000043
JITA_44, AMARR_8 = 60003760, 60008494
JITA, AMARR = 30000142, 30002187
REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = Station(JITA_44, JITA, FORGE, "Jita")
DEST = Station(AMARR_8, AMARR, DOMAIN, "Amarr")


# -- 1. the arithmetic ------------------------------------------------------


def test_survival_counts_only_generations_where_the_plan_still_nets_money(config):
    costs = CostModel.from_config(config)
    ask_now = DepthCurve.from_pairs([(100.0, 100.0)])
    bid_now = DepthCurve.from_pairs([(120.0, 100.0)])
    net_now = costs.sell_proceeds(12_000.0, maker=False) - 10_000.0
    # gen 1: identical (survives). gen 2: the bid fell below cost (dies).
    # gen 3: no bid at all (dies — the plan would not have existed).
    result = persistence_of(
        quantity=100.0,
        net_now=net_now,
        source_curves=[ask_now, ask_now, ask_now],
        dest_curves=[bid_now, DepthCurve.from_pairs([(90.0, 100.0)]), None],
        costs=costs,
        min_generations=3,
    )
    assert result["generations_checked"] == 3
    assert result["survived"] == 1
    assert result["survival_rate"] == pytest.approx(1 / 3)
    assert result["known"] is True
    # net ratio over ALL checked generations, 0 where the plan was gone.
    assert result["median_net_ratio"] == pytest.approx(0.0)
    assert result["net_ratios"][0] == pytest.approx(1.0)


def test_too_few_generations_is_unknown_not_a_perfect_score(config):
    costs = CostModel.from_config(config)
    curve = DepthCurve.from_pairs([(100.0, 100.0)])
    bid = DepthCurve.from_pairs([(120.0, 100.0)])
    result = persistence_of(
        quantity=100.0,
        net_now=1.0,
        source_curves=[curve],
        dest_curves=[bid],
        costs=costs,
        min_generations=3,
    )
    assert result["known"] is False
    assert result["survival_rate"] is None
    assert "1 of 3" in result["reason"]


def test_a_source_deeper_than_the_old_generation_counts_as_gone(config):
    """A quantity the old ask could not fill was not a plan then."""
    costs = CostModel.from_config(config)
    result = persistence_of(
        quantity=100.0,
        net_now=1.0,
        source_curves=[DepthCurve.from_pairs([(100.0, 50.0)])],
        dest_curves=[DepthCurve.from_pairs([(120.0, 100.0)])],
        costs=costs,
        min_generations=1,
    )
    assert result["survived"] == 0
    assert result["known"] is True


# -- 2. from the lake through the scan --------------------------------------


def _depth_rows(*, region, station, side, levels, sweep, type_id=34):
    rows = []
    cumulative_qty = cumulative_notional = 0.0
    for price, qty in levels:
        cumulative_qty += qty
        cumulative_notional += price * qty
        rows.append(
            {
                "region_id": region,
                "sweep_ts": sweep,
                "fetched_at": sweep,
                "expires_ts": None,
                "execution_location_id": station,
                "type_id": type_id,
                "side": side,
                "price": price,
                "level_qty": qty,
                "cumulative_qty": cumulative_qty,
                "cumulative_notional": cumulative_notional,
                "level_order_count": 1,
                "min_volume_excluded_qty": 0.0,
                "oldest_issued": "2026-08-20T00:00:00Z",
                "newest_issued": "2026-08-24T00:00:00Z",
                "structure_share": 0.0,
                "depth_complete": True,
            }
        )
    return pd.DataFrame(rows, columns=DEPTH_COLUMNS)


def _sweeps(lake, *, bids_by_generation):
    """Three generations, oldest first; the newest is the one the scan prices."""
    stamps = [
        "2026-08-28T19:00:00+00:00",
        "2026-08-28T20:00:00+00:00",
        "2026-08-28T21:00:00+00:00",
    ]
    for stamp, bid in zip(stamps, bids_by_generation, strict=True):
        lake.write(
            _depth_rows(
                region=FORGE, station=JITA_44, side="sell", levels=[(100.0, 1000.0)], sweep=stamp
            )
        )
        if bid is not None:
            lake.write(
                _depth_rows(
                    region=DOMAIN, station=AMARR_8, side="buy", levels=[(bid, 1000.0)], sweep=stamp
                )
            )
    return stamps


def _snapshot(lake, region, sweep):
    frame = lake.latest(region)
    return DepthSnapshot(
        region_id=region, frame=frame, sweep_ts=sweep, age_minutes=5.0, stale=False, reason=""
    )


def _profile(**overrides):
    ship = ShipProfile(name="t", usable_cargo_m3=1e6, seconds_per_jump=60.0, handling_minutes=5.0)
    defaults = {
        "current_system": JITA,
        "ship": ship,
        "capital_isk": 5e9,
        "max_exposure_isk": 5e9,
        "session_minutes": 600.0,
        "security_profile": "shortest",
    }
    defaults.update(overrides)
    return HaulProfile(**defaults)


def _graph():
    return RouteGraph([(JITA, AMARR)], {JITA: 0.9, AMARR: 0.9}, sde_build=1)


def test_the_lake_yields_prior_generations_newest_first(paths):
    lake = DepthLake(paths)
    stamps = _sweeps(lake, bids_by_generation=[120.0, 120.0, 120.0])
    prior = lake.generations(DOMAIN, limit=5, before=stamps[-1])
    assert [str(frame["sweep_ts"].iloc[0]) for frame in prior] == [
        "2026-08-28 20:00:00+00:00",
        "2026-08-28 19:00:00+00:00",
    ]
    assert len(lake.generations(DOMAIN, limit=1, before=stamps[-1])) == 1


def test_a_plan_carries_its_survival_across_stored_generations(config, paths):
    lake = DepthLake(paths)
    # Oldest generation: the bid was below cost. Middle: alive. Newest: priced.
    stamps = _sweeps(lake, bids_by_generation=[90.0, 120.0, 120.0])
    depths = {
        FORGE: _snapshot(lake, FORGE, stamps[-1]),
        DOMAIN: _snapshot(lake, DOMAIN, stamps[-1]),
    }
    attach = persistence_attachment(
        config, depths, lake=lake, stations=[SOURCE, DEST], min_generations=2, now=NOW
    )
    scan = scan_hauls(
        config,
        _profile(),
        stations=[SOURCE, DEST],
        depths=depths,
        graph=_graph(),
        names={34: "Tritanium"},
        packaged_volume={34: 0.01},
        persistence=attach,
        now=NOW,
    )
    assert len(scan.plans) == 1
    plan = scan.plans[0]
    assert plan.persistence["generations_checked"] == 2
    assert plan.persistence["survived"] == 1
    assert plan.persistent_isk_per_active_minute == pytest.approx(plan.isk_per_active_minute * 0.5)
    assert plan.persistence["window"] == [
        "2026-08-28T19:00:00+00:00",
        "2026-08-28T20:00:00+00:00",
    ]


def test_the_persistent_objective_refuses_to_rank_an_unmeasured_plan(config, paths):
    lake = DepthLake(paths)
    stamps = _sweeps(lake, bids_by_generation=[None, None, 120.0])
    depths = {
        FORGE: _snapshot(lake, FORGE, stamps[-1]),
        DOMAIN: _snapshot(lake, DOMAIN, stamps[-1]),
    }
    attach = persistence_attachment(
        config, depths, lake=lake, stations=[SOURCE, DEST], min_generations=3, now=NOW
    )
    scan = scan_hauls(
        config,
        _profile(objective=PERSISTENT_ISK_PER_ACTIVE_MINUTE),
        stations=[SOURCE, DEST],
        depths=depths,
        graph=_graph(),
        names={34: "Tritanium"},
        packaged_volume={34: 0.01},
        persistence=attach,
        now=NOW,
    )
    assert scan.plans == []
    assert scan.dropped_unrankable == {PERSISTENCE_UNKNOWN: 1}


def test_without_an_attachment_the_plan_says_persistence_is_unmeasured(config, paths):
    lake = DepthLake(paths)
    stamps = _sweeps(lake, bids_by_generation=[120.0, 120.0, 120.0])
    depths = {
        FORGE: _snapshot(lake, FORGE, stamps[-1]),
        DOMAIN: _snapshot(lake, DOMAIN, stamps[-1]),
    }
    scan = scan_hauls(
        config,
        _profile(),
        stations=[SOURCE, DEST],
        depths=depths,
        graph=_graph(),
        names={34: "Tritanium"},
        packaged_volume={34: 0.01},
        now=NOW,
    )
    plan = scan.plans[0]
    assert plan.persistence is None
    assert plan.persistent_isk_per_active_minute is None
    assert plan.as_dict()["persistence"] is None
