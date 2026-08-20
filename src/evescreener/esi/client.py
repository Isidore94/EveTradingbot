"""The ESI HTTP client.

Every rule here is a correctness invariant, not a courtesy (plan.md §3.1,
§10.4): a descriptive User-Agent, a pinned `X-Compatibility-Date`, an ETag on
every request, and — above all — **never a fetch before the previous
response's `Expires`**. A fetch that would land early is *skipped*, not
queued. CCP treats polling before expiry as cache circumvention and bans for
it.

Failure semantics (plan.md §3.3): bounded retries on 5xx and transport errors
only (5xx costs 0 tokens); a 4xx is never retried — it is a bug, surfaced. A
429 sleeps its `Retry-After`. A 420 is a full stop for the configured window.
Per-feed circuit breaker with a cooldown after consecutive failures.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..config import Config
from ..store.db import Database
from ..timeutil import ensure_utc, iso, utcnow
from .budget import BudgetExceeded, ErrorLimitGuard, HistoryRateLimiter, TokenBudget, token_cost

ORDERS_FEED = "orders"
HISTORY_FEED = "history"
TYPES_FEED = "types"


class EsiError(RuntimeError):
    """A request failed in a way the caller must see (never swallowed)."""


class FeedCircuitOpen(EsiError):
    """The feed's breaker is open; the caller waits for the cooldown."""


class EsiNotFound(EsiError):
    """A 404 for one resource.

    Measured 2026-08-20: `/markets/{region}/types` lists type_ids that
    `/markets/{region}/history` rejects with 404 (the plan's §3.2 expectation
    that 404s "should not occur in the steady state" is wrong — 16,789 of
    19,152 Forge-active types 404 on history). That is a fact about one type,
    not a fault in the feed, so it must never trip the per-feed breaker: doing
    so turns a routine catalogue gap into a total ingest outage.
    """


@dataclass(slots=True)
class EsiResponse:
    """One ESI answer, or an honest statement that we did not ask.

    `skipped=True` means the cached response had not expired, so no request
    was made. That is a *success* — it is the invariant working.
    """

    url: str
    status: int
    data: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    expires: datetime | None = None
    last_modified: str | None = None
    pages: int | None = None
    not_modified: bool = False
    skipped: bool = False
    fetched_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def usable(self) -> bool:
        """Did this call produce a body the caller can reduce?"""
        return self.ok and self.data is not None


def parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return ensure_utc(parsedate_to_datetime(value))
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class PagedResult:
    """A multi-page sweep, with its own completeness stated rather than assumed."""

    rows: list[Any]
    first: EsiResponse
    pages_expected: int
    pages_fetched: int = 0

    @property
    def complete(self) -> bool:
        return self.pages_expected > 0 and self.pages_fetched == self.pages_expected


