"""Filters, the single-bid flag, pair-level counts and extra sources (§23.21).

Measured 2026-09-04: ten of the top 25 plans were one-to-six-unit hulls whose
exit was one bid, and quantity <= 5 plans survived to the next generation at
33% against 51% for bulk. These are page controls, default off, and every plan
they withhold is counted rather than deleted.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from evescreener.books import DepthBound, DepthSnapshot, reduce_depth
from evescreener.hauling import (
    PERSISTENT_ISK_PER_ACTIVE_MINUTE,
    HaulProfile,
    ShipProfile,
    Station,
    scan_hauls,
    stations_from_db,
)
from evescreener.routes import RouteGraph

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
SWEEP = "2026-08-25T11:45:00+00:00"
FORGE, DOMAIN = 10000002, 10000043
JITA_44, AMARR_8 = 60003760, 60008494
JITA, AMARR = 30000142, 30002187
SOURCE = Station(JITA_44, JITA, FORGE, "Jita")
DEST = Station(AMARR_8, AMARR, DOMAIN, "Amarr")


def _orders(levels, *, buy, station, system, type_id=34):
    return [
        {
            "order_id": 1000 + index,
            "type_id": type_id,
            "price": float(price),
            "volume_remain": float(qty),
            "is_buy_order": buy,
            "location_id": station,
            "system_id": system,
            "range": "station" if buy else None,
            "min_volume": 1,
            "issued": "2026-08-20T00:00:00Z",
        }
        for index, (price, qty) in enumerate(levels)
    ]


def _snapshot(orders, *, region, station, system, age=15.0):
    reduction = reduce_depth(
        orders,
        region_id=region,
        stations={station: system},
        bound=DepthBound(max_capital_isk=1e12, max_cargo_m3=1e12, safety_margin=1.0),
        sweep_ts=SWEEP,
    )
    return DepthSnapshot(
        region_id=region,
        frame=reduction.frame,
        sweep_ts=SWEEP,
        age_minutes=age,
        stale=False,
        reason="",
    )


def _graph():
    return RouteGraph([(JITA, AMARR)], {JITA: 0.9, AMARR: 0.9}, sde_build=3478781)


def _profile(**overrides) -> HaulProfile:
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


def _scan(config, asks, bids, *, profile=None, badges=None, **kwargs):
    depths = {
        FORGE: _snapshot(
            _orders(asks, buy=False, station=JITA_44, system=JITA),
            region=FORGE,
            station=JITA_44,
            system=JITA,
        ),
        DOMAIN: _snapshot(
            _orders(bids, buy=True, station=AMARR_8, system=AMARR),
            region=DOMAIN,
            station=AMARR_8,
            system=AMARR,
        ),
    }
    return scan_hauls(
        config,
        profile or _profile(),
        stations=[SOURCE, DEST],
        depths=depths,
        graph=_graph(),
        names={34: "Tritanium"},
        badges=badges or {},
        packaged_volume={34: 0.01},
        now=NOW,
        **kwargs,
    )


# -- the single-bid flag ---------------------------------------------------


def test_an_exit_into_one_order_is_flagged_and_two_orders_at_one_price_are_not(config):
    single = _scan(config, [(100.0, 10.0)], [(120.0, 10.0)]).plans[0]
    assert single.dest_orders_consumed == 1
    assert single.single_bid_exit is True
    double = _scan(config, [(100.0, 10.0)], [(120.0, 5.0), (120.0, 5.0)]).plans[0]
    assert double.dest_orders_consumed == 2
    assert double.single_bid_exit is False
    assert double.as_dict()["single_bid_exit"] is False


# -- the filters -----------------------------------------------------------


def test_a_minimum_quantity_withholds_and_counts_rather_than_rejects(config):
    scan = _scan(config, [(100.0, 3.0)], [(120.0, 3.0)], profile=_profile(min_quantity=5.0))
    assert scan.plans == []
    assert scan.withheld_by_filter == {"MIN_QUANTITY": 1}
    assert "MIN_QUANTITY" not in scan.rejection_counts, "a filter is not a rejection"


def test_a_hidden_badge_withholds_and_counts(config):
    scan = _scan(
        config,
        [(100.0, 3.0)],
        [(120.0, 3.0)],
        profile=_profile(hide_badges=("BELOW",)),
        badges={34: "BELOW"},
    )
    assert scan.plans == []
    assert scan.withheld_by_filter == {"BADGE_BELOW": 1}
    shown = _scan(config, [(100.0, 3.0)], [(120.0, 3.0)], profile=_profile(hide_badges=("BELOW",)))
    assert len(shown.plans) == 1, "an unbadged plan is not hidden"


def test_filters_default_off_and_ride_on_the_profile_dict(config):
    profile = _profile()
    assert profile.min_quantity == 0.0
    assert profile.hide_badges == ()
    assert profile.as_dict()["min_quantity"] == 0.0
    assert profile.as_dict()["hide_badges"] == []
    assert _scan(config, [(100.0, 3.0)], [(120.0, 3.0)]).withheld_by_filter == {}


# -- pair-level counts -----------------------------------------------------


def test_pair_level_refusals_are_counted_apart_from_candidate_refusals(config):
    stale = _scan(
        config,
        [(100.0, 3.0)],
        [(120.0, 3.0)],
        profile=_profile(session_minutes=1.0),
    )
    # Two stations make two ordered pairs, and both exceed a one-minute session.
    assert stale.pair_rejection_counts == {"OVER_TIME": 2}
    assert stale.rejection_counts == {"OVER_TIME": 2}
    priced = _scan(config, [(100.0, 3.0), (200.0, 3.0)], [(120.0, 6.0)])
    assert priced.pair_rejection_counts == {}
    assert "MARGINAL_NET_NEGATIVE" in priced.rejection_counts


# -- the persistent objective without persistence --------------------------


def test_the_persistent_objective_without_generations_counts_the_plan_unrankable(config):
    scan = _scan(
        config,
        [(100.0, 3.0)],
        [(120.0, 3.0)],
        profile=_profile(objective=PERSISTENT_ISK_PER_ACTIVE_MINUTE),
    )
    assert scan.plans == []
    assert scan.dropped_unrankable == {"PERSISTENCE_UNKNOWN": 1}


# -- extra sources ---------------------------------------------------------


def test_an_extra_source_station_joins_the_sources_but_not_by_default(config, db):
    db.replace_solar_systems([(JITA, FORGE, "Jita", 0.9), (30000144, FORGE, "Perimeter", 0.9)])
    db.replace_npc_stations(
        [(JITA_44, JITA, 1000035, 14, None), (60003916, 30000144, 1000035, 14, None)]
    )
    widened = replace(config, hauling=replace(config.hauling, extra_source_station_ids=(60003916,)))
    default_sources = [station.station_id for station in stations_from_db(config, db)]
    assert 60003916 not in default_sources
    sources = [
        station.station_id
        for station in stations_from_db(
            widened, db, include_extra=False, include_extra_sources=True
        )
    ]
    assert 60003916 in sources and JITA_44 in sources
    destinations = [
        station.station_id
        for station in stations_from_db(
            widened, db, include_extra=True, include_extra_sources=False
        )
    ]
    assert 60003916 not in destinations, "a source is not thereby a destination"


def test_the_config_carries_the_new_keys_with_defaults(config):
    assert config.hauling.extra_source_station_ids == ()
    assert config.hauling.persistence_generations == 12
    assert config.hauling.persistence_min_generations == 3
    assert config.hauling.route_risk_days == 90
    assert "Haulers" in config.hauling.hauler_group_names


@pytest.mark.parametrize("bad", [{"min_quantity": -1.0}])
def test_a_negative_minimum_quantity_is_a_loud_error(bad):
    with pytest.raises(ValueError):
        _profile(**bad)
