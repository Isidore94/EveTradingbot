"""The learning loop (plan.md §19 Part 4, Amendment 3).

What it does: reads the paper ledger — every open with its setup tag and its
"why I liked it" tags, every close with its realized R, and every recorded
pass with its "why I didn't like it" tags — and reports what the operator's
own decisions have actually been worth.

What it does **not** do, ever:

* silently edit a setup definition,
* change a frozen formula,
* promote, demote, or disable anything.

It correlates and reports. The operator promotes. This is not a stylistic
preference: a system that quietly retunes itself on 14 samples of its own
output is a system whose backtest means nothing, because the thing measured
is no longer the thing running.

Three properties keep the report honest:

* **UNKNOWN is first class.** A setup with 4 closed trades gets a state of
  UNKNOWN, not a win rate printed to two decimals. Small samples are reported
  as small samples; `MIN_SAMPLES_FOR_A_READ` is the line and it is stated.
* **Wilson lower bounds everywhere.** A 3-for-4 setup does not outrank a
  40-for-70 one. The point estimate is shown next to the bound, never
  instead of it.
* **Passes are measured with the same cost realism as trades.** A pass whose
  forward window has not elapsed is counted as pending, not as a win. A pass
  is scored
  "right" only if the trade avoided would have lost money **after** entry
  haircut, exit haircut and sales tax — the identical arithmetic
  `backtest.price_instances` applies, because a pass judged on gross price
  moves would flatter every pass in a market whose median spread is 98.8%.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .costs import CostModel
from .setups import UNVALIDATED, VALIDATED
from .timeutil import iso, parse_iso, utcnow
from .vendored.expected_r import blend_expected_r, freshness_factor, wilson_lower_bound

__all__ = [
    "MIN_SAMPLES_FOR_A_READ",
    "LearningReport",
    "PassRecord",
    "TagRecord",
    "SetupRecord",
    "build_learning_report",
    "render_learning",
]

# Below this many closed trades, a setup or a tag reports UNKNOWN rather than a
# number. It is the same line the §12.4 paper verdict draws, kept deliberately
# in step: two different "enough evidence" thresholds in one system would be
# two different systems.
MIN_SAMPLES_FOR_A_READ = 20

# How far forward a recorded pass is measured. The same horizons the backtest
# uses, so a pass and a trade are judged on the same clock.
PASS_HORIZONS = (5, 10, 20)

UNKNOWN = "UNKNOWN"


def _wilson(wins: int, samples: int, z: float = 1.28) -> float | None:
    if samples <= 0:
        return None
    return wilson_lower_bound(wins / samples, samples, z=z)


@dataclass(slots=True)
class SetupRecord:
    """One setup's measured record. Everything is None until it is known."""

    name: str
    notes: str = ""
    closed: int = 0
    open_now: int = 0
    wins: int = 0
    win_rate: float | None = None
    win_rate_lower: float | None = None
    average_r: float | None = None
    median_r: float | None = None
    average_net_pct: float | None = None
    expected_r: float | None = None
    prior_r: float | None = None
    blend_weight: float | None = None
    freshness: float | None = None
    days_since_last: float | None = None
    state: str = UNKNOWN
    validation: str = UNVALIDATED
    backtested: bool = False

    @property
    def known(self) -> bool:
        return self.state != UNKNOWN

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "notes": self.notes,
            "closed": self.closed,
            "open_now": self.open_now,
            "wins": self.wins,
            "win_rate": self.win_rate,
            "win_rate_lower": self.win_rate_lower,
            "average_r": self.average_r,
            "median_r": self.median_r,
            "average_net_pct": self.average_net_pct,
            "expected_r": self.expected_r,
            "prior_r": self.prior_r,
            "blend_weight": self.blend_weight,
            "freshness": self.freshness,
            "days_since_last": self.days_since_last,
            "state": self.state,
            "validation": self.validation,
        }


