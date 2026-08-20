"""R1 — executable book identity and snapshot validity (plan.md §21 R1).

The contract these tests pin down:

**A spread is only a spread if one character could actually trade both sides.**
The old reduction grouped by `(type_id, side)` and kept price and volume only,
so a region-wide lowest ask at Jita 4-4 and a region-wide highest bid at some
other station — or inside an Upwell structure the operator cannot dock at —
were joined and called an executable round trip. They are not: nobody can buy
at one and sell at the other without hauling.

**A partial sweep is never the priceable book.** A missing pagination page can
hold the true best level, so a partial snapshot cannot be allowed to displace
the last complete one.
"""

from __future__ import annotations

import pandas as pd
import pytest

from evescreener.books import (
    load_validated_book,
    reduce_orders,
    spread_view,
)
from evescreener.store.lake import BookLake
from evescreener.timeutil import parse_iso

TIERS = (250_000_000.0, 1_000_000_000.0, 2_500_000_000.0)

JITA_44 = 60003760  # NPC station
OTHER_STATION = 60003761  # a different NPC station in the same region
STRUCTURE = 1035466617946  # 13-digit Upwell structure


def order(order_id, type_id, price, volume, *, buy=False, location=JITA_44, range="region"):
    record = {
        "order_id": order_id,
        "type_id": type_id,
        "price": price,
        "volume_remain": volume,
        "is_buy_order": buy,
        "location_id": location,
    }
    if buy:
        record["range"] = range
    return record


def _rows(result):
    sell = result.frame[result.frame["side"] == "sell"]
    buy = result.frame[result.frame["side"] == "buy"]
    return (
        sell.iloc[0] if len(sell) else None,
        buy.iloc[0] if len(buy) else None,
    )


# -- 1. the reduction must keep what makes a quote executable ---------------


def test_the_reduction_preserves_location_and_buy_order_range():
    """Price and volume alone cannot answer 'could I have traded this?'."""
    result = reduce_orders(
        [
            order(1, 34, 5.0, 100, location=JITA_44),
            order(2, 34, 4.0, 100, buy=True, location=OTHER_STATION, range="station"),
        ],
        region_id=10000002,
        notional_tiers=TIERS,
    )
    sell, buy = _rows(result)
    assert sell["best_location_id"] == JITA_44
    assert buy["best_location_id"] == OTHER_STATION
    assert buy["best_range"] == "station"
    # A sell order has no range in EVE; it is executable only where it rests.
    assert sell["best_range"] is None or pd.isna(sell["best_range"])


def test_a_bid_at_another_station_is_not_an_executable_exit():
    """The defect this phase exists to fix (§21 R1).

    Lowest ask at Jita 4-4, highest bid station-ranged at another station.
    Region-wide extrema call that a 25% spread; nobody can trade it.
    """
    result = reduce_orders(
        [
            order(1, 34, 100.0, 500, location=JITA_44),
            order(2, 34, 125.0, 500, buy=True, location=OTHER_STATION, range="station"),
            order(3, 34, 80.0, 500, buy=True, location=JITA_44, range="station"),
        ],
        region_id=10000002,
        notional_tiers=TIERS,
    )
    sell, buy = _rows(result)
    # The region-wide extremum is preserved for diagnostics...
    assert buy["best_price"] == 125.0
    # ...but the executable bid at the venue where the asks rest is the local one.
    assert sell["exec_location_id"] == JITA_44
    assert buy["exec_location_id"] == JITA_44
    assert buy["exec_price"] == 80.0
    assert sell["exec_price"] == 100.0

    view = spread_view(result.frame)
    assert len(view) == 1
    row = view.iloc[0]
    assert row["best_bid"] == 80.0, "the spread must be the executable one"
    assert row["best_ask"] == 100.0
    assert row["spread_pct"] > 0


