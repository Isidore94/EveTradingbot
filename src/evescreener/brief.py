"""The desk workflow, ported — per-type brief, observation board, watchlist rows.

TradingBotV3's desk gave the trader two surfaces the digest alone does not
(plan.md §2, §18): the per-symbol chart — bands, levels, strength, and what a
trade would actually cost — and the strength board, the whole universe as one
sortable cross-section. This module is their text-mode port: `build_brief` for
one type, `build_board` for the tracked universe, and the compact watchlist
rows the digest carries every day.

Two rules keep these surfaces honest:

* **Observation, never opportunity.** The honest-zero rule (plan.md §5)
  governs the digest's candidate panel; the board deliberately shows types
  that do NOT clear costs — with their measured friction printed beside them —
  because learning why the screen rejects a type is the point of looking.
  Nothing here ranks on net edge and nothing here calls itself a pick.
* **UNKNOWN stays UNKNOWN.** A stale book, a missing composite or a gate that
  cannot be measured renders as a blank that sorts to the bottom — never as a
  zero, never as a silently-priced number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .costs import CostModel
from .screen import _book_rows, _tier_prices, setup_params
from .signals.atr import atr_last, risk_unit
from .signals.avwap import anchored_vwap_bands, classify_band
from .signals.levels import build_level_store, levels_near
from .signals.setup import GATE_NAMES, anchor_grid, evaluate_setups
from .store.db import Database
from .timeutil import ensure_utc, iso, parse_iso, utcnow
from .universe import BELOW, THIN, tier_badge

__all__ = [
    "Board",
    "TypeBrief",
    "build_board",
    "build_brief",
    "format_isk",
    "render_board",
    "render_brief",
    "watchlist_summary",
]

BOARD_SORTS = ("value", "strength", "change")


def format_isk(value: float | None, digits: int = 2) -> str:
    """Compact ISK: prices span twelve orders of magnitude, so 1.25B beats 1,250,000,000."""
    if value is None or not np.isfinite(value):
        return "UNKNOWN"
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"{value / 1e9:,.{digits}f}B"
    if magnitude >= 1e6:
        return f"{value / 1e6:,.{digits}f}M"
    return f"{value:,.{digits}f}"


@dataclass(slots=True)
class TypeBrief:
    """Everything the desk chart showed for one symbol, as data."""

    type_id: int
    type_name: str | None = None
    watched: bool = False
    tracked: bool | None = None
    tier: str | None = None
    median_unit_volume: float | None = None
    median_isk_value: float | None = None
    bars: int = 0
    last_bar: str | None = None
    close: float | None = None
    day_change_pct: float | None = None
    vwap: float | None = None
    sigma: float | None = None
    dip_sigma: float | None = None
    band_zone: str = "UNKNOWN"
    anchor_truncated: bool = False
    rrs: float | None = None
    participation: float | None = None
    atr: float | None = None
    risk_unit: float | None = None
    destruction_z: float | None = None
    is_setup: bool = False
    gates: dict[str, str] = field(default_factory=dict)
    nearby_levels: list[dict] = field(default_factory=list)
    tier_costs: list[dict] = field(default_factory=list)
    friction_pct: float | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    book_age_minutes: float | None = None
    freshness: str = "UNKNOWN"
    flags: list[str] = field(default_factory=list)
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "type_name": self.type_name,
            "watched": self.watched,
            "tracked": self.tracked,
            "tier": self.tier,
            "median_unit_volume": self.median_unit_volume,
            "median_isk_value": self.median_isk_value,
            "bars": self.bars,
            "last_bar": self.last_bar,
            "close": self.close,
            "day_change_pct": self.day_change_pct,
            "vwap": self.vwap,
            "sigma": self.sigma,
            "dip_sigma": self.dip_sigma,
            "band_zone": self.band_zone,
            "anchor_truncated": self.anchor_truncated,
            "rrs": self.rrs,
            "participation": self.participation,
            "atr": self.atr,
            "risk_unit": self.risk_unit,
            "destruction_z": self.destruction_z,
            "is_setup": self.is_setup,
            "gates": dict(self.gates),
            "nearby_levels": self.nearby_levels,
            "tier_costs": self.tier_costs,
            "friction_pct": self.friction_pct,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "book_age_minutes": self.book_age_minutes,
            "freshness": self.freshness,
            "flags": self.flags,
            "note": self.note,
        }


@dataclass(slots=True)
class Board:
    """The D1 cross-section: one row per type, blanks at the bottom."""

    generated_at: str
    region_id: int
    sort: str
    universe: int = 0
    measured: int = 0
    unknown_friction: int = 0
    setups: int = 0
    rows: list[dict] = field(default_factory=list)


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _book_state(config: Config, book: pd.DataFrame, type_id: int, now) -> tuple:
    """(sell_row, buy_row, sweep_ts, age_minutes, stale_reason) — the screen's read."""
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
    return sell_row, buy_row, sweep_ts, age_minutes, stale_reason