@dataclass(slots=True)
class TagRecord:
    """One "why I liked it" tag, measured across the trades that carried it."""

    tag: str
    label: str = ""
    closed: int = 0
    wins: int = 0
    win_rate: float | None = None
    win_rate_lower: float | None = None
    average_r: float | None = None
    state: str = UNKNOWN

    def as_dict(self) -> dict:
        return {
            "tag": self.tag,
            "label": self.label,
            "closed": self.closed,
            "wins": self.wins,
            "win_rate": self.win_rate,
            "win_rate_lower": self.win_rate_lower,
            "average_r": self.average_r,
            "state": self.state,
        }


@dataclass(slots=True)
class PassRecord:
    """One "why I passed" tag, measured forward — the regret half."""

    tag: str
    label: str = ""
    passes: int = 0
    measured: int = 0
    pending: int = 0
    right: int = 0
    right_rate: float | None = None
    right_rate_lower: float | None = None
    average_forgone_pct: float | None = None
    state: str = UNKNOWN

    def as_dict(self) -> dict:
        return {
            "tag": self.tag,
            "label": self.label,
            "passes": self.passes,
            "measured": self.measured,
            "pending": self.pending,
            "right": self.right,
            "right_rate": self.right_rate,
            "right_rate_lower": self.right_rate_lower,
            "average_forgone_pct": self.average_forgone_pct,
            "state": self.state,
        }


@dataclass(slots=True)
class LearningReport:
    generated_at: str
    closed_trades: int = 0
    recorded_passes: int = 0
    setups: list[SetupRecord] = field(default_factory=list)
    like_tags: list[TagRecord] = field(default_factory=list)
    dislike_tags: list[PassRecord] = field(default_factory=list)
    horizons: tuple[int, ...] = PASS_HORIZONS
    notes: list[str] = field(default_factory=list)

    @property
    def has_enough_for_a_digest_mention(self) -> bool:
        """The digest may name a top/bottom setup only past the threshold."""
        return self.closed_trades >= MIN_SAMPLES_FOR_A_READ

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "closed_trades": self.closed_trades,
            "recorded_passes": self.recorded_passes,
            "min_samples_for_a_read": MIN_SAMPLES_FOR_A_READ,
            "setups": [record.as_dict() for record in self.setups],
            "like_tags": [record.as_dict() for record in self.like_tags],
            "dislike_tags": [record.as_dict() for record in self.dislike_tags],
            "horizons_days": list(self.horizons),
            "notes": self.notes,
        }


def _closed_trades(ledger) -> list[dict]:
    """Every closed position, flattened with the open's tags attached."""
    rows: list[dict] = []
    for position in ledger.positions().values():
        close = position.get("close")
        if not close:
            continue
        rows.append(
            {
                "setup_tag": position.get("setup_tag") or "discretionary",
                "like_tags": list(position.get("like_tags") or []),
                "opened_at": position.get("at"),
                "closed_at": close.get("at"),
                "realized_r": close.get("realized_r"),
                "net_return_pct": close.get("net_return_pct"),
            }
        )
    return rows


def _days_since(stamp: str | None, now) -> float | None:
    parsed = parse_iso(stamp) if stamp else None
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 86400.0)


def _score(rows: list[dict]) -> tuple[int, int, float | None, float | None, float | None]:
    """(closed, wins, mean R, median R, mean net %) over rows with an R."""
    scored = [row for row in rows if row.get("realized_r") is not None]
    closed = len(rows)
    if not scored:
        nets = [row["net_return_pct"] for row in rows if row.get("net_return_pct") is not None]
        return (
            closed,
            sum(1 for value in nets if value > 0),
            None,
            None,
            (statistics.fmean(nets) if nets else None),
        )
    values = [float(row["realized_r"]) for row in scored]
    nets = [row["net_return_pct"] for row in rows if row.get("net_return_pct") is not None]
    return (
        closed,
        sum(1 for value in values if value > 0),
        statistics.fmean(values),
        statistics.median(values),
        statistics.fmean(nets) if nets else None,
    )


