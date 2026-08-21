"""S4 — an exploratory pooled result must not be rendered as a tested H2.

R5 made the *payload* honest: it carries a `cohort_declaration` saying the
pooled catalogue-wide run is exploratory and not evidence about H2. Every
renderer then threw that away and printed "the lead-lag claim was tested and
not supported", which is a claim about H2 — the confirmatory run that does not
exist.

`brief.py` was worse: it printed that sentence whenever `destruction_z` was
merely present, with no lead-lag payload involved at all.

Separately, the dependence correction was decorative. `independent_observations`
counted types and changed nothing: `spearman()` still returned a p-value
computed as though every row were independent, and Bonferroni was applied to
that. A cluster-aware p-value now stands beside it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from evescreener.killmails import (
    COHORT_DOCTRINE,
    COHORT_POOLED,
    H2_UNKNOWN,
    LeadLagResult,
    evaluate_lead_lag,
    h2_statement,
    rotation_permutation_p,
)


def _pooled_result(outcome="DOES NOT SURVIVE"):
    result = LeadLagResult(generated_at="2026-08-20T00:00:00+00:00")
    result.observations = 473_606
    result.independent_observations = 2_654
    result.lags = [
        {
            "lag_days": 1,
            "target": "participation",
            "rho": 0.027,
            "p_value": 1e-40,
            "observations": 473_606,
        }
    ]
    result.outcome = {"rule": "…", "outcome": outcome, "reason": "…"}
    return result


# -- 1. the operator-facing statement is about H2, and H2 is UNKNOWN --------


def test_a_pooled_run_yields_h2_unknown_not_a_tested_verdict():
    """The confirmatory run does not exist, so H2 has no verdict (§22 S4)."""
    statement = h2_statement(_pooled_result())
    assert statement["h2"] == H2_UNKNOWN
    assert "confirmatory run absent" in statement["h2_reason"].lower()
    assert statement["evidence_class"] == "exploratory"


def test_the_pooled_finding_is_still_reported_beside_it():
    """Exploratory is not nothing. It is shown, labelled as what it is."""
    statement = h2_statement(_pooled_result())
    assert statement["exploratory_outcome"] == "DOES NOT SURVIVE"
    assert statement["cohort"] == COHORT_POOLED


def test_a_surviving_pooled_result_still_cannot_confirm_h2():
    """The direction of the pooled answer does not change what it is evidence of."""
    statement = h2_statement(_pooled_result(outcome="SURVIVES"))
    assert statement["h2"] == H2_UNKNOWN
    assert statement["exploratory_outcome"] == "SURVIVES"


def test_only_a_declared_doctrine_cohort_may_carry_an_h2_verdict():
    result = _pooled_result()
    result.cohort = COHORT_DOCTRINE
    statement = h2_statement(result)
    assert statement["h2"] != H2_UNKNOWN
    assert statement["evidence_class"] == "confirmatory"


def test_no_result_at_all_is_unknown_rather_than_absent():
    statement = h2_statement(None)
    assert statement["h2"] == H2_UNKNOWN
    assert statement["exploratory_outcome"] is None


# -- 2. renderers carry the cohort, not just the outcome --------------------


def test_the_digest_does_not_say_the_claim_was_tested(config):
    import inspect

    from evescreener.digest import build_digest

    source = inspect.getsource(build_digest)
    assert "tested and not supported" not in source, (
        "that sentence is a claim about H2, which has no confirmatory run"
    )


def test_the_brief_does_not_claim_h2_merely_because_destruction_z_exists():
    import inspect

    from evescreener import brief

    source = inspect.getsource(brief.render_brief)
    assert "tested and not supported" not in source
    # A brief holds no lead-lag payload at all, so it may only say what
    # destruction_z is: an annotation.
    assert "annotation" in source.lower()


def test_the_consequence_text_no_longer_asserts_a_test_of_h2():
    result = _pooled_result()
    verdict = evaluate_lead_lag(result)
    consequence = (verdict.get("consequence") or "").lower()
    assert "annotation" in consequence
    assert "tested and not supported" not in consequence


# -- 3. the dependence correction is real, not decorative -------------------


def test_a_rotation_permutation_respects_within_type_serial_structure():
    """Rotating each type's series preserves its autocorrelation exactly."""
    rng = np.random.default_rng(7)
    days = pd.date_range("2026-01-01", periods=60, freq="D", tz="UTC")
    frames = []
    for type_id in range(1, 6):
        walk = np.cumsum(rng.normal(0, 1, len(days)))
        frames.append(pd.DataFrame({"type_id": type_id, "day": days, "x": walk, "y": walk}))
    frame = pd.concat(frames, ignore_index=True)
    # x and y identical: rho is 1.0, and no rotation can beat that.
    p = rotation_permutation_p(
        frame["x"].to_numpy(),
        frame["y"].to_numpy(),
        frame["type_id"].to_numpy(),
        observed_rho=1.0,
        permutations=49,
        seed=1,
    )
    assert 0.0 < p <= 1.0
    assert p < 0.5, "a perfect alignment should be rare under rotation"