def _gate_states(last) -> tuple[dict[str, str], bool]:
    """PASS / FAIL / UNKNOWN per gate — tri-state, and UNKNOWN is not a FAIL."""
    states: dict[str, str] = {}
    for gate in GATE_NAMES:
        if bool(last[f"{gate}_unknown"]):
            states[gate] = "UNKNOWN"
        else:
            states[gate] = "PASS" if bool(last[gate]) else "FAIL"
    return states, bool(last["is_setup"])


def build_brief(
    config: Config,
    db: Database,
    frame: pd.DataFrame,
    composite_frame: pd.DataFrame | None,
    book: pd.DataFrame,
    type_id: int,
    *,
    region_id: int | None = None,
    anchor_dates=(),
    destruction_z: float | None = None,
    now=None,
) -> TypeBrief:
    """One type, fully read: bands, gates, levels, strength, and priced tiers."""
    now = ensure_utc(now or utcnow())
    region = region_id or config.esi.home_region_id
    type_row = db.type_by_id(int(type_id))
    brief = TypeBrief(
        type_id=int(type_id),
        type_name=type_row["name"] if type_row else None,
        destruction_z=destruction_z,
    )
    brief.watched = (
        db.conn.execute("SELECT 1 FROM watchlist WHERE type_id=?", (int(type_id),)).fetchone()
        is not None
    )
    universe_row = db.conn.execute(
        "SELECT tracked, tier, median_unit_volume, median_isk_value FROM universe"
        " WHERE type_id=? AND region_id=?",
        (int(type_id), region),
    ).fetchone()
    if universe_row is not None:
        brief.tracked = bool(universe_row["tracked"])
        brief.tier = universe_row["tier"]
        brief.median_unit_volume = _finite(universe_row["median_unit_volume"])
        brief.median_isk_value = _finite(universe_row["median_isk_value"])

    if frame is None or frame.empty:
        brief.note = (
            "no bars in the lake for this type — run "
            "`python -m evescreener ingest-history --type-id "
            f"{int(type_id)}` first"
        )
        return brief

    work = frame.sort_values("datetime").reset_index(drop=True)
    brief.bars = int(len(work))
    brief.last_bar = str(pd.Timestamp(work["datetime"].iloc[-1]).date())
    closes = pd.to_numeric(work["close"], errors="coerce")
    brief.close = _finite(closes.iloc[-1])
    previous = _finite(closes.iloc[-2]) if len(closes) > 1 else None
    if brief.close and previous:
        brief.day_change_pct = (brief.close / previous - 1.0) * 100.0

    params = setup_params(config)
    evaluated = evaluate_setups(work, composite_frame, params, anchor_dates=anchor_dates)
    if not evaluated.empty:
        last = evaluated.iloc[-1]
        brief.gates, brief.is_setup = _gate_states(last)
        brief.dip_sigma = _finite(last["dip_sigma"])
        brief.rrs = _finite(last["rrs"])
        brief.participation = _finite(last["participation"])

    anchors = anchor_grid(work, step_days=params.anchor_lookback_days, anchor_dates=anchor_dates)
    bands = anchored_vwap_bands(work, anchors[-1] if anchors else 0)
    brief.vwap = bands.vwap
    brief.sigma = bands.sigma
    brief.band_zone = classify_band(brief.close, bands)
    brief.anchor_truncated = bands.truncated

    brief.atr = atr_last(
        work,
        length=config.signals.atr_length,
        winsor_k=config.signals.atr_winsor_k,
        winsor_window=config.signals.atr_winsor_window,
    )
    brief.risk_unit = risk_unit(work, length=config.signals.atr_length)
    levels = build_level_store(
        work,
        atr20=brief.atr,
        round_steps=config.signals.round_number_levels_isk,
        anchor_dates=anchor_dates,
    )
    brief.nearby_levels = [
        {
            "price": level["price"],
            "kind": level["kind"],
            "position": level.get("position"),
            "strength": level.get("strength"),
            "conviction": level.get("conviction"),
        }
        for level in levels_near(levels, brief.close, brief.atr, min_strength=0.5)[:5]
    ]

    costs = CostModel.from_config(config)
    tiers = tuple(float(value) for value in config.costs.notional_tiers_isk)
    sell_row, buy_row, _sweep_ts, age_minutes, stale_reason = _book_state(
        config, book, int(type_id), now
    )
    brief.book_age_minutes = round(age_minutes, 1) if age_minutes is not None else None
    brief.freshness = "fresh" if sell_row is not None and not stale_reason else "stale"
    ask_prices = _tier_prices(sell_row, tiers)
    bid_prices = _tier_prices(buy_row, tiers)
    for tier in tiers:
        priced = costs.price_round_trip(
            notional_isk=tier,
            ask_walk_price=ask_prices.get(tier),
            ask_walk_qty=None,
            bid_walk_price=bid_prices.get(tier),
            reference_price=brief.close,
            stale_reason=stale_reason,
        )
        # Friction is what the round trip costs if the price never moves: the
        # negated immediate net. It is a COST, shown as a positive number.
        friction = -priced.net_edge_pct_taker if priced.net_edge_pct_taker is not None else None
        brief.tier_costs.append(
            {
                "notional_isk": tier,
                "breakeven_move_pct": priced.breakeven_move_pct,
                "friction_pct": friction,
                "fillable": priced.known,
                "reason": priced.unknown_reason,
            }
        )
        if tier == tiers[0]:
            brief.friction_pct = friction
            brief.entry_price = priced.entry_price
            brief.exit_price = priced.exit_price_taker

    if sell_row is not None:
        share = _finite(sell_row.get("top_order_volume_share"))
        if share is not None and share > config.screen.top_order_share_flag:
            brief.flags.append(f"one order holds {share:.0%} of the ask book")
        if bool(sell_row.get("partial_sweep")):
            brief.flags.append("priced from a partial sweep")
    if buy_row is not None:
        station = _finite(buy_row.get("station_volume_share"))
        if station is not None and station < 0.9:
            brief.flags.append(
                f"{1 - station:.0%} of bid depth sits in player structures — "
                "exit may be inaccessible without docking rights"
            )
    if brief.anchor_truncated:
        brief.flags.append("anchor predates the lake horizon (truncated)")
    return brief


