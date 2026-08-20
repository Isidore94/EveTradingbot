"""The tradeable universe and the liquidity floor (plan.md §3.6).

`published types ∩ /markets/{region}/types`, annotated with 30-day median ISK
turnover and median `order_count` from the lake. The **tracked** universe is
what clears the floor. Types entering are auto-added; types falling out are
**flagged, never silently dropped** — the candidate-registry invariant, and
the reason a name that stops trading shows up as a change rather than a gap.

Share-count thresholds are meaningless across items whose unit prices span
twelve orders of magnitude; ISK turnover plus `order_count` is the only common
denominator (plan.md §6). There is no market-cap analogue and none is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import Config
from .esi.client import TYPES_FEED, EsiClient
from .store.db import Database
from .store.lake import BarLake
from .timeutil import iso, utcnow

__all__ = [
    "LiquidityStats",
    "UniverseSnapshot",
    "active_type_ids",
    "apply_floor",
    "liquidity_table",
    "sync_universe",
    "tracked_type_ids",
]


@dataclass(slots=True)
class LiquidityStats:
    """What the floor is actually measured on, per type."""

    type_id: int
    median_isk_value: float
    median_order_count: float
    bars: int
    last_bar: pd.Timestamp | None


@dataclass(slots=True)
class UniverseSnapshot:
    region_id: int
    active: int = 0
    measured: int = 0
    tracked: int = 0
    added: list[int] = field(default_factory=list)
    dropped: list[int] = field(default_factory=list)
    floor_isk_value: float = 0.0
    floor_order_count: float = 0.0

    def as_dict(self) -> dict:
        return {
            "region_id": self.region_id,
            "active": self.active,
            "measured": self.measured,
            "tracked": self.tracked,
            "added": len(self.added),
            "dropped": len(self.dropped),
            "floor_isk_value": self.floor_isk_value,
            "floor_order_count": self.floor_order_count,
        }


async def active_type_ids(client: EsiClient, region_id: int) -> tuple[list[int], bool]:
    """Every type with a live order in the region — the universe primitive.

    Returns `(type_ids, fetched)`. `fetched=False` means the list was still
    fresh and we did not ask; the caller falls back to what it already knows
    rather than pretending the universe is empty.
    """
    result = await client.get_all_pages(TYPES_FEED, f"/markets/{region_id}/types")
    if not result.rows:
        return [], False
    return sorted({int(value) for value in result.rows}), True


def liquidity_table(
    lake: BarLake, region_id: int, *, lookback_days: int, as_of: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Median ISK turnover and `order_count` per type over the lookback window.

    Median, not mean: one wash-trade day must not lift a dead item over the
    floor (plan.md §4).
    """
    frame = lake.read(region_id)
    if frame.empty:
        return pd.DataFrame(
            columns=["type_id", "median_isk_value", "median_order_count", "bars", "last_bar"]
        )
    end = as_of if as_of is not None else frame["datetime"].max()
    start = end - pd.Timedelta(days=lookback_days)
    window = frame[(frame["datetime"] > start) & (frame["datetime"] <= end)]
    if window.empty:
        return pd.DataFrame(
            columns=["type_id", "median_isk_value", "median_order_count", "bars", "last_bar"]
        )
    grouped = window.groupby("type_id").agg(
        median_isk_value=("isk_value", "median"),
        median_order_count=("order_count", "median"),
        bars=("close", "size"),
        last_bar=("datetime", "max"),
    )
    return grouped.reset_index()


def apply_floor(
    table: pd.DataFrame, *, min_isk_value: float, min_order_count: float
) -> pd.DataFrame:
    """Tag each measured type as tracked or not. Both conditions must hold."""
    if table.empty:
        return table.assign(tracked=pd.Series(dtype=bool))
    tracked = (table["median_isk_value"] >= min_isk_value) & (
        table["median_order_count"] >= min_order_count
    )
    return table.assign(tracked=tracked)


