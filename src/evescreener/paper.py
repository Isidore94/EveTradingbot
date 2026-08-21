"""Paper trading — plan.md §12, the centrepiece.

The backtest justifies *running the experiment*; this **is** the experiment.
Everything here is built around one rule: a paper record that flatters itself
is worse than no record, because it manufactures the confidence to risk real
ISK.

So the fills are real fills, or there are no fills:

* **taker** entries are **ask-walk** fills at the declared notional, from a
  live book sweep — not best ask, not mid, never the daily close, and taker
  exits are **bid-walk** fills × (1 − sales tax). This is the frozen default
  and the only fill a snapshot can *prove*;
* **maker** entries and exits (plan.md §12.2, amended 2026-08-21 on operator
  authority) post one tick in front of the executable quote and pay the broker
  fee on both legs. A snapshot proves the price was postable and nothing more:
  queue position and adverse selection are unpriced, so every maker record
  carries `fill_assumed: true`, and `paper report` scores each population
  separately under the same frozen §12.4 rule — printed beside the frozen
  whole-sample verdict, never silently merged into it;
* **mid is still not a fill.** No order type in EVE executes at the midpoint,
  and with a 98.8% median spread (§17) half of it is not a rounding error —
  it is the entire result. The two models above are the two things an operator
  can actually do;
* a book older than `paper.stale_book_minutes` **refuses the fill**. The
  position is not opened and the refusal is counted. **Nothing is ever priced
  off history.**
* there are no retro-entries — an open is stamped with the sweep that priced
  it;
* a notional above 10% of 30-day median daily ISK turnover is flagged
  `self_impact`: still recorded, but labelled as a size the market notices.

The ledger is append-only. A correction is a new record, never an edit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .config import Config
from .costs import CostModel
from .paths import append_jsonl, read_jsonl
from .reasons import DISLIKE, LIKE, PASS_ACTIONS, ReasonError, normalise_tags
from .store.lake import EXECUTABLE_COLUMNS
from .timeutil import ensure_utc, iso, parse_iso, utcnow

__all__ = [
    "FILL_MODELS",
    "PaperLedger",
    "PaperReport",
    "Refusal",
    "book_quote",
    "render_report",
]

#: The two things an operator can actually do. `taker` crosses the spread and
#: is the frozen default; `maker` posts and waits (plan.md §12.2, amended
#: 2026-08-21). There is deliberately no `mid`: no EVE order type fills there.
FILL_MODELS = ("taker", "maker")

MAKER_ASSUMED = (
    "maker fill ASSUMED: a snapshot proves this price was postable, never that "
    "anyone traded into it. Queue position, undercutting and adverse selection "
    "are unpriced. Scored as its own population, never blended with taker fills"
)

# Every recorded decision — taken or passed — must say why, in the committed
# vocabulary (§19 Amendment 3). No tags, no record, in either direction.
NO_LIKE_TAGS = (
    "an opening needs at least one 'why I like it' tag from config/reasons.jsonl. "
    "A trade whose reason is not recorded is a trade the learning loop can never "
    "attribute, in either direction"
)
NO_DISLIKE_TAGS = (
    "a pass needs at least one 'why I don't like it' tag from config/reasons.jsonl. "
    "A pass with its reasons is a recorded decision; a pass without them is a "
    "dismissal, and half the evidence is thrown away"
)
NO_SETUP_TAG = (
    "an opening needs a setup tag — the name of the setup that fired, or "
    "'discretionary'. Without it the learning loop cannot say which setups earn"
)


class Refusal(RuntimeError):
    """The system declined to price something. UNKNOWN, not an approximation."""


def _clean_tags(tags, vocabulary, direction: str) -> tuple[str, ...]:
    """Validate tags against the vocabulary when one is supplied.

    With no vocabulary the tags are still cleaned and de-duplicated but not
    checked — a caller with no `config/reasons.jsonl` can still record a
    decision, it just cannot be told it typed a tag wrong.
    """
    from .reasons import ReasonVocabulary

    return normalise_tags(tags, vocabulary or ReasonVocabulary(reasons=()), direction)


def _finite(value) -> bool:
    """True only for a real, finite number. `None`, NaN and text are UNKNOWN."""
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


@dataclass(slots=True)
class BookQuote:
    """One side's price for one intent, with its own freshness attached.

    `fill_model` says how the price was obtained, because the two are not the
    same kind of evidence. A `taker` price is what the snapshot proves you
    could have paid by consuming the book right then. A `maker` price is only
    what you could have *posted*: whether anyone would have traded into it is
    unknowable from a snapshot, so `assumed` is True and every consumer keeps
    the two populations apart (plan.md §12.2, amended 2026-08-21).
    """

    price: float | None
    quantity: float | None
    sweep_ts: str | None
    age_minutes: float | None
    stale: bool
    reason: str | None = None
    fill_model: str = "taker"
    location_id: int | None = None
    is_structure: bool | None = None

    @property
    def assumed(self) -> bool:
        return self.fill_model == "maker"


def book_quote(
    book: pd.DataFrame,
    *,
    type_id: int,
    side: str,
    tier_index: int,
    now: datetime | None = None,
    stale_after_minutes: int = 60,
    fill_model: str = "taker",
    tick: float = 0.01,
) -> BookQuote:
    """Price for one book side, or an explicit refusal.

    `side` names the side of the book being **read**, and `fill_model` says
    what is done with it:

    * `taker` — the depth-walk VWAP at `tier_index`, i.e. what consuming that
      side at the declared notional would have cost. This is the frozen
      default and the only price the snapshot guarantees.
    * `maker` — one `tick` in front of the executable quote resting on that
      side: `exec_price + tick` when joining the bid queue, `exec_price - tick`
      when joining the ask queue. Size does not move this price, so the tier
      is not consulted; what it costs instead is queue position, which a
      snapshot cannot price. `quantity` therefore carries the volume already
      resting **ahead** of you, not a fill.

    Every failure mode returns a *reason*: no sweep, wrong side, too thin, too
    old, pre-R1 contract. None of them returns a price.
    """
    if fill_model not in FILL_MODELS:
        raise ValueError(f"fill_model must be one of {FILL_MODELS}, got {fill_model!r}")
    now = ensure_utc(now or utcnow())
    if book is None or book.empty:
        return BookQuote(None, None, None, None, True, "no book sweep available", fill_model)
    missing = [column for column in EXECUTABLE_COLUMNS if column not in book.columns]
    if missing:
        # A snapshot written before the executable-quote contract cannot say
        # where its quotes rested, and `load_validated_book()` already refuses
        # it. Refusing here too is what stops a caller reaching the lake
        # directly and pricing off it anyway (§22 S2b).
        return BookQuote(
            None,
            None,
            None,
            None,
            True,
            "book predates the executable-quote contract "
            f"({', '.join(missing)} absent) — re-run sweep-books",
            fill_model,
        )
    rows = book[(book["type_id"] == int(type_id)) & (book["side"] == side)]
    if rows.empty:
        return BookQuote(
            None, None, None, None, True, f"no {side} side in the last sweep", fill_model
        )
    row = rows.sort_values("sweep_ts").iloc[-1]
    sweep_ts = parse_iso(str(row["sweep_ts"])) or ensure_utc(pd.Timestamp(row["sweep_ts"]))
    age = (now - sweep_ts).total_seconds() / 60.0
    stale = age > stale_after_minutes
    location_id = int(row["exec_location_id"]) if _finite(row.get("exec_location_id")) else None
    is_structure = (
        bool(row["exec_is_structure"]) if row.get("exec_is_structure") is not None else None
    )

    if fill_model == "maker":
        resting = row.get("exec_price")
        if not _finite(resting):
            return BookQuote(
                None,
                None,
                iso(sweep_ts),
                age,
                stale,
                f"no executable {side}-side quote to post in front of",
                fill_model,
                location_id,
                is_structure,
            )
        # One tick towards the middle: the cheapest price that puts a new
        # order at the front of that queue.
        price = float(resting) + (float(tick) if side == "buy" else -float(tick))
        # What is already resting there is what you queue behind, not a fill.
        quantity = float(row["exec_volume"]) if _finite(row.get("exec_volume")) else None
        if price <= 0:
            return BookQuote(
                None,
                None,
                iso(sweep_ts),
                age,
                stale,
                f"a maker {side} one tick from {float(resting):,.2f} is not a positive price",
                fill_model,
                location_id,
                is_structure,
            )
    else:
        raw_price = row.get(f"depth_fill_price_{tier_index}")
        raw_qty = row.get(f"depth_fill_qty_{tier_index}")
        if not _finite(raw_price):
            return BookQuote(
                None,
                None,
                iso(sweep_ts),
                age,
                stale,
                f"book cannot fill this notional on the {side} side",
                fill_model,
                location_id,
                is_structure,
            )
        price = float(raw_price)
        quantity = float(raw_qty) if _finite(raw_qty) else None

    if stale:
        return BookQuote(
            None,
            None,
            iso(sweep_ts),
            age,
            True,
            f"book is {age:.0f} min old (> {stale_after_minutes}); "
            "refusing the fill rather than pricing off history",
            fill_model,
            location_id,
            is_structure,
        )
    return BookQuote(
        float(price),
        quantity,
        iso(sweep_ts),
        age,
        False,
        None,
        fill_model,
        location_id,
        is_structure,
    )


@dataclass(slots=True)
class PaperReport:
    generated_at: str
    refused: int = 0
    refusal_reasons: dict = field(default_factory=dict)
    open_positions: list[dict] = field(default_factory=list)
    closed: list[dict] = field(default_factory=list)
    cumulative_net_isk: float = 0.0
    cumulative_net_pct: float | None = None
    win_rate: float | None = None
    wilson_lb: float | None = None
    breakeven_win_rate: float | None = None
    r_distribution: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)
    fill_accuracy: dict = field(default_factory=dict)
    self_impact_flags: int = 0
    priced_from: dict = field(default_factory=dict)
    #: The frozen §12.4 rule applied to each fill-model population on its own.
    #: A taker result and a maker result answer different questions, and an
    #: average of the two answers neither (§12.2, amended 2026-08-21).
    by_fill_model: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "refused": self.refused,
            "refusal_reasons": self.refusal_reasons,
            "open_positions": self.open_positions,
            "closed_count": len(self.closed),
            "cumulative_net_isk": self.cumulative_net_isk,
            "cumulative_net_pct": self.cumulative_net_pct,
            "win_rate": self.win_rate,
            "wilson_lb": self.wilson_lb,
            "breakeven_win_rate": self.breakeven_win_rate,
            "r_distribution": self.r_distribution,
            "verdict": self.verdict,
            "fill_accuracy": self.fill_accuracy,
            "self_impact_flags": self.self_impact_flags,
            "priced_from": self.priced_from,
            "by_fill_model": self.by_fill_model,
        }


class PaperLedger:
    """Append-only paper-trading ledger over `data/streams/paper.jsonl`."""

    def __init__(self, path: Path, config: Config) -> None:
        self.path = path
        self.config = config
        self.costs = CostModel.from_config(config)

    # -- reading -----------------------------------------------------------
    def records(self) -> list[dict]:
        return read_jsonl(self.path)

    def positions(self) -> dict[str, dict]:
        """Reconstruct positions by replaying the ledger. Nothing is mutated.

        A duplicate `open` id is disambiguated on read rather than dropped.
        Ids used to be `{type_id}-{second}`, so two entries in the same type in
        the same second produced the same key and the second one **silently
        replaced the first**: a position that existed on disk, was never
        closeable, and never appeared in the tally. New ids carry a sequence
        suffix so it cannot recur; this keeps every historical row reachable.
        """
        positions: dict[str, dict] = {}
        for record in self.records():
            kind = record.get("event")
            position_id = record.get("position_id")
            if kind == "open":
                if position_id in positions:
                    suffix = 2
                    while f"{position_id}#{suffix}" in positions:
                        suffix += 1
                    position_id = f"{position_id}#{suffix}"
                    record = {**record, "position_id": position_id, "duplicate_id": True}
                positions[position_id] = {**record, "marks": [], "close": None, "real_fills": []}
            elif position_id in positions:
                if kind == "mark":
                    positions[position_id]["marks"].append(record)
                elif kind == "close":
                    positions[position_id]["close"] = record
                elif kind == "real_fill":
                    positions[position_id]["real_fills"].append(record)
        return positions

    def refusals(self) -> list[dict]:
        return [record for record in self.records() if record.get("event") == "refused"]

    # -- writing -----------------------------------------------------------
    def _append(self, record: dict) -> dict:
        append_jsonl(self.path, [record])
        return record

    def _refuse(self, reason: str, context: dict) -> dict:
        record = {
            "event": "refused",
            "at": iso(utcnow()),
            "reason": reason,
            **context,
        }
        self._append(record)
        raise Refusal(reason)

    def open_position(
        self,
        *,
        type_id: int,
        type_name: str | None,
        notional_isk: float,
        book: pd.DataFrame,
        thesis: str,
        setup_tag: str,
        like_tags=(),
        reason_text: str = "",
        stop_price: float | None = None,
        target_price: float | None = None,
        median_daily_turnover: float | None = None,
        now: datetime | None = None,
        vocabulary=None,
        fill_model: str | None = None,
    ) -> dict:
        """Price and record one entry, or refuse it.

        A refusal is written to the ledger too: how often the system declines
        to price something is a headline number, not an omission (§12.4).

        `fill_model` is `taker` (walk the asks, guaranteed by the snapshot) or
        `maker` (post one tick above the executable bid, pay the broker fee,
        and wait). A maker entry is stamped `fill_assumed` because the book
        cannot say whether anyone would have sold into it (§12.2, amended
        2026-08-21).

        `setup_tag` and at least one `like_tags` entry are **required**
        (§19 Amendment 3). The refusal for a missing reason is recorded like
        any other refusal — a decision the operator started and did not
        qualify is itself information.
        """
        now = ensure_utc(now or utcnow())
        fill_model = str(fill_model or self.config.paper.default_fill_model)
        tiers = list(self.config.costs.notional_tiers_isk)
        tier_index = _tier_index(tiers, notional_isk)
        context = {
            "type_id": int(type_id),
            "type_name": type_name,
            "notional_isk": float(notional_isk),
            "action": "open",
            "fill_model": fill_model,
        }
        if fill_model not in FILL_MODELS:
            self._refuse(
                f"fill model must be one of {list(FILL_MODELS)}, got {fill_model!r}. "
                "There is no mid fill: no EVE order type executes at the midpoint",
                context,
            )
        if not str(thesis).strip():
            self._refuse("an opening needs a thesis sentence you can argue with", context)
        if not str(setup_tag).strip():
            self._refuse(NO_SETUP_TAG, context)
        likes = _clean_tags(like_tags, vocabulary, LIKE)
        if not likes:
            self._refuse(NO_LIKE_TAGS, context)
        if tier_index is None:
            self._refuse(
                f"notional {notional_isk:,.0f} ISK is not one of the configured tiers "
                f"{[f'{tier:,.0f}' for tier in tiers]}; the depth walk only measures those",
                context,
            )
        # A taker buy consumes the ask side; a maker buy joins the bid queue.
        # `side` always names the side of the book being read.
        entry_side = "sell" if fill_model == "taker" else "buy"
        entry_quote = self._quote(book, type_id, entry_side, tier_index, now, fill_model)
        if entry_quote.price is None:
            self._refuse(
                entry_quote.reason or f"no {fill_model} entry price",
                {**context, "sweep_ts": entry_quote.sweep_ts},
            )
        # The other side is recorded for reference at every entry, so the
        # spread that was standing when the decision was made stays auditable.
        ask_reference = self._quote(book, type_id, "sell", tier_index, now, "taker")
        bid_reference = self._quote(book, type_id, "buy", tier_index, now, "taker")
        # Measured 2026-08-20: structure-resident depth is entirely on the BID
        # side in The Forge, so it is the exit that may be inaccessible. Record
        # the exposure at entry so a later exit can be judged against it.
        bid_rows = (
            book[(book["type_id"] == int(type_id)) & (book["side"] == "buy")]
            if book is not None and not book.empty
            else None
        )
        bid_station_share = (
            float(bid_rows.iloc[-1]["station_volume_share"])
            if bid_rows is not None
            and not bid_rows.empty
            and bid_rows.iloc[-1]["station_volume_share"] is not None
            else None
        )

        # A posted buy pays the broker fee; a taker fill does not. The
        # effective price is what one unit really costs, fee included, so
        # `units` and every downstream R are computed on money actually spent.
        # Broker fee is per-station (§21 R4), and the executable quote knows
        # which station it rested at.
        broker_pct = self.costs.broker_fee_at(entry_quote.location_id)
        effective_entry = (
            entry_quote.price
            if fill_model == "taker"
            else entry_quote.price * (1.0 + broker_pct / 100.0)
        )
        units = notional_isk / effective_entry
        self_impact = bool(
            median_daily_turnover
            and notional_isk > median_daily_turnover * self.config.paper.self_impact_turnover_share
        )
        # Advisory only: never realized, never netted into a result.
        maker_exit_advisory = (
            self.costs.sell_proceeds(target_price, maker=True) if target_price else None
        )
        record = {
            "event": "open",
            "position_id": self._next_position_id(type_id, now),
            "at": iso(now),
            "type_id": int(type_id),
            "type_name": type_name,
            "side": "long",
            "notional_isk": float(notional_isk),
            "tier_index": tier_index,
            "fill_model": fill_model,
            "fill_assumed": entry_quote.assumed,
            "fill_assumption": MAKER_ASSUMED if entry_quote.assumed else None,
            "entry_quote_price": entry_quote.price,
            "entry_effective_price": effective_entry,
            "entry_units": units,
            "entry_walk_qty": entry_quote.quantity if fill_model == "taker" else None,
            "queue_ahead_units": entry_quote.quantity if fill_model == "maker" else None,
            "exec_location_id": entry_quote.location_id,
            "exec_is_structure": entry_quote.is_structure,
            "book_sweep_ts": entry_quote.sweep_ts,
            "book_age_minutes": round(entry_quote.age_minutes or 0.0, 2),
            "ask_at_entry": ask_reference.price,
            "bid_at_entry": bid_reference.price,
            "bid_station_volume_share": bid_station_share,
            "stop_price": stop_price,
            "target_price": target_price,
            "maker_exit_advisory_net": maker_exit_advisory,
            "sales_tax_pct": self.costs.sales_tax_pct,
            "broker_fee_pct": broker_pct,
            "breakeven_move_pct": self._breakeven_pct(
                fill_model, effective_entry, ask_reference.price, bid_reference.price, broker_pct
            ),
            "self_impact": self_impact,
            "median_daily_turnover": median_daily_turnover,
            "thesis": thesis,
            "setup_tag": str(setup_tag).strip(),
            "like_tags": list(likes),
            "reason_text": str(reason_text),
            "planned_r": _planned_r(
                effective_entry, stop_price, target_price, self.costs, maker=fill_model == "maker"
            ),
        }
        return self._append(record)

    def _next_position_id(self, type_id: int, now: datetime) -> str:
        """A key unique within the ledger, derived only from what is on disk.

        No clock beyond `now` and no randomness, so a replay of the same file
        produces the same ids. The sequence suffix is what stops two entries in
        one second from colliding — the failure that used to lose a position.
        """
        stem = f"{int(type_id)}-{now.strftime('%Y%m%dT%H%M%S')}"
        taken = {
            str(record.get("position_id"))
            for record in self.records()
            if record.get("event") == "open"
        }
        if stem not in taken:
            return stem
        suffix = 2
        while f"{stem}#{suffix}" in taken:
            suffix += 1
        return f"{stem}#{suffix}"

    # -- pricing helpers ---------------------------------------------------
    def _exit_proceeds(self, fill_model: str, gross: float | None, broker_pct: float):
        """ISK received per unit. Tax always; the broker fee on a posted exit."""
        if gross is None:
            return None
        rate = self.costs.sales_tax_pct + (broker_pct if fill_model == "maker" else 0.0)
        return float(gross) * (1.0 - rate / 100.0)

    def _quote(self, book, type_id, side, tier_index, now, fill_model) -> BookQuote:
        """One `book_quote` with this ledger's staleness budget and tick."""
        return book_quote(
            book,
            type_id=int(type_id),
            side=side,
            tier_index=tier_index,
            now=now,
            stale_after_minutes=self.config.paper.stale_book_minutes,
            fill_model=fill_model,
            tick=self.config.paper.maker_tick_isk,
        )

    def _breakeven_pct(self, fill_model, entry, ask, bid, broker_pct) -> float | None:
        """How far price must move for this round trip to clear its own costs.

        The two models pay for different things and the number has to say so.
        A taker crosses the spread twice and pays tax; a maker pays the broker
        fee twice plus tax but is *paid* the spread — which is why a maker
        breakeven can be negative, and why that number means "only if both
        legs actually fill".
        """
        if not entry or entry <= 0:
            return None
        if fill_model == "taker":
            reference = (ask + bid) / 2.0 if ask and bid else None
            return self.costs.breakeven_move_pct(
                entry_price=entry, exit_price=bid, reference_price=reference
            )
        # Maker: the exit rests one tick inside the executable ask.
        if not ask or ask <= 0:
            return None
        exit_quote = ask - self.config.paper.maker_tick_isk
        reference = (ask + bid) / 2.0 if bid else ask
        if exit_quote <= 0 or not reference:
            return None
        fee = (self.costs.sales_tax_pct + broker_pct) / 100.0
        if fee >= 1.0:
            return None
        required_exit = entry / (1.0 - fee)
        return (required_exit / exit_quote - 1.0) * 100.0

    def record_pass(
        self,
        *,
        type_id: int,
        type_name: str | None,
        action: str,
        dislike_tags=(),
        reason_text: str = "",
        setup_tag: str | None = None,
        close: float | None = None,
        now: datetime | None = None,
        vocabulary=None,
    ) -> dict:
        """Record a decision NOT to take something. Same rigour as an opening.

        `action` is `not_today` (clears the name from today's queue only —
        it NEVER touches the watchlist, §11 D4) or `bad_signal` (the setup
        itself misfired). The forward outcome of this pass is measured later
        on the backtest's horizons and cost realism, which is what lets the
        learning loop say whether the reason was a good one.
        """
        now = ensure_utc(now or utcnow())
        context = {
            "type_id": int(type_id),
            "type_name": type_name,
            "action": action,
            "attempted_dislike_tags": [str(tag) for tag in (dislike_tags or ())],
        }
        # §19.4: the refusal itself goes in the ledger. Validation used to
        # raise BEFORE `_refuse`, so an unknown tag or a bad action left no
        # record at all — the one class of decision the ledger silently lost
        # was the one made wrongly (§22 S7).
        if action not in PASS_ACTIONS:
            self._refuse(f"pass action must be one of {PASS_ACTIONS}, got {action!r}", context)
        try:
            dislikes = _clean_tags(dislike_tags, vocabulary, DISLIKE)
        except ReasonError as exc:
            self._refuse(str(exc), context)
        if not dislikes:
            self._refuse(NO_DISLIKE_TAGS, context)
        return self._append(
            {
                "event": "pass",
                "at": iso(now),
                "type_id": int(type_id),
                "type_name": type_name,
                "action": action,
                "setup_tag": setup_tag,
                "dislike_tags": list(dislikes),
                "reason_text": str(reason_text),
                "close_at_pass": close,
            }
        )

    def passes(self) -> list[dict]:
        """Every recorded pass. The other half of the decision record."""
        return [record for record in self.records() if record.get("event") == "pass"]

    def mark(self, *, book: pd.DataFrame, now: datetime | None = None) -> list[dict]:
        """Daily mark-to-market. Every mark carries its staleness stamp.

        A position is marked on **its own** fill model, so an unrealized
        number is measured on the same basis the trade will close on — and the
        taker (liquidation) mark is recorded beside it, always. For a maker
        position those two differ by the whole spread, and a reader is entitled
        to both: one is what the plan says it is worth, the other is what
        walking out today would actually pay.
        """
        now = ensure_utc(now or utcnow())
        marks: list[dict] = []
        for position_id, position in self.positions().items():
            if position.get("close"):
                continue
            model = str(position.get("fill_model") or "taker")
            exit_side = "buy" if model == "taker" else "sell"
            quote = self._quote(
                book, position["type_id"], exit_side, position["tier_index"], now, model
            )
            liquidation = (
                quote
                if model == "taker"
                else self._quote(
                    book, position["type_id"], "buy", position["tier_index"], now, "taker"
                )
            )
            broker_pct = self.costs.broker_fee_at(quote.location_id)
            net = self._exit_proceeds(model, quote.price, broker_pct)
            liquidation_net = (
                self.costs.sell_proceeds(liquidation.price, maker=False)
                if liquidation.price is not None
                else None
            )
            entry = position["entry_effective_price"]
            units = position["entry_units"]
            marks.append(
                self._append(
                    {
                        "event": "mark",
                        "position_id": position_id,
                        "at": iso(now),
                        "type_id": position["type_id"],
                        "fill_model": model,
                        "mark_price": quote.price,
                        "mark_net_price": net,
                        "unrealized_net_isk": ((net - entry) * units if net is not None else None),
                        "liquidation_net_price": liquidation_net,
                        "liquidation_net_isk": (
                            (liquidation_net - entry) * units
                            if liquidation_net is not None
                            else None
                        ),
                        "book_sweep_ts": quote.sweep_ts,
                        "book_age_minutes": round(quote.age_minutes, 2)
                        if quote.age_minutes is not None
                        else None,
                        "stale": quote.stale,
                        "unknown_reason": quote.reason,
                    }
                )
            )
        return marks

    def close_position(
        self,
        *,
        position_id: str,
        book: pd.DataFrame,
        now: datetime | None = None,
        note: str = "",
        actual_price: float | None = None,
        fill_model: str | None = None,
    ) -> dict:
        """Exit on the position's own fill model, or a refusal. Never off history.

        A `taker` position exits on the bid walk net of sales tax; a `maker`
        position exits one tick inside the executable ask, net of tax **and**
        the broker fee, and stays flagged `fill_assumed`. `fill_model`
        overrides the position's own only when the operator says the exit
        really went the other way — a maker entry that had to be dumped into
        the bid is a taker exit, and recording it as anything else would
        flatter the record.

        `actual_price` records a close the operator **really made**, at a gross
        unit price he actually received. That is the only way to close a
        position whose book can no longer price it — and it is real evidence,
        not a substitute for a missing measurement. Fees still apply; the
        operator supplies the price, not the arithmetic.
        """
        now = ensure_utc(now or utcnow())
        positions = self.positions()
        position = positions.get(position_id)
        context = {"position_id": position_id, "action": "close"}
        if position is None:
            self._refuse(f"unknown position {position_id!r}", context)
        if position.get("close"):
            self._refuse(f"position {position_id!r} is already closed", context)
        model = str(fill_model or position.get("fill_model") or "taker")
        if model not in FILL_MODELS:
            self._refuse(f"fill model must be one of {list(FILL_MODELS)}, got {model!r}", context)
        context["fill_model"] = model
        if actual_price is not None:
            if actual_price <= 0:
                self._refuse("an actual close price must be positive", context)
            bid = BookQuote(
                price=float(actual_price),
                quantity=position["entry_units"],
                sweep_ts=None,
                age_minutes=None,
                stale=False,
                reason=None,
                fill_model=model,
                location_id=position.get("exec_location_id"),
            )
        else:
            # A taker exit consumes the bid side; a maker exit joins the ask
            # queue one tick inside the executable ask.
            exit_side = "buy" if model == "taker" else "sell"
            bid = self._quote(
                book, position["type_id"], exit_side, position["tier_index"], now, model
            )
        if bid.price is None:
            self._refuse(
                bid.reason or f"no {model} exit price", {**context, "sweep_ts": bid.sweep_ts}
            )
        broker_pct = self.costs.broker_fee_at(bid.location_id)
        exit_net = self._exit_proceeds(model, bid.price, broker_pct)
        entry = position["entry_effective_price"]
        units = position["entry_units"]
        net_isk = (exit_net - entry) * units
        held_days = (now - (parse_iso(position["at"]) or now)).total_seconds() / 86400.0
        record = {
            "event": "close",
            "position_id": position_id,
            "at": iso(now),
            "type_id": position["type_id"],
            "type_name": position.get("type_name"),
            "setup_tag": position.get("setup_tag"),
            "fill_model": model,
            "fill_assumed": bool(model == "maker" and actual_price is None),
            "fill_assumption": (
                MAKER_ASSUMED if model == "maker" and actual_price is None else None
            ),
            "exit_walk_price": bid.price,
            "exit_effective_price": exit_net,
            "sales_tax_pct": self.costs.sales_tax_pct,
            "broker_fee_pct": broker_pct if model == "maker" else 0.0,
            "entry_effective_price": entry,
            "units": units,
            "net_isk": net_isk,
            "net_return_pct": (exit_net / entry - 1.0) * 100.0,
            "realized_r": _realized_r(entry, exit_net, position.get("stop_price")),
            "held_days": held_days,
            "escrow_capital_days": self.costs.escrow_capital_days(
                position["notional_isk"], held_days
            ),
            "book_sweep_ts": bid.sweep_ts,
            "book_age_minutes": round(bid.age_minutes or 0.0, 2),
            "priced_from": "operator_actual_fill" if actual_price is not None else "book_walk",
            "note": note,
        }
        return self._append(record)

    def record_real_fill(
        self,
        *,
        position_id: str,
        side: str,
        actual_price: float,
        actual_units: float,
        now: datetime | None = None,
    ) -> dict:
        """The SMALL-REAL rung: an actual fill beside the predicted price.

        This is how the cost model gets validated against reality rather than
        against itself. The tolerance was stated in plan.md §12.3 before any
        fill was recorded: ±0.5% of notional.
        """
        now = ensure_utc(now or utcnow())
        positions = self.positions()
        position = positions.get(position_id)
        if position is None:
            self._refuse(f"unknown position {position_id!r}", {"action": "real_fill"})
        predicted = (
            position["entry_effective_price"]
            if side == "buy"
            else (position.get("close") or {}).get("exit_effective_price")
        )
        notional = position["notional_isk"]
        difference_isk = (
            (actual_price - predicted) * actual_units if predicted is not None else None
        )
        difference_pct = (
            abs(difference_isk) / notional * 100.0
            if difference_isk is not None and notional
            else None
        )
        tolerance = self.config.paper.fill_tolerance_pct_of_notional
        return self._append(
            {
                "event": "real_fill",
                "position_id": position_id,
                "at": iso(now),
                "side": side,
                "actual_price": actual_price,
                "actual_units": actual_units,
                "predicted_price": predicted,
                "difference_isk": difference_isk,
                "difference_pct_of_notional": difference_pct,
                "tolerance_pct_of_notional": tolerance,
                "within_tolerance": (
                    None if difference_pct is None else bool(difference_pct <= tolerance)
                ),
            }
        )

    # -- reporting ---------------------------------------------------------
    def report(self, now: datetime | None = None) -> PaperReport:
        """The §12.4 report: refusals first, then results, then the verdict."""
        from .backtest import breakeven_win_rate, wilson_lower_bound

        now = ensure_utc(now or utcnow())
        refusals = self.refusals()
        reasons: dict[str, int] = {}
        for refusal in refusals:
            key = str(refusal.get("reason", "")).split(";")[0][:80]
            reasons[key] = reasons.get(key, 0) + 1

        positions = self.positions()
        closed = [
            {
                **position["close"],
                "thesis": position.get("thesis"),
                "setup_tag": position["close"].get("setup_tag") or position.get("setup_tag"),
                "fill_model": position["close"].get("fill_model")
                or position.get("fill_model")
                or "taker",
            }
            for position in positions.values()
            if position.get("close")
        ]
        open_rows = []
        for position_id, position in positions.items():
            if position.get("close"):
                continue
            latest = position["marks"][-1] if position["marks"] else None
            open_rows.append(
                {
                    "position_id": position_id,
                    "type_id": position["type_id"],
                    "type_name": position.get("type_name"),
                    "opened_at": position["at"],
                    "notional_isk": position["notional_isk"],
                    "entry_effective_price": position["entry_effective_price"],
                    "last_mark": latest.get("mark_net_price") if latest else None,
                    "last_mark_at": latest.get("at") if latest else None,
                    "mark_stale": latest.get("stale") if latest else None,
                    "unrealized_net_isk": latest.get("unrealized_net_isk") if latest else None,
                    "self_impact": position.get("self_impact"),
                    "fill_model": position.get("fill_model") or "taker",
                    "fill_assumed": bool(position.get("fill_assumed")),
                }
            )

        report = PaperReport(
            generated_at=iso(now),
            refused=len(refusals),
            refusal_reasons=reasons,
            open_positions=open_rows,
            closed=closed,
            self_impact_flags=sum(
                1 for position in positions.values() if position.get("self_impact")
            ),
        )
        # A tally where half the exits were priced by the operator means
        # something different from one where every exit came off a live book.
        for record in closed:
            key = str(record.get("priced_from") or "book_walk")
            report.priced_from[key] = report.priced_from.get(key, 0) + 1
        real_fills = [record for record in self.records() if record.get("event") == "real_fill"]
        if real_fills:
            within = [record for record in real_fills if record.get("within_tolerance") is True]
            report.fill_accuracy = {
                "samples": len(real_fills),
                "within_tolerance": len(within),
                "tolerance_pct_of_notional": self.config.paper.fill_tolerance_pct_of_notional,
                "worst_difference_pct": max(
                    (record.get("difference_pct_of_notional") or 0.0 for record in real_fills),
                    default=None,
                ),
            }
        if not closed:
            report.verdict = _verdict(0, 0.0, None, None, self.config)
            return report

        returns = [float(record["net_return_pct"]) for record in closed]
        report.cumulative_net_isk = float(sum(record["net_isk"] for record in closed))
        report.cumulative_net_pct = float(sum(returns))
        wins = sum(1 for value in returns if value > 0)
        report.win_rate = wins / len(returns)
        report.wilson_lb = wilson_lower_bound(wins, len(returns))
        import numpy as np

        report.breakeven_win_rate = breakeven_win_rate(np.array(returns, dtype="float64"))
        r_values = [
            record["realized_r"] for record in closed if record.get("realized_r") is not None
        ]
        if r_values:
            report.r_distribution = {
                "count": len(r_values),
                "mean": float(sum(r_values) / len(r_values)),
                "min": float(min(r_values)),
                "max": float(max(r_values)),
                "positive": sum(1 for value in r_values if value > 0),
            }
        report.verdict = _verdict(
            len(closed),
            report.cumulative_net_isk,
            report.wilson_lb,
            report.breakeven_win_rate,
            self.config,
        )
        report.by_fill_model = self._by_fill_model(closed)
        return report

    def _by_fill_model(self, closed: list[dict]) -> dict:
        """The same frozen rule, applied to each population on its own.

        A taker trade pays the spread; a maker trade is paid it and carries an
        unpriced queue assumption instead. They are two different experiments,
        and one blended win rate answers neither of them.
        """
        import numpy as np

        from .backtest import breakeven_win_rate, wilson_lower_bound

        out: dict = {}
        for model in FILL_MODELS:
            sample = [record for record in closed if (record.get("fill_model") or "taker") == model]
            if not sample:
                continue
            returns = [float(record["net_return_pct"]) for record in sample]
            net_isk = float(sum(record["net_isk"] for record in sample))
            wins = sum(1 for value in returns if value > 0)
            wilson = wilson_lower_bound(wins, len(returns))
            breakeven = breakeven_win_rate(np.array(returns, dtype="float64"))
            out[model] = {
                "closed": len(sample),
                "cumulative_net_isk": net_isk,
                "win_rate": wins / len(returns),
                "wilson_lb": wilson,
                "breakeven_win_rate": breakeven,
                "assumed_fills": sum(1 for record in sample if record.get("fill_assumed")),
                "verdict": _verdict(len(sample), net_isk, wilson, breakeven, self.config),
            }
        return out


