"""The hauling engine, end to end (plan.md §23.10, §23.17).

The first test is the whole point of the track: synthetic sweeps for two
regions go in at one end, and §23.17's frozen numbers come out at the other —
1,200 units, a 102,416.67 source WAP, a 117,375 destination WAP,
**13,196,312.50** net after 4,753,687.50 of sales tax, a **10.74%** ROI. Those
numbers were written into `plan.md` before this module existed.

The rest is about what the engine refuses to do: price a pair when either
region's generation is stale, size a plan past the hold or the wallet, keep
adding units after the last chunk stopped paying, or route through space the
operator said he would not fly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evescreener.books import DepthBound, DepthSnapshot, reduce_depth
from evescreener.hauling import (
    DEPTH_TRUNCATED,
    MARGINAL_NET_NEGATIVE,
    NO_ROUTE,
    OVER_CAPITAL,
    OVER_CARGO,
    OVER_EXPOSURE,
    OVER_JUMPS,
    OVER_TIME,
    ROUTE_BLOCKED_SECURITY,
    STALE_BOOK,
    HaulProfile,
    ShipProfile,
    Station,
    scan_hauls,
)
from evescreener.routes import RouteGraph

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
SWEEP = "2026-08-25T11:45:00+00:00"

FORGE, DOMAIN = 10000002, 10000043
JITA_44, AMARR_8 = 60003760, 60008494
JITA, AMARR = 30000142, 30002187


@pytest.fixture(scope="module")
def worked() -> dict:
    return json.loads((FIXTURES / "haul_worked_example.json").read_text(encoding="utf-8"))


def _orders(levels, *, buy: bool, station: int, system: int, type_id: int = 34, min_volume=1):
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
            "min_volume": min_volume,
            "issued": "2026-08-20T00:00:00Z",
        }
        for index, (price, qty) in enumerate(levels)
    ]


def _snapshot(orders, *, region: int, station: int, system: int, sweep: str = SWEEP, age=15.0):
    reduction = reduce_depth(
        orders,
        region_id=region,
        stations={station: system},
        bound=DepthBound(max_capital_isk=1e12, max_cargo_m3=1e12, safety_margin=1.0),
        sweep_ts=sweep,
    )
    return DepthSnapshot(
        region_id=region,
        frame=reduction.frame,
        sweep_ts=sweep,
        age_minutes=age,
        stale=False,
        reason="",
    )


def _graph(jumps: int = 1, *, security: float = 0.9) -> RouteGraph:
    """A corridor of `jumps` hops between Jita and Amarr, all one security."""
    systems = [JITA] + [90_000 + index for index in range(jumps - 1)] + [AMARR]
    edges = list(zip(systems, systems[1:], strict=False))
    return RouteGraph(edges, {system: security for system in systems}, sde_build=3478781)


SOURCE = Station(JITA_44, JITA, FORGE, "Jita")
DEST = Station(AMARR_8, AMARR, DOMAIN, "Amarr")


def _profile(**overrides) -> HaulProfile:
    ship = ShipProfile(
        name="test hauler",
        usable_cargo_m3=overrides.pop("cargo_m3", 1_000_000.0),
        seconds_per_jump=60.0,
        handling_minutes=5.0,
    )
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


def _scan(config, worked, *, profile=None, graph=None, source_age=15.0, dest_age=20.0, **kwargs):
    depths = {
        FORGE: _snapshot(
            _orders(worked["source"]["asks"], buy=False, station=JITA_44, system=JITA),
            region=FORGE,
            station=JITA_44,
            system=JITA,
            age=source_age,
        ),
        DOMAIN: _snapshot(
            _orders(worked["destination"]["bids"], buy=True, station=AMARR_8, system=AMARR),
            region=DOMAIN,
            station=AMARR_8,
            system=AMARR,
            sweep="2026-08-25T11:40:00+00:00",
            age=dest_age,
        ),
    }
    depths.update(kwargs.pop("depths", {}))
    return scan_hauls(
        config,
        profile or _profile(**kwargs.pop("profile_kwargs", {})),
        stations=[SOURCE, DEST],
        depths=depths,
        # `RouteGraph.__bool__` is False for an empty map, so this is an
        # identity check rather than an `or`: a graph with no edges is exactly
        # what one of these tests is about.
        graph=graph if graph is not None else _graph(),
        names={34: "Tritanium"},
        packaged_volume={34: 0.01},
        now=NOW,
        **kwargs,
    )


# -- 1. the worked example, end to end -------------------------------------


def test_the_worked_example_survives_the_whole_pipeline(config, worked):
    """Synthetic sweeps in, §23.17's frozen numbers out."""
    expected = worked["expected"]
    scan = _scan(config, worked)
    assert scan.plans, f"expected a plan; rejections were {scan.rejection_counts}"
    plan = scan.plans[0]

    assert plan.quantity == pytest.approx(worked["quantity"])
    assert plan.source_wap == pytest.approx(expected["source_wap"])
    assert plan.source_cost == pytest.approx(expected["source_cost"])
    assert plan.dest_wap == pytest.approx(expected["destination_wap"])
    assert plan.gross_sale == pytest.approx(expected["gross_sale"])
    assert plan.sales_tax_isk == pytest.approx(expected["sales_tax_isk"])
    assert plan.net_profit == pytest.approx(expected["net_profit"])
    assert plan.net_roi_pct == pytest.approx(expected["net_roi_pct"])
    assert plan.type_name == "Tritanium"


