"""Mixed cargo: filling a hold with more than one thing (plan.md §23, H3).

The single-item plan is the honest one — it is priced end to end from measured
depth. A hold, though, is rarely filled by one item: the best plan takes 12 m³
of a 60,000 m³ freighter, and what goes in the other 59,988 m³ is a real
question.

This module answers it with a **deterministic greedy heuristic**, and the word
HEURISTIC is carried on the output rather than buried in a docstring. Filling a
hold optimally is a knapsack over marginal chunks whose prices move as you take
them; greedy is not optimal and can be beaten. What it is, is checkable: the
chunks are the same breakpoints the ranker already priced, the order is by
conservative profit per m³, and every cap is re-tested before each chunk rather
than once at the end.

**A basket is always shown beside the best single-item plan, never instead of
it.** If the operator wants one item he can read one item; the basket is an
additional read, and one whose method he can disagree with.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

__all__ = [
    "Basket",
    "BasketItem",
    "Chunk",
    "greedy_basket",
    "marginal_chunks",
    "non_overlapping",
]

HEURISTIC = "HEURISTIC"


@dataclass(frozen=True, slots=True)
class Chunk:
    """One step between two breakpoints of one plan: what it adds, and costs."""

    plan_index: int
    type_id: int
    type_name: str | None
    from_quantity: float
    to_quantity: float
    quantity: float
    capital_isk: float
    net_isk: float
    volume_m3: float | None
    #: Which books this chunk consumes. A basket that packs two plans sharing
    #: one of them is spending measured depth twice (see `haul_basket`).
    source: int | None
    destination: int | None

    @property
    def profit_per_m3(self) -> float | None:
        if not self.volume_m3 or self.volume_m3 <= 0:
            return None
        return self.net_isk / self.volume_m3


def marginal_chunks(plan, plan_index: int = 0) -> list[Chunk]:
    """The steps between a plan's priced breakpoints.

    Only steps that pay for themselves are returned: a chunk whose marginal net
    is zero or negative is the point at which the book stopped rewarding size,
    and it is exactly what the ranker already refused (`MARGINAL_NET_NEGATIVE`).
    """
    chunks: list[Chunk] = []
    previous = (0.0, 0.0, 0.0)
    unit_volume = plan.packaged_volume_m3
    for quantity, cost, net, _rejected in plan.breakpoints:
        step_quantity = quantity - previous[0]
        step_cost = cost - previous[1]
        step_net = net - previous[2]
        previous = (quantity, cost, net)
        if step_quantity <= 0 or step_net <= 0:
            continue
        chunks.append(
            Chunk(
                plan_index=plan_index,
                type_id=plan.type_id,
                type_name=plan.type_name,
                from_quantity=quantity - step_quantity,
                to_quantity=quantity,
                quantity=step_quantity,
                capital_isk=step_cost,
                net_isk=step_net,
                volume_m3=(step_quantity * unit_volume) if unit_volume else None,
                source=plan.source.station_id,
                destination=plan.destination.station_id,
            )
        )
    return chunks


@dataclass(frozen=True, slots=True)
class BasketItem:
    type_id: int
    type_name: str | None
    quantity: float
    capital_isk: float
    net_isk: float
    volume_m3: float
    destination: int | None

    def as_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "type_name": self.type_name,
            "quantity": self.quantity,
            "capital_isk": self.capital_isk,
            "net_isk": self.net_isk,
            "volume_m3": self.volume_m3,
            "destination": self.destination,
        }


@dataclass(slots=True)
class Basket:
    """A mixed hold, labelled for what it is."""

    items: list[BasketItem] = field(default_factory=list)
    capital_isk: float = 0.0
    net_isk: float = 0.0
    volume_m3: float = 0.0
    cargo_m3: float = 0.0
    method: str = HEURISTIC
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Plans dropped because they would have spent a book a sibling already
    #: spent. Counted rather than silently filtered.
    withheld_for_overlap: int = 0
    #: Which greedy key packed it (§23.21): `isk_per_m3` when the hold binds,
    #: `isk_per_capital` when the wallet does.
    score: str = "isk_per_m3"
    #: The one destination this basket is a trip to, when packed as one trip.
    destination: int | None = None

    @property
    def cargo_utilisation_pct(self) -> float | None:
        return (self.volume_m3 / self.cargo_m3 * 100.0) if self.cargo_m3 else None

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "items": [item.as_dict() for item in self.items],
            "capital_isk": self.capital_isk,
            "net_isk": self.net_isk,
            "volume_m3": self.volume_m3,
            "cargo_m3": self.cargo_m3,
            "cargo_utilisation_pct": self.cargo_utilisation_pct,
            "skipped": self.skipped,
            "notes": self.notes,
            "withheld_for_overlap": self.withheld_for_overlap,
            "score": self.score,
            "destination": self.destination,
        }


def non_overlapping(plans: Sequence, *, objective: str | None = None):
    """At most one plan per `(type, source)` and per `(type, destination)`.

    The scan ranks `(item, source, destination)` plans **independently**, which
    is right — they are alternatives, and the operator picks one. A basket that
    packs all of them spends measured depth twice: one 1,000-unit Jita ask sold
    to two hubs becomes 2,000 units of cargo out of a 1,000-unit book, and the
    mirror case double-counts one destination's bid depth.

    The restriction is the smallest one that cannot double-spend: keep the best
    plan (by the run's own objective) touching each book, and say how many were
    withheld. **The known refinement, deliberately not built here:** a shared
    consumption ledger, so a basket could take *part* of a book to one hub and
    the rest to another. That is worth doing if real baskets ever look starved,
    and it needs the marginal chunks to be re-priced against what a sibling
    plan already took — which is a different computation, not a filter.

    This lives here, next to the packing, rather than in the report layer that
    used to own it: a guard one caller away from the primitive it guards is a
    guard the next caller does not get.
    """

    def rank(plan):
        value = plan.objective_value(objective) if objective else None
        return -(value if value is not None else plan.net_profit)

    seen_source: set[tuple[int, int | None]] = set()
    seen_destination: set[tuple[int, int | None]] = set()
    kept = []
    withheld = 0
    for plan in sorted(plans, key=rank):
        source_key = (plan.type_id, plan.source.station_id)
        dest_key = (plan.type_id, plan.destination.station_id)
        if source_key in seen_source or dest_key in seen_destination:
            withheld += 1
            continue
        seen_source.add(source_key)
        seen_destination.add(dest_key)
        kept.append(plan)
    return kept, withheld


ISK_PER_M3 = "isk_per_m3"
ISK_PER_CAPITAL = "isk_per_capital"
AUTO = "auto"
SCORES = (ISK_PER_M3, ISK_PER_CAPITAL, AUTO)


def greedy_basket(
    plans: Sequence,
    *,
    capital_isk: float,
    cargo_m3: float,
    exposure_per_trade_isk: float | None = None,
    exposure_per_destination_isk: float | None = None,
    max_items: int = 20,
    objective: str | None = None,
    score: str = ISK_PER_M3,
    single_destination: bool = False,
) -> Basket:
    """Fill the hold greedily, by the constraint that actually binds.

    Every cap is re-checked **before each chunk**, not once at the end: a cap
    tested against the total is a cap that has already been exceeded on the way
    there. A chunk whose volume is unknown is skipped and named — packing a
    hold with something whose size nobody knows is how a plan becomes
    unexecutable at the station.

    Overlapping plans are withheld here, by `non_overlapping`, rather than by
    the caller: one book can only be spent once, and that is a property of
    packing, not of any one report.

    **Two rules added by §23.21, from a measurement.** On two real generations
    the greedy-by-ISK/m³ filled 250 M ISK with 1.8 m³ of formulas across four
    hubs and earned 42–66% of the best single plan. So: `score="auto"` packs
    by profit per ISK when the wallet binds and per m³ when the hold does (it
    runs both and keeps the higher net, and says which); `single_destination`
    packs one trip, to the destination whose basket nets most; and whatever
    the heuristic does, **a basket never under-earns the best single plan the
    same caps would admit** — if it would, that plan is shown instead, with a
    note saying the heuristic lost.
    """
    if score not in SCORES:
        raise ValueError(f"unknown basket score {score!r}; known: {SCORES}")
    caps = dict(
        capital_isk=float(capital_isk),
        cargo_m3=float(cargo_m3),
        exposure_per_trade_isk=exposure_per_trade_isk,
        exposure_per_destination_isk=exposure_per_destination_isk,
        max_items=max_items,
    )
    if single_destination:
        destinations = sorted({plan.destination.station_id for plan in plans}, key=str)
        candidates = [
            _basket(
                [plan for plan in plans if plan.destination.station_id == destination],
                objective=objective,
                score=score,
                destination=destination,
                **caps,
            )
            for destination in destinations
        ] or [_basket([], objective=objective, score=score, destination=None, **caps)]
        candidates.sort(key=lambda basket: -basket.net_isk)
        chosen = candidates[0]
        # Plans to OTHER destinations that spend a book this trip spends are
        # alternatives, not additions — the same rule `non_overlapping` applies
        # inside one basket, stated across baskets so it stays visible.
        packed = {item.type_id for item in chosen.items}
        sources = {
            (plan.type_id, plan.source.station_id)
            for plan in plans
            if plan.destination.station_id == chosen.destination and plan.type_id in packed
        }
        overlapping = sum(
            1
            for plan in plans
            if plan.destination.station_id != chosen.destination
            and (plan.type_id, plan.source.station_id) in sources
        )
        if overlapping:
            chosen.withheld_for_overlap += overlapping
            chosen.notes.append(
                f"{overlapping} plan(s) to other destinations withheld for depth overlap: "
                "they spend a source book this trip already spends. The scan still ranks "
                "them as alternatives."
            )
        if len(candidates) > 1:
            others = ", ".join(
                f"{basket.destination}: {basket.net_isk:,.0f}" for basket in candidates[1:]
            )
            chosen.notes.append(
                f"one trip: packed for destination {chosen.destination}; the other "
                f"destination basket(s) netted less ({others})"
            )
        elif chosen.destination is not None:
            chosen.notes.append(f"one trip: packed for destination {chosen.destination}")
        return chosen
    return _basket(plans, objective=objective, score=score, destination=None, **caps)


def _basket(plans, *, objective, score, destination, **caps) -> Basket:
    """One packing, floored at the best single plan the caps admit."""
    cargo_m3 = caps["cargo_m3"]
    basket = Basket(cargo_m3=cargo_m3, method=HEURISTIC, destination=destination)
    plans, withheld = non_overlapping(plans, objective=objective)
    available: list[list[Chunk]] = []
    skipped: list[str] = []
    for index, plan in enumerate(plans):
        chunks = marginal_chunks(plan, index)
        if not chunks:
            continue
        if chunks[0].volume_m3 is None:
            skipped.append(
                f"{plan.type_name or plan.type_id}: packaged volume UNKNOWN, so it cannot be packed"
            )
            continue
        available.append(chunks)

    if score == AUTO:
        by_volume = _pack([list(chunks) for chunks in available], score=ISK_PER_M3, **caps)
        by_capital = _pack([list(chunks) for chunks in available], score=ISK_PER_CAPITAL, **caps)
        packed, chosen_score = (
            (by_capital, ISK_PER_CAPITAL)
            if by_capital[1] > by_volume[1] + 1e-9
            else (by_volume, ISK_PER_M3)
        )
    else:
        packed, chosen_score = _pack(available, score=score, **caps), score
    taken, net, capital, volume = packed
    basket.score = chosen_score
    basket.notes.append(
        f"HEURISTIC: greedy over marginal chunks by {chosen_score.replace('_', ' ')}, not an "
        "optimum. Shown beside the best single-item plan, never instead of it."
        + (
            " Scored by the binding constraint: both keys were packed and the higher net kept."
            if score == AUTO
            else ""
        )
    )
    basket.withheld_for_overlap = withheld
    if withheld:
        basket.notes.append(
            f"{withheld} plan(s) withheld for depth overlap: they share a "
            "source or a destination book with a plan already in the basket, and one book can "
            "only be spent once. The scan still ranks them as alternatives."
        )
    basket.skipped.extend(skipped)
    basket.capital_isk, basket.net_isk, basket.volume_m3 = capital, net, volume
    for chunks in taken.values():
        first = chunks[0]
        basket.items.append(
            BasketItem(
                type_id=first.type_id,
                type_name=first.type_name,
                quantity=sum(chunk.quantity for chunk in chunks),
                capital_isk=sum(chunk.capital_isk for chunk in chunks),
                net_isk=sum(chunk.net_isk for chunk in chunks),
                volume_m3=sum(chunk.volume_m3 or 0.0 for chunk in chunks),
                destination=first.destination,
            )
        )
    basket.items.sort(key=lambda item: -item.net_isk)

    # **The floor.** The best single plan the same caps admit, taken whole.
    floor = _best_single(plans, **caps)
    if floor is not None and floor.net_profit > basket.net_isk + 1e-9:
        basket.notes.append(
            f"the greedy under-earned the best single plan ({basket.net_isk:,.0f} vs "
            f"{floor.net_profit:,.0f} ISK for {floor.type_name or floor.type_id}); "
            "showing that plan instead"
        )
        basket.items = [
            BasketItem(
                type_id=floor.type_id,
                type_name=floor.type_name,
                quantity=floor.quantity,
                capital_isk=floor.source_cost,
                net_isk=floor.net_profit,
                volume_m3=floor.cargo_m3 or 0.0,
                destination=floor.destination.station_id,
            )
        ]
        basket.capital_isk = floor.source_cost
        basket.net_isk = floor.net_profit
        basket.volume_m3 = floor.cargo_m3 or 0.0
    if not basket.items:
        basket.notes.append(
            "Nothing fits: every priced chunk is over a cap, or none has a "
            "measurable volume. That is an answer."
        )
    return basket


def _best_single(
    plans,
    *,
    capital_isk,
    cargo_m3,
    exposure_per_trade_isk,
    exposure_per_destination_isk,
    **_ignored,
):
    """The single plan with the highest net that every cap admits whole."""
    best = None
    for plan in plans:
        if plan.cargo_m3 is None:
            continue
        if plan.source_cost > capital_isk + 1e-9 or plan.cargo_m3 > cargo_m3 + 1e-9:
            continue
        if exposure_per_trade_isk is not None and plan.source_cost > exposure_per_trade_isk + 1e-9:
            continue
        if (
            exposure_per_destination_isk is not None
            and plan.source_cost > exposure_per_destination_isk + 1e-9
        ):
            continue
        if best is None or plan.net_profit > best.net_profit:
            best = plan
    return best


def _pack(
    available: list[list[Chunk]],
    *,
    score: str,
    capital_isk: float,
    cargo_m3: float,
    exposure_per_trade_isk,
    exposure_per_destination_isk,
    max_items: int,
) -> tuple[dict[int, list[Chunk]], float, float, float]:
    """The greedy itself. Returns `(taken, net, capital, volume)`."""
    taken: dict[int, list[Chunk]] = {}
    per_destination: dict[int, float] = {}
    per_plan_capital: dict[int, float] = {}
    net = capital = volume = 0.0
    while len(taken) < max_items:
        best: tuple[float, int] | None = None
        for position, chunks in enumerate(available):
            if not chunks:
                continue
            chunk = chunks[0]
            if chunk.volume_m3 is None:
                continue
            if volume + chunk.volume_m3 > cargo_m3 + 1e-9:
                continue
            if capital + chunk.capital_isk > capital_isk + 1e-9:
                continue
            spent = per_plan_capital.get(chunk.plan_index, 0.0) + chunk.capital_isk
            if exposure_per_trade_isk is not None and spent > exposure_per_trade_isk + 1e-9:
                continue
            destination_spent = (
                per_destination.get(chunk.destination, 0.0) + chunk.capital_isk
                if chunk.destination is not None
                else 0.0
            )
            if (
                exposure_per_destination_isk is not None
                and chunk.destination is not None
                and destination_spent > exposure_per_destination_isk + 1e-9
            ):
                continue
            value = (
                chunk.profit_per_m3
                if score == ISK_PER_M3
                else (chunk.net_isk / chunk.capital_isk if chunk.capital_isk > 0 else None)
            )
            if value is None or value <= 0:
                continue
            if best is None or value > best[0]:
                best = (value, position)
        if best is None:
            break
        _value, position = best
        chunk = available[position].pop(0)
        taken.setdefault(chunk.plan_index, []).append(chunk)
        capital += chunk.capital_isk
        net += chunk.net_isk
        volume += chunk.volume_m3 or 0.0
        per_plan_capital[chunk.plan_index] = (
            per_plan_capital.get(chunk.plan_index, 0.0) + chunk.capital_isk
        )
        if chunk.destination is not None:
            per_destination[chunk.destination] = (
                per_destination.get(chunk.destination, 0.0) + chunk.capital_isk
            )
    return taken, net, capital, volume


def render_basket(basket: Basket) -> str:
    """Text for the CLI and the drawer. The label leads."""
    lines = [
        f"MIXED CARGO — {basket.method} (not an optimum)",
        "",
        f"{len(basket.items)} item(s) · {basket.capital_isk:,.0f} ISK committed · "
        f"{basket.net_isk:,.0f} ISK net · {basket.volume_m3:,.0f} of "
        f"{basket.cargo_m3:,.0f} m³"
        + (
            f" ({basket.cargo_utilisation_pct:.0f}% of the hold)"
            if basket.cargo_utilisation_pct is not None
            else ""
        ),
        "",
    ]
    for item in basket.items:
        lines.append(
            f"  {item.type_name or item.type_id}: {item.quantity:,.0f} units · "
            f"{item.capital_isk:,.0f} ISK · {item.net_isk:,.0f} net · {item.volume_m3:,.0f} m³"
        )
    for note in basket.notes:
        lines.extend(["", note])
    for skip in basket.skipped:
        lines.append(f"  skipped — {skip}")
    return "\n".join(lines) + "\n"