def _tier_index(tiers: list[float], notional: float) -> int | None:
    for index, tier in enumerate(tiers):
        if abs(float(tier) - float(notional)) < 1.0:
            return index
    return None


def _planned_r(
    entry: float,
    stop: float | None,
    target: float | None,
    costs: CostModel,
    *,
    maker: bool = False,
):
    """Planned R, net of the fees the *planned exit* pays. Gross R never appears.

    A maker exit pays the broker fee as well as the tax, so planning one in R
    means charging both — otherwise a maker plan looks better than a taker
    plan by exactly the fee it forgot.
    """
    if not stop or not target or entry <= 0 or stop >= entry:
        return None
    risk = entry - stop
    reward = costs.sell_proceeds(target, maker=maker) - entry
    if risk <= 0:
        return None
    return reward / risk


def _realized_r(entry: float, exit_net: float, stop: float | None):
    if not stop or entry <= 0 or stop >= entry:
        return None
    risk = entry - stop
    if risk <= 0:
        return None
    return (exit_net - entry) / risk


def _verdict(
    closed: int,
    cumulative_net_isk: float,
    wilson_lb: float | None,
    breakeven: float | None,
    config: Config,
) -> dict:
    """The FROZEN §12.4 verdict tracker. Thresholds never move after the fact."""
    first_read = config.paper.verdict_first_read_closed
    falsify = config.paper.verdict_falsify_negative_closed
    rule = (
        f"first read at {first_read} closed trades; falsified at {falsify} if cumulative "
        "net P&L < 0 AND Wilson LB win rate < breakeven win rate "
        "(plan.md §12.4, frozen 2026-08-20 before the first trade)"
    )
    base = {"rule": rule, "closed": closed}
    if closed < first_read:
        return {
            **base,
            "verdict": "TOO_EARLY",
            "detail": f"{closed}/{first_read} closed trades; no read is offered, "
            "and none should be taken",
        }
    strong = (
        cumulative_net_isk > 0
        and wilson_lb is not None
        and breakeven is not None
        and wilson_lb > breakeven
    )
    weak = (
        cumulative_net_isk < 0
        and wilson_lb is not None
        and breakeven is not None
        and wilson_lb < breakeven
    )
    if closed < falsify:
        return {
            **base,
            "verdict": "PROMISING" if strong else "WEAK",
            "detail": "first read only — neither outcome is a decision",
        }
    if weak:
        return {
            **base,
            "verdict": "FALSIFIED",
            "detail": "at this size and cadence the setup does not make money; "
            "the honest response is to stop",
        }
    if strong:
        return {
            **base,
            "verdict": "PROVISIONALLY_CONFIRMED",
            "detail": "promotion to real ISK remains an explicit operator decision, "
            "never an automatic consequence",
        }
    return {**base, "verdict": "INCONCLUSIVE", "detail": "keep running"}


