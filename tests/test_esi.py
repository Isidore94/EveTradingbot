"""The client's invariants, exercised offline against a mock transport.

These are the tests that stand between this repo and a ban: never fetching
before expiry, never spending past the self-cap, never retrying a 4xx.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from evescreener.clock import now_utc
from evescreener.esi import (
    ORDERS_GROUP,
    BudgetExhausted,
    ErrorLimited,
    EsiClient,
    EsiHttpError,
)


def http_date(moment: dt.datetime) -> str:
    from email.utils import format_datetime

    return format_datetime(moment, usegmt=True)


class Recorder:
    """A mock transport that records every request it is actually asked for."""

    def __init__(self, responses):
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            spec = (
                self._responses.pop(0)
                if len(self._responses) > 1
                else self._responses[0]
            )
            if isinstance(spec, Exception):
                raise spec
            return spec

        return httpx.MockTransport(handler)


def ok(payload, *, expires_in=300, etag='"abc"'):
    return httpx.Response(
        200,
        json=payload,
        headers={
            "Expires": http_date(now_utc() + dt.timedelta(seconds=expires_in)),
            "ETag": etag,
            "X-Pages": "1",
            "X-Ratelimit-Group": ORDERS_GROUP,
            "X-Ratelimit-Limit": "12000/15m",
            "X-Ratelimit-Used": "2",
            "X-Ratelimit-Remaining": "11998",
        },
    )


@pytest.fixture
def sleeps():
    recorded: list[float] = []

    async def sleeper(seconds: float) -> None:
        recorded.append(seconds)

    sleeper.recorded = recorded  # type: ignore[attr-defined]
    return sleeper


async def client_for(config, store, recorder, sleeps):
    return EsiClient(config, store, transport=recorder.transport(), sleeper=sleeps)


def run(coro):
    """Drive one coroutine to completion. pytest-asyncio is not a dependency."""
    import asyncio

    return asyncio.run(coro)


def test_a_still_fresh_url_is_never_refetched(config, store, sleeps):
    recorder = Recorder([ok([{"a": 1}])])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            first = await client.get("/markets/10000002/history")
            second = await client.get("/markets/10000002/history")
            return first, second

    first, second = run(scenario())
    assert first.outcome == "fetched"
    assert second.outcome == "skipped_fresh"
    assert second.data == first.data
    assert len(recorder.requests) == 1, "the second call must not touch the network"
    assert second.tokens_charged == 0


def test_the_ledger_records_that_expiry_was_honoured(config, store, sleeps):
    recorder = Recorder([ok([1])])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/prices")

    run(scenario())
    summary = store.ledger_summary(now_utc() - dt.timedelta(minutes=5))
    assert summary["requests"] == 1
    assert summary["early_fetches"] == 0
    assert summary["tokens"] == 2


def test_an_expired_url_is_refetched_with_if_none_match(config, store, sleeps):
    recorder = Recorder([ok([1], expires_in=-1), ok([2])])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/prices")
            return await client.get("/markets/prices")

    second = run(scenario())
    assert second.outcome == "fetched"
    assert len(recorder.requests) == 2
    assert recorder.requests[1].headers["If-None-Match"] == '"abc"'


def test_a_304_costs_one_token_and_resolves_from_the_body_cache(config, store, sleeps):
    not_modified = httpx.Response(
        304,
        headers={
            "Expires": http_date(now_utc() + dt.timedelta(seconds=300)),
            "ETag": '"abc"',
            "X-Ratelimit-Group": ORDERS_GROUP,
        },
    )
    recorder = Recorder([ok([{"payload": True}], expires_in=-1), not_modified])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/prices")
            return await client.get("/markets/prices")

    second = run(scenario())
    assert second.outcome == "not_modified"
    assert second.tokens_charged == 1
    assert second.data == [{"payload": True}]


def test_orders_fetches_stop_at_the_self_imposed_token_cap(config, store, sleeps):
    now = now_utc()
    for _ in range(config.budget.orders_token_hard_stop // 2):
        store.record_request(
            requested_at=now,
            url="https://esi.evetech.net/markets/10000002/orders?page=1",
            outcome="fetched",
            tokens_charged=2,
            ratelimit_group=ORDERS_GROUP,
        )
    recorder = Recorder([ok([1])])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/10000002/orders", group=ORDERS_GROUP)

    with pytest.raises(BudgetExhausted, match="self-cap"):
        run(scenario())
    assert recorder.requests == [], "the cap must stop the request, not log after it"


def test_a_4xx_is_surfaced_and_never_retried(config, store, sleeps):
    recorder = Recorder([httpx.Response(404, text="not found")])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/10000002/history")

    with pytest.raises(EsiHttpError):
        run(scenario())
    assert len(recorder.requests) == 1
    summary = store.ledger_summary(now_utc() - dt.timedelta(minutes=5))
    assert summary["client_errors"] == 1
    assert summary["tokens"] == 5, "a 4xx costs five tokens (§0)"


def test_a_5xx_is_retried_within_bounds_and_costs_nothing(config, store, sleeps):
    recorder = Recorder([httpx.Response(503, text="upstream")])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/prices")

    with pytest.raises(EsiHttpError):
        run(scenario())
    assert len(recorder.requests) == config.esi.max_retries + 1
    summary = store.ledger_summary(now_utc() - dt.timedelta(minutes=5))
    assert summary["tokens"] == 0
    assert len(sleeps.recorded) == config.esi.max_retries
    assert sleeps.recorded[0] >= config.esi.retry_backoff_seconds


def test_a_transport_error_is_retried_then_raised(config, store, sleeps):
    recorder = Recorder([httpx.ConnectError("boom")])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/prices")

    with pytest.raises(httpx.ConnectError):
        run(scenario())
    assert len(recorder.requests) == config.esi.max_retries + 1


def test_429_sleeps_for_retry_after_before_trying_again(config, store, sleeps):
    recorder = Recorder(
        [httpx.Response(429, headers={"Retry-After": "17"}, text="slow down")]
    )

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/10000002/orders", group=ORDERS_GROUP)

    with pytest.raises(EsiHttpError):
        run(scenario())
    assert 17.0 in sleeps.recorded


def test_420_stops_everything_for_the_configured_cooldown(config, store, sleeps):
    recorder = Recorder([httpx.Response(420, text="error limited")])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/prices")

    with pytest.raises(ErrorLimited):
        run(scenario())
    assert sleeps.recorded == [config.budget.error_limit_stop_seconds]
    assert len(recorder.requests) == 1


def test_every_request_carries_the_user_agent_and_compatibility_date(
    config, store, sleeps
):
    recorder = Recorder([ok([1])])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/prices")

    run(scenario())
    sent = recorder.requests[0]
    assert sent.headers["X-Compatibility-Date"] == config.esi.compatibility_date
    assert sent.headers["User-Agent"] == config.user_agent
    assert "@" in sent.headers["User-Agent"]


def test_a_fresh_url_with_no_cached_body_waits_out_the_window(config, store, sleeps):
    recorder = Recorder([ok([1], expires_in=120), ok([2], expires_in=120)])

    async def scenario():
        async with await client_for(config, store, recorder, sleeps) as client:
            await client.get("/markets/prices")
            # Drop the body so the client holds freshness but no data.
            for path in config.paths.cache_dir.glob("*.json.gz"):
                path.unlink()
            return await client.get("/markets/prices")

    second = run(scenario())
    assert second.outcome == "fetched"
    assert sleeps.recorded, "it must wait for expiry rather than fetch early"
    assert sleeps.recorded[0] > 0
