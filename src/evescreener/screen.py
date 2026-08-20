"""The candidate screen — build universe → compute → net costs → rank.

The pipeline shape is the source repo's (plan.md §2): build universe, fetch
bars, compute, rank, publish, each stage individually try/excepted, last-good
on failure. What it ranks is different: **net expected edge at a real
notional**, never a gross margin.

The two rules that shape every row:

* a setup whose expected move does not clear `breakeven_move_pct` at the
  **smallest** tier is not opportunity and is not shown;
* a stale book renders the cost UNKNOWN and the row is flagged, never silently
  priced off history.

"Nothing clears costs today" is a valid, expected output (plan.md §5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .costs import CostModel
from .signals.atr import atr_last, risk_unit
from .signals.avwap import anchored_vwap_bands, classify_band
from .signals.levels import build_level_store, levels_near
from .signals.rrs import real_relative_strength
from .signals.setup import SetupParams, anchor_grid, evaluate_setups
from .store.db import Database
from .timeutil import ensure_utc, iso, parse_iso, utcnow

__all__ = ["Candidate", "ScreenResult", "run_screen"]


@dataclass(slots=True)
class Candidate:
    type_id: int
    type_name: str | None
    close: float
    band_zone: str
    dip_sigma: float | None
    rrs: float | None
    rrs_scope: str
    participation: float | None
    atr: float | None
    risk_unit: float | None
    destruction_z: float | None
    tier_breakevens: list[dict] = field(default_factory=list)
    net_edge_pct: float | None = None
    expected_move_pct: float | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    maker_exit_advisory: float | None = None
    book_sweep_ts: str | None = None
    book_age_minutes: float | None = None
    freshness: str = "UNKNOWN"
    flags: list[str] = field(default_factory=list)
    thesis: str = ""
    nearby_levels: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "type_name": self.type_name,
            "close": self.close,
            "band_zone": self.band_zone,
            "dip_sigma": self.dip_sigma,
            "rrs": self.rrs,
            "rrs_scope": self.rrs_scope,
            "participation": self.participation,
            "atr": self.atr,
            "risk_unit": self.risk_unit,
            "destruction_z": self.destruction_z,
            "tier_breakevens": self.tier_breakevens,
            "net_edge_pct": self.net_edge_pct,
            "expected_move_pct": self.expected_move_pct,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "maker_exit_advisory": self.maker_exit_advisory,
            "book_sweep_ts": self.book_sweep_ts,
            "book_age_minutes": self.book_age_minutes,
            "freshness": self.freshness,
            "flags": self.flags,
            "thesis": self.thesis,
            "nearby_levels": self.nearby_levels,
        }


@dataclass(slots=True)
class ScreenResult:
    generated_at: str
    region_id: int
    candidates: list[dict] = field(default_factory=list)
    universe: int = 0
    setups_found: int = 0
    below_breakeven: int = 0
    unknown_cost: int = 0
    stale_book: int = 0
    composite: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def honest_zero(self) -> bool:
        return not self.candidates

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "region_id": self.region_id,
            "candidates": self.candidates,
            "universe": self.universe,
            "setups_found": self.setups_found,
            "below_breakeven": self.below_breakeven,
            "unknown_cost": self.unknown_cost,
            "stale_book": self.stale_book,
            "composite": self.composite,
            "gates": self.gates,
            "notes": self.notes,
        }


def _book_rows(book: pd.DataFrame, type_id: int):
    if book is None or book.empty:
        return None, None
    rows = book[book["type_id"] == int(type_id)]
    if rows.empty:
        return None, None
    sells = rows[rows["side"] == "sell"]
    buys = rows[rows["side"] == "buy"]
    sell = sells.iloc[-1] if not sells.empty else None
    buy = buys.iloc[-1] if not buys.empty else None
    return sell, buy


def _tier_prices(row, tiers) -> dict[float, float | None]:
    prices: dict[float, float | None] = {}
    for index, tier in enumerate(tiers):
        if row is None:
            prices[float(tier)] = None
            continue
        value = row.get(f"depth_fill_price_{index}")
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        prices[float(tier)] = value if value and np.isfinite(value) and value > 0 else None
    return prices


def run_screen(
    config: Config,
    db: Database,
    bars: pd.DataFrame,
    composite,
    book: pd.DataFrame,
    *,
    type_ids: list[int] | None = None,
    destruction: pd.DataFrame | None = None,
    anchor_dates=(),
    region_id: int | None = None,
    now=None,
) -> ScreenResult:
    """Build ranked candidates for one region. Every displayed number is net."""
    now = ensure_utc(now or utcnow())
    region = region_id or config.esi.home_region_id
    costs = CostModel.from_config(config)
    tiers = tuple(float(value) for value in config.costs.notional_tiers_isk)
    result = ScreenResult(generated_at=iso(now), region_id=region)
    if composite is not None and getattr(composite, "diagnostics", None):
        result.composite = dict(composite.diagnostics)
    composite_frame = getattr(composite, "frame", composite)

    if bars is None or bars.empty:
        result.notes.append("the bar lake is empty for this region; nothing can be screened")
        return result

    # Group ONCE: per-type boolean masks over the whole lake are O(n x m).
    groups = dict(tuple(bars.groupby("type_id", sort=True)))
    ids = type_ids if type_ids is not None else sorted(groups)
    result.universe = len(ids)
    params = SetupParams(
        entry_band_sigma=config.backtest.entry_band_sigma,
        min_rrs=config.backtest.min_rrs,
        participation_floor=config.backtest.participation_floor,
        min_bars=config.backtest.min_bars,
        anchor_lookback_days=config.backtest.anchor_lookback_days,
        rrs_length=config.signals.rrs_length,
        atr_length=config.signals.atr_length,
        participation_window=config.screen.participation_window,
        atr_winsor_k=config.signals.atr_winsor_k,
        atr_winsor_window=config.signals.atr_winsor_window,
    )
    destruction_latest: dict[int, float] = {}
    if destruction is not None and not destruction.empty:
        newest = destruction.sort_values("day").groupby("type_id").tail(1)
        destruction_latest = {
            int(row.type_id): float(row.destruction_z) for row in newest.itertuples()
        }

    names = db.type_names(ids)
    candidates: list[Candidate] = []
    for type_id in ids:
        group = groups.get(type_id)
        if group is None:
            continue
        frame = group.sort_values("datetime").reset_index(drop=True)
        if len(frame) < params.min_bars:
            continue
        try:
            evaluated = evaluate_setups(frame, composite_frame, params, anchor_dates=anchor_dates)
        except Exception as exc:  # noqa: BLE001 - one bad type never kills the sweep
            result.notes.append(f"type {type_id}: {type(exc).__name__}: {exc}")
            continue
        if evaluated.empty:
            continue
        for key, value in _gate_counts(evaluated).items():
            result.gates[key] = result.gates.get(key, 0) + value
        last = evaluated.iloc[-1]
        if not bool(last["is_setup"]):
            continue
        result.setups_found += 1

        sell_row, buy_row = _book_rows(book, type_id)
        sweep_ts = str(sell_row.get("sweep_ts")) if sell_row is not None else None
        age_minutes = None
        stale_reason = None
        if sweep_ts:
            swept = parse_iso(sweep_ts) or ensure_utc(pd.Timestamp(sweep_ts).to_pydatetime())
            age_minutes = (now - swept).total_seconds() / 60.0
            if age_minutes > config.costs.book_staleness_minutes:
                stale_reason = f"book swept {age_minutes:.0f} min ago"
        else:
            stale_reason = "no book sweep for this type"

        ask_prices = _tier_prices(sell_row, tiers)
        bid_prices = _tier_prices(buy_row, tiers)
        reference = float(last["close"])
        breakevens = costs.tier_breakevens(
            ask_prices=ask_prices,
            bid_prices=bid_prices,
            reference_price=reference,
            stale_reason=stale_reason,
        )
        anchors = anchor_grid(
            frame, step_days=params.anchor_lookback_days, anchor_dates=anchor_dates
        )
        bands = anchored_vwap_bands(frame, anchors[-1] if anchors else 0)
        # The expected move is the distance back to anchored value — the thesis
        # is mean reversion to value, never a continuation target.
        expected_move = (
            (bands.vwap / reference - 1.0) * 100.0 if bands.known and reference > 0 else None
        )
        priced = costs.price_round_trip(
            notional_isk=tiers[0],
            ask_walk_price=ask_prices[tiers[0]],
            ask_walk_qty=None,
            bid_walk_price=bid_prices[tiers[0]],
            reference_price=reference,
            stale_reason=stale_reason,
        )
        flags: list[str] = []
        if sell_row is not None:
            share = sell_row.get("top_order_volume_share")
            if share is not None and float(share) > config.screen.top_order_share_flag:
                flags.append(f"one order holds {float(share):.0%} of the ask book")
            if bool(sell_row.get("partial_sweep")):
                flags.append("priced from a partial sweep")
        if buy_row is not None:
            # Measured 2026-08-20 on a full Forge sweep: 0% of visible SELL
            # volume rests in player structures, while a large share of BUY
            # volume does. So the structure exposure is entirely on the EXIT
            # side — the bid-walk price is optimistic by however much of that
            # depth the operator cannot dock at. plan.md §9 R3 assumed the
            # opposite direction; the measurement corrected it.
            station = buy_row.get("station_volume_share")
            if station is not None and float(station) < 0.9:
                flags.append(
                    f"{1 - float(station):.0%} of bid depth sits in player structures — "
                    "exit may be inaccessible without docking rights"
                )
            top_bid = buy_row.get("top_order_volume_share")
            if top_bid is not None and float(top_bid) > config.screen.top_order_share_flag:
                flags.append(f"one order holds {float(top_bid):.0%} of the bid book")
        if bands.truncated:
            flags.append("anchor predates the lake horizon (truncated)")

        smallest = breakevens[0] if breakevens else None
        if smallest is None or smallest.breakeven_move_pct is None:
            result.unknown_cost += 1
            if stale_reason:
                result.stale_book += 1
            # UNKNOWN cost is never shown as an opportunity, but the count is
            # reported so an outage does not look like an absence of setups.
            continue
        if expected_move is None or expected_move <= smallest.breakeven_move_pct:
            result.below_breakeven += 1
            continue

        atr_value = atr_last(
            frame,
            length=config.signals.atr_length,
            winsor_k=config.signals.atr_winsor_k,
            winsor_window=config.signals.atr_winsor_window,
        )
        levels = build_level_store(
            frame,
            atr20=atr_value,
            round_steps=config.signals.round_number_levels_isk,
            anchor_dates=anchor_dates,
        )
        strength = real_relative_strength(
            frame, composite_frame, length=config.signals.rrs_length, scope="forge_composite"
        )
        candidates.append(
            Candidate(
                type_id=int(type_id),
                type_name=names.get(int(type_id)),
                close=reference,
                band_zone=classify_band(reference, bands),
                dip_sigma=float(last["dip_sigma"]) if np.isfinite(last["dip_sigma"]) else None,
                rrs=strength.rrs,
                rrs_scope=strength.scope if strength.known else "UNKNOWN",
                participation=float(last["participation"])
                if np.isfinite(last["participation"])
                else None,
                atr=atr_value,
                risk_unit=risk_unit(frame, length=config.signals.atr_length),
                destruction_z=destruction_latest.get(int(type_id)),
                tier_breakevens=[
                    {
                        "notional_isk": tier.notional_isk,
                        "breakeven_move_pct": tier.breakeven_move_pct,
                        "fillable": tier.fillable,
                        "reason": tier.reason,
                    }
                    for tier in breakevens
                ],
                net_edge_pct=(expected_move - smallest.breakeven_move_pct),
                expected_move_pct=expected_move,
                entry_price=priced.entry_price,
                exit_price=priced.exit_price_taker,
                maker_exit_advisory=(
                    costs.sell_proceeds(bands.vwap, maker=True) if bands.known else None
                ),
                book_sweep_ts=sweep_ts,
                book_age_minutes=round(age_minutes, 1) if age_minutes is not None else None,
                freshness="fresh" if not stale_reason else "stale",
                flags=flags,
                thesis=_thesis(last, bands, strength, destruction_latest.get(int(type_id))),
                nearby_levels=[
                    {
                        "price": level["price"],
                        "kind": level["kind"],
                        "strength": level.get("strength"),
                        "conviction": level.get("conviction"),
                    }
                    for level in levels_near(levels, reference, atr_value, min_strength=0.5)[:3]
                ],
            )
        )

    candidates.sort(key=lambda item: -(item.net_edge_pct or 0.0))
    result.candidates = [item.as_dict() for item in candidates[: config.screen.max_candidates]]
    return result


def _gate_counts(evaluated: pd.DataFrame) -> dict:
    last = evaluated.iloc[-1]
    counts = {}
    for gate in (
        "below_anchored_value",
        "relative_strength_intact",
        "participation_intact",
        "measurable",
    ):
        counts[f"{gate}_pass"] = int(bool(last[gate]))
        counts[f"{gate}_unknown"] = int(bool(last[f"{gate}_unknown"]))
    return counts


def _thesis(last, bands, strength, destruction) -> str:
    """One sentence the operator can argue with. Never a score in prose."""
    parts = [
        f"{abs(last['dip_sigma']):.2f}σ below anchored value "
        f"({bands.vwap:,.2f} vs close {last['close']:,.2f})"
        if bands.known
        else "below anchored value"
    ]
    if strength.known:
        parts.append(f"RRS {strength.rrs:+.2f} vs the Forge Composite")
    else:
        parts.append("RRS UNKNOWN")
    if np.isfinite(last["participation"]):
        parts.append(f"participation {last['participation']:.2f}x its 20-day baseline")
    if destruction is not None:
        parts.append(f"destruction_z {destruction:+.2f}")
    return "; ".join(parts)
