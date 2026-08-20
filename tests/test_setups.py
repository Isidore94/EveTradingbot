"""The operator setup engine — the DSL, its validation, and its tri-state."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from evescreener.setups import (
    CONDITION_SPECS,
    UNVALIDATED,
    VALIDATED,
    Condition,
    Setup,
    SetupContext,
    SetupError,
    describe_condition,
    evaluate_setup,
    load_setups,
    validation_state,
)
from evescreener.signals.avwap import anchored_vwap_bands

REPO_SETUPS = Path(__file__).resolve().parents[1] / "config" / "setups.jsonl"


def frame(closes, *, volume=10_000.0, order_count=50):
    stamps = pd.date_range("2026-01-01 11:00", periods=len(closes), freq="D", tz="UTC")
    values = np.asarray(closes, dtype="float64")
    return pd.DataFrame(
        {
            "type_id": 34,
            "region_id": 10000002,
            "datetime": stamps,
            "high": values * 1.01,
            "low": values * 0.99,
            "close": values,
            "volume": volume,
            "order_count": order_count,
            "isk_value": values * volume,
            "fetched_at": "2026-08-20T00:00:00+00:00",
        }
    )


def write(tmp_path, *lines) -> Path:
    path = tmp_path / "setups.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# -- the committed examples -------------------------------------------------


def test_the_committed_setups_parse_and_are_marked_as_examples():
    setups = load_setups(REPO_SETUPS)
    assert len(setups) >= 3
    assert all(setup.example for setup in setups), (
        "shipped setups must be labelled examples, not passed off as validated"
    )
    assert all(setup.notes for setup in setups)


def test_every_condition_kind_has_a_description():
    for kind, spec in CONDITION_SPECS.items():
        assert spec.summary, f"{kind} has no summary"
        assert spec.required, f"{kind} requires nothing, which cannot be right"


def test_a_missing_setups_file_is_an_empty_list(tmp_path):
    assert load_setups(tmp_path / "nope.jsonl") == []


# -- validation is loud -----------------------------------------------------


def test_an_unknown_condition_kind_is_refused_by_name(tmp_path):
    path = write(
        tmp_path,
        '{"name": "x", "conditions": [{"kind": "macd_histogram_wiggle", "value": 1}]}',
    )
    with pytest.raises(SetupError, match="unknown condition kind"):
        load_setups(path)


def test_an_unknown_condition_never_silently_passes(tmp_path):
    """The failure mode this guards against is the expensive one.

    A DSL that ignores what it does not understand produces a setup that
    looks tested and is not. Loading must stop before anything evaluates.
    """
    path = write(
        tmp_path,
        '{"name": "x", "conditions": ['
        '{"kind": "dip_sigma", "op": "at_most", "value": -1.0},'
        '{"kind": "moon_phase", "value": "waxing"}]}',
    )
    with pytest.raises(SetupError):
        load_setups(path)


def test_a_misspelled_parameter_is_refused(tmp_path):
    path = write(
        tmp_path,
        '{"name": "x", "conditions": [{"kind": "price_vs_ma", "ma": "ema",'
        ' "lenght": 21, "op": "above"}]}',
    )
    with pytest.raises(SetupError, match="missing length|unknown parameter"):
        load_setups(path)


def test_a_bad_enum_lists_the_choices(tmp_path):
    path = write(
        tmp_path,
        '{"name": "x", "conditions": [{"kind": "price_vs_ma", "ma": "wma",'
        ' "length": 21, "op": "above"}]}',
    )
    with pytest.raises(SetupError, match="must be one of ema, sma"):
        load_setups(path)


def test_a_cloud_with_fast_slower_than_slow_is_refused(tmp_path):
    path = write(
        tmp_path, '{"name": "x", "conditions": [{"kind": "cloud", "fast": 21, "slow": 9}]}'
    )
    with pytest.raises(SetupError, match="fast < slow"):
        load_setups(path)


def test_a_malformed_line_names_the_file_and_line(tmp_path):
    path = write(
        tmp_path,
        '{"name": "ok", "conditions": [{"kind": "dip_sigma", "op": "at_most", "value": -1}]}',
        "not json at all",
    )
    with pytest.raises(SetupError, match=r"setups\.jsonl:2"):
        load_setups(path)


def test_duplicate_names_are_refused(tmp_path):
    line = '{"name": "A", "conditions": [{"kind": "change", "bars": 5, "op": "above", "value": 0}]}'
    path = write(tmp_path, line, line)
    with pytest.raises(SetupError, match="duplicate setup name"):
        load_setups(path)


def test_an_empty_condition_list_is_refused(tmp_path):
    path = write(tmp_path, '{"name": "A", "conditions": []}')
    with pytest.raises(SetupError, match="non-empty list"):
        load_setups(path)


def test_an_unknown_top_level_field_is_refused(tmp_path):
    path = write(
        tmp_path,
        '{"name": "A", "weight": 3, "conditions": [{"kind": "change", "bars": 5,'
        ' "op": "above", "value": 0}]}',
    )
    with pytest.raises(SetupError, match="unknown field"):
        load_setups(path)


def test_an_unknown_band_zone_is_refused(tmp_path):
    path = write(tmp_path, '{"name": "A", "conditions": [{"kind": "band_zone", "zone": "MIDDLE"}]}')
    with pytest.raises(SetupError, match="unknown zone"):
        load_setups(path)


def test_the_below_value_alias_expands(tmp_path):
    path = write(
        tmp_path, '{"name": "A", "conditions": [{"kind": "band_zone", "zone": "below_value"}]}'
    )
    zones = load_setups(path)[0].conditions[0].params["zone"]
    assert "BELOW_LOWER_3" in zones and "UPPER_1_2" not in zones


# -- evaluation is tri-state ------------------------------------------------


def test_a_warming_up_average_is_unknown_not_false():
    setup = Setup(
        name="x",
        conditions=(Condition("price_vs_ma", {"ma": "sma", "length": 200, "op": "above"}),),
    )
    verdict = evaluate_setup(setup, SetupContext(frame=frame(np.linspace(100, 120, 50))))
    assert verdict.unknown
    assert not verdict.fired, "UNKNOWN never fires (§4)"
    assert "warming up" in verdict.results[0].detail


def test_one_unknown_condition_sinks_a_setup_whose_others_pass():
    setup = Setup(
        name="x",
        conditions=(
            Condition("price_vs_ma", {"ma": "sma", "length": 20, "op": "above"}),
            Condition("rrs", {"scope": "forge", "op": "at_least", "value": -0.5}),
        ),
    )
    context = SetupContext(frame=frame(np.linspace(100, 140, 60)), rrs_forge=None)
    verdict = evaluate_setup(setup, context)
    assert verdict.results[0].passed is True
    assert verdict.results[1].passed is None
    assert not verdict.fired
    assert verdict.unknown_on == ("RRS vs FORGE at least -0.50",)


def test_a_sector_scope_that_cannot_resolve_is_unknown_not_the_market():
    setup = Setup(
        name="x",
        conditions=(Condition("rrs", {"scope": "sector", "op": "at_least", "value": 0.0}),),
    )
    context = SetupContext(
        frame=frame(np.linspace(100, 140, 60)), rrs_forge=2.0, sector_ticker=None
    )
    verdict = evaluate_setup(setup, context)
    assert verdict.results[0].passed is None
    assert "no sector" in verdict.results[0].detail


def test_a_setup_fires_when_every_condition_is_true():
    closes = np.linspace(100, 160, 80)
    setup = Setup(
        name="x",
        conditions=(
            Condition("price_vs_ma", {"ma": "ema", "length": 21, "op": "above"}),
            Condition("change", {"bars": 10, "op": "at_least", "value": 1.0}),
            Condition("participation", {"op": "at_least", "value": 0.7}),
        ),
    )
    work = frame(closes)
    evaluated = pd.DataFrame({"participation": [1.2] * len(work)})
    verdict = evaluate_setup(setup, SetupContext(frame=work, evaluated=evaluated))
    assert verdict.fired, verdict.as_dict()
    assert not verdict.unknown


def test_a_failing_condition_says_which_one():
    setup = Setup(
        name="x",
        conditions=(
            Condition("change", {"bars": 5, "op": "at_least", "value": 50.0}),
            Condition("price_vs_ma", {"ma": "sma", "length": 20, "op": "above"}),
        ),
    )
    verdict = evaluate_setup(setup, SetupContext(frame=frame(np.linspace(100, 110, 60))))
    assert not verdict.fired
    assert not verdict.unknown
    assert verdict.failed_on == ("5-bar change at least +50.00%",)


def test_the_cloud_condition_reads_position_and_slope():
    rising = frame(np.linspace(100, 200, 80))
    setup = Setup(
        name="x",
        conditions=(
            Condition("cloud", {"fast": 9, "slow": 21, "position": "above", "slope": "rising"}),
        ),
    )
    assert evaluate_setup(setup, SetupContext(frame=rising)).fired

    falling = frame(np.linspace(200, 100, 80))
    assert not evaluate_setup(setup, SetupContext(frame=falling)).fired


def test_band_zone_without_bands_is_unknown():
    setup = Setup(name="x", conditions=(Condition("band_zone", {"zone": ["LOWER_1_2"]}),))
    verdict = evaluate_setup(setup, SetupContext(frame=frame(np.linspace(100, 110, 60))))
    assert verdict.results[0].passed is None
    assert "UNKNOWN" in verdict.results[0].detail


def test_band_zone_reads_the_frozen_bands():
    work = frame(np.concatenate([np.linspace(100, 140, 70), np.linspace(140, 96, 20)]))
    bands = anchored_vwap_bands(work, 0)
    setup = Setup(
        name="x",
        conditions=(
            Condition(
                "band_zone",
                {"zone": ["BELOW_LOWER_3", "LOWER_1_2", "LOWER_2_3", "VWAP_LOWER_1"]},
            ),
        ),
    )
    verdict = evaluate_setup(setup, SetupContext(frame=work, bands=bands))
    assert verdict.results[0].passed is not None, "bands were supplied; this is measurable"
    assert verdict.results[0].detail.startswith("zone ")


def test_near_level_without_atr_is_unknown():
    setup = Setup(
        name="x",
        conditions=(Condition("near_level", {"level": "hv", "within_atr": 1.0, "side": "any"}),),
    )
    verdict = evaluate_setup(setup, SetupContext(frame=frame(np.linspace(100, 110, 60))))
    assert verdict.results[0].passed is None


def test_change_needs_enough_bars():
    setup = Setup(
        name="x", conditions=(Condition("change", {"bars": 20, "op": "above", "value": 0}),)
    )
    verdict = evaluate_setup(setup, SetupContext(frame=frame(np.linspace(100, 110, 10))))
    assert verdict.results[0].passed is None
    assert "needs 21 bars" in verdict.results[0].detail


# -- the validation label ---------------------------------------------------


def test_a_setup_is_unvalidated_until_it_has_evidence():
    assert validation_state(backtested=False, closed_trades=0) == UNVALIDATED
    assert validation_state(backtested=False, closed_trades=19) == UNVALIDATED
    assert validation_state(backtested=False, closed_trades=20) == VALIDATED
    assert validation_state(backtested=True, closed_trades=0) == VALIDATED


def test_describe_covers_every_shipped_condition():
    for setup in load_setups(REPO_SETUPS):
        for condition in setup.conditions:
            assert describe_condition(condition)


# -- per-bar evaluation must agree with the last-bar path -------------------


def _context_with_gates(closes, **kwargs):
    work = frame(closes)
    from evescreener.signals.setup import SetupParams, evaluate_setups

    params = SetupParams(min_bars=20)
    evaluated = evaluate_setups(work, None, params, anchor_dates=())
    return SetupContext(frame=work, evaluated=evaluated, **kwargs)


@pytest.mark.parametrize(
    "condition",
    [
        Condition("price_vs_ma", {"ma": "sma", "length": 20, "op": "above"}),
        Condition("price_vs_ma", {"ma": "ema", "length": 21, "op": "below"}),
        Condition("cloud", {"fast": 9, "slow": 21, "position": "above", "slope": "any"}),
        Condition("cloud", {"fast": 9, "slow": 21, "position": "any", "slope": "rising"}),
        Condition("ma_cross", {"ma": "ema", "fast": 9, "slow": 21, "direction": "up", "within": 5}),
        Condition("dip_sigma", {"op": "at_most", "value": -0.5}),
        Condition("participation", {"op": "at_least", "value": 0.7}),
        Condition("change", {"bars": 10, "op": "at_least", "value": 1.0}),
        Condition("band_zone", {"zone": ["LOWER_1_2", "LOWER_2_3", "BELOW_LOWER_3"]}),
    ],
)
def test_the_per_bar_series_agrees_with_the_last_bar_read(condition):
    """The backtest must measure what the scanner shows, condition by condition.

    A drift between the two is the failure that makes a backtest worse than
    no backtest: the study would score a setup nobody is being shown.
    """
    from evescreener.setups import fire_series

    rng = np.random.default_rng(11)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, 200)))
    context = _context_with_gates(closes)
    # The last-bar path needs bands for band_zone; give it the same numbers
    # the per-bar path reads out of `dip_sigma`.
    setup = Setup(name="probe", conditions=(condition,))
    per_bar = fire_series(setup, context)
    if condition.kind == "band_zone":
        from evescreener.signals.avwap import zone_from_position

        zone = zone_from_position(float(context.evaluated["dip_sigma"].iloc[-1]))
        expected = zone in condition.params["zone"]
    else:
        expected = evaluate_setup(setup, context).fired
    assert bool(per_bar.iloc[-1]) == bool(expected), condition.as_dict()


def test_a_condition_that_cannot_be_measured_over_history_yields_no_instances():
    """A level condition cannot be backtested without lookahead, so it isn't.

    The setup is NOT quietly reduced to its other conditions — that would
    score a different setup than the one written down.
    """
    from evescreener.setups import HISTORY_UNMEASURABLE, fire_series, unmeasurable_conditions

    assert "near_level" in HISTORY_UNMEASURABLE
    setup = Setup(
        name="x",
        conditions=(
            Condition("change", {"bars": 5, "op": "at_least", "value": -100.0}),
            Condition("near_level", {"level": "hv", "within_atr": 1.0, "side": "any"}),
        ),
    )
    context = _context_with_gates(np.linspace(100, 200, 120))
    assert not fire_series(setup, context).any()
    assert unmeasurable_conditions(setup) == ("within 1.00 ATR of a hv level",)


def test_a_sector_rrs_series_is_used_when_supplied():
    from evescreener.setups import fire_series

    context = _context_with_gates(np.linspace(100, 200, 120))
    strong = pd.Series(2.0, index=context.frame.index)
    setup = Setup(
        name="x",
        conditions=(Condition("rrs", {"scope": "sector", "op": "at_least", "value": 1.0}),),
    )
    context.rrs_sector_series = strong
    assert fire_series(setup, context).all()
    context.rrs_sector_series = None
    assert not fire_series(setup, context).any(), "no sector series is UNKNOWN, not a pass"


# -- backtest --setup -------------------------------------------------------


def test_the_backtest_measures_an_operator_setup_on_the_same_terms(config):
    """`backtest --setup NAME` reuses the whole cost pipeline, unchanged."""
    from evescreener.backtest import find_instances
    from evescreener.signals.setup import SetupParams

    rng = np.random.default_rng(3)
    rows = []
    for type_id in (600, 601):
        closes = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.02, 240)))
        stamps = pd.date_range("2026-01-01 11:00", periods=240, freq="D", tz="UTC")
        for position, stamp in enumerate(stamps):
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": closes[position] * 1.02,
                    "low": closes[position] * 0.98,
                    "close": closes[position],
                    "volume": 50_000.0,
                    "order_count": 40,
                    "isk_value": closes[position] * 50_000.0,
                    "fetched_at": "x",
                }
            )
    bars = pd.DataFrame(rows)
    setup = Setup(
        name="Above the 20",
        conditions=(Condition("price_vs_ma", {"ma": "sma", "length": 20, "op": "above"}),),
    )
    params = SetupParams(min_bars=120)
    instances, _gates = find_instances(bars, None, params, (5, 10), setup=setup)
    assert not instances.empty, "a permissive setup must produce historical instances"
    assert set(instances["horizon_days"]) == {5, 10}
    assert (instances["entry_close"] > 0).all()


def test_the_backtest_of_a_lookahead_setup_produces_nothing_and_says_why(config):
    from evescreener.backtest import find_instances
    from evescreener.signals.setup import SetupParams

    stamps = pd.date_range("2026-01-01 11:00", periods=200, freq="D", tz="UTC")
    closes = np.linspace(100, 200, 200)
    bars = pd.DataFrame(
        {
            "type_id": 600,
            "region_id": 10000002,
            "datetime": stamps,
            "high": closes * 1.02,
            "low": closes * 0.98,
            "close": closes,
            "volume": 50_000.0,
            "order_count": 40,
            "isk_value": closes * 50_000.0,
            "fetched_at": "x",
        }
    )
    setup = Setup(
        name="Level touch",
        conditions=(Condition("near_level", {"level": "hv", "within_atr": 1.0, "side": "any"}),),
    )
    instances, _gates = find_instances(bars, None, SetupParams(min_bars=120), (5,), setup=setup)
    assert instances.empty, "a setup that cannot be measured without lookahead measures nothing"
