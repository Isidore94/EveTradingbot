"""The one live path, run intentionally: ``uv run pytest -m network``.

It is a smoke test, not a gate: it asserts the facts plan.md §0 records about
the live endpoints, so the day CCP changes one of them this fails loudly
instead of the screener quietly computing on a changed contract.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from evescreener.bars import bars_from_history, history_path
from evescreener.clock import now_utc
from evescreener.config import load_config
from evescreener.esi import ORDERS_GROUP, EsiClient
from evescreener.state import StateStore

pytestmark = pytest.mark.network

TRITANIUM = 34
THE_FORGE = 10000002


@pytest.fixture
def live(tmp_path):
    """Real config, but a throwaway data dir: no live run touches the lake."""
    config = load_config()
    from evescreener.paths import DataPaths

    object.__setattr__(config, "paths", DataPaths(tmp_path / "data"))
    config.paths.ensure()
    return config


def test_history_endpoint_still_matches_the_recorded_contract(live):
    async def scenario():
        with StateStore(live.paths.state_db) as store:
            async with EsiClient(live, store) as client:
                return await client.get(
                    history_path(THE_FORGE),
                    params={"type_id": TRITANIUM},
                    paced=True,
                )

    response = asyncio.run(scenario())
    assert response.status == 200
    assert response.expires_at is not None
    # History expires daily at 11:05 UTC (§0).
    assert (response.expires_at.hour, response.expires_at.minute) == (11, 5)

    row = response.data[-1]
    assert set(row) == {
        "date",
        "average",
        "highest",
        "lowest",
        "volume",
        "order_count",
    }, "the history field set changed; §4's mapping must be revisited"

    frame, dropped = bars_from_history(
        response.data,
        type_id=TRITANIUM,
        region_id=THE_FORGE,
        fetched_at=response.fetched_at,
        last_modified=response.last_modified,
    )
    assert not frame.empty
    assert "open" not in frame.columns
    assert (frame["high"] >= frame["low"]).all()
    assert frame["datetime"].max() < now_utc()


def test_orders_endpoint_still_reports_pages_and_token_headers(live):
    async def scenario():
        with StateStore(live.paths.state_db) as store:
            async with EsiClient(live, store) as client:
                response = await client.get(
                    f"/markets/{THE_FORGE}/orders",
                    params={"page": 1},
                    group=ORDERS_GROUP,
                )
                return response, client.budget

    response, budget = asyncio.run(scenario())
    assert response.status == 200
    assert response.pages and response.pages > 100, "The Forge is deeply paginated"
    assert response.tokens_charged == 2
    assert budget.observed_remaining is not None, (
        "X-Ratelimit-Remaining disappeared; the token regime changed (§0, R4)"
    )
    order = response.data[0]
    assert {"order_id", "type_id", "price", "volume_remain", "is_buy_order"} <= set(
        order
    )


def test_the_client_refuses_to_refetch_inside_the_cache_window(live):
    """The invariant that matters most, proven against the live endpoint."""

    async def scenario():
        with StateStore(live.paths.state_db) as store:
            async with EsiClient(live, store) as client:
                first = await client.get("/markets/prices")
                second = await client.get("/markets/prices")
                return (
                    first,
                    second,
                    store.ledger_summary(now_utc() - dt.timedelta(minutes=5)),
                )

    first, second, summary = asyncio.run(scenario())
    assert first.outcome == "fetched"
    assert second.outcome == "skipped_fresh"
    assert summary["early_fetches"] == 0