def measure_passes(
    passes: list[dict],
    bars: pd.DataFrame,
    *,
    haircuts: dict | None = None,
    sales_tax_pct: float = 0.0,
    horizon_days: int = 10,
    now=None,
) -> list[dict]:
    """Forward outcome of each recorded pass, on the backtest's cost terms.

    Returns one row per pass: `forgone_net_pct` is what the avoided trade
    would have returned **after** entry haircut, exit haircut and sales tax,
    or None when the window has not elapsed or the type has no measurable
    haircut. A pass is `right` when that number is not positive.

    Judging a pass on gross price moves would flatter every pass in a market
    whose median spread is 98.8% — and flatter it in the wrong direction,
    since the trades that look best gross are usually the widest.
    """
    now = now or utcnow()
    rows: list[dict] = []
    by_type = (
        {int(key): value.sort_values("datetime") for key, value in bars.groupby("type_id")}
        if bars is not None and not bars.empty
        else {}
    )
    for record in passes:
        type_id = int(record.get("type_id", 0))
        stamp = parse_iso(record.get("at"))
        row = {
            "type_id": type_id,
            "type_name": record.get("type_name"),
            "at": record.get("at"),
            "action": record.get("action"),
            "dislike_tags": list(record.get("dislike_tags") or []),
            "horizon_days": horizon_days,
            "forgone_net_pct": None,
            "right": None,
            "reason": None,
        }
        frame = by_type.get(type_id)
        if frame is None or stamp is None:
            row["reason"] = "no bars for this type"
            rows.append(row)
            continue
        after = frame[frame["datetime"] >= stamp]
        if len(after) <= horizon_days:
            row["reason"] = (
                f"only {max(0, len(after) - 1)} of {horizon_days} bars have elapsed since the pass"
            )
            rows.append(row)
            continue
        entry = float(after["close"].iloc[0])
        exit_close = float(after["close"].iloc[horizon_days])
        if not np.isfinite(entry) or entry <= 0 or not np.isfinite(exit_close):
            row["reason"] = "closes not measurable"
            rows.append(row)
            continue
        walk = (haircuts or {}).get(type_id) or {}
        tier = next(iter(walk.values()), None) if walk else None
        if tier is None or tier.get("entry") is None or tier.get("exit") is None:
            row["reason"] = (
                "no measurable haircut for this type, so the avoided trade cannot be "
                "priced; it is UNKNOWN, not free"
            )
            rows.append(row)
            continue
        entry_effective = entry * (1.0 + float(tier["entry"]))
        exit_effective = exit_close * (1.0 - float(tier["exit"])) * (1.0 - sales_tax_pct / 100.0)
        forgone = (exit_effective / entry_effective - 1.0) * 100.0
        row["forgone_net_pct"] = forgone
        row["right"] = forgone <= 0.0
        rows.append(row)
    return rows


