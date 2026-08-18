import datetime as dt

import pandas as pd
import pytest

from evescreener.clock import UTC
from evescreener.screen import build_screen

AS_OF = dt.datetime(2026, 8, 18, 2, 40, tzinfo=UTC)
FRESH_SWEEP = dt.datetime(2026, 8, 18, 2, 34, tzinfo=UTC)


def book_row(type_id, side, best, walk, **overrides):
    row = {
        "type_id": type_id,
        "region_id": 10000002,
        "side": side,
        "sweep_ts": FRESH_SWEEP,
        "expires_ts": FRESH_SWEEP + dt.timedelta(minutes=1),
        "best_price": best,
        "total_volume": 1_000_000,
        "order_count": 50,
        "p5_price": best,
        "top_order_volume_share": 0.1,
        "station_volume_share": 1.0,
        "depth_fill_price_1": walk,
        "depth_fill_qty_1": 1000,
        "depth_fill_price_2": walk,
        "depth_fill_qty_2": 1000,
        "depth_fill_price_3": walk,
        "depth_fill_qty_3": 1000,
    }
    row.update(overrides)
    return row


def turnover_row(type_id, isk=1e11, orders=1000, bars=29):
    return {
        "type_id": type_id,
        "median_isk_value_30d": isk,
        "median_order_count_30d": orders,
        "bars": bars,
    }


def screen(config, book_rows, turnover_rows=None, names=None, as_of=AS_OF):
    book = pd.DataFrame(book_rows)
    type_ids = sorted({row["type_id"] for row in book_rows}) or [1]
    if turnover_rows is None:
        turnover_rows = [turnover_row(t) for t in type_ids]
    return build_screen(
        config,
        book=book,
        turnover=pd.DataFrame(turnover_rows),
        names=names or {t: f"Type {t}" for t in type_ids},
        type_ids=type_ids,
        as_of=as_of,
    )


def test_a_losing_round_trip_is_not_a_candidate(config):
    result = screen(
        config,
        [book_row(1, "sell", 100.0, 100.0), book_row(1, "buy", 99.0, 99.0)],
    )
    assert len(result.rows) == 1
    assert result.rows.iloc[0]["status"] == "priced"
    assert result.rows.iloc[0]["net_margin_pct"] < 0
    assert result.candidates.empty, "honest zero beats a filled panel"


def test_a_winning_round_trip_is_a_candidate(config):
    result = screen(
        config,
        [book_row(1, "sell", 100.0, 100.0), book_row(1, "buy", 120.0, 120.0)],
    )
    assert len(result.candidates) == 1


def test_a_stale_book_renders_unknown_rather_than_a_priced_row(config):
    late = AS_OF + dt.timedelta(minutes=config.market.book_staleness_minutes + 1)
    result = screen(
        config,
        [book_row(1, "sell", 100.0, 100.0), book_row(1, "buy", 120.0, 120.0)],
        as_of=late,
    )
    assert result.book_is_stale
    assert result.rows.iloc[0]["status"] == "unknown"
    assert "old" in result.rows.iloc[0]["reason"]
    assert result.candidates.empty


def test_a_missing_side_is_unknown_not_an_infinite_margin(config):
    result = screen(config, [book_row(1, "sell", 100.0, 100.0)])
    assert result.rows.iloc[0]["status"] == "unknown"
    assert "both sides" in result.rows.iloc[0]["reason"]


def test_a_book_that_cannot_fill_the_tier_is_unknown(config):
    result = screen(
        config,
        [
            book_row(1, "sell", 100.0, float("nan")),
            book_row(1, "buy", 120.0, 120.0),
        ],
    )
    assert result.rows.iloc[0]["status"] == "unknown"
    assert "sell side" in result.rows.iloc[0]["reason"]


def test_a_crossed_region_wide_book_is_flagged(config):
    result = screen(
        config,
        [book_row(1, "sell", 100.0, 130.0), book_row(1, "buy", 120.0, 118.0)],
    )
    row = result.rows.iloc[0]
    assert row["crossed_book"]
    assert row["spread_pct"] < 0


def test_the_liquidity_floor_is_reported_per_row(config):
    result = screen(
        config,
        [book_row(1, "sell", 100.0, 100.0), book_row(1, "buy", 99.0, 99.0)],
        turnover_rows=[turnover_row(1, isk=1e6, orders=2)],
    )
    assert not bool(result.rows.iloc[0]["passes_liquidity_floor"])


def test_a_type_with_no_bars_does_not_pass_the_floor(config):
    result = screen(
        config,
        [book_row(1, "sell", 100.0, 100.0), book_row(1, "buy", 99.0, 99.0)],
        turnover_rows=[],
    )
    assert not bool(result.rows.iloc[0]["passes_liquidity_floor"])


def test_no_book_at_all_yields_unknown_rows_not_an_exception(config):
    result = build_screen(
        config,
        book=pd.DataFrame(),
        turnover=pd.DataFrame(),
        names={1: "Type 1"},
        type_ids=[1],
        as_of=AS_OF,
    )
    assert result.book_is_stale
    assert result.rows.iloc[0]["status"] == "unknown"


def test_priced_rows_sort_ahead_of_unknown_rows(config):
    result = screen(
        config,
        [
            book_row(1, "sell", 100.0, 100.0),
            book_row(1, "buy", 99.0, 99.0),
            book_row(2, "sell", 100.0, 100.0),
        ],
    )
    assert list(result.rows["status"]) == ["priced", "unknown"]


def test_the_screen_nets_at_the_smallest_configured_tier(config):
    result = screen(
        config,
        [book_row(1, "sell", 100.0, 100.0), book_row(1, "buy", 99.0, 99.0)],
    )
    assert result.notional_isk == pytest.approx(config.market.notional_tiers_isk[0])
