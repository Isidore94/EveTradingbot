"""The scanner — the built-in setup and every operator setup, across the desk
universe (plan.md §19 Part 2 page 5, Part 3).

What it is careful about:

* **Honest zero, per setup.** Each setup reports its own result. "Nothing
  cleared this today" is a valid, expected answer and it is printed as such,
  next to the count of names actually examined — an empty panel with no
  denominator is indistinguishable from a scan that never ran (§5).
* **UNKNOWN is counted, not hidden.** A setup whose conditions could not be
  measured on a name reports that name as unmeasurable rather than as a
  rejection. "We could not look" and "we looked and it failed" are different
  facts and the operator needs both.
* **Costs travel with every hit.** A hit that cannot clear its own friction is
  still a hit, and its friction is printed beside it. The scanner does not
  quietly filter on net edge — that is the screen's job, and the board's rule
  (§18) applies here too.
* **THIN is badged.** A name in the 100–999 units/day band appears with its
  badge wherever it appears (§11 D3, amended).
* **Freshness is stamped.** Every result carries the age of the book it was
  priced against; a stale book renders UNKNOWN friction, never a guess.

The scan reads only the local lake and the local state database. It never
causes an ESI fetch, which is what lets the desk refresh on a timer without
touching the Expires invariant (§3.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .costs import CostModel
from .indices import Sector, sector_for_type
from .screen import _tier_prices, setup_params
from .setups import Setup, SetupContext, evaluate_setup
from .signals.atr import atr_last
from .signals.avwap import anchored_vwap_bands
from .signals.levels import build_level_store
from .signals.setup import GATE_NAMES, anchor_grid, evaluate_setups
from .store.db import Database
from .timeutil import ensure_utc, iso, utcnow
from .universe import tier_badge

__all__ = [
    "BUILTIN_SETUP_NAME",
    "ScanResult",
    "SetupScan",
    "render_scan",
    "run_scan",
]

# The system's own setup keeps a reserved name so an operator setup can never
# quietly shadow it in a results table.
BUILTIN_SETUP_NAME = "Dip below anchored value (built-in)"


@dataclass(slots=True)
class SetupScan:
    """One setup's result across the universe. Zero hits is an answer."""

    name: str
    enabled: bool = True
    builtin: bool = False
    example: bool = False
    notes: str = ""
    validation: str = "UNVALIDATED"
    examined: int = 0
    unmeasurable: int = 0
    hits: list[dict] = field(default_factory=list)
    unmeasurable_conditions: tuple[str, ...] = ()

    @property
    def honest_zero(self) -> bool:
        return self.examined > 0 and not self.hits

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "builtin": self.builtin,
            "example": self.example,
            "notes": self.notes,
            "validation": self.validation,
            "examined": self.examined,
            "unmeasurable": self.unmeasurable,
            "hits": self.hits,
            "unmeasurable_conditions": list(self.unmeasurable_conditions),
        }


@dataclass(slots=True)
class ScanResult:
    generated_at: str
    region_id: int
    universe: int = 0
    evaluated: int = 0
    book_sweep_ts: str | None = None
    book_age_minutes: float | None = None
    banner: str = ""
    setups: list[SetupScan] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "region_id": self.region_id,
            "universe": self.universe,
            "evaluated": self.evaluated,
            "book_sweep_ts": self.book_sweep_ts,
            "book_age_minutes": self.book_age_minutes,
            "banner": self.banner,
            "setups": [scan.as_dict() for scan in self.setups],
            "notes": self.notes,
        }


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _friction(config: Config, costs: CostModel, book, type_id: int, close, now) -> tuple:
    """(friction_pct, book_age_minutes, stale_reason) — never a guessed number."""
    from .brief import _book_state

    sell_row, buy_row, _ts, age, stale_reason = _book_state(config, book, int(type_id), now)
    tier0 = float(config.costs.notional_tiers_isk[0])
    priced = costs.price_round_trip(
        notional_isk=tier0,
        ask_walk_price=_tier_prices(sell_row, (tier0,)).get(tier0),
        ask_walk_qty=None,
        bid_walk_price=_tier_prices(buy_row, (tier0,)).get(tier0),
        reference_price=close,
        stale_reason=stale_reason,
    )
    friction = -priced.net_edge_pct_taker if priced.net_edge_pct_taker is not None else None
    return friction, age, stale_reason


