"""Cross-region scan with real freight netting — plan.md §15.

Buy in hub A, haul, sell in hub B, over days. This is a **swing-compatible
arbitrage screen**, not station trading (§10.5): the holding period is the
freight time plus the sell queue, and the escrow cost of that time is charged.

The one rule that makes this honest: **no freight quote → no row, ever.** A
margin that has not paid for its own hauling is not a margin, and estimating
freight from a formula would be exactly the invented number this system exists
to avoid. Quotes come from the PushX API (verified live 2026-08-20; it keys off
system *names*, not ids), are cached, and a cached quote takes a staleness
haircut so an old number never reads as a fresh one.

PushX is an unversioned third-party schema (plan.md §9 R10). It is an
enrichment, not a load-bearing wall: without it, cross-region simply pauses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
import pandas as pd

from .config import Config
from .costs import CostModel
from .store.db import Database
from .timeutil import ensure_utc, iso, parse_iso, utcnow

__all__ = [
    "CrossRegionRow",
    "CrossRegionScan",
    "FreightQuote",
    "hub_system_name",
    "quote_freight",
    "render_cross_region",
    "scan_cross_region",
]


@dataclass(frozen=True, slots=True)
class FreightQuote:
    route: str
    volume_m3: float
    collateral: float
    price: float | None
    quoted_at: str
    cached: bool
    haircut_pct: float
    unknown_reason: str | None = None

    @property
    def known(self) -> bool:
        return self.price is not None

    @property
    def effective_price(self) -> float | None:
        """Quote plus the staleness haircut. A cached quote never reads fresh."""
        if self.price is None:
            return None
        return self.price * (1.0 + self.haircut_pct / 100.0)


def hub_system_name(config: Config, region_id: int) -> str | None:
    for entry in config.freight.hub_systems:
        if int(entry.get("region_id", 0)) == int(region_id):
            return str(entry.get("system"))
    return None


def quote_freight(
    config: Config,
    db: Database,
    *,
    start_system: str,
    end_system: str,
    volume_m3: float,
    collateral: float,
    client: httpx.Client | None = None,
    now=None,
) -> FreightQuote:
    """One PushX quote, cached. A failure is UNKNOWN with its reason, never 0."""
    now = ensure_utc(now or utcnow())
    route = f"{start_system}->{end_system}"
    cached = db.conn.execute(
        "SELECT * FROM freight_quotes WHERE route=? AND volume_m3=? AND collateral=?",
        (route, float(volume_m3), float(collateral)),
    ).fetchone()
    if cached is not None:
        quoted_at = parse_iso(cached["quoted_at"])
        age = now - quoted_at if quoted_at else timedelta(days=999)
        if age <= timedelta(minutes=config.freight.quote_cache_minutes):
            return FreightQuote(
                route=route,
                volume_m3=float(volume_m3),
                collateral=float(collateral),
                price=cached["price"],
                quoted_at=cached["quoted_at"],
                cached=True,
                haircut_pct=config.freight.staleness_haircut_pct,
            )

    owns = client is None
    client = client or httpx.Client(headers=config.headers, timeout=30.0, follow_redirects=True)
    try:
        response = client.get(
            config.freight.pushx_quote_url,
            params={
                "startSystemName": start_system,
                "endSystemName": end_system,
                "volume": int(max(1, round(volume_m3))),
                "collateral": int(max(0, round(collateral))),
                "apiClient": "EveTradingbot",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return FreightQuote(
            route=route,
            volume_m3=float(volume_m3),
            collateral=float(collateral),
            price=None,
            quoted_at=iso(now),
            cached=False,
            haircut_pct=config.freight.staleness_haircut_pct,
            unknown_reason=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if owns:
            client.close()

    error = payload.get("PriceError") or payload.get("GeneralError")
    price = payload.get("PriceNormal")
    if error or not price:
        return FreightQuote(
            route=route,
            volume_m3=float(volume_m3),
            collateral=float(collateral),
            price=None,
            quoted_at=iso(now),
            cached=False,
            haircut_pct=config.freight.staleness_haircut_pct,
            unknown_reason=str(error or "no PriceNormal in the quote"),
        )
    db.conn.execute(
        "INSERT INTO freight_quotes(route, volume_m3, collateral, quoted_at, price, raw)"
        " VALUES(?,?,?,?,?,?) ON CONFLICT(route, volume_m3, collateral) DO UPDATE SET"
        " quoted_at=excluded.quoted_at, price=excluded.price, raw=excluded.raw",
        (route, float(volume_m3), float(collateral), iso(now), float(price), json.dumps(payload)),
    )
    return FreightQuote(
        route=route,
        volume_m3=float(volume_m3),
        collateral=float(collateral),
        price=float(price),
        quoted_at=iso(now),
        cached=False,
        haircut_pct=0.0,
    )


@dataclass(slots=True)
class CrossRegionRow:
    type_id: int
    type_name: str | None
    buy_region: int
    sell_region: int
    notional_isk: float
    buy_price: float
    sell_price: float
    units: float
    packaged_volume_m3: float
    freight_isk: float
    freight_route: str
    freight_cached: bool
    sales_tax_isk: float
    net_isk: float
    net_pct: float
    breakeven_move_pct: float | None
    buy_sweep_ts: str
    sell_sweep_ts: str
    sell_side_station_share: float | None = None
    flags: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "type_name": self.type_name,
            "buy_region": self.buy_region,
            "sell_region": self.sell_region,
            "notional_isk": self.notional_isk,
            "buy_price": self.buy_price,
            "sell_price": self.sell_price,
            "units": self.units,
            "packaged_volume_m3": self.packaged_volume_m3,
            "freight_isk": self.freight_isk,
            "freight_route": self.freight_route,
            "freight_cached": self.freight_cached,
            "sales_tax_isk": self.sales_tax_isk,
            "net_isk": self.net_isk,
            "net_pct": self.net_pct,
            "breakeven_move_pct": self.breakeven_move_pct,
            "buy_sweep_ts": self.buy_sweep_ts,
            "sell_sweep_ts": self.sell_sweep_ts,
            "sell_side_station_share": self.sell_side_station_share,
            "flags": list(self.flags),
        }


@dataclass(slots=True)
class CrossRegionScan:
    generated_at: str
    notional_isk: float
    rows: list[dict] = field(default_factory=list)
    pairs_considered: int = 0
    dropped_no_freight: int = 0
    dropped_no_depth: int = 0
    dropped_negative: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "notional_isk": self.notional_isk,
            "rows": self.rows,
            "pairs_considered": self.pairs_considered,
            "dropped_no_freight": self.dropped_no_freight,
            "dropped_no_depth": self.dropped_no_depth,
            "dropped_negative": self.dropped_negative,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One priced hub pair, before freight. Named fields, not a 15-tuple."""

    gross_edge: float
    type_id: int
    type_name: str | None
    buy_region: int
    sell_region: int
    buy_system: str
    sell_system: str
    buy_price: float
    sell_price: float
    units: float
    volume_m3: float
    gross_sale: float
    buy_sweep: str
    sell_sweep: str
    sell_station_share: float | None


