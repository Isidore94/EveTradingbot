"""Opportunistic hauling, and what flying it yourself is worth (§23, H4).

Two additions, and both are about charging the right thing.

**`along_route` charges the detour, not the trip.** If the operator was flying
Jita → Rens anyway, a pickup that costs him two extra jumps costs two jumps —
not the whole route. A zero-detour pickup still pays handling, because loading
and unloading are real minutes even when the flying is free.

**The PushX comparison is a column, never a dependency.** `quote_freight` is
reused verbatim, cache and haircut included; no quote means the column reads
UNKNOWN and the self-haul row stays exactly as priced. What the column actually
answers is "what is my flying time worth on this haul", which is the only form
of the question with a number in it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from evescreener.crossregion import FreightQuote
from evescreener.haulfreight import attach_freight, freight_comparison
from evescreener.hauling import ALONG_ROUTE, NO_ROUTE, Station, scan_hauls
from evescreener.routes import RouteGraph
from test_hauling import DEST, DOMAIN, FORGE, NOW, SOURCE, _orders, _profile, _snapshot

RENS_SYS, RENS_STATION, HEIMATAR = 30002510, 60004588, 10000030
STAGING = Station(60099999, 30002510, HEIMATAR, "Rens", None)


def _corridor() -> RouteGraph:
    """Jita — A — Amarr — B — Rens: a line, so detours are countable by hand."""
    systems = {30000142: 0.9, 90001: 0.9, 30002187: 0.9, 90002: 0.9, RENS_SYS: 0.9}
    edges = [(30000142, 90001), (90001, 30002187), (30002187, 90002), (90002, RENS_SYS)]
    return RouteGraph(edges, systems, sde_build=3478781)


def _depths(worked):
    return {
        FORGE: _snapshot(
            _orders(
                worked["source"]["asks"],
                buy=False,
                station=SOURCE.station_id,
                system=SOURCE.system_id,
            ),
            region=FORGE,
            station=SOURCE.station_id,
            system=SOURCE.system_id,
        ),
        DOMAIN: _snapshot(
            _orders(
                worked["destination"]["bids"],
                buy=True,
                station=DEST.station_id,
                system=DEST.system_id,
            ),
            region=DOMAIN,
            station=DEST.station_id,
            system=DEST.system_id,
        ),
    }


@pytest.fixture
def worked():
    import json
    from pathlib import Path

    return json.loads(
        (Path(__file__).parent / "fixtures" / "haul_worked_example.json").read_text(
            encoding="utf-8"
        )
    )


def _scan(config, worked, profile):
    return scan_hauls(
        config,
        profile,
        stations=[SOURCE, DEST],
        depths=_depths(worked),
        graph=_corridor(),
        names={34: "Tritanium"},
        packaged_volume={34: 0.01},
        now=NOW,
    )


# -- 1. the detour ---------------------------------------------------------


def test_a_dedicated_trip_charges_the_whole_route(config, worked):
    profile = _profile(security_profile="shortest")
    plan = _scan(config, worked, profile).plans[0]
    # Jita -> Amarr is 2 jumps, and the operator is already at Jita.
    assert plan.total_jumps == 2
    assert plan.detour_jumps is None


def test_along_route_charges_only_the_incremental_jumps(config, worked):
    """Jita → Rens is 4 jumps and Amarr is on the way: the detour is zero."""
    profile = replace(
        _profile(security_profile="shortest"),
        mode=ALONG_ROUTE,
        intended_destination=RENS_SYS,
    )
    plan = _scan(config, worked, profile).plans[0]
    assert plan.detour_jumps == 0
    assert plan.total_jumps == 0, "the flying was happening anyway"


def test_a_zero_detour_still_pays_the_handling_minutes(config, worked):
    """Loading and unloading are real minutes even when the flying is free."""
    profile = replace(
        _profile(security_profile="shortest"),
        mode=ALONG_ROUTE,
        intended_destination=RENS_SYS,
    )
    plan = _scan(config, worked, profile).plans[0]
    assert plan.active_minutes == pytest.approx(2 * profile.ship.handling_minutes)
    assert plan.isk_per_active_minute == pytest.approx(
        plan.net_profit / (2 * profile.ship.handling_minutes)
    )


def test_a_detour_off_the_route_is_charged_in_full(config, worked):
    """Going somewhere that is NOT on the way costs what it costs."""
    on_route = replace(
        _profile(security_profile="shortest"),
        mode=ALONG_ROUTE,
        intended_destination=RENS_SYS,
    )
    back_home = replace(on_route, intended_destination=30000142)
    detoured = _scan(config, worked, back_home).plans[0]
    assert detoured.detour_jumps == 4, "out and back is the whole round trip"
    assert detoured.detour_jumps > _scan(config, worked, on_route).plans[0].detour_jumps


def test_an_unreachable_intended_destination_rejects_rather_than_guessing(config, worked):
    profile = replace(
        _profile(security_profile="shortest"),
        mode=ALONG_ROUTE,
        intended_destination=30009999,
    )
    scan = _scan(config, worked, profile)
    assert scan.plans == []
    assert scan.rejected_for(NO_ROUTE)


# -- 2. extra destinations -------------------------------------------------


def test_extra_stations_are_destinations_and_never_sources(config, db):
    """An extra station is somewhere to DELIVER to. Ranking plans that buy
    from it would rank a book the operator chose that station despite."""
    from dataclasses import replace as _replace

    from evescreener.hauling import scan_inputs

    config = _replace(
        config,
        hauling=_replace(
            config.hauling,
            hub_station_ids=(SOURCE.station_id,),
            extra_destination_station_ids=(STAGING.station_id,),
        ),
    )
    config.paths.ensure()
    db.replace_solar_systems(
        [(SOURCE.system_id, FORGE, "Jita", 0.9), (RENS_SYS, HEIMATAR, "Rens", 0.9)]
    )
    db.replace_npc_stations(
        [
            (SOURCE.station_id, SOURCE.system_id, 1, 1, None),
            (STAGING.station_id, RENS_SYS, 1, 1, None),
        ]
    )
    sources, destinations, _depth, _graph, _names, _badges, _packaged = scan_inputs(config, db)
    assert [station.station_id for station in sources] == [SOURCE.station_id]
    assert STAGING.station_id in [station.station_id for station in destinations]


# -- 3. the freight column -------------------------------------------------


def _plan(config, worked):
    return _scan(config, worked, _profile(security_profile="shortest")).plans[0]


def test_the_freight_column_reuses_quote_freight_verbatim(config, worked, db):
    seen = {}

    def fake_quote(cfg, database, **kwargs):
        seen.update(kwargs)
        return FreightQuote(
            route=f"{kwargs['start_system']}->{kwargs['end_system']}",
            volume_m3=kwargs["volume_m3"],
            collateral=kwargs["collateral"],
            price=3_000_000.0,
            quoted_at="2026-08-25T12:00:00+00:00",
            cached=False,
            haircut_pct=0.0,
        )

    plan = _plan(config, worked)
    payload = freight_comparison(config, db, plan, quote_fn=fake_quote)
    assert payload["state"] == "OK"
    assert seen["start_system"] == "Jita" and seen["end_system"] == "Amarr"
    assert seen["volume_m3"] == pytest.approx(plan.cargo_m3)
    assert payload["freight_isk"] == 3_000_000.0
    assert payload["net_if_shipped"] == pytest.approx(plan.net_profit - 3_000_000.0)
    assert payload["your_time_isk_per_minute"] == pytest.approx(3_000_000.0 / plan.active_minutes)


def test_no_quote_means_the_column_is_unknown_and_the_row_is_untouched(config, worked, db):
    def refuse(cfg, database, **kwargs):
        return FreightQuote(
            route="x",
            volume_m3=1.0,
            collateral=1.0,
            price=None,
            quoted_at="2026-08-25T12:00:00+00:00",
            cached=False,
            haircut_pct=0.0,
            unknown_reason="ConnectError: PushX is down",
        )

    plan = _plan(config, worked)
    payload = freight_comparison(config, db, plan, quote_fn=refuse)
    assert payload["state"] == "UNKNOWN"
    assert "PushX is down" in payload["reason"]
    # The self-haul row is priced from swept depth and does not move.
    assert plan.net_profit > 0 and plan.source_wap > 0


def test_only_the_top_plans_are_quoted_and_the_rest_say_so(config, worked, db):
    calls = {"n": 0}

    def counting(cfg, database, **kwargs):
        calls["n"] += 1
        return FreightQuote(
            route="r",
            volume_m3=1.0,
            collateral=1.0,
            price=1.0,
            quoted_at="t",
            cached=False,
            haircut_pct=0.0,
        )

    scan = _scan(config, worked, _profile(security_profile="shortest"))
    scan.plans = scan.plans * 4
    attach_freight(config, db, scan, limit=1, quote_fn=counting)
    assert calls["n"] == 1, "quoting a hundred losers is rude and pointless"
    assert scan.plans[0].freight["state"] == "OK"
    assert scan.plans[-1].freight["state"] == "UNKNOWN"
    assert "not quoted" in scan.plans[-1].freight["reason"]


def test_the_docking_rights_flag_is_not_inherited_from_the_cross_region_scan(config, worked, db):
    """Range decides reachability, not station ownership (§22 S2a). The depth
    reduction already applied that rule; repeating it as a warning here would
    contradict it."""
    payload = freight_comparison(
        config,
        db,
        _plan(config, worked),
        quote_fn=lambda cfg, database, **kwargs: FreightQuote(
            route="r",
            volume_m3=1.0,
            collateral=1.0,
            price=1.0,
            quoted_at="t",
            cached=False,
            haircut_pct=0.0,
        ),
    )
    assert "docking" not in json_dumps(payload).lower()


def json_dumps(payload) -> str:
    import json

    return json.dumps(payload)


def test_freight_disabled_in_config_is_an_unknown_column_not_a_crash(config, worked, db):
    from dataclasses import replace as _replace

    disabled = _replace(config, freight=_replace(config.freight, enabled=False))
    payload = freight_comparison(disabled, db, _plan(config, worked))
    assert payload["state"] == "UNKNOWN" and "disabled" in payload["reason"]
