"""Historical viability backtest — plan.md §13, hypothesis and verdict frozen.

This study exists to answer one question before the operator spends time on
the system: **does the setup class have positive net expectancy in EVE, after
all of EVE's frictions, at a size he can actually trade?**

Everything here is bound by §13, which was written before the study ran:

* §13.2 defines the setup mechanically (`signals/setup.py` owns the code, so
  the screen and the backtest cannot drift apart);
* §13.4 states the hard limitation — **there are no historical order books**,
  so fills are close-to-close with a slippage haircut *measured from live book
  sweeps per type*, and a type whose current book cannot fill a tier is
  excluded from that tier with its exclusion counted;
* §13.6 is the verdict rule, and this module implements it literally;
* §13.7 is the limitations list, and `render_backtest` prints it into the
  report body — a backtest that hides its weaknesses is worthless.

Momentum is out of scope even as a study (operator directive 2026-08-20).
There is no code path here that could discover a continuation setup, and none
may be added.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .costs import CostModel
from .paths import atomic_write_text
from .signals.setup import SetupParams, evaluate_setups, gate_summary
from .store.db import Database
from .timeutil import iso, utcnow

__all__ = [
    "BacktestResult",
    "HorizonStats",
    "measure_haircuts",
    "render_backtest",
    "run_backtest",
    "verdict",
    "wilson_lower_bound",
    "write_backtest",
]

# §13.6: the verdict is read at the smallest tier and needs this many samples.
MIN_SAMPLES_FOR_VERDICT = 100
VERDICT_HAIRCUT_MULTIPLE = 2.0


def wilson_lower_bound(wins: int, samples: int, z: float = 1.96) -> float | None:
    """Wilson one-sided lower bound of a win rate. None when there is no sample.

    Same statistic the vendored `expected_r.wilson_lower_bound` uses; restated
    here in wins/samples terms so the backtest never has to invent a point
    estimate to feed it.
    """
    if samples <= 0:
        return None
    n = float(samples)
    p = min(max(wins / n, 0.0), 1.0)
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return max(0.0, (centre - margin) / denominator)


def breakeven_win_rate(returns: np.ndarray) -> float | None:
    """The win rate the observed payoff ratio requires: |L| / (W + |L|).

    The degenerate cases take their limits rather than returning None: a sample
    with no losses needs a win rate of 0 to break even, and one with no wins
    needs 1. Small-sample skepticism is carried by the Wilson lower bound on
    the win rate, which is where it belongs — not by refusing to state the
    payoff ratio.
    """
    if returns.size == 0:
        return None
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    if losses.size == 0:
        return 0.0
    if wins.size == 0:
        return 1.0
    mean_win = float(wins.mean())
    mean_loss = abs(float(losses.mean()))
    if mean_win + mean_loss <= 0:
        return None
    return mean_loss / (mean_win + mean_loss)


def max_drawdown(returns: np.ndarray) -> float | None:
    """Max drawdown of the equity curve formed by compounding each trade."""
    if returns.size == 0:
        return None
    equity = np.cumprod(1.0 + returns / 100.0)
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    return float(drawdown.min() * 100.0)


def measure_haircuts(
    book_frame: pd.DataFrame, tiers: tuple[float, ...]
) -> dict[int, dict[float, dict[str, float]]]:
    """Per-type round-trip slippage, measured from a live sweep (§13.4).

    Returns `{type_id: {tier: {"entry": pct, "exit": pct, "round_trip": pct}}}`
    as *fractions*, not percents. A type/tier the current book cannot fill is
    simply absent — the caller must treat absence as `haircut_unknown` and
    exclude it, never as zero slippage.
    """
    haircuts: dict[int, dict[float, dict[str, float]]] = {}
    if book_frame is None or book_frame.empty:
        return haircuts
    sells = book_frame[book_frame["side"] == "sell"].set_index("type_id")
    buys = book_frame[book_frame["side"] == "buy"].set_index("type_id")
    common = sells.index.intersection(buys.index)
    for type_id in common:
        sell = sells.loc[type_id]
        buy = buys.loc[type_id]
        if isinstance(sell, pd.DataFrame):
            sell = sell.iloc[0]
        if isinstance(buy, pd.DataFrame):
            buy = buy.iloc[0]
        best_ask = float(sell.get("best_price") or 0.0)
        best_bid = float(buy.get("best_price") or 0.0)
        if best_ask <= 0 or best_bid <= 0:
            continue
        mid = (best_ask + best_bid) / 2.0
        per_tier: dict[float, dict[str, float]] = {}
        for index, tier in enumerate(tiers):
            ask_fill = sell.get(f"depth_fill_price_{index}")
            bid_fill = buy.get(f"depth_fill_price_{index}")
            if (
                not ask_fill
                or not bid_fill
                or not np.isfinite(ask_fill)
                or not np.isfinite(bid_fill)
            ):
                continue
            entry = float(ask_fill) / mid - 1.0
            exit_cost = 1.0 - float(bid_fill) / mid
            if entry < 0 or exit_cost < 0:
                # An inverted book is a data-quality event, not a free lunch.
                continue
            per_tier[float(tier)] = {
                "entry": entry,
                "exit": exit_cost,
                "round_trip": entry + exit_cost,
            }
        if per_tier:
            haircuts[int(type_id)] = per_tier
    return haircuts


@dataclass(slots=True)
class HorizonStats:
    """One (horizon, tier, haircut multiple) cell of the result table."""

    horizon_days: int
    notional_isk: float
    haircut_multiple: float
    samples: int
    wins: int
    win_rate: float | None
    wilson_lb: float | None
    breakeven_win_rate: float | None
    expectancy_pct: float | None
    median_pct: float | None
    max_drawdown_pct: float | None
    first_half_wilson_lb: float | None = None
    first_half_breakeven: float | None = None
    second_half_wilson_lb: float | None = None
    second_half_breakeven: float | None = None

    def as_dict(self) -> dict:
        return {
            "horizon_days": self.horizon_days,
            "notional_isk": self.notional_isk,
            "haircut_multiple": self.haircut_multiple,
            "samples": self.samples,
            "wins": self.wins,
            "win_rate": self.win_rate,
            "wilson_lb": self.wilson_lb,
            "breakeven_win_rate": self.breakeven_win_rate,
            "expectancy_pct": self.expectancy_pct,
            "median_pct": self.median_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "first_half_wilson_lb": self.first_half_wilson_lb,
            "first_half_breakeven": self.first_half_breakeven,
            "second_half_wilson_lb": self.second_half_wilson_lb,
            "second_half_breakeven": self.second_half_breakeven,
        }


def _stats(
    instances: pd.DataFrame,
    *,
    horizon: int,
    tier: float,
    multiple: float,
    wilson_z: float,
) -> HorizonStats:
    returns = instances["net_return_pct"].to_numpy(dtype="float64")
    samples = int(returns.size)
    if samples == 0:
        return HorizonStats(horizon, tier, multiple, 0, 0, None, None, None, None, None, None)
    wins = int((returns > 0).sum())
    ordered = instances.sort_values("datetime")
    half = len(ordered) // 2
    first = ordered.iloc[:half]["net_return_pct"].to_numpy(dtype="float64")
    second = ordered.iloc[half:]["net_return_pct"].to_numpy(dtype="float64")
    return HorizonStats(
        horizon_days=horizon,
        notional_isk=tier,
        haircut_multiple=multiple,
        samples=samples,
        wins=wins,
        win_rate=wins / samples,
        wilson_lb=wilson_lower_bound(wins, samples, wilson_z),
        breakeven_win_rate=breakeven_win_rate(returns),
        expectancy_pct=float(returns.mean()),
        median_pct=float(np.median(returns)),
        max_drawdown_pct=max_drawdown(ordered["net_return_pct"].to_numpy(dtype="float64")),
        first_half_wilson_lb=wilson_lower_bound(int((first > 0).sum()), first.size, wilson_z)
        if first.size
        else None,
        first_half_breakeven=breakeven_win_rate(first) if first.size else None,
        second_half_wilson_lb=wilson_lower_bound(int((second > 0).sum()), second.size, wilson_z)
        if second.size
        else None,
        second_half_breakeven=breakeven_win_rate(second) if second.size else None,
    )


def verdict(stats: HorizonStats | None) -> dict:
    """The FROZEN §13.6 rule, applied literally. No interpretation.

    PLAUSIBLE requires all four conditions; UNKNOWN (too few samples) is not a
    pass and never rounds up to one.
    """
    rule = (
        "PLAUSIBLE iff n >= 100 at the horizon, expectancy > 0 at 2x the measured "
        "haircut, Wilson 95% LB win rate > breakeven win rate on the full sample, "
        "and that same condition holds independently in both halves of the period "
        "(plan.md §13.6, frozen 2026-08-20 before measurement)"
    )
    if stats is None or stats.samples == 0:
        return {"rule": rule, "verdict": "UNKNOWN", "reason": "no instances at this cell"}
    if stats.samples < MIN_SAMPLES_FOR_VERDICT:
        return {
            "rule": rule,
            "verdict": "UNKNOWN",
            "reason": f"n={stats.samples} < {MIN_SAMPLES_FOR_VERDICT}; "
            "insufficient sample is not a pass",
            "samples": stats.samples,
        }
    failures: list[str] = []
    if stats.expectancy_pct is None or stats.expectancy_pct <= 0:
        failures.append(
            f"expectancy at 2x haircut is {stats.expectancy_pct:.3f}% (needs > 0)"
            if stats.expectancy_pct is not None
            else "expectancy unmeasurable"
        )
    if stats.wilson_lb is None or stats.breakeven_win_rate is None:
        failures.append("win rate or breakeven win rate unmeasurable")
    elif stats.wilson_lb <= stats.breakeven_win_rate:
        failures.append(
            f"Wilson LB {stats.wilson_lb:.3f} <= breakeven {stats.breakeven_win_rate:.3f}"
        )
    for label, lb, breakeven in (
        ("first half", stats.first_half_wilson_lb, stats.first_half_breakeven),
        ("second half", stats.second_half_wilson_lb, stats.second_half_breakeven),
    ):
        if lb is None or breakeven is None:
            failures.append(f"{label}: unmeasurable")
        elif lb <= breakeven:
            failures.append(f"{label}: Wilson LB {lb:.3f} <= breakeven {breakeven:.3f}")
    if failures:
        return {
            "rule": rule,
            "verdict": "NOT PLAUSIBLE",
            "reason": "; ".join(failures),
            "samples": stats.samples,
        }
    return {
        "rule": rule,
        "verdict": "PLAUSIBLE",
        "reason": (
            f"n={stats.samples}, expectancy {stats.expectancy_pct:.3f}% at 2x haircut, "
            f"Wilson LB {stats.wilson_lb:.3f} > breakeven {stats.breakeven_win_rate:.3f}, "
            "consistent across both halves"
        ),
        "samples": stats.samples,
    }


LIMITATIONS = (
    "No historical order books exist. Fills are close-to-close with a slippage "
    "haircut measured from TODAY's book per type; a type whose liquidity has "
    "changed since the sample period is mispriced by exactly that change, in an "
    "unknowable direction.",
    "Close-to-close fills. ESI `average` is a whole-day mean, so the study can "
    "neither buy the low nor sell the high — and equally cannot be hurt by "
    "intraday adversity. This cuts both ways; it is not conservative by default.",
    "~13.5-month window. One year is one meta. A patch cycle, a war, or a single "
    "industry rebalance can dominate the result.",
    "Survivorship. The universe comes from types with live orders TODAY. Types "
    "that died during the sample period are absent, and their absence is "
    "invisible to the win rate.",
    "No portfolio constraint. Expectancy is per instance, not per ISK-day of a "
    "real book with finite capital and escrow.",
    "The haircut is measured, not the spread actually paid. It assumes the "
    "operator crosses the spread exactly as the depth walk describes, at one "
    "moment in time.",
)


@dataclass(slots=True)
class BacktestResult:
    generated_at: str
    region_id: int
    params: dict = field(default_factory=dict)
    universe: int = 0
    types_evaluated: int = 0
    instances: int = 0
    haircut_types: int = 0
    excluded_haircut_unknown: dict = field(default_factory=dict)
    sample_start: str | None = None
    sample_end: str | None = None
    cells: list[dict] = field(default_factory=list)
    cohorts: list[dict] = field(default_factory=list)
    verdicts: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "region_id": self.region_id,
            "params": self.params,
            "universe": self.universe,
            "types_evaluated": self.types_evaluated,
            "instances": self.instances,
            "haircut_types": self.haircut_types,
            "excluded_haircut_unknown": self.excluded_haircut_unknown,
            "sample_start": self.sample_start,
            "sample_end": self.sample_end,
            "cells": self.cells,
            "cohorts": self.cohorts,
            "verdicts": self.verdicts,
            "gates": self.gates,
            "limitations": list(LIMITATIONS),
            "notes": self.notes,
        }


def _cohort_label(db: Database | None, type_id: int) -> str:
    if db is None:
        return "(all)"
    row = db.type_by_id(type_id)
    if row is None or row["market_group_id"] is None:
        return "(no market group)"
    chain = db.market_group_chain(int(row["market_group_id"]))
    if not chain:
        return "(no market group)"
    group = db.conn.execute(
        "SELECT name FROM sde_market_groups WHERE market_group_id=?", (chain[-1],)
    ).fetchone()
    return group["name"] if group else "(unknown group)"


def find_instances(
    bars: pd.DataFrame,
    composite: pd.DataFrame,
    params: SetupParams,
    horizons: tuple[int, ...],
    *,
    anchor_dates=(),
    db: Database | None = None,
    progress=None,
) -> tuple[pd.DataFrame, dict]:
    """Every historical instance of the setup, with its forward closes attached.

    One row per `(type_id, entry date, horizon)` carrying the raw forward
    close. Costs are applied later so the same instance set can be repriced at
    every tier and haircut multiple without re-scanning the lake.
    """
    records: list[dict] = []
    gates: dict[str, int] = {}
    evaluated = 0
    type_ids = sorted(bars["type_id"].unique())
    for position, type_id in enumerate(type_ids, start=1):
        frame = bars[bars["type_id"] == type_id].sort_values("datetime").reset_index(drop=True)
        if len(frame) < params.min_bars:
            continue
        evaluated += 1
        result = evaluate_setups(frame, composite, params, anchor_dates=anchor_dates)
        if result.empty:
            continue
        for key, value in gate_summary(result).items():
            gates[key] = gates.get(key, 0) + value
        hits = np.flatnonzero(result["is_setup"].to_numpy())
        if hits.size == 0:
            continue
        closes = result["close"].to_numpy(dtype="float64")
        stamps = result["datetime"].to_numpy()
        cohort = _cohort_label(db, int(type_id))
        for index in hits:
            for horizon in horizons:
                target = index + horizon
                if target >= closes.size:
                    continue
                entry_close = closes[index]
                exit_close = closes[target]
                if not np.isfinite(entry_close) or not np.isfinite(exit_close) or entry_close <= 0:
                    continue
                records.append(
                    {
                        "type_id": int(type_id),
                        "cohort": cohort,
                        "datetime": stamps[index],
                        "horizon_days": int(horizon),
                        "entry_close": float(entry_close),
                        "exit_close": float(exit_close),
                        "dip_sigma": float(result["dip_sigma"].iloc[index]),
                        "rrs": float(result["rrs"].iloc[index]),
                        "participation": float(result["participation"].iloc[index]),
                    }
                )
        if progress is not None and position % 250 == 0:
            progress(position, len(type_ids), len(records))
    return pd.DataFrame(records), gates


def price_instances(
    instances: pd.DataFrame,
    haircuts: dict[int, dict[float, dict[str, float]]],
    *,
    tier: float,
    multiple: float,
    sales_tax_pct: float,
) -> tuple[pd.DataFrame, int]:
    """Apply full costs at one tier and haircut multiple (§13.4's arithmetic).

    Returns `(priced, excluded)` where `excluded` counts instances whose type
    has no measurable haircut at this tier — those are dropped, never priced
    at zero slippage.
    """
    if instances.empty:
        return instances.assign(net_return_pct=pd.Series(dtype="float64")), 0
    entry_haircut = instances["type_id"].map(
        lambda value: haircuts.get(int(value), {}).get(float(tier), {}).get("entry")
    )
    exit_haircut = instances["type_id"].map(
        lambda value: haircuts.get(int(value), {}).get(float(tier), {}).get("exit")
    )
    usable = entry_haircut.notna() & exit_haircut.notna()
    excluded = int((~usable).sum())
    priced = instances[usable].copy()
    if priced.empty:
        return priced.assign(net_return_pct=pd.Series(dtype="float64")), excluded
    entry_effective = priced["entry_close"] * (
        1.0 + entry_haircut[usable].to_numpy(dtype="float64") * multiple
    )
    exit_effective = (
        priced["exit_close"]
        * (1.0 - exit_haircut[usable].to_numpy(dtype="float64") * multiple)
        * (1.0 - sales_tax_pct / 100.0)
    )
    priced["entry_effective"] = entry_effective
    priced["exit_effective"] = exit_effective
    priced["net_return_pct"] = (exit_effective / entry_effective - 1.0) * 100.0
    return priced, excluded


def run_backtest(
    config: Config,
    bars: pd.DataFrame,
    composite: pd.DataFrame,
    book_frame: pd.DataFrame,
    *,
    db: Database | None = None,
    region_id: int | None = None,
    anchor_dates=(),
    progress=None,
) -> BacktestResult:
    """The whole study: find instances, price them, score them, judge them."""
    settings = config.backtest
    params = SetupParams(
        entry_band_sigma=settings.entry_band_sigma,
        min_rrs=settings.min_rrs,
        participation_floor=settings.participation_floor,
        min_bars=settings.min_bars,
        anchor_lookback_days=settings.anchor_lookback_days,
        rrs_length=config.signals.rrs_length,
        atr_length=config.signals.atr_length,
        participation_window=config.screen.participation_window,
        atr_winsor_k=config.signals.atr_winsor_k,
        atr_winsor_window=config.signals.atr_winsor_window,
    )
    costs = CostModel.from_config(config)
    tiers = tuple(float(value) for value in config.costs.notional_tiers_isk)
    haircuts = measure_haircuts(book_frame, tiers)

    result = BacktestResult(
        generated_at=iso(utcnow()),
        region_id=region_id or config.esi.home_region_id,
        params={
            "entry_band_sigma": params.entry_band_sigma,
            "min_rrs": params.min_rrs,
            "participation_floor": params.participation_floor,
            "min_bars": params.min_bars,
            "anchor_lookback_days": params.anchor_lookback_days,
            "horizons_days": list(settings.horizons_days),
            "haircut_multipliers": list(settings.haircut_multipliers),
            "wilson_z": settings.wilson_z,
            "sales_tax_pct": costs.sales_tax_pct,
            "notional_tiers_isk": list(tiers),
        },
        universe=int(bars["type_id"].nunique()) if not bars.empty else 0,
        haircut_types=len(haircuts),
    )
    if bars.empty:
        result.notes.append("no bars in the lake; nothing to measure")
        result.verdicts = {"note": "UNKNOWN — the lake is empty"}
        return result
    if not haircuts:
        result.notes.append(
            "no live book sweep available, so no slippage haircut could be measured; "
            "every instance is haircut_unknown and the study reports UNKNOWN rather "
            "than pricing history at zero slippage"
        )

    instances, gates = find_instances(
        bars,
        composite,
        params,
        tuple(int(value) for value in settings.horizons_days),
        anchor_dates=anchor_dates,
        db=db,
        progress=progress,
    )
    result.gates = gates
    result.types_evaluated = int(gates.get("bars", 0) and len(bars["type_id"].unique()))
    result.instances = int(len(instances))
    if instances.empty:
        result.notes.append("no historical instances of the setup were found")
        result.verdicts = {"note": "UNKNOWN — no instances"}
        return result
    stamps = pd.to_datetime(instances["datetime"], utc=True)
    result.sample_start = iso(stamps.min().to_pydatetime())
    result.sample_end = iso(stamps.max().to_pydatetime())

    excluded: dict[str, int] = {}
    cells: list[dict] = []
    cohorts: list[dict] = []
    for horizon in settings.horizons_days:
        subset = instances[instances["horizon_days"] == int(horizon)]
        for tier in tiers:
            for multiple in settings.haircut_multipliers:
                priced, dropped = price_instances(
                    subset,
                    haircuts,
                    tier=tier,
                    multiple=float(multiple),
                    sales_tax_pct=costs.sales_tax_pct,
                )
                excluded[f"h{horizon}_t{int(tier)}"] = dropped
                stats = _stats(
                    priced,
                    horizon=int(horizon),
                    tier=float(tier),
                    multiple=float(multiple),
                    wilson_z=settings.wilson_z,
                )
                cells.append(stats.as_dict())
                if float(multiple) == VERDICT_HAIRCUT_MULTIPLE and tier == tiers[0]:
                    for cohort, group in priced.groupby("cohort"):
                        cohort_stats = _stats(
                            group,
                            horizon=int(horizon),
                            tier=float(tier),
                            multiple=float(multiple),
                            wilson_z=settings.wilson_z,
                        )
                        cohorts.append({"cohort": str(cohort), **cohort_stats.as_dict()})
    result.cells = cells
    result.cohorts = cohorts
    result.excluded_haircut_unknown = excluded

    # §13.6: the verdict is read at the SMALLEST tier, at 2x the haircut.
    verdicts: dict = {}
    for horizon in settings.horizons_days:
        match = [
            cell
            for cell in cells
            if cell["horizon_days"] == int(horizon)
            and cell["notional_isk"] == tiers[0]
            and cell["haircut_multiple"] == VERDICT_HAIRCUT_MULTIPLE
        ]
        stats = HorizonStats(**match[0]) if match else None
        verdicts[str(horizon)] = verdict(stats)
    result.verdicts = verdicts
    return result


def render_backtest(result: BacktestResult) -> str:
    """The report, with its own limitations printed in its own body (§13.7)."""
    lines = [
        f"# Historical viability backtest — region {result.region_id}",
        "",
        f"Generated {result.generated_at}.",
        "",
        "**Hypothesis (frozen in plan.md §13.1 before this study ran):** in The",
        "Forge, a type trading below its anchored value while its demand is still",
        "intact produces a positive net expectancy over 5–20 trading days, after",
        "all EVE frictions at a real notional.",
        "",
        "## Sample",
        "",
        f"- Types in the lake: **{result.universe}**",
        f"- Setup instances found: **{result.instances}**",
        f"- Types with a measurable live-book haircut: **{result.haircut_types}**",
        f"- Sample period: {result.sample_start} → {result.sample_end}",
    ]
    if result.gates:
        lines.append("")
        lines.append("### Gate pass/UNKNOWN counts")
        lines.append("")
        lines.append("| gate | passed | UNKNOWN |")
        lines.append("|---|---:|---:|")
        for gate in (
            "below_anchored_value",
            "relative_strength_intact",
            "participation_intact",
            "measurable",
        ):
            lines.append(
                f"| {gate} | {result.gates.get(f'{gate}_pass', 0):,} "
                f"| {result.gates.get(f'{gate}_unknown', 0):,} |"
            )
    lines.append("")
    lines.append("## Verdict (plan.md §13.6, frozen before measurement)")
    lines.append("")
    for horizon, judgement in sorted(result.verdicts.items()):
        if not isinstance(judgement, dict) or "verdict" not in judgement:
            lines.append(f"- **{horizon}**: {judgement}")
            continue
        lines.append(f"### {horizon}-day horizon: **{judgement['verdict']}**")
        lines.append("")
        lines.append(f"{judgement.get('reason', '')}")
        lines.append("")
    if result.verdicts:
        first = next(iter(result.verdicts.values()))
        if isinstance(first, dict) and "rule" in first:
            lines.append(f"> Rule applied: {first['rule']}")
            lines.append("")
    if result.cells:
        lines.append("## Results by horizon, tier and haircut sensitivity")
        lines.append("")
        lines.append(
            "| horizon | notional | haircut | n | win rate | Wilson LB | breakeven WR "
            "| expectancy % | median % | max DD % |"
        )
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for cell in result.cells:

            def fmt(value, digits=3):
                return "UNKNOWN" if value is None else f"{value:.{digits}f}"

            lines.append(
                f"| {cell['horizon_days']}d | {cell['notional_isk'] / 1e9:.2f}B "
                f"| {cell['haircut_multiple']:.0f}x | {cell['samples']} "
                f"| {fmt(cell['win_rate'])} | {fmt(cell['wilson_lb'])} "
                f"| {fmt(cell['breakeven_win_rate'])} | {fmt(cell['expectancy_pct'])} "
                f"| {fmt(cell['median_pct'])} | {fmt(cell['max_drawdown_pct'], 2)} |"
            )
    if result.cohorts:
        lines.append("")
        lines.append("## By market-group cohort (smallest tier, 2x haircut)")
        lines.append("")
        lines.append("| cohort | horizon | n | win rate | Wilson LB | expectancy % |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for row in result.cohorts:

            def fmt(value):
                return "UNKNOWN" if value is None else f"{value:.3f}"

            lines.append(
                f"| {row['cohort']} | {row['horizon_days']}d | {row['samples']} "
                f"| {fmt(row['win_rate'])} | {fmt(row['wilson_lb'])} "
                f"| {fmt(row['expectancy_pct'])} |"
            )
    if result.excluded_haircut_unknown:
        lines.append("")
        lines.append("## Excluded as `haircut_unknown`")
        lines.append("")
        lines.append(
            "Instances whose type's CURRENT book cannot fill the tier. These are "
            "dropped, never priced at zero slippage."
        )
        lines.append("")
        for key, count in sorted(result.excluded_haircut_unknown.items()):
            lines.append(f"- {key}: {count:,}")
    lines.append("")
    lines.append("## Limitations of this study (plan.md §13.7)")
    lines.append("")
    for index, limitation in enumerate(LIMITATIONS, start=1):
        lines.append(f"{index}. {limitation}")
    if result.notes:
        lines.append("")
        lines.append("## Notes")
        for note in result.notes:
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def write_backtest(config: Config, result: BacktestResult) -> tuple[str, str]:
    paths = config.paths.ensure()
    stem = f"backtest-{result.region_id}-{result.generated_at[:10]}"
    json_path = paths.reports / f"{stem}.json"
    md_path = paths.reports / f"{stem}.md"
    atomic_write_text(
        json_path, json.dumps(result.as_dict(), indent=2, sort_keys=True, default=str)
    )
    atomic_write_text(md_path, render_backtest(result))
    return str(json_path), str(md_path)
