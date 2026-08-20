"""The anchor calendar — patch dates are this system's earnings dates.

Equities anchor AVWAP at earnings gaps and must *infer* the gap index from
open-vs-prior-close arithmetic. EVE patch datetimes are exact, published in
advance, and global, so the inference machinery is unnecessary and the whole
open-dependent code path dies with it (plan.md §6, §2).

`config/anchors.jsonl` is committed — it is data, not secret. Each record is
`{date, label, scope}` where scope is `global` or a `market_group_id` subtree.
The operator seeds it; the patch-notes watcher may only *append candidates for
confirmation*, and never auto-anchors (plan.md §11 D7).

Point-in-time filtering is preserved from upstream: an anchor is only visible
to a computation whose as-of date is on or after it. Nothing back-dates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ..paths import append_jsonl
from ..store.db import Database

__all__ = [
    "Anchor",
    "anchor_index",
    "append_candidate",
    "load_anchors",
    "pick_current_anchor",
    "seed_anchors_into_db",
]

GLOBAL_SCOPE = "global"


@dataclass(frozen=True, slots=True)
class Anchor:
    anchor_date: date
    label: str
    scope: str = GLOBAL_SCOPE
    confirmed: bool = True
    source: str | None = None

    @property
    def is_global(self) -> bool:
        return self.scope == GLOBAL_SCOPE

    def market_group_id(self) -> int | None:
        if self.is_global:
            return None
        try:
            return int(self.scope)
        except (TypeError, ValueError):
            return None

    def as_dict(self) -> dict:
        return {
            "date": self.anchor_date.isoformat(),
            "label": self.label,
            "scope": self.scope,
            "confirmed": self.confirmed,
            "source": self.source,
        }


def load_anchors(path: Path) -> list[Anchor]:
    """Read the committed calendar. A missing file is an empty calendar."""
    if not path.exists():
        return []
    anchors: list[Anchor] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            record = json.loads(line)
            try:
                anchor_date = date.fromisoformat(str(record["date"])[:10])
            except (KeyError, ValueError):
                continue
            anchors.append(
                Anchor(
                    anchor_date=anchor_date,
                    label=str(record.get("label") or "unnamed"),
                    scope=str(record.get("scope") or GLOBAL_SCOPE),
                    confirmed=bool(record.get("confirmed", True)),
                    source=record.get("source"),
                )
            )
    return sorted(anchors, key=lambda item: item.anchor_date)


def append_candidate(path: Path, anchor: Anchor) -> None:
    """Append an UNCONFIRMED candidate. The watcher may do this; it may not anchor."""
    payload = anchor.as_dict()
    payload["confirmed"] = False
    append_jsonl(path, [payload])


def seed_anchors_into_db(db: Database, anchors: list[Anchor]) -> int:
    with db.transaction() as conn:
        for anchor in anchors:
            conn.execute(
                "INSERT INTO anchors(anchor_date, label, scope, confirmed, source)"
                " VALUES(?,?,?,?,?) ON CONFLICT(anchor_date, label, scope) DO UPDATE SET"
                " confirmed=excluded.confirmed, source=excluded.source",
                (
                    anchor.anchor_date.isoformat(),
                    anchor.label,
                    anchor.scope,
                    1 if anchor.confirmed else 0,
                    anchor.source,
                ),
            )
    return len(anchors)


def applicable_anchors(
    anchors: list[Anchor],
    *,
    market_group_chain: list[int] | None = None,
    as_of: date | datetime | None = None,
    confirmed_only: bool = True,
) -> list[Anchor]:
    """Anchors visible to one type at one moment. Point-in-time, never future."""
    chain = {int(value) for value in (market_group_chain or [])}
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    result: list[Anchor] = []
    for anchor in anchors:
        if confirmed_only and not anchor.confirmed:
            continue
        if as_of is not None and anchor.anchor_date > as_of:
            continue
        if anchor.is_global or (anchor.market_group_id() in chain):
            result.append(anchor)
    return sorted(result, key=lambda item: item.anchor_date)


def anchor_index(frame: pd.DataFrame, anchor: Anchor) -> tuple[int, bool]:
    """Bar index of the anchor, and whether the anchor predates the frame.

    A `truncated=True` anchor is honoured from the first available bar and the
    band says it is truncated — the ~13.5-month ESI horizon is a fact about the
    data, never a silently shortened anchor (plan.md §9 R7).
    """
    if frame.empty:
        return 0, True
    stamps = pd.to_datetime(frame["datetime"], utc=True)
    target = pd.Timestamp(anchor.anchor_date, tz="UTC")
    matches = stamps[stamps >= target]
    if matches.empty:
        # The anchor is newer than every bar: there is no window to anchor.
        return len(frame) - 1, False
    index = int(stamps.searchsorted(matches.iloc[0]))
    return index, target < stamps.iloc[0]


def pick_current_anchor(
    anchors: list[Anchor],
    *,
    market_group_chain: list[int] | None = None,
    as_of: date | datetime | None = None,
    fresh_days: int = 10,
) -> tuple[Anchor | None, bool]:
    """The anchor a setup is read against, plus the fresh-anchor ambiguity flag.

    Ported behaviour: when the newest applicable anchor is younger than
    `fresh_days`, both it and its predecessor are arguably live, and the read
    is **ambiguous** — flagged for the operator rather than resolved by the
    machine.
    """
    visible = applicable_anchors(anchors, market_group_chain=market_group_chain, as_of=as_of)
    if not visible:
        return None, False
    newest = visible[-1]
    if as_of is None:
        return newest, False
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    ambiguous = (as_of - newest.anchor_date).days < fresh_days and len(visible) > 1
    return newest, ambiguous
