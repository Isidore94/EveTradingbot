"""Order-book sweeps, reduced on write (plan.md §3.4).

A full Forge sweep is ~400k orders. Nothing downstream needs a raw order, so
raw pages are never persisted: each sweep is reduced in memory to one row per
``(type_id, region_id, side)`` and only that reduction reaches the lake.

Two anti-bait rules are built into the reduction rather than left to the
ranker (§9 R2): ``p5_price`` (volume-weighted mean of the best 5% of resting
volume) and ``depth_fill_price`` at real ISK notionals, which prices a bait
order in and dilutes it instead of being fooled by it. ``top_order_volume_share``
is carried so a book that is really one order can be flagged.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .clock import now_utc
from .config import Config
from .esi import ORDERS_GROUP, EsiClient
from .paths import DataPaths, atomic_write_path

# Upwell structures carry 13-digit IDs; NPC stations are far below this (§9 R3).
NPC_STATION_ID_CEILING = 1_000_000_000_000

P5_FRACTION = 0.05

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
    "top_order_volume_share",
    "station_volume_share",
]


@dataclass
class SweepResult:
    """One sweep, with its data-quality counters kept as counters, not errors."""

    region_id: int
    sweep_ts: dt.datetime
    expires_ts: dt.datetime | None
    pages: int = 0
    pages_fetched: int = 0
    pages_skipped_fresh: int = 0
    pages_not_modified: int = 0
    orders: int = 0
    duplicate_order_ids: int = 0
    tokens_charged: int = 0
    types: int = 0
    summary: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())


def orders_path(region_id: int) -> str:
    return f"/markets/{region_id}/orders"


def _walk(
    prices: np.ndarray, volumes: np.ndarray, notional: float
) -> tuple[float, int]:
    """Walk a price-sorted book until ``notional`` ISK is transacted.

    Returns ``(effective_unit_price, units)``. If the whole book cannot absorb
    ``notional``, the price is NaN and the units are what the book does hold —
    "cannot fill at this size" is a distinct answer from "fills at price X",
    and the screen must never confuse the two (§5, §9 R5).
    """
    if prices.size == 0 or notional <= 0:
        return float("nan"), 0
    line_value = prices * volumes
    cumulative = np.cumsum(line_value)
    if cumulative[-1] < notional:
        return float("nan"), int(volumes.sum())
    index = int(np.searchsorted(cumulative, notional))
    spent_before = cumulative[index - 1] if index else 0.0
    units_before = int(volumes[:index].sum())
    remainder = notional - spent_before
    partial_units = remainder / prices[index]
    units = units_before + partial_units
    return float(notional / units), int(np.floor(units))


def _percentile_price(prices: np.ndarray, volumes: np.ndarray) -> float:
    """Volume-weighted mean price of the best 5% of resting volume."""
    total = volumes.sum()
    if total <= 0:
        return float("nan")
    target = total * P5_FRACTION
    cumulative = np.cumsum(volumes)
    index = int(np.searchsorted(cumulative, target))
    index = min(index, volumes.size - 1)
    taken = volumes[: index + 1].astype("float64").copy()
    overshoot = cumulative[index] - target
    if overshoot > 0:
        taken[index] -= overshoot
    weight = taken.sum()
    if weight <= 0:
        return float(prices[0])
    return float((prices[: index + 1] * taken).sum() / weight)


def reduce_orders(
    orders: pd.DataFrame,
    *,
    region_id: int,
    sweep_ts: dt.datetime,
    expires_ts: dt.datetime | None,
    notional_tiers: tuple[float, ...],
) -> pd.DataFrame:
    """Reduce raw orders to one ``book_summary`` row per (type, region, side)."""
    tier_columns: list[str] = []
    for index in range(len(notional_tiers)):
        tier_columns += [f"depth_fill_price_{index + 1}", f"depth_fill_qty_{index + 1}"]
    columns = BOOK_SUMMARY_COLUMNS + tier_columns

    if orders.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for (type_id, is_buy), group in orders.groupby(["type_id", "is_buy_order"]):
        # Sell orders fill cheapest-first; buy orders fill highest-bid-first.
        ascending = not bool(is_buy)
        group = group.sort_values("price", ascending=ascending, kind="stable")
        prices = group["price"].to_numpy(dtype="float64")
        volumes = group["volume_remain"].to_numpy(dtype="int64")
        total_volume = int(volumes.sum())

        row: dict[str, object] = {
            "type_id": int(type_id),
            "region_id": region_id,
            "side": "buy" if is_buy else "sell",
            "sweep_ts": sweep_ts,
            "expires_ts": expires_ts,
            "best_price": float(prices[0]),
            "total_volume": total_volume,
            "order_count": int(len(group)),
            "p5_price": _percentile_price(prices, volumes),
            "top_order_volume_share": (
                float(volumes.max() / total_volume) if total_volume else float("nan")
            ),
            "station_volume_share": (
                float(
                    volumes[
                        group["location_id"].to_numpy() < NPC_STATION_ID_CEILING
                    ].sum()
                    / total_volume
                )
                if total_volume
                else float("nan")
            ),
        }
        for index, notional in enumerate(notional_tiers, start=1):
            price, qty = _walk(prices, volumes, notional)
            row[f"depth_fill_price_{index}"] = price
            row[f"depth_fill_qty_{index}"] = qty
        rows.append(row)

    return pd.DataFrame(rows, columns=columns).sort_values(
        ["type_id", "side"], ignore_index=True
    )


def write_book_summary(
    paths: DataPaths, region_id: int, summary: pd.DataFrame, sweep_ts: dt.datetime
) -> int:
    """Append a sweep's reduction to that day's Parquet partition, atomically."""
    if summary.empty:
        return 0
    target = paths.books_partition(region_id, sweep_ts.date().isoformat())
    frame = summary
    if target.exists():
        frame = pd.concat([pd.read_parquet(target), summary], ignore_index=True)
    frame = frame.drop_duplicates(
        subset=["type_id", "region_id", "side", "sweep_ts"], keep="last"
    ).reset_index(drop=True)
    with atomic_write_path(target) as tmp:
        frame.to_parquet(tmp, index=False)
    return len(summary)


def latest_book_summary(paths: DataPaths, region_id: int) -> pd.DataFrame:
    """The most recent sweep's rows, or an empty frame if none exist."""
    partition_dir = paths.books_dir / f"region={region_id}"
    if not partition_dir.exists():
        return pd.DataFrame()
    partitions = sorted(partition_dir.glob("date=*.parquet"))
    if not partitions:
        return pd.DataFrame()
    frame = pd.read_parquet(partitions[-1])
    if frame.empty:
        return frame
    newest = frame["sweep_ts"].max()
    return frame[frame["sweep_ts"] == newest].reset_index(drop=True)