def render_report(report: PaperReport) -> str:
    """Refusals lead. A system that declines to price things is working."""
    lines = [
        "# Paper trading report",
        "",
        f"Generated {report.generated_at}.",
        "",
        "## Refused / UNKNOWN",
        "",
        f"**{report.refused}** decisions were refused rather than priced.",
    ]
    for reason, count in sorted(report.refusal_reasons.items(), key=lambda item: -item[1]):
        lines.append(f"- {count}x {reason}")
    lines.extend(
        [
            "",
            "## Results",
            "",
            f"- Closed trades: **{len(report.closed)}**",
            f"- Cumulative net P&L: **{report.cumulative_net_isk:,.0f} ISK**",
        ]
    )
    if report.win_rate is not None:
        lines.append(
            f"- Win rate: {report.win_rate:.1%} "
            f"(Wilson 95% LB {report.wilson_lb:.3f}"
            + (
                f", breakeven {report.breakeven_win_rate:.3f})"
                if report.breakeven_win_rate is not None
                else ", breakeven UNKNOWN)"
            )
        )
    if report.priced_from and set(report.priced_from) != {"book_walk"}:
        lines.append(
            "- Exits priced from: "
            + ", ".join(f"{count}x {kind}" for kind, count in sorted(report.priced_from.items()))
        )
    if report.r_distribution:
        distribution = report.r_distribution
        lines.append(
            f"- R distribution: n={distribution['count']}, mean {distribution['mean']:.2f}R, "
            f"range {distribution['min']:.2f}R … {distribution['max']:.2f}R"
        )
    if report.self_impact_flags:
        lines.append(
            f"- Positions flagged `self_impact` (notional the market would notice): "
            f"{report.self_impact_flags}"
        )
    if report.fill_accuracy:
        accuracy = report.fill_accuracy
        lines.extend(
            [
                "",
                "## Predicted vs actual fills (the SMALL-REAL rung)",
                "",
                f"- {accuracy['within_tolerance']}/{accuracy['samples']} within "
                f"±{accuracy['tolerance_pct_of_notional']}% of notional "
                "(tolerance stated in plan.md §12.3 before any fill was recorded)",
            ]
        )
        if accuracy.get("worst_difference_pct") is not None:
            lines.append(f"- worst deviation: {accuracy['worst_difference_pct']:.3f}% of notional")
    if report.by_fill_model:
        lines.extend(
            [
                "",
                "## By fill model — two experiments, scored apart",
                "",
                "| fill model | closed | net ISK | win rate | Wilson LB | breakeven | verdict |",
                "|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for model, block in sorted(report.by_fill_model.items()):
            lines.append(
                f"| {model} | {block['closed']} | {block['cumulative_net_isk']:,.0f} "
                f"| {block['win_rate']:.1%} | {block['wilson_lb']:.3f} "
                + (
                    f"| {block['breakeven_win_rate']:.3f} "
                    if block["breakeven_win_rate"] is not None
                    else "| UNKNOWN "
                )
                + f"| {block['verdict'].get('verdict', 'UNKNOWN')} |"
            )
        if "maker" in report.by_fill_model:
            lines.extend(
                [
                    "",
                    f"> {report.by_fill_model['maker']['assumed_fills']} of the maker rows are "
                    "ASSUMED fills: the book proved the price was postable, never that anyone "
                    "traded into it. Queue position and adverse selection are unpriced.",
                ]
            )
    lines.extend(["", "## Open positions", ""])
    if not report.open_positions:
        lines.append("_none_")
    else:
        lines.append("| position | type | opened | notional | fill | entry | last mark | stale |")
        lines.append("|---|---|---|---:|---|---:|---:|---|")
        for row in report.open_positions:
            mark = "UNKNOWN" if row["last_mark"] is None else f"{row['last_mark']:,.2f}"
            fill = row.get("fill_model") or "taker"
            lines.append(
                f"| {row['position_id']} | {row.get('type_name') or row['type_id']} "
                f"| {row['opened_at'][:10]} | {row['notional_isk']:,.0f} "
                f"| {fill}{'*' if row.get('fill_assumed') else ''} "
                f"| {row['entry_effective_price']:,.2f} | {mark} "
                f"| {'yes' if row.get('mark_stale') else 'no'} |"
            )
    verdict_block = report.verdict
    lines.extend(
        [
            "",
            "## Verdict (plan.md §12.4, frozen before the first trade)",
            "",
            f"**{verdict_block.get('verdict', 'UNKNOWN')}** — {verdict_block.get('detail', '')}",
            "",
            (
                "> This headline is the frozen rule over **every** closed trade. With "
                "both fill models in the sample it averages two different experiments; "
                "the per-model table above is the one to read."
                if len(report.by_fill_model) > 1
                else ""
            ),
            "",
            f"> Rule: {verdict_block.get('rule', '')}",
        ]
    )
    return "\n".join(lines) + "\n"


def horizon_hint(config: Config) -> timedelta:
    """How long a paper position is expected to live, for the daemon's marks."""
    return timedelta(days=max(config.backtest.horizons_days))
