"""Paper hauls: the evidence half of the hauling tab (plan.md §23, §19.4).

The scan says what looks good. This records what the operator decided, in both
directions, under exactly the discipline §19.4 imposes on the paper ledger:

* an **open** needs a thesis he can argue with and at least one *like* tag from
  the committed vocabulary;
* a **pass** needs at least one *dislike* tag;
* **no tags, no record** — and the refusal itself is written to the ledger,
  because a decision started and not qualified is information too (§22 S7).

The refusal path is the one worth being careful about, and it is the same
mistake §22 S7 found in `paper.py`: validation that raises *before* the refusal
is recorded loses exactly the class of decision worth keeping — the one made
wrongly. Every check here routes through `_refuse` first.

This ledger deliberately does **not** price anything. A haul is opened against
a plan the scan produced and closed against what the operator really got; there
is no book quote in here to be optimistic with.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .paths import append_jsonl, read_jsonl
from .reasons import DISLIKE, LIKE, PASS_ACTIONS, ReasonError, normalise_tags
from .timeutil import ensure_utc, iso, utcnow

__all__ = ["HaulLedger", "HaulRefusal", "HaulTally", "render_haul_tally"]

NO_THESIS = "a recorded haul needs a thesis sentence you can argue with"
NO_LIKE_TAGS = (
    "a recorded haul needs at least one 'like' tag from config/reasons.jsonl — "
    "a decision whose reason is not recorded can never be attributed in either direction"
)
NO_DISLIKE_TAGS = (
    "a recorded pass needs at least one 'dislike' tag from config/reasons.jsonl — "
    "this is the half of the record nobody keeps, and it is the cheap half"
)


class HaulRefusal(RuntimeError):
    """Raised after the refusal is written, never instead of writing it."""


@dataclass(slots=True)
class HaulTally:
    """What the ledger holds. Refusals lead, as they do in `paper report`."""

    refused: int = 0
    opened: int = 0
    closed: int = 0
    passed: int = 0
    open_ids: tuple[str, ...] = ()
    realized_net_isk: float | None = None
    unresolved_closes: int = 0

    def as_dict(self) -> dict:
        return {
            "refused": self.refused,
            "opened": self.opened,
            "closed": self.closed,
            "passed": self.passed,
            "open_ids": list(self.open_ids),
            "realized_net_isk": self.realized_net_isk,
            "unresolved_closes": self.unresolved_closes,
        }


class HaulLedger:
    """Append-only, one record per event. Nothing rewrites history."""

    def __init__(self, path: Path, config: Config) -> None:
        self.path = path
        self.config = config

    # -- reading -----------------------------------------------------------
    def records(self) -> list[dict]:
        return read_jsonl(self.path)

    def hauls(self) -> dict[str, dict]:
        hauls: dict[str, dict] = {}
        for record in self.records():
            haul_id = record.get("haul_id")
            if record.get("event") == "open" and haul_id:
                if haul_id in hauls:
                    suffix = 2
                    while f"{haul_id}#{suffix}" in hauls:
                        suffix += 1
                    haul_id = f"{haul_id}#{suffix}"
                    record = {**record, "haul_id": haul_id, "duplicate_id": True}
                hauls[haul_id] = {**record, "close": None}
            elif record.get("event") == "close" and haul_id in hauls:
                hauls[haul_id]["close"] = record
        return hauls

    def refusals(self) -> list[dict]:
        return [record for record in self.records() if record.get("event") == "refused"]

    def passes(self) -> list[dict]:
        return [record for record in self.records() if record.get("event") == "pass"]

    # -- writing -----------------------------------------------------------
    def _append(self, record: dict) -> dict:
        append_jsonl(self.path, [record])
        return record

    def _refuse(self, reason: str, context: dict) -> dict:
        self._append({"event": "refused", "at": iso(utcnow()), "reason": reason, **context})
        raise HaulRefusal(reason)

    def _haul_id(self, type_id: int, now: datetime) -> str:
        stamp = now.strftime("%Y%m%dT%H%M%S")
        base = f"{int(type_id)}-{stamp}"
        existing = set(self.hauls())
        if base not in existing:
            return base
        suffix = 2
        while f"{base}#{suffix}" in existing:
            suffix += 1
        return f"{base}#{suffix}"

    def record_open(
        self,
        *,
        type_id: int,
        type_name: str | None,
        quantity: float,
        source_station: int | None,
        dest_station: int | None,
        thesis: str,
        like_tags=(),
        reason_text: str = "",
        expected_cost_isk: float | None = None,
        expected_net_isk: float | None = None,
        route_jumps: int | None = None,
        generations: dict | None = None,
        vocabulary=None,
        now: datetime | None = None,
    ) -> dict:
        """Record a haul the operator is actually taking, or refuse it."""
        now = ensure_utc(now or utcnow())
        context = {
            "type_id": int(type_id),
            "type_name": type_name,
            "quantity": float(quantity),
            "action": "open",
            "attempted_like_tags": [str(tag) for tag in (like_tags or ())],
        }
        if not str(thesis).strip():
            self._refuse(NO_THESIS, context)
        try:
            likes = normalise_tags(like_tags, vocabulary, LIKE) if vocabulary else ()
        except ReasonError as exc:
            self._refuse(str(exc), context)
        if not likes:
            self._refuse(NO_LIKE_TAGS, context)
        if float(quantity) <= 0:
            self._refuse("a haul with no units in it is not a haul", context)
        return self._append(
            {
                "event": "open",
                "at": iso(now),
                "haul_id": self._haul_id(type_id, now),
                "type_id": int(type_id),
                "type_name": type_name,
                "quantity": float(quantity),
                "source_station": source_station,
                "dest_station": dest_station,
                "thesis": str(thesis),
                "like_tags": list(likes),
                "reason_text": str(reason_text),
                "expected_cost_isk": expected_cost_isk,
                "expected_net_isk": expected_net_isk,
                "route_jumps": route_jumps,
                "generations": generations or {},
            }
        )

    def record_close(
        self,
        *,
        haul_id: str,
        actual_proceeds_isk: float | None = None,
        actual_cost_isk: float | None = None,
        note: str = "",
        now: datetime | None = None,
    ) -> dict:
        """Close a haul against what really happened.

        A close with no numbers is still recorded — "I sold it and did not write
        down what for" is a fact about the evidence, and the tally reports it as
        unresolved rather than assuming the plan's own forecast came true.

        **A close is resolved only when BOTH sides are actual.** Proceeds with
        no cost yields `assumed_net_isk`, labelled and computed from the
        forecast; `realized_net_isk` and the forecast error stay UNKNOWN,
        because a forecast cannot be evidence about itself.
        """
        now = ensure_utc(now or utcnow())
        context = {"haul_id": haul_id, "action": "close"}
        hauls = self.hauls()
        if haul_id not in hauls:
            self._refuse(f"no open haul with id {haul_id!r}", context)
        if hauls[haul_id].get("close") is not None:
            self._refuse(f"haul {haul_id!r} is already closed", context)
        opened = hauls[haul_id]
        # **An actual is an actual.** Borrowing `expected_cost_isk` and storing
        # it under `actual_cost_isk` let the forecast grade its own homework:
        # a "realized" net and a "forecast error" computed from the forecast,
        # counted as resolved evidence. This ledger is the path by which
        # §23.7's priors are supposed to *become* measurements, so contaminating
        # it is the one thing it cannot do.
        expected_cost = opened.get("expected_cost_isk")
        # Where the cost figure this record *used* came from. Nothing computed
        # means nothing sourced — a close with no proceeds applied no cost at
        # all, whatever the open happened to forecast.
        if actual_cost_isk is not None:
            cost_source = "actual"
        elif expected_cost is not None and actual_proceeds_isk is not None:
            cost_source = "expected"
        else:
            cost_source = None
        fully_actual = actual_proceeds_isk is not None and actual_cost_isk is not None
        realized = float(actual_proceeds_isk) - float(actual_cost_isk) if fully_actual else None
        # Shown, but never mistaken for a measurement: what the trip nets if
        # the forecast cost was right.
        assumed = (
            float(actual_proceeds_isk) - float(expected_cost)
            if not fully_actual and actual_proceeds_isk is not None and expected_cost is not None
            else None
        )
        return self._append(
            {
                "event": "close",
                "at": iso(now),
                "haul_id": haul_id,
                "type_id": opened.get("type_id"),
                "actual_proceeds_isk": actual_proceeds_isk,
                "actual_cost_isk": actual_cost_isk,
                "cost_source": cost_source,
                "expected_cost_isk": expected_cost,
                "realized_net_isk": realized,
                "assumed_net_isk": assumed,
                "expected_net_isk": opened.get("expected_net_isk"),
                "forecast_error_isk": (
                    realized - float(opened["expected_net_isk"])
                    if realized is not None and opened.get("expected_net_isk") is not None
                    else None
                ),
                "note": str(note),
            }
        )

    def record_pass(
        self,
        *,
        type_id: int,
        type_name: str | None,
        action: str = "not_today",
        dislike_tags=(),
        reason_text: str = "",
        source_station: int | None = None,
        dest_station: int | None = None,
        vocabulary=None,
        now: datetime | None = None,
    ) -> dict:
        """Record a haul deliberately not taken. Same rigour as taking one."""
        now = ensure_utc(now or utcnow())
        context = {
            "type_id": int(type_id),
            "type_name": type_name,
            "action": action,
            "attempted_dislike_tags": [str(tag) for tag in (dislike_tags or ())],
        }
        if action not in PASS_ACTIONS:
            self._refuse(f"pass action must be one of {PASS_ACTIONS}, got {action!r}", context)
        try:
            dislikes = normalise_tags(dislike_tags, vocabulary, DISLIKE) if vocabulary else ()
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
                "dislike_tags": list(dislikes),
                "reason_text": str(reason_text),
                "source_station": source_station,
                "dest_station": dest_station,
            }
        )

    # -- reporting ---------------------------------------------------------
    def tally(self) -> HaulTally:
        hauls = self.hauls()
        closes = [haul["close"] for haul in hauls.values() if haul.get("close")]
        # Only fully-actual closes count as resolved: this is the sample that
        # will one day replace §23.7's priors, and a half-measured close is not
        # a member of it.
        realized = [
            float(close["realized_net_isk"])
            for close in closes
            if close.get("realized_net_isk") is not None
        ]
        return HaulTally(
            refused=len(self.refusals()),
            opened=len(hauls),
            closed=len(closes),
            passed=len(self.passes()),
            open_ids=tuple(haul_id for haul_id, haul in hauls.items() if haul.get("close") is None),
            realized_net_isk=sum(realized) if realized else None,
            unresolved_closes=len(closes) - len(realized),
        )


def render_haul_tally(tally: HaulTally) -> str:
    """Refusals first: a system that declines to record things is working."""
    lines = [
        "# Paper hauls",
        "",
        f"- refused: **{tally.refused}**",
        f"- opened: {tally.opened} ({len(tally.open_ids)} still open)",
        f"- closed: {tally.closed} ({tally.unresolved_closes} with no ISK recorded)",
        f"- passed: {tally.passed}",
    ]
    if tally.realized_net_isk is not None:
        lines.append(
            f"- realized net across resolved closes: **{tally.realized_net_isk:,.0f} ISK**"
        )
    else:
        lines.append("- realized net: **UNKNOWN** — no close has recorded what it actually paid")
    if tally.open_ids:
        lines.extend(["", "Open: " + ", ".join(tally.open_ids)])
    lines.extend(
        [
            "",
            "A haul recorded here is evidence, not a fill: nothing in this ledger",
            "prices anything, and a close carries what the operator really got or",
            "says it does not know.",
        ]
    )
    return "\n".join(lines) + "\n"
