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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd

from .config import Config
from .store.lake import BOOK_SUMMARY_COLUMNS, DEPTH_COLUMNS, EXECUTABLE_COLUMNS, BookLake, DepthLake
from .timeutil import iso, parse_iso, utcnow

__all__ = [
    "BookSnapshot",
    "BookSummaryRow",
    "DepthSnapshot",
    "load_validated_depth",
    "DepthBound",
    "DepthCurve",
    "DepthLevel",
    "DepthReduction",
    "QuantityWalk",
    "SweepResult",
    "curve_from_frame",
    "depth_bound",
    "depth_jump_distance",
    "depth_stations",
    "executable_venue",
    "load_validated_book",
    "q_walk",
    "reachable_from",
    "reachable_from_station",
    "reduce_depth",
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
    exec_reachable_volume_share: float | None = None
    region_p5_price: float | None = None
    region_depth_fill_price: list = field(default_factory=list)
    region_depth_fill_qty: list = field(default_factory=list)
    region_top_order_volume_share: float | None = None
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
            "exec_reachable_volume_share": self.exec_reachable_volume_share,
            "region_p5_price": self.region_p5_price,
            "region_top_order_volume_share": self.region_top_order_volume_share,
        }
        for index in range(3):
            record[f"region_depth_fill_price_{index}"] = (
                self.region_depth_fill_price[index]
                if index < len(self.region_depth_fill_price)
                else None
            )
            record[f"region_depth_fill_qty_{index}"] = (
                self.region_depth_fill_qty[index]
                if index < len(self.region_depth_fill_qty)
                else None
            )
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
    #: The depth product of the same sweep, when one was asked for (§23.6).
    depth: object | None = None

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
            "depth": self.depth.as_dict() if self.depth is not None else None,
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
            reachable_prices = [float(order["price"]) for order in executable]
            exec_price = max(reachable_prices) if side == "buy" else min(reachable_prices)
        # Depth, p5 and concentration are walked over the EXECUTABLE book, not
        # the region (§22 S2a). Region-wide levels priced a 1,000 ISK bid fill
        # at a venue whose reachable bid was 90 — an impossible round trip, and
        # optimistic on both sides at once.
        exec_levels = _sorted_levels(executable, is_buy=(side == "buy"))
        total_volume = sum(volume for _, volume in levels)
        station_volume = sum(
            float(order.get("volume_remain") or 0)
            for order in group
            if order.get("location_id") is not None and is_npc_station(order["location_id"])
        )
        prices: list[float | None] = []
        quantities: list[float | None] = []
        region_prices: list[float | None] = []
        region_quantities: list[float | None] = []
        for notional in notional_tiers:
            price, units = depth_walk(exec_levels, float(notional))
            prices.append(price)
            quantities.append(units)
            region_price, region_units = depth_walk(levels, float(notional))
            region_prices.append(region_price)
            region_quantities.append(region_units)
        largest = max(volume for _, volume in levels)
        exec_volume = sum(volume for _, volume in exec_levels)
        exec_largest = max((volume for _, volume in exec_levels), default=0.0)
        rows.append(
            BookSummaryRow(
                type_id=type_id,
                region_id=region_id,
                side=side,
                sweep_ts=stamp,
                expires_ts=expires_ts,
                best_price=levels[0][0],
                total_volume=total_volume,
                order_count=len(exec_levels) or None,
                p5_price=p5_price(exec_levels) if exec_levels else None,
                depth_fill_price=prices,
                depth_fill_qty=quantities,
                top_order_volume_share=(exec_largest / exec_volume) if exec_volume else None,
                station_volume_share=(station_volume / total_volume) if total_volume else None,
                exec_reachable_volume_share=(exec_volume / total_volume) if total_volume else None,
                region_p5_price=p5_price(levels),
                region_depth_fill_price=region_prices,
                region_depth_fill_qty=region_quantities,
                region_top_order_volume_share=(largest / total_volume) if total_volume else None,
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
                exec_volume=exec_volume or None,
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
    depth_lake: DepthLake | None = None,
    stations: Mapping[int, int | None] | None = None,
    bound: DepthBound | None = None,
    jump_distance=None,
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
    if depth_lake is not None and stations:
        # The SAME in-memory pages, reduced a second way (§23.6). One fetch,
        # two products: no extra request, no cadence change, and the depth
        # generation is `(region_id, sweep_ts)` — identical to the book's, so
        # the two can be proved to come from one sweep.
        depth = reduce_depth(
            paged.rows,
            region_id=region_id,
            stations=stations,
            bound=bound or DepthBound(max_capital_isk=0.0, max_cargo_m3=0.0),
            jump_distance=jump_distance,
            sweep_ts=result.sweep_ts,
            fetched_at=result.sweep_ts,
            expires_ts=iso(paged.first.expires),
        )
        result.depth = depth
        if result.complete:
            depth_lake.write(depth.frame)
        else:
            depth_lake.write_partial(depth.frame)
    if result.complete:
        lake.write(result.frame)
    else:
        # A missing page can hold the true best level, so an incomplete sweep
        # is kept for diagnosis and never allowed to displace the last
        # complete snapshot (§21 R1).
        lake.write_partial(result.frame)
    return result


@dataclass(slots=True)
class DepthSnapshot:
    """The one contract every hauling path reads depth through (§23.6).

    The analogue of `BookSnapshot`, and for the same reason: a caller that
    *could* check completeness and staleness is a caller that can forget to,
    and the failure mode of forgetting is a confidently priced haul built on a
    curve nobody fetched. Staleness is decided once, here.
    """

    region_id: int
    frame: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=DEPTH_COLUMNS))
    sweep_ts: str | None = None
    age_minutes: float | None = None
    stale: bool = True
    reason: str = "no depth on disk"

    @property
    def generation(self) -> tuple[int, str] | None:
        return (self.region_id, self.sweep_ts) if self.sweep_ts else None

    @property
    def known(self) -> bool:
        return not self.frame.empty and not self.stale

    @property
    def priceable(self) -> pd.DataFrame:
        """The rows, or nothing at all. There is no partly-priceable curve."""
        if not self.known:
            return pd.DataFrame(columns=DEPTH_COLUMNS)
        return self.frame