def sync_universe(
    db: Database,
    region_id: int,
    active_ids: list[int],
    table: pd.DataFrame,
    *,
    min_isk_value: float,
    min_order_count: float,
    source: str = "census",
) -> UniverseSnapshot:
    """Persist the universe. Falling out of the floor is flagged, never erased."""
    snapshot = UniverseSnapshot(
        region_id=region_id,
        active=len(active_ids),
        floor_isk_value=min_isk_value,
        floor_order_count=min_order_count,
    )
    scored = apply_floor(table, min_isk_value=min_isk_value, min_order_count=min_order_count)
    snapshot.measured = int(len(scored))
    measurements = {
        int(row.type_id): (
            float(row.median_isk_value),
            float(row.median_order_count),
            bool(row.tracked),
        )
        for row in scored.itertuples()
    }
    previous = {
        int(row["type_id"]): bool(row["tracked"])
        for row in db.conn.execute(
            "SELECT type_id, tracked FROM universe WHERE region_id=?", (region_id,)
        )
    }
    now = iso(utcnow())
    with db.transaction() as conn:
        for type_id in active_ids:
            isk_value, order_count, tracked = measurements.get(type_id, (None, None, False))
            was_tracked = previous.get(type_id)
            if tracked and not was_tracked:
                snapshot.added.append(type_id)
            if was_tracked and not tracked:
                snapshot.dropped.append(type_id)
            conn.execute(
                "INSERT INTO universe(type_id, region_id, first_seen, last_seen,"
                " median_isk_value, median_order_count, tracked, dropped_at, source)"
                " VALUES(?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(type_id, region_id) DO UPDATE SET"
                " last_seen=excluded.last_seen,"
                " median_isk_value=excluded.median_isk_value,"
                " median_order_count=excluded.median_order_count,"
                " tracked=excluded.tracked,"
                " dropped_at=CASE WHEN excluded.tracked=0 AND universe.tracked=1"
                "   THEN excluded.last_seen ELSE"
                "   CASE WHEN excluded.tracked=1 THEN NULL ELSE universe.dropped_at END END,"
                " source=excluded.source",
                (
                    type_id,
                    region_id,
                    now,
                    now,
                    isk_value,
                    order_count,
                    1 if tracked else 0,
                    None,
                    source,
                ),
            )
        snapshot.tracked = conn.execute(
            "SELECT COUNT(*) AS n FROM universe WHERE region_id=? AND tracked=1", (region_id,)
        ).fetchone()["n"]
    return snapshot


def tracked_type_ids(db: Database, region_id: int) -> list[int]:
    return [
        int(row["type_id"])
        for row in db.conn.execute(
            "SELECT type_id FROM universe WHERE region_id=? AND tracked=1 ORDER BY type_id",
            (region_id,),
        )
    ]


def dropped_type_ids(db: Database, region_id: int) -> list[int]:
    """Types that once cleared the floor and no longer do — flagged, not gone."""
    return [
        int(row["type_id"])
        for row in db.conn.execute(
            "SELECT type_id FROM universe WHERE region_id=? AND tracked=0"
            " AND dropped_at IS NOT NULL ORDER BY type_id",
            (region_id,),
        )
    ]


def seed_watchlist(db: Database, config: Config, resolved: dict[str, int]) -> None:
    """Record the operator watchlist. Entries here are NEVER auto-removed."""
    now = iso(utcnow())
    with db.transaction() as conn:
        for name in config.universe.watchlist:
            type_id = resolved.get(name)
            conn.execute(
                "INSERT INTO watchlist(name, type_id, added_at, resolved_at, note)"
                " VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET"
                " type_id=COALESCE(excluded.type_id, watchlist.type_id),"
                " resolved_at=COALESCE(excluded.resolved_at, watchlist.resolved_at)",
                (name, type_id, now, now if type_id else None, None),
            )


def watchlist_type_ids(db: Database) -> list[int]:
    return [
        int(row["type_id"])
        for row in db.conn.execute(
            "SELECT type_id FROM watchlist WHERE type_id IS NOT NULL ORDER BY type_id"
        )
    ]


def add_watch(db: Database, *, name: str, type_id: int, note: str | None = None) -> dict:
    """Add one operator-entered name. Re-adding updates the note, never duplicates."""
    now = iso(utcnow())
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO watchlist(name, type_id, added_at, resolved_at, note)"
            " VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET"
            " type_id=excluded.type_id, resolved_at=excluded.resolved_at,"
            " note=COALESCE(excluded.note, watchlist.note)",
            (name, int(type_id), now, now, note),
        )
    row = db.conn.execute("SELECT * FROM watchlist WHERE name=?", (name,)).fetchone()
    return dict(row)


def remove_watch(db: Database, name: str) -> bool:
    """Remove one name. This is the ONLY removal path and only the operator
    reaches it — nothing automatic may call this (§11 D4)."""
    cursor = db.conn.execute("DELETE FROM watchlist WHERE name=? COLLATE NOCASE", (name,))
    return cursor.rowcount > 0


def watchlist_entries(db: Database) -> list:
    """Every entry, unresolved names included — they render loudly, not silently."""
    return list(db.conn.execute("SELECT * FROM watchlist ORDER BY name"))
