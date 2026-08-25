"""Getting out: what is measured, what is assumed, and what stays UNKNOWN.

The line this module draws is the important one. Daily units at the destination
are **measured** from completed bars. The share of that flow which happens at
the destination hub, and the share of it one order wins, are **assumptions** —
ESI's regional history carries no station split, so no amount of computation
turns them into measurements. Every scenario therefore carries its assumptions
on its face, and a zero or unmeasurable quantile is UNKNOWN rather than fast.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from evescreener.books import DepthCurve, DepthLevel
from evescreener.costs import CostModel
from evescreener.liquidity import (
    RELIABILITY_WEIGHTS,
    liquidation_days,
    liquidity_attachment,
    maker_scenario,
    measure_liquidity,
    reliability_grade,
    scenarios,
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
DOMAIN = 10000043


def _bars(volumes, *, type_id=34, region=DOMAIN, end="2026-08-24", close=100.0):
    stamps = pd.date_range(end=f"{end} 11:00", periods=len(volumes), freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "type_id": type_id,
            "region_id": region,
            "datetime": stamps,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": [float(value) for value in volumes],
            "order_count": 10,
            "isk_value": [float(value) * close for value in volumes],
            "fetched_at": "2026-08-25T00:00:00+00:00",
        }
    )


# -- 1. measurement --------------------------------------------------------


def test_the_quantiles_come_off_the_destination_regions_own_bars(config):
    profile = measure_liquidity(
        _bars([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200]),
        type_id=34,
        region_id=DOMAIN,
        quantiles=config.hauling.liquidity_quantiles,
        min_bars=10,
        now=NOW,
    )
    assert profile.known
    assert profile.median_units == pytest.approx(650.0)
    assert profile.quantile_units["low"] < profile.quantile_units["base"]
    assert profile.quantile_units["base"] < profile.quantile_units["high"]
    assert profile.bars_used == 12


def test_too_few_bars_is_unknown_and_says_what_it_needed(config):
    profile = measure_liquidity(
        _bars([100, 200, 300]),
        type_id=34,
        region_id=DOMAIN,
        min_bars=config.hauling.min_liquidity_bars,
        now=NOW,
    )
    assert profile.known is False
    assert "10-bar minimum" in profile.reason
    assert profile.quantile_units == {}


def test_a_type_with_no_bars_in_the_destination_region_is_unknown():
    profile = measure_liquidity(_bars([100] * 30, region=10000002), type_id=34, region_id=DOMAIN)
    assert profile.known is False


def test_only_completed_bars_are_counted():
    """A current-day bar is still moving in every field (§21 R2)."""
    frame = _bars([100] * 12, end="2026-08-25")
    profile = measure_liquidity(frame, type_id=34, region_id=DOMAIN, min_bars=1, now=NOW)
    # 2026-08-25 12:00 UTC is past the 11:05 roll, so the 25th is complete and
    # the whole window counts; the guard is that the cutoff is applied at all.
    assert profile.bars_used <= len(frame)
    early = measure_liquidity(
        frame, type_id=34, region_id=DOMAIN, min_bars=1, now=datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    )
    assert early.bars_used == profile.bars_used - 1, "before the roll, today is not a bar"


def test_zero_days_are_counted_rather_than_averaged_away():
    profile = measure_liquidity(
        _bars([0, 0, 0, 100, 200, 300, 400, 500, 600, 700]),
        type_id=34,
        region_id=DOMAIN,
        min_bars=5,
        now=NOW,
    )
    assert profile.zero_days == 3


# -- 2. the scenarios, and their assumptions -------------------------------


def test_liquidation_days_divides_by_the_two_assumed_factors():
    days = liquidation_days(1000.0, 500.0, destination_share=0.25, capture_share=0.20)
    assert days == pytest.approx(1000.0 / (500.0 * 0.25 * 0.20))


def test_a_zero_quantile_is_unknown_not_a_very_long_wait():
    """A dead market does not become tradeable by dividing by something small."""
    assert liquidation_days(1000.0, 0.0, destination_share=0.25, capture_share=0.2) is None
    assert liquidation_days(1000.0, None, destination_share=0.25, capture_share=0.2) is None
    assert liquidation_days(1000.0, 500.0, destination_share=0.0, capture_share=0.2) is None


def test_the_scenarios_carry_their_assumptions_labelled_as_such(config):
    profile = measure_liquidity(
        _bars(list(range(100, 1300, 100))), type_id=34, region_id=DOMAIN, min_bars=5, now=NOW
    )
    payload = scenarios(
        profile,
        1200.0,
        destination_share=config.hauling.destination_share_prior,
        capture_shares=config.hauling.capture_share,
    )
    assert payload["known"]
    assert set(payload["scenarios"]) == {"low", "base", "high"}
    assert payload["scenarios"]["low"] > payload["scenarios"]["high"], "less capture, more days"
    assert payload["assumptions"]["destination_share_prior"] == 0.25
    assert "ASSUMED, not measured" in payload["assumptions"]["note"]
    assert payload["measured"]["bars_used"] == profile.bars_used


def test_an_unmeasurable_profile_makes_every_scenario_unknown(config):
    profile = measure_liquidity(_bars([1, 2]), type_id=34, region_id=DOMAIN, min_bars=10, now=NOW)
    payload = scenarios(
        profile, 1000.0, destination_share=0.25, capture_shares=config.hauling.capture_share
    )
    assert payload["known"] is False
    assert set(payload["scenarios"].values()) == {None}
    assert "minimum" in payload["reason"]


# -- 3. the grade is about the data ----------------------------------------


def test_everything_measured_grades_a():
    grade = reliability_grade(dict.fromkeys(RELIABILITY_WEIGHTS, "ok"), RELIABILITY_WEIGHTS)
    assert grade["grade"] == "A"
    assert grade["capped_by_unknown"] is False
    assert "NOT a probability of profit" in grade["note"]


def test_one_unknown_component_caps_the_grade_at_d():
    components = dict.fromkeys(RELIABILITY_WEIGHTS, "ok")
    components["destination_bars"] = "unknown"
    grade = reliability_grade(components, RELIABILITY_WEIGHTS)
    assert grade["grade"] == "D"
    assert grade["capped_by_unknown"] is True


def test_nothing_measured_grades_f():
    grade = reliability_grade(dict.fromkeys(RELIABILITY_WEIGHTS, "unknown"), RELIABILITY_WEIGHTS)
    assert grade["grade"] == "F"


# -- 4. the maker scenario, display only -----------------------------------


def _sell_curve() -> DepthCurve:
    return DepthCurve(
        levels=(
            DepthLevel(
                price=120.0,
                qty=50.0,
                cumulative_qty=50.0,
                cumulative_notional=6000.0,
                order_count=2,
            ),
            DepthLevel(
                price=130.0,
                qty=80.0,
                cumulative_qty=130.0,
                cumulative_notional=16400.0,
                order_count=3,
            ),
        ),
        complete=True,
        side="sell",
    )


def test_the_maker_scenario_posts_one_tick_inside_and_counts_the_queue(config):
    costs = CostModel.from_config(config)
    payload = maker_scenario(
        dest_sell_curve=_sell_curve(),
        quantity=100.0,
        immediate_bid_value=9_000.0,
        costs=costs,
        dest_station=60008494,
        tick_isk=0.01,
        liquidation={"scenarios": {"base": 2.5}},
    )
    assert payload["proposed_list_price"] == pytest.approx(119.99)
    # Undercutting the whole book puts nothing in front of you — and that is
    # precisely the position that invites being undercut back, so the competing
    # depth is reported beside the zero rather than instead of it.
    assert payload["queue_ahead_units"] == pytest.approx(0.0)
    assert payload["queue_ahead_orders"] == 0
    assert payload["competing_units"] == pytest.approx(130.0)
    assert payload["competing_orders"] == 5
    assert payload["broker_fee_pct"] == pytest.approx(costs.broker_fee_at(60008494))
    assert payload["downside_immediate_bid_value"] == 9_000.0
    assert payload["liquidation_days"] == 2.5
    assert "DISPLAY ONLY" in payload["assumption"]


def test_a_maker_scenario_needs_a_sell_side_to_post_into(config):
    assert (
        maker_scenario(
            dest_sell_curve=None,
            quantity=1.0,
            immediate_bid_value=None,
            costs=CostModel.from_config(config),
            dest_station=None,
            tick_isk=0.01,
            liquidation=None,
        )
        is None
    )


# -- 5. the attachment, on a real plan -------------------------------------


def test_the_attachment_puts_scenarios_and_a_grade_on_a_plan(config, db, monkeypatch):
    from evescreener.hauling import scan_hauls
    from evescreener.store.lake import BarLake
    from test_hauling import DEST, FORGE, SOURCE, _graph, _orders, _profile, _snapshot

    config.paths.ensure()
    BarLake(config.paths).write(_bars(list(range(100, 1300, 100))))
    depths = {
        FORGE: _snapshot(
            _orders(
                [(100.0, 2000.0)], buy=False, station=SOURCE.station_id, system=SOURCE.system_id
            ),
            region=FORGE,
            station=SOURCE.station_id,
            system=SOURCE.system_id,
        ),
        DOMAIN: _snapshot(
            _orders([(200.0, 2000.0)], buy=True, station=DEST.station_id, system=DEST.system_id),
            region=DOMAIN,
            station=DEST.station_id,
            system=DEST.system_id,
        ),
    }
    profile = _profile()
    scan = scan_hauls(
        config,
        profile,
        stations=[SOURCE, DEST],
        depths=depths,
        graph=_graph(),
        names={34: "Tritanium"},
        packaged_volume={34: 0.01},
        liquidity=liquidity_attachment(config, db, depths, profile, now=NOW),
        now=NOW,
    )
    plan = scan.plans[0]
    assert plan.liquidity is not None and plan.liquidity["known"]
    assert plan.maker is None or "DISPLAY ONLY" in plan.maker["assumption"]
    assert plan.reliability["grade"] in {"A", "B", "C", "D", "E", "F"}
    assert plan.liquidation_days is not None
    assert plan.isk_per_capital_day == pytest.approx(
        plan.net_profit / (plan.source_cost * plan.liquidation_days)
    )
    # Which clock that is, is the exit model's business — see the immediate /
    # maker pair of tests below.


def test_a_maker_exit_refuses_a_plan_whose_liquidation_is_unknown(config, db):
    """The maker exit is the one that depends on the assumption, so it is the
    one the assumption is allowed to refuse."""
    from dataclasses import replace

    from evescreener.hauling import LIQUIDATION_UNKNOWN, scan_hauls
    from test_hauling import DEST, FORGE, SOURCE, _graph, _orders, _profile, _snapshot

    config.paths.ensure()
    depths = {
        FORGE: _snapshot(
            _orders(
                [(100.0, 2000.0)], buy=False, station=SOURCE.station_id, system=SOURCE.system_id
            ),
            region=FORGE,
            station=SOURCE.station_id,
            system=SOURCE.system_id,
        ),
        DOMAIN: _snapshot(
            _orders([(200.0, 2000.0)], buy=True, station=DEST.station_id, system=DEST.system_id),
            region=DOMAIN,
            station=DEST.station_id,
            system=DEST.system_id,
        ),
    }
    profile = replace(_profile(), exit_model="maker")
    scan = scan_hauls(
        config,
        profile,
        stations=[SOURCE, DEST],
        depths=depths,
        graph=_graph(),
        names={34: "Tritanium"},
        packaged_volume={34: 0.01},
        liquidity=liquidity_attachment(config, db, depths, profile, now=NOW),
        now=NOW,
    )
    assert scan.plans == []
    assert scan.rejected_for(LIQUIDATION_UNKNOWN)


def test_an_immediate_exit_charges_isk_days_over_travel_time_not_sell_out_time(config, db):
    """§23.5: an immediate exit dumps into the bid on arrival, so the capital is
    committed for the trip and nothing longer.

    The scenario is a different question — how long the destination would take
    to absorb the goods at a price — and it belongs in the drawer, not in this
    row's denominator. Attaching it there also left `liquidation_reason` still
    saying "charged over travel time" beside a number that was not.
    """
    from evescreener.hauling import scan_hauls
    from evescreener.store.lake import BarLake
    from test_hauling import DEST, FORGE, SOURCE, _graph, _orders, _profile, _snapshot

    config.paths.ensure()
    BarLake(config.paths).write(_bars(list(range(100, 1300, 100))))
    depths = {
        FORGE: _snapshot(
            _orders(
                [(100.0, 2000.0)], buy=False, station=SOURCE.station_id, system=SOURCE.system_id
            ),
            region=FORGE,
            station=SOURCE.station_id,
            system=SOURCE.system_id,
        ),
        DOMAIN: _snapshot(
            _orders([(200.0, 2000.0)], buy=True, station=DEST.station_id, system=DEST.system_id),
            region=DOMAIN,
            station=DEST.station_id,
            system=DEST.system_id,
        ),
    }
    profile = _profile()
    assert profile.exit_model == "immediate"
    scan = scan_hauls(
        config,
        profile,
        stations=[SOURCE, DEST],
        depths=depths,
        graph=_graph(),
        names={34: "Tritanium"},
        packaged_volume={34: 0.01},
        liquidity=liquidity_attachment(config, db, depths, profile, now=NOW),
        now=NOW,
    )
    plan = scan.plans[0]
    travel_days = plan.active_minutes / (60.0 * 24.0)
    assert plan.liquidation_days == pytest.approx(travel_days)
    assert plan.isk_per_capital_day == pytest.approx(
        plan.net_profit / (plan.source_cost * travel_days)
    )
    assert "travel time" in plan.liquidation_reason
    # The sell-out scenario is still measured and still shown — beside it.
    assert plan.liquidity["known"] and plan.liquidity["scenarios"]["base"] > travel_days