def load_validated_depth(
    config: Config,
    region_id: int,
    *,
    lake: DepthLake | None = None,
    now=None,
) -> DepthSnapshot:
    """Load one region's depth and decide, once, whether it may price anything.

    Same staleness budget as the book — `costs.book_staleness_minutes` — because
    it is the same sweep. Reimplementing staleness per call site is how two
    surfaces end up disagreeing about whether the same generation is fresh.
    """
    lake = lake or DepthLake(config.paths)
    now = now or utcnow()
    frame = lake.latest(int(region_id))
    if frame.empty:
        return DepthSnapshot(
            region_id=int(region_id),
            reason=(
                f"no complete depth generation on disk for region {int(region_id)} — "
                "run: sweep-books"
            ),
        )
    stamps = frame["sweep_ts"].dropna() if "sweep_ts" in frame else pd.Series(dtype="object")
    sweep_ts = iso(pd.Timestamp(stamps.max()).to_pydatetime()) if not stamps.empty else None
    age = None
    if sweep_ts:
        swept = parse_iso(sweep_ts)
        if swept is not None:
            age = max(0.0, (now - swept).total_seconds() / 60.0)
    stale = age is None or age > config.costs.book_staleness_minutes
    reason = ""
    if stale:
        reason = (
            f"depth {age:.0f} min old — STALE"
            if age is not None
            else "depth generation has no sweep timestamp"
        )
    return DepthSnapshot(
        region_id=int(region_id),
        frame=frame,
        sweep_ts=sweep_ts,
        age_minutes=age,
        stale=stale,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# H1b — per-station depth (plan.md §23.6)
# ---------------------------------------------------------------------------
#
# `reduce_orders` above answers "what is the executable quote for this type in
# this region". A hauling plan asks something else entirely: *what does it cost
# to buy 1,200 of these at Jita 4-4, and what do 1,200 fetch at Amarr VIII?*
# That is a price-level curve per execution station, and no existing reduction
# carries one.
#
# It is built from the **same in-memory pages** as `reduce_orders`, in one
# pass: no extra ESI traffic, no cadence change, no new feed. `reduce_orders`
# and everything downstream of it are untouched, and a regression test asserts
# its frame is byte-identical through the modified sweep path.

#: Buy-order range values that are not jump counts.
STATION_RANGE = "station"
SOLARSYSTEM_RANGE = "solarsystem"

#: Why an order was left out of the executable curve. Each is counted, because
#: depth excluded silently is depth that looks like it was never there.
EXCLUDED_RANGE = "range_out_of_reach"
EXCLUDED_UNRESOLVABLE = "range_unresolvable"
EXCLUDED_MIN_VOLUME = "min_volume"


@dataclass(frozen=True, slots=True)
class DepthBound:
    """How much of a curve is worth storing.

    Deep enough that no question the operator's capital and cargo can ask ever
    reaches the end of it, and no deeper. Truncation is safe rather than
    optimistic: a curve cut short is marked incomplete, and a walk that reaches
    the boundary returns UNKNOWN instead of a number computed from levels that
    were never stored.
    """

    max_capital_isk: float
    max_cargo_m3: float
    safety_margin: float = 1.5
    #: `type_id -> packaged m³`, from the SDE. A type whose volume is unknown
    #: cannot have its cargo condition evaluated, so only the capital condition
    #: bounds it — and any consequence of stopping early surfaces as UNKNOWN.
    packaged_volume: Mapping[int, float] = field(default_factory=dict)

    @property
    def notional_target(self) -> float:
        return max(0.0, float(self.max_capital_isk) * float(self.safety_margin))

    @property
    def volume_target(self) -> float:
        return max(0.0, float(self.max_cargo_m3) * float(self.safety_margin))

    def satisfied(self, type_id: int, cumulative_notional: float, cumulative_qty: float) -> bool:
        if cumulative_notional < self.notional_target:
            return False
        volume = self.packaged_volume.get(int(type_id))
        if not volume or volume <= 0:
            return True
        return cumulative_qty * float(volume) >= self.volume_target


@dataclass(frozen=True, slots=True)
class DepthLevel:
    """One price on one side of one station's book, after the filters."""

    price: float
    qty: float
    cumulative_qty: float
    cumulative_notional: float
    order_count: int = 0
    min_volume_excluded_qty: float = 0.0
    structure_share: float | None = None
    oldest_issued: str | None = None
    newest_issued: str | None = None


@dataclass(frozen=True, slots=True)
class DepthCurve:
    """A side of one station's book, and whether it is the whole of it."""

    levels: tuple[DepthLevel, ...] = ()
    complete: bool = True
    side: str = ""
    type_id: int | None = None
    execution_location_id: int | None = None
    generation: tuple[int, str] | None = None

    @property
    def available_qty(self) -> float:
        return self.levels[-1].cumulative_qty if self.levels else 0.0

    @property
    def breakpoints(self) -> tuple[float, ...]:
        """Every cumulative quantity a level ends at.

        These are the only quantities worth pricing: between two breakpoints
        the marginal price does not change, so the best plan in that interval
        is always one of its ends.
        """
        return tuple(level.cumulative_qty for level in self.levels if level.qty > 0)

    @property
    def min_volume_excluded_qty(self) -> float:
        return float(sum(level.min_volume_excluded_qty for level in self.levels))

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[float, float]], *, complete: bool = True, **kwargs):
        """Build a curve from `(price, qty)` pairs, best-first as given."""
        levels: list[DepthLevel] = []
        cumulative_qty = 0.0
        cumulative_notional = 0.0
        for price, qty in pairs:
            cumulative_qty += float(qty)
            cumulative_notional += float(price) * float(qty)
            levels.append(
                DepthLevel(
                    price=float(price),
                    qty=float(qty),
                    cumulative_qty=cumulative_qty,
                    cumulative_notional=cumulative_notional,
                    order_count=1,
                )
            )
        return cls(levels=tuple(levels), complete=complete, **kwargs)