def render_brief(brief: TypeBrief) -> str:
    """The chart, in text. Ends by saying what it is not."""
    name = brief.type_name or f"type {brief.type_id}"
    badges = []
    if brief.watched:
        badges.append("WATCHLIST")
    if brief.tier == THIN:
        # The THIN badge is louder than "tracked" because it is the one the
        # operator has to price in: this name is carried, but it is not one
        # you get out of at size (§11 D3, amended).
        badges.append("THIN")
    elif brief.tracked is True:
        badges.append("tracked")
    elif brief.tier == BELOW or brief.tracked is False:
        badges.append("BELOW FLOOR — not tradeable")
    header = f"# {name} (type {brief.type_id})" + (f" — {' · '.join(badges)}" if badges else "")
    lines = [header, ""]
    if brief.note:
        lines.append(brief.note)
        return "\n".join(lines)
    change = f"{brief.day_change_pct:+.2f}% on the day" if brief.day_change_pct is not None else ""
    lines.append(
        f"close {format_isk(brief.close)}"
        + (f" ({change})" if change else "")
        + f" · {brief.bars} bars, last {brief.last_bar}"
    )
    if brief.median_unit_volume is not None:
        floor_note = {
            THIN: " — THIN: carried and charted, excluded from FORGE",
            BELOW: " — below the absolute floor; direct lookup only, not tradeable",
        }.get(brief.tier or "", "")
        lines.append(f"median daily volume {brief.median_unit_volume:,.0f} units{floor_note}")
    if brief.median_isk_value is not None:
        lines.append(f"median daily turnover {format_isk(brief.median_isk_value)} ISK")
    lines.append("")
    dip = f"{brief.dip_sigma:+.2f}σ from value" if brief.dip_sigma is not None else "σ UNKNOWN"
    lines.append(
        f"anchored VWAP {format_isk(brief.vwap)} (σ {format_isk(brief.sigma)}) · "
        f"zone {brief.band_zone} · {dip}"
    )
    gates = " · ".join(f"{gate} {state}" for gate, state in brief.gates.items())
    verdict = "SETUP" if brief.is_setup else "not a setup"
    lines.append(f"gates: {gates or 'UNKNOWN'} → **{verdict}**")
    rrs = f"{brief.rrs:+.2f}" if brief.rrs is not None else "UNKNOWN"
    part = f"{brief.participation:.2f}x" if brief.participation is not None else "UNKNOWN"
    lines.append(f"RRS {rrs} vs the Forge Composite · participation {part} of its 20-day baseline")
    lines.append(f"ATR {format_isk(brief.atr)} · risk unit {format_isk(brief.risk_unit)}")
    if brief.destruction_z is not None:
        lines.append(
            f"destruction_z {brief.destruction_z:+.2f} "
            "(annotation only — the lead-lag claim was tested and not supported)"
        )
    if brief.nearby_levels:
        lines.append("")
        lines.append("levels near the close:")
        for level in brief.nearby_levels:
            conviction = level.get("conviction")
            lines.append(
                f"  {format_isk(level['price'])} {level['kind']} ({level.get('position')})"
                + (f", conviction {conviction:.2f}" if conviction is not None else "")
            )
    lines.append("")
    age = f"{brief.book_age_minutes:.0f} min old" if brief.book_age_minutes is not None else ""
    lines.append(f"book {brief.freshness}" + (f", {age}" if age else "") + ":")
    lines.append(
        f"  taker entry {format_isk(brief.entry_price)} → taker exit {format_isk(brief.exit_price)}"
    )
    for tier in brief.tier_costs:
        label = f"{tier['notional_isk'] / 1e9:.2f}B"
        if tier["fillable"]:
            lines.append(
                f"  {label}: breakeven {tier['breakeven_move_pct']:.2f}% · "
                f"round-trip friction {tier['friction_pct']:.2f}%"
            )
        else:
            lines.append(f"  {label}: UNKNOWN ({tier['reason']})")
    for flag in brief.flags:
        lines.append(f"⚠ {flag}")
    lines.append("")
    lines.append(
        "_This is an observation, not a pick: friction is what the round trip costs "
        "before the thesis earns anything._"
    )
    return "\n".join(lines)