async def sweep_books(client: EsiClient, config: Config) -> SweepResult:
    """Fetch every page of the region order book once and reduce it.

    One sweep per cache window, reconciled by ``order_id``: pages are fetched
    across a few seconds and CCP does not promise they come from one snapshot
    (open check #2), so cross-page duplicates are counted as a data-quality
    signal rather than treated as an error.
    """
    region_id = config.market.region_id
    path = orders_path(region_id)
    sweep_ts = now_utc()

    first = await client.get(path, params={"page": 1}, group=ORDERS_GROUP)
    result = SweepResult(
        region_id=region_id,
        sweep_ts=sweep_ts,
        expires_ts=first.expires_at,
        pages=first.pages or 1,
        tokens_charged=first.tokens_charged,
    )
    _count_page(result, first.outcome)
    if not first.is_usable:
        return result

    pages: list[list[dict]] = [first.data]
    semaphore = asyncio.Semaphore(config.esi.orders_concurrency)

    async def fetch(page: int) -> None:
        async with semaphore:
            response = await client.get(path, params={"page": page}, group=ORDERS_GROUP)
        result.tokens_charged += response.tokens_charged
        _count_page(result, response.outcome)
        if response.is_usable:
            pages.append(response.data)

    if result.pages > 1:
        await asyncio.gather(*(fetch(page) for page in range(2, result.pages + 1)))

    orders = pd.DataFrame([order for page in pages for order in page])
    result.orders = len(orders)
    if not orders.empty:
        deduped = orders.drop_duplicates(subset="order_id", keep="first")
        result.duplicate_order_ids = len(orders) - len(deduped)
        orders = deduped

    result.summary = reduce_orders(
        orders,
        region_id=region_id,
        sweep_ts=sweep_ts,
        expires_ts=first.expires_at,
        notional_tiers=config.market.notional_tiers_isk,
    )
    result.types = (
        int(result.summary["type_id"].nunique()) if not result.summary.empty else 0
    )
    write_book_summary(config.paths, region_id, result.summary, sweep_ts)
    return result


def _count_page(result: SweepResult, outcome: str) -> None:
    if outcome == "skipped_fresh":
        result.pages_skipped_fresh += 1
    elif outcome == "not_modified":
        result.pages_not_modified += 1
    else:
        result.pages_fetched += 1