def build_learning_report(
    config: Config,
    ledger,
    *,
    bars: pd.DataFrame | None = None,
    haircuts: dict | None = None,
    setups=None,
    vocabulary=None,
    backtested: set[str] | None = None,
    horizon_days: int = 10,
    now=None,
) -> LearningReport:
    """What is working, what is bleeding, and what there is not enough of."""
    now = now or utcnow()
    report = LearningReport(generated_at=iso(now))
    trades = _closed_trades(ledger)
    passes = ledger.passes() if hasattr(ledger, "passes") else []
    report.closed_trades = len(trades)
    report.recorded_passes = len(passes)
    open_counts: dict[str, int] = {}
    for position in ledger.positions().values():
        if not position.get("close"):
            tag = position.get("setup_tag") or "discretionary"
            open_counts[tag] = open_counts.get(tag, 0) + 1

    notes = {setup.name: setup.notes for setup in (setups or [])}
    tested = backtested or set()

    by_setup: dict[str, list[dict]] = {}
    for trade in trades:
        by_setup.setdefault(trade["setup_tag"], []).append(trade)
    for name in list(notes) + list(open_counts):
        by_setup.setdefault(name, [])

    for name, rows in by_setup.items():
        closed, wins, mean_r, median_r, mean_net = _score(rows)
        last = max((row["closed_at"] for row in rows if row.get("closed_at")), default=None)
        days = _days_since(last, now)
        record = SetupRecord(
            name=name,
            notes=notes.get(name, ""),
            closed=closed,
            open_now=open_counts.get(name, 0),
            wins=wins,
            average_r=mean_r,
            median_r=median_r,
            average_net_pct=mean_net,
            days_since_last=days,
            freshness=freshness_factor(days) if days is not None else None,
            backtested=name in tested,
            validation=VALIDATED
            if (name in tested or closed >= MIN_SAMPLES_FOR_A_READ)
            else UNVALIDATED,
        )
        if closed:
            record.win_rate = wins / closed
            record.win_rate_lower = _wilson(wins, closed)
        # The prior is 0R: an unproven setup is worth nothing until its own
        # outcomes say otherwise. Shrinkage pulls a small sample back toward it.
        blended = blend_expected_r(0.0, mean_r, closed)
        record.expected_r = blended["expected_r"]
        record.prior_r = blended["prior_r"]
        record.blend_weight = blended["blend_weight"]
        record.state = "MEASURED" if closed >= MIN_SAMPLES_FOR_A_READ else UNKNOWN
        report.setups.append(record)

    # Evidence-weighted: an UNKNOWN setup never outranks a measured one, and a
    # small sample is ranked on its lower bound, not its lucky point estimate.
    report.setups.sort(
        key=lambda record: (
            record.state == UNKNOWN,
            -(record.expected_r or 0.0),
            -(record.win_rate_lower or 0.0),
            record.name,
        )
    )

    by_tag: dict[str, list[dict]] = {}
    for trade in trades:
        for tag in trade["like_tags"]:
            by_tag.setdefault(tag, []).append(trade)
    for tag, rows in sorted(by_tag.items()):
        closed, wins, mean_r, _median, _net = _score(rows)
        record = TagRecord(
            tag=tag,
            label=vocabulary.label(tag) if vocabulary else tag,
            closed=closed,
            wins=wins,
            average_r=mean_r,
            state="MEASURED" if closed >= MIN_SAMPLES_FOR_A_READ else UNKNOWN,
        )
        if closed:
            record.win_rate = wins / closed
            record.win_rate_lower = _wilson(wins, closed)
        report.like_tags.append(record)

    measured = measure_passes(
        passes,
        bars if bars is not None else pd.DataFrame(),
        haircuts=haircuts,
        sales_tax_pct=CostModel.from_config(config).sales_tax_pct,
        horizon_days=horizon_days,
        now=now,
    )
    pass_by_tag: dict[str, list[dict]] = {}
    for row in measured:
        for tag in row["dislike_tags"]:
            pass_by_tag.setdefault(tag, []).append(row)
    for tag, rows in sorted(pass_by_tag.items()):
        scored = [row for row in rows if row["right"] is not None]
        right = sum(1 for row in scored if row["right"])
        forgone = [row["forgone_net_pct"] for row in scored]
        record = PassRecord(
            tag=tag,
            label=vocabulary.label(tag) if vocabulary else tag,
            passes=len(rows),
            measured=len(scored),
            pending=len(rows) - len(scored),
            right=right,
            average_forgone_pct=statistics.fmean(forgone) if forgone else None,
            state="MEASURED" if len(scored) >= MIN_SAMPLES_FOR_A_READ else UNKNOWN,
        )
        if scored:
            record.right_rate = right / len(scored)
            record.right_rate_lower = _wilson(right, len(scored))
        report.dislike_tags.append(record)

    if report.closed_trades < MIN_SAMPLES_FOR_A_READ:
        report.notes.append(
            f"{report.closed_trades} closed trade(s) — below the {MIN_SAMPLES_FOR_A_READ} "
            "needed for any read. Everything below is UNKNOWN, which is a statement "
            "about the sample, not about the setups."
        )
    if passes and not any(record.measured for record in report.dislike_tags):
        report.notes.append(
            "no recorded pass has a measurable forward outcome yet — either the window "
            "has not elapsed or the type has no measurable haircut. A pass that cannot "
            "be priced is UNKNOWN, never scored as a good call."
        )
    report.notes.append(
        "This report correlates and reports. It never edits a setup, changes a frozen "
        "formula, or promotes anything — the operator does that."
    )
    return report


