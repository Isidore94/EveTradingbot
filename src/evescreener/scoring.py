"""Expected-R scoring — plan.md §6, wiring the vendored engine to EVE inputs.

`vendored/expected_r.py` is pure math over `(setup, outcome)` samples: a static
quality score becomes a prior expected R, which is then blended toward the
**realized** R the operator's own closed trades have actually produced, with a
shrinkage constant so a two-trade fluke cannot hijack the ranking, and a
freshness decay so a stale signal cannot outrank a fresh one on points alone.

Two EVE-specific facts shape how it is wired here:

1. **Calibration restarts at zero samples.** Nothing carries over from the
   equity system; the priors are the vendored defaults until the operator's own
   paper ledger has closed trades. Until then `closed_samples = 0`, the blend
   weight is 0, and expected-R is the structural prior — which the report and
   digest must say plainly rather than presenting as measured.
2. **Freshness decay matters more here than in equities** (§6): a patch can
   reshape an item overnight, so evidence ages faster.

The static quality points are built from what this system actually measures —
the depth of the dip against anchored value, relative strength, participation,
and nearby level conviction — and nothing else. There is no hand-tuned signal
stack to inherit.
"""

from __future__ import annotations

from dataclasses import dataclass

from .vendored.expected_r import compute_expected_r

__all__ = ["QUALITY_ANCHORS", "SetupScore", "quality_points", "score_candidate"]

# Points scale matched to the vendored default anchors (60 -> -0.20R,
# 100 -> +0.30R, 140 -> +0.70R, 180 -> +1.05R). 100 is the "worth looking at"
# threshold, exactly as upstream.
QUALITY_ANCHORS = (60.0, 100.0, 140.0, 180.0)

DIP_POINTS_PER_SIGMA = 22.0
DIP_POINTS_CAP = 55.0
RRS_POINTS_PER_UNIT = 12.0
RRS_POINTS_CAP = 30.0
PARTICIPATION_POINTS_CAP = 20.0
LEVEL_CONVICTION_POINTS = 15.0
NET_EDGE_POINTS_PER_PCT = 2.0
NET_EDGE_POINTS_CAP = 40.0
BASE_POINTS = 60.0


def quality_points(
    *,
    dip_sigma: float | None,
    rrs: float | None,
    participation: float | None,
    level_conviction: float | None = None,
    net_edge_pct: float | None = None,
) -> float | None:
    """Structural quality of one setup, in the vendored engine's points scale.

    `net_edge_pct` is a first-class input, not a post-filter: plan.md §5 requires
    the ranked quantity to be net-expected-R, computed from effective entry and
    exit prices at the intended size. A setup whose edge the spread eats scores
    lower *as a setup*, which is the whole point.

    Returns None when the load-bearing measurements are missing — an unscored
    setup is UNKNOWN, and UNKNOWN never becomes a middling score.
    """
    if dip_sigma is None or rrs is None:
        return None
    points = BASE_POINTS
    # Deeper below anchored value is a better dip, up to a cap: past ~2.5σ the
    # question stops being "is it cheap" and becomes "is it broken".
    points += min(DIP_POINTS_CAP, max(0.0, -float(dip_sigma)) * DIP_POINTS_PER_SIGMA)
    # Relative strength is what separates a dip from a decline.
    points += max(-RRS_POINTS_CAP, min(RRS_POINTS_CAP, float(rrs) * RRS_POINTS_PER_UNIT))
    if participation is not None:
        # Participation at or above baseline is demand still present; below it,
        # the book is emptying and the dip is an artifact.
        points += max(
            -PARTICIPATION_POINTS_CAP,
            min(PARTICIPATION_POINTS_CAP, (float(participation) - 1.0) * 40.0),
        )
    if level_conviction:
        points += min(LEVEL_CONVICTION_POINTS, float(level_conviction) * 10.0)
    if net_edge_pct is not None:
        points += max(
            -NET_EDGE_POINTS_CAP,
            min(NET_EDGE_POINTS_CAP, float(net_edge_pct) * NET_EDGE_POINTS_PER_PCT),
        )
    return round(points, 2)


@dataclass(frozen=True, slots=True)
class SetupScore:
    quality_points: float | None
    expected_r: float | None
    rank_score: float | None
    prior_r: float | None
    blend_weight: float
    closed_samples: int
    evidence: str

    def as_dict(self) -> dict:
        return {
            "quality_points": self.quality_points,
            "expected_r": self.expected_r,
            "rank_score": self.rank_score,
            "prior_r": self.prior_r,
            "blend_weight": self.blend_weight,
            "closed_samples": self.closed_samples,
            "evidence": self.evidence,
        }


def score_candidate(
    *,
    dip_sigma: float | None,
    rrs: float | None,
    participation: float | None,
    level_conviction: float | None = None,
    net_edge_pct: float | None = None,
    realized_r: float | None = None,
    closed_samples: int = 0,
    days_since_signal: int = 0,
) -> SetupScore:
    """Expected R for one candidate, and an honest label for its evidence."""
    points = quality_points(
        dip_sigma=dip_sigma,
        rrs=rrs,
        participation=participation,
        level_conviction=level_conviction,
        net_edge_pct=net_edge_pct,
    )
    if points is None:
        return SetupScore(None, None, None, None, 0.0, closed_samples, "UNKNOWN")
    result = compute_expected_r(
        quality_points=points,
        realized_r=realized_r,
        closed_samples=closed_samples,
        days_since_signal=days_since_signal,
    )
    weight = float(result.get("blend_weight", 0.0) or 0.0)
    if closed_samples <= 0:
        evidence = "structural prior only — no closed trades have been recorded yet"
    elif weight < 0.5:
        evidence = (
            f"mostly structural — {closed_samples} closed trade(s) carry "
            f"{weight:.0%} of the estimate"
        )
    else:
        evidence = (
            f"tracker-led — {closed_samples} closed trade(s) carry {weight:.0%} of the estimate"
        )
    return SetupScore(
        quality_points=points,
        expected_r=float(result["expected_r"]),
        rank_score=float(result["rank_score"]),
        prior_r=float(result.get("prior_r", 0.0)),
        blend_weight=weight,
        closed_samples=closed_samples,
        evidence=evidence,
    )


def realized_from_ledger(records: list[dict]) -> tuple[float | None, int]:
    """Mean realized R and sample count from the paper ledger's closed trades.

    Closed trades with no stop price have no R, so they contribute to neither
    the mean nor the count — a trade whose risk was never defined cannot
    calibrate a risk-multiple.
    """
    values = [
        float(record["realized_r"])
        for record in records
        if record.get("event") == "close" and record.get("realized_r") is not None
    ]
    if not values:
        return None, 0
    return sum(values) / len(values), len(values)
