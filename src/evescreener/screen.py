"""The net-cost screen (plan.md §5, §8 Phase 0).

Joins the newest ``book_summary`` reduction to the bar lake's 30-day turnover
and nets the round trip at the smallest configured notional tier. Ranking is on
the netted number; a candidate that does not clear costs at the smallest tier
is not opportunity, and an honest zero beats a filled panel.

Every row is tri-state. A stale book, a book that cannot absorb the tier, or a
missing side all render UNKNOWN — never a silently-priced row.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from .clock import UTC, now_utc
from .config import Config
from .costs import PRICED, quote, spread_pct

SCREEN_COLUMNS = [
    "type_id",
    "name",
    "status",
    "reason",
    "best_bid",
    "best_ask",
    "spread_pct",
    "crossed_book",
    "p5_bid",
    "p5_ask",
    "entry_price",
    "entry_units",
    "net_margin_pct",
    "breakeven_move_taker_pct",
    "breakeven_move_maker_pct",
    "median_isk_value_30d",
    "median_order_count_30d",
    "bars",
    "passes_liquidity_floor",
    "top_order_volume_share",
    "station_volume_share",
    "book_age_minutes",
    "sweep_ts",
]


@dataclass(frozen=True)
class ScreenResult:
    """The screen plus the context the digest and the gate both need."""

    rows: pd.DataFrame
    notional_isk: float
    as_of: dt.datetime
    sweep_ts: dt.datetime | None
    book_age_minutes: float | None
    book_is_stale: bool

    @property
    def candidates(self) -> pd.DataFrame:
        """Priced rows whose round trip clears costs right now."""
        if self.rows.empty:
            return self.rows
        priced = self.rows[self.rows["status"] == PRICED]
        return priced[priced["net_margin_pct"] > 0.0]


def build_screen(
    config: Config,
    *,
    book: pd.DataFrame,
    turnover: pd.DataFrame,
    names: dict[int, str],
    type_ids: list[int],
    as_of: dt.datetime | None = None,
) -> ScreenResult:
    """Build the ranked net-cost screen for ``type_ids``."""
    as_of = (as_of or now_utc()).astimezone(UTC)
    notional = config.market.notional_tiers_isk[0]
    tax = config.costs.sales_tax_rate
    broker = config.costs.broker_fee_rate

    sweep_ts, age_minutes, stale = _book_freshness(book, config, as_of)
    sides = _by_side(book, type_ids)
    turnover_by_type = (
        turnover.set_index("type_id").to_dict("index") if not turnover.empty else {}
    )

    rows = []
    for type_id in sorted(type_ids):
        sell = sides.get((type_id, "sell"))
        buy = sides.get((type_id, "buy"))
        stats = turnover_by_type.get(type_id, {})
        row: dict[str, object] = {
            "type_id": type_id,
            "name": names.get(type_id, f"type {type_id}"),
            "best_bid": buy["best_price"] if buy else float("nan"),
            "best_ask": sell["best_price"] if sell else float("nan"),
            "p5_bid": buy["p5_price"] if buy else float("nan"),
            "p5_ask": sell["p5_price"] if sell else float("nan"),
            "median_isk_value_30d": stats.get("median_isk_value_30d", float("nan")),
            "median_order_count_30d": stats.get("median_order_count_30d", float("nan")),
            "bars": stats.get("bars", 0),
            "top_order_volume_share": max(
                (side["top_order_volume_share"] for side in (buy, sell) if side),
                default=float("nan"),
            ),
            "station_volume_share": (
                sell["station_volume_share"] if sell else float("nan")
            ),
            "book_age_minutes": age_minutes,
            "sweep_ts": sweep_ts,
        }
        row["spread_pct"] = spread_pct(row["best_bid"], row["best_ask"])
        # Region-wide books cross: a bid at one station can sit above an ask at
        # another, and a lone cheap sell order does the same thing (§9 R2). The
        # depth walk prices that away, but top-of-book must be flagged, not
        # quietly shown as a 17% spread.
        row["crossed_book"] = bool(
            pd.notna(row["best_bid"])
            and pd.notna(row["best_ask"])
            and row["best_bid"] > row["best_ask"]
        )
        row["passes_liquidity_floor"] = _passes_floor(config, stats)

        if sell is None or buy is None:
            row.update(_unpriced("no resting orders on both sides"))
        elif stale:
            row.update(
                _unpriced(f"book is {age_minutes:.0f} min old; depth cost unknown")
            )
        else:
            netted = quote(
                notional_isk=notional,
                ask_walk_price=sell["depth_fill_price_1"],
                ask_walk_units=int(sell["depth_fill_qty_1"]),
                bid_walk_price=buy["depth_fill_price_1"],
                best_ask=sell["best_price"],
                tax_rate=tax,
                broker_rate=broker,
            )
            row.update(
                {
                    "status": netted.status,
                    "reason": netted.reason,
                    "entry_price": netted.entry_price,
                    "entry_units": netted.entry_units,
                    "net_margin_pct": netted.net_margin_pct,
                    "breakeven_move_taker_pct": netted.breakeven_move_taker_pct,
                    "breakeven_move_maker_pct": netted.breakeven_move_maker_pct,
                }
            )
        rows.append(row)

    frame = pd.DataFrame(rows, columns=SCREEN_COLUMNS)
    if not frame.empty:
        frame = frame.sort_values(
            ["status", "net_margin_pct"],
            ascending=[True, False],
            na_position="last",
        ).reset_index(drop=True)

    return ScreenResult(
        rows=frame,
        notional_isk=notional,
        as_of=as_of,
        sweep_ts=sweep_ts,
        book_age_minutes=age_minutes,
        book_is_stale=stale,
    )


def _unpriced(reason: str) -> dict[str, object]:
    nan = float("nan")
    return {
        "status": "unknown",
        "reason": reason,
        "entry_price": nan,
        "entry_units": 0,
        "net_margin_pct": nan,
        "breakeven_move_taker_pct": nan,
        "breakeven_move_maker_pct": nan,
    }


def _passes_floor(config: Config, stats: dict) -> bool:
    isk = stats.get("median_isk_value_30d")
    orders = stats.get("median_order_count_30d")
    if isk is None or orders is None:
        return False
    return (
        float(isk) >= config.liquidity.min_median_isk_value_30d
        and float(orders) >= config.liquidity.min_median_order_count_30d
    )


def _by_side(book: pd.DataFrame, type_ids: list[int]) -> dict[tuple[int, str], dict]:
    if book.empty:
        return {}
    subset = book[book["type_id"].isin(type_ids)]
    return {
        (int(row["type_id"]), str(row["side"])): row
        for row in subset.to_dict("records")
    }


def _book_freshness(
    book: pd.DataFrame, config: Config, as_of: dt.datetime
) -> tuple[dt.datetime | None, float | None, bool]:
    if book.empty:
        return None, None, True
    sweep_ts = pd.Timestamp(book["sweep_ts"].max()).to_pydatetime()
    if sweep_ts.tzinfo is None:
        sweep_ts = sweep_ts.replace(tzinfo=UTC)
    age_minutes = (as_of - sweep_ts).total_seconds() / 60.0
    return sweep_ts, age_minutes, age_minutes > config.market.book_staleness_minutes
