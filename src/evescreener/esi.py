"""The ESI client. Public, read-only, and cache-respecting by construction.

Every rule in plan.md §3.1-§3.3 is enforced here rather than left to callers:

* a descriptive User-Agent with operator contact, and a pinned
  ``X-Compatibility-Date``, on every request;
* **never fetch before expiry** — a request whose cached ``Expires`` has not
  passed is *skipped*, not queued (§3.1). Circumventing the cache is a
  bannable offence, so this is a correctness invariant, not an optimisation;
* ``If-None-Match`` on every request, with the body cached on disk so a 304
  always resolves to real data for one token instead of two;
* the token accountant: the ``market-order`` group's 12,000/15-min budget is
  tracked from the ledger and orders fetches hard-stop at the configured 50%
  self-cap, so a bug cannot spend to the cap;
* bounded retries on 5xx and transport errors only. A 4xx is never retried —
  it is a bug to surface, and it costs 5 tokens.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .clock import UTC, now_utc, parse_http_date
from .config import Config
from .state import CacheEntry, StateStore

ORDERS_GROUP = "market-order"

# Token costs for the market-order group, verified against live headers (§0).
TOKEN_COST_SUCCESS = 2
TOKEN_COST_NOT_MODIFIED = 1
TOKEN_COST_CLIENT_ERROR = 5
TOKEN_COST_SERVER_ERROR = 0

TOKEN_WINDOW = dt.timedelta(minutes=15)

# HTTP 420: the legacy error limit, 100 non-2xx/3xx per minute, all routes.
STATUS_ERROR_LIMITED = 420


class EsiError(RuntimeError):
    """Base class for client-side stop conditions."""


class BudgetExhausted(EsiError):
    """The self-imposed token cap was reached. Stop, do not throttle-and-hope."""


class ErrorLimited(EsiError):
    """HTTP 420 — the legacy error limit tripped. Full stop (§3.2)."""


class EsiHttpError(EsiError):
    """A 4xx that is not retried: a bug to surface, never a silent skip."""

    def __init__(self, status: int, url: str, body: str) -> None:
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status
        self.url = url


@dataclass(frozen=True)
class EsiResponse:
    """One resolved request.

    ``outcome`` is tri-state on purpose: ``skipped_fresh`` means "we already
    hold data that has not expired", which is a different fact from "we asked
    and it had not changed" (``not_modified``) and from a real fetch.
    """

    url: str
    outcome: str
    data: Any | None
    status: int | None
    expires_at: dt.datetime | None
    last_modified: str | None
    pages: int | None
    tokens_charged: int
    fetched_at: dt.datetime

    @property
    def is_usable(self) -> bool:
        return self.data is not None


@dataclass
class BudgetAccountant:
    """Rolling estimate of the ``market-order`` token window (§3.2)."""

    store: StateStore
    hard_stop: int
    window_limit: int
    spent: int = 0
    observed_used: int = 0
    observed_remaining: int | None = None
    _window_start: dt.datetime | None = field(default=None, repr=False)

    def load(self, now: dt.datetime) -> None:
        self._window_start = now - TOKEN_WINDOW
        self.spent = self.store.tokens_used_since(self._window_start, ORDERS_GROUP)

    def check(self, now: dt.datetime, cost: int) -> None:
        if self._window_start is None or now - self._window_start > TOKEN_WINDOW:
            self.load(now)
        if self.spent + cost > self.hard_stop:
            raise BudgetExhausted(
                f"orders token self-cap reached: {self.spent} spent in the last "
                f"15 minutes, hard stop {self.hard_stop} of {self.window_limit}"
            )

    def charge(self, cost: int) -> None:
        self.spent += cost

    def observe(self, used: int | None, remaining: int | None) -> None:
        if used is not None:
            self.observed_used = max(self.observed_used, used)
        if remaining is not None:
            self.observed_remaining = remaining


class RateLimiter:
    """Minimum spacing between requests, for the history endpoint's 300/min.

    We take 50% of CCP's stated ceiling (§3.2, D3). The endpoint is outside the
    token regime, so this pacing is the only thing standing between the daily
    job and the limit CCP attaches developer-app termination to.
    """

    def __init__(self, per_minute: int) -> None:
        self._interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._next_at - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_at = max(loop.time(), self._next_at) + self._interval


class BodyCache:
    """Gzipped response bodies keyed by URL, so a 304 resolves to real data."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json.gz"

    def get(self, url: str) -> Any | None:
        path = self._path(url)
        if not path.exists():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)

    def put(self, url: str, data: Any) -> None:
        from .paths import atomic_write_path

        self.root.mkdir(parents=True, exist_ok=True)
        with (
            atomic_write_path(self._path(url)) as tmp,
            gzip.open(tmp, "wt", encoding="utf-8") as handle,
        ):
            json.dump(data, handle)


