"""The viability report — plan.md §16.

One document that answers the operator's actual question from measurements:
census opportunity map, backtest verdict with sensitivities and stated
limitations, destruction lead-lag result, cross-region margin distribution
after freight, and the running paper-trading tally.

Two rules govern every line:

* **every number cites its source and its date** — a lake query, a sweep, a
  study — so a figure can always be traced back to what produced it;
* **a section whose inputs do not exist renders as UNKNOWN with the reason**,
  never as an empty table implying zero opportunity and never as an estimate.

Reading it and deciding whether EVE trading is worth his time is the
operator's decision. The system's job is to make that decision an informed one,
and to be honest enough that a negative answer is a possible output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .paths import atomic_write_text
from .timeutil import iso, utcnow

__all__ = ["ViabilityReport", "build_viability_report", "render_viability", "write_viability"]


@dataclass(slots=True)
class Section:
    title: str
    source: str
    generated: str | None
    body: list[str] = field(default_factory=list)
    unknown_reason: str | None = None

    def render(self) -> list[str]:
        lines = [f"## {self.title}", ""]
        if self.unknown_reason:
            lines.append(f"**UNKNOWN** — {self.unknown_reason}")
            lines.append("")
            lines.append(f"_source: {self.source}_")
            return lines
        lines.extend(self.body)
        lines.append("")
        lines.append(f"_source: {self.source}; generated {self.generated or 'UNKNOWN'}_")
        return lines


@dataclass(slots=True)
class ViabilityReport:
    generated_at: str
    sections: list[Section] = field(default_factory=list)
    headline: str = ""

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "headline": self.headline,
            "sections": [
                {
                    "title": section.title,
                    "source": section.source,
                    "generated": section.generated,
                    "unknown_reason": section.unknown_reason,
                    "body": section.body,
                }
                for section in self.sections
            ],
        }


def _load(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _latest(directory: Path, prefix: str) -> Path | None:
    if not directory.exists():
        return None
    candidates = sorted(directory.glob(f"{prefix}-*.json"))
    return candidates[-1] if candidates else None


def _census_section(payload: dict | None) -> Section:
    if not payload:
        return Section(
            "1. Census — the opportunity map",
            "data/reports/census-*.json (`python -m evescreener census`)",
            None,
            unknown_reason="no census has been run; the size of the universe is unmeasured, "
            "and every later 'the universe is N' claim would be a guess",
        )
    derived = payload.get("derived_floor") or {}
    turnover = payload.get("turnover_percentiles") or {}
    body = [
        f"- Types with a live order in the region: **{payload.get('active_types', 0):,}**",
        f"- Types with daily bars in the lake: **{payload.get('types_with_bars', 0):,}**",
        f"- Total bars stored: **{payload.get('total_bars', 0):,}**",
        "",
        "Median daily ISK turnover across measured types:",
        f"- p50 {turnover.get('p50', 0):,.0f} ISK · p90 {turnover.get('p90', 0):,.0f} ISK "
        f"· p99 {turnover.get('p99', 0):,.0f} ISK",
        "",
    ]
    if derived.get("resolved"):
        body.extend(
            [
                f"**Derived liquidity floor** (rule stated before measurement: "
                f"{derived.get('rule')}):",
                f"- median daily ISK turnover ≥ **{derived['min_median_isk_value']:,.0f}**",
                f"- median `order_count` ≥ **{derived['min_median_order_count']:,.0f}**",
                f"- admits **{derived['types']:,} types** carrying "
                f"{derived['share_of_turnover']:.1%} of median daily turnover",
                "",
                "**This is the denominator.** Every later claim about how many "
                "opportunities exist is a fraction of this number.",
            ]
        )
    else:
        body.append(f"**Floor UNRESOLVED**: {derived.get('reason', 'no reason recorded')}")
    ingest = payload.get("ingest") or {}
    if ingest:
        body.extend(
            [
                "",
                f"_Ingest: {ingest.get('fetched', 0):,} fetched, "
                f"{ingest.get('skipped_fresh', 0):,} skipped as still-fresh, "
                f"{ingest.get('no_history', 0):,} with no history in the region, "
                f"{ingest.get('failed', 0):,} failed._",
            ]
        )
    return Section(
        "1. Census — the opportunity map",
        "data/reports/census-*.json (`python -m evescreener census`)",
        payload.get("generated_at"),
        body,
    )


def _backtest_section(payload: dict | None) -> Section:
    if not payload:
        return Section(
            "2. Historical viability backtest",
            "data/reports/backtest-*.json (`python -m evescreener backtest`)",
            None,
            unknown_reason="no backtest has been run; whether the setup class has ever "
            "had positive net expectancy is unmeasured",
        )
    body: list[str] = []
    verdicts = payload.get("verdicts") or {}
    body.append(
        "**Hypothesis (frozen in plan.md §13.1 before measurement):** a type trading "
        "below its anchored value with demand intact produces positive net expectancy "
        "over 5–20 days after all EVE frictions at a real notional."
    )
    body.append("")
    for horizon, judgement in sorted(verdicts.items()):
        if not isinstance(judgement, dict):
            continue
        body.append(
            f"- **{horizon}-day horizon: {judgement.get('verdict', 'UNKNOWN')}** — "
            f"{judgement.get('reason', '')}"
        )
    body.extend(
        [
            "",
            f"- Setup instances found: **{payload.get('instances', 0):,}** across "
            f"**{payload.get('universe', 0):,}** types",
            f"- Sample period: {payload.get('sample_start')} → {payload.get('sample_end')}",
            f"- Types with a measurable live-book haircut: {payload.get('haircut_types', 0):,}",
        ]
    )
    cells = payload.get("cells") or []
    smallest = min((cell["notional_isk"] for cell in cells), default=None)
    sensitivity = [
        cell for cell in cells if smallest is not None and cell["notional_isk"] == smallest
    ]
    if sensitivity:
        body.extend(
            [
                "",
                "**Sensitivity at the smallest tier** (the tier that matters: a setup "
                "needing size to work is not one to start with):",
                "",
                "| horizon | haircut | n | Wilson LB | breakeven WR | expectancy % |",
                "|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for cell in sensitivity:

            def fmt(value):
                return "UNKNOWN" if value is None else f"{value:.3f}"

            body.append(
                f"| {cell['horizon_days']}d | {cell['haircut_multiple']:.0f}x "
                f"| {cell['samples']:,} | {fmt(cell['wilson_lb'])} "
                f"| {fmt(cell['breakeven_win_rate'])} | {fmt(cell['expectancy_pct'])} |"
            )
    limitations = payload.get("limitations") or []
    if limitations:
        body.extend(["", "**Limitations of this measurement, stated by the study itself:**", ""])
        for index, limitation in enumerate(limitations, start=1):
            body.append(f"{index}. {limitation}")
    return Section(
        "2. Historical viability backtest",
        "data/reports/backtest-*.json (`python -m evescreener backtest`)",
        payload.get("generated_at"),
        body,
    )


def _lead_lag_section(payload: dict | None) -> Section:
    if not payload:
        return Section(
            "3. Destruction lead-lag study",
            "data/reports/leadlag-*.json (`python -m evescreener killmails --study`)",
            None,
            unknown_reason="no lead-lag study has been run; whether destruction telemetry "
            "leads demand is unmeasured, so it may not influence any ranking",
        )
    outcome = payload.get("outcome") or {}
    body = [
        "**Hypothesis (frozen in plan.md §14.1 before measurement):** `destruction_z` "
        "leads `order_count`/`volume` upticks and price firming in doctrine-class hulls "
        "and fitted modules by 1–5 days.",
        "",
        f"**{outcome.get('outcome', 'UNKNOWN')}** — {outcome.get('reason', '')}",
        "",
        outcome.get("consequence", ""),
        "",
        f"- Observations: {payload.get('observations', 0):,} across "
        f"{payload.get('types', 0):,} types",
        f"- Sample period: {payload.get('sample_start')} → {payload.get('sample_end')}",
    ]
    return Section(
        "3. Destruction lead-lag study",
        "data/reports/leadlag-*.json (`python -m evescreener killmails --study`)",
        payload.get("generated_at"),
        body,
    )


def _cross_region_section(payload: dict | None) -> Section:
    if not payload:
        return Section(
            "4. Cross-region margins after freight",
            "data/reports/crossregion-*.json (`python -m evescreener cross-region`)",
            None,
            unknown_reason="no cross-region scan has been run; hub-to-hub margins after "
            "real freight are unmeasured",
        )
    rows = payload.get("rows") or []
    body = [
        f"- Hub pairs considered: {payload.get('pairs_considered', 0):,}",
        f"- Dropped for want of a freight quote: {payload.get('dropped_no_freight', 0):,} "
        "(no quote, no row — a margin that has not paid for its hauling is not a margin)",
        f"- Dropped, no depth at the notional: {payload.get('dropped_no_depth', 0):,}",
        f"- Dropped, negative after costs: {payload.get('dropped_negative', 0):,}",
        "",
    ]
    if not rows:
        body.append("**No route cleared costs in this scan.** That is a valid, expected result.")
    else:
        margins = [row["net_pct"] for row in rows]
        body.extend(
            [
                f"- Routes clearing costs: **{len(rows)}**",
                f"- Net margin after freight and tax: best {max(margins):.2f}%, "
                f"median {sorted(margins)[len(margins) // 2]:.2f}%, worst {min(margins):.2f}%",
            ]
        )
    return Section(
        "4. Cross-region margins after freight",
        "data/reports/crossregion-*.json (`python -m evescreener cross-region`)",
        payload.get("generated_at"),
        body,
    )


def _paper_section(payload: dict | None) -> Section:
    if not payload:
        return Section(
            "5. Paper trading — the running experiment",
            "data/streams/paper.jsonl (`python -m evescreener paper report`)",
            None,
            unknown_reason="the paper ledger is empty; the experiment has not started",
        )
    verdict = payload.get("verdict") or {}
    body = [
        f"- Decisions refused rather than priced: **{payload.get('refused', 0)}**",
        f"- Closed trades: **{payload.get('closed_count', 0)}**",
        f"- Cumulative net P&L: **{payload.get('cumulative_net_isk', 0.0):,.0f} ISK**",
    ]
    if payload.get("win_rate") is not None:
        body.append(
            f"- Win rate {payload['win_rate']:.1%} "
            f"(Wilson LB {payload.get('wilson_lb', 0):.3f} vs breakeven "
            f"{payload.get('breakeven_win_rate', 0):.3f})"
        )
    accuracy = payload.get("fill_accuracy") or {}
    if accuracy:
        body.append(
            f"- Predicted vs actual fills: {accuracy.get('within_tolerance', 0)}/"
            f"{accuracy.get('samples', 0)} within "
            f"±{accuracy.get('tolerance_pct_of_notional', 0)}% of notional"
        )
    body.extend(
        [
            "",
            f"**Verdict: {verdict.get('verdict', 'UNKNOWN')}** — {verdict.get('detail', '')}",
            "",
            f"_Rule: {verdict.get('rule', '')}_",
        ]
    )
    return Section(
        "5. Paper trading — the running experiment",
        "data/streams/paper.jsonl (`python -m evescreener paper report`)",
        payload.get("generated_at"),
        body,
    )


def _headline(census, backtest, lead_lag, paper) -> str:
    """One paragraph that refuses to overstate what has been measured."""
    if not census:
        return (
            "**Not enough has been measured to answer the question.** No census exists, "
            "so the size of the opportunity is unknown."
        )
    paper_verdict = ((paper or {}).get("verdict") or {}).get("verdict")
    if paper_verdict in {"FALSIFIED", "PROVISIONALLY_CONFIRMED"}:
        return (
            f"**The paper experiment has reached a read: {paper_verdict}.** That is the "
            "only evidence here from actually pricing decisions at a real size against "
            "live books; everything above it is context for it."
        )
    verdicts = {
        horizon: judgement.get("verdict")
        for horizon, judgement in ((backtest or {}).get("verdicts") or {}).items()
        if isinstance(judgement, dict)
    }
    if not verdicts:
        return (
            "**The question is not yet answered.** The universe has been measured, but "
            "no backtest has run, so whether the setup class has ever paid is unknown."
        )
    edge = ""
    if ((lead_lag or {}).get("outcome") or {}).get("outcome") == "SURVIVES":
        edge = (
            " The destruction lead-lag effect also survived its own frozen pass rule, "
            "which is the one edge here that equity systems cannot have."
        )
    if "PLAUSIBLE" in verdicts.values():
        horizons = [key for key, value in verdicts.items() if value == "PLAUSIBLE"]
        return (
            f"**The setup class is PLAUSIBLE at the {', '.join(horizons)}-day horizon(s)** "
            "on history, at 2x the measured slippage haircut. Plausible is not proven: "
            "the backtest has no historical order books, and the only way to find out "
            "whether it pays *for you* is the paper experiment, which is "
            f"{paper_verdict or 'not yet started'}." + edge
        )
    if set(verdicts.values()) == {"UNKNOWN"}:
        return (
            "**The question is not yet answered.** Every horizon returned UNKNOWN — the "
            "sample was too small to judge, which is not the same as a negative result."
        )
    return (
        "**The setup class did not clear its own pre-stated bar on history.** The verdict "
        "rule was frozen before the measurement and the measurement did not meet it. That "
        "is a real answer, and it argues against spending more time here — not for "
        "loosening the rule."
    )


def build_viability_report(
    config: Config,
    *,
    census: dict | None = None,
    backtest: dict | None = None,
    lead_lag: dict | None = None,
    cross_region: dict | None = None,
    paper: dict | None = None,
    reports_dir: Path | None = None,
) -> ViabilityReport:
    """Assemble the report from whatever has actually been measured."""
    directory = reports_dir or config.paths.reports
    census = census if census is not None else _load(_latest(directory, "census"))
    backtest = backtest if backtest is not None else _load(_latest(directory, "backtest"))
    lead_lag = lead_lag if lead_lag is not None else _load(_latest(directory, "leadlag"))
    cross_region = (
        cross_region if cross_region is not None else _load(_latest(directory, "crossregion"))
    )
    report = ViabilityReport(generated_at=iso(utcnow()))
    report.headline = _headline(census, backtest, lead_lag, paper)
    report.sections = [
        _census_section(census),
        _backtest_section(backtest),
        _lead_lag_section(lead_lag),
        _cross_region_section(cross_region),
        _paper_section(paper),
    ]
    return report


def render_viability(report: ViabilityReport) -> str:
    lines = [
        "# Is EVE market swing trading worth my time?",
        "",
        f"Generated {report.generated_at}. Every number below cites the artifact that",
        "produced it and the date it was produced. A section whose inputs do not exist",
        "says UNKNOWN and why — never an empty table implying zero, never an estimate.",
        "",
        "---",
        "",
        "## Headline",
        "",
        report.headline,
        "",
        "---",
        "",
    ]
    for section in report.sections:
        lines.extend(section.render())
        lines.append("")
        lines.append("---")
        lines.append("")
    lines.extend(
        [
            "## What this document is not",
            "",
            "It is not a recommendation. Reading it and deciding whether EVE trading is",
            "worth your time is your decision; the system's job is to make that decision",
            "an informed one, and to be honest enough that a negative answer is a possible",
            "output of it.",
            "",
            "Nothing in this system places, automates, or assists in placing an order, and",
            "nothing automates the EVE client (plan.md §10).",
        ]
    )
    return "\n".join(lines) + "\n"


def write_viability(config: Config, report: ViabilityReport) -> tuple[str, str]:
    paths = config.paths.ensure()
    stem = f"viability-{report.generated_at[:10]}"
    json_path = paths.reports / f"{stem}.json"
    md_path = paths.reports / f"{stem}.md"
    atomic_write_text(json_path, json.dumps(report.as_dict(), indent=2, sort_keys=True))
    atomic_write_text(md_path, render_viability(report))
    return str(json_path), str(md_path)