def render_learning(report: LearningReport) -> str:
    """The LEARNING page, in text."""

    def pct(value):
        return f"{value * 100:,.0f}%" if value is not None else "—"

    def num(value, digits=2):
        return f"{value:+,.{digits}f}" if value is not None else "—"

    lines = [
        f"# What's working — {report.generated_at[:16]}Z",
        "",
        f"{report.closed_trades} closed trade(s), {report.recorded_passes} recorded pass(es). "
        f"A read needs {MIN_SAMPLES_FOR_A_READ}.",
        "",
        "## Setups, ranked by evidence-weighted expected net R",
        "",
        f"   {'setup':<34} {'n':>4} {'open':>5} {'win':>5} {'winLB':>6} {'avgR':>7} "
        f"{'expR':>7}  state",
    ]
    for record in report.setups:
        lines.append(
            f"   {record.name[:34]:<34} {record.closed:>4} {record.open_now:>5} "
            f"{pct(record.win_rate):>5} {pct(record.win_rate_lower):>6} "
            f"{num(record.average_r):>7} {num(record.expected_r):>7}  "
            f"{record.state} · {record.validation}"
        )
        if record.notes:
            lines.append(f"      {record.notes}")
    if not report.setups:
        lines.append("   (nothing tagged yet)")

    lines.extend(["", "## Why I liked it — do my reasons earn?", ""])
    if report.like_tags:
        lines.append(f"   {'tag':<28} {'n':>4} {'win':>5} {'winLB':>6} {'avgR':>7}  state")
        for record in report.like_tags:
            lines.append(
                f"   {record.label[:28]:<28} {record.closed:>4} {pct(record.win_rate):>5} "
                f"{pct(record.win_rate_lower):>6} {num(record.average_r):>7}  {record.state}"
            )
    else:
        lines.append("   (no closed trade carries a like tag yet)")

    lines.extend(
        [
            "",
            "## Why I passed — was I right? (regret tracking)",
            "",
            "Measured on the same horizons and the same cost realism as the backtest: "
            "a pass is 'right' when the avoided trade would not have made money net of "
            "both haircuts and sales tax.",
            "",
        ]
    )
    if report.dislike_tags:
        lines.append(
            f"   {'tag':<28} {'n':>4} {'meas':>5} {'pend':>5} {'right':>6} {'rightLB':>8} "
            f"{'avg fwd%':>9}  state"
        )
        for record in report.dislike_tags:
            forgone = (
                f"{record.average_forgone_pct:+,.2f}"
                if record.average_forgone_pct is not None
                else "—"
            )
            lines.append(
                f"   {record.label[:28]:<28} {record.passes:>4} {record.measured:>5} "
                f"{record.pending:>5} {pct(record.right_rate):>6} "
                f"{pct(record.right_rate_lower):>8} {forgone:>9}  {record.state}"
            )
    else:
        lines.append("   (no pass recorded yet — half the decision record is missing)")

    lines.append("")
    lines.extend(f"_{note}_" for note in report.notes)
    return "\n".join(lines)
