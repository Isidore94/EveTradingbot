"""The bar contract and history ingest.

`EVE_DAILY_BAR_COLUMNS = ["datetime","high","low","close","volume",
"order_count"]` — plan.md §4. **There is no `open` column and none is ever
synthesized.** `close` ← ESI `average`; that mapping happens exactly once, in
`frame_from_history`, and is the only place in the system where ESI field
names appear.

`order_count` is a first-class column here, not decoration: it is a liquidity
floor input, the `avg_trade_size` spoof discriminator, and the participation
baseline that replaces equity RVOL (plan.md §4).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import pandas as pd

from .store.lake import BAR_LAKE_COLUMNS, EVE_DAILY_BAR_COLUMNS, BarLake
from .timeutil import bar_datetime, ensure_utc, iso, last_completed_bar_date, utcnow

__all__ = [
    "BAR_LAKE_COLUMNS",
    "EVE_DAILY_BAR_COLUMNS",
    "HistoryIngestResult",
    "empty_bar_frame",
    "BarFreshness",
    "bar_freshness",
    "frame_from_history",
    "ingest_history",
    "participation",
    "quality_flags",
]


def empty_bar_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_LAKE_COLUMNS)


def frame_from_history(
    rows: Sequence[dict],
    *,
    type_id: int,
    region_id: int,
    fetched_at=None,
    now=None,
) -> pd.DataFrame:
    """The single ESI-history -> bar-contract mapping site.

    close  <- average    high <- highest    low <- lowest
    datetime = the ESI date stamped at 11:00 UTC (the downtime boundary)
    isk_value = volume x close, derived at write (plan.md §3.4)

    **Completed days only, enforced here (§21 R2).** Anything dated after
    `last_completed_bar_date` is a day that has not finished happening, and a
    partial day that reaches the lake can confirm a signal with a bar whose
    high, low and average are all still moving. The rule was written in
    `timeutil` and never applied at ingestion; it is applied here now, at the
    one place ESI rows become bars, so no caller can forget it. Drops are
    counted in `frame.attrs`, never silent.
    """
    if not rows:
        return empty_bar_frame()
    fetched = iso(fetched_at or utcnow())
    cutoff = last_completed_bar_date(now)
    dropped = 0
    records = []
    for row in rows:
        try:
            close = float(row["average"])
            high = float(row["highest"])
            low = float(row["lowest"])
            volume = float(row["volume"])
            order_count = int(row["order_count"])
            moment = bar_datetime(row["date"])
        except (KeyError, TypeError, ValueError):
            # A malformed bar is dropped and counted, never repaired by guess.
            continue
        if moment.date() > cutoff:
            # Today's partial bar, or a future-dated one. Not yet a fact.
            dropped += 1
            continue
        records.append(
            {
                "type_id": int(type_id),
                "region_id": int(region_id),
                "datetime": moment,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "order_count": order_count,
                "isk_value": volume * close,
                "fetched_at": fetched,
            }
        )
    if not records:
        frame = empty_bar_frame()
        frame.attrs["incomplete_dropped"] = dropped
        frame.attrs["last_completed_bar_date"] = cutoff.isoformat()
        return frame
    frame = pd.DataFrame.from_records(records, columns=BAR_LAKE_COLUMNS)
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    frame = frame.sort_values("datetime").reset_index(drop=True)
    frame.attrs["incomplete_dropped"] = dropped
    frame.attrs["last_completed_bar_date"] = cutoff.isoformat()
    return frame


@dataclass(slots=True)
class BarFreshness:
    """Whether the *bars* are current — a fact the order book cannot supply.

    Two independent ways the lake goes stale, and they are not the same
    question:

    * **`bar_age_days`** — how many completed EVE days lie between the newest
      bar and today's. This is what an analyst means by "old data".
    * **`refresh_age_hours`** — how long since ingestion last wrote anything.
      A lake whose history job has been failing for a week still contains a
      bar dated the day it stopped, so bar age alone cannot see the outage.

    Either exceeding its budget is stale, and stale is UNKNOWN, and UNKNOWN
    fails (§4).
    """

    last_bar_date: str | None = None
    bar_age_days: int | None = None
    fetched_at: str | None = None
    refresh_age_hours: float | None = None
    stale: bool = True
    reason: str = "no bars"

    @property
    def known(self) -> bool:
        return self.last_bar_date is not None and not self.stale

    @property
    def label(self) -> str:
        if self.last_bar_date is None:
            return "UNKNOWN"
        return "stale" if self.stale else "fresh"


def bar_freshness(
    frame: pd.DataFrame,
    *,
    now=None,
    max_bar_age_days: int,
    max_refresh_age_hours: float,
) -> BarFreshness:
    """Judge the bars on their own evidence, never on the book's (§21 R2)."""
    moment = ensure_utc(now or utcnow())
    if frame is None or frame.empty or "datetime" not in frame:
        return BarFreshness(reason="no bars in the lake for this type")

    stamps = pd.to_datetime(frame["datetime"], utc=True, errors="coerce").dropna()
    if stamps.empty:
        return BarFreshness(reason="no bars in the lake for this type")
    newest = stamps.max()
    last_bar = newest.date()
    cutoff = last_completed_bar_date(moment)
    age_days = max(0, (cutoff - last_bar).days)

    fetched_iso = None
    refresh_hours = None
    if "fetched_at" in frame:
        fetched = pd.to_datetime(frame["fetched_at"], utc=True, errors="coerce").dropna()
        if not fetched.empty:
            newest_fetch = fetched.max()
            fetched_iso = iso(newest_fetch.to_pydatetime())
            refresh_hours = max(
                0.0, (moment - newest_fetch.to_pydatetime()).total_seconds() / 3600.0
            )

    reasons = []
    if age_days > max_bar_age_days:
        reasons.append(f"bars {age_days} completed day(s) behind")
    if refresh_hours is None:
        reasons.append("no ingestion stamp on these bars")
    elif refresh_hours > max_refresh_age_hours:
        reasons.append(f"last refreshed {refresh_hours:.0f}h ago")

    return BarFreshness(
        last_bar_date=last_bar.isoformat(),
        bar_age_days=age_days,
        fetched_at=fetched_iso,
        refresh_age_hours=refresh_hours,
        stale=bool(reasons),
        reason=" · ".join(reasons),
    )


