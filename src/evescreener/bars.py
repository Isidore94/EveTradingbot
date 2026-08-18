"""The daily bar contract and the history ingest (plan.md §4, §3.2).

There is no ``open`` column and none is ever synthesized. ESI publishes a
whole-day trade-derived ``average``, which *is* the day's typical price; a
manufactured open would launder uncertainty into confirmation, and would put a
value outside the day's high-low range into frames that assume otherwise.

``close <- average`` is mapped in exactly one place: ``_FIELD_MAP`` below.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from .clock import UTC, bar_datetime, last_completed_bar_date, now_utc
from .config import Config
from .esi import EsiClient
from .paths import DataPaths, atomic_write_path

EVE_DAILY_BAR_COLUMNS = [
    "datetime",
    "high",
    "low",
    "close",
    "volume",
    "order_count",
]

# The single mapping site. ESI history field -> bar contract column (§4).
_FIELD_MAP = {
    "average": "close",
    "highest": "high",
    "lowest": "low",
    "volume": "volume",
    "order_count": "order_count",
}

IDENTITY_COLUMNS = ["type_id", "region_id", "date"]
PROVENANCE_COLUMNS = ["fetched_at", "last_modified"]
LAKE_COLUMNS = (
    IDENTITY_COLUMNS + EVE_DAILY_BAR_COLUMNS + ["isk_value"] + PROVENANCE_COLUMNS
)


@dataclass
class IngestResult:
    """What one history ingest run did, and what it refused to do."""

    types_requested: int = 0
    types_fetched: int = 0
    types_skipped_fresh: int = 0
    types_not_modified: int = 0
    rows_written: int = 0
    partial_bars_dropped: int = 0
    zero_order_count_bars: int = 0
    tokens_charged: int = 0
    failures: dict[int, str] = field(default_factory=dict)


def history_path(region_id: int) -> str:
    return f"/markets/{region_id}/history"


def bars_from_history(
    payload: list[dict],
    *,
    type_id: int,
    region_id: int,
    fetched_at: dt.datetime,
    last_modified: str | None,
    as_of: dt.datetime | None = None,
) -> tuple[pd.DataFrame, int]:
    """Map an ESI history payload onto the bar contract.

    Returns ``(frame, partial_bars_dropped)``. Completed bars only: a row dated
    on or after :func:`last_completed_bar_date` is still accumulating and is
    dropped, never carried as if it were a finished day.
    """
    cutoff = last_completed_bar_date(as_of)
    rows = []
    dropped = 0
    for entry in payload:
        bar_date = dt.date.fromisoformat(entry["date"])
        if bar_date > cutoff:
            dropped += 1
            continue
        row = {"type_id": type_id, "region_id": region_id, "date": bar_date}
        row["datetime"] = bar_datetime(bar_date)
        for source, column in _FIELD_MAP.items():
            row[column] = entry[source]
        rows.append(row)

    frame = pd.DataFrame(rows, columns=LAKE_COLUMNS)
    if rows:
        frame["isk_value"] = frame["volume"] * frame["close"]
        frame["fetched_at"] = fetched_at
        frame["last_modified"] = last_modified
    return _typed(frame), dropped


def _typed(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.astype(
            {
                "type_id": "int64",
                "region_id": "int64",
                "high": "float64",
                "low": "float64",
                "close": "float64",
                "volume": "int64",
                "order_count": "int64",
                "isk_value": "float64",
            },
            errors="ignore",
        )
    frame = frame.copy()
    frame["type_id"] = frame["type_id"].astype("int64")
    frame["region_id"] = frame["region_id"].astype("int64")
    frame["volume"] = frame["volume"].astype("int64")
    frame["order_count"] = frame["order_count"].astype("int64")
    for column in ("high", "low", "close", "isk_value"):
        frame[column] = frame[column].astype("float64")
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    frame["fetched_at"] = pd.to_datetime(frame["fetched_at"], utc=True)
    return frame[LAKE_COLUMNS]


def write_bars(paths: DataPaths, region_id: int, frame: pd.DataFrame) -> int:
    """Merge ``frame`` into the year-partitioned Parquet store, atomically.

    Existing rows win on nothing: a re-fetch of the same day replaces the stored
    row, so a corrected bar propagates. A failed write leaves the previous
    partition untouched (§3.3).
    """
    if frame.empty:
        return 0
    written = 0
    frame = frame.copy()
    frame["_year"] = pd.to_datetime(frame["datetime"], utc=True).dt.year
    for year, chunk in frame.groupby("_year", sort=True):
        chunk = chunk.drop(columns="_year")
        target = paths.bars_partition(region_id, int(year))
        if target.exists():
            existing = pd.read_parquet(target)
            chunk = pd.concat([existing, chunk], ignore_index=True)
        chunk = (
            chunk.drop_duplicates(subset=IDENTITY_COLUMNS, keep="last")
            .sort_values(IDENTITY_COLUMNS)
            .reset_index(drop=True)
        )
        with atomic_write_path(target) as tmp:
            chunk.to_parquet(tmp, index=False)
        written += len(chunk)
    return written


def read_bars(
    paths: DataPaths, region_id: int, *, type_ids: list[int] | None = None
) -> pd.DataFrame:
    """Read every stored bar for ``region_id``, optionally filtered by type."""
    partition_dir = paths.bars_dir / f"region={region_id}"
    if not partition_dir.exists():
        return _typed(pd.DataFrame(columns=LAKE_COLUMNS))
    frames = [
        pd.read_parquet(path) for path in sorted(partition_dir.glob("year=*.parquet"))
    ]
    if not frames:
        return _typed(pd.DataFrame(columns=LAKE_COLUMNS))
    frame = pd.concat(frames, ignore_index=True)
    if type_ids is not None:
        frame = frame[frame["type_id"].isin(type_ids)]
    return frame.sort_values(IDENTITY_COLUMNS).reset_index(drop=True)


async def ingest_history(
    client: EsiClient,
    config: Config,
    type_ids: list[int],
    *,
    as_of: dt.datetime | None = None,
) -> IngestResult:
    """Refresh daily bars for ``type_ids`` in the configured region.

    Paced at the configured history request rate; the endpoint sits outside the
    token regime and CCP names developer-app termination as the sanction for
    exceeding its 300/min (§3.2).
    """
    result = IngestResult(types_requested=len(type_ids))
    region_id = config.market.region_id
    path = history_path(region_id)
    collected: list[pd.DataFrame] = []

    for type_id in sorted(type_ids):
        try:
            response = await client.get(path, params={"type_id": type_id}, paced=True)
        except Exception as exc:  # a failed type never aborts the whole run
            result.failures[type_id] = repr(exc)
            continue

        result.tokens_charged += response.tokens_charged
        if response.outcome == "skipped_fresh":
            result.types_skipped_fresh += 1
        elif response.outcome == "not_modified":
            result.types_not_modified += 1
        else:
            result.types_fetched += 1

        if not response.is_usable:
            result.failures[type_id] = f"no body available ({response.outcome})"
            continue

        frame, dropped = bars_from_history(
            response.data,
            type_id=type_id,
            region_id=region_id,
            fetched_at=response.fetched_at,
            last_modified=response.last_modified,
            as_of=as_of or now_utc(),
        )
        result.partial_bars_dropped += dropped
        if not frame.empty:
            result.zero_order_count_bars += int((frame["order_count"] == 0).sum())
            collected.append(frame)

    if collected:
        merged = pd.concat(collected, ignore_index=True)
        result.rows_written = write_bars(config.paths, region_id, merged)
    return result


def turnover_stats(
    frame: pd.DataFrame, *, days: int = 30, as_of: dt.datetime | None = None
) -> pd.DataFrame:
    """Per-type median ISK turnover and order_count over the last ``days`` bars.

    Median, not mean: one wash-trade day should not decide whether a name is
    liquid (§3.6, §4).
    """
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "type_id",
                "median_isk_value_30d",
                "median_order_count_30d",
                "bars",
            ]
        )
    cutoff = (as_of or now_utc()).astimezone(UTC) - dt.timedelta(days=days)
    recent = frame[frame["datetime"] >= cutoff]
    grouped = recent.groupby("type_id", sort=True)
    stats = pd.DataFrame(
        {
            "median_isk_value_30d": grouped["isk_value"].median(),
            "median_order_count_30d": grouped["order_count"].median(),
            "bars": grouped.size(),
        }
    ).reset_index()
    return stats
