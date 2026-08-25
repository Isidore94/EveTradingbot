"""Getting out: scenarios, assumptions, and the grade that is not a forecast.

Everything on the hauling page up to this point is **measured** — depth that
was swept, routes on a graph, fees from the operator's own skills. This module
is where the honest part stops, and the whole design is about saying so.

**What is measured.** Daily units traded at the destination, over completed
bars only: median, low/base/high quantiles, zero days, missing days, whether
recent volume looks like the longer run, and how dispersed the price is.

**What is assumed, and labelled ASSUMED everywhere it appears.**

* `destination_share_prior` — the share of a region's traded volume that
  happens at the destination hub. ESI's regional history carries **no station
  split**, so this is not derivable from the lake at any price. It stays a
  prior until the operator's own recorded fills can replace it.
* `capture_share` — the share of that flow one order wins. Also a prior.

`liquidation_days = q / (quantile_units × destination_share × capture_share)`.
Two of those three denominators are assumptions, so the answer is a scenario,
never an estimate — and a **zero or unmeasurable quantile makes it UNKNOWN**
rather than fast. A dead market does not become tradeable by dividing by a
small number.

**The reliability grade is about data quality, not about profit.** It counts
what was measurable — generation freshness, depth completeness, bar
sample/freshness, route provenance — and any UNKNOWN component caps it at D.
An A means "everything this row rests on was measured", not "this trade works".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .books import DepthCurve, curve_from_frame, q_walk
from .config import Config
from .costs import CostModel
from .hauling import IMMEDIATE
from .timeutil import ensure_utc, last_completed_bar_date, utcnow

__all__ = [
    "GRADES",
    "LiquidityProfile",
    "MAKER_ASSUMPTION",
    "liquidation_days",
    "liquidity_attachment",
    "measure_liquidity",
    "reliability_grade",
    "scenarios",
]

LOW, BASE, HIGH = "low", "base", "high"
SCENARIOS = (LOW, BASE, HIGH)
GRADES = ("A", "B", "C", "D", "E", "F")

MAKER_ASSUMPTION = (
    "MAKER SCENARIO — DISPLAY ONLY, NEVER THE RANK. A snapshot proves the price "
    "was postable, never that anyone traded into it. Queue position, undercutting, "
    "waiting time and adverse selection are unpriced and no number in this system "
    "bounds them (the same limit §17 D-31 states for SPREADS)."
)


@dataclass(frozen=True, slots=True)
class LiquidityProfile:
    """What the destination's own history says about daily volume."""

    type_id: int
    region_id: int
    bars_used: int = 0
    median_units: float | None = None
    quantile_units: dict = field(default_factory=dict)
    zero_days: int = 0
    missing_days: int = 0
    recent_long_ratio: float | None = None
    price_dispersion: float | None = None
    known: bool = False
    reason: str = "no bars for this type in the destination region"

    def units(self, scenario: str) -> float | None:
        return self.quantile_units.get(scenario)

    def as_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "region_id": self.region_id,
            "bars_used": self.bars_used,
            "median_units": self.median_units,
            "quantile_units": self.quantile_units,
            "zero_days": self.zero_days,
            "missing_days": self.missing_days,
            "recent_long_ratio": self.recent_long_ratio,
            "price_dispersion": self.price_dispersion,
            "known": self.known,
            "reason": self.reason,
        }


