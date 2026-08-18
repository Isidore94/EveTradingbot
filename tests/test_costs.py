import math

import pytest

from evescreener.costs import PRICED, UNKNOWN, quote, sales_tax_rate, spread_pct

TAX = 0.03375  # Accounting V
BROKER = 0.01  # Broker Relations V


@pytest.mark.parametrize(
    ("level", "expected"),
    [(0, 0.075), (4, 0.0420), (5, 0.03375)],
)
def test_sales_tax_scales_with_accounting(level, expected):
    assert sales_tax_rate(7.5, level) == pytest.approx(expected)


def _quote(**overrides):
    args = {
        "notional_isk": 250_000_000.0,
        "ask_walk_price": 100.0,
        "ask_walk_units": 2_500_000,
        "bid_walk_price": 90.0,
        "best_ask": 99.0,
        "tax_rate": TAX,
        "broker_rate": BROKER,
    }
    args.update(overrides)
    return quote(**args)


def test_a_round_trip_is_netted_not_gross():
    result = _quote()
    assert result.status == PRICED
    assert result.exit_taker_net == pytest.approx(90.0 * (1 - TAX))
    expected_margin = (90.0 * (1 - TAX) - 100.0) / 100.0 * 100.0
    assert result.net_margin_pct == pytest.approx(expected_margin)
    assert not result.clears_costs


def test_breakeven_sell_price_covers_tax_on_the_taker_exit():
    result = _quote()
    assert result.breakeven_sell_taker == pytest.approx(100.0 / (1 - TAX))
    # Selling at exactly the breakeven price nets back the entry price.
    assert result.breakeven_sell_taker * (1 - TAX) == pytest.approx(100.0)


def test_maker_exit_must_clear_tax_and_broker_fee():
    result = _quote()
    assert result.breakeven_sell_maker == pytest.approx(100.0 / (1 - TAX - BROKER))
    assert result.breakeven_sell_maker > result.breakeven_sell_taker


def test_breakeven_move_is_quoted_against_the_price_it_must_beat():
    result = _quote()
    assert result.breakeven_move_taker_pct == pytest.approx(
        (100.0 / (1 - TAX) / 90.0 - 1) * 100.0
    )
    assert result.breakeven_move_maker_pct == pytest.approx(
        (100.0 / (1 - TAX - BROKER) / 99.0 - 1) * 100.0
    )


def test_a_profitable_round_trip_clears_costs():
    result = _quote(ask_walk_price=100.0, bid_walk_price=110.0)
    assert result.clears_costs
    assert result.net_margin_pct > 0


def test_an_unfillable_sell_side_is_unknown_not_zero():
    result = _quote(ask_walk_price=float("nan"))
    assert result.status == UNKNOWN
    assert "sell side" in result.reason
    assert math.isnan(result.net_margin_pct)
    assert not result.clears_costs


def test_an_unabsorbing_buy_side_is_unknown():
    result = _quote(bid_walk_price=float("nan"))
    assert result.status == UNKNOWN
    assert "buy side" in result.reason


def test_impossible_fee_configuration_is_unknown_not_a_division_blowup():
    result = _quote(tax_rate=0.9, broker_rate=0.2)
    assert result.status == UNKNOWN


def test_spread_is_a_percentage_of_the_mid():
    assert spread_pct(90.0, 110.0) == pytest.approx(20.0)
    assert math.isnan(spread_pct(float("nan"), 110.0))


def test_a_crossed_book_yields_a_negative_spread_rather_than_being_hidden():
    assert spread_pct(110.0, 90.0) < 0
