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
from pathlib import Path

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
REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_the_liquidity_window_is_config_not_a_literal(config):
    """`window_days` was the last analytic parameter still hard-coded.

    Every sibling — the quantiles, the minimum bar count, the priors — is a
    config field the operator can argue with. This one was a default argument
    nothing reached, so 30 days was the only window the system could measure.
    """
    from dataclasses import replace

    from evescreener.config import config_from_mapping, load_example

    assert config.hauling.liquidity_window_days == 30, "absent key keeps the default"

    bars = _bars([500] * 45, end="2026-08-24")
    thirty = measure_liquidity(bars, type_id=34, region_id=DOMAIN, now=NOW)
    sixty = measure_liquidity(
        bars,
        type_id=34,
        region_id=DOMAIN,
        window_days=config.hauling.liquidity_window_days,
        now=NOW,
    )
    assert thirty.bars_used == sixty.bars_used

    raw = load_example(REPO_ROOT)
    raw["app"]["data_dir"] = str(config.paths.root)
    raw.setdefault("hauling", {})["liquidity_window_days"] = 60
    wider = config_from_mapping(raw)
    assert wider.hauling.liquidity_window_days == 60
    measured = measure_liquidity(
        bars,
        type_id=34,
        region_id=DOMAIN,
        window_days=wider.hauling.liquidity_window_days,
        now=NOW,
    )
    assert measured.bars_used > thirty.bars_used, "a wider window must see more bars"
    assert replace(config.hauling, liquidity_window_days=60).liquidity_window_days == 60


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


def test_bars_outside_the_window_do_not_become_a_measurement(config):
    """A fallback to `frame.tail(window_days)` measured a market that has not
    traded for a year and called it 500 units a day.

    The reason field came back empty, `known` came back True, and that fed the
    maker caps, the scenarios drawer and the reliability grade's
    `destination_bars: ok`. Missing data is uncertainty, never confirmation.
    """
    stale = _bars([500] * 15, end="2025-08-20")
    profile = measure_liquidity(
        stale,
        type_id=34,
        region_id=DOMAIN,
        min_bars=config.hauling.min_liquidity_bars,
        now=NOW,
    )
    assert profile.known is False
    assert profile.median_units is None
    assert profile.quantile_units == {}
    assert profile.bars_used == 0
    assert "window" in profile.reason


def test_a_dead_destination_cannot_grade_its_bars_as_measured(config, db):
    """The grade's `destination_bars` component must follow the same answer."""
    from evescreener.liquidity import RELIABILITY_WEIGHTS, reliability_grade

    stale = measure_liquidity(_bars([500] * 15, end="2025-08-20"), type_id=34, region_id=DOMAIN)
    components = dict.fromkeys(RELIABILITY_WEIGHTS, "ok")
    components["destination_bars"] = "ok" if stale.known else "unknown"
    assert reliability_grade(components, RELIABILITY_WEIGHTS)["grade"] == "D"


# -- 6. the grade is quarantined, and provably so --------------------------


GRADED = {"reliability", "grade", "reliability_grade"}
GRADE_LETTERS = {"A", "B", "C", "D", "E", "F"}
RANKERS = {"sorted", "sort", "filter", "max", "min"}


def _named_grade(child) -> bool:
    """One AST node that reaches a reliability grade by a name we can see.

    Three spellings: the attribute (`plan.reliability`), the dict subscript
    (`row["reliability"]["grade"]` — which is how the report renderer and the
    drawer read row payloads, and what this was blind to), and an explicit
    `getattr(plan, "reliability")`.
    """
    import ast

    if isinstance(child, ast.Attribute):
        return child.attr in GRADED
    if isinstance(child, ast.Subscript):
        return isinstance(child.slice, ast.Constant) and child.slice.value in GRADED
    if isinstance(child, ast.Call) and getattr(child.func, "id", None) == "getattr":
        return (
            len(child.args) >= 2
            and isinstance(child.args[1], ast.Constant)
            and child.args[1].value in GRADED
        )
    return False


