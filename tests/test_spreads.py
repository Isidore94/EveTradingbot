"""The maker read, and the dust bid it exists to refuse (plan.md §20.2)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evescreener.books import reduce_orders
from evescreener.costs import CostModel
from evescreener.spreads import (
    DEFAULT_MIN_UNITS,
    HubSpreads,
    filter_rows,
    hub_choices,
    maker_edge_frame,
    maker_spreads,
)

JITA_44 = 60003760
TIERS = (250_000_000.0, 1_000_000_000.0, 2_500_000_000.0)


def _book(rows) -> pd.DataFrame:
    """`rows` is (type_id, best_bid, best_ask).

    Built through the real `reduce_orders` rather than hand-rolled records, so
    these fixtures carry the executable identity R1 requires and cannot drift
    away from what a sweep actually produces. Both sides rest at Jita 4-4, so
    every quote here is executable at one venue by construction; the
    incompatible-location cases live in `test_books_executable.py`.
    """
    orders = []
    order_id = 0
    for type_id, bid, ask in rows:
        for side_is_buy, price in ((False, ask), (True, bid)):
            # Two orders a side so no single order owns the book.
            for offset, volume in ((0.0, 400.0), (0.01, 100.0)):
                order_id += 1
                record = {
                    "order_id": order_id,
                    "type_id": int(type_id),
                    "price": float(price) + (-offset if side_is_buy else offset),
                    "volume_remain": volume,
                    "is_buy_order": side_is_buy,
                    "location_id": JITA_44,
                }
                if side_is_buy:
                    record["range"] = "station"
                orders.append(record)
    return reduce_orders(orders, region_id=10000002, notional_tiers=TIERS).frame


@pytest.fixture
def costs(config) -> CostModel:
    return CostModel.from_config(config)


# -- the arithmetic ---------------------------------------------------------


def test_net_edge_is_measured_against_what_is_actually_committed(costs):
    """Not the mid: a maker who measures on the mid counts half the spread twice."""
    frame = maker_edge_frame(
        _book([(1, 100.0, 130.0)]),
        costs,
        averages={1: 110.0},
        volumes={1: 5000.0},
    )
    row = frame.iloc[0]
    outlay = costs.buy_outlay(100.0, maker=True)
    proceeds = costs.sell_proceeds(130.0, maker=True)
    assert row["net_isk"] == pytest.approx(proceeds - outlay)
    assert row["net_pct"] == pytest.approx((proceeds - outlay) / outlay * 100.0)
    # Both sides pay broker; only the sale pays tax.
    assert outlay > 100.0 and proceeds < 130.0
    assert row["state"] == "OK"


def test_the_maker_round_trip_costs_broker_twice_and_tax_once(costs):
    """The fee floor a spread has to clear before any of it is the operator's."""
    fee_pct = costs.sales_tax_pct + 2 * costs.broker_fee_pct
    frame = maker_edge_frame(_book([(1, 100.0, 100.0)]), costs, averages={1: 100.0})
    # Bid == ask means the whole round trip is fee, and it is a loss.
    assert frame.iloc[0]["net_pct"] == pytest.approx(-fee_pct, abs=0.2)


# -- the guard this page exists for -----------------------------------------


def test_a_dust_bid_is_flagged_not_ranked(costs):
    """0.02 bid against a 129,000 ask is not a 608,000,000% edge (§17 D-31)."""
    frame = maker_edge_frame(
        _book([(1, 0.02, 129000.0), (2, 300.0, 500.0)]),
        costs,
        averages={1: 100000.0, 2: 400.0},
        volumes={1: 140.0, 2: 900.0},
    )
    dust = frame[frame["type_id"] == 1].iloc[0]
    real = frame[frame["type_id"] == 2].iloc[0]
    assert dust["state"] == "DUST_BID"
    assert real["state"] == "OK"
    # The arithmetic is still reported — it is flagged, not hidden or repaired.
    assert dust["net_pct"] > 1_000_000
    assert filter_rows(frame)["type_id"].tolist() == [2]


def test_a_fantasy_ask_is_flagged_too(costs):
    frame = maker_edge_frame(
        _book([(1, 90.0, 10_000.0)]), costs, averages={1: 100.0}, volumes={1: 900.0}
    )
    assert frame.iloc[0]["state"] == "WIDE_ASK"
    assert filter_rows(frame).empty


def test_a_book_with_no_traded_average_cannot_be_judged(costs):
    """No average means no anchor; that is UNKNOWN, never an opportunity."""
    frame = maker_edge_frame(_book([(1, 90.0, 200.0)]), costs, volumes={1: 900.0})
    assert frame.iloc[0]["state"] == "NO_AVG"
    assert filter_rows(frame).empty


def test_showing_excluded_rows_puts_the_rejects_back_with_their_flags(costs):
    """The operator must be able to check the guard rather than trust it."""
    frame = maker_edge_frame(
        _book([(1, 0.02, 129000.0), (2, 300.0, 500.0)]),
        costs,
        averages={1: 100000.0, 2: 400.0},
        volumes={1: 140.0, 2: 900.0},
    )
    shown = filter_rows(frame, only_ok=False, positive_only=False, min_units=0)
    assert set(shown["type_id"]) == {1, 2}
    assert "DUST_BID" in set(shown["state"])


# -- staleness --------------------------------------------------------------


def test_a_stale_book_prices_nothing(costs):
    """A stale quote is not a cheap quote, it is an unmeasured one (§4)."""
    frame = maker_edge_frame(
        _book([(1, 300.0, 500.0)]), costs, averages={1: 400.0}, volumes={1: 900.0}, stale=True
    )
    row = frame.iloc[0]
    assert row["state"] == "STALE"
    assert not np.isfinite(row["net_pct"])
    assert not np.isfinite(row["net_isk"])
    assert filter_rows(frame).empty


def test_a_hub_with_no_book_says_so_rather_than_looking_empty(config, tmp_path):
    """An omitted hub reads as 'no spreads', which is a different, false claim."""
    result = maker_spreads(config, [10_000_999])
    assert len(result) == 1
    hub = result[0]
    assert isinstance(hub, HubSpreads)
    assert hub.rows.empty
    assert "no complete book on disk" in hub.note
    assert not hub.known


# -- the dropdown -----------------------------------------------------------


def test_the_hub_dropdown_offers_every_configured_hub_and_an_all_entry(config):
    choices = hub_choices(config)
    labels = [label for label, _regions in choices]
    assert labels[-1] == "All hubs"
    assert len(labels) == len(config.freight.hub_systems) + 1
    every = choices[-1][1]
    assert len(every) == len(config.freight.hub_systems)
    for _label, regions in choices[:-1]:
        assert len(regions) == 1
        assert regions[0] in every


def test_filtering_is_exclusion_and_ordering_only(costs):
    """Rows are dropped or sorted. No value is ever rewritten by the filter."""
    frame = maker_edge_frame(
        _book([(1, 300.0, 500.0), (2, 300.0, 340.0)]),
        costs,
        averages={1: 400.0, 2: 320.0},
        volumes={1: 900.0, 2: 50.0},
    )
    shown = filter_rows(frame, min_units=DEFAULT_MIN_UNITS)
    assert shown["type_id"].tolist() == [1], "type 2 is under the volume floor"
    for column in ("best_bid", "best_ask", "net_pct"):
        original = frame.set_index("type_id")[column]
        kept = shown.set_index("type_id")[column]
        for type_id in kept.index:
            assert kept[type_id] == original[type_id]
