"""Loops: out and back, composed from plans already priced (plan.md §23.21).

The ranking is one-way, and every one-way ISK per active minute on the page
silently assumes the return leg is free — or that the session ends at the
destination. A hauler's unit of work is a loop. Measured on the 2026-08-28
lake: Jita → Dodixie → Jita netted 23.9 M in 57 minutes where the best one-way
plan showed 12.2 M in 22.

Nothing here prices anything new. A loop's legs are the best plan per ordered
station pair, its minutes add (the pickup leg is charged once, on the first
leg; every later leg starts where the previous one ended), and the capital it
commits is the **peak** outlay after each leg's proceeds — an `immediate` exit
turns the goods into ISK on arrival, so the return leg is partly funded by the
outbound one. Circuits of up to `max_stops` distinct stations are enumerated
exhaustively; five hubs is a few hundred sequences.

A loop that does not fit the session is counted, never shown.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import permutations

from .hauling import HaulPlan, HaulProfile

__all__ = ["Loop", "LoopSet", "compose_loops", "render_loops"]


@dataclass(frozen=True, slots=True)
class Loop:
    legs: tuple[HaulPlan, ...]
    stations: tuple[int, ...]
    net_isk: float
    capital_committed_isk: float
    active_minutes: float
    isk_per_active_minute: float | None
    jumps: int | None

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(leg.source.label for leg in self.legs)

    def as_dict(self) -> dict:
        return {
            "stations": list(self.stations),
            "route": " → ".join([*self.labels, self.legs[0].source.label]),
            "legs": [
                {
                    "type_id": leg.type_id,
                    "type_name": leg.type_name,
                    "source": leg.source.label,
                    "destination": leg.destination.label,
                    "quantity": leg.quantity,
                    "source_cost": leg.source_cost,
                    "net_profit": leg.net_profit,
                    "haul_jumps": leg.haul.jumps,
                }
                for leg in self.legs
            ],
            "net_isk": self.net_isk,
            "capital_committed_isk": self.capital_committed_isk,
            "active_minutes": self.active_minutes,
            "isk_per_active_minute": self.isk_per_active_minute,
            "jumps": self.jumps,
        }


@dataclass(slots=True)
class LoopSet:
    loops: list[Loop] = field(default_factory=list)
    considered: int = 0
    over_session: int = 0
    max_stops: int = 2
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "loops": [loop.as_dict() for loop in self.loops],
            "considered": self.considered,
            "over_session": self.over_session,
            "max_stops": self.max_stops,
            "notes": self.notes,
        }


def _leg_minutes(plan: HaulPlan, profile: HaulProfile, *, first: bool) -> float | None:
    """The first leg carries its pickup; later legs start where the last ended."""
    if first:
        return plan.active_minutes
    if not plan.haul.known or plan.haul.jumps is None:
        return None
    ship = profile.ship
    minutes = plan.haul.jumps * float(ship.seconds_per_jump) / 60.0 + 2.0 * float(
        ship.handling_minutes
    )
    return max(minutes, float(ship.handling_minutes))


def compose_loops(
    plans: Sequence[HaulPlan],
    *,
    profile: HaulProfile,
    max_stops: int = 3,
    max_loops: int = 20,
) -> LoopSet:
    """Every circuit of 2..`max_stops` distinct stations with a plan on each leg."""
    result = LoopSet(max_stops=int(max_stops))
    best: dict[tuple[int, int], HaulPlan] = {}
    for plan in plans:
        key = (plan.source.station_id, plan.destination.station_id)
        if key not in best or plan.net_profit > best[key].net_profit:
            best[key] = plan
    stations = sorted({station for key in best for station in key})
    if len(stations) < 2:
        result.notes.append("no loop: fewer than two stations carry a plan")
        return result

    seen: set[tuple[int, ...]] = set()
    for length in range(2, max(2, int(max_stops)) + 1):
        for sequence in permutations(stations, length):
            # A circuit is the same circuit from any of its stations; keep the
            # rotation that starts at the smallest id so each is counted once.
            rotation = min(range(length), key=lambda index: sequence[index])
            canonical = sequence[rotation:] + sequence[:rotation]
            if canonical in seen:
                continue
            seen.add(canonical)
            legs = []
            for index in range(length):
                key = (canonical[index], canonical[(index + 1) % length])
                if key not in best:
                    legs = []
                    break
                legs.append(best[key])
            if not legs:
                continue
            result.considered += 1
            loop = _price(legs, canonical, profile)
            if loop is None:
                continue
            if profile.session_minutes and loop.active_minutes > float(profile.session_minutes):
                result.over_session += 1
                continue
            result.loops.append(loop)
    result.loops.sort(
        key=lambda loop: (
            -(loop.isk_per_active_minute if loop.isk_per_active_minute is not None else -1.0)
        )
    )
    del result.loops[max_loops:]
    if not result.loops:
        result.notes.append(
            "no loop fits: "
            + (
                f"{result.over_session} circuit(s) exceed the session"
                if result.over_session
                else "no ordered station pair has a plan both ways"
            )
        )
    return result


def _price(legs: list[HaulPlan], stations: tuple[int, ...], profile: HaulProfile) -> Loop | None:
    minutes = 0.0
    net = 0.0
    peak = 0.0
    banked = 0.0
    jumps = 0
    for index, leg in enumerate(legs):
        leg_minutes = _leg_minutes(leg, profile, first=index == 0)
        if leg_minutes is None:
            return None
        minutes += leg_minutes
        # What the wallet has to find for this leg, after the ISK the previous
        # legs already turned the goods back into.
        peak = max(peak, leg.source_cost - banked)
        banked += leg.net_profit
        net += leg.net_profit
        jumps += (leg.total_jumps if index == 0 else leg.haul.jumps) or 0
    return Loop(
        legs=tuple(legs),
        stations=tuple(stations),
        net_isk=net,
        capital_committed_isk=peak,
        active_minutes=minutes,
        isk_per_active_minute=(net / minutes) if minutes else None,
        jumps=jumps,
    )


def render_loops(loops: LoopSet) -> str:
    """Text for the CLI and the drawer."""
    lines = [
        f"LOOPS — out and back, composed from the plans above (up to {loops.max_stops} stops)",
        "",
        "Every one-way ISK/minute assumes the return leg is free. A loop charges it: the "
        "pickup once, each later leg from where the last one ended, and the capital is the "
        "peak outlay after each leg's proceeds.",
        "",
    ]
    if not loops.loops:
        lines.append(
            "no loop composed"
            + (
                f" — {loops.over_session} circuit(s) exceed the session"
                if loops.over_session
                else " — no ordered station pair has a plan both ways"
            )
        )
        return "\n".join(lines) + "\n"
    for loop in loops.loops:
        per_minute = (
            f"{loop.isk_per_active_minute:,.0f} ISK/min"
            if loop.isk_per_active_minute is not None
            else "UNKNOWN ISK/min"
        )
        lines.append(
            f"{' → '.join([*loop.labels, loop.legs[0].source.label])}: "
            f"net {loop.net_isk:,.0f} ISK in {loop.active_minutes:.0f} min ({per_minute}), "
            f"peak capital {loop.capital_committed_isk:,.0f}"
        )
        for leg in loop.legs:
            lines.append(
                f"    {leg.type_name or leg.type_id}: {leg.quantity:,.0f} units "
                f"{leg.source.label} → {leg.destination.label}, net {leg.net_profit:,.0f}"
            )
    if loops.over_session:
        lines.extend(
            ["", f"{loops.over_session} circuit(s) exceeded the session and are not shown"]
        )
    return "\n".join(lines) + "\n"
