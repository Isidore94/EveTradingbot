"""Persistence: a snapshot measured against the snapshots before it (plan.md §23.21).

The hauling page's standing caveat is *a snapshot is not a tape*. Until this
module the size of that caveat was unmeasured. Measured on the real lake on
2026-09-04, across two five-hub generations 46.5 h apart: **44.5%** of ranked
plans were still plans in the next generation, the top-25's net re-priced to
**36%**, and quantity ≤ 5 plans survived at 33% against 51% for bulk. A ranking
that ignores this is ranking on the noisiest component of the signal.

What this module does is arithmetic over generations the lake already stores:
for each plan, re-walk **its chosen quantity** on each of the last K prior
generations of both books and ask whether it would still have netted money.
The result is a survival rate, the net it would have realised in each, and a
**persistence-weighted** ISK per active minute — offered as an objective, with
the unweighted figure kept beside it.

Three rules keep it honest.

* **Every generation used is a complete sweep.** Partial sweeps never reach
  `DepthLake.generations()` (§21 R1), so a missing page cannot masquerade as a
  vanished bid.
* **Too few generations is UNKNOWN**, never 100%. A plan first seen an hour ago
  has no persistence to speak of, and the persistent objective refuses to rank
  it (`PERSISTENCE_UNKNOWN`, counted as unrankable rather than rejected).
* **The plan's own quantity is what is re-walked.** Re-optimising the size on
  each old book would measure a different plan every time.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from statistics import median

import pandas as pd

from .books import DepthCurve, q_walk
from .config import Config
from .costs import CostModel
from .hauling import HaulPlan, curves_from_depth
from .timeutil import ensure_utc, iso, utcnow

__all__ = ["PERSISTENCE_UNKNOWN", "persistence_attachment", "persistence_of"]

#: Why the persistent objective could not score a plan. Not a rejection.
PERSISTENCE_UNKNOWN = "PERSISTENCE_UNKNOWN"


def persistence_of(
    *,
    quantity: float,
    net_now: float,
    source_curves: Sequence[DepthCurve | None],
    dest_curves: Sequence[DepthCurve | None],
    costs: CostModel,
    min_generations: int,
) -> dict:
    """Re-walk one plan's quantity on paired prior generations.

    `source_curves[k]` and `dest_curves[k]` are the k-th most recent prior
    generation of each book; a `None` is a book that carried no curve for this
    (station, type, side) then — the plan did not exist, which counts as not
    surviving rather than as unmeasured.
    """
    checked = min(len(source_curves), len(dest_curves))
    nets: list[float | None] = []
    for index in range(checked):
        ask = source_curves[index]
        bid = dest_curves[index]
        if ask is None or bid is None:
            nets.append(None)
            continue
        buy = q_walk(ask, quantity)
        sell = q_walk(bid, quantity)
        if not buy.known or not sell.known:
            nets.append(None)
            continue
        nets.append(costs.sell_proceeds(sell.value, maker=False) - buy.value)
    survived = sum(1 for net in nets if net is not None and net > 0)
    ratios = [
        (net / net_now if net is not None and net > 0 and net_now > 0 else 0.0) for net in nets
    ]
    known = checked >= int(min_generations) and checked > 0
    return {
        "generations_checked": checked,
        "survived": survived,
        "survival_rate": (survived / checked) if known else None,
        "median_net_ratio": (float(median(ratios)) if known and ratios else None),
        "net_ratios": ratios,
        "nets": nets,
        "known": known,
        "reason": (
            ""
            if known
            else (
                f"{checked} of {int(min_generations)} prior generation(s) available — "
                "persistence is UNKNOWN until more sweeps are stored"
            )
        ),
    }


def persistence_attachment(
    config: Config,
    depths,
    *,
    lake,
    stations: Iterable,
    costs: CostModel | None = None,
    generations: int | None = None,
    min_generations: int | None = None,
    now=None,
):
    """Build the bulk attachment `scan_hauls(persistence=...)` calls.

    Prior generations are read once per region and indexed once for the types
    the plans actually name, so a thousand plans cost one pass per generation
    rather than a thousand. Returns a callable `plans -> plans`.
    """
    costs = costs or CostModel.from_config(config)
    limit = int(generations if generations is not None else config.hauling.persistence_generations)
    floor = int(
        min_generations
        if min_generations is not None
        else config.hauling.persistence_min_generations
    )
    moment = ensure_utc(now or utcnow())
    station_ids = {int(station.station_id) for station in stations}
    prior: dict[int, list[pd.DataFrame]] = {}
    for region, snapshot in depths.items():
        if not getattr(snapshot, "known", False) or not snapshot.sweep_ts:
            continue
        prior[int(region)] = lake.generations(int(region), limit=limit, before=snapshot.sweep_ts)

    def attach(plans: list[HaulPlan]) -> list[HaulPlan]:
        if not plans or not prior:
            return list(plans)
        wanted = {int(plan.type_id) for plan in plans}
        indexed: dict[int, list[dict]] = {}
        stamps: dict[int, list[str]] = {}
        for region, frames in prior.items():
            indexed[region] = []
            stamps[region] = []
            for frame in frames:
                subset = frame[
                    frame["type_id"].isin(wanted) & frame["execution_location_id"].isin(station_ids)
                ]
                indexed[region].append(curves_from_depth(subset))
                stamp = frame["sweep_ts"].iloc[0]
                stamps[region].append(iso(pd.Timestamp(stamp).to_pydatetime()))
        out: list[HaulPlan] = []
        for plan in plans:
            source_region = int(plan.source.region_id) if plan.source.region_id else None
            dest_region = int(plan.destination.region_id) if plan.destination.region_id else None
            source_gens = indexed.get(source_region, []) if source_region is not None else []
            dest_gens = indexed.get(dest_region, []) if dest_region is not None else []
            checked = min(len(source_gens), len(dest_gens))
            result = persistence_of(
                quantity=plan.quantity,
                net_now=plan.net_profit,
                source_curves=[
                    source_gens[k].get((plan.source.station_id, int(plan.type_id), "sell"))
                    for k in range(checked)
                ],
                dest_curves=[
                    dest_gens[k].get((plan.destination.station_id, int(plan.type_id), "buy"))
                    for k in range(checked)
                ],
                costs=costs,
                min_generations=floor,
            )
            window = sorted(stamps.get(dest_region, [])[:checked]) if checked else []
            result["window"] = [window[0], window[-1]] if window else []
            result["measured_at"] = iso(moment)
            rate = result["survival_rate"]
            weighted = (
                plan.isk_per_active_minute * rate
                if rate is not None and plan.isk_per_active_minute is not None
                else None
            )
            out.append(replace(plan, persistence=result, persistent_isk_per_active_minute=weighted))
        return out

    return attach
