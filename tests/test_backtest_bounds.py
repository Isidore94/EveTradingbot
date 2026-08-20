"""R3 — price bounds, honest statistics, and friction that says what it is.

Four contracts, all of them about claiming exactly what was measured:

**A sale cannot realise negative ISK.** Exit stress was
`exit_close * (1 - haircut * multiple)`. With bid 1, ask 99, mid 50, the exit
haircut is ~0.98; at 2x stress the factor is `1 - 1.96 = -0.96`, so the trade
"realised" a negative price and an unlevered long returned worse than -100%.

**A bound must be the bound it is called.** `z = 1.96` is the two-sided 95%
critical value, i.e. a **one-sided 97.5%** bound. It was labelled 95%
one-sided.

**Overlapping windows are not independent observations.** Ten-day forward
returns sampled daily on the same type share nine days of bars.

**Friction has parts, and they were added together.** The reported round-trip
haircut already included sales tax while the control text called it friction
"before tax".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evescreener.backtest import (
    effective_samples,
    stress_factors,
    wilson_lower_bound,
)

# -- 1. stress prices stay economically possible ----------------------------


def test_a_wide_book_at_two_x_stress_does_not_invert_the_sale():
    """bid 1 / ask 99 / mid 50 gives an exit haircut of ~0.98 (§21 R3)."""
    entry_factor, exit_factor = stress_factors(entry_haircut=0.98, exit_haircut=0.98, multiple=2.0)
    assert exit_factor >= 0.0, "a sale cannot realise negative ISK"
    assert exit_factor == 0.0, "at this stress the exit realises nothing at all"
    assert entry_factor > 1.0, "the buy only ever costs more"


@pytest.mark.parametrize("multiple", [1.0, 2.0, 3.0])
@pytest.mark.parametrize("haircut", [0.0, 0.1, 0.5, 0.9, 0.98, 1.0])
def test_exit_proceeds_are_never_negative_at_any_stress(haircut, multiple):
    _entry, exit_factor = stress_factors(
        entry_haircut=haircut, exit_haircut=haircut, multiple=multiple
    )
    assert 0.0 <= exit_factor <= 1.0


def test_zero_liquidity_is_represented_explicitly_not_as_a_negative_price():
    """A book that cannot absorb the exit loses the position, it does not owe."""
    _entry, exit_factor = stress_factors(entry_haircut=0.0, exit_haircut=1.0, multiple=1.0)
    assert exit_factor == 0.0
    # A total loss is -100%, and never worse, for an unlevered long.
    net_return_pct = (100.0 * exit_factor / (100.0 * 1.0) - 1.0) * 100.0
    assert net_return_pct == -100.0


def test_an_untouched_book_leaves_the_price_alone():
    entry_factor, exit_factor = stress_factors(entry_haircut=0.0, exit_haircut=0.0, multiple=3.0)
    assert entry_factor == 1.0
    assert exit_factor == 1.0


# -- 2. the Wilson bound is labelled as what it is --------------------------


def test_the_wilson_helper_states_its_own_one_sided_confidence():
    """1.96 is two-sided 95%, which is one-sided 97.5% (§21 R3)."""
    from evescreener.backtest import wilson_one_sided_confidence

    assert wilson_one_sided_confidence(1.96) == pytest.approx(0.975, abs=5e-4)
    assert wilson_one_sided_confidence(1.645) == pytest.approx(0.95, abs=5e-4)


def test_the_frozen_z_is_unchanged_so_no_old_result_moves():
    """The label was wrong, not the number. Changing z would move a verdict."""
    import inspect

    from evescreener import backtest

    signature = inspect.signature(backtest.wilson_lower_bound)
    assert signature.parameters["z"].default == 1.96


def test_a_more_conservative_bound_cannot_have_flattered_the_old_verdict():
    """97.5% one-sided is stricter than 95%, so NOT PLAUSIBLE stays sound."""
    strict = wilson_lower_bound(60, 100, 1.96)
    loose = wilson_lower_bound(60, 100, 1.645)
    assert strict < loose


# -- 3. overlapping instances are not independent ---------------------------


def _instances(type_ids, days, horizon_offset=0):
    rows = []
    for type_id in type_ids:
        for day in range(days):
            rows.append(
                {
                    "type_id": type_id,
                    "datetime": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=day),
                }
            )
    return pd.DataFrame(rows)


def test_daily_samples_of_a_ten_day_return_are_not_ten_independent_ones():
    """Ten-day forward returns sampled daily share nine days of bars."""
    frame = _instances([34], days=100)
    assert len(frame) == 100
    assert effective_samples(frame, horizon=10) == 10


def test_effective_samples_counts_each_type_separately():
    """Two types over the same dates are two independent series, not one."""
    frame = _instances([34, 35], days=100)
    assert effective_samples(frame, horizon=10) == 20


def test_a_one_day_horizon_overlaps_nothing():
    frame = _instances([34], days=100)
    assert effective_samples(frame, horizon=1) == 100


def test_effective_samples_never_exceeds_the_raw_count():
    frame = _instances([34], days=5)
    assert effective_samples(frame, horizon=10) <= len(frame)
    assert effective_samples(frame, horizon=10) >= 1


def test_the_clustered_bound_is_never_more_confident_than_the_naive_one():
    """Fewer independent observations can only widen the interval."""
    naive = wilson_lower_bound(60, 100, 1.96)
    clustered = wilson_lower_bound(6, 10, 1.96)
    assert clustered < naive


# -- 4. friction reports its parts ------------------------------------------


def test_round_trip_friction_separates_book_haircut_from_tax():
    """'14.7% friction before tax' was a number that already had tax in it."""
    from evescreener.backtest import friction_breakdown

    parts = friction_breakdown(
        entry_close=100.0,
        entry_effective=105.0,
        exit_close=100.0,
        exit_effective=90.0,
        sales_tax_pct=3.375,
    )
    # Entry: 105/100 - 1 = 5%. Exit: the 90 received is AFTER tax, so the
    # book's own bite is 1 - (90 / 0.96625) / 100 = 6.8564%.
    expected_exit = (1.0 - (90.0 / (1.0 - 0.03375)) / 100.0) * 100.0
    assert parts["book_haircut_pct"] == pytest.approx(5.0 + expected_exit, rel=1e-9)
    assert parts["sales_tax_pct"] == pytest.approx(3.375)
    # Tax is levied on what the book already left, so the two COMPOUND. The
    # sum would overstate the cost of a strategy already judged NOT PLAUSIBLE.
    book = parts["book_haircut_pct"] / 100.0
    assert parts["total_friction_pct"] == pytest.approx(
        (1.0 - (1.0 - book) * (1.0 - 0.03375)) * 100.0
    )
    assert parts["total_friction_pct"] < parts["book_haircut_pct"] + parts["sales_tax_pct"]


def test_the_breakdown_labels_cannot_be_confused_for_one_another():
    from evescreener.backtest import friction_breakdown

    parts = friction_breakdown(
        entry_close=100.0,
        entry_effective=100.0,
        exit_close=100.0,
        exit_effective=100.0,
        sales_tax_pct=0.0,
    )
    assert set(parts) == {"book_haircut_pct", "sales_tax_pct", "total_friction_pct"}
    assert all(value == 0.0 for value in parts.values())


# -- 5. max drawdown is gone until a portfolio model exists ------------------


def test_max_drawdown_is_not_reported_without_a_portfolio_model():
    """Compounding overlapping trades in date order is not an equity curve."""
    from evescreener.backtest import HorizonStats

    fields = {field for field in HorizonStats.__dataclass_fields__}
    assert "max_drawdown_pct" not in fields, (
        "sequential compounding of overlapping trades has no capital model "
        "behind it and must not be called drawdown (§21 R3)"
    )
    stats = HorizonStats(
        horizon_days=10,
        notional_isk=250_000_000.0,
        haircut_multiple=1.0,
        samples=0,
        wins=0,
        win_rate=None,
        wilson_lb=None,
        breakeven_win_rate=None,
        expectancy_pct=None,
        median_pct=None,
    )
    assert "max_drawdown_pct" not in stats.as_dict()
    assert "effective_samples" in stats.as_dict()


def test_the_stats_row_carries_the_clustered_bound_beside_the_naive_one():
    """Both are reported; the old number is not deleted, it is contextualised."""
    from evescreener.backtest import HorizonStats

    payload = HorizonStats(
        horizon_days=10,
        notional_isk=250_000_000.0,
        haircut_multiple=1.0,
        samples=100,
        wins=56,
        win_rate=0.56,
        wilson_lb=0.46,
        breakeven_win_rate=0.5,
        expectancy_pct=1.0,
        median_pct=0.5,
        effective_samples=10,
        wilson_lb_clustered=0.30,
    ).as_dict()
    assert payload["wilson_lb"] == 0.46
    assert payload["wilson_lb_clustered"] == 0.30
    assert payload["effective_samples"] == 10
    assert np.isfinite(payload["wilson_lb_clustered"])