class EsiClient:
    """Async ESI client. One per process; ``async with`` manages the transport."""

    def __init__(
        self,
        config: Config,
        store: StateStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: Any = asyncio.sleep,
    ) -> None:
        self.config = config
        self.store = store
        self._sleep = sleeper
        self._client = httpx.AsyncClient(
            base_url=config.esi.base_url,
            http2=True,
            timeout=config.esi.timeout_seconds,
            transport=transport,
            headers={
                "User-Agent": config.user_agent,
                "X-Compatibility-Date": config.esi.compatibility_date,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )
        self.budget = BudgetAccountant(
            store=store,
            hard_stop=config.budget.orders_token_hard_stop,
            window_limit=config.budget.orders_tokens_per_window,
        )
        self.history_limiter = RateLimiter(config.budget.history_requests_per_minute)
        self.bodies = BodyCache(config.paths.cache_dir)

    async def __aenter__(self) -> EsiClient:
        self.budget.load(now_utc())
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.aclose()

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        group: str | None = None,
        paced: bool = False,
        force: bool = False,
    ) -> EsiResponse:
        """Fetch ``path``, honouring cache, budget, and pacing rules.

        ``force`` exists for the live smoke path only; it still refuses to fetch
        before expiry — it only bypasses the *local* freshness short-circuit
        when the caller genuinely needs the body and does not hold one.
        """
        url = self._absolute(path, params)
        now = now_utc()
        cached = self.store.get_cache(url)

        if cached is not None and cached.is_fresh(now):
            body = self.bodies.get(url)
            if body is not None and not force:
                self.store.record_request(
                    requested_at=now,
                    url=url,
                    status=None,
                    outcome="skipped_fresh",
                    tokens_charged=0,
                    ratelimit_group=group,
                    expires_at=cached.expires_at,
                    prior_expires_at=cached.expires_at,
                    honored_expiry=1,
                    note="cached response has not expired; not refetching",
                )
                return EsiResponse(
                    url=url,
                    outcome="skipped_fresh",
                    data=body,
                    status=cached.status,
                    expires_at=cached.expires_at,
                    last_modified=cached.last_modified,
                    pages=None,
                    tokens_charged=0,
                    fetched_at=cached.fetched_at,
                )
            # No usable body for a still-fresh URL. Waiting out the window is
            # the only correct move: fetching now would be cache circumvention.
            wait = (cached.expires_at - now).total_seconds()
            await self._sleep(wait + random.uniform(0, self.config.esi.jitter_seconds))
            now = now_utc()

        if paced:
            await self.history_limiter.acquire()

        return await self._fetch(url, cached, group, now)

    async def _fetch(
        self,
        url: str,
        cached: CacheEntry | None,
        group: str | None,
        now: dt.datetime,
    ) -> EsiResponse:
        headers: dict[str, str] = {}
        if cached is not None and cached.etag:
            headers["If-None-Match"] = cached.etag

        attempt = 0
        while True:
            attempt += 1
            request_at = now_utc()
            if group == ORDERS_GROUP:
                self.budget.check(request_at, TOKEN_COST_SUCCESS)

            started = asyncio.get_running_loop().time()
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                self.store.record_request(
                    requested_at=request_at,
                    url=url,
                    status=None,
                    outcome="transport_error",
                    tokens_charged=0,
                    ratelimit_group=group,
                    prior_expires_at=cached.expires_at if cached else None,
                    honored_expiry=self._honored(cached, request_at),
                    sent_if_none_match=int("If-None-Match" in headers),
                    note=repr(exc),
                )
                if attempt > self.config.esi.max_retries:
                    raise
                await self._backoff(attempt)
                continue

            duration_ms = int((asyncio.get_running_loop().time() - started) * 1000)
            result = self._record(
                response, url, cached, group, request_at, duration_ms, headers
            )
            if result is not None:
                return result

            if response.status_code == STATUS_ERROR_LIMITED:
                await self._sleep(self.config.budget.error_limit_stop_seconds)
                raise ErrorLimited(
                    f"HTTP 420 from {url}: stopped for "
                    f"{self.config.budget.error_limit_stop_seconds}s"
                )

            if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                await self._sleep(float(response.headers.get("Retry-After", "60")))
                if attempt > self.config.esi.max_retries:
                    raise EsiHttpError(429, url, response.text)
                continue

            if 400 <= response.status_code < 500:
                raise EsiHttpError(response.status_code, url, response.text)

            # 5xx: costs zero tokens, retried with bounded backoff.
            if attempt > self.config.esi.max_retries:
                raise EsiHttpError(response.status_code, url, response.text)
            await self._backoff(attempt)

    def _record(
        self,
        response: httpx.Response,
        url: str,
        cached: CacheEntry | None,
        group: str | None,
        request_at: dt.datetime,
        duration_ms: int,
        sent_headers: dict[str, str],
    ) -> EsiResponse | None:
        status = response.status_code
        expires_at = self._header_date(response, "Expires")
        cost = self._token_cost(status)
        used = _int_header(response, "X-Ratelimit-Used")
        remaining = _int_header(response, "X-Ratelimit-Remaining")
        self.budget.observe(used, remaining)
        if group == ORDERS_GROUP:
            self.budget.charge(cost)

        outcome = {True: "not_modified"}.get(status == 304, "fetched")
        if status >= 400:
            outcome = "error"

        self.store.record_request(
            requested_at=request_at,
            url=url,
            status=status,
            outcome=outcome,
            tokens_charged=cost,
            ratelimit_group=response.headers.get("X-Ratelimit-Group", group),
            ratelimit_limit=response.headers.get("X-Ratelimit-Limit"),
            ratelimit_remaining=remaining,
            ratelimit_used=used,
            error_limit_remain=_int_header(response, "X-Esi-Error-Limit-Remain"),
            expires_at=expires_at,
            prior_expires_at=cached.expires_at if cached else None,
            honored_expiry=self._honored(cached, request_at),
            sent_if_none_match=int("If-None-Match" in sent_headers),
            duration_ms=duration_ms,
        )

        if status >= 400:
            return None

        etag = response.headers.get("ETag", cached.etag if cached else None)
        last_modified = response.headers.get("Last-Modified")
        self.store.put_cache(
            CacheEntry(
                url=url,
                etag=etag,
                expires_at=expires_at,
                last_modified=last_modified,
                fetched_at=request_at,
                status=status,
            )
        )

        if status == 304:
            data = self.bodies.get(url)
        else:
            data = response.json()
            self.bodies.put(url, data)

        return EsiResponse(
            url=url,
            outcome=outcome,
            data=data,
            status=status,
            expires_at=expires_at,
            last_modified=last_modified,
            pages=_int_header(response, "X-Pages"),
            tokens_charged=cost,
            fetched_at=request_at,
        )

    async def _backoff(self, attempt: int) -> None:
        base = self.config.esi.retry_backoff_seconds * (2 ** (attempt - 1))
        await self._sleep(base + random.uniform(0, self.config.esi.jitter_seconds))

    @staticmethod
    def _honored(cached: CacheEntry | None, now: dt.datetime) -> int:
        """1 unless we asked again while a stored ``Expires`` was still future."""
        if cached is None or cached.expires_at is None:
            return 1
        return int(now >= cached.expires_at)

    @staticmethod
    def _token_cost(status: int) -> int:
        if status == 429:
            return 0  # 429 is exempt from token charges (§0)
        if status == 304:
            return TOKEN_COST_NOT_MODIFIED
        if 200 <= status < 300:
            return TOKEN_COST_SUCCESS
        if 400 <= status < 500:
            return TOKEN_COST_CLIENT_ERROR
        return TOKEN_COST_SERVER_ERROR

    @staticmethod
    def _header_date(response: httpx.Response, name: str) -> dt.datetime | None:
        raw = response.headers.get(name)
        if not raw:
            return None
        try:
            return parse_http_date(raw)
        except (TypeError, ValueError):
            return None

    def _absolute(self, path: str, params: dict[str, Any] | None) -> str:
        request = self._client.build_request("GET", path, params=params)
        return str(request.url)


def _int_header(response: httpx.Response, name: str) -> int | None:
    raw = response.headers.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def window_start(now: dt.datetime | None = None) -> dt.datetime:
    """Start of the current rolling token window."""
    return (now or now_utc()).astimezone(UTC) - TOKEN_WINDOW