@dataclass(frozen=True, slots=True)
class QuantityWalk:
    """What `quantity` units really cost, or the reason that is UNKNOWN."""

    quantity: float
    value: float | None = None
    wap: float | None = None
    levels_consumed: int = 0
    marginal_next_price: float | None = None
    known: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "quantity": self.quantity,
            "value": self.value,
            "wap": self.wap,
            "levels_consumed": self.levels_consumed,
            "marginal_next_price": self.marginal_next_price,
            "known": self.known,
            "reason": self.reason,
        }


def q_walk(levels, quantity: float) -> QuantityWalk:
    """Walk a curve for a **quantity of units**, not for an ISK notional.

    The existing `depth_walk` prices "how many units does 0.25B buy"; a hauler
    asks the opposite question, because the thing that binds is his hold and
    the destination's bid depth, both of which are counted in units.

    A quantity beyond what the curve holds is **UNKNOWN**. When the curve was
    truncated by the storage bound that is doubly true — the levels that would
    have answered it were never written — and neither case is ever extrapolated
    from the last known price (§23.6).
    """
    curve = levels if isinstance(levels, DepthCurve) else DepthCurve.from_pairs(levels)
    quantity = float(quantity)
    if quantity <= 0:
        return QuantityWalk(quantity=quantity, reason="a walk needs a positive quantity")
    if not curve.levels:
        return QuantityWalk(quantity=quantity, reason="no depth on this side at this station")
    if quantity > curve.available_qty + 1e-9:
        reason = (
            "quantity reaches past the stored curve, which was truncated by the "
            "depth bound — the levels that would price it were never written"
            if not curve.complete
            else "the book at this station is not that deep"
        )
        return QuantityWalk(quantity=quantity, reason=reason)
    value = 0.0
    taken = 0.0
    consumed = 0
    marginal: float | None = None
    for index, level in enumerate(curve.levels):
        if level.qty <= 0:
            continue
        chunk = min(level.qty, quantity - taken)
        if chunk <= 0:
            break
        value += chunk * level.price
        taken += chunk
        consumed += 1
        if taken >= quantity - 1e-9:
            # The next unit costs either the rest of this level or the next.
            marginal = (
                level.price
                if chunk < level.qty - 1e-9
                else next(
                    (other.price for other in curve.levels[index + 1 :] if other.qty > 0),
                    None,
                )
            )
            break
    if taken < quantity - 1e-9:  # pragma: no cover - guarded by the check above
        return QuantityWalk(quantity=quantity, reason="the book at this station is not that deep")
    return QuantityWalk(
        quantity=quantity,
        value=value,
        wap=value / taken,
        levels_consumed=consumed,
        marginal_next_price=marginal,
        known=True,
    )


