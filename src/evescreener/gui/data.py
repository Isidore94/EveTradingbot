"""The desk's data layer (plan.md §19 Part 2).

**This module is the reason a refresh timer is safe.** It reads the local
Parquet lake, the local state database and the local book snapshot, and it has
no ESI client, no network import and no way to acquire one. A UI timer may
re-read local data as often as it likes; nothing here can cause a fetch before
`Expires`, which is a correctness invariant and a bannable offence to
circumvent (§3.2). The desk therefore *shows* staleness rather than curing it:
a book swept 40 minutes ago renders as 40 minutes old, and pricing off it is
refused exactly as the CLI refuses it.

It imports no Qt either. Every page is built on top of this, so the whole data
path can be tested headless, and the Qt layer stays thin enough to be tested
for wiring rather than for arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..config import Config
from ..indices import IndexSet, Sector, build_index_set, load_sectors, rotation_table
from ..reasons import ReasonVocabulary, load_reasons
from ..setups import Setup, load_setups
from ..signals.composite import Composite, clamp_settings
from ..store.db import Database
from ..store.lake import BarLake, BookLake
from ..timeutil import ensure_utc, parse_iso, utcnow

__all__ = ["DeskData", "desk_input_key", "load_desk"]


def _stat_key(path: Path) -> tuple:
    """`(size, mtime_ns)` for a file, or a marker that it is absent.

    Deliberately a stat and not a hash: the lake is hundreds of megabytes and
    this runs on a timer. A rewritten-but-identical Parquet file would read as
    changed, which costs one recompute — the failure that matters is the other
    direction, and stat never misses a real write.
    """
    try:
        info = path.stat()
    except OSError:
        return ("absent",)
    return (int(info.st_size), int(info.st_mtime_ns))


def desk_input_key(config, region_id: int, *, root: Path | None = None) -> tuple:
    """Everything a page's computation depends on, cheaply.

    Bars, the book snapshot, the operator's setups and reason vocabulary, and
    the newest order-book sweep. No Parquet is parsed and no index is built —
    this has to be affordable every 60 seconds.
    """
    root = root or Path.cwd()
    paths = config.paths
    parts: list[tuple] = [("region", int(region_id))]

    for directory, pattern in ((paths.bars, "**/*.parquet"), (paths.books, "**/*.parquet")):
        try:
            found = sorted(Path(directory).glob(pattern))
        except OSError:
            found = []
        parts.append(tuple((str(item.name), _stat_key(item)) for item in found))

    for name in ("setups.jsonl", "reasons.jsonl", "sectors.jsonl", "anchors.jsonl"):
        parts.append((name, _stat_key(root / "config" / name)))

    return tuple(parts)


@dataclass(slots=True)
class DeskData:
    """One consistent read of everything on disk, with its own age stamps."""

    config: Config
    db: Database
    region_id: int
    loaded_at: object
    bars: pd.DataFrame = field(default_factory=pd.DataFrame)
    all_bars: pd.DataFrame = field(default_factory=pd.DataFrame)
    book: pd.DataFrame = field(default_factory=pd.DataFrame)
    composite: Composite | None = None
    index_set: IndexSet | None = None
    sectors: list[Sector] = field(default_factory=list)
    setups: list[Setup] = field(default_factory=list)
    vocabulary: ReasonVocabulary = field(default_factory=lambda: ReasonVocabulary(reasons=()))
    anchor_dates: list[str] = field(default_factory=list)
    tiers: dict[int, str] = field(default_factory=dict)
    watch_ids: set[int] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    #: Cheap fingerprint of everything a page's computation depends on
    #: (`gui/work.desk_input_key`). Pages recompute only when it moves.
    input_key: tuple = ()

    # -- threads -----------------------------------------------------------
    def thread_local_db(self) -> Database:
        """A fresh connection for a worker thread.

        sqlite3 connections belong to the thread that opened them, so a page
        computing off the GUI thread opens its own and closes it when done.
        The work is read-only against a WAL database, and concurrent readers
        are exactly what WAL is for.
        """
        return Database(self.config.paths.db)

    # -- names -------------------------------------------------------------
    def type_name(self, type_id: int) -> str:
        row = self.db.type_by_id(int(type_id))
        return row["name"] if row else f"type {int(type_id)}"

    def tier(self, type_id: int) -> str | None:
        return self.tiers.get(int(type_id))

    def frame_for(self, type_id: int) -> pd.DataFrame:
        """One type's bars, newest last. Watchlist names come from the whole
        lake, not the tracked slice — an operator's name renders even below
        the floor (§11 D4)."""
        source = self.all_bars if not self.all_bars.empty else self.bars
        if source.empty:
            return pd.DataFrame()
        frame = source[source["type_id"] == int(type_id)]
        return frame.sort_values("datetime").reset_index(drop=True)

    # -- freshness ---------------------------------------------------------
    @property
    def book_sweep_ts(self) -> str | None:
        if self.book is None or self.book.empty or "sweep_ts" not in self.book:
            return None
        stamps = self.book["sweep_ts"].dropna()
        return str(stamps.max()) if not stamps.empty else None

    @property
    def book_age_minutes(self) -> float | None:
        stamp = parse_iso(self.book_sweep_ts) if self.book_sweep_ts else None
        if stamp is None:
            return None
        return max(0.0, (ensure_utc(self.loaded_at) - stamp).total_seconds() / 60.0)

    @property
    def book_is_stale(self) -> bool:
        """UNKNOWN counts as stale: an unmeasurable book is not a fresh one."""
        age = self.book_age_minutes
        if age is None:
            return True
        return age > self.config.costs.book_staleness_minutes

    @property
    def last_bar(self) -> str | None:
        if self.bars.empty:
            return None
        return str(self.bars["datetime"].max())

    def rotation(self) -> list[dict]:
        if self.index_set is None:
            return []
        return rotation_table(self.index_set)

    def as_dict(self) -> dict:
        return {
            "region_id": self.region_id,
            "types": int(self.bars["type_id"].nunique()) if not self.bars.empty else 0,
            "bars": int(len(self.bars)),
            "book_sweep_ts": self.book_sweep_ts,
            "book_age_minutes": self.book_age_minutes,
            "setups": len(self.setups),
            "sectors": len(self.sectors),
            "notes": self.notes,
        }


def load_desk(
    config: Config,
    *,
    region_id: int | None = None,
    root: Path | None = None,
    now=None,
) -> DeskData:
    """Read everything the desk needs, once. Local files only — never ESI."""
    from ..signals.anchors import load_anchors
    from ..signals.composite import TURNOVER, build_composite
    from ..universe import index_eligible_type_ids, tracked_type_ids, watchlist_type_ids

    root = root or Path.cwd()
    region = region_id or config.esi.home_region_id
    db = Database(config.paths.db)
    notes: list[str] = []

    all_bars = BarLake(config.paths).read(region)
    tracked = tracked_type_ids(db, region)
    bars = all_bars
    if tracked and not all_bars.empty:
        bars = all_bars[all_bars["type_id"].isin(tracked)]
    elif not tracked:
        notes.append(
            "the universe has never been synced, so every type in the lake is shown; "
            "run `census` to apply the membership floor"
        )

    eligible = index_eligible_type_ids(db, region)
    composite = None
    index_set = None
    sectors = load_sectors(root / "config" / "sectors.jsonl")
    if not bars.empty:
        composite = build_composite(
            bars,
            members=config.signals.composite_members,
            single_cap=config.signals.composite_single_weight_cap,
            rebalance_days=config.signals.composite_rebalance_days,
            **clamp_settings(config.signals),
            weighting=TURNOVER,
            member_ids=eligible or None,
            ticker="FORGE",
            name="Forge Composite",
        )
        volumes = {
            int(row["type_id"]): float(row["median_unit_volume"] or 0.0)
            for row in db.conn.execute(
                "SELECT type_id, median_unit_volume FROM universe WHERE region_id=?", (region,)
            )
        }
        index_set = build_index_set(
            config,
            db,
            bars,
            member_ids=eligible or None,
            unit_volume=volumes,
            sectors=sectors,
        )

    return DeskData(
        config=config,
        db=db,
        region_id=region,
        loaded_at=ensure_utc(now or utcnow()),
        bars=bars,
        all_bars=all_bars,
        book=BookLake(config.paths).latest(region),
        composite=composite,
        index_set=index_set,
        sectors=sectors,
        setups=load_setups(root / "config" / "setups.jsonl"),
        vocabulary=load_reasons(root / "config" / "reasons.jsonl"),
        anchor_dates=[
            anchor.anchor_date.isoformat()
            for anchor in load_anchors(root / "config" / "anchors.jsonl")
            if anchor.confirmed
        ],
        tiers={
            int(row["type_id"]): row["tier"]
            for row in db.conn.execute(
                "SELECT type_id, tier FROM universe WHERE region_id=?", (region,)
            )
        },
        watch_ids=set(watchlist_type_ids(db)),
        notes=notes,
        input_key=desk_input_key(config, region, root=root),
    )
