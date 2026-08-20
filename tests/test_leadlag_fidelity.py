"""R5 — the lead-lag study must test the hypothesis that was frozen.

**H2 (plan.md §14.1) is about doctrine-class hulls and their fitted modules,
with losses bucketed by regional catchment.** The implementation pooled global
destruction against every type in the lake. Pooling unrelated catalogue types
can dilute a real effect or manufacture one, and either way the number
answers a different question than the one that was preregistered.

**`groupby.shift(-lag)` is the next observed row, not `day + k`.** A type with
bars on 1 January and 10 January had 10 January labelled "lag 1". Across a
sparse lake that silently mixes nine-day leads into a one-day bucket.

**Ten tests were run and each judged at p < 0.01.** Five lags times two
targets, with no family-wise policy declared.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evescreener.killmails import (
    FAMILY_ALPHA,
    LEAD_LAG_TESTS,
    exact_lag_frame,
    independent_observations,
)


def _bars(days, type_id=34):
    return pd.DataFrame(
        {
            "type_id": type_id,
            "day": [pd.Timestamp(day, tz="UTC") for day in days],
            "close": np.arange(1.0, len(days) + 1.0),
            "participation": np.arange(1.0, len(days) + 1.0),
        }
    )


# -- 1. lags are calendar days, not row positions ---------------------------


def test_a_gap_in_the_series_does_not_become_a_one_day_lead():
    """1 Jan and 10 Jan: shift(-1) called the 10th a one-day lead (§21 R5)."""
    frame = _bars(["2026-01-01", "2026-01-10"])
    lagged = exact_lag_frame(frame, lag=1)
    # There is no 2 January, so the 1 January row has no lag-1 partner at all.
    assert lagged["close_lead"].isna().all()


def test_an_exact_calendar_partner_is_found():
    frame = _bars(["2026-01-01", "2026-01-02", "2026-01-03"])
    lagged = exact_lag_frame(frame, lag=1)
    first = lagged[lagged["day"] == pd.Timestamp("2026-01-01", tz="UTC")].iloc[0]
    assert first["close_lead"] == 2.0
    # The last row has nothing after it.
    last = lagged[lagged["day"] == pd.Timestamp("2026-01-03", tz="UTC")].iloc[0]
    assert pd.isna(last["close_lead"])


def test_a_five_day_lag_joins_day_plus_five_exactly():
    days = [f"2026-01-{n:02d}" for n in range(1, 11)]
    frame = _bars(days)
    lagged = exact_lag_frame(frame, lag=5)
    first = lagged[lagged["day"] == pd.Timestamp("2026-01-01", tz="UTC")].iloc[0]
    assert first["close_lead"] == 6.0


def test_each_type_is_lagged_against_its_own_calendar():
    left = _bars(["2026-01-01", "2026-01-02"], type_id=34)
    right = _bars(["2026-01-01", "2026-01-02"], type_id=35)
    frame = pd.concat([left, right], ignore_index=True)
    lagged = exact_lag_frame(frame, lag=1)
    for type_id in (34, 35):
        row = lagged[
            (lagged["type_id"] == type_id) & (lagged["day"] == pd.Timestamp("2026-01-01", tz="UTC"))
        ].iloc[0]
        assert row["close_lead"] == 2.0


def test_a_forward_return_over_a_gap_is_unknown_not_interpolated():
    frame = _bars(["2026-01-01", "2026-01-10"])
    lagged = exact_lag_frame(frame, lag=1)
    forward = lagged["close_lead"] / lagged["close"] - 1.0
    assert forward.isna().all(), "a missing day is UNKNOWN, never a zero return"


# -- 2. dependence-aware sample size ----------------------------------------


def test_observations_from_one_type_are_not_hundreds_of_independent_facts():
    """Serial and cross-sectional dependence both bite (§21 R5)."""
    days = pd.date_range("2026-01-01", periods=100, freq="D", tz="UTC")
    frame = pd.DataFrame({"type_id": 34, "day": days})
    assert independent_observations(frame) == 1


def test_each_type_contributes_one_independent_cluster():
    days = pd.date_range("2026-01-01", periods=100, freq="D", tz="UTC")
    frame = pd.concat(
        [pd.DataFrame({"type_id": type_id, "day": days}) for type_id in (34, 35, 36)],
        ignore_index=True,
    )
    assert independent_observations(frame) == 3


def test_an_empty_frame_has_no_observations():
    assert independent_observations(pd.DataFrame()) == 0


# -- 3. a declared multiple-comparison policy -------------------------------


def test_the_family_of_tests_is_declared_and_the_alpha_is_corrected():
    """Five lags x two targets is ten tests, each judged at p < 0.01."""
    assert LEAD_LAG_TESTS == 10
    assert FAMILY_ALPHA == pytest.approx(0.01 / 10)


def test_the_frozen_rule_text_is_not_edited():
    """§14.3 is frozen. The amendment is added beside it, never over it."""
    from evescreener.killmails import PASS_RULE

    assert "rho >= 0.10" in PASS_RULE
    assert "p < 0.01" in PASS_RULE
    assert "frozen 2026-08-20 before measurement" in PASS_RULE


def test_every_lag_row_carries_its_family_adjusted_verdict():
    from evescreener.killmails import adjusted_verdict

    # p = 0.005 clears the frozen 0.01 but not the family-wise 0.001.
    row = adjusted_verdict({"p_value": 0.005, "rho": 0.2})
    assert row["p_value_frozen_rule"] is True
    assert row["p_value_family_wise"] is False
    strong = adjusted_verdict({"p_value": 1e-6, "rho": 0.2})
    assert strong["p_value_family_wise"] is True
    unknown = adjusted_verdict({"p_value": None, "rho": None})
    assert unknown["p_value_frozen_rule"] is None
    assert unknown["p_value_family_wise"] is None


# -- 4. the pooled run is labelled exploratory ------------------------------


def test_the_pooled_run_declares_itself_exploratory():
    """It pooled every catalogue type; H2 named a doctrine cohort (§21 R5)."""
    from evescreener.killmails import COHORT_POOLED, cohort_declaration

    declared = cohort_declaration(COHORT_POOLED)
    assert declared["cohort"] == COHORT_POOLED
    assert declared["evidence_class"] == "exploratory"
    assert "NOT the H2 cohort" in declared["caveat"]
    assert declared["catchment"] == "global"


def test_the_h2_cohort_declares_itself_confirmatory_and_names_its_catchment():
    from evescreener.killmails import COHORT_DOCTRINE, cohort_declaration

    declared = cohort_declaration(COHORT_DOCTRINE)
    assert declared["evidence_class"] == "confirmatory"
    assert declared["catchment"] != "global"
    assert "doctrine" in declared["definition"].lower()


def test_a_result_carries_its_cohort_so_two_runs_cannot_be_confused():
    from evescreener.killmails import COHORT_POOLED, LeadLagResult

    result = LeadLagResult(generated_at="2026-08-20T00:00:00+00:00")
    assert result.cohort == COHORT_POOLED
    payload = result.as_dict()
    assert payload["cohort"] == COHORT_POOLED
    assert payload["cohort_declaration"]["evidence_class"] == "exploratory"
    assert payload["multiple_comparisons"]["tests"] == LEAD_LAG_TESTS