@dataclass(slots=True)
class _RowBuilder:
    """One type's result row, priced against the book at most once.

    A type can hit several setups on the same day; walking the book once per
    setup would be pure waste, and worse, it would give two hits on the same
    name two different friction numbers if a sweep landed in between.
    """

    config: Config
    costs: CostModel
    book: pd.DataFrame
    now: object
    type_id: int
    type_name: str | None
    tier: str | None
    close: float | None
    last: pd.Series
    atr: float | None
    rrs_sector: float | None
    sector_ticker: str | None
    _friction: float | None = None
    _age: float | None = None
    _stale: str | None = None
    _priced: bool = False

    def build(self, conditions: list[dict] | None = None) -> dict:
        if not self._priced:
            self._friction, self._age, self._stale = _friction(
                self.config, self.costs, self.book, self.type_id, self.close, self.now
            )
            self._priced = True
        return {
            "type_id": self.type_id,
            "type_name": self.type_name,
            "tier": self.tier,
            "badge": tier_badge(self.tier),
            "close": self.close,
            "dip_sigma": _finite(self.last["dip_sigma"]),
            "rrs": _finite(self.last["rrs"]),
            "rrs_sector": self.rrs_sector,
            "sector": self.sector_ticker,
            "participation": _finite(self.last["participation"]),
            "atr": self.atr,
            "friction_pct": self._friction,
            "book_age_minutes": self._age,
            "stale_reason": self._stale,
            "conditions": conditions or [],
        }


def run_scan(
    config: Config,
    db: Database,
    bars: pd.DataFrame,
    composite_frame: pd.DataFrame | None,
    book: pd.DataFrame,
    *,
    setups: list[Setup] | None = None,
    sectors: list[Sector] | None = None,
    sector_frames: dict[str, pd.DataFrame] | None = None,
    anchor_dates=(),
    region_id: int | None = None,
    now=None,
    backtest_verdict: dict | None = None,
    validation: dict[str, str] | None = None,
) -> ScanResult:
    """Run the built-in setup and every enabled operator setup over `bars`."""
    from .backtest import verdict_banner

    now = ensure_utc(now or utcnow())
    result = ScanResult(
        generated_at=iso(now),
        region_id=region_id or config.esi.home_region_id,
        banner=verdict_banner(backtest_verdict),
    )
    params = setup_params(config)
    costs = CostModel.from_config(config)
    operator = [setup for setup in (setups or []) if setup.enabled]

    builtin = SetupScan(name=BUILTIN_SETUP_NAME, builtin=True, validation="VALIDATED")
    scans = {BUILTIN_SETUP_NAME: builtin}
    for setup in operator:
        scans[setup.name] = SetupScan(
            name=setup.name,
            example=setup.example,
            notes=setup.notes,
            validation=(validation or {}).get(setup.name, "UNVALIDATED"),
        )
    result.setups = list(scans.values())

    if bars is None or bars.empty:
        result.notes.append("no bars in the lake; nothing to scan")
        return result

    groups = dict(tuple(bars.groupby("type_id", sort=True)))
    result.universe = len(groups)
    names = db.type_names(list(groups))
    tiers = {
        int(row["type_id"]): row["tier"]
        for row in db.conn.execute(
            "SELECT type_id, tier FROM universe WHERE region_id=?", (result.region_id,)
        )
    }
    round_steps = tuple(config.signals.round_number_levels_isk)

    for type_id, group in groups.items():
        frame = group.sort_values("datetime").reset_index(drop=True)
        if len(frame) < params.min_bars:
            continue
        evaluated = evaluate_setups(frame, composite_frame, params, anchor_dates=anchor_dates)
        if evaluated.empty:
            continue
        result.evaluated += 1
        last = evaluated.iloc[-1]
        close = _finite(last["close"])
        atr = atr_last(
            frame,
            length=params.atr_length,
            winsor_k=params.atr_winsor_k,
            winsor_window=params.atr_winsor_window,
        )
        anchors = anchor_grid(
            frame, step_days=params.anchor_lookback_days, anchor_dates=anchor_dates
        )
        bands = anchored_vwap_bands(frame, anchors[-1] if anchors else 0)
        sector = sector_for_type(db, sectors or [], int(type_id))
        rrs_sector = None
        if sector is not None and sector_frames and sector.ticker in sector_frames:
            from .signals.rrs import real_relative_strength

            strength = real_relative_strength(
                frame,
                sector_frames[sector.ticker],
                length=params.rrs_length,
                scope=sector.ticker,
            )
            rrs_sector = strength.rrs
        context = SetupContext(
            frame=frame,
            evaluated=evaluated,
            level_store=build_level_store(frame, atr20=atr, round_steps=round_steps)
            if atr
            else None,
            atr=atr,
            rrs_forge=_finite(last["rrs"]),
            rrs_sector=rrs_sector,
            sector_ticker=sector.ticker if sector else None,
            bands=bands,
        )

        row = _RowBuilder(
            config=config,
            costs=costs,
            book=book,
            now=now,
            type_id=int(type_id),
            type_name=names.get(int(type_id)),
            tier=tiers.get(int(type_id)),
            close=close,
            last=last,
            atr=atr,
            rrs_sector=rrs_sector,
            sector_ticker=sector.ticker if sector else None,
        )

        builtin.examined += 1
        if bool(last["is_setup"]):
            builtin.hits.append(row.build())
        elif any(bool(last.get(f"{gate}_unknown", False)) for gate in GATE_NAMES):
            # A gate that could not be measured is not a rejection. Counting it
            # separately is what keeps "nothing cleared" distinguishable from
            # "we could not look" (§4).
            builtin.unmeasurable += 1

        for setup in operator:
            scan = scans[setup.name]
            scan.examined += 1
            verdict = evaluate_setup(setup, context)
            if verdict.fired:
                scan.hits.append(row.build([r.as_dict() for r in verdict.results]))
            elif verdict.unknown:
                scan.unmeasurable += 1

    for scan in result.setups:
        scan.hits.sort(
            key=lambda row: (row["dip_sigma"] is None, row["dip_sigma"] or 0.0),
        )
    if not book.empty and "sweep_ts" in book:
        stamps = book["sweep_ts"].dropna()
        if not stamps.empty:
            result.book_sweep_ts = str(stamps.max())
    return result


