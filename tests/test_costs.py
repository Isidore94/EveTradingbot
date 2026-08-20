"""The cost model. Gross margins never appear; UNKNOWN is never zero."""

from __future__ import annotations

import pytest

from evescreener.config import config_from_mapping, load_example
from evescreener.costs import CostModel


@pytest.fixture
def model(config):
    return CostModel.from_config(config)


def test_sales_tax_at_accounting_five(model):
    assert model.sales_tax_pct == pytest.approx(3.375)


def test_broker_fee_at_broker_relations_five(model):
    assert model.broker_fee_pct == pytest.approx(1.0)


def test_skills_are_config_not_constants(repo_root):
    raw = load_example(repo_root)
    raw["costs"]["accounting_level"] = 0
    raw["costs"]["broker_relations_level"] = 0
    raw["costs"]["broker_fee_standings_pct"] = 0.0
    model = CostModel.from_config(config_from_mapping(raw))
    assert model.sales_tax_pct == pytest.approx(7.5)
    assert model.broker_fee_pct == pytest.approx(3.0)


def test_broker_fee_is_never_charged_on_a_taker_fill(model):
    assert model.buy_outlay(1_000_000, maker=False) == 1_000_000
    assert model.buy_outlay(1_000_000, maker=True) == pytest.approx(1_010_000)


def test_sell_always_pays_tax_and_maker_also_pays_broker(model):
    assert model.sell_proceeds(1_000_000, maker=False) == pytest.approx(966_250)
    assert model.sell_proceeds(1_000_000, maker=True) == pytest.approx(956_250)


def test_fee_floor_is_the_documented_3_4_to_4_4_percent(model):
    assert model.round_trip_fee_pct(maker_exit=False) == pytest.approx(3.375)
    assert model.round_trip_fee_pct(maker_exit=True) == pytest.approx(4.375)


def test_breakeven_includes_spread_and_fees(model):
    # Ask-walk 105, bid-walk 100: 5% spread cost plus 3.375% tax.
    breakeven = model.breakeven_move_pct(entry_price=105, exit_price=100, reference_price=102)
    assert breakeven > 8.0
    assert breakeven == pytest.approx((105 / (1 - 0.03375) / 100 - 1) * 100)


def test_missing_inputs_render_unknown_not_zero(model):
    assert model.breakeven_move_pct(entry_price=None, exit_price=100, reference_price=102) is None
    assert model.breakeven_move_pct(entry_price=105, exit_price=0, reference_price=102) is None


def test_stale_book_short_circuits_to_unknown(model):
    costs = model.price_round_trip(
        notional_isk=250_000_000,
        ask_walk_price=105,
        ask_walk_qty=10,
        bid_walk_price=100,
        reference_price=102,
        stale_reason="book swept 4h ago",
    )
    assert not costs.known
    assert costs.breakeven_move_pct is None
    assert costs.net_edge_pct_taker is None


def test_thin_book_is_unknown_not_a_wide_margin(model):
    costs = model.price_round_trip(
        notional_isk=2_500_000_000,
        ask_walk_price=105,
        ask_walk_qty=3,
        bid_walk_price=None,
        reference_price=102,
    )
    assert not costs.known
    assert "thin" in costs.unknown_reason


def test_round_trip_nets_every_number(model):
    costs = model.price_round_trip(
        notional_isk=250_000_000,
        ask_walk_price=100,
        ask_walk_qty=2_500_000,
        bid_walk_price=110,
        reference_price=105,
        maker_target_price=112,
    )
    assert costs.known
    # Net edge is after tax, never gross.
    assert costs.net_edge_pct_taker == pytest.approx((110 * (1 - 0.03375) / 100 - 1) * 100)
    assert costs.net_edge_pct_taker < 10.0
    assert costs.net_edge_pct_maker == pytest.approx((112 * (1 - 0.04375) / 100 - 1) * 100)


def test_tier_breakevens_flag_unfillable_tiers(model):
    tiers = model.tier_breakevens(
        ask_prices={250_000_000.0: 105.0, 1_000_000_000.0: 120.0, 2_500_000_000.0: None},
        bid_prices={250_000_000.0: 100.0, 1_000_000_000.0: 95.0, 2_500_000_000.0: None},
        reference_price=102,
    )
    assert [tier.fillable for tier in tiers] == [True, True, False]
    assert tiers[2].breakeven_move_pct is None
    assert tiers[1].breakeven_move_pct > tiers[0].breakeven_move_pct


def test_escrow_is_capital_days_never_free(model):
    assert model.escrow_capital_days(1_000_000_000, 10) == 10_000_000_000