def _as_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _walk(row, tier_index: int) -> float | None:
    value = row.get(f"depth_fill_price_{tier_index}")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def scan_cross_region(
    config: Config,
    db: Database,
    books: dict[int, pd.DataFrame],
    *,
    tier_index: int = 0,
    max_rows: int = 25,
    client: httpx.Client | None = None,
    now=None,
    quote_fn=quote_freight,
) -> CrossRegionScan:
    """Score every hub pair for one notional, netting real freight.

    `books` maps `region_id -> book_summary` from that region's latest sweep.
    A pair with no freight quote is **dropped and counted**, never shown with
    an assumed haircut.
    """
    now = ensure_utc(now or utcnow())
    costs = CostModel.from_config(config)
    tiers = list(config.costs.notional_tiers_isk)
    notional = float(tiers[tier_index])
    scan = CrossRegionScan(generated_at=iso(now), notional_isk=notional)
    if not config.freight.enabled:
        scan.notes.append("freight disabled in config; no cross-region rows are produced")
        return scan

    regions = [region for region, frame in books.items() if frame is not None and not frame.empty]
    if len(regions) < 2:
        scan.notes.append("fewer than two regions have a book sweep; nothing to compare")
        return scan

    asks: dict[int, pd.DataFrame] = {}
    bids: dict[int, pd.DataFrame] = {}
    for region in regions:
        frame = books[region]
        asks[region] = frame[frame["side"] == "sell"].set_index("type_id")
        bids[region] = frame[frame["side"] == "buy"].set_index("type_id")

    candidates: list[_Candidate] = []
    for buy_region in regions:
        for sell_region in regions:
            if buy_region == sell_region:
                continue
            buy_system = hub_system_name(config, buy_region)
            sell_system = hub_system_name(config, sell_region)
            if not buy_system or not sell_system:
                scan.notes.append(
                    f"no hub system configured for region {buy_region} or {sell_region}; "
                    "the pair is skipped rather than guessed"
                )
                continue
            shared = asks[buy_region].index.intersection(bids[sell_region].index)
            for type_id in shared:
                scan.pairs_considered += 1
                ask_row = asks[buy_region].loc[type_id]
                bid_row = bids[sell_region].loc[type_id]
                if isinstance(ask_row, pd.DataFrame):
                    ask_row = ask_row.iloc[0]
                if isinstance(bid_row, pd.DataFrame):
                    bid_row = bid_row.iloc[0]
                buy_price = _walk(ask_row, tier_index)
                sell_price = _walk(bid_row, tier_index)
                if buy_price is None or sell_price is None:
                    scan.dropped_no_depth += 1
                    continue
                gross_sale = sell_price / buy_price * notional
                if gross_sale <= notional:
                    scan.dropped_negative += 1
                    continue
                type_row = db.type_by_id(int(type_id))
                packaged = float((type_row["packaged_volume"] if type_row else None) or 0.0)
                units = notional / buy_price
                volume_m3 = packaged * units
                if volume_m3 <= 0:
                    scan.dropped_no_depth += 1
                    continue
                candidates.append(
                    _Candidate(
                        gross_edge=gross_sale - notional,
                        type_id=int(type_id),
                        type_name=type_row["name"] if type_row else None,
                        buy_region=buy_region,
                        sell_region=sell_region,
                        buy_system=buy_system,
                        sell_system=sell_system,
                        buy_price=buy_price,
                        sell_price=sell_price,
                        units=units,
                        volume_m3=volume_m3,
                        gross_sale=gross_sale,
                        buy_sweep=str(ask_row.get("sweep_ts")),
                        sell_sweep=str(bid_row.get("sweep_ts")),
                        sell_station_share=_as_float(bid_row.get("station_volume_share")),
                    )
                )

    # Quote freight only for the best candidates: each quote is a third-party
    # API call, and quoting thousands of losers would be rude and pointless.
    candidates.sort(key=lambda item: -item.gross_edge)
    rows: list[CrossRegionRow] = []
    for candidate in candidates[: max_rows * 4]:
        quote = quote_fn(
            config,
            db,
            start_system=candidate.buy_system,
            end_system=candidate.sell_system,
            volume_m3=candidate.volume_m3,
            collateral=notional * config.freight.collateral_multiple,
            client=client,
            now=now,
        )
        if not quote.known:
            scan.dropped_no_freight += 1
            continue
        freight = quote.effective_price or 0.0
        net_sale = costs.sell_proceeds(candidate.gross_sale, maker=False)
        tax = candidate.gross_sale - net_sale
        net = net_sale - notional - freight
        if net <= 0:
            scan.dropped_negative += 1
            continue
        flags: list[str] = []
        share = candidate.sell_station_share
        if share is not None and share < 0.9:
            # Measured 2026-08-20 across all five hubs: every visible ASK sits in
            # an NPC station, while bids are 9%-98% structure-resident (Amarr is
            # 98.3%). The exposure is always on the SELL leg of a haul.
            flags.append(
                f"{1 - share:.0%} of the sell-side book is in player structures — "
                "this exit needs docking rights"
            )
        if quote.cached:
            flags.append("freight quote is cached and haircut for staleness")
        rows.append(
            CrossRegionRow(
                type_id=candidate.type_id,
                type_name=candidate.type_name,
                buy_region=candidate.buy_region,
                sell_region=candidate.sell_region,
                notional_isk=notional,
                buy_price=candidate.buy_price,
                sell_price=candidate.sell_price,
                units=candidate.units,
                packaged_volume_m3=candidate.volume_m3,
                freight_isk=freight,
                freight_route=quote.route,
                freight_cached=quote.cached,
                sales_tax_isk=tax,
                net_isk=net,
                net_pct=net / notional * 100.0,
                breakeven_move_pct=costs.breakeven_move_pct(
                    entry_price=candidate.buy_price,
                    exit_price=candidate.sell_price,
                    reference_price=(candidate.buy_price + candidate.sell_price) / 2.0,
                ),
                buy_sweep_ts=candidate.buy_sweep,
                sell_sweep_ts=candidate.sell_sweep,
                sell_side_station_share=share,
                flags=tuple(flags),
            )
        )
        if len(rows) >= max_rows:
            break
    rows.sort(key=lambda row: -row.net_pct)
    scan.rows = [row.as_dict() for row in rows]
    return scan