@pytest.mark.parametrize(
    ("buy_range", "reachable"),
    [
        ("region", True),  # a region-ranged bid is hittable from anywhere in it
        ("station", False),  # station-ranged, and it is not this station
        ("solarsystem", False),  # topology unresolved without the SDE: UNKNOWN
        ("system", False),
        ("5", False),  # jump range, likewise unresolved
        ("40", False),
    ],
)
def test_buy_order_range_decides_whether_a_remote_bid_is_reachable(buy_range, reachable):
    """Range is what makes a remote bid hittable. Unresolvable means no."""
    result = reduce_orders(
        [
            order(1, 34, 100.0, 500, location=JITA_44),
            order(2, 34, 125.0, 500, buy=True, location=OTHER_STATION, range=buy_range),
        ],
        region_id=10000002,
        notional_tiers=TIERS,
    )
    _sell, buy = _rows(result)
    if reachable:
        assert buy["exec_price"] == 125.0
    else:
        assert buy["exec_price"] is None or pd.isna(buy["exec_price"])
        assert spread_view(result.frame).empty, "no reachable bid means no spread"


def test_a_bid_resting_at_the_ask_venue_is_always_reachable_whatever_its_range():
    """Same station is inside every range class, including 'station'."""
    result = reduce_orders(
        [
            order(1, 34, 100.0, 500, location=JITA_44),
            order(2, 34, 90.0, 500, buy=True, location=JITA_44, range="station"),
        ],
        region_id=10000002,
        notional_tiers=TIERS,
    )
    _sell, buy = _rows(result)
    assert buy["exec_price"] == 90.0


# -- 2. structure access is uncertainty, not execution ----------------------


def test_a_structure_venue_is_flagged_rather_than_silently_executable():
    """Docking rights are not in the lake, so a structure quote is uncertain."""
    result = reduce_orders(
        [
            order(1, 34, 100.0, 500, location=STRUCTURE),
            order(2, 34, 90.0, 500, buy=True, location=STRUCTURE, range="station"),
        ],
        region_id=10000002,
        notional_tiers=TIERS,
    )
    sell, _buy = _rows(result)
    assert sell["exec_location_id"] == STRUCTURE
    assert bool(sell["exec_is_structure"]) is True
    view = spread_view(result.frame)
    assert bool(view.iloc[0]["exec_is_structure"]) is True


def test_the_executable_venue_follows_the_asks_not_the_bids():
    """Measured: ~0% of ask volume rests in structures, much bid volume does.

    Anchoring the venue on the asks therefore lands on the station the
    operator must dock at to buy, rather than on a structure they may not
    be able to enter (plan.md §17, structure exposure is on the EXIT).
    """
    result = reduce_orders(
        [
            order(1, 34, 100.0, 10, location=JITA_44),
            order(2, 34, 95.0, 100_000, buy=True, location=STRUCTURE, range="station"),
        ],
        region_id=10000002,
        notional_tiers=TIERS,
    )
    sell, buy = _rows(result)
    assert sell["exec_location_id"] == JITA_44, "the structure holds more volume but no asks"
    assert not bool(sell["exec_is_structure"])
    # And the structure bid is not reachable from Jita 4-4, so there is no spread.
    assert buy["exec_price"] is None or pd.isna(buy["exec_price"])
    assert spread_view(result.frame).empty


# -- 3. partial sweeps are diagnostics, never prices ------------------------


def test_a_partial_sweep_never_becomes_the_priceable_latest_book(tmp_path, config):
    """A missing page can hold the true best level (§21 R1)."""
    paths = config.paths
    lake = BookLake(paths)

    complete = reduce_orders(
        [order(1, 34, 100.0, 500), order(2, 34, 90.0, 500, buy=True)],
        region_id=10000002,
        notional_tiers=TIERS,
        sweep_ts="2026-08-20T10:00:00+00:00",
    )
    lake.write(complete.frame)

    partial = reduce_orders(
        [order(3, 34, 500.0, 500), order(4, 34, 5.0, 500, buy=True)],
        region_id=10000002,
        notional_tiers=TIERS,
        sweep_ts="2026-08-20T11:00:00+00:00",
        partial=True,
    )
    lake.write_partial(partial.frame)

    latest = lake.latest(10000002)
    assert not latest.empty
    assert not latest["partial_sweep"].any()
    assert pd.Timestamp(latest.iloc[0]["sweep_ts"]) == pd.Timestamp("2026-08-20T10:00:00+00:00")
    sells = latest[latest["side"] == "sell"]
    assert float(sells.iloc[0]["best_price"]) == 100.0, "the complete snapshot survives"