def build_board(
    config: Config,
    db: Database,
    bars: pd.DataFrame,
    composite_frame: pd.DataFrame | None,
    book: pd.DataFrame,
    *,
    watch_ids=frozenset(),
    anchor_dates=(),
    region_id: int | None = None,
    now=None,
    top: int = 20,
    sort: str = "value",
) -> Board:
    """One row per measurable type; costs printed, never used to hide a row."""
    now = ensure_utc(now or utcnow())
    if sort not in BOARD_SORTS:
        raise ValueError(f"sort must be one of {BOARD_SORTS}, not {sort!r}")
    board = Board(
        generated_at=iso(now),
        region_id=region_id or config.esi.home_region_id,
        sort=sort,
    )
    if bars is None or bars.empty:
        return board
    params = setup_params(config)
    costs = CostModel.from_config(config)
    tier0 = float(config.costs.notional_tiers_isk[0])
    groups = dict(tuple(bars.groupby("type_id", sort=True)))
    board.universe = len(groups)
    names = db.type_names(list(groups))
    tiers = {
        int(row["type_id"]): row["tier"]
        for row in db.conn.execute(
            "SELECT type_id, tier FROM universe WHERE region_id=?", (board.region_id,)
        )
    }
    rows: list[dict] = []
    for type_id, group in groups.items():
        work = group.sort_values("datetime").reset_index(drop=True)
        if len(work) < params.min_bars:
            continue
        evaluated = evaluate_setups(work, composite_frame, params, anchor_dates=anchor_dates)
        if evaluated.empty:
            continue
        last = evaluated.iloc[-1]
        close = _finite(last["close"])
        previous = _finite(evaluated["close"].iloc[-2]) if len(evaluated) > 1 else None
        change = (close / previous - 1.0) * 100.0 if close and previous else None

        sell_row, buy_row, _ts, _age, stale_reason = _book_state(config, book, int(type_id), now)
        priced = costs.price_round_trip(
            notional_isk=tier0,
            ask_walk_price=_tier_prices(sell_row, (tier0,)).get(tier0),
            ask_walk_qty=None,
            bid_walk_price=_tier_prices(buy_row, (tier0,)).get(tier0),
            reference_price=close,
            stale_reason=stale_reason,
        )
        friction = -priced.net_edge_pct_taker if priced.net_edge_pct_taker is not None else None
        if friction is None:
            board.unknown_friction += 1
        if bool(last["is_setup"]):
            board.setups += 1
        rows.append(
            {
                "type_id": int(type_id),
                "type_name": names.get(int(type_id)),
                "watched": int(type_id) in watch_ids,
                "close": close,
                "day_change_pct": change,
                "dip_sigma": _finite(last["dip_sigma"]),
                "rrs": _finite(last["rrs"]),
                "participation": _finite(last["participation"]),
                "friction_pct": friction,
                "is_setup": bool(last["is_setup"]),
                "tier": tiers.get(int(type_id)),
            }
        )
    board.measured = len(rows)

    def key(row: dict):
        # Blanks sort to the BOTTOM whichever way the board is sorted — a blank
        # is a type the board could not measure, not a zero (source-repo idiom).
        if sort == "value":
            value = row["dip_sigma"]
            return (value is None, value if value is not None else 0.0)
        if sort == "strength":
            value = row["rrs"]
            return (value is None, -value if value is not None else 0.0)
        value = row["day_change_pct"]
        return (value is None, -value if value is not None else 0.0)

    rows.sort(key=key)
    board.rows = rows[: max(1, int(top))]
    return board


