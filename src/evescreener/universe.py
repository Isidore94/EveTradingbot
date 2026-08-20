"""The tradeable universe and the liquidity floor (plan.md §3.6).

`published types ∩ /markets/{region}/types`, annotated with 30-day median unit
volume, ISK turnover and `order_count` from the lake. Types entering are
auto-added; types falling out are **flagged, never silently dropped** — the
candidate-registry invariant, and the reason a name that stops trading shows
up as a change rather than a gap.

**Membership is decided on median UNIT volume; weighting is decided on ISK
turnover** (§11 D3, amended 2026-08-20). The two questions are different: how
many of a thing change hands is what tells you whether you can get out of it,
while turnover is the only common denominator for how much a thing should
*count* in an index across unit prices spanning twelve orders of magnitude.
Gating on turnover alone admits a name that prints one 50-billion-ISK block a
month; gating on units alone would weight the index ~100% Tritanium. Each
number answers the question it can answer.

Median, never mean, on both: one wash-trade day must not lift a dead item over
the floor (plan.md §4).

Three tiers come out of it:

- `OK`     — cleared `min_median_unit_volume`. Tradeable, index-eligible.
- `THIN`   — between `absolute_min_unit_volume` and the default floor. Carried,
             charted, scanned, and **badged THIN on every surface**; excluded
             from FORGE, because an index should not be moved by something you
             cannot actually get out of.
- `BELOW`  — under the absolute minimum. Not tradeable. It resolves on a direct
             lookup and says why it is not on the board, which is a different
             thing from not existing.

There is no market-cap analogue and none is invented.
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
    "BELOW",
    "OK",
    "THIN",
    "LiquidityStats",
    "UniverseSnapshot",
    "active_type_ids",
    "apply_floor",
    "classify_tier",
    "index_eligible_type_ids",
    "liquidity_table",
    "sync_universe",
    "thin_type_ids",
    "tier_badge",
    "tier_of",
    "tracked_type_ids",
]

OK = "OK"
THIN = "THIN"
BELOW = "BELOW"


def classify_tier(
    median_unit_volume: float | None, *, default_floor: float, absolute_floor: float
) -> str:
    """OK / THIN / BELOW from one median unit volume.

    A type we could not measure is BELOW, not OK: an unmeasured name is
    uncertainty, and uncertainty never reads as a pass (§4).
    """
    if median_unit_volume is None or pd.isna(median_unit_volume):
        return BELOW
    value = float(median_unit_volume)
    if value >= default_floor:
        return OK
    if value >= absolute_floor:
        return THIN
    return BELOW


def tier_badge(tier: str | None) -> str:
    """What the operator sees beside a name in a column. OK prints nothing.

    Short on purpose — it sits in a fixed-width board column. The long form
    ("below the absolute floor; direct lookup only") belongs on the brief,
    where there is room to say why.
    """
    if tier == THIN:
        return THIN
    if tier == BELOW:
        return BELOW
    return ""


def tier_of(db: Database, region_id: int, type_id: int) -> str | None:
    """The stored tier for one type, or None if the universe has never seen it."""
    row = db.conn.execute(
        "SELECT tier FROM universe WHERE type_id=? AND region_id=?", (int(type_id), region_id)
    ).fetchone()
    if row is None or row["tier"] is None:
        return None
    return str(row["tier"])


@dataclass(slots=True)
class LiquidityStats:
    """What the floor is actually measured on, per type."""

    type_id: int
    median_unit_volume: float
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
    index_eligible: int = 0
    price_pinned: int = 0
    thin: int = 0
    below: int = 0
    added: list[int] = field(default_factory=list)
    dropped: list[int] = field(default_factory=list)
    floor_unit_volume: float = 0.0
    absolute_floor_unit_volume: float = 0.0

    def as_dict(self) -> dict:
        return {
            "region_id": self.region_id,
            "active": self.active,
            "measured": self.measured,
            "tracked": self.tracked,
            "index_eligible": self.index_eligible,
            "price_pinned": self.price_pinned,
            "thin": self.thin,
            "below": self.below,
            "added": len(self.added),
            "dropped": len(self.dropped),
            "floor_unit_volume": self.floor_unit_volume,
            "absolute_floor_unit_volume": self.absolute_floor_unit_volume,
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


LIQUIDITY_COLUMNS = [
    "type_id",
    "median_unit_volume",
    "median_isk_value",
    "median_order_count",
    "close_min",
    "close_max",
    "price_pinned",
    "bars",
    "last_bar",
]

# A price that has not moved at all across the window is seeded, not traded:
# an NPC vendor is holding it at a fixed number. The tolerance is a float-noise
# allowance, not a band — anything with a real tick moves further than this.
PINNED_TOLERANCE = 1e-7
# Below this many bars, "the price never moved" is a statement about the sample
# rather than about the item.
PINNED_MIN_BARS = 5


def liquidity_table(
    lake: BarLake, region_id: int, *, lookback_days: int, as_of: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Median unit volume, ISK turnover and `order_count` over the lookback.

    Median, not mean: one wash-trade day must not lift a dead item over the
    floor (plan.md §4).
    """
    frame = lake.read(region_id)
    if frame.empty:
        return pd.DataFrame(columns=LIQUIDITY_COLUMNS)
    end = as_of if as_of is not None else frame["datetime"].max()
    start = end - pd.Timedelta(days=lookback_days)
    window = frame[(frame["datetime"] > start) & (frame["datetime"] <= end)]
    if window.empty:
        return pd.DataFrame(columns=LIQUIDITY_COLUMNS)
    grouped = window.groupby("type_id").agg(
        median_unit_volume=("volume", "median"),
        median_isk_value=("isk_value", "median"),
        median_order_count=("order_count", "median"),
        close_min=("close", "min"),
        close_max=("close", "max"),
        bars=("close", "size"),
        last_bar=("datetime", "max"),
    )
    grouped["price_pinned"] = (
        grouped["close_max"] <= grouped["close_min"] * (1.0 + PINNED_TOLERANCE)
    ) & (grouped["bars"] >= PINNED_MIN_BARS)
    return grouped.reset_index()[LIQUIDITY_COLUMNS]


