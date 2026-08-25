"""Per-station depth, and the walk that prices a quantity (plan.md §23.6).

The fixtures came first (§11 D5). The one that matters most is §23.17's worked
example — 1,200 units bought at a 102,416.67 WAP and sold at 117,375, netting
13,196,312.50 after 3.375% sales tax — because it is the arithmetic every other
number on the hauling page is built out of, and it is checked here at the walk
level and again end to end in `test_hauling.py`.

Everything else here is about what the reduction **refuses** to count: a bid
demanding a parcel bigger than one unit, a bid whose range does not reach the
station, a range nothing can resolve, and a curve the storage bound cut short.
Each of those is a way for exit depth to look larger than it is, which is the
direction that costs real ISK.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evescreener.books import (
    DepthBound,
    DepthCurve,
    curve_from_frame,
    q_walk,
    reachable_from_station,
    reduce_depth,
)
from evescreener.store.lake import DEPTH_COLUMNS

FIXTURES = Path(__file__).parent / "fixtures"
JITA_44, JITA = 60003760, 30000142
PERIMETER, AMARR_SYS = 30000144, 30002187
STRUCTURE = 1_035_466_617_946


@pytest.fixture(scope="module")
def worked() -> dict:
    return json.loads((FIXTURES / "haul_worked_example.json").read_text(encoding="utf-8"))


def _order(**kwargs) -> dict:
    order = {
        "order_id": kwargs.pop("order_id", 1),
        "type_id": 34,
        "location_id": JITA_44,
        "system_id": JITA,
        "price": 100.0,
        "volume_remain": 10.0,
        "volume_total": 10.0,
        "is_buy_order": False,
        "range": None,
        "min_volume": 1,
        "issued": "2026-08-25T10:00:00Z",
    }
    order.update(kwargs)
    return order


def _hops(distances: dict[tuple[int, int], int]):
    """A jump-distance function over a hand-written table; None is UNKNOWN."""

    def distance(origin: int, destination: int) -> int | None:
        if origin == destination:
            return 0
        return distances.get((origin, destination), distances.get((destination, origin)))

    return distance


BOUND = DepthBound(max_capital_isk=1e12, max_cargo_m3=1e12, safety_margin=1.0)


# -- 1. the walk, to the ISK -----------------------------------------------


def test_the_worked_example_walks_to_the_frozen_numbers(worked):
    expected = worked["expected"]
    buy = q_walk(DepthCurve.from_pairs(worked["source"]["asks"]), worked["quantity"])
    sell = q_walk(DepthCurve.from_pairs(worked["destination"]["bids"]), worked["quantity"])
    assert buy.known and sell.known
    assert buy.wap == pytest.approx(expected["source_wap"])
    assert buy.value == pytest.approx(expected["source_cost"])
    assert sell.wap == pytest.approx(expected["destination_wap"])
    assert sell.value == pytest.approx(expected["gross_sale"])
    assert buy.levels_consumed == 2 and sell.levels_consumed == 2

    tax = sell.value * expected["sales_tax_pct"] / 100.0
    assert tax == pytest.approx(expected["sales_tax_isk"])
    assert sell.value - tax - buy.value == pytest.approx(expected["net_profit"])


def test_the_walk_is_by_units_and_not_by_notional():
    """`depth_walk` prices "what does 0.25B buy"; a hold is counted in units."""
    curve = DepthCurve.from_pairs([(100.0, 10.0), (110.0, 10.0)])
    assert q_walk(curve, 15).wap == pytest.approx((100 * 10 + 110 * 5) / 15)


def test_the_marginal_next_price_is_what_the_next_unit_would_cost():
    curve = DepthCurve.from_pairs([(100.0, 10.0), (110.0, 10.0), (130.0, 5.0)])
    # Ending exactly on a level boundary: the next unit comes from the next level.
    assert q_walk(curve, 10).marginal_next_price == pytest.approx(110.0)
    # Ending mid-level: the next unit is still at this level's price.
    assert q_walk(curve, 12).marginal_next_price == pytest.approx(110.0)
    assert q_walk(curve, 25).marginal_next_price is None


def test_a_quantity_past_a_complete_curve_is_unknown_not_extrapolated():
    curve = DepthCurve.from_pairs([(100.0, 10.0)], complete=True)
    walk = q_walk(curve, 25)
    assert walk.known is False and walk.wap is None
    assert "not that deep" in walk.reason


def test_a_quantity_reaching_into_a_truncated_curve_says_which_it_is():
    curve = DepthCurve.from_pairs([(100.0, 10.0)], complete=False)
    walk = q_walk(curve, 25)
    assert walk.known is False
    assert "truncated" in walk.reason, "an unwritten level is not a missing level"


def test_an_empty_curve_and_a_zero_quantity_are_both_unknown():
    assert q_walk(DepthCurve(), 10).known is False
    assert q_walk(DepthCurve.from_pairs([(1.0, 1.0)]), 0).known is False


# -- 2. reachability, the doctrine ------------------------------------------


def test_an_order_at_the_station_is_reachable_whatever_its_range():
    order = _order(is_buy_order=True, range="station")
    assert reachable_from_station(order, station_id=JITA_44, station_system=JITA) == (True, None)


def test_a_sell_order_elsewhere_is_never_reachable():
    """A sell order rests where it rests; it has no range to reach with."""
    order = _order(location_id=60008494, system_id=AMARR_SYS)
    reachable, reason = reachable_from_station(order, station_id=JITA_44, station_system=JITA)
    assert reachable is False and reason == "range_out_of_reach"


def test_a_region_ranged_bid_reaches_from_anywhere_in_the_region():
    order = _order(is_buy_order=True, range="region", location_id=99, system_id=PERIMETER)
    assert reachable_from_station(order, station_id=JITA_44, station_system=JITA)[0] is True


def test_a_structure_resting_region_ranged_bid_is_included():
    """Docking rights are not the question — the seller never docks there.

    §17 measured 8.8–98.3% of bid volume resting in player structures, so
    excluding them on ownership would throw away most of the exit book. What
    decides is RANGE, from the order's own location (§22 S2a).
    """
    order = _order(is_buy_order=True, range="region", location_id=STRUCTURE, system_id=AMARR_SYS)
    assert reachable_from_station(order, station_id=JITA_44, station_system=JITA)[0] is True


def test_a_solarsystem_ranged_bid_reaches_only_inside_its_own_system():
    same = _order(is_buy_order=True, range="solarsystem", location_id=99, system_id=JITA)
    other = _order(is_buy_order=True, range="solarsystem", location_id=99, system_id=PERIMETER)
    assert reachable_from_station(same, station_id=JITA_44, station_system=JITA)[0] is True
    assert reachable_from_station(other, station_id=JITA_44, station_system=JITA) == (
        False,
        "range_out_of_reach",
    )


def test_a_numeric_range_is_decided_by_the_stargate_graph():
    """The rule R1 had to fail closed on, now decidable (§21 R1 → §23.6)."""
    hops = _hops({(PERIMETER, JITA): 1, (AMARR_SYS, JITA): 11})
    near = _order(is_buy_order=True, range="5", location_id=99, system_id=PERIMETER)
    far = _order(is_buy_order=True, range="5", location_id=99, system_id=AMARR_SYS)
    assert reachable_from_station(
        near, station_id=JITA_44, station_system=JITA, jump_distance=hops
    ) == (True, None)
    assert reachable_from_station(
        far, station_id=JITA_44, station_system=JITA, jump_distance=hops
    ) == (False, "range_out_of_reach")


def test_a_numeric_range_with_no_graph_is_unknown_and_excluded():
    order = _order(is_buy_order=True, range="5", location_id=99, system_id=PERIMETER)
    assert reachable_from_station(order, station_id=JITA_44, station_system=JITA) == (
        False,
        "range_unresolvable",
    )


def test_an_order_with_no_system_or_a_nonsense_range_is_unresolvable():
    hops = _hops({})
    no_system = _order(is_buy_order=True, range="5", location_id=99, system_id=None)
    nonsense = _order(is_buy_order=True, range="nearby", location_id=99, system_id=PERIMETER)
    for order in (no_system, nonsense):
        reachable, reason = reachable_from_station(
            order, station_id=JITA_44, station_system=JITA, jump_distance=hops
        )
        assert reachable is False and reason == "range_unresolvable"


# -- 3. the reduction -------------------------------------------------------


def test_min_volume_puts_depth_out_of_reach_and_says_how_much():
    """A bid that will not take a small parcel is not exit depth today."""
    orders = [
        _order(order_id=1, is_buy_order=True, range="station", price=90.0, volume_remain=100.0),
        _order(
            order_id=2,
            is_buy_order=True,
            range="station",
            price=90.0,
            volume_remain=400.0,
            min_volume=250,
        ),
    ]
    reduction = reduce_depth(orders, region_id=10000002, stations={JITA_44: JITA}, bound=BOUND)
    row = reduction.frame.iloc[0]
    assert row["level_qty"] == 100.0, "the blocked 400 units are not executable"
    assert row["min_volume_excluded_qty"] == 400.0, "and they are not invisible either"
    assert reduction.excluded_min_volume == 1
    assert reduction.min_volume_excluded_qty == 400.0


def test_a_sell_order_is_never_min_volume_blocked():
    orders = [_order(price=100.0, volume_remain=50.0, min_volume=999)]
    reduction = reduce_depth(orders, region_id=10000002, stations={JITA_44: JITA}, bound=BOUND)
    assert reduction.frame.iloc[0]["level_qty"] == 50.0
    assert reduction.excluded_min_volume == 0


def test_identical_prices_aggregate_after_the_filters_not_before():
    orders = [
        _order(order_id=1, price=100.0, volume_remain=10.0, issued="2026-08-01T00:00:00Z"),
        _order(order_id=2, price=100.0, volume_remain=15.0, issued="2026-08-20T00:00:00Z"),
        _order(order_id=3, price=110.0, volume_remain=5.0),
    ]
    frame = reduce_depth(
        orders, region_id=10000002, stations={JITA_44: JITA}, bound=BOUND
    ).frame.sort_values("price")
    best = frame.iloc[0]
    assert best["level_qty"] == 25.0 and best["level_order_count"] == 2
    assert best["oldest_issued"] == "2026-08-01T00:00:00Z"
    assert best["newest_issued"] == "2026-08-20T00:00:00Z"
    assert list(frame["cumulative_qty"]) == [25.0, 30.0]


def test_the_structure_share_of_a_level_is_carried(worked):
    orders = [
        _order(order_id=1, price=100.0, volume_remain=30.0),
        _order(order_id=2, price=100.0, volume_remain=10.0, location_id=STRUCTURE),
    ]
    # Both rest at different locations, so only the station's own sell order is
    # reachable; the structure's is not, which is the point of the next assert.
    frame = reduce_depth(orders, region_id=10000002, stations={JITA_44: JITA}, bound=BOUND).frame
    assert frame.iloc[0]["level_qty"] == 30.0
    assert frame.iloc[0]["structure_share"] == pytest.approx(0.0)


def test_a_bid_resting_in_a_structure_contributes_its_share_of_the_level():
    orders = [
        _order(order_id=1, is_buy_order=True, range="region", price=90.0, volume_remain=30.0),
        _order(
            order_id=2,
            is_buy_order=True,
            range="region",
            price=90.0,
            volume_remain=10.0,
            location_id=STRUCTURE,
            system_id=AMARR_SYS,
        ),
    ]
    frame = reduce_depth(orders, region_id=10000002, stations={JITA_44: JITA}, bound=BOUND).frame
    assert frame.iloc[0]["level_qty"] == 40.0
    assert frame.iloc[0]["structure_share"] == pytest.approx(0.25)


def test_the_bound_truncates_and_the_truncation_makes_the_walk_unknown():
    orders = [
        _order(order_id=index, price=100.0 + index, volume_remain=100.0) for index in range(10)
    ]
    bound = DepthBound(max_capital_isk=20_000.0, max_cargo_m3=0.0, safety_margin=1.0)
    reduction = reduce_depth(orders, region_id=10000002, stations={JITA_44: JITA}, bound=bound)
    assert reduction.truncated_curves == 1
    assert len(reduction.frame) < 10
    assert not reduction.frame["depth_complete"].any()

    curve = curve_from_frame(
        reduction.frame, type_id=34, side="sell", execution_location_id=JITA_44
    )
    assert curve.complete is False
    assert q_walk(curve, curve.available_qty).known is True, "inside the stored curve is priceable"
    assert q_walk(curve, curve.available_qty + 1).known is False


def test_a_curve_inside_the_bound_is_complete():
    orders = [_order(order_id=index, price=100.0 + index, volume_remain=1.0) for index in range(3)]
    reduction = reduce_depth(orders, region_id=10000002, stations={JITA_44: JITA}, bound=BOUND)
    assert reduction.truncated_curves == 0
    assert reduction.frame["depth_complete"].all()


def test_the_cargo_condition_also_has_to_be_met_before_a_curve_is_cut():
    """Capital alone is not enough: a cheap, bulky type fills the hold first."""
    orders = [
        _order(order_id=index, price=1.0 + index * 0.01, volume_remain=100.0, type_id=34)
        for index in range(10)
    ]
    cheap = DepthBound(
        max_capital_isk=100.0,
        max_cargo_m3=5_000.0,
        safety_margin=1.0,
        packaged_volume={34: 10.0},
    )
    reduction = reduce_depth(orders, region_id=10000002, stations={JITA_44: JITA}, bound=cheap)
    # 100 ISK of capital is covered by the first level, but 5,000 m³ needs 500
    # units, which is five levels.
    assert len(reduction.frame) >= 5


def test_every_station_gets_its_own_curve():
    orders = [
        _order(order_id=1, price=100.0, volume_remain=10.0, location_id=JITA_44, system_id=JITA),
        _order(
            order_id=2, price=90.0, volume_remain=10.0, location_id=60008494, system_id=AMARR_SYS
        ),
    ]
    reduction = reduce_depth(
        orders,
        region_id=10000002,
        stations={JITA_44: JITA, 60008494: AMARR_SYS},
        bound=BOUND,
    )
    by_station = reduction.frame.groupby("execution_location_id")["price"].min().to_dict()
    assert by_station == {JITA_44: 100.0, 60008494: 90.0}


def test_the_frame_has_exactly_the_declared_columns():
    reduction = reduce_depth([_order()], region_id=10000002, stations={JITA_44: JITA}, bound=BOUND)
    assert list(reduction.frame.columns) == DEPTH_COLUMNS


def test_the_reduction_searches_from_the_station_not_from_every_order():
    """A Forge sweep carries orders resting in thousands of systems.

    Jump distance is symmetric on a stargate graph, so a search rooted at each
    order's system gives the same answers as one rooted at the station — and
    builds thousands of distance maps to do it. This asserts the cheap
    direction is the one taken, because the expensive one is correct enough to
    pass every other test in this file while making a real sweep unusable.
    """
    from evescreener.routes import RouteGraph

    systems = {JITA: 0.9, PERIMETER: 0.9, AMARR_SYS: 0.9}
    graph = RouteGraph([(JITA, PERIMETER), (PERIMETER, AMARR_SYS)], systems, sde_build=1)
    orders = [
        _order(
            order_id=index,
            is_buy_order=True,
            range="5",
            location_id=99,
            system_id=system,
            price=90.0 + index,
            volume_remain=10.0,
        )
        for index, system in enumerate((PERIMETER, AMARR_SYS))
    ]
    reduction = reduce_depth(
        orders,
        region_id=10000002,
        stations={JITA_44: JITA},
        bound=BOUND,
        jump_distance=graph.jump_distance,
    )
    assert len(reduction.frame) == 2, "both bids reach a five-jump range"
    assert list(graph._distance_cache) == [(JITA, 40)], "one search, rooted at the station"
