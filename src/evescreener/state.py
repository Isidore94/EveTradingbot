"""SQLite state store: HTTP cache, telemetry ledger, SDE snapshot, watchlist.

One file (``state.db``, WAL), one process, no writer leases (plan.md §3.5).

The sweep ledger is not a log — it is the evidence the Phase 0 gate is settled
on. Every request records what it cost, what the rate-limit headers said, and
whether the previously-stored ``Expires`` had actually passed before we asked
again (§3.2, §3.3).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .clock import UTC, ensure_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    url            TEXT PRIMARY KEY,
    etag           TEXT,
    expires_at     TEXT,
    last_modified  TEXT,
    fetched_at     TEXT NOT NULL,
    status         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sweep_ledger (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at        TEXT NOT NULL,
    url                 TEXT NOT NULL,
    status              INTEGER,
    outcome             TEXT NOT NULL,
    tokens_charged      INTEGER NOT NULL DEFAULT 0,
    ratelimit_group     TEXT,
    ratelimit_limit     TEXT,
    ratelimit_remaining INTEGER,
    ratelimit_used      INTEGER,
    error_limit_remain  INTEGER,
    expires_at          TEXT,
    prior_expires_at    TEXT,
    honored_expiry      INTEGER NOT NULL DEFAULT 1,
    sent_if_none_match  INTEGER NOT NULL DEFAULT 0,
    duration_ms         INTEGER,
    note                TEXT
);
CREATE INDEX IF NOT EXISTS sweep_ledger_requested_at
    ON sweep_ledger (requested_at);

CREATE TABLE IF NOT EXISTS sde_types (
    type_id         INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    published       INTEGER NOT NULL,
    group_id        INTEGER,
    market_group_id INTEGER,
    volume          REAL,
    packaged_volume REAL,
    portion_size    INTEGER
);
CREATE INDEX IF NOT EXISTS sde_types_name ON sde_types (name);

CREATE TABLE IF NOT EXISTS sde_market_groups (
    market_group_id INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    parent_group_id INTEGER,
    has_types       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sde_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    type_id  INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    source   TEXT NOT NULL,
    added_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class CacheEntry:
    """What we know about a URL from the last time we asked for it."""

    url: str
    etag: str | None
    expires_at: dt.datetime | None
    last_modified: str | None
    fetched_at: dt.datetime
    status: int

    def is_fresh(self, now: dt.datetime) -> bool:
        """True while the cached response has not expired.

        Fetching again before this goes False is cache circumvention, which
        CCP treats as a bannable offence (§3.1, §10.4).
        """
        return self.expires_at is not None and now < self.expires_at


def _iso(value: dt.datetime | None) -> str | None:
    return None if value is None else ensure_utc(value).isoformat()


def _parse(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value).astimezone(UTC)


class StateStore:
    """Thin, explicit SQLite wrapper. No ORM, no migrations framework."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ---- HTTP cache -------------------------------------------------------

    def get_cache(self, url: str) -> CacheEntry | None:
        row = self._conn.execute(
            "SELECT * FROM http_cache WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        return CacheEntry(
            url=row["url"],
            etag=row["etag"],
            expires_at=_parse(row["expires_at"]),
            last_modified=row["last_modified"],
            fetched_at=_parse(row["fetched_at"]),
            status=row["status"],
        )

    def put_cache(self, entry: CacheEntry) -> None:
        self._conn.execute(
            """
            INSERT INTO http_cache
                (url, etag, expires_at, last_modified, fetched_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                etag = excluded.etag,
                expires_at = excluded.expires_at,
                last_modified = excluded.last_modified,
                fetched_at = excluded.fetched_at,
                status = excluded.status
            """,
            (
                entry.url,
                entry.etag,
                _iso(entry.expires_at),
                entry.last_modified,
                _iso(entry.fetched_at),
                entry.status,
            ),
        )

    # ---- Telemetry ledger -------------------------------------------------

    def record_request(self, **fields: object) -> None:
        for key in ("requested_at", "expires_at", "prior_expires_at"):
            if isinstance(fields.get(key), dt.datetime):
                fields[key] = _iso(fields[key])  # type: ignore[arg-type]
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        self._conn.execute(
            f"INSERT INTO sweep_ledger ({columns}) VALUES ({placeholders})",
            tuple(fields.values()),
        )

    def tokens_used_since(self, since: dt.datetime, group: str) -> int:
        row = self._conn.execute(
            """
            SELECT COALESCE(SUM(tokens_charged), 0) AS total
            FROM sweep_ledger
            WHERE requested_at >= ? AND ratelimit_group = ?
            """,
            (_iso(since), group),
        ).fetchone()
        return int(row["total"])

    LEDGER_SUMMARY_COLUMNS = (
        "requests",
        "tokens",
        "early_fetches",
        "not_modified",
        "skipped_fresh",
        "client_errors",
        "server_errors",
        "peak_tokens_used",
    )

    def ledger_summary(self, since: dt.datetime) -> dict[str, int]:
        """Counters the Phase 0 gate reads: expiry honoured, tokens, errors."""
        row = self._conn.execute(
            """
            SELECT
                COUNT(*)                                          AS requests,
                COALESCE(SUM(tokens_charged), 0)                  AS tokens,
                COALESCE(SUM(1 - honored_expiry), 0)              AS early_fetches,
                COALESCE(SUM(outcome = 'not_modified'), 0)        AS not_modified,
                COALESCE(SUM(outcome = 'skipped_fresh'), 0)       AS skipped_fresh,
                COALESCE(SUM(status >= 400 AND status < 500), 0)  AS client_errors,
                COALESCE(SUM(status >= 500), 0)                   AS server_errors,
                COALESCE(MAX(ratelimit_used), 0)                  AS peak_tokens_used
            FROM sweep_ledger
            WHERE requested_at >= ?
            """,
            (_iso(since),),
        ).fetchone()
        return {key: int(row[key] or 0) for key in self.LEDGER_SUMMARY_COLUMNS}

    # ---- SDE snapshot -----------------------------------------------------

    def replace_sde_types(self, rows: list[tuple]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM sde_types")
            conn.executemany(
                "INSERT INTO sde_types (type_id, name, published, group_id, "
                "market_group_id, volume, packaged_volume, portion_size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def replace_sde_market_groups(self, rows: list[tuple]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM sde_market_groups")
            conn.executemany(
                "INSERT INTO sde_market_groups (market_group_id, name, "
                "parent_group_id, has_types) VALUES (?, ?, ?, ?)",
                rows,
            )

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO sde_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM sde_meta WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else row["value"]

    def sde_type_count(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) AS n FROM sde_types").fetchone()["n"]
        )

    def resolve_names(self, names: list[str]) -> dict[str, int | None]:
        """Map exact English type names to type_ids; unresolved map to None."""
        resolved: dict[str, int | None] = {}
        for name in names:
            row = self._conn.execute(
                "SELECT type_id FROM sde_types WHERE name = ? AND published = 1",
                (name,),
            ).fetchone()
            resolved[name] = None if row is None else int(row["type_id"])
        return resolved

    def type_names(self, type_ids: list[int]) -> dict[int, str]:
        if not type_ids:
            return {}
        marks = ", ".join("?" for _ in type_ids)
        rows = self._conn.execute(
            f"SELECT type_id, name FROM sde_types WHERE type_id IN ({marks})",
            tuple(type_ids),
        ).fetchall()
        return {int(r["type_id"]): r["name"] for r in rows}

    # ---- Watchlist --------------------------------------------------------

    def upsert_watchlist(
        self, entries: list[tuple[int, str, str]], now: dt.datetime
    ) -> None:
        """Add or refresh watchlist rows. Nothing is ever auto-removed here.

        The candidate-registry invariant (§3.6, §10): operator-entered names
        leave the watchlist only when the operator takes them out.
        """
        self._conn.executemany(
            "INSERT INTO watchlist (type_id, name, source, added_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(type_id) DO UPDATE SET name = excluded.name",
            [(type_id, name, source, _iso(now)) for type_id, name, source in entries],
        )

    def watchlist(self) -> list[tuple[int, str]]:
        rows = self._conn.execute(
            "SELECT type_id, name FROM watchlist ORDER BY name"
        ).fetchall()
        return [(int(r["type_id"]), r["name"]) for r in rows]
