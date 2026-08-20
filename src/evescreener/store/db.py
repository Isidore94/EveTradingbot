"""SQLite state store (WAL): ETags, the sweep telemetry ledger, SDE tables.

Everything that is small, relational, and mutable lives here; everything that
is large and append-only lives in the Parquet lake (plan.md §3.5). One file,
one process, no writer leases.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..timeutil import iso, parse_iso, utcnow

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Per-URL ETag store. `If-None-Match` on every request; a 304 costs 1 token
-- and confirms freshness (plan.md §3.1).
CREATE TABLE IF NOT EXISTS etags (
    url TEXT PRIMARY KEY,
    etag TEXT NOT NULL,
    expires_at TEXT,
    last_modified TEXT,
    fetched_at TEXT NOT NULL,
    body_sha TEXT
);

-- The sweep ledger IS the provider telemetry: every fetch, its status, the
-- rate-limit headers observed, and its duration (plan.md §3.5, §9 R4).
CREATE TABLE IF NOT EXISTS sweep_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at TEXT NOT NULL,
    feed TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER,
    duration_ms REAL,
    tokens_used INTEGER,
    tokens_remaining INTEGER,
    ratelimit_group TEXT,
    expires_at TEXT,
    error_limit_remain INTEGER,
    from_cache INTEGER NOT NULL DEFAULT 0,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger_time ON sweep_ledger(requested_at);
CREATE INDEX IF NOT EXISTS idx_ledger_feed ON sweep_ledger(feed, requested_at);

CREATE TABLE IF NOT EXISTS sde_types (
    type_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    market_group_id INTEGER,
    volume REAL,
    packaged_volume REAL,
    published INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_sde_types_name ON sde_types(name);
CREATE INDEX IF NOT EXISTS idx_sde_types_group ON sde_types(market_group_id);

-- Solar system -> region, so a killmail's system id becomes a market region.
CREATE TABLE IF NOT EXISTS sde_solar_systems (
    solar_system_id INTEGER PRIMARY KEY,
    region_id INTEGER NOT NULL,
    name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sde_systems_region ON sde_solar_systems(region_id);

CREATE TABLE IF NOT EXISTS sde_market_groups (
    market_group_id INTEGER PRIMARY KEY,
    parent_group_id INTEGER,
    name TEXT NOT NULL
);

-- The tracked universe: what cleared the liquidity floor, when, and on what
-- measurements. Types falling out are FLAGGED, never silently dropped.
CREATE TABLE IF NOT EXISTS universe (
    type_id INTEGER NOT NULL,
    region_id INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    -- Membership is decided on median UNIT volume; the ISK figure is the index
    -- WEIGHTING input, not a gate (§11 D3, amended).
    median_unit_volume REAL,
    median_isk_value REAL,
    median_order_count REAL,
    -- 1 when the price has not moved across the window: an NPC vendor is
    -- holding it. Tradeable and chartable, never an index member.
    price_pinned INTEGER NOT NULL DEFAULT 0,
    -- OK / THIN / BELOW. THIN is carried and badged; BELOW is lookup-only.
    tier TEXT,
    tracked INTEGER NOT NULL DEFAULT 0,
    dropped_at TEXT,
    source TEXT NOT NULL DEFAULT 'census',
    PRIMARY KEY (type_id, region_id)
);

-- Operator-entered watchlist names are NEVER auto-removed (§11 D4).
CREATE TABLE IF NOT EXISTS watchlist (
    name TEXT PRIMARY KEY,
    type_id INTEGER,
    added_at TEXT NOT NULL,
    resolved_at TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS anchors (
    anchor_date TEXT NOT NULL,
    label TEXT NOT NULL,
    scope TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 1,
    source TEXT,
    PRIMARY KEY (anchor_date, label, scope)
);

-- Circuit-breaker state per feed (plan.md §3.3).
CREATE TABLE IF NOT EXISTS feed_health (
    feed TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    opened_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS freight_quotes (
    route TEXT NOT NULL,
    volume_m3 REAL NOT NULL,
    collateral REAL NOT NULL,
    quoted_at TEXT NOT NULL,
    price REAL,
    raw TEXT,
    PRIMARY KEY (route, volume_m3, collateral)
);

-- Killmail destruction counts, reduced on ingest (plan.md §7).
CREATE TABLE IF NOT EXISTS destruction (
    type_id INTEGER NOT NULL,
    region_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    hull_losses INTEGER NOT NULL DEFAULT 0,
    module_losses INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (type_id, region_id, day)
);
CREATE INDEX IF NOT EXISTS idx_destruction_day ON destruction(day);

-- Types listed by /markets/{region}/types that /markets/{region}/history
-- rejects with 404 (measured 2026-08-20: 241 of 17,325 history requests,
-- 1.3%. An earlier note said 16,789; that figure is withdrawn -- plan.md
-- §17 D-10 -- as a circuit-breaker cascade mistaken for data).
-- Remembered so a daily crawl does not spend its 4xx error budget rediscovering
-- the same catalogue gap every morning.
CREATE TABLE IF NOT EXISTS history_missing (
    type_id INTEGER NOT NULL,
    region_id INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_checked TEXT NOT NULL,
    PRIMARY KEY (type_id, region_id)
);

CREATE TABLE IF NOT EXISTS killmail_ingest (
    source TEXT PRIMARY KEY,
    ingested_at TEXT NOT NULL,
    killmail_count INTEGER NOT NULL DEFAULT 0
);
"""