def measure_liquidity(
    bars: pd.DataFrame,
    *,
    type_id: int,
    region_id: int,
    quantiles: Sequence[float] = (0.25, 0.5, 0.75),
    min_bars: int = 10,
    window_days: int = 30,
    now=None,
) -> LiquidityProfile:
    """Daily units at the destination, over **completed bars only**.

    A partial current-day bar is still moving in every field, so it is excluded
    at the boundary the history roll actually uses (11:05 UTC, §21 R2).
    """
    moment = ensure_utc(now or utcnow())
    profile = LiquidityProfile(type_id=int(type_id), region_id=int(region_id))
    if bars is None or bars.empty:
        return profile
    frame = bars
    if "type_id" in frame:
        frame = frame[frame["type_id"] == int(type_id)]
    if "region_id" in frame:
        frame = frame[frame["region_id"] == int(region_id)]
    if frame.empty:
        return profile

    stamps = pd.to_datetime(frame["datetime"], utc=True, errors="coerce")
    frame = frame.assign(_day=stamps.dt.date).dropna(subset=["_day"])
    cutoff = last_completed_bar_date(moment)
    frame = frame[frame["_day"] <= cutoff]
    if frame.empty:
        return replace(profile, reason="no completed bars for this type in the destination region")

    window_start = cutoff - pd.Timedelta(days=window_days).to_pytimedelta()
    window = frame[frame["_day"] >= window_start]
    # **No fallback to the last N rows.** That fallback measured a market which
    # had not traded for a year and reported 500 units a day with an empty
    # reason — feeding the maker caps, the scenarios drawer and the reliability
    # grade's `destination_bars: ok`. Bars outside the window are not evidence
    # about the window; an empty window falls through to the `min_bars` refusal
    # below and says so.

    volumes = (
        pd.to_numeric(window["volume"], errors="coerce").dropna()
        if not window.empty
        else pd.Series(dtype="float64")
    )
    if len(volumes) < int(min_bars):
        return replace(
            profile,
            bars_used=int(len(volumes)),
            reason=(
                f"{len(volumes)} completed bar(s) in the {int(window_days)}-day window "
                f"against a {int(min_bars)}-bar minimum — the volume distribution is "
                "UNKNOWN"
            ),
        )

    labelled = dict(zip(SCENARIOS, sorted(float(value) for value in quantiles), strict=False))
    quantile_units = {
        name: float(np.quantile(volumes, quantile)) for name, quantile in labelled.items()
    }
    closes = pd.to_numeric(window["close"], errors="coerce").dropna()
    dispersion = None
    if len(closes) >= 3 and float(np.median(closes)) > 0:
        dispersion = float(np.median(np.abs(closes - np.median(closes))) / np.median(closes))

    recent = pd.to_numeric(window.tail(7)["volume"], errors="coerce").dropna()
    ratio = None
    if len(recent) >= 3 and float(np.median(volumes)) > 0:
        ratio = float(np.median(recent) / np.median(volumes))

    return LiquidityProfile(
        type_id=int(type_id),
        region_id=int(region_id),
        bars_used=int(len(volumes)),
        median_units=float(np.median(volumes)),
        quantile_units=quantile_units,
        zero_days=int((volumes <= 0).sum()),
        missing_days=int(max(0, window_days - len(volumes))),
        recent_long_ratio=ratio,
        price_dispersion=dispersion,
        known=True,
        reason="",
    )


def liquidation_days(
    quantity: float,
    quantile_units: float | None,
    *,
    destination_share: float,
    capture_share: float,
) -> float | None:
    """Days to sell `quantity` under one scenario, or **None** for UNKNOWN.

    Zero or unmeasurable flow is UNKNOWN, never a very large number and never a
    fast one: a market with no volume does not become tradeable by dividing by
    something small.
    """
    if not quantile_units or quantile_units <= 0:
        return None
    if destination_share <= 0 or capture_share <= 0:
        return None
    daily = float(quantile_units) * float(destination_share) * float(capture_share)
    if daily <= 0:
        return None
    return float(quantity) / daily


def scenarios(
    profile: LiquidityProfile,
    quantity: float,
    *,
    destination_share: float,
    capture_shares: Sequence[float],
) -> dict:
    """The three liquidation reads, with their assumptions attached."""
    shares = dict(zip(SCENARIOS, sorted(float(value) for value in capture_shares), strict=False))
    days = {
        name: liquidation_days(
            quantity,
            profile.units(name),
            destination_share=destination_share,
            capture_share=shares.get(name, 0.0),
        )
        for name in SCENARIOS
    }
    return {
        "scenarios": days,
        "known": any(value is not None for value in days.values()),
        "reason": profile.reason,
        "measured": {
            "bars_used": profile.bars_used,
            "median_units_per_day": profile.median_units,
            "quantile_units": profile.quantile_units,
            "zero_days": profile.zero_days,
            "missing_days": profile.missing_days,
            "recent_7d_over_window_median": profile.recent_long_ratio,
            "price_dispersion": profile.price_dispersion,
        },
        "assumptions": {
            "destination_share_prior": destination_share,
            "capture_share_low_base_high": [shares.get(name) for name in SCENARIOS],
            "note": (
                "ASSUMED, not measured. Regional history carries no station split, so "
                "neither factor is derivable from this lake; both become measurements "
                "only when the operator's own fills can replace them (§23.7)."
            ),
        },
    }


