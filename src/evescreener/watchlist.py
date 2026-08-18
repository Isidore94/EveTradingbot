"""The Phase 0 seed watchlist (plan.md §11 D4).

Fifty names the operator already knows, resolved to type_ids against the SDE
at ingest. **An unresolvable name is a loud error, never a silent skip** —
names drift across patches, and a quietly-dropped name is a hole in the screen
that nobody notices.

The operator edits this roster freely; nothing in the system removes a name
from the watchlist on its own (the candidate-registry invariant, §3.6).
"""

from __future__ import annotations

from .clock import now_utc
from .state import StateStore

SEED_SOURCE = "seed:plan-d4"

MINERALS = (
    "Tritanium",
    "Pyerite",
    "Mexallon",
    "Isogen",
    "Nocxium",
    "Zydrine",
    "Megacyte",
    "Morphite",
)

FUEL = (
    "Nitrogen Fuel Block",
    "Oxygen Fuel Block",
    "Helium Fuel Block",
    "Hydrogen Fuel Block",
    "Nitrogen Isotopes",
    "Helium Isotopes",
)

ACCOUNT_TIER = (
    "PLEX",
    "Large Skill Injector",
    "Skill Extractor",
)

CONSUMABLES = (
    "Nanite Repair Paste",
    "Antimatter Charge M",
    "Antimatter Charge L",
    "Scourge Light Missile",
    "Scourge Heavy Missile",
    "Inferno Heavy Missile",
)

DRONES = (
    "Hobgoblin II",
    "Hammerhead II",
    "Ogre II",
)

T2_MODULES = (
    "Damage Control II",
    "Large Shield Extender II",
    "Ballistic Control System II",
    "Gyrostabilizer II",
    "Heat Sink II",
    "Magnetic Field Stabilizer II",
    "Drone Damage Amplifier II",
    "10MN Afterburner II",
    "50MN Microwarpdrive II",
    "Warp Disruptor II",
    "Warp Scrambler II",
    "Stasis Webifier II",
)

HULLS = (
    "Caracal",
    "Vexor",
    "Drake",
    "Ferox",
    "Hurricane",
    "Myrmidon",
    "Gila",
    "Ishtar",
    "Praxis",
    "Dominix",
    "Raven",
    "Megathron",
)

SEED_WATCHLIST: tuple[str, ...] = (
    MINERALS + FUEL + ACCOUNT_TIER + CONSUMABLES + DRONES + T2_MODULES + HULLS
)


class UnresolvedTypeNames(RuntimeError):
    """One or more watchlist names do not exist in the loaded SDE."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(
            "watchlist names could not be resolved against the SDE "
            f"({len(names)}): {', '.join(names)}. Fix the name or refresh the "
            "SDE — a name is never silently dropped (plan.md §11 D4)."
        )
        self.names = names


def resolve_seed(store: StateStore) -> dict[str, int]:
    """Resolve every seed name to a type_id, or raise with the full failure list."""
    resolved = store.resolve_names(list(SEED_WATCHLIST))
    missing = sorted(name for name, type_id in resolved.items() if type_id is None)
    if missing:
        raise UnresolvedTypeNames(missing)
    return {name: type_id for name, type_id in resolved.items() if type_id is not None}


def seed_watchlist(store: StateStore) -> dict[str, int]:
    """Resolve the seed roster and record it in the watchlist table."""
    resolved = resolve_seed(store)
    store.upsert_watchlist(
        [(type_id, name, SEED_SOURCE) for name, type_id in resolved.items()],
        now_utc(),
    )
    return resolved