def apply_floor(
    table: pd.DataFrame, *, min_unit_volume: float, absolute_min_unit_volume: float
) -> pd.DataFrame:
    """Tier each measured type on median UNIT volume (§11 D3, amended).

    Adds `tier` (OK/THIN/BELOW), `tracked` (OK or THIN — what the desk carries)
    and `index_eligible` (OK, price not pinned — what FORGE may hold). THIN
    names stay visible everywhere with their badge; they are simply not
    allowed to move the market index.

    A **price-pinned** type is excluded from the index too: its price is set by
    an NPC vendor, so it contributes a flat line that absorbs weight and
    reports nothing. It stays tradeable and chartable — the operator may well
    want to look at one — it just cannot be part of the market read.
    """
    if table.empty:
        return table.assign(
            tier=pd.Series(dtype="object"),
            tracked=pd.Series(dtype=bool),
            index_eligible=pd.Series(dtype=bool),
        )
    tier = table["median_unit_volume"].map(
        lambda value: classify_tier(
            value, default_floor=min_unit_volume, absolute_floor=absolute_min_unit_volume
        )
    )
    pinned = (
        table["price_pinned"].fillna(False).astype(bool)
        if "price_pinned" in table
        else pd.Series(False, index=table.index)
    )
    return table.assign(
        tier=tier,
        tracked=tier.isin([OK, THIN]),
        index_eligible=tier.eq(OK) & ~pinned,
    )