def test_a_partial_sweep_written_into_the_normal_lake_is_still_refused(tmp_path, config):
    """Belt and braces: the filter is on the data, not only on the write path."""
    lake = BookLake(config.paths)
    lake.write(
        reduce_orders(
            [order(1, 34, 100.0, 500), order(2, 34, 90.0, 500, buy=True)],
            region_id=10000002,
            notional_tiers=TIERS,
            sweep_ts="2026-08-20T10:00:00+00:00",
        ).frame
    )
    lake.write(
        reduce_orders(
            [order(3, 34, 500.0, 500)],
            region_id=10000002,
            notional_tiers=TIERS,
            sweep_ts="2026-08-20T12:00:00+00:00",
            partial=True,
        ).frame
    )
    latest = lake.latest(10000002)
    assert not latest["partial_sweep"].any()
    assert pd.Timestamp(latest.iloc[0]["sweep_ts"]) == pd.Timestamp("2026-08-20T10:00:00+00:00")


# -- 4. one central validated-book contract --------------------------------


def test_the_validated_book_is_unknown_when_there_is_no_sweep(config):
    snapshot = load_validated_book(config, 10_000_999)
    assert not snapshot.known
    assert snapshot.frame.empty
    assert "no complete book" in snapshot.reason


def test_the_validated_book_is_unknown_when_the_sweep_is_stale(config):
    lake = BookLake(config.paths)
    lake.write(
        reduce_orders(
            [order(1, 34, 100.0, 500), order(2, 34, 90.0, 500, buy=True)],
            region_id=10000002,
            notional_tiers=TIERS,
            sweep_ts="2020-01-01T00:00:00+00:00",
        ).frame
    )
    snapshot = load_validated_book(config, 10000002)
    assert not snapshot.known
    assert snapshot.stale
    assert snapshot.priceable.empty, "a stale book prices nothing"


def test_the_validated_book_is_known_when_the_sweep_is_complete_and_fresh(config):
    from evescreener.timeutil import iso, utcnow

    now = utcnow()
    lake = BookLake(config.paths)
    lake.write(
        reduce_orders(
            [order(1, 34, 100.0, 500), order(2, 34, 90.0, 500, buy=True)],
            region_id=10000002,
            notional_tiers=TIERS,
            sweep_ts=iso(now),
        ).frame
    )
    snapshot = load_validated_book(config, 10000002, now=now)
    assert snapshot.known
    assert snapshot.complete
    assert not snapshot.stale
    assert not snapshot.priceable.empty
    assert parse_iso(snapshot.sweep_ts) is not None


def test_a_book_predating_the_executable_contract_prices_nothing(config):
    """An old snapshot genuinely does not know where its quotes rested."""
    lake = BookLake(config.paths)
    frame = reduce_orders(
        [order(1, 34, 100.0, 500), order(2, 34, 90.0, 500, buy=True)],
        region_id=10000002,
        notional_tiers=TIERS,
        sweep_ts="2026-08-20T10:00:00+00:00",
    ).frame
    # Simulate a pre-R1 partition: the executable columns did not exist.
    legacy = frame.drop(columns=["exec_location_id", "exec_price", "exec_is_structure"])
    lake.write(legacy)
    snapshot = load_validated_book(config, 10000002, now=parse_iso("2026-08-20T10:05:00+00:00"))
    assert not snapshot.known
    assert "executable" in snapshot.reason
    assert snapshot.priceable.empty


# -- 5. spread_view is the executable join ---------------------------------


def test_spread_view_reports_both_the_region_extremum_and_the_executable_quote():
    """Keeping the old number visible is how the correction stays auditable."""
    result = reduce_orders(
        [
            order(1, 34, 100.0, 500, location=JITA_44),
            order(2, 34, 125.0, 500, buy=True, location=OTHER_STATION, range="station"),
            order(3, 34, 80.0, 500, buy=True, location=JITA_44, range="station"),
        ],
        region_id=10000002,
        notional_tiers=TIERS,
    )
    row = spread_view(result.frame).iloc[0]
    assert row["best_bid"] == 80.0
    assert row["region_best_bid"] == 125.0
    assert row["exec_location_id"] == JITA_44


def test_spread_view_drops_a_type_with_no_executable_pair():
    result = reduce_orders(
        [
            order(1, 34, 100.0, 500, location=JITA_44),
            order(2, 34, 90.0, 500, buy=True, location=OTHER_STATION, range="station"),
        ],
        region_id=10000002,
        notional_tiers=TIERS,
    )
    assert spread_view(result.frame).empty