def _numeric_range(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def reachable_from_station(
    order: dict,
    *,
    station_id: int,
    station_system: int | None,
    jump_distance=None,
) -> tuple[bool, str | None]:
    """Could a character standing at `station_id` trade against this order?

    The reachability doctrine in full (§23.6). CCP matches a buy order by its
    **range from the order's own location**, and the seller transacts from the
    station he is docked in — so a bid reaches *out* to the seller, and the
    seller never travels.

    Returns `(reachable, exclusion_reason)`. Anything that cannot be decided —
    an unknown system, a range that is neither a keyword we know nor a number,
    a jump distance the graph cannot answer — is **excluded and counted**.
    UNKNOWN fails (§4); the alternative is an exit that prices against depth
    the operator cannot actually sell into.
    """
    location = order.get("location_id")
    if location is not None and int(location) == int(station_id):
        return True, None
    if not order.get("is_buy_order"):
        # A sell order is executable only where it rests. Nothing about a sell
        # order's range is negotiable, because it has none.
        return False, EXCLUDED_RANGE
    raw_range = str(order.get("range") or "").strip().lower()
    if raw_range == REGION_RANGE:
        return True, None
    if raw_range == STATION_RANGE:
        return False, EXCLUDED_RANGE
    order_system = order.get("system_id")
    if order_system is None or station_system is None:
        return False, EXCLUDED_UNRESOLVABLE
    if raw_range == SOLARSYSTEM_RANGE:
        return (True, None) if int(order_system) == int(station_system) else (False, EXCLUDED_RANGE)
    jumps_allowed = _numeric_range(raw_range)
    if jumps_allowed is None or jump_distance is None:
        return False, EXCLUDED_UNRESOLVABLE
    distance = jump_distance(int(order_system), int(station_system))
    if distance is None:
        return False, EXCLUDED_UNRESOLVABLE
    return (True, None) if distance <= jumps_allowed else (False, EXCLUDED_RANGE)


@dataclass(slots=True)
class DepthReduction:
    """One sweep's depth product, with everything it left out counted."""

    region_id: int
    sweep_ts: str
    frame: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=DEPTH_COLUMNS))
    stations: tuple[int, ...] = ()
    curves: int = 0
    truncated_curves: int = 0
    excluded_range: int = 0
    excluded_unresolvable: int = 0
    excluded_min_volume: int = 0
    min_volume_excluded_qty: float = 0.0

    def as_dict(self) -> dict:
        return {
            "region_id": self.region_id,
            "sweep_ts": self.sweep_ts,
            "stations": list(self.stations),
            "rows": int(len(self.frame)),
            "curves": self.curves,
            "truncated_curves": self.truncated_curves,
            "excluded_range": self.excluded_range,
            "excluded_unresolvable": self.excluded_unresolvable,
            "excluded_min_volume": self.excluded_min_volume,
            "min_volume_excluded_qty": self.min_volume_excluded_qty,
        }


