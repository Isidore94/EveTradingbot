"""The backtest's statistics and its frozen verdict rule (plan.md §13.6)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evescreener.backtest import (
    LIMITATIONS,
    HorizonStats,
    breakeven_win_rate,
    measure_haircuts,
    price_instances,
    render_backtest,
    run_backtest,
    verdict,
    wilson_lower_bound,
)
from evescreener.signals.setup import SetupParams, anchor_grid, evaluate_setups


def stats(**overrides) -> HorizonStats:
    base = dict(
        horizon_days=10,
        notional_isk=250_000_000.0,
        haircut_multiple=2.0,
        samples=500,
        wins=300,
        win_rate=0.6,
        wilson_lb=0.56,
        breakeven_win_rate=0.45,
        expectancy_pct=1.2,
        median_pct=0.8,
        first_half_wilson_lb=0.55,
        first_half_breakeven=0.45,
        second_half_wilson_lb=0.54,
        second_half_breakeven=0.46,
    )
    base.update(overrides)
    return HorizonStats(**base)


# -- statistics -------------------------------------------------------------


def test_wilson_lower_bound_punishes_small_samples():
    assert wilson_lower_bound(2, 2) < 0.6
    assert wilson_lower_bound(200, 200) > 0.97
    assert wilson_lower_bound(0, 0) is None


def test_breakeven_win_rate_is_the_payoff_ratio():
    returns = np.array([2.0, 2.0, -1.0, -1.0])
    assert breakeven_win_rate(returns) == pytest.approx(1 / 3)


def test_breakeven_takes_its_limits_when_one_side_is_empty():
    """No losses -> 0 is required; no wins -> 1 is required. Not None.

    Small-sample skepticism lives in the Wilson bound on the win rate, not in
    refusing to state the payoff ratio.
    """
    assert breakeven_win_rate(np.array([1.0, 2.0])) == 0.0
    assert breakeven_win_rate(np.array([-1.0, -2.0])) == 1.0
    assert breakeven_win_rate(np.array([])) is None


def test_max_drawdown_is_withdrawn_not_merely_unused():
    """§21 R3: compounding overlapping trades in date order is not an equity
    curve, and there is no portfolio model behind it. The metric is gone from
    the code; its last measured values are preserved in the golden fixture
    under `backtest_withdrawn_pre_r3` so no historical number is erased."""
    import json
    from pathlib import Path

    from evescreener import backtest

    assert not hasattr(backtest, "max_drawdown")
    golden = json.loads(
        (Path(__file__).parent / "fixtures" / "golden_signals.json").read_text(encoding="utf-8")
    )
    withdrawn = golden["backtest_withdrawn_pre_r3"]
    assert "_reason" in withdrawn
    assert withdrawn["2x"]["max_drawdown_pct"] == -100.0


# -- the frozen verdict rule ------------------------------------------------


def test_small_sample_is_unknown_and_never_rounds_up_to_a_pass():
    result = verdict(stats(samples=99))
    assert result["verdict"] == "UNKNOWN"
    assert "not a pass" in result["reason"]


def test_no_instances_is_unknown():
    assert verdict(None)["verdict"] == "UNKNOWN"
    assert verdict(stats(samples=0))["verdict"] == "UNKNOWN"


def test_plausible_requires_all_four_conditions():
    assert verdict(stats())["verdict"] == "PLAUSIBLE"


def test_negative_expectancy_at_double_haircut_fails():
    result = verdict(stats(expectancy_pct=-0.1))
    assert result["verdict"] == "NOT PLAUSIBLE"
    assert "expectancy" in result["reason"]


def test_wilson_lb_below_breakeven_fails():
    result = verdict(stats(wilson_lb=0.40))
    assert result["verdict"] == "NOT PLAUSIBLE"
    assert "breakeven" in result["reason"]


def test_inconsistency_across_halves_fails():
    result = verdict(stats(second_half_wilson_lb=0.30))
    assert result["verdict"] == "NOT PLAUSIBLE"
    assert "second half" in result["reason"]


def test_verdict_always_states_the_rule_it_applied():
    for candidate in (verdict(stats()), verdict(stats(samples=10))):
        assert "plan.md §13.6" in candidate["rule"]
        assert "frozen" in candidate["rule"]


# -- haircuts ---------------------------------------------------------------


def book_row(type_id, side, best, fills):
    row = {
        "type_id": type_id,
        "region_id": 10000002,
        "side": side,
        "sweep_ts": "2026-08-20T12:00:00+00:00",
        "expires_ts": None,
        "best_price": best,
        "total_volume": 1e9,
        "order_count": 10,
        "p5_price": best,
        "top_order_volume_share": 0.1,
        "station_volume_share": 1.0,
        "partial_sweep": False,
        # R1: a haircut is measured against the pair one character could
        # actually have traded, so the fixture must name the venue.
        "best_location_id": 60003760,
        "best_range": "station" if side == "buy" else None,
        "exec_location_id": 60003760,
        "exec_price": best,
        "exec_volume": 1e9,
        "exec_order_count": 10,
        "exec_is_structure": False,
    }
    for index in range(3):
        row[f"depth_fill_price_{index}"] = fills[index]
        row[f"depth_fill_qty_{index}"] = 1000.0
    return row


def test_haircuts_are_measured_per_type_and_tier():
    frame = pd.DataFrame(
        [
            book_row(34, "sell", 105.0, [106.0, 110.0, None]),
            book_row(34, "buy", 95.0, [94.0, 90.0, None]),
        ]
    )
    haircuts = measure_haircuts(frame, (250e6, 1e9, 2.5e9))
    assert set(haircuts[34]) == {250e6, 1e9}
    assert haircuts[34][250e6]["entry"] == pytest.approx(106 / 100 - 1)
    assert haircuts[34][250e6]["exit"] == pytest.approx(1 - 94 / 100)
    assert haircuts[34][250e6]["round_trip"] == pytest.approx(0.12)


def test_a_tier_the_book_cannot_fill_is_absent_not_zero():
    frame = pd.DataFrame(
        [
            book_row(34, "sell", 105.0, [106.0, None, None]),
            book_row(34, "buy", 95.0, [94.0, None, None]),
        ]
    )
    haircuts = measure_haircuts(frame, (250e6, 1e9, 2.5e9))
    assert 1e9 not in haircuts[34]
    assert haircuts[34][250e6]["entry"] > 0


def test_an_empty_sweep_measures_nothing():
    assert measure_haircuts(pd.DataFrame(), (250e6,)) == {}


def test_instances_without_a_haircut_are_excluded_and_counted():
    instances = pd.DataFrame(
        {
            "type_id": [34, 35],
            "cohort": ["a", "a"],
            "datetime": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
            "horizon_days": [10, 10],
            "entry_close": [100.0, 100.0],
            "exit_close": [110.0, 110.0],
        }
    )
    haircuts = {34: {250e6: {"entry": 0.01, "exit": 0.01, "round_trip": 0.02}}}
    priced, excluded = price_instances(
        instances, haircuts, tier=250e6, multiple=1.0, sales_tax_pct=3.375
    )
    assert excluded == 1
    assert len(priced) == 1
    assert priced.iloc[0]["type_id"] == 34


def test_costs_are_inside_every_priced_instance():
    instances = pd.DataFrame(
        {
            "type_id": [34],
            "cohort": ["a"],
            "datetime": pd.to_datetime(["2026-01-01"], utc=True),
            "horizon_days": [10],
            "entry_close": [100.0],
            "exit_close": [110.0],
        }
    )
    haircuts = {34: {250e6: {"entry": 0.02, "exit": 0.02, "round_trip": 0.04}}}
    priced, _ = price_instances(instances, haircuts, tier=250e6, multiple=1.0, sales_tax_pct=3.375)
    gross = (110 / 100 - 1) * 100
    assert priced.iloc[0]["net_return_pct"] < gross
    expected = (110 * 0.98 * (1 - 0.03375)) / (100 * 1.02) - 1
    assert priced.iloc[0]["net_return_pct"] == pytest.approx(expected * 100)


def test_haircut_multiple_scales_the_friction():
    instances = pd.DataFrame(
        {
            "type_id": [34],
            "cohort": ["a"],
            "datetime": pd.to_datetime(["2026-01-01"], utc=True),
            "horizon_days": [10],
            "entry_close": [100.0],
            "exit_close": [110.0],
        }
    )
    haircuts = {34: {250e6: {"entry": 0.02, "exit": 0.02, "round_trip": 0.04}}}
    single, _ = price_instances(instances, haircuts, tier=250e6, multiple=1.0, sales_tax_pct=3.375)
    triple, _ = price_instances(instances, haircuts, tier=250e6, multiple=3.0, sales_tax_pct=3.375)
    assert triple.iloc[0]["net_return_pct"] < single.iloc[0]["net_return_pct"]


# -- the setup detector -----------------------------------------------------


def synthetic(bars=200, seed=3, dip_at=150) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0.0, 0.005, bars)))
    close[dip_at:] = close[dip_at:] * 0.80
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2025-06-01 11:00", periods=bars, freq="D", tz="UTC"),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(bars, 1_000_000.0),
            "order_count": np.full(bars, 500),
        }
    )


def test_setup_detects_a_dip_below_anchored_value():
    frame = synthetic()
    reference = synthetic(dip_at=10_000)
    result = evaluate_setups(frame, reference, SetupParams(min_bars=60))
    assert result["is_setup"].any()
    hits = result[result["is_setup"]]
    assert (hits["dip_sigma"] <= -1.0).all()


def test_setup_never_fires_on_strength():
    frame = synthetic(dip_at=10_000)
    rising = frame.copy()
    rising["close"] = rising["close"] * np.linspace(1.0, 1.6, len(rising))
    rising["high"] = rising["close"] * 1.01
    rising["low"] = rising["close"] * 0.99
    result = evaluate_setups(rising, frame, SetupParams(min_bars=60))
    hits = result[result["is_setup"]]
    assert hits.empty or (hits["dip_sigma"] < 0).all()


def test_unknown_gates_fail_rather_than_pass():
    frame = synthetic(bars=200)
    result = evaluate_setups(frame, None, SetupParams(min_bars=60))
    assert result["relative_strength_intact_unknown"].all()
    assert not result["is_setup"].any(), "an UNKNOWN gate must never let a setup through"


def test_collapsing_participation_blocks_the_setup():
    """The gate is relative to the type's OWN recent baseline (plan.md §13.2).

    It catches the collapse, not the level: after ~20 bars a permanently thin
    book normalizes to participation 1.0 by construction. Guarding the absolute
    level is the census liquidity floor's job (gate 5), not this gate's, and
    conflating the two would make the frozen definition mean two things.
    """
    frame = synthetic()
    frame.loc[150:, "order_count"] = 1
    reference = synthetic(dip_at=10_000)
    result = evaluate_setups(frame, reference, SetupParams(min_bars=60))
    collapsing = result.iloc[151:170]
    assert (collapsing["participation"] < 0.7).all()
    assert not collapsing["is_setup"].any(), "a dip on a collapsing book is not demand"


def test_anchor_grid_uses_events_not_a_sliding_window():
    frame = synthetic(bars=200)
    grid = anchor_grid(frame, step_days=90)
    assert grid[0] == 0
    assert len(grid) == 3  # bar 0, +90d, +180d
    with_calendar = anchor_grid(frame, step_days=90, anchor_dates=["2025-08-15"])
    assert len(with_calendar) == 4


def test_short_history_cannot_produce_a_setup():
    frame = synthetic(bars=60)
    result = evaluate_setups(frame, synthetic(bars=60, dip_at=10_000), SetupParams(min_bars=120))
    assert not result["is_setup"].any()


# -- the whole run ----------------------------------------------------------


def test_run_backtest_on_an_empty_lake_is_unknown_not_zero(config):
    result = run_backtest(config, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert "UNKNOWN" in str(result.verdicts)
    assert result.instances == 0


def test_report_always_states_its_own_limitations(config):
    result = run_backtest(config, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    report = render_backtest(result)
    assert "Limitations of this study" in report
    assert "No historical order books exist" in report
    assert len(LIMITATIONS) == 7


def test_report_states_the_frozen_hypothesis(config):
    report = render_backtest(run_backtest(config, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()))
    assert "frozen in plan.md §13.1 before this study ran" in report


def test_halves_split_by_date_not_by_instance_count():
    """§13.6 says "both halves of the sample PERIOD" — so the split is temporal.

    A burst of instances inside one month must not silently become a whole
    "half" of a year-long sample.
    """
    from evescreener.backtest import _stats

    # 100 instances in January, 4 in December. A count-split would put the
    # boundary inside January; a date-split puts it in June.
    dates = list(pd.date_range("2026-01-01", periods=100, freq="h", tz="UTC")) + list(
        pd.date_range("2026-12-01", periods=4, freq="D", tz="UTC")
    )
    frame = pd.DataFrame(
        {
            "datetime": dates,
            "net_return_pct": [5.0] * 100 + [-5.0] * 4,
            "entry_close": [100.0] * 104,
            "exit_close": [105.0] * 100 + [95.0] * 4,
        }
    )
    stats = _stats(frame, horizon=10, tier=250e6, multiple=1.0, wilson_z=1.96)
    # First half = the 100 January winners; second half = the 4 December losers.
    assert stats.first_half_wilson_lb > 0.9
    assert stats.second_half_wilson_lb == 0.0
    assert stats.second_half_breakeven == 1.0


def test_a_negative_verdict_reports_the_gross_edge_that_frictions_ate():
    """A negative verdict is unreadable without the pre-cost number.

    "The setup has no edge" and "the setup has an edge that EVE's frictions
    eat" are very different answers to the operator's question, and only the
    second one tells him where to look next.
    """
    result = verdict(
        stats(
            expectancy_pct=-21.8,
            gross_expectancy_pct=3.08,
            gross_win_rate=0.537,
            round_trip_haircut_pct=28.79,
        )
    )
    assert result["verdict"] == "NOT PLAUSIBLE"
    assert "not directionless" in result["gross_context"]
    assert "+3.08%" in result["gross_context"]
    assert "28.79%" in result["gross_context"]


def test_a_directionless_setup_says_so_plainly():
    result = verdict(
        stats(
            expectancy_pct=-5.0,
            gross_expectancy_pct=-1.2,
            gross_win_rate=0.41,
            round_trip_haircut_pct=4.0,
        )
    )
    assert "no pre-cost edge to lose" in result["gross_context"]


def test_a_plausible_verdict_needs_no_excuse():
    assert "gross_context" not in verdict(stats())


# -- the banner when nothing has been measured ------------------------------


def test_a_missing_study_renders_an_explicit_unknown_banner():
    """`data/` is gitignored, so a fresh clone has no stored verdict.

    This used to return an empty string, which renders as *no warning at all* —
    a desk that has never measured anything looking exactly like one that
    measured and passed. UNKNOWN never gets to look like a pass (plan.md §4).
    """
    from evescreener.backtest import verdict_banner

    for absent in (None, {}, {"10": "not a dict"}):
        banner = verdict_banner(absent)
        assert banner, f"{absent!r} produced no banner"
        assert "UNKNOWN" in banner
        assert "no study has run on this machine" in banner


def test_a_plausible_verdict_still_silences_the_banner():
    """Guards the guard: the banner is a warning, not decoration."""
    from evescreener.backtest import verdict_banner

    assert verdict_banner({"10": {"verdict": "PLAUSIBLE"}}) == ""


def test_not_plausible_keeps_its_exact_wording():
    from evescreener.backtest import verdict_banner

    banner = verdict_banner({"10": {"verdict": "NOT PLAUSIBLE"}})
    assert "NOT PLAUSIBLE at every horizon" in banner
