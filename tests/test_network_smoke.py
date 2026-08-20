"""Live smoke path — `@pytest.mark.network`, run intentionally.

`uv run pytest -m network -q`

Everything else in this suite is offline against recorded fixtures (plan.md
§11 D5). This module is the one place that touches real endpoints, and it
exists to answer a single question: does the whole pipeline work against the
actual world?

It pulls real history, sweeps the real Forge book, produces a real digest,
prices a real paper open, and fetches one real day of EVE Ref killmails. It
writes only into a temp data dir, so it never disturbs the operator's lake —
and because the ETag store starts empty there, it never risks a
fetch-before-expiry against cached state it did not create.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from evescreener.bars import ingest_history
from evescreener.books import sweep_region
from evescreener.config import config_from_mapping, load_example
from evescreener.digest import build_digest, post_digest
from evescreener.esi.client import EsiClient
from evescreener.killmails import backfill_archives
from evescreener.paper import PaperLedger, Refusal
from evescreener.screen import run_screen
from evescreener.signals.composite import build_composite
from evescreener.store.db import Database
from evescreener.store.lake import BarLake, BookLake

pytestmark = pytest.mark.network

REPO_ROOT = Path(__file__).resolve().parents[1]
TRITANIUM = 34
FORGE = 10000002


@pytest.fixture
def live_config(tmp_path):
    raw = load_example(REPO_ROOT)
    raw["app"]["data_dir"] = str(tmp_path / "data")
    return config_from_mapping(raw)


@pytest.fixture
def live_db(live_config):
    database = Database(live_config.paths.ensure().db)
    yield database
    database.close()


def test_real_history_matches_the_bar_contract(live_config, live_db):
    lake = BarLake(live_config.paths)

    async def run():
        client = EsiClient(live_config, live_db)
        try:
            return await ingest_history(client, lake, [TRITANIUM, 44992], region_id=FORGE)
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert result.fetched >= 1, result.errors
    frame = lake.read(FORGE, type_ids=[TRITANIUM])
    assert not frame.empty
    assert "open" not in frame.columns
    assert frame["close"].gt(0).all()
    assert frame["isk_value"].gt(0).all()
    assert str(frame["datetime"].dt.tz) == "UTC"
    # ~13.5 months of daily bars is the documented horizon.
    assert 300 < len(frame) < 500


def test_a_second_history_call_is_skipped_as_still_fresh(live_config, live_db):
    lake = BarLake(live_config.paths)

    async def run():
        client = EsiClient(live_config, live_db)
        try:
            first = await ingest_history(client, lake, [TRITANIUM], region_id=FORGE)
            second = await ingest_history(client, lake, [TRITANIUM], region_id=FORGE)
            return first, second, client
        finally:
            await client.aclose()

    first, second, client = asyncio.run(run())
    assert first.fetched == 1
    assert second.skipped_fresh == 1, "history expires daily; a second call must not fetch"
    assert second.fetched == 0


def test_real_forge_sweep_reduces_and_stays_inside_the_budget(live_config, live_db):
    book_lake = BookLake(live_config.paths.ensure())

    async def run():
        client = EsiClient(live_config, live_db)
        try:
            return await sweep_region(live_config, client, book_lake, FORGE), client
        finally:
            await client.aclose()

    result, client = asyncio.run(run())
    assert result.pages_expected > 300, "The Forge book is hundreds of pages"
    assert result.orders_seen > 100_000
    assert result.types > 5_000
    assert not result.frame.empty
    # The self-cap is the whole point: the sweep must fit comfortably inside it.
    assert client.tokens.used() < live_config.budget.orders_token_self_cap
    tritanium = result.frame[result.frame["type_id"] == TRITANIUM]
    assert not tritanium.empty
    assert set(tritanium["side"]) == {"buy", "sell"}
    assert tritanium["depth_fill_price_0"].notna().any(), "0.25B must fill in Tritanium"
    assert result.structure_volume_share is not None, "the blind spot must be quantified"


def test_the_telemetry_ledger_recorded_every_request(live_config, live_db):
    from datetime import timedelta

    from evescreener.timeutil import utcnow

    async def run():
        client = EsiClient(live_config, live_db)
        try:
            await ingest_history(client, BarLake(live_config.paths), [TRITANIUM], region_id=FORGE)
        finally:
            await client.aclose()

    asyncio.run(run())
    rows = live_db.ledger_since(utcnow() - timedelta(minutes=10))
    assert rows
    assert all(row["url"].startswith("https://esi.evetech.net") for row in rows)
    assert any(row["expires_at"] for row in rows), "every response's Expires is recorded"


def test_real_digest_and_paper_open_against_a_live_book(live_config, live_db):
    bar_lake = BarLake(live_config.paths.ensure())
    book_lake = BookLake(live_config.paths)
    ids = [TRITANIUM, 35, 36, 37, 38, 39, 40, 11399, 16273, 44992]

    async def run():
        client = EsiClient(live_config, live_db)
        try:
            await ingest_history(client, bar_lake, ids, region_id=FORGE)
            await sweep_region(live_config, client, book_lake, FORGE)
        finally:
            await client.aclose()

    asyncio.run(run())
    bars = bar_lake.read(FORGE)
    assert not bars.empty
    composite = build_composite(bars, members=10, min_members=5)
    book = book_lake.latest(FORGE)
    assert not book.empty

    screen = run_screen(live_config, live_db, bars, composite, book, region_id=FORGE)
    content = build_digest(live_config, screen)
    assert content
    # No webhook is configured in the example config: `unconfigured` is the
    # correct outcome, and the digest is still archived.
    delivery = post_digest(live_config, content, archive_path=live_config.paths.digests)
    assert delivery.kind == "unconfigured"
    assert live_config.paths.digests.exists()

    ledger = PaperLedger(live_config.paths.paper_ledger, live_config)
    try:
        record = ledger.open_position(
            type_id=TRITANIUM,
            type_name="Tritanium",
            notional_isk=live_config.paper.default_notional_isk,
            book=book,
            thesis="network smoke: price a real taker entry against a real book",
        )
    except Refusal as refusal:
        pytest.fail(f"a fresh Forge sweep should price Tritanium at 0.25B: {refusal}")
    assert record["entry_effective_price"] > 0
    assert record["entry_units"] > 0
    assert record["book_age_minutes"] < live_config.paper.stale_book_minutes
    # The ask walk is at or above the best ask, never below it.
    ask = book[(book["type_id"] == TRITANIUM) & (book["side"] == "sell")].iloc[-1]
    assert record["entry_effective_price"] >= float(ask["best_price"]) - 1e-9

    report = ledger.report()
    assert report.verdict["verdict"] == "TOO_EARLY"


def test_one_real_day_of_everef_killmails(live_config, live_db, tmp_path):
    from datetime import timedelta

    from evescreener.sde import load_sde
    from evescreener.timeutil import utcnow

    bundle = REPO_ROOT / "data" / "sde" / "sde-3473160-jsonl.zip"
    if not bundle.exists():
        pytest.skip("no local SDE bundle; run `python -m evescreener sde` first")
    load_sde(live_config, live_db, bundle_path=bundle)
    assert live_db.system_region_map(), "killmails need the solar-system map"

    result = backfill_archives(
        live_config,
        live_db,
        days=1,
        end=utcnow().date() - timedelta(days=2),
        cache_dir=tmp_path / "archives",
    )
    assert result.days == 1, result.errors
    assert result.killmails > 5_000, "a real day is 15k-24k killmails"
    assert result.hull_rows > 0
    assert result.unmapped_systems == 0, "every solar system must map to a region"
    rows = live_db.conn.execute("SELECT COUNT(*) AS n FROM destruction").fetchone()["n"]
    assert rows > 1_000


def test_pushx_quotes_a_real_route(live_config, live_db):
    from evescreener.crossregion import quote_freight

    quote = quote_freight(
        live_config,
        live_db,
        start_system="Jita",
        end_system="Amarr",
        volume_m3=10_000,
        collateral=1_000_000_000,
    )
    if not quote.known:
        pytest.skip(f"PushX is a third-party enrichment and is unavailable: {quote.unknown_reason}")
    assert quote.price > 0
    assert quote.effective_price == quote.price, "a live quote takes no staleness haircut"