def _issued(order: dict) -> str | None:
    value = order.get("issued")
    return str(value) if value else None


def reduce_depth(
    orders: Iterable[dict],
    *,
    region_id: int,
    stations: Mapping[int, int | None],
    bound: DepthBound,
    jump_distance=None,
    sweep_ts: str | None = None,
    fetched_at: str | None = None,
    expires_ts: str | None = None,
) -> DepthReduction:
    """Reduce a raw sweep to per-station price-level curves.

    `stations` maps each execution station id to the solar system it sits in;
    a station whose system is unknown can still take its own resting orders and
    region-ranged bids, and everything else against it is UNKNOWN.

    The **`min_volume` rule** (§23.6, conservative v1): a buy order demanding a
    minimum parcel larger than one unit is excluded from the executable levels
    and its volume accumulated into `min_volume_excluded_qty`. Modelling the
    packing problem it creates would interact with every other level in the
    walk; under-stating reachable exit depth is the safe direction, and the
    excluded volume stays visible so the simplification can be seen.
    """
    stamp = sweep_ts or iso(utcnow())
    station_ids = tuple(int(station) for station in stations)
    reduction = DepthReduction(region_id=int(region_id), sweep_ts=stamp, stations=station_ids)

    # (station, type, side) -> price -> accumulator
    buckets: dict[tuple[int, int, str], dict[float, dict]] = {}
    live = _live(list(orders))
    for station in station_ids:
        station_system = stations.get(station)
        for order in live:
            reachable, reason = reachable_from_station(
                order,
                station_id=station,
                station_system=station_system,
                jump_distance=jump_distance,
            )
            if not reachable:
                if reason == EXCLUDED_UNRESOLVABLE:
                    reduction.excluded_unresolvable += 1
                else:
                    reduction.excluded_range += 1
                continue
            side = "buy" if order.get("is_buy_order") else "sell"
            price = float(order["price"])
            volume = float(order.get("volume_remain") or 0.0)
            level = buckets.setdefault((station, int(order["type_id"]), side), {}).setdefault(
                price,
                {
                    "qty": 0.0,
                    "orders": 0,
                    "excluded": 0.0,
                    "structure": 0.0,
                    "oldest": None,
                    "newest": None,
                },
            )
            minimum = order.get("min_volume")
            if side == "buy" and minimum is not None and float(minimum) > 1.0:
                level["excluded"] += volume
                reduction.excluded_min_volume += 1
                reduction.min_volume_excluded_qty += volume
                continue
            level["qty"] += volume
            level["orders"] += 1
            location = order.get("location_id")
            if location is not None and not is_npc_station(location):
                level["structure"] += volume
            issued = _issued(order)
            if issued:
                level["oldest"] = min(level["oldest"] or issued, issued)
                level["newest"] = max(level["newest"] or issued, issued)

    rows: list[dict] = []
    for (station, type_id, side), prices in buckets.items():
        ordered = sorted(prices.items(), key=lambda item: item[0], reverse=(side == "buy"))
        cumulative_qty = 0.0
        cumulative_notional = 0.0
        complete = True
        kept: list[dict] = []
        for index, (price, level) in enumerate(ordered):
            cumulative_qty += level["qty"]
            cumulative_notional += price * level["qty"]
            kept.append(
                {
                    "region_id": int(region_id),
                    "sweep_ts": stamp,
                    "fetched_at": fetched_at or stamp,
                    "expires_ts": expires_ts,
                    "execution_location_id": int(station),
                    "type_id": int(type_id),
                    "side": side,
                    "price": float(price),
                    "level_qty": float(level["qty"]),
                    "cumulative_qty": float(cumulative_qty),
                    "cumulative_notional": float(cumulative_notional),
                    "level_order_count": int(level["orders"]),
                    "min_volume_excluded_qty": float(level["excluded"]),
                    "oldest_issued": level["oldest"],
                    "newest_issued": level["newest"],
                    "structure_share": (
                        float(level["structure"] / level["qty"]) if level["qty"] > 0 else None
                    ),
                    "depth_complete": True,
                }
            )
            if bound.satisfied(type_id, cumulative_notional, cumulative_qty) and index + 1 < len(
                ordered
            ):
                complete = False
                break
        if not complete:
            for row in kept:
                row["depth_complete"] = False
            reduction.truncated_curves += 1
        reduction.curves += 1
        rows.extend(kept)

    reduction.frame = pd.DataFrame(rows, columns=DEPTH_COLUMNS)
    return reduction


