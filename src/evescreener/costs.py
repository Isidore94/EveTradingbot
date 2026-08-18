"""The cost model (plan.md §5).

Costs are netted *inside* the screen. The ranked quantity is a net number at an
intended size, never a gross margin with fees mentioned in a footnote.

The operator is a taker on entry: he crosses the spread, so entry is an
ask-walk VWAP at the intended notional. Two exits are modeled and both are
reported — the maker branch's fee edge is real but carries queue risk that a
snapshot cannot price, so the screen shows both numbers and never picks.

Everything here is pure arithmetic over floats so it can be checked against a
real fill by hand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

UNKNOWN = "unknown"
PRICED = "priced"


def sales_tax_rate(base_pct: float, accounting_level: int) -> float:
    """Effective sales tax as a fraction: ``base * (1 - 0.11 * Accounting)``.

    7.5% base at Accounting V => 3.375%. Paid on every sell, always (§5).
    """
    return (base_pct / 100.0) * (1.0 - 0.11 * accounting_level)


@dataclass(frozen=True)
class Quote:
    """A netted round trip at one notional tier.

    ``status`` is tri-state by construction: anything the book cannot answer
    comes back ``unknown`` with a reason, and UNKNOWN always fails the screen
    (§4, §8 Phase 0). A row is never silently priced off data that isn't there.
    """

    notional_isk: float
    status: str
    reason: str | None
    entry_price: float
    entry_units: int
    exit_taker_gross: float
    exit_taker_net: float
    net_margin_pct: float
    breakeven_sell_taker: float
    breakeven_move_taker_pct: float
    breakeven_sell_maker: float
    breakeven_move_maker_pct: float

    @property
    def clears_costs(self) -> bool:
        """True only for a priced row whose round trip is positive right now."""
        return self.status == PRICED and self.net_margin_pct > 0.0


def _unknown(notional: float, reason: str) -> Quote:
    nan = float("nan")
    return Quote(
        notional_isk=notional,
        status=UNKNOWN,
        reason=reason,
        entry_price=nan,
        entry_units=0,
        exit_taker_gross=nan,
        exit_taker_net=nan,
        net_margin_pct=nan,
        breakeven_sell_taker=nan,
        breakeven_move_taker_pct=nan,
        breakeven_sell_maker=nan,
        breakeven_move_maker_pct=nan,
    )


def quote(
    *,
    notional_isk: float,
    ask_walk_price: float,
    ask_walk_units: int,
    bid_walk_price: float,
    best_ask: float,
    tax_rate: float,
    broker_rate: float,
) -> Quote:
    """Net one round trip at ``notional_isk``.

    ``ask_walk_price`` is the effective unit price of buying ``notional_isk``
    worth from the sell side; ``bid_walk_price`` is the effective unit price of
    selling the same notional into the buy side. Either being NaN means the
    book cannot absorb that size — which is the whole point of pricing at a
    notional rather than at top of book (§9 R5).
    """
    if not _finite(ask_walk_price):
        return _unknown(notional_isk, "sell side cannot fill this notional")
    if not _finite(bid_walk_price):
        return _unknown(notional_isk, "buy side cannot absorb this notional")
    if tax_rate + broker_rate >= 1.0:
        return _unknown(notional_isk, "configured fees exceed 100%")

    exit_gross = bid_walk_price
    exit_net = exit_gross * (1.0 - tax_rate)
    net_margin_pct = (exit_net - ask_walk_price) / ask_walk_price * 100.0

    breakeven_taker = ask_walk_price / (1.0 - tax_rate)
    breakeven_maker = ask_walk_price / (1.0 - tax_rate - broker_rate)

    # Each breakeven is quoted against the price the operator would actually
    # have to beat: the bid walk for a taker exit, the resting ask for a maker.
    move_taker = (breakeven_taker / bid_walk_price - 1.0) * 100.0
    move_maker = (
        (breakeven_maker / best_ask - 1.0) * 100.0
        if _finite(best_ask)
        else float("nan")
    )

    return Quote(
        notional_isk=notional_isk,
        status=PRICED,
        reason=None,
        entry_price=ask_walk_price,
        entry_units=ask_walk_units,
        exit_taker_gross=exit_gross,
        exit_taker_net=exit_net,
        net_margin_pct=net_margin_pct,
        breakeven_sell_taker=breakeven_taker,
        breakeven_move_taker_pct=move_taker,
        breakeven_sell_maker=breakeven_maker,
        breakeven_move_maker_pct=move_maker,
    )


def spread_pct(best_bid: float, best_ask: float) -> float:
    """Quoted spread as a percentage of the mid price."""
    if not (_finite(best_bid) and _finite(best_ask)):
        return float("nan")
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return float("nan")
    return (best_ask - best_bid) / mid * 100.0


def _finite(value: float | None) -> bool:
    return value is not None and isinstance(value, float | int) and math.isfinite(value)
