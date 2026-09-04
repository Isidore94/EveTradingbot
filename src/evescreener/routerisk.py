"""Route losses from the killmail lake — a column, never a multiplier (plan.md §23.21).

The destruction lead-lag hypothesis did not survive (§14, ρ = 0.027), and the
killmail data had no other consumer. It has one use the plan never assigned
it: **hauler losses per system on the route the plan flies.** `RouteFacts`
already carries the ordered system list; the archive ingest already sees every
killmail's system and hull. This module reduces losses per system per day on
the same ingest and reads them back per route.

It is a column. Turning a 90-day count into a probability of loss, and that
into an expected cost, needs an assumption about the operator's own ship,
fit, timing and attention that nothing in this lake measures; until the
shadow period says how to weight it, the number is shown and never multiplied
into a net (§23.14: an assumption is never folded into a profit).

**UNKNOWN is not zero.** A window with no ingested archive days reports
UNKNOWN with the reason; a route through quiet systems inside a covered window
reports zero with its coverage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, timedelta

from .config import Config
from .timeutil import utcnow

__all__ = [
    "SHIPS_ROOT",
    "hauler_type_ids",
    "persist_system_losses",
    "reduce_system_losses",
    "route_risk",
    "route_risk_attachment",
]

#: The market-group root a hull's ancestry must end at. Blueprints sit under
#: an identically named "Haulers" group beneath "Blueprints & Reactions".
SHIPS_ROOT = "Ships"

NOTE = (
    "Losses on the route over the window, from ingested killmails. A column, never a "
    "multiplier: nothing here turns a count into a probability for THIS ship."
)


def hauler_type_ids(db, group_names: Iterable[str]) -> set[int]:
    """Type ids whose market-group ancestry passes a hauler group under `Ships`."""
    wanted = {str(name) for name in group_names}
    if not wanted:
        return set()
    names = {
        int(row["market_group_id"]): str(row["name"])
        for row in db.conn.execute("SELECT market_group_id, name FROM sde_market_groups")
    }
    chains: dict[int, list[int]] = {}
    hulls: set[int] = set()
    for row in db.conn.execute(
        "SELECT type_id, market_group_id FROM sde_types WHERE market_group_id IS NOT NULL"
    ):
        group = int(row["market_group_id"])
        if group not in chains:
            chains[group] = db.market_group_chain(group)
        chain = [names.get(ancestor, "") for ancestor in chains[group]]
        if chain and chain[-1] == SHIPS_ROOT and wanted.intersection(chain):
            hulls.add(int(row["type_id"]))
    return hulls


def reduce_system_losses(killmails, hauler_types: set[int]) -> dict[tuple[int, str], list[int]]:
    """`(system_id, day) -> [hull_losses, hauler_losses]`."""
    counts: dict[tuple[int, str], list[int]] = {}
    for killmail in killmails:
        time = str(killmail.get("killmail_time") or "")
        system = killmail.get("solar_system_id")
        if not time or system is None:
            continue
        hull = (killmail.get("victim") or {}).get("ship_type_id")
        if hull is None:
            continue
        entry = counts.setdefault((int(system), time[:10]), [0, 0])
        entry[0] += 1
        if int(hull) in hauler_types:
            entry[1] += 1
    return counts


def persist_system_losses(db, counts: dict[tuple[int, str], list[int]]) -> int:
    """Upsert per (system, day); a re-ingested day replaces rather than adds."""
    rows = [(system, day, hulls, haulers) for (system, day), (hulls, haulers) in counts.items()]
    if not rows:
        return 0
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO system_losses(solar_system_id, day, hull_losses, hauler_losses)"
            " VALUES(?,?,?,?) ON CONFLICT(solar_system_id, day) DO UPDATE SET"
            " hull_losses=excluded.hull_losses, hauler_losses=excluded.hauler_losses",
            rows,
        )
    return len(rows)


def route_risk(db, systems: Iterable[int], *, days: int, end: date | None = None) -> dict:
    """Losses along `systems` over the last `days` ingested days ending at `end`."""
    ids = [int(system) for system in systems]
    last = end or utcnow().date()
    first = last - timedelta(days=max(0, int(days) - 1))
    start_day, end_day = first.isoformat(), last.isoformat()
    covered = db.killmail_days_between(start_day, end_day)
    if covered <= 0:
        return {
            "known": False,
            "reason": (
                f"no killmail archive days ingested between {start_day} and {end_day} — "
                "run `killmails` to backfill; a window nobody measured is UNKNOWN, not zero"
            ),
            "window_days": int(days),
            "days_covered": 0,
            "hull_losses": None,
            "hauler_losses": None,
            "per_system": {},
            "note": NOTE,
        }
    sums = db.system_losses_between(ids, start_day, end_day)
    per_system = {
        system: {
            "hull_losses": sums.get(system, (0, 0))[0],
            "hauler_losses": sums.get(system, (0, 0))[1],
        }
        for system in ids
    }
    return {
        "known": True,
        "reason": "",
        "window_days": int(days),
        "days_covered": covered,
        "hull_losses": sum(entry["hull_losses"] for entry in per_system.values()),
        "hauler_losses": sum(entry["hauler_losses"] for entry in per_system.values()),
        "per_system": per_system,
        "note": NOTE,
    }


def route_risk_attachment(config: Config, db, *, end: date | None = None):
    """`plan -> plan` with `route_risk` attached, cached per route. Never a rank input."""
    cache: dict[tuple[int, ...], dict] = {}
    days = int(config.hauling.route_risk_days)

    def attach(plan):
        systems = tuple(plan.haul.systems) if plan.haul.known else ()
        if not systems:
            risk = {
                "known": False,
                "reason": "the route is UNKNOWN, so there is nothing to count losses along",
                "window_days": days,
                "days_covered": 0,
                "hull_losses": None,
                "hauler_losses": None,
                "per_system": {},
                "note": NOTE,
            }
        else:
            if systems not in cache:
                cache[systems] = route_risk(db, systems, days=days, end=end)
            risk = cache[systems]
        return replace(plan, route_risk=risk)

    return attach