def render_cross_region(scan: CrossRegionScan) -> str:
    lines = [
        "# Cross-region scan",
        "",
        f"Generated {scan.generated_at} at {scan.notional_isk / 1e9:.2f}B ISK notional.",
        "",
        "Buy in hub A, haul, sell in hub B over days. Freight is a real PushX quote at",
        "the SDE packaged volume plus collateral. **No freight quote → no row, ever.**",
        "",
        f"- Pairs considered: {scan.pairs_considered:,}",
        f"- Dropped, no depth at this notional: {scan.dropped_no_depth:,}",
        f"- Dropped, no freight quote: {scan.dropped_no_freight:,}",
        f"- Dropped, negative after costs: {scan.dropped_negative:,}",
        "",
    ]
    if not scan.rows:
        lines.append("**Nothing clears costs today.** That is a valid, expected result.")
    else:
        lines.append("| type | route | buy | sell | m³ | freight | net ISK | net % | flags |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
        for row in scan.rows:
            lines.append(
                f"| {row['type_name'] or row['type_id']} | {row['freight_route']} "
                f"| {row['buy_price']:,.2f} | {row['sell_price']:,.2f} "
                f"| {row['packaged_volume_m3']:,.0f} | {row['freight_isk']:,.0f} "
                f"| {row['net_isk']:,.0f} | {row['net_pct']:.2f}% "
                f"| {'; '.join(row.get('flags') or []) or '—'} |"
            )
    if scan.notes:
        lines.extend(["", "## Notes"])
        for note in dict.fromkeys(scan.notes):
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"