def test_the_plan_names_both_generations_and_ages_as_the_older_one(config, worked):
    plan = _scan(config, worked, source_age=15.0, dest_age=200.0).plans[0]
    assert plan.source_generation == (FORGE, SWEEP)
    assert plan.dest_generation == (DOMAIN, "2026-08-25T11:40:00+00:00")
    assert plan.generation_age_minutes == 200.0, "a row is as fresh as its stalest half"


def test_the_trip_is_priced_in_minutes_and_isk_per_minute(config, worked):
    plan = _scan(config, worked, graph=_graph(jumps=4)).plans[0]
    # 4 haul jumps, no pickup (already at the source), 60 s/jump, 5 min handling
    # at each end: 4 + 10 = 14 minutes.
    assert plan.total_jumps == 4
    assert plan.active_minutes == pytest.approx(14.0)
    assert plan.isk_per_active_minute == pytest.approx(plan.net_profit / 14.0)


def test_pickup_jumps_are_charged_when_the_operator_is_somewhere_else(config, worked):
    graph = _graph(jumps=2)
    graph.adjacency.setdefault(50_000, set()).add(JITA)
    graph.adjacency[JITA].add(50_000)
    graph.known_systems.add(50_000)
    graph.security[50_000] = 0.9
    plan = _scan(config, worked, graph=graph, profile=_profile(current_system=50_000)).plans[0]
    assert plan.pickup.jumps == 1
    assert plan.total_jumps == 3, "pickup counts: it is real time in a real ship"


def test_cargo_is_measured_and_utilisation_reported(config, worked):
    plan = _scan(config, worked, profile=_profile(cargo_m3=100.0)).plans[0]
    assert plan.cargo_m3 == pytest.approx(12.0)
    assert plan.cargo_utilisation_pct == pytest.approx(12.0)
    assert plan.profit_per_m3 == pytest.approx(plan.net_profit / 12.0)


# -- 2. what it refuses ----------------------------------------------------


def test_a_stale_generation_on_either_side_prices_nothing(config, worked):
    """Not the fresh leg, not a partial row — the pair prices nothing."""
    stale = _snapshot(
        _orders(worked["destination"]["bids"], buy=True, station=AMARR_8, system=AMARR),
        region=DOMAIN,
        station=AMARR_8,
        system=AMARR,
    )
    stale = DepthSnapshot(
        region_id=DOMAIN,
        frame=stale.frame,
        sweep_ts=SWEEP,
        age_minutes=400.0,
        stale=True,
        reason="depth 400 min old — STALE",
    )
    scan = _scan(config, worked, depths={DOMAIN: stale})
    assert scan.plans == []
    assert scan.rejection_counts[STALE_BOOK] >= 1
    assert scan.unknown_pairs, "the pair renders as UNKNOWN with its reason"
    assert "STALE" in scan.unknown_pairs[0]["reason"]