def reliability_grade(components: Mapping[str, str], weights: Mapping[str, int]) -> dict:
    """Grade the DATA behind a row. Not the trade — say so in the tooltip.

    Each component is `ok`, `weak` or `unknown`. An `unknown` anywhere **caps
    the grade at D**: a row resting on something nobody measured cannot be an A
    however good the rest of it looks.
    """
    score = 0
    total = 0
    unknown = False
    detail = []
    for name, weight in weights.items():
        state = components.get(name, "unknown")
        total += weight
        if state == "ok":
            score += weight
        elif state == "weak":
            score += weight / 2
        else:
            unknown = True
        detail.append({"component": name, "weight": weight, "state": state})
    ratio = (score / total) if total else 0.0
    if ratio >= 0.95:
        grade = "A"
    elif ratio >= 0.8:
        grade = "B"
    elif ratio >= 0.6:
        grade = "C"
    elif ratio >= 0.4:
        grade = "D"
    elif ratio > 0:
        grade = "E"
    else:
        grade = "F"
    capped = False
    if unknown and GRADES.index(grade) < GRADES.index("D"):
        grade = "D"
        capped = True
    return {
        "grade": grade,
        "score": float(score),
        "max_score": float(total),
        "components": detail,
        "capped_by_unknown": capped,
        "note": (
            "Data quality only: how much of what this row rests on was actually "
            "measured. It is NOT a probability of profit, and nothing in this "
            "system estimates one."
        ),
    }


RELIABILITY_WEIGHTS = {
    "generation_freshness": 2,
    "depth_completeness": 2,
    "destination_bars": 1,
    "route_provenance": 1,
}


def maker_scenario(
    *,
    dest_sell_curve: DepthCurve | None,
    quantity: float,
    immediate_bid_value: float | None,
    costs: CostModel,
    dest_station: int | None,
    tick_isk: float,
    liquidation: dict | None,
) -> dict | None:
    """What posting the goods instead of dumping them would look like.

    Display only, never the default rank (§23.11). The downside column is the
    honest one: what walking out into the bid **today** would actually pay.
    """
    if dest_sell_curve is None or not dest_sell_curve.levels:
        return None
    best_ask = min(level.price for level in dest_sell_curve.levels if level.qty > 0)
    list_price = max(0.01, float(best_ask) - float(tick_isk))
    ahead_units = sum(
        level.qty for level in dest_sell_curve.levels if level.qty > 0 and level.price <= list_price
    )
    ahead_orders = sum(
        level.order_count
        for level in dest_sell_curve.levels
        if level.qty > 0 and level.price <= list_price
    )
    # Queue-ahead is what sells BEFORE you at your own price, which is zero the
    # moment you undercut the book — and that is exactly the position that
    # invites being undercut back. The competing depth is reported beside it so
    # the zero cannot read as "no competition".
    competing_units = sum(level.qty for level in dest_sell_curve.levels if level.qty > 0)
    competing_orders = sum(level.order_count for level in dest_sell_curve.levels if level.qty > 0)
    broker = costs.broker_fee_at(dest_station)
    gross = list_price * float(quantity)
    proceeds = gross * (1.0 - (costs.sales_tax_pct + broker) / 100.0)
    return {
        "proposed_list_price": list_price,
        "queue_ahead_units": float(ahead_units),
        "queue_ahead_orders": int(ahead_orders),
        "competing_units": float(competing_units),
        "competing_orders": int(competing_orders),
        "broker_fee_pct": broker,
        "gross_if_filled": gross,
        "net_if_filled": proceeds,
        "downside_immediate_bid_value": immediate_bid_value,
        "liquidation_days": (liquidation or {}).get("scenarios", {}).get(BASE),
        "assumption": MAKER_ASSUMPTION,
    }


