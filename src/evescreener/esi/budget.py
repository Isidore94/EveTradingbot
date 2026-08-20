"""Rate-limit accounting: the token regime, the history ceiling, error limits.

Three independent limiters, because ESI has three independent limits
(plan.md §0, §3.2):

* `TokenBudget` — the `market-order` group: 12,000 tokens per 15-minute
  floating window, orders endpoint only. 2xx costs 2, 304 costs 1, 4xx costs
  5, 5xx costs 0. We self-cap at 6,000 (50%) so a bug can never spend to the
  cap, and we believe the server's `X-Ratelimit-Remaining` over our own count
  whenever it is present.
* `HistoryRateLimiter` — `/markets/{region}/history` is *outside* the token
  regime and carries a CCP-stated 300 req/min/IP with developer-app
  termination named as the sanction. Self-cap: 150/min.
* `ErrorLimitGuard` — the legacy limit still applies on **all** routes: 100
  non-2xx/3xx per minute, then HTTP 420. A 420 is a full stop, not a retry.

None of these is advisory. Exceeding them risks the operator's account.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..timeutil import utcnow

# Verified token costs by status class (plan.md §0).
TOKEN_COST_2XX = 2
TOKEN_COST_304 = 1
TOKEN_COST_4XX = 5
TOKEN_COST_5XX = 0


class BudgetExceeded(RuntimeError):
    """Raised when a request would breach a self-cap. Never caught-and-retried."""


def token_cost(status: int) -> int:
    if status == 304:
        return TOKEN_COST_304
    if 200 <= status < 300:
        return TOKEN_COST_2XX
    if 400 <= status < 500:
        return TOKEN_COST_4XX
    return TOKEN_COST_5XX


@dataclass
class TokenBudget:
    """Floating-window token accountant for the `market-order` group."""

    limit: int = 12000
    self_cap: int = 6000
    window_minutes: int = 15
    _spend: deque[tuple[datetime, int]] = field(default_factory=deque, repr=False)
    server_remaining: int | None = None
    server_observed_at: datetime | None = None

    def _prune(self, now: datetime) -> None:
        horizon = now - timedelta(minutes=self.window_minutes)
        while self._spend and self._spend[0][0] < horizon:
            self._spend.popleft()

    def used(self, now: datetime | None = None) -> int:
        """Best estimate of tokens spent in the current window.

        The server's own `X-Ratelimit-Used` is authoritative while fresh; our
        local tally is the fallback and the floor (never under-report).
        """
        now = now or utcnow()
        self._prune(now)
        local = sum(cost for _, cost in self._spend)
        if self.server_remaining is not None and self.server_observed_at is not None:
            if now - self.server_observed_at <= timedelta(minutes=self.window_minutes):
                return max(local, self.limit - self.server_remaining)
        return local

    def remaining(self, now: datetime | None = None) -> int:
        """Tokens left *under the self-cap*, which is the only cap we honour."""
        return max(0, self.self_cap - self.used(now))

    def can_spend(self, cost: int = TOKEN_COST_2XX, now: datetime | None = None) -> bool:
        return self.remaining(now) >= cost

    def check(self, cost: int = TOKEN_COST_2XX, now: datetime | None = None) -> None:
        if not self.can_spend(cost, now):
            raise BudgetExceeded(
                f"orders token self-cap reached: used {self.used(now)} of {self.self_cap} "
                f"(hard limit {self.limit}) in the last {self.window_minutes} min"
            )

    def charge(self, status: int, now: datetime | None = None) -> int:
        cost = token_cost(status)
        if cost:
            self._spend.append((now or utcnow(), cost))
        return cost

    def observe_headers(self, headers, now: datetime | None = None) -> None:
        raw_remaining = headers.get("x-ratelimit-remaining")
        if raw_remaining is None:
            return
        try:
            self.server_remaining = int(raw_remaining)
        except (TypeError, ValueError):
            return
        self.server_observed_at = now or utcnow()

    def seconds_until_available(self, cost: int = TOKEN_COST_2XX, now: datetime | None = None):
        """How long until `cost` fits under the self-cap; None when it fits now."""
        now = now or utcnow()
        if self.can_spend(cost, now):
            return None
        if not self._spend:
            return float(self.window_minutes * 60)
        oldest = self._spend[0][0]
        wait = (oldest + timedelta(minutes=self.window_minutes) - now).total_seconds()
        return max(1.0, wait)


@dataclass
class HistoryRateLimiter:
    """Self-capped 150 req/min for the history endpoint (CCP states 300)."""

    per_minute: int = 150
    _calls: deque[datetime] = field(default_factory=deque, repr=False)

    def _prune(self, now: datetime) -> None:
        horizon = now - timedelta(seconds=60)
        while self._calls and self._calls[0] < horizon:
            self._calls.popleft()

    def used(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        self._prune(now)
        return len(self._calls)

    def delay(self, now: datetime | None = None) -> float:
        """Seconds to wait before the next call keeps us under the ceiling."""
        now = now or utcnow()
        self._prune(now)
        if len(self._calls) < self.per_minute:
            return 0.0
        return max(0.0, (self._calls[0] + timedelta(seconds=60) - now).total_seconds())

    def record(self, now: datetime | None = None) -> None:
        self._calls.append(now or utcnow())

    async def acquire(self) -> None:
        while True:
            wait = self.delay()
            if wait <= 0:
                self.record()
                return
            await asyncio.sleep(wait)


@dataclass
class ErrorLimitGuard:
    """The legacy 100-errors/minute limit that applies to every route."""

    stop_seconds: int = 60
    remain: int | None = None
    blocked_until: datetime | None = None

    def observe(self, headers, status: int, now: datetime | None = None) -> None:
        now = now or utcnow()
        raw = headers.get("x-esi-error-limit-remain")
        if raw is not None:
            try:
                self.remain = int(raw)
            except (TypeError, ValueError):
                self.remain = None
        if status == 420:
            self.blocked_until = now + timedelta(seconds=self.stop_seconds)

    def block_seconds(self, now: datetime | None = None) -> float:
        now = now or utcnow()
        if self.blocked_until and self.blocked_until > now:
            return (self.blocked_until - now).total_seconds()
        return 0.0