def sync_universe(
    db: Database,
    region_id: int,
    active_ids: list[int],
    table: pd.DataFrame,
    *,
    min_unit_volume: float,
    absolute_min_unit_volume: float,
    source: str = "census",
) -> UniverseSnapshot:
    """Persist the universe. Falling out of the floor is flagged, never erased."""
    snapshot = UniverseSnapshot(
        region_id=region_id,
        active=len(active_ids),
        floor_unit_volume=min_unit_volume,
        absolute_floor_unit_volume=absolute_min_unit_volume,
    )
    scored = apply_floor(
        table,
        min_unit_volume=min_unit_volume,
        absolute_min_unit_volume=absolute_min_unit_volume,
    )
    snapshot.measured = int(len(scored))
    measurements = {
        int(row.type_id): (
            float(row.median_unit_volume),
            float(row.median_isk_value),
            float(row.median_order_count),
            str(row.tier),
            bool(row.price_pinned),
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
            unit_volume, isk_value, order_count, tier, pinned = measurements.get(
                type_id, (None, None, None, BELOW, False)
            )
            tracked = tier in (OK, THIN)
            was_tracked = previous.get(type_id)
            if tracked and not was_tracked:
                snapshot.added.append(type_id)
            if was_tracked and not tracked:
                snapshot.dropped.append(type_id)
            conn.execute(
                "INSERT INTO universe(type_id, region_id, first_seen, last_seen,"
                " median_unit_volume, median_isk_value, median_order_count, price_pinned,"
                " tier, tracked, dropped_at, source)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(type_id, region_id) DO UPDATE SET"
                " last_seen=excluded.last_seen,"
                " median_unit_volume=excluded.median_unit_volume,"
                " median_isk_value=excluded.median_isk_value,"
                " median_order_count=excluded.median_order_count,"
                " price_pinned=excluded.price_pinned,"
                " tier=excluded.tier,"
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
                    unit_volume,
                    isk_value,
                    order_count,
                    1 if pinned else 0,
                    tier,
                    1 if tracked else 0,
                    None,
                    source,
                ),
            )
        counts = {
            str(row["tier"]): int(row["n"])
            for row in conn.execute(
                "SELECT tier, COUNT(*) AS n FROM universe WHERE region_id=? GROUP BY tier",
                (region_id,),
            )
        }
        snapshot.index_eligible = conn.execute(
            "SELECT COUNT(*) AS n FROM universe WHERE region_id=? AND tier=? AND price_pinned=0",
            (region_id, OK),
        ).fetchone()["n"]
        snapshot.price_pinned = conn.execute(
            "SELECT COUNT(*) AS n FROM universe WHERE region_id=? AND tier=? AND price_pinned=1",
            (region_id, OK),
        ).fetchone()["n"]
    snapshot.thin = counts.get(THIN, 0)
    snapshot.below = counts.get(BELOW, 0)
    snapshot.tracked = counts.get(OK, 0) + snapshot.thin
    return snapshot


def tracked_type_ids(db: Database, region_id: int) -> list[int]:
    """Everything the desk carries — OK and THIN together."""
    return [
        int(row["type_id"])
        for row in db.conn.execute(
            "SELECT type_id FROM universe WHERE region_id=? AND tracked=1 ORDER BY type_id",
            (region_id,),
        )
    ]


def index_eligible_type_ids(db: Database, region_id: int) -> list[int]:
    """What FORGE may hold: OK tier, price not pinned (§11 D3, amended).

    THIN is excluded because you cannot get out of it; an NPC-seeded price is
    excluded because it cannot move. Both are still tracked and chartable.
    """
    return [
        int(row["type_id"])
        for row in db.conn.execute(
            "SELECT type_id FROM universe WHERE region_id=? AND tier=? AND price_pinned=0"
            " ORDER BY type_id",
            (region_id, OK),
        )
    ]


def thin_type_ids(db: Database, region_id: int) -> list[int]:
    """Carried but badged — measurable, chartable, never an index member."""
    return [
        int(row["type_id"])
        for row in db.conn.execute(
            "SELECT type_id FROM universe WHERE region_id=? AND tier=? ORDER BY type_id",
            (region_id, THIN),
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
