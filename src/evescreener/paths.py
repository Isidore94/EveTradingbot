"""Data-dir resolution and atomic writes.

Replaces the source repo's 1,001-line ``project_paths.py`` with the two things
that actually mattered: one resolver, and a write that cannot leave a partial
file behind. A failed publish never destroys the last verified output (§3.3).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DATA_DIR_ENV = "EVESCREENER_DATA_DIR"

REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_data_dir(configured: str) -> Path:
    """Resolve the data dir: ``EVESCREENER_DATA_DIR`` wins, else config (D2)."""
    raw = os.environ.get(DATA_DIR_ENV) or configured
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    return path


class DataPaths:
    """Every path the system writes, derived from one directory (§3.5)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def state_db(self) -> Path:
        return self.root / "state.db"

    @property
    def bars_dir(self) -> Path:
        return self.root / "bars"

    @property
    def books_dir(self) -> Path:
        return self.root / "books"

    @property
    def streams_dir(self) -> Path:
        return self.root / "streams"

    @property
    def digest_archive(self) -> Path:
        return self.streams_dir / "digests.jsonl"

    @property
    def decisions_log(self) -> Path:
        return self.streams_dir / "decisions.jsonl"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    def bars_partition(self, region_id: int, year: int) -> Path:
        return self.bars_dir / f"region={region_id}" / f"year={year}.parquet"

    def books_partition(self, region_id: int, date_iso: str) -> Path:
        return self.books_dir / f"region={region_id}" / f"date={date_iso}.parquet"

    def ensure(self) -> None:
        for path in (
            self.root,
            self.bars_dir,
            self.books_dir,
            self.streams_dir,
            self.cache_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@contextmanager
def atomic_write_path(target: Path) -> Iterator[Path]:
    """Yield a temp path in ``target``'s directory, then rename it into place.

    The rename is atomic on every platform this runs on, so a crash mid-write
    leaves the previous verified file untouched.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        yield tmp
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def append_jsonl(target: Path, record: dict) -> None:
    """Append one record to an append-only JSONL stream (§3.5)."""
    import json

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
