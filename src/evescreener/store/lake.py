"""The Parquet lake: daily bars and reduced book summaries.

Pandas in, pandas out (plan.md §3.5). Bars are partitioned
`bars/region=…/year=….parquet`; reduced book sweeps are partitioned
`books/region=…/date=….parquet`. Raw order pages are **never** persisted
(§3.4) — only the reduction.

Every write is atomic: a failed publish leaves the last verified partition
exactly as it was.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd

from ..paths import DataPaths, atomic_write_bytes

# The frozen bar contract (plan.md §4). There is no `open` column and none is
# ever synthesized. `close` is ESI `average`; the mapping happens once, in
# `bars.frame_from_history`, and nowhere else.
EVE_DAILY_BAR_COLUMNS = ["datetime", "high", "low", "close", "volume", "order_count"]
BAR_LAKE_COLUMNS = [
    "type_id",
    "region_id",
    *EVE_DAILY_BAR_COLUMNS,
    "isk_value",
    "fetched_at",
]
BAR_KEY = ["type_id", "region_id", "datetime"]

BOOK_SUMMARY_COLUMNS = [
    "type_id",
    "region_id",
    "side",
    "sweep_ts",
    "expires_ts",
    "best_price",
    "total_volume",
    "order_count",
    "p5_price",
    "depth_fill_price_0",
    "depth_fill_price_1",
    "depth_fill_price_2",
    "depth_fill_qty_0",
    "depth_fill_qty_1",
    "depth_fill_qty_2",
    "top_order_volume_share",
    "station_volume_share",
    "partial_sweep",
    # -- R1: what makes a quote executable (plan.md §21 R1) ----------------
    # `best_*` are REGION-WIDE extrema and are diagnostics only: the lowest
    # ask and the highest bid in a region are routinely at different stations
    # and cannot be traded against each other. `exec_*` is the quote a single
    # character could actually hit, at one named venue.
    "best_location_id",
    "best_range",
    "exec_location_id",
    "exec_price",
    "exec_volume",
    "exec_order_count",
    "exec_is_structure",
    # Share of this side's volume that is actually reachable at the executable
    # venue. Supersedes `station_volume_share` as the accessibility measure:
    # CCP matches a buy order by its RANGE from its own location, so NPC
    # ownership is not the question (§22 S2a).
    "exec_reachable_volume_share",
    # -- region-wide diagnostics -------------------------------------------
    # The pre-S2a readings, kept so the correction stays auditable. They
    # describe the whole region and must never be used to price a fill.
    "region_p5_price",
    "region_depth_fill_price_0",
    "region_depth_fill_price_1",
    "region_depth_fill_price_2",
    "region_depth_fill_qty_0",
    "region_depth_fill_qty_1",
    "region_depth_fill_qty_2",
    "region_top_order_volume_share",
]

#: Columns without which a snapshot cannot be priced at all. A partition
#: written before R1 lacks them, and genuinely does not know where its quotes
#: rested — so it is UNKNOWN rather than retro-fitted with a guess.
EXECUTABLE_COLUMNS = ["exec_location_id", "exec_price", "exec_is_structure"]
BOOK_KEY = ["type_id", "region_id", "side", "sweep_ts"]


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    buffer = BytesIO()
    frame.to_parquet(buffer, index=False, engine="pyarrow", compression="snappy")
    atomic_write_bytes(path, buffer.getvalue())


def _read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path, engine="pyarrow")


def _merge(existing: pd.DataFrame, incoming: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    if existing.empty:
        merged = incoming
    else:
        merged = pd.concat([existing, incoming], ignore_index=True)
    merged = merged.drop_duplicates(subset=key, keep="last")
    return merged.sort_values(key).reset_index(drop=True)


class BarLake:
    """Daily bars, partitioned by region and year."""

    def __init__(self, paths: DataPaths) -> None:
        self.paths = paths

    def write(self, frame: pd.DataFrame) -> int:
        """Diff-append `frame`; returns the number of rows newly written.

        A re-crawl is cheap on write because only unseen `(type, region, date)`
        rows change anything (plan.md §3.2).
        """
        if frame.empty:
            return 0
        frame = frame.loc[:, [column for column in BAR_LAKE_COLUMNS if column in frame.columns]]
        frame = frame.copy()
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
        written = 0
        for (region_id, year), chunk in frame.groupby(
            [frame["region_id"], frame["datetime"].dt.year]
        ):
            path = self.paths.bars_partition(int(region_id), int(year))
            existing = _read_parquet(path)
            before = len(existing)
            merged = _merge(existing, chunk, BAR_KEY)
            if len(merged) != before or existing.empty or not merged.equals(existing):
                _write_parquet(path, merged)
            written += max(0, len(merged) - before)
        return written

    def read(
        self,
        region_id: int,
        *,
        type_ids: Iterable[int] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        directory = self.paths.bars / f"region={region_id}"
        if not directory.exists():
            return pd.DataFrame(columns=BAR_LAKE_COLUMNS)
        frames = [_read_parquet(path) for path in sorted(directory.glob("year=*.parquet"))]
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            return pd.DataFrame(columns=BAR_LAKE_COLUMNS)
        frame = pd.concat(frames, ignore_index=True)
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
        if type_ids is not None:
            wanted = set(int(value) for value in type_ids)
            frame = frame[frame["type_id"].isin(wanted)]
        if start is not None:
            frame = frame[frame["datetime"] >= start]
        if end is not None:
            frame = frame[frame["datetime"] <= end]
        return frame.sort_values(BAR_KEY).reset_index(drop=True)

    def type_ids(self, region_id: int) -> list[int]:
        frame = self.read(region_id)
        if frame.empty:
            return []
        return sorted(int(value) for value in frame["type_id"].unique())

    def latest_date(self, region_id: int) -> pd.Timestamp | None:
        frame = self.read(region_id)
        if frame.empty:
            return None
        return frame["datetime"].max()


class BookLake:
    """Reduced book summaries, partitioned by region and sweep date."""

    def __init__(self, paths: DataPaths) -> None:
        self.paths = paths

    def write(self, frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        frame = frame.copy()
        frame["sweep_ts"] = pd.to_datetime(frame["sweep_ts"], utc=True)
        written = 0
        for (region_id, day), chunk in frame.groupby(
            [frame["region_id"], frame["sweep_ts"].dt.strftime("%Y-%m-%d")]
        ):
            path = self.paths.books_partition(int(region_id), str(day))
            existing = _read_parquet(path)
            before = len(existing)
            merged = _merge(existing, chunk, BOOK_KEY)
            _write_parquet(path, merged)
            written += max(0, len(merged) - before)
        return written

    def read_day(self, region_id: int, day: str) -> pd.DataFrame:
        return _read_parquet(self.paths.books_partition(region_id, day))

    def read_range(self, region_id: int, days: Iterable[str]) -> pd.DataFrame:
        frames = [self.read_day(region_id, day) for day in days]
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            return pd.DataFrame(columns=BOOK_SUMMARY_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def write_partial(self, frame: pd.DataFrame) -> int:
        """Persist an incomplete sweep for diagnostics only.

        A partial sweep is missing pages, and a missing page can hold the true
        best level — so it is not a cheap book, it is an unmeasured one. It is
        written under a filename `latest()` does not glob, which is what keeps
        a failed refresh from displacing the last complete snapshot (§21 R1).
        """
        if frame.empty:
            return 0
        frame = frame.copy()
        frame["sweep_ts"] = pd.to_datetime(frame["sweep_ts"], utc=True)
        written = 0
        for (region_id, day), chunk in frame.groupby(
            [frame["region_id"], frame["sweep_ts"].dt.strftime("%Y-%m-%d")]
        ):
            directory = self.paths.books / f"region={int(region_id)}"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"partial-date={day}.parquet"
            existing = _read_parquet(path)
            before = len(existing)
            merged = _merge(existing, chunk, BOOK_KEY)
            _write_parquet(path, merged)
            written += max(0, len(merged) - before)
        return written

    def latest(self, region_id: int) -> pd.DataFrame:
        """The most recent **complete** sweep in the region, or an empty frame.

        Complete-only is structural rather than advisory. Every consumer that
        prices anything reaches the book through here, so filtering partial
        rows at this one point is what makes "a failed or partial refresh
        never replaces the last verified snapshot" true for all of them at
        once, instead of a rule each caller has to remember (§21 R1).

        Callers still decide staleness themselves — see `load_validated_book`,
        which is the contract that decides both.
        """
        empty = pd.DataFrame(columns=BOOK_SUMMARY_COLUMNS)
        directory = self.paths.books / f"region={region_id}"
        if not directory.exists():
            return empty
        partitions = sorted(directory.glob("date=*.parquet"))
        if not partitions:
            return empty
        # Newest partition first, and within it newest sweep first: return the
        # newest snapshot that is complete, not merely the newest one.
        for path in reversed(partitions):
            frame = _read_parquet(path)
            if frame.empty:
                continue
            frame["sweep_ts"] = pd.to_datetime(frame["sweep_ts"], utc=True)
            if "partial_sweep" in frame:
                frame = frame[~frame["partial_sweep"].fillna(False).astype(bool)]
            if frame.empty:
                continue
            newest = frame["sweep_ts"].max()
            return frame[frame["sweep_ts"] == newest].reset_index(drop=True)
        return empty
