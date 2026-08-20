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
from .store.lake import BOOK_SUMMARY_COLUMNS, EXECUTABLE_COLUMNS, BookLake
from .timeutil import iso, parse_iso, utcnow

__all__ = [
    "BookSnapshot",
    "BookSummaryRow",
    "SweepResult",
    "executable_venue",
    "load_validated_book",
    "reachable_from",
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


def _live(orders: Sequence[dict]) -> list[dict]:
    """Orders with a usable price and remaining volume."""
    return [
        order
        for order in orders
        if order.get("price") is not None and float(order.get("volume_remain") or 0) > 0
    ]


def _sorted_levels(orders: Sequence[dict], *, is_buy: bool) -> list[tuple[float, float]]:
    """(price, volume) levels, best-first. Asks ascend; bids descend."""
    levels = [(float(order["price"]), float(order["volume_remain"])) for order in _live(orders)]
    levels.sort(key=lambda level: level[0], reverse=is_buy)
    return levels


# EVE buy-order ranges. A sell order has no range: it is executable only at
# the station it rests in.
REGION_RANGE = "region"


def reachable_from(order: dict, location_id: int | None) -> bool:
    """Could a character standing at `location_id` trade against this order?

    Same station is inside every range class, so a local order always
    qualifies. A remote order qualifies only when its range is `region`,
    which needs no topology to evaluate. `solarsystem` and the numeric jump
    ranges *may* reach, but deciding that needs station→system→jump data the
    book reduction does not have — so they are **UNKNOWN, and UNKNOWN fails**
    (§4). Counting an unresolvable range as reachable would be exactly the
    optimistic guess this phase exists to remove.
    """
    if location_id is None:
        return False
    if int(order.get("location_id", -1)) == int(location_id):
        return True
    if not order.get("is_buy_order"):
        return False  # a sell order rests where it rests
    return str(order.get("range") or "").strip().lower() == REGION_RANGE


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


def executable_venue(orders: Sequence[dict]) -> int | None:
    """The one location a round trip in this type could actually happen at.

    **Anchored on the asks, deliberately.** A sell order is executable only
    where it rests, so to buy at all the operator must dock where the asks
    are; a bid, by contrast, may reach across the region. The 2026-08-20 full
    Forge sweep measured ~0% of visible ask volume in player structures
    against 8.8–98.3% of bid volume (plan.md §17), so anchoring on the asks
    lands on a station the operator can almost always dock at, while anchoring
    on total volume would keep landing on structures whose access is unknown.

    Among ask locations the busiest wins. That is deliberately *not* the
    location with the widest spread: choosing the venue that flatters the
    number is how a screen talks itself into a trade.
    """
    volumes: dict[int, float] = {}
    for order in _live(orders):
        if order.get("is_buy_order") or order.get("location_id") is None:
            continue
        location = int(order["location_id"])
        volumes[location] = volumes.get(location, 0.0) + float(order["volume_remain"])
    if not volumes:
        return None
    return max(volumes.items(), key=lambda item: (item[1], -item[0]))[0]


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
    # -- R1: executable identity (plan.md §21 R1) --------------------------
    best_location_id: int | None = None
    best_range: str | None = None
    exec_location_id: int | None = None
    exec_price: float | None = None
    exec_volume: float | None = None
    exec_order_count: int | None = None
    exec_is_structure: bool | None = None

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
            "best_location_id": self.best_location_id,
            "best_range": self.best_range,
            "exec_location_id": self.exec_location_id,
            "exec_price": self.exec_price,
            "exec_volume": self.exec_volume,
            "exec_order_count": self.exec_order_count,
            "exec_is_structure": self.exec_is_structure,
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
    by_type: dict[int, list[dict]] = {}
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
        by_type.setdefault(int(order["type_id"]), []).append(order)
        volume = float(order.get("volume_remain") or 0)
        total_volume_all += volume
        if order.get("location_id") is not None and not is_npc_station(order["location_id"]):
            structure_volume_all += volume

    # One executable venue per type, decided before the per-side rows are
    # built, because executability is a property of the *pair* and cannot be
    # seen from one side of the book alone.
    venues = {type_id: executable_venue(orders) for type_id, orders in by_type.items()}

    rows: list[dict] = []
    for (type_id, side), group in buckets.items():
        levels = _sorted_levels(group, is_buy=(side == "buy"))
        if not levels:
            continue
        venue = venues.get(type_id)
        best_order = min(
            _live(group),
            key=lambda order: -float(order["price"]) if side == "buy" else float(order["price"]),
            default=None,
        )
        executable = [order for order in _live(group) if reachable_from(order, venue)]
        exec_price = None
        if executable:
            prices = [float(order["price"]) for order in executable]
            exec_price = max(prices) if side == "buy" else min(prices)
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
                best_location_id=(
                    int(best_order["location_id"])
                    if best_order is not None and best_order.get("location_id") is not None
                    else None
                ),
                best_range=(
                    str(best_order.get("range"))
                    if best_order is not None and best_order.get("range") is not None
                    else None
                ),
                exec_location_id=venue,
                exec_price=exec_price,
                exec_volume=(
                    sum(float(order["volume_remain"]) for order in executable)
                    if executable
                    else None
                ),
                exec_order_count=len(executable) or None,
                exec_is_structure=((not is_npc_station(venue)) if venue is not None else None),
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
    """Join the two sides into one **executable** row per type (§21 R1).

    `best_ask` and `best_bid` are the quotes available at one named venue, not
    the region-wide extrema — those are kept alongside as `region_best_ask` /
    `region_best_bid` so the correction stays auditable, and so a reader can
    see how far apart the two readings are.

    A type with no executable pair is dropped rather than priced. That is not
    a filtered-out opportunity; it is a book in which no single character
    could have traded both sides.
    """
    if frame.empty:
        return pd.DataFrame()
    if any(column not in frame.columns for column in EXECUTABLE_COLUMNS):
        # A pre-R1 snapshot does not know where its quotes rested, and a
        # spread it cannot place is not a spread it can price.
        return pd.DataFrame()
    sells = frame[frame["side"] == "sell"].set_index("type_id")
    buys = frame[frame["side"] == "buy"].set_index("type_id")
    common = sells.index.intersection(buys.index)
    if len(common) == 0:
        return pd.DataFrame()
    view = pd.DataFrame(index=common)
    view["best_ask"] = pd.to_numeric(sells.loc[common, "exec_price"], errors="coerce")
    view["best_bid"] = pd.to_numeric(buys.loc[common, "exec_price"], errors="coerce")
    view["region_best_ask"] = pd.to_numeric(sells.loc[common, "best_price"], errors="coerce")
    view["region_best_bid"] = pd.to_numeric(buys.loc[common, "best_price"], errors="coerce")
    view["exec_location_id"] = sells.loc[common, "exec_location_id"]
    view["exec_is_structure"] = sells.loc[common, "exec_is_structure"]
    view["exec_ask_volume"] = pd.to_numeric(sells.loc[common, "exec_volume"], errors="coerce")
    view["exec_bid_volume"] = pd.to_numeric(buys.loc[common, "exec_volume"], errors="coerce")
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
    # Both sides must be executable at the same venue, or there is no trade.
    view = view[view["best_ask"].notna() & view["best_bid"].notna()]
    return view.reset_index().rename(columns={"index": "type_id"})


@dataclass(slots=True)
class BookSnapshot:
    """The one contract every pricing path reads a book through (§21 R1).

    Warning flags were not enough. A caller that *could* check
    `partial_sweep`, or *could* compare `sweep_ts` against the staleness
    budget, is a caller that can forget to — and the failure mode of
    forgetting is a confidently priced row built on a book that was never
    fully fetched. So the decision is made once, here, and `priceable` is
    empty unless every condition holds.

    Tri-state, and UNKNOWN fails (§4): missing, stale, partial or pre-R1 data
    all resolve to `known is False`, and none of them prices anything.
    """

    region_id: int
    frame: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=BOOK_SUMMARY_COLUMNS))
    sweep_ts: str | None = None
    age_minutes: float | None = None
    stale: bool = True
    complete: bool = False
    executable: bool = False
    reason: str = "no book on disk"

    @property
    def known(self) -> bool:
        return not self.frame.empty and self.complete and self.executable and not self.stale

    @property
    def priceable(self) -> pd.DataFrame:
        """The rows, or nothing at all. There is no partly-priceable book."""
        if not self.known:
            return pd.DataFrame(columns=BOOK_SUMMARY_COLUMNS)
        return self.frame


