"""Mixed cargo, and the word HEURISTIC on the label (plan.md §23, H3).

Filling a hold optimally is a knapsack over marginal chunks whose prices move
as you take them. This is greedy, it is not optimal, and it says so on every
output. What these tests pin is that it is **checkable**: the chunks are the
same breakpoints the ranker already priced, the order is by conservative profit
per m³, and every cap is re-tested before each chunk rather than once at the
end — the case where a cap must bind is as important as the case where mixing
wins.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from evescreener.hauling import HaulPlan, Station
from evescreener.positioning import greedy_basket, marginal_chunks, render_basket
from evescreener.routes import RouteFacts

SOURCE = Station(60003760, 30000142, 10000002, "Jita")
DEST = Station(60008494, 30002187, 10000043, "Amarr")
UNKNOWN_ROUTE = RouteFacts.unknown(None, None, "shortest", "")


def _labelled(breakpoints):
    """`(q, cost, net)` in, `(q, cost, net, rejected)` out — the engine's rule.

    The ranker stops at the first chunk whose marginal net is <= 0 and keeps
    that size in the audit, flagged. The helper reproduces the flag rather than
    hard-coding False, so a test written with a losing tail gets the same shape
    a real scan would have produced.
    """
    laid = []
    previous_net = 0.0
    for quantity, cost, net in breakpoints:
        laid.append((quantity, cost, net, net - previous_net <= 0))
        previous_net = net
    return laid


def _plan(type_id, name, breakpoints, *, volume_m3=1.0, destination=DEST) -> HaulPlan:
    quantity, cost, net = breakpoints[-1]
    return HaulPlan(
        type_id=type_id,
        type_name=name,
        badge=None,
        source=SOURCE,
        destination=destination,
        quantity=quantity,
        source_wap=cost / quantity,
        source_cost=cost,
        source_levels=1,
        source_marginal_next_price=None,
        dest_wap=(cost + net) / quantity,
        gross_sale=cost + net,
        dest_levels=1,
        dest_marginal_next_price=None,
        sales_tax_isk=0.0,
        net_profit=net,
        net_roi_pct=net / cost * 100.0,
        marginal_net_isk=None,
        packaged_volume_m3=volume_m3,
        cargo_m3=(quantity * volume_m3) if volume_m3 else None,
        cargo_utilisation_pct=None,
        profit_per_m3=(net / (quantity * volume_m3)) if volume_m3 else None,
        pickup=UNKNOWN_ROUTE,
        haul=UNKNOWN_ROUTE,
        total_jumps=1,
        detour_jumps=None,
        active_minutes=10.0,
        isk_per_active_minute=net / 10.0,
        breakpoints=tuple(_labelled(breakpoints)),
    )


# -- 1. the chunks ---------------------------------------------------------


def test_the_chunks_are_the_breakpoints_the_ranker_already_priced():
    plan = _plan(34, "Tritanium", [(100.0, 1000.0, 500.0), (200.0, 2500.0, 700.0)])
    chunks = marginal_chunks(plan)
    assert [chunk.quantity for chunk in chunks] == [100.0, 100.0]
    assert [chunk.capital_isk for chunk in chunks] == [1000.0, 1500.0]
    assert [chunk.net_isk for chunk in chunks] == [500.0, 200.0]
    assert chunks[0].profit_per_m3 == pytest.approx(5.0)


def test_a_chunk_that_does_not_pay_for_itself_is_not_offered():
    """It is the same size the ranker refused as MARGINAL_NET_NEGATIVE."""
    plan = _plan(34, "Tritanium", [(100.0, 1000.0, 500.0), (200.0, 3000.0, 400.0)])
    assert len(marginal_chunks(plan)) == 1


# -- 2. mixing wins ---------------------------------------------------------


def test_a_mixed_hold_beats_the_best_single_item_when_the_hold_is_the_binding_cap():
    """The best item runs out of book long before the hold runs out of space."""
    dense = _plan(34, "Tritanium", [(100.0, 1000.0, 900.0)], volume_m3=1.0)
    bulky = _plan(35, "Pyerite", [(400.0, 4000.0, 1200.0)], volume_m3=1.0)
    basket = greedy_basket([dense, bulky], capital_isk=1e9, cargo_m3=500.0)
    assert len(basket.items) == 2
    assert basket.net_isk == pytest.approx(2100.0)
    assert basket.net_isk > dense.net_profit, "mixing beat the best single plan"
    assert basket.volume_m3 == pytest.approx(500.0)
    assert basket.method == "HEURISTIC"


def test_the_hold_is_filled_by_profit_per_cubic_metre_first():
    thin = _plan(34, "Tritanium", [(100.0, 1000.0, 100.0)], volume_m3=1.0)
    rich = _plan(35, "Pyerite", [(100.0, 1000.0, 900.0)], volume_m3=1.0)
    basket = greedy_basket([thin, rich], capital_isk=1e9, cargo_m3=100.0)
    assert [item.type_id for item in basket.items] == [35]


# -- 3. and the caps still bind --------------------------------------------


def test_the_exposure_cap_binds_and_the_basket_must_not_beat_it():
    """A cap tested against the total is a cap already exceeded on the way."""
    plan = _plan(
        34,
        "Tritanium",
        [(100.0, 1000.0, 500.0), (200.0, 2000.0, 900.0), (300.0, 3000.0, 1200.0)],
        volume_m3=1.0,
    )
    basket = greedy_basket([plan], capital_isk=1e9, cargo_m3=1e9, exposure_per_trade_isk=2000.0)
    assert basket.capital_isk <= 2000.0
    assert basket.items[0].quantity == pytest.approx(200.0)


def test_capital_binds_across_items_not_just_within_one():
    first = _plan(34, "A", [(100.0, 1000.0, 900.0)], volume_m3=1.0)
    second = _plan(35, "B", [(100.0, 1000.0, 800.0)], volume_m3=1.0)
    basket = greedy_basket([first, second], capital_isk=1500.0, cargo_m3=1e9)
    assert basket.capital_isk <= 1500.0
    assert len(basket.items) == 1, "the second item does not fit the wallet"


def test_a_per_destination_cap_binds_across_plans_going_to_the_same_hub():
    first = _plan(34, "A", [(100.0, 1000.0, 900.0)])
    second = _plan(35, "B", [(100.0, 1000.0, 800.0)])
    basket = greedy_basket(
        [first, second],
        capital_isk=1e9,
        cargo_m3=1e9,
        exposure_per_destination_isk=1000.0,
    )
    assert basket.capital_isk <= 1000.0


def test_an_item_with_no_measurable_volume_is_skipped_and_named():
    """Packing a hold with something whose size nobody knows is how a plan
    becomes unexecutable at the station."""
    unknown = _plan(34, "Mystery", [(100.0, 1000.0, 900.0)], volume_m3=None)
    basket = greedy_basket([unknown], capital_isk=1e9, cargo_m3=1e9)
    assert basket.items == []
    assert any("packaged volume UNKNOWN" in note for note in basket.skipped)


def test_a_basket_that_fits_nothing_says_so_rather_than_rendering_empty():
    plan = _plan(34, "Tritanium", [(100.0, 1_000_000.0, 900.0)])
    basket = greedy_basket([plan], capital_isk=1.0, cargo_m3=1e9)
    assert basket.items == []
    assert any("Nothing fits" in note for note in basket.notes)


def test_the_render_leads_with_the_label():
    plan = _plan(34, "Tritanium", [(100.0, 1000.0, 900.0)])
    text = render_basket(greedy_basket([plan], capital_isk=1e9, cargo_m3=1e9))
    assert text.startswith("MIXED CARGO — HEURISTIC")
    assert "not an optimum" in text


def test_the_per_destination_cap_reaches_the_basket_from_config(config):
    """A setting nothing reads is §22 S6's defect wearing a different name."""
    from evescreener.hauling import HaulProfile, HaulScan, ShipProfile
    from evescreener.haulreport import haul_basket

    profile = HaulProfile(
        current_system=30000142,
        ship=ShipProfile(name="test", usable_cargo_m3=1e9),
        capital_isk=1000.0,
        max_exposure_isk=1000.0,
    )
    scan = HaulScan(generated_at="2026-08-25T12:00:00+00:00", profile=profile)
    scan.plans = [
        _plan(34, "A", [(100.0, 400.0, 300.0)]),
        _plan(35, "B", [(100.0, 400.0, 200.0)]),
        _plan(36, "C", [(100.0, 400.0, 100.0)]),
    ]
    # 50% of 1,000 ISK caps one destination at 500, so the third 400-ISK chunk
    # to the same hub cannot be taken however profitable it looks.
    basket = haul_basket(scan, config=config)
    assert config.hauling.max_exposure_pct_per_destination == 50.0
    assert basket.capital_isk <= 500.0
    assert len(basket.items) == 1
    # Without the config the cap is simply absent, which is the old behaviour:
    # two 400-ISK chunks fit the 1,000 wallet and the third does not.
    assert haul_basket(scan).capital_isk == pytest.approx(800.0)


