import datetime as dt

import numpy as np
import pandas as pd
import pytest

from evescreener.books import (
    _percentile_price,
    _walk,
    latest_book_summary,
    reduce_orders,
    write_book_summary,
)
from evescreener.clock import UTC

from .conftest import load_fixture

SWEEP_TS = dt.datetime(2026, 8, 18, 2, 34, 16, tzinfo=UTC)
EXPIRES_TS = dt.datetime(2026, 8, 18, 2, 35, 16, tzinfo=UTC)
TIERS = (250_000_000.0, 1_000_000_000.0, 2_500_000_000.0)


@pytest.fixture
def orders():
    return pd.DataFrame(load_fixture("esi_orders_forge_three_types.json")["response"])


@pytest.fixture
def summary(orders):
    return reduce_orders(
        orders,
        region_id=10000002,
        sweep_ts=SWEEP_TS,
        expires_ts=EXPIRES_TS,
        notional_tiers=TIERS,
    )


def test_reduction_matches_the_frozen_golden_frame(summary):
    golden = pd.DataFrame(load_fixture("golden_book_summary_three_types.json")["rows"])
    actual = summary.drop(columns=["sweep_ts", "expires_ts"])
    pd.testing.assert_frame_equal(
        actual.reset_index(drop=True),
        golden.reset_index(drop=True),
        check_dtype=False,
    )


def test_walk_returns_the_effective_unit_price_not_the_top_of_book():
    prices = np.array([10.0, 11.0, 12.0])
    volumes = np.array([10, 10, 10])
    price, units = _walk(prices, volumes, 150.0)
    # 10 units at 10 ISK (=100) then 50 ISK more at 11 ISK => 4.545 units.
    assert units == 14
    assert price == pytest.approx(150.0 / (10 + 50 / 11))


def test_walk_reports_unknown_when_the_book_cannot_absorb_the_notional():
    price, units = _walk(np.array([10.0]), np.array([5]), 1_000.0)
    assert np.isnan(price), "cannot fill is a distinct answer from fills at X"
    assert units == 5


def test_walk_on_an_empty_side_is_unknown():
    price, units = _walk(np.array([]), np.array([]), 100.0)
    assert np.isnan(price)
    assert units == 0


def test_percentile_price_takes_exactly_five_percent_of_resting_volume():
    prices = np.array([1.0, 2.0, 3.0])
    volumes = np.array([10, 10, 80])
    # 5% of 100 units = 5 units, all from the best-priced order.
    assert _percentile_price(prices, volumes) == pytest.approx(1.0)


def test_percentile_price_blends_across_the_boundary_order():
    prices = np.array([1.0, 2.0])
    volumes = np.array([4, 96])
    # 5 units: 4 at 1.0 plus 1 at 2.0.
    assert _percentile_price(prices, volumes) == pytest.approx((4 * 1.0 + 1 * 2.0) / 5)


def test_a_lone_bait_sell_order_does_not_set_the_depth_walk_price(summary):
    zydrine = summary[(summary.type_id == 39) & (summary.side == "sell")].iloc[0]
    assert zydrine["best_price"] == 1000.0
    assert zydrine["depth_fill_price_1"] > 1190.0, (
        "the walk must price the bait in and dilute it (§9 R2)"
    )


def test_a_book_dominated_by_one_order_is_flagged(summary):
    zydrine_bid = summary[(summary.type_id == 39) & (summary.side == "buy")].iloc[0]
    assert zydrine_bid["top_order_volume_share"] > 0.5


def test_a_thin_book_reports_unknown_at_every_tier_it_cannot_fill(summary):
    thin = summary[(summary.type_id == 210) & (summary.side == "sell")].iloc[0]
    assert np.isnan(thin["depth_fill_price_1"])
    assert thin["depth_fill_qty_1"] == thin["total_volume"]


def test_station_volume_share_separates_npc_stations_from_structures():
    orders = pd.DataFrame(
        [
            {
                "type_id": 1,
                "is_buy_order": True,
                "price": 10.0,
                "volume_remain": 30,
                "location_id": 60003760,
                "order_id": 1,
            },
            {
                "type_id": 1,
                "is_buy_order": True,
                "price": 9.0,
                "volume_remain": 70,
                "location_id": 1_044_752_365_771,
                "order_id": 2,
            },
        ]
    )
    row = reduce_orders(
        orders,
        region_id=10000002,
        sweep_ts=SWEEP_TS,
        expires_ts=EXPIRES_TS,
        notional_tiers=TIERS,
    ).iloc[0]
    assert row["station_volume_share"] == pytest.approx(0.3)


def test_sides_fill_from_the_right_end_of_the_book(summary):
    sell = summary[(summary.type_id == 34) & (summary.side == "sell")].iloc[0]
    buy = summary[(summary.type_id == 34) & (summary.side == "buy")].iloc[0]
    # Buying walks up from the cheapest ask; selling walks down from the top bid.
    assert sell["depth_fill_price_1"] >= sell["best_price"]
    assert buy["depth_fill_price_1"] <= buy["best_price"]


def test_reducing_an_empty_sweep_yields_an_empty_summary():
    assert reduce_orders(
        pd.DataFrame(),
        region_id=10000002,
        sweep_ts=SWEEP_TS,
        expires_ts=EXPIRES_TS,
        notional_tiers=TIERS,
    ).empty


def test_write_and_read_back_the_latest_sweep(config, summary):
    write_book_summary(config.paths, 10000002, summary, SWEEP_TS)
    later = summary.copy()
    later["sweep_ts"] = SWEEP_TS + dt.timedelta(minutes=5)
    write_book_summary(config.paths, 10000002, later, SWEEP_TS)
    latest = latest_book_summary(config.paths, 10000002)
    assert len(latest) == len(summary)
    assert latest["sweep_ts"].max() == later["sweep_ts"].max()


def test_latest_book_summary_is_empty_when_no_sweep_exists(config):
    assert latest_book_summary(config.paths, 10000002).empty
