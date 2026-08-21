"""Reproducible measurement reports (plan.md §22 S8).

**A number in prose is not a measurement.** `plan.md` and `performers.py`
carried figures like "2,944 tracked types", "0.88 pp median difference" and
"39/23 readings above 1000%" with no as-of date, no membership definition, no
denominator and no way to re-run them. An independent reproduction disagreed
with every one of them, and a third run disagreed again — and **none of the
three can be shown right or wrong**, because not one recorded what it measured
or when. That is the defect; the differing numbers are only its symptom.

So a statistic that appears in the documentation is emitted **here**, with
enough provenance to be re-derived or contradicted:

* **as-of timestamp** — the clock the run used, not the clock it was written up;
* **membership and filters** — exactly which rows were in the denominator;
* **input identity** — size and mtime-derived digest of every source file, so a
  changed lake is visible rather than silent;
* **denominator** — carried beside every count, because "39 above 1000%" is not
  a fact without an *of what*;
* **command and code version** — the git revision, so the run can be repeated.

Figures whose original inputs cannot be recovered are **not replaced with new
ones**. They are labelled a historical snapshot and left where they are.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .timeutil import iso, utcnow

__all__ = ["MeasurementReport", "Statistic", "code_version", "file_identity"]


def file_identity(paths) -> list[dict]:
    """Size and a content-free digest for each input file.

    Hashing whole Parquet files would take minutes on a four-million-row lake,
    so the digest is over `(name, size, mtime_ns)`. That is enough to notice
    the inputs changed between two runs, which is the question being asked —
    it is deliberately **not** a claim that identical digests mean identical
    bytes, and it says so rather than implying otherwise.
    """
    records = []
    for path in sorted(Path(part) for part in paths):
        if not path.exists():
            records.append({"path": str(path), "present": False})
            continue
        stat = path.stat()
        digest = hashlib.sha256(
            f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()
        ).hexdigest()[:16]
        records.append(
            {
                "path": str(path),
                "present": True,
                "bytes": stat.st_size,
                "identity": digest,
                "identity_is": "sha256 of (name, size, mtime_ns) — not of the contents",
            }
        )
    return records


def code_version() -> str:
    """The git revision this ran at, or `unknown` outside a checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        return "unknown"
    return result.stdout.strip() or "unknown" if result.returncode == 0 else "unknown"


@dataclass(slots=True)
class Statistic:
    """One measured number, with the population it was measured over."""

    name: str
    value: float | int | None
    denominator: int | None = None
    unit: str = ""
    note: str = ""
    #: True when `value` counts members of `denominator`, so a share is
    #: meaningful. False for a magnitude such as "worst reading", where
    #: dividing by the population size produces a number that means nothing.
    is_count: bool = False

    def as_dict(self) -> dict:
        payload = {"name": self.name, "value": self.value, "unit": self.unit}
        # A count without an "of what" is not a fact.
        payload["denominator"] = self.denominator
        if self.denominator and self.is_count:
            payload["share"] = (
                round(float(self.value) / self.denominator, 6)
                if isinstance(self.value, int | float)
                else None
            )
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass(slots=True)
class MeasurementReport:
    """A set of statistics plus everything needed to re-derive them."""

    name: str
    as_of: str
    command: str
    membership: str
    filters: dict = field(default_factory=dict)
    inputs: list = field(default_factory=list)
    statistics: list = field(default_factory=list)
    code_version: str = ""
    notes: list = field(default_factory=list)

    @classmethod
    def start(cls, name: str, *, command: str, membership: str, now=None) -> MeasurementReport:
        return cls(
            name=name,
            as_of=iso(now or utcnow()),
            command=command,
            membership=membership,
            code_version=code_version(),
        )

    def add(self, statistic: Statistic) -> MeasurementReport:
        self.statistics.append(statistic)
        return self

    def as_dict(self) -> dict:
        return {
            "report": self.name,
            "as_of": self.as_of,
            "code_version": self.code_version,
            "command": self.command,
            "membership": self.membership,
            "filters": dict(self.filters),
            "inputs": list(self.inputs),
            "statistics": [statistic.as_dict() for statistic in self.statistics],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        """Markdown, with the provenance block first — it is not an appendix."""
        lines = [
            f"# {self.name}",
            "",
            f"* **as of** {self.as_of}",
            f"* **code** {self.code_version}",
            f"* **command** `{self.command}`",
            f"* **membership** {self.membership}",
        ]
        for key, value in sorted(self.filters.items()):
            lines.append(f"* **filter** `{key}` = {value}")
        if self.inputs:
            lines.append("* **inputs**")
            for record in self.inputs:
                if record.get("present"):
                    lines.append(
                        f"  * `{record['path']}` — {record['bytes']:,} bytes, "
                        f"identity `{record['identity']}`"
                    )
                else:
                    lines.append(f"  * `{record['path']}` — **absent**")
        lines += ["", "| statistic | value | of | share |", "|---|---:|---:|---:|"]
        for statistic in self.statistics:
            payload = statistic.as_dict()
            share = payload.get("share")
            lines.append(
                f"| {payload['name']} | {payload['value']} {payload['unit']} "
                f"| {payload['denominator'] if payload['denominator'] else ''} "
                f"| {f'{share:.4f}' if share is not None else ''} |"
            )
        if self.notes:
            lines += ["", "## Notes", ""]
            lines += [f"* {note}" for note in self.notes]
        return "\n".join(lines) + "\n"

    def write(self, path) -> Path:
        """Atomic write: a failed report never destroys the last one (§5)."""
        from .paths import atomic_write_text

        target = Path(path)
        atomic_write_text(target, self.render())
        atomic_write_text(target.with_suffix(".json"), json.dumps(self.as_dict(), indent=1) + "\n")
        return target
