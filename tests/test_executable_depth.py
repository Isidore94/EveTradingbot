"""S2 — executable identity must cover depth, and pricing must use the validator.

**S2a.** R1 made `exec_price` respect `reachable_from()`, but left `p5_price`,
`depth_fill_price_*`, the quantities and `top_order_volume_share` computed over
**region-wide** levels. Screen, paper and backtest all consume those fields, so
a row could carry an executable ask of 100 and an ask *fill* of 9.26 taken from
a venue the operator cannot reach — a physically incompatible book, and a
strictly optimistic one on both sides.

**S2b.** `load_validated_book()` correctly rejects the operator's stored
pre-R1 snapshot, and production then priced off it anyway: `paper.book_quote`
returned 9.2584 with `stale=False`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evescreener.books import reduce_orders, spread_view

JITA = 60003760
FAR = 60003761
STRUCTURE = 1035466617946
TIERS = (1000.0,)


def order(oid, price, volume, *, buy=False, location=JITA, rng="region"):
    record = {
        "order_id": oid,
        "type_id": 34,
        "price": float(price),
        "volume_remain": float(volume),
        "is_buy_order": buy,
        "location_id": location,
    }
    if buy:
        record["range"] = rng
    return record


#: Sol's reproduction, verbatim.
INCOMPATIBLE = [
    order(1, 100.0, 100, location=JITA),
    order(2, 90.0, 100, buy=True, location=JITA, rng="station"),
    order(3, 1.0, 99, location=FAR),
    order(4, 1000.0, 100, buy=True, location=FAR, rng="station"),
]


def _sides(orders):
    result = reduce_orders(orders, region_id=10000002, notional_tiers=TIERS)
    frame = result.frame
    return (
        frame[frame["side"] == "sell"].iloc[0],
        frame[frame["side"] == "buy"].iloc[0],
    )


# -- 1. depth follows the executable venue ----------------------------------


def test_the_depth_walk_cannot_fill_at_a_venue_the_operator_cannot_reach():
    """Ask fill was 9.258402 while the executable ask was 100 (§22 S2a)."""
    sell, buy = _sides(INCOMPATIBLE)
    assert sell["exec_location_id"] == JITA
    assert sell["exec_price"] == 100.0
    assert buy["exec_price"] == 90.0
    # Every executable field is now priced at that one venue.
    assert sell["depth_fill_price_0"] >= 100.0, "cannot buy below the reachable ask"
    assert buy["depth_fill_price_0"] <= 90.0, "cannot sell above the reachable bid"


def test_p5_is_taken_over_reachable_volume_only():
    sell, buy = _sides(INCOMPATIBLE)
    assert sell["p5_price"] == 100.0, "the 1.0 ask rests where we cannot trade"
    assert buy["p5_price"] == 90.0, "the 1,000 bid is station-ranged elsewhere"


def test_order_concentration_describes_the_executable_book():
    """A spoof flag about orders you cannot trade against is not a flag."""
    sell, _buy = _sides(INCOMPATIBLE)
    # Only one ask is reachable, so it owns the whole executable book.
    assert sell["top_order_volume_share"] == pytest.approx(1.0)


def test_the_region_wide_numbers_survive_under_diagnostic_names():
    """The correction stays auditable: both readings remain visible."""
    sell, buy = _sides(INCOMPATIBLE)
    assert sell["region_p5_price"] == 1.0
    assert buy["region_p5_price"] == 1000.0
    assert sell["region_depth_fill_price_0"] == pytest.approx(9.258402, rel=1e-6)
    assert buy["region_depth_fill_price_0"] == 1000.0
    assert sell["region_top_order_volume_share"] < 1.0


def test_a_region_ranged_bid_elsewhere_still_counts_as_depth():
    """Reachability, not co-location: a region-ranged bid is hittable here."""
    _sell, buy = _sides(
        [
            order(1, 100.0, 100, location=JITA),
            order(2, 90.0, 10, buy=True, location=JITA, rng="station"),
            order(3, 95.0, 1000, buy=True, location=FAR, rng="region"),
        ]
    )
    assert buy["exec_price"] == 95.0
    assert buy["depth_fill_price_0"] == pytest.approx(95.0)


def test_a_book_with_no_reachable_depth_reports_unknown_not_a_regional_price():
    _sell, buy = _sides(
        [
            order(1, 100.0, 100, location=JITA),
            order(2, 1000.0, 100, buy=True, location=FAR, rng="station"),
        ]
    )
    assert buy["exec_price"] is None or pd.isna(buy["exec_price"])
    assert buy["depth_fill_price_0"] is None or pd.isna(buy["depth_fill_price_0"])


# -- 2. accessibility is reachability, not NPC-station share ----------------


def test_accessibility_is_measured_by_reach_not_by_station_ownership():
    """A bid at an NPC station you cannot reach is not accessible depth.

    CCP matches a buy order by its *range* from its own location, so a
    station-ranged bid at another station is unreachable however NPC-owned it
    is — while a region-ranged bid inside a structure is reachable, because the
    seller never has to dock there.
    """
    _sell, buy = _sides(INCOMPATIBLE)
    # Both bids sit in NPC stations, so the old measure said "fully accessible".
    assert buy["station_volume_share"] == pytest.approx(1.0)
    # Half the bid volume is station-ranged somewhere else, so it is not.
    assert buy["exec_reachable_volume_share"] == pytest.approx(0.5)


def test_a_region_ranged_structure_bid_is_reachable_depth():
    _sell, buy = _sides(
        [
            order(1, 100.0, 100, location=JITA),
            order(2, 95.0, 100, buy=True, location=STRUCTURE, rng="region"),
        ]
    )
    assert buy["exec_reachable_volume_share"] == pytest.approx(1.0)
    assert buy["station_volume_share"] == pytest.approx(0.0), "it IS in a structure"


# -- 3. the spread view carries executable depth ----------------------------


def test_the_spread_view_reports_executable_depth(qtbot=None):
    view = spread_view(reduce_orders(INCOMPATIBLE, region_id=10000002, notional_tiers=TIERS).frame)
    row = view.iloc[0]
    assert row["best_ask"] == 100.0
    assert row["best_bid"] == 90.0
    assert row["region_best_bid"] == 1000.0


# -- 4. no pricing path prices a pre-R1 snapshot ----------------------------


def _legacy_frame():
    frame = reduce_orders(
        [order(1, 100.0, 100), order(2, 90.0, 100, buy=True, rng="station")],
        region_id=10000002,
        notional_tiers=TIERS,
    ).frame
    from evescreener.store.lake import EXECUTABLE_COLUMNS

    return frame.drop(columns=list(EXECUTABLE_COLUMNS))


def test_paper_refuses_to_price_a_snapshot_the_validator_rejects():
    """It returned 9.2584 with stale=False (§22 S2b)."""
    from evescreener.paper import book_quote

    quote = book_quote(_legacy_frame(), type_id=34, side="sell", tier_index=0)
    assert quote.price is None
    assert quote.stale is True
    assert "executable" in (quote.reason or "").lower()


def test_paper_still_prices_a_current_snapshot():
    """The guard must refuse only what it should."""
    from evescreener.paper import book_quote

    frame = reduce_orders(
        [order(1, 100.0, 100_000), order(2, 90.0, 100_000, buy=True, rng="station")],
        region_id=10000002,
        notional_tiers=TIERS,
    ).frame
    quote = book_quote(frame, type_id=34, side="sell", tier_index=0)
    assert quote.price is not None and np.isfinite(quote.price)


@pytest.mark.parametrize(
    "entrypoint",
    ["paper.book_quote", "books.spread_view", "backtest.measure_haircuts"],
)
def test_every_pricing_entrypoint_refuses_the_pre_r1_schema(entrypoint):
    """A pre-R1 snapshot cannot say where its quotes rested, so it prices nothing."""
    legacy = _legacy_frame()
    if entrypoint == "paper.book_quote":
        from evescreener.paper import book_quote

        assert book_quote(legacy, type_id=34, side="sell", tier_index=0).price is None
    elif entrypoint == "books.spread_view":
        assert spread_view(legacy).empty
    else:
        from evescreener.backtest import measure_haircuts

        assert measure_haircuts(legacy, TIERS) == {}


def test_the_census_still_measures_the_region_it_always_measured():
    """§22 S2a redefined `top_order_volume_share`; §17's figure was region-wide.

    The census is a diagnostic of the whole region, so pointing it at the
    executable column would silently change what an already-recorded statistic
    means — the same class of error as replacing a historical figure.
    """
    import inspect

    from evescreener import census

    source = inspect.getsource(census.book_statistics)
    assert "region_top_order_volume_share" in source