# -- 4. one book, spent once -----------------------------------------------

DODIXIE = Station(60011866, 30002659, 10000032, "Dodixie")


def _scan_of(config, plans):
    from evescreener.hauling import HaulProfile, HaulScan, ShipProfile

    profile = HaulProfile(
        current_system=30000142,
        ship=ShipProfile(name="test", usable_cargo_m3=1e9),
        capital_isk=1e12,
        max_exposure_isk=1e12,
    )
    scan = HaulScan(generated_at="2026-08-25T12:00:00+00:00", profile=profile)
    scan.plans = list(plans)
    return scan


def test_the_bare_primitive_cannot_double_spend_a_book_either(config):
    """The guard has to live with the packing, not beside it.

    `haul_basket` filtered correctly and `greedy_basket` did not, so the only
    thing standing between a caller and two thousand units out of a
    thousand-unit ask was remembering to call the wrapper. Every test below
    goes through the wrapper, which is why nothing caught it.
    """
    to_amarr = _plan(34, "Tritanium", [(1000.0, 10_000.0, 5_000.0)], destination=DEST)
    to_dodixie = _plan(34, "Tritanium", [(1000.0, 10_000.0, 4_000.0)], destination=DODIXIE)
    basket = greedy_basket([to_amarr, to_dodixie], capital_isk=1e9, cargo_m3=1e9)
    assert sum(item.quantity for item in basket.items) <= 1000.0
    assert basket.withheld_for_overlap == 1
    assert any("withheld" in note for note in basket.notes)