def test_a_pure_noise_pairing_is_not_declared_significant():
    """Independent series must not produce a small permutation p-value."""
    rng = np.random.default_rng(11)
    days = pd.date_range("2026-01-01", periods=80, freq="D", tz="UTC")
    frames = []
    for type_id in range(1, 9):
        frames.append(
            pd.DataFrame(
                {
                    "type_id": type_id,
                    "day": days,
                    "x": np.cumsum(rng.normal(0, 1, len(days))),
                    "y": np.cumsum(rng.normal(0, 1, len(days))),
                }
            )
        )
    frame = pd.concat(frames, ignore_index=True)
    from evescreener.killmails import spearman

    rho, naive_p, _n = spearman(frame["x"].to_numpy(), frame["y"].to_numpy())
    p = rotation_permutation_p(
        frame["x"].to_numpy(),
        frame["y"].to_numpy(),
        frame["type_id"].to_numpy(),
        observed_rho=rho,
        permutations=199,
        seed=3,
    )
    # Two independent random walks are strongly rank-correlated by accident,
    # and the naive p-value calls that significance. The rotation test does not.
    assert p > naive_p, "the cluster-aware p-value must be the more sceptical one"


def test_the_permutation_p_value_is_deterministic_for_a_seed():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    y = np.array([2.0, 1.0, 4.0, 3.0, 6.0, 5.0])
    groups = np.array([1, 1, 1, 2, 2, 2])
    first = rotation_permutation_p(x, y, groups, observed_rho=0.5, permutations=99, seed=5)
    second = rotation_permutation_p(x, y, groups, observed_rho=0.5, permutations=99, seed=5)
    assert first == second


def test_a_permutation_p_value_can_never_be_zero():
    """An empirical p-value is bounded below by 1/(permutations + 1)."""
    x = np.arange(20.0)
    groups = np.ones(20, dtype=int)
    p = rotation_permutation_p(x, x, groups, observed_rho=1.0, permutations=9, seed=1)
    assert p >= 1 / 10


def test_the_lag_rows_carry_both_p_values_and_say_which_assumes_independence():
    from evescreener.killmails import adjusted_verdict

    row = adjusted_verdict({"p_value": 0.005, "p_value_permutation": 0.30, "rho": 0.2})
    assert row["p_value_frozen_rule"] is True
    # Bonferroni is applied to the cluster-aware p-value, not the naive one.
    assert row["p_value_family_wise"] is False
    assert row["p_value_assumes_independence"] is True


def test_independent_observations_is_no_longer_decorative():
    """It must reach something. A field nothing reads is not a correction."""
    import inspect

    from evescreener import killmails

    source = inspect.getsource(killmails.run_lead_lag_study)
    assert "rotation_permutation_p" in source, (
        "the dependence correction must reach the reported inference"
    )
