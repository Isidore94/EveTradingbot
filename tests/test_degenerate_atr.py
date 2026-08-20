"""A risk unit made of float noise is not a risk unit (plan.md §17 D-29).

`measurable` used to ask only `atr > 0`. On the operator's real lake that let
through **2.6% of tracked types whose ATR is around 4e-11** — near-flat price
series where the twenty-day "range" is the last bits of a float. Their dip-σ
and RRS then divide by it and explode: *Power Couplings* measured RRS
−905 billion. Both surfaces that rank by depth — the board's value sort and
the screen — select for exactly those names, so the degenerate tail was the
first thing the operator saw.

The gate is now **relative**: an ATR below `signals.min_atr_fraction` of price
is UNKNOWN, and UNKNOWN fails (§4).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evescreener.signals.atr import MIN_ATR_FRACTION, atr_last, risk_unit
from evescreener.signals.rrs import real_relative_strength
from evescreener.signals.setup import SetupParams, evaluate_setups


def _frame(closes, *, volume=5_000.0, order_count=40, spread=0.0):
    stamps = pd.date_range("2026-01-01 11:00", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "datetime": stamps,
            "high": [c * (1.0 + spread) for c in closes],
            "low": [c * (1.0 - spread) for c in closes],
            "close": list(closes),
            "volume": [volume] * len(closes),
            "order_count": [order_count] * len(closes),
            "isk_value": [c * volume for c in closes],
        }
    )


def _near_flat(bars=200, price=1_000.0, wobble=1e-11):
    """A series that does not move: ATR lands at ~1e-11 of price."""
    generator = np.random.default_rng(4)
    return _frame([price + generator.normal(0.0, wobble) for _ in range(bars)])


def _normal(bars=200, price=1_000.0):
    generator = np.random.default_rng(5)
    walk = price * np.cumprod(1.0 + generator.normal(0.0, 0.02, bars))
    return _frame(walk, spread=0.01)


# -- the fixture reproduces the defect --------------------------------------


def test_the_near_flat_fixture_really_is_degenerate():
    """Guards the fixture: it must actually carry a float-noise ATR."""
    frame = _near_flat()
    raw = atr_last(frame.iloc[:-1], min_fraction=0.0)
    close = float(frame["close"].iloc[-1])
    assert raw is not None and raw > 0, "the old gate passed this"
    assert raw / close < 1e-9, f"atr/close is {raw / close:.2e}, not degenerate enough"


# -- 1. a degenerate series is UNKNOWN, not a pass --------------------------


def test_a_degenerate_atr_is_unknown_rather_than_a_tiny_number():
    frame = _near_flat()
    assert atr_last(frame.iloc[:-1]) is None
    assert risk_unit(frame.iloc[:-1]) is None


def test_a_degenerate_series_fails_the_measurable_gate_as_unknown():
    frame = _near_flat()
    result = evaluate_setups(frame, None, params=SetupParams(min_bars=20))
    last = result.iloc[-1]
    assert not bool(last["measurable"])
    assert bool(last["measurable_unknown"]), "it must read UNKNOWN, not merely False"
    assert not bool(last["is_setup"])


def test_a_degenerate_series_reports_dip_sigma_as_unknown():
    """The same flatness makes the AVWAP sigma degenerate too.

    dip-σ divides by σ, not by ATR, so it is a second denominator with the
    same failure — and one epsilon governs both.
    """
    frame = _near_flat()
    result = evaluate_setups(frame, None, params=SetupParams(min_bars=20))
    last = result.iloc[-1]
    assert bool(last["below_anchored_value_unknown"])
    assert not bool(last["below_anchored_value"])


def test_a_degenerate_series_reports_rrs_as_unknown():
    reference = _normal()
    frame = _near_flat()
    strength = real_relative_strength(frame, reference, length=20)
    assert strength.rrs is None
    assert strength.unknown_reason


# -- 2. a normal series is untouched ----------------------------------------


def test_a_normal_series_is_completely_unaffected():
    frame = _normal()
    guarded = atr_last(frame.iloc[:-1])
    unguarded = atr_last(frame.iloc[:-1], min_fraction=0.0)
    assert guarded is not None
    assert guarded == unguarded
    assert risk_unit(frame.iloc[:-1]) is not None


def test_a_normal_series_still_measures_rrs_and_dip_sigma():
    reference = _normal(price=500.0)
    frame = _normal()
    assert real_relative_strength(frame, reference, length=20).rrs is not None
    result = evaluate_setups(frame, reference, params=SetupParams(min_bars=20))
    last = result.iloc[-1]
    assert bool(last["measurable"])
    assert not bool(last["measurable_unknown"])
    assert np.isfinite(last["dip_sigma"])


def test_a_quiet_but_real_type_is_kept():
    """The floor must not swallow low-volatility names that genuinely trade.

    1e-6 sits in the empty band the real lake shows between the degenerate
    cluster (p1 = 1.6e-08) and the working distribution (p2 = 2.4e-05). A type
    whose ATR is a tenth of a percent of price is ordinary and must survive.
    """
    generator = np.random.default_rng(6)
    walk = 1_000.0 * np.cumprod(1.0 + generator.normal(0.0, 0.001, 200))
    frame = _frame(walk, spread=0.0005)
    value = atr_last(frame.iloc[:-1])
    assert value is not None
    assert value / float(frame["close"].iloc[-1]) > MIN_ATR_FRACTION


# -- 3. the epsilon has one definition site ---------------------------------


def test_the_floor_is_configurable_and_defaults_to_the_measured_value():
    assert MIN_ATR_FRACTION == pytest.approx(1e-6)


def test_the_floor_is_read_from_config(config):
    from evescreener.screen import setup_params

    assert config.signals.min_atr_fraction == pytest.approx(MIN_ATR_FRACTION)
    assert setup_params(config).min_atr_fraction == pytest.approx(config.signals.min_atr_fraction)


def test_raising_the_floor_excludes_more(config):
    """The gate is relative, so a higher floor must reject a bigger type."""
    frame = _normal()
    close = float(frame["close"].iloc[-1])
    value = atr_last(frame.iloc[:-1], min_fraction=0.0)
    assert atr_last(frame.iloc[:-1], min_fraction=(value / close) * 0.5) is not None
    assert atr_last(frame.iloc[:-1], min_fraction=(value / close) * 2.0) is None


def test_a_nonpositive_close_is_unknown_not_a_division():
    frame = _normal()
    frame.loc[frame.index[-1], "close"] = 0.0
    assert atr_last(frame) is None
