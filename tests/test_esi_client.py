"""The client's non-negotiables: never fetch before expiry, ETags, budget.

Every test here is offline against `httpx.MockTransport`. These are the rules
CCP bans for breaking, so they are tested as invariants, not behaviours.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from evescreener.esi.budget import (
    BudgetExceeded,
    ErrorLimitGuard,
    HistoryRateLimiter,
    TokenBudget,
    token_cost,
)
from evescreener.esi.client import HISTORY_FEED, ORDERS_FEED, EsiClient, EsiError, FeedCircuitOpen

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def http_date(moment: datetime) -> str:
    return moment.strftime("%a, %d %b %Y %H:%M:%S GMT")


class Recorder:
    """Counts real requests so 'we did not ask' is observable."""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def make_client(config, db, handler, *, now=lambda: NOW, sleeps=None):
    recorder = Recorder(handler)
    transport = httpx.MockTransport(recorder)
    client = httpx.AsyncClient(
        base_url=config.esi.base_url, headers=config.headers, transport=transport
    )

    async def sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    return EsiClient(config, db, client=client, sleep=sleep, now=now), recorder


def ok_response(request, *, body=None, expires=None, etag='W/"abc"', extra=None):
    headers = {
        "expires": http_date(expires or NOW + timedelta(minutes=5)),
        "etag": etag,
        "last-modified": http_date(NOW),
    }
    headers.update(extra or {})
    return httpx.Response(
        200, json=body if body is not None else [{"type_id": 34}], headers=headers
    )


def test_token_costs_match_the_verified_regime():
    assert token_cost(200) == 2
    assert token_cost(304) == 1
    assert token_cost(404) == 5
    assert token_cost(503) == 0


def test_token_budget_self_caps_at_half_the_limit():
    budget = TokenBudget(limit=12000, self_cap=6000, window_minutes=15)
    for _ in range(3000):
        budget.charge(200, NOW)
    assert budget.used(NOW) == 6000
    assert not budget.can_spend(2, NOW)
    with pytest.raises(BudgetExceeded):
        budget.check(2, NOW)


def test_token_budget_believes_the_server_over_itself():
    budget = TokenBudget(limit=12000, self_cap=6000)
    budget.charge(200, NOW)
    budget.observe_headers({"x-ratelimit-remaining": "7000"}, NOW)
    # Server says 5000 used; our local tally says 2. Report the larger.
    assert budget.used(NOW) == 5000


def test_token_budget_window_rolls_off():
    budget = TokenBudget(limit=12000, self_cap=6000, window_minutes=15)
    for _ in range(3000):
        budget.charge(200, NOW)
    assert budget.used(NOW + timedelta(minutes=16)) == 0


def test_history_limiter_holds_the_150_per_minute_ceiling():
    limiter = HistoryRateLimiter(per_minute=150)
    for _ in range(150):
        limiter.record(NOW)
    assert limiter.delay(NOW) > 0
    assert limiter.delay(NOW + timedelta(seconds=61)) == 0


def test_error_limit_guard_full_stops_on_420():
    guard = ErrorLimitGuard(stop_seconds=60)
    guard.observe({"x-esi-error-limit-remain": "0"}, 420, NOW)
    assert guard.block_seconds(NOW) == pytest.approx(60)
    assert guard.block_seconds(NOW + timedelta(seconds=61)) == 0


def _run(coro):
    """Drive one coroutine to completion; the suite needs no asyncio plugin."""
    return asyncio.run(coro)


def test_never_fetches_before_expiry(config, db):
    client, recorder = make_client(config, db, lambda request: ok_response(request))

    async def scenario():
        first = await client.get(ORDERS_FEED, "/markets/10000002/orders")
        second = await client.get(ORDERS_FEED, "/markets/10000002/orders")
        return first, second

    first, second = _run(scenario())
    assert first.ok and not first.skipped
    assert second.skipped, "a second call inside the Expires window must not hit the network"
    assert len(recorder.requests) == 1
    assert client.skipped_count == 1


def test_refetches_once_expired(config, db):
    clock = {"now": NOW}
    client, recorder = make_client(
        config, db, lambda request: ok_response(request), now=lambda: clock["now"]
    )

    async def scenario():
        await client.get(ORDERS_FEED, "/markets/10000002/orders")
        clock["now"] = NOW + timedelta(minutes=6)
        return await client.get(ORDERS_FEED, "/markets/10000002/orders")

    second = _run(scenario())
    assert not second.skipped
    assert len(recorder.requests) == 2


def test_sends_if_none_match_after_the_first_response(config, db):
    clock = {"now": NOW}
    client, recorder = make_client(
        config, db, lambda request: ok_response(request), now=lambda: clock["now"]
    )

    async def scenario():
        await client.get(ORDERS_FEED, "/markets/10000002/orders")
        clock["now"] = NOW + timedelta(minutes=6)
        await client.get(ORDERS_FEED, "/markets/10000002/orders")

    _run(scenario())
    assert "if-none-match" not in recorder.requests[0].headers
    assert recorder.requests[1].headers["if-none-match"] == 'W/"abc"'


def test_every_request_carries_the_descriptive_ua_and_pinned_date(config, db):
    client, recorder = make_client(config, db, lambda request: ok_response(request))
    _run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    headers = recorder.requests[0].headers
    assert headers["user-agent"] == config.app.user_agent
    assert headers["x-compatibility-date"] == config.app.compatibility_date


def test_304_is_freshness_confirmed_not_a_failure(config, db):
    clock = {"now": NOW}
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return ok_response(request)
        return httpx.Response(304, headers={"expires": http_date(NOW + timedelta(minutes=12))})

    client, _ = make_client(config, db, handler, now=lambda: clock["now"])

    async def scenario():
        await client.get(ORDERS_FEED, "/markets/10000002/orders")
        clock["now"] = NOW + timedelta(minutes=6)
        return await client.get(ORDERS_FEED, "/markets/10000002/orders")

    result = _run(scenario())
    assert result.not_modified
    assert result.status == 304
    assert not result.usable


def test_4xx_is_never_retried(config, db):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, text="not found")

    client, _ = make_client(config, db, handler)
    with pytest.raises(EsiError, match="404"):
        _run(client.get(HISTORY_FEED, "/markets/10000002/history"))
    assert calls["n"] == 1


def test_5xx_is_retried_then_surfaced(config, db):
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="down")

    client, _ = make_client(config, db, handler, sleeps=sleeps)
    with pytest.raises(EsiError, match="503"):
        _run(client.get(HISTORY_FEED, "/markets/10000002/history"))
    assert calls["n"] == config.esi.max_retries + 1
    assert sleeps, "5xx must back off between attempts"


def test_429_sleeps_retry_after_and_resumes(config, db):
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return ok_response(request)

    client, _ = make_client(config, db, handler, sleeps=sleeps)
    result = _run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    assert result.ok
    assert 7.0 in sleeps


def test_420_is_a_full_stop(config, db):
    client, _ = make_client(config, db, lambda request: httpx.Response(420, text="rate limited"))
    with pytest.raises(EsiError, match="420"):
        _run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    assert client.error_limit.block_seconds(NOW) > 0


def test_circuit_breaker_opens_after_consecutive_failures(config, db):
    client, _ = make_client(config, db, lambda request: httpx.Response(503))

    async def scenario():
        for _ in range(config.esi.circuit_breaker_failures):
            with pytest.raises(EsiError):
                await client.get(HISTORY_FEED, "/markets/10000002/history", params={"type_id": 34})
        with pytest.raises(FeedCircuitOpen):
            await client.get(HISTORY_FEED, "/markets/10000002/history", params={"type_id": 35})

    _run(scenario())


def test_orders_sweep_stops_at_the_self_cap(config, db):
    def handler(request):
        return ok_response(request, extra={"x-pages": "5000"}, etag='W/"sweep"')

    client, _ = make_client(config, db, handler)
    client.tokens.charge(200, NOW)
    with pytest.raises(BudgetExceeded):
        _run(client.get_all_pages(ORDERS_FEED, "/markets/10000002/orders"))


def test_paged_sweep_reports_its_own_completeness(config, db):
    def handler(request):
        page = int(request.url.params.get("page", 1))
        return ok_response(
            request, body=[{"page": page}], extra={"x-pages": "3"}, etag=f'W/"p{page}"'
        )

    client, _ = make_client(config, db, handler)
    result = _run(client.get_all_pages(ORDERS_FEED, "/markets/10000002/orders"))
    assert result.pages_expected == 3
    assert result.pages_fetched == 3
    assert result.complete
    assert len(result.rows) == 3


def test_every_request_lands_in_the_telemetry_ledger(config, db):
    client, _ = make_client(config, db, lambda request: ok_response(request))
    _run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    _run(client.get(ORDERS_FEED, "/markets/10000002/orders"))
    rows = db.ledger_since(NOW - timedelta(hours=1))
    assert len(rows) == 2
    assert rows[0]["feed"] == ORDERS_FEED
    assert rows[1]["from_cache"] == 1
    assert rows[1]["note"] == "not-expired"


def test_expiry_is_stored_even_when_the_response_carries_no_etag(config, db):
    """The expiry is what the bannable rule keys off; an absent ETag must not
    silently license early polling."""
    client, recorder = make_client(
        config,
        db,
        lambda request: httpx.Response(
            200, json=[{"type_id": 34}], headers={"expires": http_date(NOW + timedelta(minutes=5))}
        ),
    )

    async def scenario():
        first = await client.get(ORDERS_FEED, "/markets/10000002/orders")
        second = await client.get(ORDERS_FEED, "/markets/10000002/orders")
        return first, second

    first, second = _run(scenario())
    assert first.ok
    assert second.skipped, "no ETag must not mean no expiry tracking"
    assert len(recorder.requests) == 1
    assert "if-none-match" not in recorder.requests[0].headers


def test_a_short_wait_never_becomes_an_early_fetch(config, db):
    """The page-wait path sleeps, then RE-CHECKS. A no-op sleep must not fetch."""
    clock = {"now": NOW}
    client, recorder = make_client(
        config, db, lambda request: ok_response(request), now=lambda: clock["now"]
    )

    async def scenario():
        # Seed an expiry 60s out, then ask with a wait cap that would cover it —
        # but with a sleep that does not advance the clock.
        await client.get(ORDERS_FEED, "/markets/10000002/orders")
        return await client.get(ORDERS_FEED, "/markets/10000002/orders", wait_cap_seconds=600)

    result = _run(scenario())
    assert result.skipped, "a sleep that did not reach the expiry must not fetch"
    assert len(recorder.requests) == 1
    rows = db.ledger_since(NOW - timedelta(hours=1))
    assert rows[-1]["note"] == "not-expired-after-wait"


def test_the_wait_path_does_fetch_once_the_expiry_actually_passes(config, db):
    clock = {"now": NOW}
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] = clock["now"] + timedelta(seconds=seconds)

    recorder = Recorder(
        lambda request: ok_response(request, expires=clock["now"] + timedelta(minutes=5))
    )
    http_client = httpx.AsyncClient(
        base_url=config.esi.base_url,
        headers=config.headers,
        transport=httpx.MockTransport(recorder),
    )
    client = EsiClient(config, db, client=http_client, sleep=sleep, now=lambda: clock["now"])

    async def scenario():
        await client.get(ORDERS_FEED, "/markets/10000002/orders")
        return await client.get(ORDERS_FEED, "/markets/10000002/orders", wait_cap_seconds=600)

    result = _run(scenario())
    assert result.ok and not result.skipped
    assert len(recorder.requests) == 2
    assert sleeps and sleeps[0] > 0