def render_scan(result: ScanResult) -> str:
    """Text mode. One block per setup, honest zero included."""
    from .brief import format_isk

    lines = [
        f"# Scanner — {result.generated_at[:16]}Z · region {result.region_id}",
    ]
    if result.banner:
        lines.extend(["", result.banner])
    lines.extend(
        [
            "",
            f"{result.evaluated} of {result.universe} names had enough bars to evaluate.",
        ]
    )
    for scan in result.setups:
        label = scan.name
        marks = []
        if scan.builtin:
            marks.append("built-in")
        if scan.example:
            marks.append("example")
        marks.append(scan.validation)
        lines.extend(["", f"## {label} — {' · '.join(marks)}"])
        if scan.notes:
            lines.append(f"_{scan.notes}_")
        if scan.unmeasurable_conditions:
            lines.append(
                "cannot be measured over history: " + "; ".join(scan.unmeasurable_conditions)
            )
        if not scan.hits:
            lines.append(
                f"Nothing cleared this setup today "
                f"({scan.examined} examined, {scan.unmeasurable} unmeasurable). "
                "That is an answer, not a gap."
            )
            continue
        lines.append(
            f"   {'name':<30} {'close':>12} {'dipσ':>7} {'RRS':>7} {'frict%':>7} {'thin':>5}"
        )
        for row in scan.hits:
            name = (row["type_name"] or f"type {row['type_id']}")[:30]

            def cell(value, spec):
                return format(value, spec) if value is not None else "—"

            lines.append(
                f"   {name:<30} {format_isk(row['close']):>12} "
                f"{cell(row['dip_sigma'], '+7.2f'):>7} {cell(row['rrs'], '+7.2f'):>7} "
                f"{cell(row['friction_pct'], '7.2f'):>7} {row['badge']:>5}"
            )
        lines.append(
            f"{len(scan.hits)} hit(s) of {scan.examined} examined; "
            f"{scan.unmeasurable} unmeasurable."
        )
    return "\n".join(lines)