def quality_flags(frame: pd.DataFrame) -> dict[str, int]:
    """Data-quality counters. `order_count == 0` days are ghosts, not prices.

    Bars flagged here are excluded from ATR/sigma warm-ups by the signal layer
    (plan.md §4); nothing repairs them in place.
    """
    if frame.empty:
        return {"rows": 0, "zero_order_count": 0, "nonpositive_price": 0, "inverted_range": 0}
    return {
        "rows": int(len(frame)),
        "zero_order_count": int((frame["order_count"] <= 0).sum()),
        "nonpositive_price": int((frame["close"] <= 0).sum()),
        "inverted_range": int((frame["high"] < frame["low"]).sum()),
    }


def participation(frame: pd.DataFrame, window: int = 20) -> pd.Series:
    """`order_count` against its own rolling mean — the EVE volume-thrust read.

    A price move on collapsing `order_count` is a thin-book artifact, not
    demand (plan.md §4). This is a *demand-event detector*, never a breakout
    confirmation.
    """
    if frame.empty:
        return pd.Series(dtype="float64")
    counts = pd.to_numeric(frame["order_count"], errors="coerce")
    # The baseline EXCLUDES the current bar: a bar must not dilute its own
    # thrust reading (the equity RVOL convention, kept).
    baseline = counts.shift(1).rolling(window, min_periods=max(2, window // 2)).mean()
    return counts / baseline


@dataclass
class HistoryIngestResult:
    """What one ingest run actually did — including what it did *not* fetch."""

    requested: int = 0
    fetched: int = 0
    skipped_fresh: int = 0
    not_modified: int = 0
    no_history: int = 0
    failed: int = 0
    rows_written: int = 0
    quality: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    missing_type_ids: list[int] = field(default_factory=list)

    def merge_quality(self, flags: dict[str, int]) -> None:
        for key, value in flags.items():
            self.quality[key] = self.quality.get(key, 0) + value

    def as_dict(self) -> dict:
        return {
            "requested": self.requested,
            "fetched": self.fetched,
            "skipped_fresh": self.skipped_fresh,
            "not_modified": self.not_modified,
            "no_history": self.no_history,
            "failed": self.failed,
            "rows_written": self.rows_written,
            "quality": dict(self.quality),
            "errors": self.errors[:20],
            "missing_type_ids": len(self.missing_type_ids),
        }


async def ingest_history(
    client,
    lake: BarLake,
    type_ids: Iterable[int],
    *,
    region_id: int | None = None,
    batch_size: int = 200,
    skip_type_ids: set[int] | None = None,
    on_missing=None,
    progress=None,
) -> HistoryIngestResult:
    """Refresh daily bars for `type_ids` and diff-append them to the lake.

    History expires daily at 11:05 UTC, so a second run on the same day fetches
    nothing at all — every URL is skipped as still-fresh, which is the
    never-fetch-before-expiry invariant doing its job, not a failure.

    **The ESI client is imported here, not at module scope (§21 R8).** Every
    other function in this module is pure analysis over a frame, and the desk
    reaches them through `screen` and `brief` — so a module-level import put
    `httpx` into the GUI's import graph through a chain no direct-import check
    could see.
    """
    from .esi.client import HISTORY_FEED, EsiNotFound

    region = region_id if region_id is not None else client.config.esi.home_region_id
    result = HistoryIngestResult()
    pending: list[pd.DataFrame] = []
    skip = skip_type_ids or set()
    ids = [int(value) for value in type_ids if int(value) not in skip]
    result.requested = len(ids)
    flush_missing_every = 200
    flushed = 0

    for index, type_id in enumerate(ids, start=1):
        try:
            response = await client.get(
                HISTORY_FEED,
                f"/markets/{region}/history",
                params={"type_id": type_id},
            )
        except EsiNotFound:
            # The region's /types list includes ids /history rejects. That is a
            # catalogue gap, recorded so tomorrow's crawl skips it — not a
            # failure, and not something to keep paying 4xx error budget for.
            result.no_history += 1
            result.missing_type_ids.append(type_id)
            continue
        except Exception as exc:  # noqa: BLE001 - recorded, never silently dropped
            result.failed += 1
            result.errors.append(f"type {type_id}: {type(exc).__name__}: {exc}")
            continue
        if response.skipped:
            result.skipped_fresh += 1
        elif response.not_modified:
            result.not_modified += 1
        elif response.usable:
            result.fetched += 1
            frame = frame_from_history(
                response.data, type_id=type_id, region_id=region, fetched_at=response.fetched_at
            )
            result.merge_quality(quality_flags(frame))
            if not frame.empty:
                pending.append(frame)
        if len(pending) >= batch_size:
            result.rows_written += lake.write(pd.concat(pending, ignore_index=True))
            pending.clear()
        if on_missing is not None and len(result.missing_type_ids) >= flush_missing_every:
            # Persist the catalogue gap as we learn it. A crawl killed halfway
            # must not have to rediscover thousands of 404s tomorrow.
            on_missing(result.missing_type_ids[-flush_missing_every:])
            flushed += flush_missing_every
        if progress is not None and index % 100 == 0:
            progress(index, len(ids), result)
        await asyncio.sleep(0)

    if pending:
        result.rows_written += lake.write(pd.concat(pending, ignore_index=True))
    if on_missing is not None and len(result.missing_type_ids) > flushed:
        on_missing(result.missing_type_ids[flushed:])
    return result
