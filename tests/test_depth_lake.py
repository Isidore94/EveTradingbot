"""The depth lake, and the promise that `book_summary` did not move.

Two products now come out of one sweep. That is only safe if the second one
cannot disturb the first, so the first assertion in this file is that the
`book_summary` frame produced through the modified sweep path is **byte
identical** to the one produced without it — same columns, same dtypes, same
values. Everything downstream of `reduce_orders` — the screen, the paper
ledger, the backtest's haircuts, SPREADS — reads that frame, and this whole
track is supposed to be additive.

The rest is `BookLake`'s own guarantees, held again for depth: atomic writes, a
partial sweep quarantined where `latest()` cannot see it, and complete-only
reads. A curve built from a sweep that was missing pages is not a cheap curve,
it is an unmeasured one.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest

from evescreener.books import DepthBound, load_validated_depth, reduce_orders, sweep_region
from evescreener.store.lake import DEPTH_COLUMNS, BookLake, DepthLake

JITA_44, JITA = 60003760, 30000142
TIERS = (250_000_000.0, 1_000_000_000.0, 2_500_000_000.0)
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _orders():
    return [
        {
            "order_id": 1,
            "type_id": 34,
            "price": 5.0,
            "volume_remain": 1_000_000.0,
            "is_buy_order": False,
            "location_id": JITA_44,
            "system_id": JITA,
            "range": None,
            "min_volume": 1,
            "issued": "2026-08-25T09:00:00Z",
        },
        {
            "order_id": 2,
            "type_id": 34,
            "price": 4.0,
            "volume_remain": 900_000.0,
            "is_buy_order": True,
            "location_id": JITA_44,
            "system_id": JITA,
            "range": "station",
            "min_volume": 1,
            "issued": "2026-08-25T08:00:00Z",
        },
    ]


def _client(config, db, rows):
    from evescreener.esi.client import EsiClient

    def handler(_request):
        return httpx.Response(
            200,
            json=rows,
            headers={"expires": "Thu, 27 Aug 2026 03:00:00 GMT", "x-pages": "1"},
        )

    return EsiClient(
        config,
        db,
        client=httpx.AsyncClient(
            base_url=config.esi.base_url, transport=httpx.MockTransport(handler)
        ),
    )


def _sweep(config, db, *, with_depth: bool):
    paths = config.paths.ensure()
    lake = BookLake(paths)
    depth_lake = DepthLake(paths) if with_depth else None
    client = _client(config, db, _orders())
    try:
        return asyncio.run(
            sweep_region(
                config,
                client,
                lake,
                10000002,
                depth_lake=depth_lake,
                stations={JITA_44: JITA} if with_depth else None,
                bound=DepthBound(max_capital_isk=1e10, max_cargo_m3=1e6),
            )
        )
    finally:
        asyncio.run(client.aclose())


# -- 1. the existing product does not move ---------------------------------


def test_the_book_summary_frame_is_byte_identical_with_and_without_depth(config, db):
    """The whole track is additive, and this is where that is proved."""
    plain = _sweep(config, db, with_depth=False).frame
    db.conn.execute("UPDATE etags SET expires_at='2020-01-01T00:00:00+00:00'")
    withdepth = _sweep(config, db, with_depth=True).frame

    assert list(plain.columns) == list(withdepth.columns)
    assert plain.dtypes.equals(withdepth.dtypes)
    # `sweep_ts` is the fetch time and legitimately differs between two sweeps.
    columns = [column for column in plain.columns if column != "sweep_ts"]
    pd.testing.assert_frame_equal(plain[columns], withdepth[columns])


def test_reduce_orders_still_produces_what_it_always_did(config):
    """A direct call, unchanged by anything H1b added."""
    result = reduce_orders(_orders(), region_id=10000002, notional_tiers=TIERS, sweep_ts="t")
    sell = result.frame[result.frame["side"] == "sell"].iloc[0]
    assert sell["exec_location_id"] == JITA_44
    assert sell["exec_price"] == 5.0
    assert result.frame["type_id"].tolist() == [34, 34]


def test_no_depth_is_written_when_none_was_asked_for(config, db):
    _sweep(config, db, with_depth=False)
    assert DepthLake(config.paths).latest(10000002).empty


# -- 2. one sweep, one generation ------------------------------------------


def test_the_depth_generation_is_the_book_generation(config, db):
    result = _sweep(config, db, with_depth=True)
    book = BookLake(config.paths).latest(10000002)
    depth = DepthLake(config.paths).latest(10000002)
    assert not depth.empty
    assert set(depth["sweep_ts"].unique()) == set(book["sweep_ts"].unique())
    assert set(depth["region_id"].unique()) == {10000002}
    assert result.depth is not None
    assert result.as_dict()["depth"]["rows"] == len(depth)


def test_the_written_depth_carries_the_declared_columns(config, db):
    _sweep(config, db, with_depth=True)
    assert list(DepthLake(config.paths).latest(10000002).columns) == DEPTH_COLUMNS


# -- 3. the lake's guarantees ----------------------------------------------


def _frame(sweep_ts: str, *, price: float = 100.0, region: int = 10000002) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "region_id": region,
                "sweep_ts": sweep_ts,
                "fetched_at": sweep_ts,
                "expires_ts": None,
                "execution_location_id": JITA_44,
                "type_id": 34,
                "side": "sell",
                "price": price,
                "level_qty": 10.0,
                "cumulative_qty": 10.0,
                "cumulative_notional": price * 10.0,
                "level_order_count": 1,
                "min_volume_excluded_qty": 0.0,
                "oldest_issued": None,
                "newest_issued": None,
                "structure_share": 0.0,
                "depth_complete": True,
            }
        ],
        columns=DEPTH_COLUMNS,
    )


def test_a_partial_sweep_never_displaces_the_last_complete_one(config):
    lake = DepthLake(config.paths.ensure())
    lake.write(_frame("2026-08-25T10:00:00+00:00", price=100.0))
    lake.write_partial(_frame("2026-08-25T11:00:00+00:00", price=999.0))
    latest = lake.latest(10000002)
    assert latest["price"].tolist() == [100.0], "the newer partial must not be read"


def test_latest_returns_the_newest_complete_generation(config):
    lake = DepthLake(config.paths.ensure())
    lake.write(_frame("2026-08-25T10:00:00+00:00", price=100.0))
    lake.write(_frame("2026-08-25T11:00:00+00:00", price=110.0))
    assert lake.latest(10000002)["price"].tolist() == [110.0]


def test_a_region_with_no_depth_reads_empty_rather_than_another_regions(config):
    lake = DepthLake(config.paths.ensure())
    lake.write(_frame("2026-08-25T10:00:00+00:00"))
    assert lake.latest(10000043).empty


# -- 4. one staleness contract, not one per call site ----------------------


def test_a_fresh_generation_is_priceable(config):
    DepthLake(config.paths.ensure()).write(_frame("2026-08-25T11:30:00+00:00"))
    snapshot = load_validated_depth(config, 10000002, now=NOW)
    assert snapshot.known and snapshot.age_minutes == pytest.approx(30.0)
    assert snapshot.generation == (10000002, "2026-08-25T11:30:00+00:00")
    assert not snapshot.priceable.empty


def test_a_stale_generation_prices_nothing_and_says_how_old_it_is(config):
    DepthLake(config.paths.ensure()).write(_frame("2026-08-25T06:00:00+00:00"))
    snapshot = load_validated_depth(config, 10000002, now=NOW)
    assert snapshot.known is False
    assert snapshot.priceable.empty, "there is no partly-priceable curve"
    assert "STALE" in snapshot.reason


def test_no_depth_at_all_names_the_command_that_fixes_it(config):
    config.paths.ensure()
    snapshot = load_validated_depth(config, 10000002, now=NOW)
    assert snapshot.known is False
    assert "sweep-books" in snapshot.reason