class EsiClient:
    """Async ESI client with expiry, ETag, budget and breaker enforcement."""

    def __init__(
        self,
        config: Config,
        db: Database,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
        now=utcnow,
    ) -> None:
        self.config = config
        self.db = db
        self._sleep = sleep
        self._now = now
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.esi.base_url,
            headers=config.headers,
            timeout=config.esi.timeout_seconds,
            http2=True,
            transport=transport,
            follow_redirects=False,
        )
        self.tokens = TokenBudget(
            limit=config.budget.orders_token_limit,
            self_cap=config.budget.orders_token_self_cap,
            window_minutes=config.budget.orders_window_minutes,
        )
        self.history_limiter = HistoryRateLimiter(config.budget.history_requests_per_minute)
        self.error_limit = ErrorLimitGuard(
            config.budget.error_limit_stop_seconds,
            pause_remaining=config.budget.error_limit_pause_remaining,
        )
        self.skipped_count = 0
        self.request_count = 0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> EsiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- circuit breaker ---------------------------------------------------
    def _breaker_check(self, feed: str) -> None:
        row = self.db.feed_health(feed)
        if row is None or not row["opened_at"]:
            return
        opened = ensure_utc(datetime.fromisoformat(row["opened_at"]))
        cooldown = timedelta(minutes=self.config.esi.circuit_breaker_cooldown_minutes)
        if self._now() < opened + cooldown:
            raise FeedCircuitOpen(
                f"feed '{feed}' breaker open until {iso(opened + cooldown)}: {row['last_error']}"
            )
        self.db.set_feed_health(feed, 0, None, None)

    def _breaker_failure(self, feed: str, error: str) -> None:
        row = self.db.feed_health(feed)
        failures = (row["consecutive_failures"] if row else 0) + 1
        opened = self._now() if failures >= self.config.esi.circuit_breaker_failures else None
        self.db.set_feed_health(feed, failures, opened, error[:500])

    def _breaker_success(self, feed: str) -> None:
        row = self.db.feed_health(feed)
        if row is not None and (row["consecutive_failures"] or row["opened_at"]):
            self.db.set_feed_health(feed, 0, None, None)

    # -- expiry ------------------------------------------------------------
    def not_expired(self, url: str) -> datetime | None:
        """Return the stored `Expires` if it is still in the future."""
        expires = self.db.expires_at(url)
        if expires is None:
            return None
        return expires if expires > self._now() else None

    # -- core --------------------------------------------------------------
    async def get(
        self,
        feed: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        wait_cap_seconds: float = 0.0,
        note: str | None = None,
    ) -> EsiResponse:
        """One GET, fully governed. Returns `skipped` when still fresh.

        There is no override: a URL whose stored `Expires` is in the future is
        either *waited out* (when the wait fits inside `wait_cap_seconds`, used
        to complete a multi-page sweep whose pages expire seconds apart) or
        skipped outright. Nothing in this system fetches early.
        """
        url = self._absolute(path, params)
        self._breaker_check(feed)

        expires = self.not_expired(url)
        if expires is not None:
            wait = (expires - self._now()).total_seconds() + self.config.esi.expiry_jitter_seconds
            if wait_cap_seconds > 0 and wait <= wait_cap_seconds:
                await self._sleep(max(0.0, wait))
            else:
                self.skipped_count += 1
                self._record(feed, url, None, 0.0, {}, from_cache=True, note="not-expired")
                return EsiResponse(url=url, status=304, expires=expires, skipped=True)

        block = self.error_limit.block_seconds(self._now())
        if block > 0:
            await self._sleep(block)

        if feed == ORDERS_FEED:
            self.tokens.check(now=self._now())
        if feed == HISTORY_FEED:
            delay = self.history_limiter.delay(self._now())
            if delay > 0:
                await self._sleep(delay)
            self.history_limiter.record(self._now())

        headers: dict[str, str] = {}
        stored = self.db.get_etag(url)
        if stored is not None and stored["etag"]:
            headers["If-None-Match"] = stored["etag"]

        attempt = 0
        last_error: str | None = None
        while attempt <= self.config.esi.max_retries:
            attempt += 1
            started = self._now()
            try:
                response = await self._client.get(path, params=params, headers=headers)
            except httpx.HTTPError as exc:  # transport failure: retryable
                last_error = f"{type(exc).__name__}: {exc}"
                self._record(feed, url, None, self._elapsed_ms(started), {}, note=last_error)
                if attempt > self.config.esi.max_retries:
                    break
                await self._backoff(attempt)
                continue

            self.request_count += 1
            duration = self._elapsed_ms(started)
            status = response.status_code
            hdrs = {key.lower(): value for key, value in response.headers.items()}
            if feed == ORDERS_FEED:
                self.tokens.charge(status, self._now())
                self.tokens.observe_headers(hdrs, self._now())
            self.error_limit.observe(hdrs, status, self._now())
            self._record(feed, url, status, duration, hdrs, note=note)

            if status == 420:
                last_error = "HTTP 420 error limit — full stop"
                self._breaker_failure(feed, last_error)
                raise EsiError(last_error)

            if status == 429:
                retry_after = float(hdrs.get("retry-after") or 5)
                await self._sleep(retry_after)
                if attempt > self.config.esi.max_retries:
                    last_error = "HTTP 429 exhausted retries"
                    break
                continue

            if status == 304:
                self._breaker_success(feed)
                expires_at = parse_http_date(hdrs.get("expires"))
                self.db.touch_etag_expiry(url, expires_at)
                return EsiResponse(
                    url=url,
                    status=304,
                    headers=hdrs,
                    expires=expires_at,
                    last_modified=hdrs.get("last-modified"),
                    not_modified=True,
                    fetched_at=self._now(),
                )

            if 500 <= status < 600:
                last_error = f"HTTP {status}"
                if attempt > self.config.esi.max_retries:
                    break
                await self._backoff(attempt)
                continue

            if status == 404:
                # Per-resource fact, not a feed failure. The breaker is left
                # alone; the error-limit guard above already yields when the
                # 4xx budget runs low.
                self._breaker_success(feed)
                raise EsiNotFound(f"{url} -> HTTP 404: {response.text[:200]}")

            if 400 <= status < 500:
                # Never retried: a 4xx is a bug in our request, surfaced.
                self._breaker_failure(feed, f"HTTP {status} {response.text[:200]}")
                raise EsiError(f"{url} -> HTTP {status}: {response.text[:300]}")

            expires_at = parse_http_date(hdrs.get("expires"))
            self.db.put_etag(url, hdrs.get("etag"), expires_at, hdrs.get("last-modified"))
            self._breaker_success(feed)
            pages = int(hdrs["x-pages"]) if hdrs.get("x-pages") else None
            return EsiResponse(
                url=url,
                status=status,
                data=response.json(),
                headers=hdrs,
                expires=expires_at,
                last_modified=hdrs.get("last-modified"),
                pages=pages,
                fetched_at=self._now(),
            )

        message = f"{url} failed after {attempt} attempts: {last_error}"
        self._breaker_failure(feed, message)
        raise EsiError(message)

    async def get_all_pages(
        self,
        feed: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_wait_cap_seconds: float = 120.0,
    ) -> PagedResult:
        """Fetch every page of a paginated route.

        Page 1 decides: if it is still fresh, no page is fetched at all. Later
        pages carry their own `Expires` a few seconds apart, so a page that is
        marginally fresh is *waited out* rather than fetched early; a page that
        would need a longer wait is left unfetched and the sweep is reported
        **partial**, never silently presented as a complete book. Cross-page
        duplicates and gaps are data-quality counters, not errors (§0 check #2).
        """
        first_params = dict(params or {})
        first_params["page"] = 1
        first = await self.get(feed, path, params=first_params)
        if first.skipped or not first.usable:
            return PagedResult(rows=[], first=first, pages_expected=first.pages or 0)
        rows = list(first.data)
        pages = first.pages or 1
        fetched = 1
        for page in range(2, pages + 1):
            page_params = dict(params or {})
            page_params["page"] = page
            if feed == ORDERS_FEED and not self.tokens.can_spend(token_cost(200), self._now()):
                raise BudgetExceeded(
                    f"orders self-cap hit mid-sweep at page {page}/{pages}; sweep abandoned"
                )
            result = await self.get(
                feed, path, params=page_params, wait_cap_seconds=page_wait_cap_seconds
            )
            if result.usable:
                rows.extend(result.data)
                fetched += 1
        return PagedResult(rows=rows, first=first, pages_expected=pages, pages_fetched=fetched)

    # -- helpers -----------------------------------------------------------
    def _absolute(self, path: str, params: dict[str, Any] | None) -> str:
        request = self._client.build_request("GET", path, params=params)
        return str(request.url)

    def _elapsed_ms(self, started: datetime) -> float:
        return (self._now() - started).total_seconds() * 1000.0

    async def _backoff(self, attempt: int) -> None:
        base = self.config.esi.retry_base_seconds * (2 ** (attempt - 1))
        await self._sleep(base + random.uniform(0, 1.0))

    def _record(
        self,
        feed: str,
        url: str,
        status: int | None,
        duration_ms: float,
        headers: dict[str, str],
        *,
        from_cache: bool = False,
        note: str | None = None,
    ) -> None:
        def as_int(key: str) -> int | None:
            raw = headers.get(key)
            try:
                return int(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None

        self.db.record_request(
            {
                "requested_at": iso(self._now()),
                "feed": feed,
                "url": url,
                "status": status,
                "duration_ms": round(duration_ms, 2),
                "tokens_used": as_int("x-ratelimit-used"),
                "tokens_remaining": as_int("x-ratelimit-remaining"),
                "ratelimit_group": headers.get("x-ratelimit-group"),
                "expires_at": iso(parse_http_date(headers.get("expires"))),
                "error_limit_remain": as_int("x-esi-error-limit-remain"),
                "from_cache": 1 if from_cache else 0,
                "note": note,
            }
        )