# (table, column, declaration) pairs applied to databases that predate them.
# Append only; never reorder, never remove.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("universe", "median_unit_volume", "REAL"),
    ("universe", "tier", "TEXT"),
    ("universe", "price_pinned", "INTEGER NOT NULL DEFAULT 0"),
)


class Database:
    """Thin sqlite3 wrapper. No ORM; the schema above is the whole model."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        # WAL allows one writer at a time. Without a busy timeout a concurrent
        # writer (a manual sweep during a long crawl) fails instantly with
        # "database is locked" instead of waiting its turn.
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._add_missing_columns()
        self.set_meta("schema_version", str(SCHEMA_VERSION))

    def _add_missing_columns(self) -> None:
        """Additive migration for databases created by an earlier schema.

        `CREATE TABLE IF NOT EXISTS` leaves an existing table exactly as it
        was, so a column added to SCHEMA never reaches a lake the operator
        already built. Rebuilding is not an option — the state database holds
        the paper ledger and the watchlist, and those are not regenerable.
        Adding the column and letting the next census fill it is.
        """
        for table, column, decl in ADDED_COLUMNS:
            existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def checkpoint(self) -> dict:
        """Fold the WAL back into the database file and truncate it.

        A bulk ingest leaves a very large WAL — measured 2026-08-20: a year of
        killmail archives produced a 731 MB WAL beside a 1.3 GB database. The
        space is only reclaimed on checkpoint, so every bulk writer calls this
        when it finishes rather than leaving the operator to discover it.
        """
        row = self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return {
            "busy": row[0] if row else None,
            "wal_pages": row[1] if row else None,
            "checkpointed": row[2] if row else None,
        }

    # -- meta --------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # -- etags -------------------------------------------------------------
    def get_etag(self, url: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM etags WHERE url=?", (url,)).fetchone()

    def put_etag(
        self,
        url: str,
        etag: str | None,
        expires_at: datetime | None,
        last_modified: str | None,
        body_sha: str | None = None,
    ) -> None:
        """Record the response's ETag *and its expiry*.

        A row is written even when the response carried no ETag: the expiry is
        what the never-fetch-before-expiry rule keys off, and dropping it for
        want of an ETag would silently license early polling — the one failure
        mode that gets an account banned.
        """
        self.conn.execute(
            "INSERT INTO etags(url, etag, expires_at, last_modified, fetched_at, body_sha) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET "
            "etag=excluded.etag, expires_at=excluded.expires_at, "
            "last_modified=excluded.last_modified, fetched_at=excluded.fetched_at, "
            "body_sha=COALESCE(excluded.body_sha, etags.body_sha)",
            (url, etag or "", iso(expires_at), last_modified, iso(utcnow()), body_sha),
        )

    def touch_etag_expiry(self, url: str, expires_at: datetime | None) -> None:
        self.conn.execute(
            "UPDATE etags SET expires_at=?, fetched_at=? WHERE url=?",
            (iso(expires_at), iso(utcnow()), url),
        )

    def expires_at(self, url: str) -> datetime | None:
        row = self.get_etag(url)
        return parse_iso(row["expires_at"]) if row else None

    # -- telemetry ---------------------------------------------------------
    def record_request(self, record: dict) -> None:
        self.conn.execute(
            "INSERT INTO sweep_ledger(requested_at, feed, url, status, duration_ms, tokens_used,"
            " tokens_remaining, ratelimit_group, expires_at, error_limit_remain, from_cache, note)"
            " VALUES(:requested_at,:feed,:url,:status,:duration_ms,:tokens_used,:tokens_remaining,"
            ":ratelimit_group,:expires_at,:error_limit_remain,:from_cache,:note)",
            record,
        )

    def ledger_since(self, since: datetime) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM sweep_ledger WHERE requested_at >= ? ORDER BY requested_at",
                (iso(since),),
            )
        )

    # -- feed health -------------------------------------------------------
    def feed_health(self, feed: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM feed_health WHERE feed=?", (feed,)).fetchone()

    def set_feed_health(
        self, feed: str, failures: int, opened_at: datetime | None, last_error: str | None
    ) -> None:
        self.conn.execute(
            "INSERT INTO feed_health(feed, consecutive_failures, opened_at, last_error)"
            " VALUES(?,?,?,?) ON CONFLICT(feed) DO UPDATE SET"
            " consecutive_failures=excluded.consecutive_failures,"
            " opened_at=excluded.opened_at, last_error=excluded.last_error",
            (feed, failures, iso(opened_at), last_error),
        )

    # -- history gaps ------------------------------------------------------
    def mark_history_missing(self, type_ids, region_id: int) -> int:
        stamp = iso(utcnow())
        rows = [(int(value), int(region_id), stamp, stamp) for value in type_ids]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO history_missing(type_id, region_id, first_seen, last_checked)"
            " VALUES(?,?,?,?) ON CONFLICT(type_id, region_id) DO UPDATE SET"
            " last_checked=excluded.last_checked",
            rows,
        )
        return len(rows)

    def history_missing(self, region_id: int) -> set[int]:
        return {
            int(row["type_id"])
            for row in self.conn.execute(
                "SELECT type_id FROM history_missing WHERE region_id=?", (region_id,)
            )
        }

    # -- SDE ---------------------------------------------------------------
    def replace_types(self, rows: Iterable[tuple]) -> int:
        with self.transaction() as conn:
            conn.execute("DELETE FROM sde_types")
            conn.executemany(
                "INSERT INTO sde_types(type_id, name, market_group_id, volume,"
                " packaged_volume, published) VALUES(?,?,?,?,?,?)",
                rows,
            )
            return conn.execute("SELECT COUNT(*) AS n FROM sde_types").fetchone()["n"]

    def replace_market_groups(self, rows: Iterable[tuple]) -> int:
        with self.transaction() as conn:
            conn.execute("DELETE FROM sde_market_groups")
            conn.executemany(
                "INSERT INTO sde_market_groups(market_group_id, parent_group_id, name)"
                " VALUES(?,?,?)",
                rows,
            )
            return conn.execute("SELECT COUNT(*) AS n FROM sde_market_groups").fetchone()["n"]

    def replace_solar_systems(self, rows) -> int:
        with self.transaction() as conn:
            conn.execute("DELETE FROM sde_solar_systems")
            conn.executemany(
                "INSERT INTO sde_solar_systems(solar_system_id, region_id, name) VALUES(?,?,?)",
                rows,
            )
            return conn.execute("SELECT COUNT(*) AS n FROM sde_solar_systems").fetchone()["n"]

    def system_region_map(self) -> dict[int, int]:
        return {
            int(row["solar_system_id"]): int(row["region_id"])
            for row in self.conn.execute("SELECT solar_system_id, region_id FROM sde_solar_systems")
        }

    def type_by_name(self, name: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM sde_types WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()

    def type_by_id(self, type_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sde_types WHERE type_id=?", (type_id,)).fetchone()

    def type_names(self, type_ids: Iterable[int]) -> dict[int, str]:
        ids = list(type_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT type_id, name FROM sde_types WHERE type_id IN ({marks})", ids
        )
        return {row["type_id"]: row["name"] for row in rows}

    def market_group_chain(self, market_group_id: int | None) -> list[int]:
        """Ancestor chain, nearest first. Cycles are cut, never followed."""
        chain: list[int] = []
        seen: set[int] = set()
        current = market_group_id
        while current is not None and current not in seen:
            seen.add(int(current))
            chain.append(int(current))
            row = self.conn.execute(
                "SELECT parent_group_id FROM sde_market_groups WHERE market_group_id=?",
                (int(current),),
            ).fetchone()
            if row is None:
                break
            current = row["parent_group_id"]
        return chain