def test_a_hold_too_small_rejects_the_size_and_says_so(config, worked):
    scan = _scan(config, worked, profile=_profile(cargo_m3=1.0))
    assert scan.plans == []
    rejection = scan.rejected_for(OVER_CARGO)[0]
    assert "m³ of hold" in rejection.detail


def test_capital_and_exposure_are_separate_refusals(config, worked):
    poor = _scan(config, worked, profile=_profile(capital_isk=1000.0, max_exposure_isk=1000.0))
    assert poor.plans == [] and poor.rejected_for(OVER_CAPITAL)

    capped = _scan(config, worked, profile=_profile(capital_isk=5e9, max_exposure_isk=1000.0))
    assert capped.plans == [] and capped.rejected_for(OVER_EXPOSURE)


def test_a_route_that_does_not_exist_rejects_the_pair(config, worked):
    graph = RouteGraph([], {JITA: 0.9, AMARR: 0.9}, sde_build=1)
    scan = _scan(config, worked, graph=graph)
    assert scan.plans == []
    assert scan.rejected_for(NO_ROUTE)


def test_a_low_sec_corridor_is_blocked_by_security_not_called_disconnected(config, worked):
    """The operator can act on 'your profile blocks this'. He cannot act on
    'these systems are not connected', which would be a different claim."""
    scan = _scan(
        config,
        worked,
        graph=_graph(jumps=2, security=0.2),
        profile=_profile(security_profile="highsec"),
    )
    assert scan.plans == []
    assert scan.rejected_for(ROUTE_BLOCKED_SECURITY)
    assert not scan.rejected_for(NO_ROUTE)


def test_too_many_jumps_and_too_few_minutes_are_different_refusals(config, worked):
    far = _scan(config, worked, graph=_graph(jumps=9), profile=_profile(max_jumps=3))
    assert far.rejected_for(OVER_JUMPS) and far.plans == []

    slow = _scan(config, worked, graph=_graph(jumps=9), profile=_profile(session_minutes=5.0))
    assert slow.rejected_for(OVER_TIME) and slow.plans == []


def test_a_chunk_that_does_not_pay_for_itself_is_refused_by_name(config):
    """Bigger is not better. Somewhere the book stops rewarding size, and the
    ranker has to find that point rather than the largest fillable one."""
    asks = [(100.0, 100.0), (400.0, 100.0)]
    bids = [(300.0, 100.0), (200.0, 100.0)]
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
    assert scan.plans[0].quantity == pytest.approx(100.0), "the second 100 units lose money"
    assert scan.rejected_for(MARGINAL_NET_NEGATIVE)