def test_one_source_book_cannot_be_packed_twice_into_the_same_hold(config):
    """The same 1,000-unit Jita ask sold to two hubs is ONE 1,000-unit ask.

    The scan ranks (item, source, destination) plans independently, which is
    right — they are alternatives. The basket then packed all of them, so a
    thousand units of measured depth became two thousand units of cargo.
    """
    from evescreener.haulreport import haul_basket

    to_amarr = _plan(34, "Tritanium", [(1000.0, 10_000.0, 5_000.0)], destination=DEST)
    to_dodixie = _plan(34, "Tritanium", [(1000.0, 10_000.0, 4_000.0)], destination=DODIXIE)
    basket = haul_basket(_scan_of(config, [to_amarr, to_dodixie]))
    packed = sum(item.quantity for item in basket.items if item.type_id == 34)
    assert packed <= 1000.0, "the source book was spent twice"
    assert len(basket.items) == 1
    assert any("withheld" in note for note in basket.notes), "the restriction must be visible"


def test_one_destination_book_cannot_be_sold_into_twice(config):
    """The mirror case: two sources feeding one destination bid book."""
    from evescreener.hauling import Station as _Station
    from evescreener.haulreport import haul_basket

    other_source = _Station(60005686, 30002053, 10000042, "Hek")
    first = _plan(34, "Tritanium", [(1000.0, 10_000.0, 5_000.0)], destination=DEST)
    second = _plan(34, "Tritanium", [(1000.0, 10_000.0, 4_000.0)], destination=DEST)
    second = replace(second, source=other_source)
    basket = haul_basket(_scan_of(config, [first, second]))
    assert sum(item.quantity for item in basket.items) <= 1000.0


def test_two_distinct_types_are_both_packed(config):
    """The restriction must not over-reach: different books do not overlap."""
    from evescreener.haulreport import haul_basket

    first = _plan(34, "Tritanium", [(100.0, 1000.0, 900.0)])
    second = _plan(35, "Pyerite", [(100.0, 1000.0, 800.0)])
    basket = haul_basket(_scan_of(config, [first, second]))
    assert {item.type_id for item in basket.items} == {34, 35}
    assert not any("withheld" in note for note in basket.notes)


def test_the_item_cap_admits_what_it_says_it_admits():
    """`while len(taken) <= max_items` admitted max_items + 1."""
    plans = [_plan(100 + index, f"T{index}", [(10.0, 100.0, 90.0)]) for index in range(6)]
    basket = greedy_basket(plans, capital_isk=1e9, cargo_m3=1e9, max_items=3)
    assert len(basket.items) == 3


def test_a_chunk_knows_which_book_it_came_from():
    chunk = marginal_chunks(_plan(34, "Tritanium", [(100.0, 1000.0, 900.0)]))[0]
    assert chunk.source == SOURCE.station_id
    assert chunk.destination == DEST.station_id
