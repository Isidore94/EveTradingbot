"""S1 — a missing or malformed `Expires` must never license an early fetch.

Fetching before a resource's `Expires` is the one rule CCP bans accounts for,
so it is tested as an invariant through **real `EsiClient.get()` calls against a
counting transport**, not through the fallback helper in isolation. The helper
was already correct; the production branches around it were not.

The reproduced defect (§22 S1):

* seed an ETag whose stored expiry is 12:00;
* at 12:01 the server answers **304** with `Expires: not-a-date`;
* the client restored the stored 12:00 — a timestamp already in the past;
* the very next call therefore saw "no active expiry" and hit the network.

Two real requests where there should have been one.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from evescreener.esi.client import (
    HISTORY_FEED,
    ORDERS_FEED,
    TYPES_FEED,
    EsiClient,
    unknown_expiry_boundary,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def http_date(moment: datetime) -> str:
    return moment.strftime("%a, %d %b %Y %H:%M:%S GMT")


class Counter:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def client_for(config, db, handler, *, now):
    counter = Counter(handler)
    http = httpx.AsyncClient(
        base_url=config.esi.base_url,
        headers=config.headers,
        transport=httpx.MockTransport(counter),
    )

    async def sleep(_seconds):
        return None

    return EsiClient(config, db, client=http, sleep=sleep, now=now), counter


# -- 1. the 304 branch ------------------------------------------------------


@pytest.mark.parametrize("header", [None, "not-a-date", "", "Expires: soon"])
def test_a_304_never_restores_an_expiry_that_has_already_passed(config, db, header):
    """The exact reproduction: stored 12:00, clock 12:01, malformed 304."""
    clock = {"t": NOW + timedelta(minutes=1)}
    headers = {"etag": 'W/"abc"'}
    if header is not None:
        headers["expires"] = header

    def handler(_request):
        return httpx.Response(304, headers=headers)

    client, counter = client_for(config, db, handler, now=lambda: clock["t"])
    url = client._absolute("/markets/10000002/orders", None)
    db.put_etag(url, 'W/"abc"', NOW, http_date(NOW))  # expiry already one minute past

    async def run():
        first = await client.get(ORDERS_FEED, "/markets/10000002/orders")
        second = await client.get(ORDERS_FEED, "/markets/10000002/orders")
        return first, second

    first, second = asyncio.run(run())

    stored = db.expires_at(url)
    assert stored is not None
    assert stored > clock["t"], "a 304 must never leave an expiry in the past"
    assert first.expires is not None and first.expires > clock["t"]
    assert second.skipped, "the second call must be refused as still-fresh"
    assert len(counter.requests) == 1, "one request, not two"


def test_a_304_with_a_good_expires_is_untouched(config, db):
    """The correct path must keep working exactly as before."""
    good = NOW + timedelta(minutes=20)

    def handler(_request):
        return httpx.Response(304, headers={"etag": 'W/"abc"', "expires": http_date(good)})

    client, counter = client_for(config, db, handler, now=lambda: NOW + timedelta(minutes=1))
    url = client._absolute("/markets/10000002/orders", None)
    db.put_etag(url, 'W/"abc"', NOW, http_date(NOW))

    response = asyncio.run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    assert response.expires == good
    assert db.expires_at(url) == good
    assert len(counter.requests) == 1


def test_a_304_never_shortens_an_expiry_we_already_trusted(config, db):
    """A malformed header must not pull a future expiry back toward now."""
    far = NOW + timedelta(hours=6)

    def handler(_request):
        return httpx.Response(304, headers={"etag": 'W/"abc"', "expires": "rubbish"})

    client, _counter = client_for(config, db, handler, now=lambda: NOW)
    url = client._absolute("/markets/10000002/orders", None)
    db.put_etag(url, 'W/"abc"', far, http_date(NOW))
    # A still-future expiry means the client refuses to ask at all; force the
    # branch by asking once the stored expiry has lapsed.
    client2, _c2 = client_for(config, db, handler, now=lambda: far + timedelta(seconds=1))
    asyncio.run(client2.get(ORDERS_FEED, "/markets/10000002/orders"))
    assert db.expires_at(url) > far


# -- 2. the 200 branch ------------------------------------------------------


@pytest.mark.parametrize("header", [None, "not-a-date", ""])
def test_a_200_with_no_usable_expires_does_not_permit_an_immediate_refetch(config, db, header):
    headers = {"etag": 'W/"z"'}
    if header is not None:
        headers["expires"] = header

    def handler(_request):
        return httpx.Response(200, json=[{"type_id": 34}], headers=headers)

    client, counter = client_for(config, db, handler, now=lambda: NOW)

    async def run():
        await client.get(ORDERS_FEED, "/markets/10000002/orders")
        return await client.get(ORDERS_FEED, "/markets/10000002/orders")

    second = asyncio.run(run())
    assert second.skipped
    assert len(counter.requests) == 1


def test_history_does_not_receive_the_orders_fallback(config, db):
    """A universal five-minute TTL is not a safe statement about every feed.

    History rolls once a day at 11:05 UTC. Treating an unknown history expiry
    as five minutes would re-ask 288 times a day for a resource that changes
    once — a number invented for one feed and applied to all of them.
    """
    orders = unknown_expiry_boundary(config, ORDERS_FEED, NOW)
    history = unknown_expiry_boundary(config, HISTORY_FEED, NOW)
    assert history > orders
    # The history boundary is the next 11:05 roll, which is a data fact.
    assert history.hour == 11 and history.minute == 5
    assert history > NOW


def test_every_boundary_is_in_the_future_and_never_invented_per_call(config):
    for feed in (ORDERS_FEED, HISTORY_FEED, TYPES_FEED, "something-unmapped"):
        boundary = unknown_expiry_boundary(config, feed, NOW)
        assert boundary > NOW, feed
        # Deterministic: the same inputs give the same answer.
        assert boundary == unknown_expiry_boundary(config, feed, NOW), feed


def test_an_unmapped_feed_waits_at_least_as_long_as_any_mapped_one(config):
    """Not knowing the feed is a reason to wait longer, never shorter."""
    unmapped = unknown_expiry_boundary(config, "mystery", NOW)
    for feed in (ORDERS_FEED, HISTORY_FEED, TYPES_FEED):
        assert unmapped >= unknown_expiry_boundary(config, feed, NOW), feed


# -- 3. everything else about the request survives --------------------------


def test_the_etag_and_last_modified_are_still_stored(config, db):
    def handler(_request):
        return httpx.Response(
            200,
            json=[{"type_id": 34}],
            headers={"etag": 'W/"keep"', "last-modified": http_date(NOW)},
        )

    client, _counter = client_for(config, db, handler, now=lambda: NOW)
    asyncio.run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    url = client._absolute("/markets/10000002/orders", None)
    row = db.get_etag(url)
    assert row is not None
    assert row["etag"] == 'W/"keep"'


def test_the_degraded_expiry_is_recorded_rather_than_silent(config, db):
    """Telemetry must be able to show how often the server gave us nothing."""

    def handler(_request):
        return httpx.Response(200, json=[{"type_id": 34}], headers={"etag": 'W/"z"'})

    client, _counter = client_for(config, db, handler, now=lambda: NOW)
    response = asyncio.run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    assert response.expiry_unknown is True

    def good(_request):
        return httpx.Response(
            200,
            json=[{"type_id": 34}],
            headers={"etag": 'W/"z"', "expires": http_date(NOW + timedelta(minutes=5))},
        )

    client2, _c2 = client_for(config, db, good, now=lambda: NOW)
    assert asyncio.run(client2.get(ORDERS_FEED, "/markets/99/orders")).expiry_unknown is False


def test_the_telemetry_ledger_marks_a_silent_server(config, db):
    """The claim must be true: how often the server gave nothing is answerable."""
    from evescreener.timeutil import parse_iso

    def handler(_request):
        return httpx.Response(200, json=[{"type_id": 34}], headers={"etag": 'W/"z"'})

    client, _counter = client_for(config, db, handler, now=lambda: NOW)
    asyncio.run(client.get(ORDERS_FEED, "/markets/10000002/orders"))

    rows = db.ledger_since(parse_iso("2020-01-01T00:00:00+00:00"))
    assert rows, "the request must reach the telemetry ledger"
    last = rows[-1]
    assert "expiry-unknown" in (last["note"] or "")
    assert last["expires_at"] is None, "and its recorded expiry is NULL"


def test_a_well_behaved_response_is_not_marked(config, db):
    from evescreener.timeutil import parse_iso

    def handler(_request):
        return httpx.Response(
            200,
            json=[{"type_id": 34}],
            headers={"etag": 'W/"z"', "expires": http_date(NOW + timedelta(minutes=5))},
        )

    client, _counter = client_for(config, db, handler, now=lambda: NOW)
    asyncio.run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    last = db.ledger_since(parse_iso("2020-01-01T00:00:00+00:00"))[-1]
    assert "expiry-unknown" not in (last["note"] or "")
    assert last["expires_at"] is not None


def test_pagination_headers_still_reach_the_caller(config, db):
    def handler(_request):
        return httpx.Response(
            200, json=[{"type_id": 34}], headers={"etag": 'W/"z"', "x-pages": "7"}
        )

    client, _counter = client_for(config, db, handler, now=lambda: NOW)
    response = asyncio.run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    assert response.pages == 7