def test_a_truncated_curve_caps_the_size_and_names_the_reason(config, worked):
    truncated = reduce_depth(
        _orders(
            [(100.0, 500.0), (101.0, 500.0), (102.0, 500.0)],
            buy=False,
            station=JITA_44,
            system=JITA,
        ),
        region_id=FORGE,
        stations={JITA_44: JITA},
        bound=DepthBound(max_capital_isk=50_000.0, max_cargo_m3=0.0, safety_margin=1.0),
        sweep_ts=SWEEP,
    )
    assert truncated.truncated_curves == 1
    depths = {
        FORGE: DepthSnapshot(
            region_id=FORGE,
            frame=truncated.frame,
            sweep_ts=SWEEP,
            age_minutes=10.0,
            stale=False,
        ),
        DOMAIN: _snapshot(
            _orders([(200.0, 1500.0)], buy=True, station=AMARR_8, system=AMARR),
            region=DOMAIN,
            station=AMARR_8,
            system=AMARR,
        ),
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
    assert scan.rejected_for(DEPTH_TRUNCATED), "sizes past the stored curve are UNKNOWN"
    assert scan.plans[0].source_depth_complete is False


# -- 3. ranking ------------------------------------------------------------


def test_the_objective_decides_the_size_and_the_others_are_recorded(config):
    """A bigger plan can earn more ISK and less ISK per minute at once."""
    asks = [(100.0, 100.0), (150.0, 400.0)]
    bids = [(400.0, 500.0)]
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
    common = {
        "stations": [SOURCE, DEST],
        "depths": depths,
        "graph": _graph(),
        "names": {34: "Tritanium"},
        "packaged_volume": {34: 0.01},
        "now": NOW,
    }
    roi = scan_hauls(config, _profile(objective="net_roi"), **common).plans[0]
    profit = scan_hauls(config, _profile(objective="net_profit"), **common).plans[0]
    assert roi.quantity == pytest.approx(100.0), "the cheap level is the best return"
    assert profit.quantity == pytest.approx(500.0), "the whole book is the most ISK"
    assert "net_profit" in roi.alternatives
    assert roi.alternatives["net_profit"]["quantity"] == pytest.approx(500.0)


def test_an_unknown_objective_is_a_loud_error(config):
    with pytest.raises(ValueError, match="unknown objective"):
        _profile(objective="vibes")


def test_the_scan_reports_its_own_denominators(config, worked):
    scan = _scan(config, worked)
    assert scan.pairs_considered == 2, "both directions are considered"
    assert scan.candidates_considered >= 2
    payload = scan.as_dict()
    assert payload["sde_build"] == 3478781
    assert any("snapshot is not a tape" in caveat for caveat in payload["caveats"])
    assert any("UNVERIFIED" in caveat for caveat in payload["caveats"])


def test_min_volume_blocked_names_the_depth_the_exit_cannot_reach(config):
    """A bid demanding a big parcel is depth the book shows and you cannot use.

    It is excluded from the executable curve by construction (§23.6), and when
    that exclusion is what caps the size, the rejection says so by name rather
    than reporting a destination that looks merely shallow.
    """
    asks = _orders([(100.0, 5000.0)], buy=False, station=JITA_44, system=JITA)
    bids = _orders([(200.0, 100.0)], buy=True, station=AMARR_8, system=AMARR)
    blocked = _orders([(200.0, 4000.0)], buy=True, station=AMARR_8, system=AMARR, min_volume=500)
    blocked[0]["order_id"] = 9999
    depths = {
        FORGE: _snapshot(asks, region=FORGE, station=JITA_44, system=JITA),
        DOMAIN: _snapshot(bids + blocked, region=DOMAIN, station=AMARR_8, system=AMARR),
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
    rejection = scan.rejected_for("MIN_VOLUME_BLOCKED")
    assert rejection, f"expected MIN_VOLUME_BLOCKED; got {scan.rejection_counts}"
    assert "4,000 units of bid depth" in rejection[0].detail
    assert scan.plans[0].quantity == pytest.approx(100.0), "only the usable bid is fillable"
    assert scan.plans[0].min_volume_excluded_qty == pytest.approx(4000.0)


def test_a_source_shallower_than_the_destination_refuses_nothing(config):
    """Every quantity the source could supply was priced. Reporting the
    destination as short would name a side that is not short at all."""
    asks = _orders([(100.0, 100.0)], buy=False, station=JITA_44, system=JITA)
    bids = _orders([(200.0, 5000.0)], buy=True, station=AMARR_8, system=AMARR)
    depths = {
        FORGE: _snapshot(asks, region=FORGE, station=JITA_44, system=JITA),
        DOMAIN: _snapshot(bids, region=DOMAIN, station=AMARR_8, system=AMARR),
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
    assert scan.plans[0].quantity == pytest.approx(100.0)
    assert not scan.rejected_for("DEST_DEPTH_SHORT")
