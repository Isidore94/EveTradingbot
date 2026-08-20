"""Data-directory resolution and atomic writes.

One resolver, one atomic-write helper — the 1,001-LOC `project_paths.py` of
the source repo reduced to what a single-machine, single-process system needs
(plan.md §2). The atomic-write rule carries the source repo's invariant: **a
failed publish never destroys the last verified output.**
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

ENV_DATA_DIR = "EVESCREENER_DATA_DIR"


def resolve_data_dir(configured: str | os.PathLike[str]) -> Path:
    """Data dir, with `EVESCREENER_DATA_DIR` taking precedence (§11 D2)."""
    override = os.environ.get(ENV_DATA_DIR)
    root = Path(override).expanduser() if override else Path(configured).expanduser()
    return root.resolve()


class DataPaths:
    """Every path the system writes, derived from one root."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def db(self) -> Path:
        return self.root / "state.db"

    @property
    def bars(self) -> Path:
        return self.root / "bars"

    @property
    def books(self) -> Path:
        return self.root / "books"

    @property
    def killmails(self) -> Path:
        return self.root / "killmails"

    @property
    def streams(self) -> Path:
        return self.root / "streams"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def sde(self) -> Path:
        return self.root / "sde"

    @property
    def decisions(self) -> Path:
        return self.streams / "decisions.jsonl"

    @property
    def digests(self) -> Path:
        return self.streams / "digests.jsonl"

    @property
    def paper_ledger(self) -> Path:
        return self.streams / "paper.jsonl"

    def bars_partition(self, region_id: int, year: int) -> Path:
        return self.bars / f"region={region_id}" / f"year={year}.parquet"

    def books_partition(self, region_id: int, day: str) -> Path:
        return self.books / f"region={region_id}" / f"date={day}.parquet"

    def ensure(self) -> DataPaths:
        for directory in (
            self.root,
            self.bars,
            self.books,
            self.killmails,
            self.streams,
            self.reports,
            self.sde,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write `payload` to `path` via a same-directory temp file + rename.

    A crash or exception mid-write leaves the previous file untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def append_jsonl(path: Path, records: Iterable[dict]) -> int:
    """Append records to an append-only JSONL stream. Returns rows written.

    Append is the only mutation these streams allow; nothing rewrites history.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            written += 1
        stream.flush()
        os.fsync(stream.fileno())
    return written


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL stream; a missing file is an empty stream, not an error."""
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
