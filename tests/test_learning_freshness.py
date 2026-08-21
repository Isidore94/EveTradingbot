"""R6 — freshness must change the number that is actually ranked.

`learning.py` computed `freshness_factor(days_since_last)` and stored it on the
record, then ranked on `expected_r`, which never saw it. A setup last measured
a year ago sorted exactly level with one measured yesterday. The existing tests
established only that the field changed value, not that anything depended on
it — which is how a decorative number survives.

Separately, shrinkage used `closed = len(rows)` — every closed trade — while
the mean R was computed only over rows that *have* a realized R. A setup with
twenty closes and two scored outcomes was shrunk as though it had twenty facts.
"""

from __future__ import annotations

import pytest

from evescreener.learning import (
    SetupRecord,
    effective_expected_r,
)

# -- 1. one shared expected-R contract --------------------------------------


def test_freshness_scales_the_expected_r_that_is_ranked():
    """The contract: what is ranked is what freshness has already touched."""
    assert effective_expected_r(1.0, 1.0) == pytest.approx(1.0)
    assert effective_expected_r(1.0, 0.5) == pytest.approx(0.5)
    assert effective_expected_r(2.0, 0.4) == pytest.approx(0.8)


def test_an_unknown_freshness_cannot_silently_mean_fresh():
    """No freshness is UNKNOWN, and UNKNOWN must not read as 1.0 (§4)."""
    assert effective_expected_r(1.0, None) is None
    assert effective_expected_r(None, 1.0) is None


def _record(name, expected_r, freshness, *, closed=10, state="MEASURED"):
    record = SetupRecord(name=name, closed=closed)
    record.expected_r = expected_r
    record.freshness = freshness
    record.state = state
    record.apply_freshness()
    return record


def test_aging_adverse_evidence_does_not_improve_its_rank():
    """§22 S5c: R6 multiplied, which flatters a loss as it goes stale.

    `-1R x 0.01 = -0.01R` outranked `-0.1R x 1.0 = -0.1R`, so a severe loss
    that had gone stale sorted *above* a mild one measured yesterday. Decay
    moves an estimate toward the 0R prior, which shrinks a gain and must not
    shrink a loss — a stale loss is not evidence of a smaller loss.
    """
    from evescreener.learning import rank_setups

    assert effective_expected_r(-1.0, 1.0) == pytest.approx(-1.0)
    assert effective_expected_r(-1.0, 0.01) == pytest.approx(-1.0), "held, not shrunk"
    # A positive expectancy still decays toward zero.
    assert effective_expected_r(1.0, 0.4) == pytest.approx(0.4)

    stale_severe = _record("stale-severe", expected_r=-1.0, freshness=0.01)
    fresh_mild = _record("fresh-mild", expected_r=-0.1, freshness=1.0)
    assert [r.name for r in rank_setups([stale_severe, fresh_mild])] == [
        "fresh-mild",
        "stale-severe",
    ]


def test_no_staleness_cliff_is_invented():
    """`freshness_factor` is bounded to [0.4, 1.0]; there is no point in that
    range at which it says "this carries no information", so no cutoff is
    added. Small-sample scepticism stays where it belongs — in
    `MIN_SAMPLES_FOR_A_READ` and the Wilson lower bound."""
    from evescreener import learning

    assert not hasattr(learning, "STALE_EVIDENCE_FLOOR")
    assert not hasattr(learning, "freshness_state")


# -- 2. the record carries the ranked value --------------------------------


def test_the_record_exposes_both_the_raw_and_the_freshness_adjusted_value():
    """The raw number stays visible; the ranked one is the adjusted one."""
    record = SetupRecord(name="dip")
    record.expected_r = 1.0
    record.freshness = 0.5
    record.apply_freshness()
    assert record.expected_r == pytest.approx(1.0), "the raw blend stays readable"
    assert record.effective_expected_r == pytest.approx(0.5)
    assert "effective_expected_r" in record.as_dict()


def test_a_record_with_no_freshness_reports_unknown_effective_r():
    record = SetupRecord(name="dip")
    record.expected_r = 1.0
    record.freshness = None
    record.apply_freshness()
    assert record.effective_expected_r is None


# -- 3. ranking actually moves -----------------------------------------------


def test_a_year_old_setup_no_longer_ranks_level_with_yesterdays():
    """The R6 defect, stated as an ordering (§21 R6)."""
    from evescreener.learning import rank_setups

    stale = _record("stale", expected_r=1.0, freshness=0.4)
    fresh = _record("fresh", expected_r=0.9, freshness=1.0)
    ranked = rank_setups([stale, fresh])
    assert [record.name for record in ranked] == ["fresh", "stale"], (
        "0.9 measured yesterday beats 1.0 measured a year ago"
    )


def test_an_unknown_setup_still_never_outranks_a_measured_one():
    """The pre-existing invariant must survive the change."""
    from evescreener.learning import rank_setups

    unknown = _record("unknown", expected_r=5.0, freshness=1.0, closed=1, state="UNKNOWN")
    measured = _record("measured", expected_r=0.1, freshness=1.0)
    ranked = rank_setups([unknown, measured])
    assert [record.name for record in ranked] == ["measured", "unknown"]


def test_ranking_is_stable_by_name_when_everything_else_ties():
    from evescreener.learning import rank_setups

    first = _record("aaa", expected_r=1.0, freshness=1.0)
    second = _record("bbb", expected_r=1.0, freshness=1.0)
    assert [r.name for r in rank_setups([second, first])] == ["aaa", "bbb"]


# -- 4. the eligible denominator --------------------------------------------


def test_shrinkage_counts_outcomes_that_actually_have_an_r():
    """Twenty closes with two scored outcomes is two facts, not twenty."""
    rows = [{"realized_r": 1.0}, {"realized_r": 2.0}] + [{"realized_r": None}] * 18
    from evescreener.learning import eligible_outcomes

    assert eligible_outcomes(rows) == 2
    assert len(rows) == 20


def test_a_record_reports_its_eligible_denominator_beside_its_closed_count():
    record = SetupRecord(name="dip", closed=20)
    record.eligible = 2
    payload = record.as_dict()
    assert payload["closed"] == 20
    assert payload["eligible"] == 2, (
        "a reader must be able to see that eighteen closes carried no R"
    )


def test_eligible_outcomes_of_an_empty_set_is_zero():
    from evescreener.learning import eligible_outcomes

    assert eligible_outcomes([]) == 0
    assert eligible_outcomes([{"realized_r": None}]) == 0


# -- 5. the loop still never mutates a setup ---------------------------------


def test_the_learning_loop_still_promotes_nothing():
    """R6 must not have bought ranking sensitivity with authority."""
    import inspect

    from evescreener import learning

    source = inspect.getsource(learning)
    for forbidden in ("setups.jsonl", "write_setup", "promote("):
        assert forbidden not in source, f"the learning loop must not {forbidden}"
