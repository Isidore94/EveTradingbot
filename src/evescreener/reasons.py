"""Qualified reasons, both directions (plan.md §19 Amendment 3).

The goal is that *why I acted* is data, not vibes. So both directions of a
decision are recorded with the same rigour:

* **Opening** requires a thesis sentence, a setup tag, and at least one "why I
  like it" tag from the committed vocabulary. No tags, no trade recorded —
  that refusal is written to the ledger like any other, because how often a
  decision was declined is itself a number (§12.4).
* **Passing** requires the same. "Not today" and "bad signal" demand at least
  one dislike tag. A pass with its reasons is a **recorded decision event**,
  not a dismissal — it is the half of the record that makes the other half
  interpretable.

Both directions land in the same JSONL ledger the CLI writes, so the GUI and
the CLI are two doors onto one record with identical validation.

Recording a pass is what makes regret tracking possible: the forward outcome
of a pass is measured on the same horizons and the same cost realism as the
backtest, so the learning loop can say which of the operator's *reasons* are
predictive in both directions — "trades tagged 'level confluence' ran +0.42R
on average" and "passes tagged 'spread too wide' were right 78% of the time"
are the same kind of statement about the same kind of evidence.

Nothing here adjusts anything. The system correlates and reports; the
operator promotes, demotes and edits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DISLIKE",
    "LIKE",
    "PASS_ACTIONS",
    "REASONS_FILE",
    "Reason",
    "ReasonError",
    "ReasonVocabulary",
    "load_reasons",
    "normalise_tags",
]

REASONS_FILE = "reasons.jsonl"

LIKE = "like"
DISLIKE = "dislike"

# What a pass can be. "not today" clears the name from today's queue only and
# NEVER touches Focus (§11 D4); "bad signal" says the setup itself misfired.
PASS_ACTIONS = ("not_today", "bad_signal")


class ReasonError(RuntimeError):
    """A malformed reasons.jsonl fails loudly; it is never partially loaded."""


@dataclass(frozen=True, slots=True)
class Reason:
    tag: str
    direction: str
    label: str
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "tag": self.tag,
            "direction": self.direction,
            "label": self.label,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class ReasonVocabulary:
    reasons: tuple[Reason, ...]

    @property
    def likes(self) -> tuple[Reason, ...]:
        return tuple(reason for reason in self.reasons if reason.direction == LIKE)

    @property
    def dislikes(self) -> tuple[Reason, ...]:
        return tuple(reason for reason in self.reasons if reason.direction == DISLIKE)

    def tags(self, direction: str) -> set[str]:
        return {reason.tag for reason in self.reasons if reason.direction == direction}

    def label(self, tag: str) -> str:
        for reason in self.reasons:
            if reason.tag == tag:
                return reason.label
        return tag

    def __bool__(self) -> bool:
        return bool(self.reasons)


def load_reasons(path: Path | None = None) -> ReasonVocabulary:
    """Read the committed reason vocabulary. A malformed row names itself."""
    path = path or (Path.cwd() / "config" / REASONS_FILE)
    if not path.exists():
        return ReasonVocabulary(reasons=())
    reasons: list[Reason] = []
    seen: set[tuple[str, str]] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{path}:{number}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReasonError(f"{where}: not valid JSON — {exc}") from exc
        if not isinstance(record, dict):
            raise ReasonError(f"{where}: each line must be a JSON object")
        missing = [key for key in ("tag", "direction", "label") if key not in record]
        if missing:
            raise ReasonError(f"{where}: missing {', '.join(missing)}")
        unknown = sorted(set(record) - {"tag", "direction", "label", "notes"})
        if unknown:
            raise ReasonError(
                f"{where}: unknown field(s) {', '.join(unknown)}; allowed: "
                "tag, direction, label, notes"
            )
        tag = str(record["tag"]).strip().lower().replace(" ", "_")
        if not tag:
            raise ReasonError(f"{where}: empty tag")
        direction = str(record["direction"]).strip().lower()
        if direction not in (LIKE, DISLIKE):
            raise ReasonError(
                f"{where}: direction must be {LIKE!r} or {DISLIKE!r}, got {record['direction']!r}"
            )
        key = (tag, direction)
        if key in seen:
            raise ReasonError(f"{where}: duplicate {direction} tag {tag!r}")
        seen.add(key)
        reasons.append(
            Reason(
                tag=tag,
                direction=direction,
                label=str(record["label"]),
                notes=str(record.get("notes", "")),
            )
        )
    return ReasonVocabulary(reasons=tuple(reasons))


def normalise_tags(tags, vocabulary: ReasonVocabulary, direction: str) -> tuple[str, ...]:
    """Clean, de-duplicate and check tags against the vocabulary.

    An empty result is the caller's cue to refuse. An unknown tag is a loud
    error rather than a silently-dropped one: a decision recorded with a
    typo'd reason is a decision whose reason will never be measurable.
    """
    known = vocabulary.tags(direction)
    cleaned: list[str] = []
    for value in tags or ():
        tag = str(value).strip().lower().replace(" ", "_")
        if not tag:
            continue
        if known and tag not in known:
            raise ReasonError(
                f"unknown {direction} tag {tag!r}; known: " + ", ".join(sorted(known))
            )
        if tag not in cleaned:
            cleaned.append(tag)
    return tuple(cleaned)