def load_validated_book(
    config: Config,
    region_id: int,
    *,
    lake: BookLake | None = None,
    now=None,
) -> BookSnapshot:
    """Load one region's book and decide, once, whether it may price anything."""
    lake = lake or BookLake(config.paths)
    now = now or utcnow()
    frame = lake.latest(int(region_id))
    if frame.empty:
        return BookSnapshot(
            region_id=int(region_id),
            reason=f"no complete book on disk — run: sweep-books --region {int(region_id)}",
        )

    partial = bool(frame["partial_sweep"].fillna(False).astype(bool).any())
    missing = [column for column in EXECUTABLE_COLUMNS if column not in frame.columns]

    stamps = frame["sweep_ts"].dropna() if "sweep_ts" in frame else pd.Series(dtype="object")
    sweep_ts = iso(pd.Timestamp(stamps.max()).to_pydatetime()) if not stamps.empty else None
    age = None
    if sweep_ts:
        swept = parse_iso(sweep_ts)
        if swept is not None:
            age = max(0.0, (now - swept).total_seconds() / 60.0)
    stale = age is None or age > config.costs.book_staleness_minutes

    if missing:
        reason = (
            "book predates the executable-quote contract and cannot say where "
            f"its quotes rested ({', '.join(missing)} absent) — re-run sweep-books"
        )
    elif partial:
        reason = "latest snapshot is a partial sweep"
    elif stale:
        reason = (
            f"book {age:.0f} min old — STALE" if age is not None else "book has no sweep timestamp"
        )
    else:
        reason = ""

    return BookSnapshot(
        region_id=int(region_id),
        frame=frame,
        sweep_ts=sweep_ts,
        age_minutes=age,
        stale=stale,
        complete=not partial,
        executable=not missing,
        reason=reason,
    )


async def sweep_region(
    config: Config,
    client,
    lake: BookLake,
    region_id: int,
    *,
    persist_raw_to=None,
) -> SweepResult:
    """One governed sweep of a region's book, reduced and written.

    A still-fresh book means no sweep happened; that is reported, never
    substituted with the previous sweep re-stamped as new.

    **The ESI client is imported here, not at module scope (§21 R8).** Every
    other function in this module is pure analysis over a frame, and the desk
    imports them — so a module-level `esi.client` import put `httpx` into the
    GUI's import graph through a chain no direct-import check could see.
    """
    from .esi.client import ORDERS_FEED

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
    if result.complete:
        lake.write(result.frame)
    else:
        # A missing page can hold the true best level, so an incomplete sweep
        # is kept for diagnosis and never allowed to displace the last
        # complete snapshot (§21 R1).
        lake.write_partial(result.frame)
    return result
