"""Order-book sweeps, reduced on write (plan.md §3.4).

A full Forge sweep is ~400k orders; at HOT cadence that is ~10 GB/day of raw
JSON nothing downstream needs. Each sweep is reduced **in memory** to one row
per `(type_id, region_id, side)` and the raw pages are discarded. A single
debug sweep may be persisted for fixture-building; nothing else ever writes
raw orders.

The reduction is designed around one failure mode (plan.md §9 R2): bait and
spoof orders. A 1-unit sell at 10x fair makes a naive best-price screen buy
garbage, and Fuzzwork's 5%-of-volume average — robust to any single small bait
by construction — fails exactly where margins look widest, because in a thin
book the bait *is* the top 5%. So we carry three defences at once:

* `p5_price` — the volume-weighted average of the best 5% of resting volume;
* `depth_fill_price[tier]` — the actual walk at 0.25B / 1.0B / 2.5B ISK, which
  prices the bait in and dilutes it by construction;
* `top_order_volume_share` — the spoof flag; one order owning the book is a
  fact about the book, not a price.

`station_volume_share` quantifies the Upwell structure blind spot (§9 R3):
structure ids are 13-digit, NPC station ids are not.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import pandas as pd

from .config import Config
from .esi.client import ORDERS_FEED, EsiClient
from .store.lake import BOOK_SUMMARY_COLUMNS, BookLake
from .timeutil import iso, utcnow

__all__ = [
    "BookSummaryRow",
    "SweepResult",
    "depth_walk",
    "is_npc_station",
    "p5_price",
    "reduce_orders",
    "sweep_region",
]

# Player-owned Upwell structure ids are 13 digits; NPC stations are 8.
STRUCTURE_ID_FLOOR = 1_000_000_000_000
# Fuzzwork's community statistic: the best 5% of resting volume.
P5_VOLUME_FRACTION = 0.05


def is_npc_station(location_id: int) -> bool:
    """NPC station or player structure? Jita 4-4 is an NPC station."""
    return int(location_id) < STRUCTURE_ID_FLOOR


def _sorted_levels(orders: Sequence[dict], *, is_buy: bool) -> list[tuple[float, float]]:
    """(price, volume) levels, best-first. Asks ascend; bids descend."""
    levels = [
        (float(order["price"]), float(order["volume_remain"]))
        for order in orders
        if order.get("price") is not None and float(order.get("volume_remain") or 0) > 0
    ]
    levels.sort(key=lambda level: level[0], reverse=is_buy)
    return levels


def p5_price(levels: list[tuple[float, float]]) -> float | None:
    """Volume-weighted average price of the best 5% of resting volume."""
    total = sum(volume for _, volume in levels)
    if total <= 0:
        return None
    budget = total * P5_VOLUME_FRACTION
    taken = 0.0
    value = 0.0
    for price, volume in levels:
        chunk = min(volume, budget - taken)
        if chunk <= 0:
            break
        value += price * chunk
        taken += chunk
        if taken >= budget:
            break
    if taken <= 0:
        # Less than one order's worth of the 5% budget: the best order is it.
        price, volume = levels[0]
        return price
    return value / taken


def depth_walk(
    levels: list[tuple[float, float]], notional_isk: float
) -> tuple[float | None, float | None]:
    """Walk the book for `notional_isk`; return (effective unit price, units).

    Returns `(None, None)` when the book cannot absorb the notional at all —
    an un-fillable notional is UNKNOWN, never the best price with a shrug.
    """
    if notional_isk <= 0 or not levels:
        return None, None
    spent = 0.0
    units = 0.0
    for price, volume in levels:
        if price <= 0:
            continue
        affordable_units = (notional_isk - spent) / price
        take = min(volume, affordable_units)
        if take <= 0:
            break
        spent += take * price
        units += take
        if spent >= notional_isk - 1e-6:
            break
    if units <= 0 or spent < notional_isk * 0.999:
        return None, None
    return spent / units, units


@dataclass(slots=True)
class BookSummaryRow:
    type_id: int
    region_id: int
    side: str
    sweep_ts: str
    expires_ts: str | None
    best_price: float | None
    total_volume: float
    order_count: int
    p5_price: float | None
    depth_fill_price: list[float | None]
    depth_fill_qty: list[float | None]
    top_order_volume_share: float | None
    station_volume_share: float | None
    partial_sweep: bool

    def as_record(self) -> dict:
        record = {
            "type_id": self.type_id,
            "region_id": self.region_id,
            "side": self.side,
            "sweep_ts": self.sweep_ts,
            "expires_ts": self.expires_ts,
            "best_price": self.best_price,
            "total_volume": self.total_volume,
            "order_count": self.order_count,
            "p5_price": self.p5_price,
            "top_order_volume_share": self.top_order_volume_share,
            "station_volume_share": self.station_volume_share,
            "partial_sweep": self.partial_sweep,
        }
        for index in range(3):
            record[f"depth_fill_price_{index}"] = (
                self.depth_fill_price[index] if index < len(self.depth_fill_price) else None
            )
            record[f"depth_fill_qty_{index}"] = (
                self.depth_fill_qty[index] if index < len(self.depth_fill_qty) else None
            )
        return record


@dataclass(slots=True)
class SweepResult:
    """One sweep, with its own completeness and duplicate counts stated."""

    region_id: int
    sweep_ts: str
    frame: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=BOOK_SUMMARY_COLUMNS))
    orders_seen: int = 0
    duplicate_order_ids: int = 0
    types: int = 0
    pages_expected: int = 0
    pages_fetched: int = 0
    skipped_fresh: bool = False
    not_modified: bool = False
    structure_volume_share: float | None = None

    @property
    def complete(self) -> bool:
        return self.pages_expected > 0 and self.pages_fetched == self.pages_expected

    @property
    def outcome(self) -> str:
        """One word for what happened. "nothing changed" is not "we got nothing".

        A 304 means the stored sweep is still current — a success. Reporting it
        the same way as an empty or failed sweep would be exactly the kind of
        collapsed distinction this system exists to avoid.
        """
        if self.not_modified:
            return "not_modified"
        if self.skipped_fresh:
            return "skipped_fresh"
        if self.complete:
            return "complete"
        if self.orders_seen:
            return "partial"
        return "empty"

    def as_dict(self) -> dict:
        return {
            "region_id": self.region_id,
            "sweep_ts": self.sweep_ts,
            "outcome": self.outcome,
            "orders_seen": self.orders_seen,
            "duplicate_order_ids": self.duplicate_order_ids,
            "types": self.types,
            "pages_expected": self.pages_expected,
            "pages_fetched": self.pages_fetched,
            "complete": self.complete,
            "skipped_fresh": self.skipped_fresh,
            "not_modified": self.not_modified,
            "structure_volume_share": self.structure_volume_share,
        }


def reduce_orders(
    orders: Iterable[dict],
    *,
    region_id: int,
    notional_tiers: Sequence[float],
    sweep_ts: str | None = None,
    expires_ts: str | None = None,
    partial: bool = False,
) -> SweepResult:
    """Reduce a raw sweep to `book_summary` rows. Raw orders are then dropped.

    Cross-page duplicates are reconciled by `order_id` and counted as a
    data-quality metric rather than raised as an error (plan.md §0 check #2).
    """
    stamp = sweep_ts or iso(utcnow())
    seen: set[int] = set()
    duplicates = 0
    buckets: dict[tuple[int, str], list[dict]] = {}
    total_volume_all = 0.0
    structure_volume_all = 0.0
    count = 0

    for order in orders:
        count += 1
        order_id = order.get("order_id")
        if order_id is not None:
            if order_id in seen:
                duplicates += 1
                continue
            seen.add(order_id)
        side = "buy" if order.get("is_buy_order") else "sell"
        key = (int(order["type_id"]), side)
        buckets.setdefault(key, []).append(order)
        volume = float(order.get("volume_remain") or 0)
        total_volume_all += volume
        if order.get("location_id") is not None and not is_npc_station(order["location_id"]):
            structure_volume_all += volume

    rows: list[dict] = []
    for (type_id, side), group in buckets.items():
        levels = _sorted_levels(group, is_buy=(side == "buy"))
        if not levels:
            continue
        total_volume = sum(volume for _, volume in levels)
        station_volume = sum(
            float(order.get("volume_remain") or 0)
            for order in group
            if order.get("location_id") is not None and is_npc_station(order["location_id"])
        )
        prices: list[float | None] = []
        quantities: list[float | None] = []
        for notional in notional_tiers:
            price, units = depth_walk(levels, float(notional))
            prices.append(price)
            quantities.append(units)
        largest = max(volume for _, volume in levels)
        rows.append(
            BookSummaryRow(
                type_id=type_id,
                region_id=region_id,
                side=side,
                sweep_ts=stamp,
                expires_ts=expires_ts,
                best_price=levels[0][0],
                total_volume=total_volume,
                order_count=len(levels),
                p5_price=p5_price(levels),
                depth_fill_price=prices,
                depth_fill_qty=quantities,
                top_order_volume_share=(largest / total_volume) if total_volume else None,
                station_volume_share=(station_volume / total_volume) if total_volume else None,
                partial_sweep=partial,
            ).as_record()
        )

    frame = pd.DataFrame(rows, columns=BOOK_SUMMARY_COLUMNS)
    return SweepResult(
        region_id=region_id,
        sweep_ts=stamp,
        frame=frame,
        orders_seen=count,
        duplicate_order_ids=duplicates,
        types=int(frame["type_id"].nunique()) if not frame.empty else 0,
        structure_volume_share=(structure_volume_all / total_volume_all)
        if total_volume_all
        else None,
    )


def spread_view(frame: pd.DataFrame) -> pd.DataFrame:
    """Join the two sides into one row per type: the sweep's spread view."""
    if frame.empty:
        return pd.DataFrame()
    sells = frame[frame["side"] == "sell"].set_index("type_id")
    buys = frame[frame["side"] == "buy"].set_index("type_id")
    common = sells.index.intersection(buys.index)
    if len(common) == 0:
        return pd.DataFrame()
    view = pd.DataFrame(index=common)
    view["best_ask"] = sells.loc[common, "best_price"]
    view["best_bid"] = buys.loc[common, "best_price"]
    view["ask_p5"] = sells.loc[common, "p5_price"]
    view["bid_p5"] = buys.loc[common, "p5_price"]
    view["sweep_ts"] = sells.loc[common, "sweep_ts"]
    for index in range(3):
        view[f"ask_fill_{index}"] = sells.loc[common, f"depth_fill_price_{index}"]
        view[f"bid_fill_{index}"] = buys.loc[common, f"depth_fill_price_{index}"]
        view[f"ask_qty_{index}"] = sells.loc[common, f"depth_fill_qty_{index}"]
        view[f"bid_qty_{index}"] = buys.loc[common, f"depth_fill_qty_{index}"]
    view["ask_top_share"] = sells.loc[common, "top_order_volume_share"]
    view["bid_top_share"] = buys.loc[common, "top_order_volume_share"]
    view["station_share_ask"] = sells.loc[common, "station_volume_share"]
    mid = (view["best_ask"] + view["best_bid"]) / 2.0
    view["mid"] = mid
    view["spread_pct"] = (view["best_ask"] - view["best_bid"]) / mid * 100.0
    return view.reset_index().rename(columns={"index": "type_id"})


async def sweep_region(
    config: Config,
    client: EsiClient,
    lake: BookLake,
    region_id: int,
    *,
    persist_raw_to=None,
) -> SweepResult:
    """One governed sweep of a region's book, reduced and written.

    A still-fresh book means no sweep happened; that is reported, never
    substituted with the previous sweep re-stamped as new.
    """
    paged = await client.get_all_pages(ORDERS_FEED, f"/markets/{region_id}/orders")
    if paged.first.skipped or not paged.rows:
        return SweepResult(
            region_id=region_id,
            sweep_ts=iso(utcnow()),
            pages_expected=paged.pages_expected,
            pages_fetched=paged.pages_fetched,
            skipped_fresh=paged.first.skipped,
            not_modified=paged.first.not_modified,
        )
    if persist_raw_to is not None:
        # The single debug escape hatch, for fixture-building only.
        import json

        from .paths import atomic_write_text

        atomic_write_text(persist_raw_to, json.dumps(paged.rows[:5000]))
    result = reduce_orders(
        paged.rows,
        region_id=region_id,
        notional_tiers=config.costs.notional_tiers_isk,
        sweep_ts=iso(paged.first.fetched_at or utcnow()),
        expires_ts=iso(paged.first.expires),
        partial=not paged.complete,
    )
    result.pages_expected = paged.pages_expected
    result.pages_fetched = paged.pages_fetched
    lake.write(result.frame)
    return result