def curve_from_frame(
    frame: pd.DataFrame, *, type_id: int, side: str, execution_location_id: int
) -> DepthCurve:
    """Rebuild one `(station, type, side)` curve from a depth frame."""
    if frame is None or frame.empty:
        return DepthCurve(side=side, type_id=type_id, execution_location_id=execution_location_id)
    rows = frame[
        (frame["type_id"] == int(type_id))
        & (frame["side"] == side)
        & (frame["execution_location_id"] == int(execution_location_id))
    ]
    if rows.empty:
        return DepthCurve(side=side, type_id=type_id, execution_location_id=execution_location_id)
    rows = rows.sort_values("cumulative_qty")
    levels = tuple(
        DepthLevel(
            price=float(row["price"]),
            qty=float(row["level_qty"]),
            cumulative_qty=float(row["cumulative_qty"]),
            cumulative_notional=float(row["cumulative_notional"]),
            order_count=int(row["level_order_count"] or 0),
            min_volume_excluded_qty=float(row["min_volume_excluded_qty"] or 0.0),
            structure_share=(
                float(row["structure_share"])
                if row["structure_share"] is not None
                and row["structure_share"] == row["structure_share"]
                else None
            ),
            oldest_issued=row["oldest_issued"],
            newest_issued=row["newest_issued"],
        )
        for _index, row in rows.iterrows()
    )
    complete = bool(rows["depth_complete"].fillna(False).astype(bool).all())
    generation = (int(rows.iloc[0]["region_id"]), str(rows.iloc[0]["sweep_ts"]))
    return DepthCurve(
        levels=levels,
        complete=complete,
        side=side,
        type_id=int(type_id),
        execution_location_id=int(execution_location_id),
        generation=generation,
    )


def depth_stations(config: Config, db, region_id: int) -> dict[int, int | None]:
    """Configured execution stations that actually sit in `region_id`.

    A station the SDE has never heard of is **left out**, not defaulted into
    the region being swept: a curve attributed to the wrong region would join
    two books that never traded with each other.
    """
    if not config.hauling.enabled:
        return {}
    systems = db.station_systems()
    regions = db.system_region_map()
    stations: dict[int, int | None] = {}
    for station in (*config.hauling.hub_station_ids, *config.hauling.extra_destination_station_ids):
        system = systems.get(int(station))
        if system is None or regions.get(int(system)) != int(region_id):
            continue
        stations[int(station)] = int(system)
    return stations


def depth_bound(config: Config, db) -> DepthBound:
    """How deep to store, from the operator's own capital and biggest hold.

    With no ship profile recorded yet the cargo target is zero, so the capital
    condition alone bounds the store. That is deliberately not a guess at what
    he flies: a curve cut short is marked incomplete and any walk that reaches
    the boundary is UNKNOWN, so under-storing costs an honest refusal rather
    than a wrong number.
    """
    holds = [
        float(row["usable_cargo_m3"] or 0.0)
        for row in db.haul_profiles()
        if row["usable_cargo_m3"] is not None
    ]
    packaged = {
        int(row["type_id"]): float(row["packaged_volume"])
        for row in db.conn.execute(
            "SELECT type_id, packaged_volume FROM sde_types WHERE packaged_volume IS NOT NULL"
        )
    }
    return DepthBound(
        max_capital_isk=float(config.hauling.max_scan_capital_isk),
        max_cargo_m3=max(holds, default=0.0),
        safety_margin=float(config.hauling.depth_safety_margin),
        packaged_volume=packaged,
    )


def depth_jump_distance(db):
    """The jump-distance function order ranges are resolved with, or None.

    None when the map has not been built — and a numeric range that cannot be
    resolved is excluded and counted, never assumed to reach (§23.6).
    """
    from .routes import RouteGraph

    graph = RouteGraph.from_db(db)
    if not graph:
        return None
    return graph.jump_distance