def liquidity_attachment(
    config: Config,
    db,
    depths: Mapping[int, object],
    profile,
    *,
    bars_by_region: Mapping[int, pd.DataFrame] | None = None,
    costs: CostModel | None = None,
    now=None,
):
    """Build the per-plan attachment the hauling scan calls.

    Returns a callable `plan -> plan`, or None when there is nothing to attach.
    It reads the **destination** region's bars and sell-side depth, both from
    local files, and caches per (type, region) because a scan asks about the
    same destination thousands of times.
    """
    from .store.lake import BarLake

    costs = costs or CostModel.from_config(config)
    lake = BarLake(config.paths)
    bars_cache: dict[int, pd.DataFrame] = dict(bars_by_region or {})
    profiles: dict[tuple[int, int], LiquidityProfile] = {}
    sell_curves: dict[tuple[int, int], DepthCurve | None] = {}
    moment = ensure_utc(now or utcnow())

    def region_bars(region_id: int) -> pd.DataFrame:
        if region_id not in bars_cache:
            bars_cache[region_id] = lake.read(int(region_id))
        return bars_cache[region_id]

    def liquidity_for(type_id: int, region_id: int) -> LiquidityProfile:
        key = (int(type_id), int(region_id))
        if key not in profiles:
            profiles[key] = measure_liquidity(
                region_bars(region_id),
                type_id=type_id,
                region_id=region_id,
                quantiles=config.hauling.liquidity_quantiles,
                min_bars=config.hauling.min_liquidity_bars,
                now=moment,
            )
        return profiles[key]

    def sell_curve(type_id: int, station_id: int, region_id: int) -> DepthCurve | None:
        key = (int(type_id), int(station_id))
        if key not in sell_curves:
            snapshot = depths.get(int(region_id))
            frame = getattr(snapshot, "priceable", None)
            sell_curves[key] = (
                curve_from_frame(
                    frame, type_id=type_id, side="sell", execution_location_id=station_id
                )
                if frame is not None and not frame.empty
                else None
            )
        return sell_curves[key]

    def attach(plan):
        region = plan.destination.region_id
        measured = liquidity_for(plan.type_id, region) if region is not None else None
        scenario = (
            scenarios(
                measured,
                plan.quantity,
                destination_share=config.hauling.destination_share_prior,
                capture_shares=config.hauling.capture_share,
            )
            if measured is not None
            else None
        )
        base_days = (scenario or {}).get("scenarios", {}).get(BASE)
        curve = sell_curve(plan.type_id, plan.destination.station_id, region) if region else None
        immediate = plan.gross_sale
        maker = maker_scenario(
            dest_sell_curve=curve,
            quantity=plan.quantity,
            immediate_bid_value=immediate,
            costs=costs,
            dest_station=plan.destination.station_id,
            tick_isk=config.paper.maker_tick_isk,
            liquidation=scenario,
        )
        components = {
            "generation_freshness": _freshness_state(plan, config),
            "depth_completeness": (
                "ok" if plan.source_depth_complete and plan.dest_depth_complete else "weak"
            ),
            "destination_bars": "ok" if measured is not None and measured.known else "unknown",
            "route_provenance": (
                "ok" if plan.haul.known and plan.haul.sde_build is not None else "unknown"
            ),
        }
        grade = reliability_grade(components, RELIABILITY_WEIGHTS)
        # **The exit model decides which clock ISK-days are charged on.** An
        # IMMEDIATE exit dumps into the destination bid on arrival, so the
        # capital is committed for the trip and nothing longer — §23.5 says
        # travel time, and the scenario below is a different question that
        # belongs in the drawer rather than in this row's denominator. Only a
        # MAKER exit waits for the destination's own volume, and only then does
        # the scenario become the number the row is charged over.
        maker_exit = getattr(profile, "exit_model", IMMEDIATE) != IMMEDIATE
        scenario_days = base_days if maker_exit else None
        return replace(
            plan,
            liquidity=scenario,
            maker=maker,
            reliability=grade,
            liquidation_days=(
                scenario_days if scenario_days is not None else plan.liquidation_days
            ),
            liquidation_reason=(
                measured.reason
                if maker_exit and base_days is None and measured is not None
                else plan.liquidation_reason
            ),
            isk_per_capital_day=(
                plan.net_profit / (plan.source_cost * scenario_days)
                if scenario_days and plan.source_cost > 0
                else plan.isk_per_capital_day
            ),
        )

    return attach


def _freshness_state(plan, config: Config) -> str:
    age = plan.generation_age_minutes
    if age is None:
        return "unknown"
    budget = float(config.costs.book_staleness_minutes)
    if age <= budget / 2.0:
        return "ok"
    return "weak" if age <= budget else "unknown"


def taker_walk_value(curve: DepthCurve | None, quantity: float) -> float | None:
    """What dumping `quantity` into a curve pays right now, or UNKNOWN."""
    if curve is None:
        return None
    walk = q_walk(curve, quantity)
    return walk.value if walk.known else None
