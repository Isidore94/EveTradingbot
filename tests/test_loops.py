"""Loops: a hauler's unit of work is out AND back (§23.21).

The ranking is one-way and every one-way ISK per minute silently assumes the
return leg is free. Composing loops from plans already priced needs no new
walk: the legs are the best plan per ordered station pair, the minutes add,
and the capital committed is the peak outlay after the previous leg's
proceeds. Measured on the 2026-08-28 lake: Jita ↔ Dodixie 23.9 M in 57 min.
"""

from __future__ import annotations

import pytest

from evescreener.hauling import HaulPlan, HaulProfile, ShipProfile, Station
from evescreener.loops import compose_loops, render_loops
from evescreener.routes import RouteFacts

JITA = Station(60003760, 30000142, 10000002, "Jita")
AMARR = Station(60008494, 30002187, 10000043, "Amarr")
DODIXIE = Station(60011866, 30002659, 10000032, "Dodixie")


def _route(jumps: int) -> RouteFacts:
    return RouteFacts(
        origin=None, destination=None, profile="shortest", jumps=jumps, known=True, sde_build=1
    )


def _plan(type_id, name, source, destination, *, cost, net, haul_jumps, pickup_jumps=0):
    minutes = (pickup_jumps + haul_jumps) * 1.0 + 10.0  # 60 s/jump, 5 min handling x2
    return HaulPlan(
        type_id=type_id,
        type_name=name,
        badge=None,
        source=source,
        destination=destination,
        quantity=1.0,
        source_wap=cost,
        source_cost=cost,
        source_levels=1,
        source_marginal_next_price=None,
        dest_wap=cost + net,
        gross_sale=cost + net,
        dest_levels=1,
        dest_marginal_next_price=None,
        sales_tax_isk=0.0,
        net_profit=net,
        net_roi_pct=net / cost * 100.0,
        marginal_net_isk=None,
        packaged_volume_m3=1.0,
        cargo_m3=1.0,
        cargo_utilisation_pct=None,
        profit_per_m3=net,
        pickup=_route(pickup_jumps),
        haul=_route(haul_jumps),
        total_jumps=pickup_jumps + haul_jumps,
        detour_jumps=None,
        active_minutes=minutes,
        isk_per_active_minute=net / minutes,
        breakpoints=((1.0, cost, net, False),),
    )


def _profile(**overrides) -> HaulProfile:
    ship = ShipProfile(name="t", usable_cargo_m3=1e6, seconds_per_jump=60.0, handling_minutes=5.0)
    defaults = {
        "current_system": 30000142,
        "ship": ship,
        "capital_isk": 1e9,
        "max_exposure_isk": 1e9,
        "session_minutes": 600.0,
    }
    defaults.update(overrides)
    return HaulProfile(**defaults)


def test_a_loop_adds_both_legs_and_charges_the_return_minutes():
    out = _plan(1, "A", JITA, AMARR, cost=100.0, net=10.0, haul_jumps=10)
    back = _plan(2, "B", AMARR, JITA, cost=105.0, net=8.0, haul_jumps=10)
    loops = compose_loops([out, back], profile=_profile())
    assert loops.considered >= 1
    loop = loops.loops[0]
    assert [leg.type_id for leg in loop.legs] == [1, 2]
    assert loop.net_isk == pytest.approx(18.0)
    # First leg: 10 jumps + 10 min handling = 20; return: 10 jumps + 10 = 20.
    assert loop.active_minutes == pytest.approx(40.0)
    assert loop.isk_per_active_minute == pytest.approx(18.0 / 40.0)
    # Peak capital: 100 out, then 105 - (100 + 10) proceeds = 5 short -> max 100.
    assert loop.capital_committed_isk == pytest.approx(100.0)


def test_the_best_plan_per_leg_is_chosen_by_net():
    weak = _plan(1, "A", JITA, AMARR, cost=100.0, net=1.0, haul_jumps=10)
    strong = _plan(3, "C", JITA, AMARR, cost=100.0, net=20.0, haul_jumps=10)
    back = _plan(2, "B", AMARR, JITA, cost=100.0, net=8.0, haul_jumps=10)
    loops = compose_loops([weak, strong, back], profile=_profile())
    assert loops.loops[0].legs[0].type_id == 3


def test_a_circuit_visits_a_third_station_and_returns():
    legs = [
        _plan(1, "A", JITA, AMARR, cost=100.0, net=10.0, haul_jumps=10),
        _plan(2, "B", AMARR, DODIXIE, cost=100.0, net=10.0, haul_jumps=10),
        _plan(3, "C", DODIXIE, JITA, cost=100.0, net=10.0, haul_jumps=10),
    ]
    loops = compose_loops(legs, profile=_profile(), max_stops=3)
    circuit = [loop for loop in loops.loops if len(loop.legs) == 3]
    assert circuit, "a three-stop circuit exists in this data"
    assert circuit[0].stations == (JITA.station_id, AMARR.station_id, DODIXIE.station_id)
    assert circuit[0].net_isk == pytest.approx(30.0)
    assert compose_loops(legs, profile=_profile(), max_stops=2).loops == []


def test_a_loop_that_does_not_fit_the_session_is_counted_not_shown():
    out = _plan(1, "A", JITA, AMARR, cost=100.0, net=10.0, haul_jumps=10)
    back = _plan(2, "B", AMARR, JITA, cost=100.0, net=8.0, haul_jumps=10)
    loops = compose_loops([out, back], profile=_profile(session_minutes=30.0))
    assert loops.loops == []
    assert loops.over_session == 1


def test_one_way_plans_compose_no_loop_and_say_so():
    out = _plan(1, "A", JITA, AMARR, cost=100.0, net=10.0, haul_jumps=10)
    loops = compose_loops([out], profile=_profile())
    assert loops.loops == []
    text = render_loops(loops)
    assert "no loop" in text.lower()


def test_the_render_and_the_dict_carry_the_legs():
    out = _plan(1, "A", JITA, AMARR, cost=100.0, net=10.0, haul_jumps=10)
    back = _plan(2, "B", AMARR, JITA, cost=105.0, net=8.0, haul_jumps=10)
    loops = compose_loops([out, back], profile=_profile())
    payload = loops.as_dict()
    assert payload["loops"][0]["legs"][0]["type_name"] == "A"
    assert payload["loops"][0]["net_isk"] == pytest.approx(18.0)
    text = render_loops(loops)
    assert "LOOPS" in text and "A" in text and "B" in text
    assert "return leg" in text.lower()