def render_board(board: Board) -> str:
    """Fixed-width table with honest counts under it."""
    sorted_by = {
        "value": "deepest below anchored value first",
        "strength": "strongest RRS first",
        "change": "largest day move first",
    }[board.sort]
    lines = [
        f"# Observation board — {board.generated_at[:16]}Z · region {board.region_id}",
        f"sorted: {sorted_by}. This board is observation, not opportunity: friction is",
        "printed beside every row and never used to hide one (plan.md §18).",
        "",
        f"   {'name':<30} {'close':>10} {'Δ1d%':>7} {'dipσ':>7} {'RRS':>7} "
        f"{'part':>6} {'frict%':>7} {'thin':>5}  setup",
    ]

    def cell(value, spec: str) -> str:
        return format(value, spec) if value is not None else "—"

    for row in board.rows:
        name = (row["type_name"] or f"type {row['type_id']}")[:30]
        marker = "W" if row["watched"] else " "
        lines.append(
            f" {marker} {name:<30} {format_isk(row['close']):>10} "
            f"{cell(row['day_change_pct'], '+7.2f'):>7} {cell(row['dip_sigma'], '+7.2f'):>7} "
            f"{cell(row['rrs'], '+7.2f'):>7} {cell(row['participation'], '6.2f'):>6} "
            f"{cell(row['friction_pct'], '7.2f'):>7} {tier_badge(row.get('tier')):>5}  "
            f"{'✓' if row['is_setup'] else '·'}"
        )
    if not board.rows:
        lines.append("  (nothing measurable — is the lake ingested and the census run?)")
    lines.append("")
    lines.append(
        f"{len(board.rows)} of {board.measured} measured shown ({board.universe} in scope) · "
        f"{board.unknown_friction} friction UNKNOWN (stale or thin book) · "
        f"{board.setups} setup(s) today"
    )
    return "\n".join(lines)


def watchlist_summary(
    config: Config,
    db: Database,
    bars: pd.DataFrame,
    composite_frame: pd.DataFrame | None,
    book: pd.DataFrame,
    *,
    anchor_dates=(),
    region_id: int | None = None,
    now=None,
) -> list[dict]:
    """One compact row per watchlist name — EVERY name, resolved or not.

    The watchlist is the operator's own list, so it renders unconditionally:
    a name that cannot be measured says why, and never disappears (§11 D4).
    """
    rows: list[dict] = []
    entries = list(db.conn.execute("SELECT name, type_id, note FROM watchlist ORDER BY name"))
    for entry in entries:
        if entry["type_id"] is None:
            rows.append({"name": entry["name"], "unresolved": True})
            continue
        type_id = int(entry["type_id"])
        frame = (
            bars[bars["type_id"] == type_id]
            if bars is not None and not bars.empty
            else pd.DataFrame()
        )
        brief = build_brief(
            config,
            db,
            frame,
            composite_frame,
            book,
            type_id,
            region_id=region_id,
            anchor_dates=anchor_dates,
            now=now,
        )
        rows.append(
            {
                "name": brief.type_name or entry["name"],
                "type_id": type_id,
                "bars": brief.bars,
                "close": brief.close,
                "day_change_pct": brief.day_change_pct,
                "dip_sigma": brief.dip_sigma,
                "band_zone": brief.band_zone,
                "rrs": brief.rrs,
                "friction_pct": brief.friction_pct,
                "freshness": brief.freshness,
                "is_setup": brief.is_setup,
            }
        )
    return rows
