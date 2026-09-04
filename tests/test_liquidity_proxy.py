"""The destination share: a per-hub proxy from the book, still labelled ASSUMED (§23.21).

`destination_share_prior` was a flat 0.25 for every hub. The lake measures,
on every sweep, the bid depth reachable at the destination station and the
region's whole resting bid volume. Their ratio is a book-share proxy for the
flow share — where demand sits, not where trades happen — so it replaces the
flat prior only when both sides are present, is named as a proxy on the row,
and is still replaced by the operator's recorded fills.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from evescreener.books import DepthSnapshot
from evescreener.liquidity import destination_share_for, liquidity_attachment, scenarios
from evescreener.store.lake import BOOK_SUMMARY_COLUMNS, BookLake
from test_liquidity import _bars
from test_loops import AMARR, JITA, _plan, _profile
from test_persistence import _depth_rows

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
DOMAIN = 10000043
SWEEP = "2026-08-25T11:50:00+00:00"


def _book(total_volume, *, type_id=1, side="buy"):
    row = {column: None for column in BOOK_SUMMARY_COLUMNS}
    row.update(
        {
            "type_id": type_id,
            "region_id": DOMAIN,
            "side": side,
            "sweep_ts": SWEEP,
            "total_volume": total_volume,
            "partial_sweep": False,
            "exec_location_id": AMARR.station_id,
        }
    )
    return pd.DataFrame([row], columns=BOOK_SUMMARY_COLUMNS)


def _depths(reachable=620.0):
    frame = _depth_rows(
        region=DOMAIN,
        station=AMARR.station_id,
        side="buy",
        levels=[(120.0, reachable)],
        sweep=SWEEP,
        type_id=1,
    )
    return {
        DOMAIN: DepthSnapshot(
            region_id=DOMAIN, frame=frame, sweep_ts=SWEEP, age_minutes=10.0, stale=False, reason=""
        )
    }


def test_the_book_share_replaces_the_flat_prior_and_is_named_as_a_proxy(config, paths):
    BookLake(paths).write(_book(1000.0))
    share, source = destination_share_for(config, DOMAIN, 1, reachable_qty=620.0, now=NOW)
    assert share == pytest.approx(0.62)
    assert source == "book_share_proxy"


def test_the_proxy_never_exceeds_one(config, paths):
    BookLake(paths).write(_book(100.0))
    share, _ = destination_share_for(config, DOMAIN, 1, reachable_qty=620.0, now=NOW)
    assert share == 1.0


def test_a_missing_or_zero_side_falls_back_to_the_prior_and_says_so(config, paths):
    BookLake(paths).write(_book(0.0))
    prior = (config.hauling.destination_share_prior, "prior")
    assert destination_share_for(config, DOMAIN, 1, reachable_qty=620.0, now=NOW) == prior
    assert destination_share_for(config, DOMAIN, 99, reachable_qty=620.0, now=NOW) == prior
    assert destination_share_for(config, DOMAIN, 1, reachable_qty=0.0, now=NOW) == prior
    assert destination_share_for(config, DOMAIN, 1, reachable_qty=None, now=NOW) == prior


def test_a_stale_book_cannot_lend_its_share(config, paths):
    BookLake(paths).write(_book(1000.0))
    late = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    assert destination_share_for(config, DOMAIN, 1, reachable_qty=620.0, now=late)[1] == "prior"


def test_the_scenario_carries_the_source_of_its_share():
    from evescreener.liquidity import measure_liquidity

    profile = measure_liquidity(
        _bars([500.0] * 20, type_id=1), type_id=1, region_id=DOMAIN, now=NOW
    )
    payload = scenarios(
        profile,
        1000.0,
        destination_share=0.62,
        capture_shares=(0.05, 0.15, 0.35),
        destination_share_source="book_share_proxy",
    )
    assert payload["assumptions"]["destination_share_source"] == "book_share_proxy"
    assert payload["assumptions"]["destination_share_prior"] == 0.62
    assert "ASSUMED" in payload["assumptions"]["note"]


def test_the_attachment_uses_the_proxy_per_row(config, paths):
    BookLake(paths).write(_book(1000.0))
    plan = _plan(1, "A", JITA, AMARR, cost=1000.0, net=100.0, haul_jumps=2)
    attach = liquidity_attachment(
        config,
        None,
        _depths(),
        _profile(),
        bars_by_region={DOMAIN: _bars([500.0] * 20, type_id=1)},
        now=NOW,
    )
    attached = attach(plan)
    assumptions = attached.liquidity["assumptions"]
    assert assumptions["destination_share_source"] == "book_share_proxy"
    assert assumptions["destination_share_prior"] == pytest.approx(0.62)


def test_the_attachment_falls_back_to_the_prior_without_a_book(config, paths):
    plan = _plan(1, "A", JITA, AMARR, cost=1000.0, net=100.0, haul_jumps=2)
    attach = liquidity_attachment(
        config,
        None,
        _depths(),
        _profile(),
        bars_by_region={DOMAIN: _bars([500.0] * 20, type_id=1)},
        now=NOW,
    )
    assumptions = attach(plan).liquidity["assumptions"]
    assert assumptions["destination_share_source"] == "prior"
    assert assumptions["destination_share_prior"] == config.hauling.destination_share_prior
