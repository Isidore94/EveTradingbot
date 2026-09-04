"""The basket must never under-earn its own best part (§23.21).

Measured 2026-09-04 on two real generations: greedy-by-ISK/m³ filled 250 M ISK
with 1.8 m³ of formulas across four hubs and earned 42–66% of the best single
plan on the same capital. Two rules follow. The score follows the binding
constraint (capital or hold), and a basket is one trip: one destination. And
whatever the heuristic does, the operator is never shown a basket worse than
the best single plan he could take instead.
"""

from __future__ import annotations

import pytest

from evescreener.positioning import greedy_basket
from test_positioning import DEST, DODIXIE, _plan, _scan_of


def test_the_greedy_is_floored_at_the_best_feasible_single_plan():
    formula = _plan(1, "Formula", [(1.0, 100e6, 10e6)], volume_m3=0.01)
    bulk = _plan(2, "Bulk", [(1000.0, 100e6, 30e6)], volume_m3=1.0)
    basket = greedy_basket(
        [formula, bulk], capital_isk=100e6, cargo_m3=60_000.0, score="isk_per_m3"
    )
    assert basket.net_isk == pytest.approx(30e6)
    assert [item.type_id for item in basket.items] == [2]
    assert any("under-earned" in note for note in basket.notes)


def test_the_floor_ignores_a_single_plan_the_caps_would_refuse():
    formula = _plan(1, "Formula", [(1.0, 100e6, 10e6)], volume_m3=0.01)
    bulk = _plan(2, "Bulk", [(1000.0, 100e6, 30e6)], volume_m3=1.0)
    basket = greedy_basket(
        [formula, bulk],
        capital_isk=100e6,
        cargo_m3=100.0,  # the bulk plan does not fit the hold
        score="isk_per_m3",
    )
    assert [item.type_id for item in basket.items] == [1]
    assert basket.net_isk == pytest.approx(10e6)


def test_auto_scores_by_capital_when_the_wallet_binds():
    formula = _plan(1, "Formula", [(1.0, 100e6, 10e6)], volume_m3=0.01)
    bulk = _plan(2, "Bulk", [(1000.0, 100e6, 30e6)], volume_m3=1.0)
    basket = greedy_basket([formula, bulk], capital_isk=100e6, cargo_m3=60_000.0, score="auto")
    assert basket.score == "isk_per_capital"
    assert basket.net_isk == pytest.approx(30e6)


def test_auto_scores_by_volume_when_the_hold_binds():
    dense = _plan(1, "Dense", [(100.0, 1000.0, 900.0)], volume_m3=1.0)
    bulky = _plan(2, "Bulky", [(400.0, 4000.0, 1200.0)], volume_m3=1.0)
    basket = greedy_basket([dense, bulky], capital_isk=1e9, cargo_m3=500.0, score="auto")
    assert basket.score == "isk_per_m3"
    assert basket.net_isk == pytest.approx(2100.0)


def test_a_basket_is_one_trip_and_names_the_destination():
    to_amarr = _plan(1, "A", [(100.0, 1000.0, 900.0)], destination=DEST)
    to_dodixie = _plan(2, "B", [(100.0, 1000.0, 800.0)], destination=DODIXIE)
    basket = greedy_basket(
        [to_amarr, to_dodixie], capital_isk=1e9, cargo_m3=1e9, single_destination=True
    )
    assert basket.destination == DEST.station_id
    assert [item.type_id for item in basket.items] == [1]
    assert any("one trip" in note for note in basket.notes)
    mixed = greedy_basket([to_amarr, to_dodixie], capital_isk=1e9, cargo_m3=1e9)
    assert len(mixed.items) == 2, "the primitive still mixes destinations when asked to"
    assert mixed.destination is None


def test_the_report_basket_is_one_trip_scored_by_the_binding_constraint(config):
    from evescreener.haulreport import haul_basket

    # The config caps one destination at 50% of capital (50 M here), so only
    # one 40 M plan fits a trip: by ISK/m³ the greedy takes the formula (4 M),
    # by ISK/capital it takes the bulk (12 M). The wallet binds; capital wins.
    formula = _plan(1, "Formula", [(1.0, 40e6, 4e6)], volume_m3=0.01, destination=DEST)
    bulk = _plan(2, "Bulk", [(1000.0, 40e6, 12e6)], volume_m3=1.0, destination=DEST)
    elsewhere = _plan(3, "Elsewhere", [(10.0, 1e6, 1e6)], volume_m3=1.0, destination=DODIXIE)
    scan = _scan_of(config, [formula, bulk, elsewhere])
    scan.profile = type(scan.profile)(
        current_system=scan.profile.current_system,
        ship=type(scan.profile.ship)(name="t", usable_cargo_m3=60_000.0),
        capital_isk=100e6,
        max_exposure_isk=100e6,
    )
    basket = haul_basket(scan, config=config)
    assert basket.destination == DEST.station_id
    assert basket.net_isk == pytest.approx(12e6)
    assert basket.score == "isk_per_capital"
    assert basket.capital_isk <= 50e6, "the per-destination cap still binds the floor"


def test_the_floor_honours_the_per_destination_cap():
    """A single plan that breaches the destination cap is not a floor."""
    big = _plan(1, "Big", [(1000.0, 100e6, 30e6)], volume_m3=1.0)
    small = _plan(2, "Small", [(10.0, 10e6, 1e6)], volume_m3=1.0)
    basket = greedy_basket(
        [big, small], capital_isk=1e9, cargo_m3=1e9, exposure_per_destination_isk=50e6
    )
    assert [item.type_id for item in basket.items] == [2]
    assert basket.net_isk == pytest.approx(1e6)


def test_an_unknown_score_is_a_loud_error():
    plan = _plan(1, "A", [(100.0, 1000.0, 900.0)])
    with pytest.raises(ValueError):
        greedy_basket([plan], capital_isk=1e9, cargo_m3=1e9, score="by_vibes")
