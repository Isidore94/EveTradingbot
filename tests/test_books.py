"""Book reduction: bait resistance, depth walks, and the structure blind spot."""

from __future__ import annotations

import pytest

from evescreener.books import (
    depth_walk,
    is_npc_station,
    p5_price,
    reduce_orders,
    spread_view,
)

TIERS = (250_000_000.0, 1_000_000_000.0, 2_500_000_000.0)


def order(order_id, type_id, price, volume, *, buy=False, location=60003760):
    return {
        "order_id": order_id,
        "type_id": type_id,
        "price": price,
        "volume_remain": volume,
        "is_buy_order": buy,
        "location_id": location,
    }


def test_depth_walk_prices_the_bait_in():
    # A 1-unit bait at 1000 sits on top of a real book at 100.
    levels = [(1000.0, 1.0), (100.0, 10_000.0)]
    price, units = depth_walk(levels, 250_000.0)
    assert price == pytest.approx((1000.0 * 1 + 100.0 * 2490) / 2491, rel=1e-3)
    assert price < 101.0, "one bait unit must not move the effective price materially"
    assert units == pytest.approx(2491, rel=1e-3)


def test_depth_walk_returns_unknown_when_the_book_cannot_absorb_the_size():
    levels = [(100.0, 5.0)]
    assert depth_walk(levels, 250_000_000.0) == (None, None)


def test_depth_walk_of_an_empty_book_is_unknown():
    assert depth_walk([], 1.0) == (None, None)


def test_p5_price_is_volume_weighted_over_the_best_five_percent():
    levels = [(100.0, 50.0), (110.0, 950.0)]
    # 5% of 1000 units = 50 units, all at 100.
    assert p5_price(levels) == pytest.approx(100.0)


def test_p5_fails_where_margins_look_widest_and_the_walk_does_not():
    # Thin book: the bait IS the top 5%. p5 is fooled; the 250M walk is not.
    levels = [(1000.0, 1.0), (100.0, 10.0)]
    assert p5_price(levels) == pytest.approx(1000.0)
    assert depth_walk(levels, 250_000_000.0) == (None, None)


def test_structure_ids_are_thirteen_digits():
    assert is_npc_station(60003760)  # Jita 4-4
    assert not is_npc_station(1035466617946)


def test_reduce_orders_produces_one_row_per_type_and_side():
    orders = [
        order(1, 34, 5.0, 1_000_000_000),
        order(2, 34, 5.1, 500_000_000),
        order(3, 34, 4.0, 1_000_000_000, buy=True),
    ]
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    assert len(result.frame) == 2
    sell = result.frame[result.frame["side"] == "sell"].iloc[0]
    assert sell["best_price"] == 5.0
    assert sell["order_count"] == 2
    assert sell["total_volume"] == 1_500_000_000


def test_reduce_orders_counts_cross_page_duplicates_rather_than_failing():
    orders = [order(1, 34, 5.0, 100), order(1, 34, 5.0, 100), order(2, 34, 5.1, 100)]
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    assert result.duplicate_order_ids == 1
    assert result.frame.iloc[0]["order_count"] == 2


def test_top_order_share_flags_a_one_order_book():
    orders = [order(1, 34, 5.0, 999), order(2, 34, 6.0, 1)]
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    assert result.frame.iloc[0]["top_order_volume_share"] == pytest.approx(0.999)


def test_station_volume_share_quantifies_the_structure_blind_spot():
    orders = [
        order(1, 34, 5.0, 300, location=60003760),
        order(2, 34, 5.0, 700, location=1035466617946),
    ]
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    assert result.frame.iloc[0]["station_volume_share"] == pytest.approx(0.3)
    assert result.structure_volume_share == pytest.approx(0.7)


def test_depth_tiers_land_in_their_own_columns():
    orders = [order(index, 34, 5.0, 1_000_000_000) for index in range(1, 3)]
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    row = result.frame.iloc[0]
    assert row["depth_fill_price_0"] == pytest.approx(5.0)
    assert row["depth_fill_qty_0"] == pytest.approx(50_000_000)
    assert row["depth_fill_price_2"] == pytest.approx(5.0)


def test_asks_ascend_and_bids_descend():
    orders = [
        order(1, 34, 6.0, 100),
        order(2, 34, 5.0, 100),
        order(3, 34, 3.0, 100, buy=True),
        order(4, 34, 4.0, 100, buy=True),
    ]
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    sell = result.frame[result.frame["side"] == "sell"].iloc[0]
    buy = result.frame[result.frame["side"] == "buy"].iloc[0]
    assert sell["best_price"] == 5.0
    assert buy["best_price"] == 4.0


def test_spread_view_joins_both_sides():
    orders = [order(1, 34, 5.0, 1_000_000_000), order(2, 34, 4.0, 1_000_000_000, buy=True)]
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    view = spread_view(result.frame)
    assert len(view) == 1
    assert view.iloc[0]["spread_pct"] == pytest.approx((5.0 - 4.0) / 4.5 * 100)


def test_spread_view_drops_one_sided_books():
    orders = [order(1, 34, 5.0, 100)]
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    assert spread_view(result.frame).empty


def test_zero_volume_orders_are_ignored():
    orders = [order(1, 34, 5.0, 0), order(2, 34, 6.0, 100)]
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    assert result.frame.iloc[0]["best_price"] == 6.0
    assert result.frame.iloc[0]["order_count"] == 1


def test_book_lake_round_trips_and_keeps_the_latest_sweep(paths):
    from evescreener.store.lake import BookLake

    lake = BookLake(paths)
    early = reduce_orders(
        [order(1, 34, 5.0, 100)],
        region_id=10000002,
        notional_tiers=TIERS,
        sweep_ts="2026-08-20T12:00:00+00:00",
    )
    late = reduce_orders(
        [order(1, 34, 6.0, 100)],
        region_id=10000002,
        notional_tiers=TIERS,
        sweep_ts="2026-08-20T12:05:00+00:00",
    )
    lake.write(early.frame)
    lake.write(late.frame)
    latest = lake.latest(10000002)
    assert len(latest) == 1
    assert latest.iloc[0]["best_price"] == 6.0
