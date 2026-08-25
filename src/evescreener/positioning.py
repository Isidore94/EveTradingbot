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

__all__ = ["Basket", "BasketItem", "Chunk", "greedy_basket", "marginal_chunks"]

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
    for quantity, cost, net in plan.breakpoints:
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
        }


def greedy_basket(
    plans: Sequence,
    *,
    capital_isk: float,
    cargo_m3: float,
    exposure_per_trade_isk: float | None = None,
    exposure_per_destination_isk: float | None = None,
    max_items: int = 20,
) -> Basket:
    """Fill the hold greedily by conservative profit per m³.

    Every cap is re-checked **before each chunk**, not once at the end: a cap
    tested against the total is a cap that has already been exceeded on the way
    there. A chunk whose volume is unknown is skipped and named — packing a
    hold with something whose size nobody knows is how a plan becomes
    unexecutable at the station.
    """
    basket = Basket(cargo_m3=float(cargo_m3), method=HEURISTIC)
    basket.notes.append(
        "HEURISTIC: greedy over marginal chunks by profit per m³, not an optimum. "
        "Shown beside the best single-item plan, never instead of it."
    )
    available: list[list[Chunk]] = []
    for index, plan in enumerate(plans):
        chunks = marginal_chunks(plan, index)
        if not chunks:
            continue
        if chunks[0].volume_m3 is None:
            basket.skipped.append(
                f"{plan.type_name or plan.type_id}: packaged volume UNKNOWN, so it cannot be packed"
            )
            continue
        available.append(chunks)

    taken: dict[int, list[Chunk]] = {}
    per_destination: dict[int, float] = {}
    per_plan_capital: dict[int, float] = {}
    while len(taken) <= max_items:
        best: tuple[float, int] | None = None
        for position, chunks in enumerate(available):
            if not chunks:
                continue
            chunk = chunks[0]
            if chunk.volume_m3 is None:
                continue
            if basket.volume_m3 + chunk.volume_m3 > cargo_m3 + 1e-9:
                continue
            if basket.capital_isk + chunk.capital_isk > capital_isk + 1e-9:
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
            score = chunk.profit_per_m3
            if score is None or score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, position)
        if best is None:
            break
        _score, position = best
        chunk = available[position].pop(0)
        taken.setdefault(chunk.plan_index, []).append(chunk)
        basket.capital_isk += chunk.capital_isk
        basket.net_isk += chunk.net_isk
        basket.volume_m3 += chunk.volume_m3 or 0.0
        per_plan_capital[chunk.plan_index] = (
            per_plan_capital.get(chunk.plan_index, 0.0) + chunk.capital_isk
        )
        if chunk.destination is not None:
            per_destination[chunk.destination] = (
                per_destination.get(chunk.destination, 0.0) + chunk.capital_isk
            )

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
    if not basket.items:
        basket.notes.append(
            "Nothing fits: every priced chunk is over a cap, or none has a "
            "measurable volume. That is an answer."
        )
    return basket


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