def _mentions_grade(node) -> bool:
    import ast

    return any(_named_grade(child) for child in ast.walk(node))


def _gates_on_grade(source: str) -> list[str]:
    """Places where a reliability grade decides BEHAVIOUR rather than a cell.

    Rendering a grade — including guarding it against None — is what it is for.
    What it must never do is filter, cap, branch or rank, because its weights,
    its half-credit and its cut-points are all invented.
    """
    import ast

    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        # 1. a grade compared against a letter or a number
        if isinstance(node, ast.Compare) and _mentions_grade(node):
            constants = [
                child
                for child in ast.walk(node)
                if isinstance(child, ast.Constant)
                and (child.value in GRADE_LETTERS or isinstance(child.value, (int, float)))
            ]
            if constants:
                offenders.append(f"{node.lineno}: grade compared against a threshold")
        # 2. a statement-level branch on a grade (an IfExp that renders is fine)
        if isinstance(node, ast.If) and _mentions_grade(node.test):
            offenders.append(f"{node.lineno}: control flow on a grade")
        # 3. a grade inside a sort key, a filter or an extremum
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in RANKERS and any(
                _mentions_grade(argument)
                for argument in [*node.args, *(kw.value for kw in node.keywords)]
            ):
                offenders.append(f"{node.lineno}: grade reached a {name}()")
        # 4. a comprehension that filters on a grade
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for generator in node.generators:
                if any(_mentions_grade(condition) for condition in generator.ifs):
                    offenders.append(f"{node.lineno}: comprehension filtered on a grade")
    return offenders


def test_the_gate_detector_can_actually_see_a_gate():
    """Guard against the check below passing because it detects nothing.

    This is a **tripwire, not a proof**. It reads names it can see in the
    source; access built out of a computed string — `getattr(plan, field)`,
    `row[key]` — is beyond any static check of this kind, and the quarantine
    for that case rests on review.
    """
    assert _gates_on_grade("rows = [r for r in rows if r.reliability['grade'] < 'C']")
    assert _gates_on_grade("if plan.reliability['grade'] == 'F':\n    plan = None")
    assert _gates_on_grade("rows.sort(key=lambda r: r.reliability['score'])")
    # …by dict subscript, which is how every row payload on the page is read…
    assert _gates_on_grade("rows = [r for r in rows if r['reliability']['grade'] < 'C']")
    assert _gates_on_grade("if row['reliability_grade'] == 'F':\n    row = None")
    # …and behind an explicit getattr.
    assert _gates_on_grade("if getattr(plan, 'reliability_grade') == 'F':\n    plan = None")
    # …and that none of it fires on rendering one.
    assert not _gates_on_grade("cell = plan.reliability.get('grade') if plan.reliability else '—'")
    assert not _gates_on_grade("cell = row['reliability']['grade']")


def test_no_module_lets_the_reliability_grade_gate_anything():
    """The grade's weights, half-credit and cut-points are **invented**.

    That is acceptable while it is a label the operator reads and nothing else.
    It stops being acceptable the moment a letter decides what he is shown: an
    unmeasured threshold that filters is exactly what §22 S4 removed elsewhere.
    So this is the same quarantine `relist_cost_unverified` lives under —
    consumption is a test failure, not a review comment.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "evescreener"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        # No exemption, `liquidity.py` included. It trips nothing today, and
        # exempting the module that computes the grade blinded the tripwire in
        # the one place the likeliest future consumer already lives:
        # `liquidity_attachment`, which builds the payload every surface reads.
        for offence in _gates_on_grade(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(root)}:{offence}")
    assert not offenders, (
        "the reliability grade must never gate, cap, filter or rank — its weights "
        f"are invented and it is a label only: {offenders}"
    )


def test_the_grade_says_in_its_own_payload_that_it_is_not_a_forecast():
    grade = reliability_grade(dict.fromkeys(RELIABILITY_WEIGHTS, "ok"), RELIABILITY_WEIGHTS)
    assert "not a probability of profit" in grade["note"].lower()
    assert "data quality" in grade["note"].lower()
