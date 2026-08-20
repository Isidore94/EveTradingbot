"""Cross-region: no freight quote, no row. Ever."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest

from evescreener.crossregion import (
    FreightQuote,
    hub_system_name,
    quote_freight,
    render_cross_region,
    scan_cross_region,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

PUSHX_OK = {
    "PriceNormal": 52_500_000,
    "PriceRush": 102_500_000,
    "Volume": 10000,
    "Collateral": 1_000_000_000,
    "PriceError": None,
    "GeneralError": None,
    "StartSystemName": "Jita",
    "EndSystemName": "Amarr",
}


def book(region, *, type_id=34, ask_fill=100.0, bid_fill=100.0):
    rows = []
    for side, fill in (("sell", ask_fill), ("buy", bid_fill)):
        row = {
            "type_id": type_id,
            "region_id": region,
            "side": side,
            "sweep_ts": NOW.isoformat(),
            "expires_ts": None,
            "best_price": fill,
            "total_volume": 1e9,
            "order_count": 20,
            "p5_price": fill,
            "top_order_volume_share": 0.05,
            "station_volume_share": 1.0,
            "partial_sweep": False,
        }
        for index in range(3):
            row[f"depth_fill_price_{index}"] = fill if index == 0 else None
            row[f"depth_fill_qty_{index}"] = 1000.0 if index == 0 else None
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def seeded_db(db):
    db.replace_types([(34, "Tritanium", 1857, 0.01, 0.01, 1)])
    return db


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# -- quoting ----------------------------------------------------------------


def test_a_live_quote_is_cached_and_carries_no_haircut(config, seeded_db):
    client = mock_client(lambda request: httpx.Response(200, json=PUSHX_OK))
    quote = quote_freight(
        config,
        seeded_db,
        start_system="Jita",
        end_system="Amarr",
        volume_m3=10000,
        collateral=1e9,
        client=client,
        now=NOW,
    )
    assert quote.known
    assert quote.price == 52_500_000
    assert quote.haircut_pct == 0.0
    assert quote.effective_price == 52_500_000
    stored = seeded_db.conn.execute("SELECT COUNT(*) AS n FROM freight_quotes").fetchone()["n"]
    assert stored == 1


def test_a_cached_quote_takes_the_staleness_haircut(config, seeded_db):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=PUSHX_OK)

    client = mock_client(handler)
    args = dict(start_system="Jita", end_system="Amarr", volume_m3=10000, collateral=1e9)
    quote_freight(config, seeded_db, **args, client=client, now=NOW)
    cached = quote_freight(config, seeded_db, **args, client=client, now=NOW + timedelta(hours=2))
    assert calls["n"] == 1, "the second call must come from cache"
    assert cached.cached
    assert cached.haircut_pct == config.freight.staleness_haircut_pct
    assert cached.effective_price > cached.price, "an old quote never reads as a fresh one"


def test_an_expired_cache_is_requoted(config, seeded_db):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=PUSHX_OK)

    client = mock_client(handler)
    args = dict(start_system="Jita", end_system="Amarr", volume_m3=10000, collateral=1e9)
    quote_freight(config, seeded_db, **args, client=client, now=NOW)
    quote_freight(config, seeded_db, **args, client=client, now=NOW + timedelta(days=2))
    assert calls["n"] == 2


def test_a_quote_error_is_unknown_never_zero(config, seeded_db):
    client = mock_client(
        lambda request: httpx.Response(200, json={"PriceError": "no route", "PriceNormal": 0})
    )
    quote = quote_freight(
        config,
        seeded_db,
        start_system="Jita",
        end_system="Nowhere",
        volume_m3=10,
        collateral=1e9,
        client=client,
        now=NOW,
    )
    assert not quote.known
    assert quote.effective_price is None
    assert "no route" in quote.unknown_reason


def test_a_transport_failure_is_unknown(config, seeded_db):
    def handler(request):
        raise httpx.ConnectError("down")

    quote = quote_freight(
        config,
        seeded_db,
        start_system="Jita",
        end_system="Amarr",
        volume_m3=10,
        collateral=1e9,
        client=mock_client(handler),
        now=NOW,
    )
    assert not quote.known
    assert "ConnectError" in quote.unknown_reason


def test_hub_systems_resolve_from_config(config):
    assert hub_system_name(config, 10000002) == "Jita"
    assert hub_system_name(config, 99999) is None


# -- the scan ---------------------------------------------------------------


def quoted(price):
    def stub(config, db, *, start_system, end_system, volume_m3, collateral, client=None, now=None):
        return FreightQuote(
            route=f"{start_system}->{end_system}",
            volume_m3=volume_m3,
            collateral=collateral,
            price=price,
            quoted_at=NOW.isoformat(),
            cached=False,
            haircut_pct=0.0,
        )

    return stub


def refused():
    def stub(config, db, *, start_system, end_system, volume_m3, collateral, client=None, now=None):
        return FreightQuote(
            route=f"{start_system}->{end_system}",
            volume_m3=volume_m3,
            collateral=collateral,
            price=None,
            quoted_at=NOW.isoformat(),
            cached=False,
            haircut_pct=0.0,
            unknown_reason="quote service unreachable",
        )

    return stub


def test_a_profitable_route_survives_real_freight(config, seeded_db):
    books = {
        10000002: book(10000002, ask_fill=100.0, bid_fill=99.0),
        10000043: book(10000043, ask_fill=140.0, bid_fill=138.0),
    }
    scan = scan_cross_region(config, seeded_db, books, now=NOW, quote_fn=quoted(1_000_000))
    assert scan.rows
    row = scan.rows[0]
    assert row["buy_region"] == 10000002
    assert row["sell_region"] == 10000043
    assert row["freight_isk"] == 1_000_000
    assert row["sales_tax_isk"] > 0
    assert row["net_pct"] > 0


def test_no_freight_quote_means_no_row_ever(config, seeded_db):
    books = {
        10000002: book(10000002, ask_fill=100.0, bid_fill=99.0),
        10000043: book(10000043, ask_fill=140.0, bid_fill=138.0),
    }
    scan = scan_cross_region(config, seeded_db, books, now=NOW, quote_fn=refused())
    assert scan.rows == []
    assert scan.dropped_no_freight > 0
    assert "Nothing clears costs today" in render_cross_region(scan)


def test_freight_can_eat_the_whole_edge(config, seeded_db):
    books = {
        10000002: book(10000002, ask_fill=100.0, bid_fill=99.0),
        10000043: book(10000043, ask_fill=110.0, bid_fill=106.0),
    }
    scan = scan_cross_region(config, seeded_db, books, now=NOW, quote_fn=quoted(500_000_000))
    assert scan.rows == []
    assert scan.dropped_negative > 0


def test_tax_is_inside_the_net(config, seeded_db):
    books = {
        10000002: book(10000002, ask_fill=100.0, bid_fill=99.0),
        10000043: book(10000043, ask_fill=140.0, bid_fill=138.0),
    }
    scan = scan_cross_region(config, seeded_db, books, now=NOW, quote_fn=quoted(0))
    row = scan.rows[0]
    gross = (138.0 / 100.0) * row["notional_isk"]
    assert row["sales_tax_isk"] == pytest.approx(gross * 0.03375)
    assert row["net_isk"] == pytest.approx(gross * (1 - 0.03375) - row["notional_isk"])


def test_a_tier_with_no_depth_is_dropped(config, seeded_db):
    thin = book(10000043, ask_fill=140.0, bid_fill=138.0)
    thin.loc[:, "depth_fill_price_0"] = float("nan")
    scan = scan_cross_region(
        config,
        seeded_db,
        {10000002: book(10000002), 10000043: thin},
        now=NOW,
        quote_fn=quoted(0),
    )
    assert scan.rows == []
    assert scan.dropped_no_depth > 0


def test_one_region_is_nothing_to_compare(config, seeded_db):
    scan = scan_cross_region(config, seeded_db, {10000002: book(10000002)}, now=NOW)
    assert scan.rows == []
    assert "fewer than two regions" in " ".join(scan.notes)


def test_freight_disabled_produces_no_rows(config, seeded_db, repo_root):
    from evescreener.config import config_from_mapping, load_example

    raw = load_example(repo_root)
    raw["freight"]["enabled"] = False
    disabled = config_from_mapping(raw)
    scan = scan_cross_region(
        disabled, seeded_db, {10000002: book(10000002), 10000043: book(10000043)}, now=NOW
    )
    assert scan.rows == []
    assert "freight disabled" in " ".join(scan.notes)


def test_an_unconfigured_hub_is_skipped_not_guessed(config, seeded_db):
    books = {10000002: book(10000002), 99999999: book(99999999)}
    scan = scan_cross_region(config, seeded_db, books, now=NOW, quote_fn=quoted(0))
    assert any("no hub system configured" in note for note in scan.notes)


def test_a_structure_resident_sell_side_is_flagged(config, seeded_db):
    """Measured across all five hubs: every ask is in an NPC station, but bids
    are 9%-98% structure-resident. The exposure is always on the sell leg."""
    books = {
        10000002: book(10000002, ask_fill=100.0, bid_fill=99.0),
        10000043: book(10000043, ask_fill=140.0, bid_fill=138.0),
    }
    amarr = books[10000043]
    amarr.loc[amarr["side"] == "buy", "station_volume_share"] = 0.017  # Amarr, measured
    scan = scan_cross_region(config, seeded_db, books, now=NOW, quote_fn=quoted(1_000_000))
    assert scan.rows
    row = scan.rows[0]
    assert row["sell_side_station_share"] == pytest.approx(0.017)
    assert any("needs docking rights" in flag for flag in row["flags"])
    assert "98% of the sell-side book" in " ".join(row["flags"])


def test_an_npc_station_exit_carries_no_structure_flag(config, seeded_db):
    books = {
        10000002: book(10000002, ask_fill=100.0, bid_fill=99.0),
        10000043: book(10000043, ask_fill=140.0, bid_fill=138.0),
    }
    scan = scan_cross_region(config, seeded_db, books, now=NOW, quote_fn=quoted(1_000_000))
    assert scan.rows
    assert not any("docking rights" in flag for flag in scan.rows[0]["flags"])


def test_a_cached_freight_quote_is_flagged_in_the_row(config, seeded_db):
    def cached_quote(
        config, db, *, start_system, end_system, volume_m3, collateral, client=None, now=None
    ):
        return FreightQuote(
            route=f"{start_system}->{end_system}",
            volume_m3=volume_m3,
            collateral=collateral,
            price=1_000_000,
            quoted_at=NOW.isoformat(),
            cached=True,
            haircut_pct=10.0,
        )

    books = {
        10000002: book(10000002, ask_fill=100.0, bid_fill=99.0),
        10000043: book(10000043, ask_fill=140.0, bid_fill=138.0),
    }
    scan = scan_cross_region(config, seeded_db, books, now=NOW, quote_fn=cached_quote)
    assert any("cached" in flag for flag in scan.rows[0]["flags"])
    assert scan.rows[0]["freight_isk"] == pytest.approx(1_100_000), "haircut applied"


def test_the_scan_states_that_it_prices_a_non_simultaneous_haul_simultaneously(config, seeded_db):
    from evescreener.crossregion import LIMITATIONS

    books = {
        10000002: book(10000002, ask_fill=100.0, bid_fill=99.0),
        10000043: book(10000043, ask_fill=140.0, bid_fill=138.0),
    }
    text = render_cross_region(
        scan_cross_region(config, seeded_db, books, now=NOW, quote_fn=quoted(1_000_000))
    )
    assert "What this scan cannot tell you" in text
    assert "the haul is not simultaneous" in text
    assert len(LIMITATIONS) == 4


def test_the_zero_case_also_states_its_limitations(config, seeded_db):
    text = render_cross_region(
        scan_cross_region(config, seeded_db, {10000002: book(10000002)}, now=NOW)
    )
    assert "What this scan cannot tell you" in text
