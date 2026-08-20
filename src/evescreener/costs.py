"""The EVE cost model — plan.md §5.

EVE has more friction than a retail equity account, and all of it is netted
**inside** the screen. Gross margins never appear anywhere in this system: the
ranked quantity is net, computed from effective entry and exit prices at the
intended size.

Components, all config-driven (skills are settings, never constants):

* **Sales tax** on every sell: `7.5% x (1 - 0.11 x Accounting)` -> 3.375% at V.
* **Broker fee** on every *posted* order — maker only, never on a taker fill —
  plus a relist/modify surcharge on order updates.
* **Market impact** on both sides, from the depth walk at the declared
  notional. A gorgeous margin that cannot absorb 0.25B is not an edge; the
  wide margin usually *is* the illiquidity premium (plan.md §9 R5).
* **Buy escrow**: posted buys lock 100% ISK. No leverage. Charged as
  capital-days, never as free money.
* **Freight** on cross-region only, from a real quote (plan.md §11 / §16).

The taker-entry assumption is the operator's actual behaviour: entry crosses
the spread at the ask-walk VWAP for the size — not best ask, not mid.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .config import Config

__all__ = ["CostModel", "TradeCosts", "TierBreakeven"]


@dataclass(frozen=True, slots=True)
class TradeCosts:
    """One priced round trip at one notional. Every field is ISK or a percent."""

    notional_isk: float
    entry_price: float | None
    entry_qty: float | None
    exit_price_taker: float | None
    exit_price_maker: float | None
    sales_tax_pct: float
    broker_fee_pct: float
    entry_slippage_pct: float | None
    exit_slippage_pct: float | None
    breakeven_move_pct: float | None
    net_edge_pct_taker: float | None
    net_edge_pct_maker: float | None
    unknown_reason: str | None = None

    @property
    def known(self) -> bool:
        """UNKNOWN always fails: a cost we could not measure is not a cost of 0."""
        return self.unknown_reason is None

    def as_dict(self) -> dict:
        return {
            "notional_isk": self.notional_isk,
            "entry_price": self.entry_price,
            "entry_qty": self.entry_qty,
            "exit_price_taker": self.exit_price_taker,
            "exit_price_maker": self.exit_price_maker,
            "sales_tax_pct": self.sales_tax_pct,
            "broker_fee_pct": self.broker_fee_pct,
            "entry_slippage_pct": self.entry_slippage_pct,
            "exit_slippage_pct": self.exit_slippage_pct,
            "breakeven_move_pct": self.breakeven_move_pct,
            "net_edge_pct_taker": self.net_edge_pct_taker,
            "net_edge_pct_maker": self.net_edge_pct_maker,
            "unknown_reason": self.unknown_reason,
        }


@dataclass(frozen=True, slots=True)
class TierBreakeven:
    """The floor a setup must clear at one notional tier."""

    notional_isk: float
    breakeven_move_pct: float | None
    fillable: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CostModel:
    """Fees and impact, derived once from config and reused everywhere."""

    sales_tax_pct: float
    broker_fee_pct: float
    relist_surcharge_multiple: float
    notional_tiers_isk: tuple[float, ...]
    book_staleness_minutes: int
    annual_capital_cost_pct: float
    #: Operator-observed effective broker rates by station id (§21 R4). Empty
    #: means "use the skill-derived base everywhere", the previous behaviour.
    broker_fee_overrides: dict = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Config) -> CostModel:
        costs = config.costs
        tax = costs.sales_tax_base_pct * (
            1.0 - costs.sales_tax_per_level_reduction * costs.accounting_level
        )
        broker = max(
            0.0,
            costs.broker_fee_base_pct
            - costs.broker_fee_per_level_pct * costs.broker_relations_level
            - costs.broker_fee_standings_pct,
        )
        return cls(
            sales_tax_pct=tax,
            broker_fee_pct=broker,
            relist_surcharge_multiple=costs.relist_surcharge_multiple,
            notional_tiers_isk=costs.notional_tiers_isk,
            book_staleness_minutes=costs.book_staleness_minutes,
            annual_capital_cost_pct=costs.annual_capital_cost_pct,
        )

    # -- primitives --------------------------------------------------------
    def sell_proceeds(self, gross_isk: float, *, maker: bool) -> float:
        """ISK actually received. Tax always; broker fee only when posting."""
        rate = self.sales_tax_pct + (self.broker_fee_pct if maker else 0.0)
        return gross_isk * (1.0 - rate / 100.0)

    def buy_outlay(self, gross_isk: float, *, maker: bool) -> float:
        """ISK actually spent. A posted buy pays the broker fee; a taker does not."""
        rate = self.broker_fee_pct if maker else 0.0
        return gross_isk * (1.0 + rate / 100.0)

    def broker_fee_at(self, location_id: int | None) -> float:
        """Broker fee at one station, which is not one number game-wide (§21 R4).

        Broker fee scales with corporation standings, and standings are held
        per corporation — so the rate differs between hubs owned by different
        NPC corps. A single scalar applied everywhere quietly prices Amarr as
        if it were Jita. Overrides are **operator-observed effective rates**,
        never derived; with none configured this returns the skill-derived
        base, which is the previous behaviour exactly.
        """
        if location_id is None:
            return self.broker_fee_pct
        return self.broker_fee_overrides.get(int(location_id), self.broker_fee_pct)

    def with_broker_overrides(self, overrides: dict[int, float]) -> CostModel:
        """A copy carrying operator-**observed** per-station broker rates.

        These are transcribed from what the client actually charged, not
        computed from standings the system cannot read. That is why they are
        an override and not a model.
        """
        return replace(
            self,
            broker_fee_overrides={int(key): float(value) for key, value in overrides.items()},
        )

    def relist_cost_unverified(
        self, *, old_price: float, new_price: float, quantity: float = 1.0
    ) -> float:
        """Cost of modifying a resting order — **UNVERIFIED** (plan.md §0 #5).

        The previous `relist_cost(gross_isk)` charged a broker fee on the whole
        order value. That is not the game's formula: EVE charges on the change
        between the **old** and **new** price, so the old form overstated a
        one-tick undercut by orders of magnitude and understated nothing.

        This form takes the price change, which is the right shape — but the
        exact terms and skill discount have never been checked against a live
        client, and plan.md §0 open check #5 remains open. **No analytical
        output may consume this**, and a test enforces that: a wrong cost model
        is worse than an absent one, because it looks answered.
        """
        delta = abs(float(new_price) - float(old_price))
        return (
            delta * float(quantity) * (self.broker_fee_pct / 100.0) * self.relist_surcharge_multiple
        )

    def escrow_capital_days(self, notional_isk: float, days: float) -> float:
        """ISK-days of capital an escrowed buy locks up; 0% by default."""
        return notional_isk * max(0.0, days)

    def capital_charge(self, notional_isk: float, days: float) -> float:
        if self.annual_capital_cost_pct <= 0:
            return 0.0
        return notional_isk * (self.annual_capital_cost_pct / 100.0) * (max(0.0, days) / 365.0)

    def round_trip_fee_pct(self, *, maker_exit: bool) -> float:
        """Fee floor before any spread or impact: tax (+ broker on a maker exit)."""
        return self.sales_tax_pct + (self.broker_fee_pct if maker_exit else 0.0)

    # -- the screen's floor -------------------------------------------------
    def breakeven_move_pct(
        self,
        *,
        entry_price: float | None,
        exit_price: float | None,
        reference_price: float | None,
        maker_exit: bool = False,
    ) -> float | None:
        """Percent the price must move for the round trip to break even.

        `entry_price` is the ask-walk VWAP at size and `exit_price` the
        bid-walk VWAP at size, both measured; `reference_price` is the mid or
        last close the move is quoted against. Any missing input returns None
        — UNKNOWN, never an optimistic zero.
        """
        if not entry_price or not exit_price or not reference_price:
            return None
        if entry_price <= 0 or exit_price <= 0 or reference_price <= 0:
            return None
        fee = self.round_trip_fee_pct(maker_exit=maker_exit) / 100.0
        # Buy at entry_price, later sell at exit_price x (1 + m) net of fees.
        # Break even when exit_price x (1 + m) x (1 - fee) == entry_price.
        required_exit = entry_price / (1.0 - fee)
        return (required_exit / exit_price - 1.0) * 100.0

    def price_round_trip(
        self,
        *,
        notional_isk: float,
        ask_walk_price: float | None,
        ask_walk_qty: float | None,
        bid_walk_price: float | None,
        reference_price: float | None,
        maker_target_price: float | None = None,
        stale_reason: str | None = None,
    ) -> TradeCosts:
        """Price one round trip at one notional from measured depth.

        `stale_reason` short-circuits to UNKNOWN: a book older than the
        configured staleness window renders the cost unknown and the row is
        flagged, never silently priced off history (plan.md §5).
        """
        if stale_reason:
            return TradeCosts(
                notional_isk=notional_isk,
                entry_price=None,
                entry_qty=None,
                exit_price_taker=None,
                exit_price_maker=None,
                sales_tax_pct=self.sales_tax_pct,
                broker_fee_pct=self.broker_fee_pct,
                entry_slippage_pct=None,
                exit_slippage_pct=None,
                breakeven_move_pct=None,
                net_edge_pct_taker=None,
                net_edge_pct_maker=None,
                unknown_reason=stale_reason,
            )
        if not ask_walk_price or not bid_walk_price:
            return TradeCosts(
                notional_isk=notional_isk,
                entry_price=ask_walk_price,
                entry_qty=ask_walk_qty,
                exit_price_taker=bid_walk_price,
                exit_price_maker=None,
                sales_tax_pct=self.sales_tax_pct,
                broker_fee_pct=self.broker_fee_pct,
                entry_slippage_pct=None,
                exit_slippage_pct=None,
                breakeven_move_pct=None,
                net_edge_pct_taker=None,
                net_edge_pct_maker=None,
                unknown_reason="book too thin to fill this notional on both sides",
            )

        exit_taker_net = self.sell_proceeds(bid_walk_price, maker=False)
        exit_maker_net = (
            self.sell_proceeds(maker_target_price, maker=True) if maker_target_price else None
        )
        entry_slip = (
            (ask_walk_price / reference_price - 1.0) * 100.0
            if reference_price and reference_price > 0
            else None
        )
        exit_slip = (
            (1.0 - bid_walk_price / reference_price) * 100.0
            if reference_price and reference_price > 0
            else None
        )
        breakeven = self.breakeven_move_pct(
            entry_price=ask_walk_price,
            exit_price=bid_walk_price,
            reference_price=reference_price,
            maker_exit=False,
        )
        net_taker = (exit_taker_net / ask_walk_price - 1.0) * 100.0
        net_maker = (
            (exit_maker_net / ask_walk_price - 1.0) * 100.0 if exit_maker_net is not None else None
        )
        return TradeCosts(
            notional_isk=notional_isk,
            entry_price=ask_walk_price,
            entry_qty=ask_walk_qty,
            exit_price_taker=bid_walk_price,
            exit_price_maker=maker_target_price,
            sales_tax_pct=self.sales_tax_pct,
            broker_fee_pct=self.broker_fee_pct,
            entry_slippage_pct=entry_slip,
            exit_slippage_pct=exit_slip,
            breakeven_move_pct=breakeven,
            net_edge_pct_taker=net_taker,
            net_edge_pct_maker=net_maker,
        )

    def tier_breakevens(
        self,
        *,
        ask_prices: dict[float, float | None],
        bid_prices: dict[float, float | None],
        reference_price: float | None,
        stale_reason: str | None = None,
    ) -> list[TierBreakeven]:
        """Breakeven at every configured notional tier (0.25B / 1.0B / 2.5B)."""
        tiers: list[TierBreakeven] = []
        for notional in self.notional_tiers_isk:
            if stale_reason:
                tiers.append(TierBreakeven(notional, None, False, stale_reason))
                continue
            ask = ask_prices.get(notional)
            bid = bid_prices.get(notional)
            if not ask or not bid:
                tiers.append(
                    TierBreakeven(notional, None, False, "insufficient depth at this notional")
                )
                continue
            tiers.append(
                TierBreakeven(
                    notional,
                    self.breakeven_move_pct(
                        entry_price=ask, exit_price=bid, reference_price=reference_price
                    ),
                    True,
                )
            )
        return tiers
