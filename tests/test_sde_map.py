"""The map the router reads, and the migration that reaches a live database.

Three things are proved here.

**The parsers read the shapes the bundle really has.** Verified against build
3478781: `mapStargates` carries `destination` as an object with
`solarSystemID`; `mapSolarSystems` carries `securityStatus`; and
`npcStations` carries **no name field at all**, which is why a station with no
operator-supplied name renders as its system and id rather than as a guess.

**The migration is additive and reaches the operator's deployed lake.**
`CREATE TABLE IF NOT EXISTS` leaves an existing table exactly as it was, so a
new column never arrives on a database that already exists. The state database
holds the paper ledger and the watchlist and is not regenerable, so the
migration adds columns and lets the next `sde` run fill them — and a test
drives it against a database built with the **old** schema.

**A stargate that cannot say where it goes is not an edge.** It is counted, and
if every gate is unresolvable the load fails loudly rather than producing a
router with an empty map that answers "no route" to everything.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from evescreener.sde import (
    SdeError,
    load_sde,
    parse_npc_stations,
    parse_solar_systems,
    parse_stargates,
)
from evescreener.store.db import Database

# Record shapes verified against the live bundle, build 3478781 (2026-08-25).
TYPES = [
    {
        "_key": 34,
        "name": {"en": "Tritanium"},
        "marketGroupID": 18,
        "volume": 0.01,
        "published": True,
    }
]
GROUPS = [{"_key": 18, "parentGroupID": None, "name": {"en": "Minerals"}}]
SYSTEMS = [
    {"_key": 30000142, "regionID": 10000002, "name": {"en": "Jita"}, "securityStatus": 0.945913},
    {"_key": 30000144, "regionID": 10000002, "name": {"en": "Perimeter"}, "securityStatus": 0.95},
    {"_key": 30005196, "regionID": 10000036, "name": {"en": "Ahbazon"}, "securityStatus": 0.449},
]
STARGATES = [
    {
        "_key": 50000056,
        "solarSystemID": 30000142,
        "destination": {"solarSystemID": 30000144, "stargateID": 50000057},
    },
    {
        "_key": 50000057,
        "solarSystemID": 30000144,
        "destination": {"solarSystemID": 30000142, "stargateID": 50000056},
    },
    {"_key": 50000058, "solarSystemID": 30005196, "destination": None},
]
STATIONS = [
    {"_key": 60003760, "solarSystemID": 30000142, "ownerID": 1000035, "operationID": 14},
    {"_key": 60003761, "solarSystemID": 30000144, "ownerID": 1000003, "operationID": 5},
]


def _bundle(tmp_path: Path, *, stargates=None) -> Path:
    path = tmp_path / "sde-3478781-jsonl.zip"
    members = {
        "types.jsonl": TYPES,
        "marketGroups.jsonl": GROUPS,
        "mapSolarSystems.jsonl": SYSTEMS,
        "mapStargates.jsonl": STARGATES if stargates is None else stargates,
        "npcStations.jsonl": STATIONS,
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, records in members.items():
            archive.writestr(name, "\n".join(json.dumps(record) for record in records))
    return path


# -- 1. the parsers ---------------------------------------------------------


def test_solar_systems_carry_their_raw_security(tmp_path):
    rows = parse_solar_systems(_bundle(tmp_path))
    by_id = {row[0]: row for row in rows}
    assert by_id[30000142][1] == 10000002
    assert by_id[30000142][2] == "Jita"
    assert by_id[30005196][3] == pytest.approx(0.449)


def test_stargates_become_system_edges_and_the_unresolvable_one_is_counted(tmp_path):
    rows, unresolved = parse_stargates(_bundle(tmp_path))
    assert unresolved == 1, "a gate with no destination is not an edge"
    assert sorted((row[1], row[2]) for row in rows) == [
        (30000142, 30000144),
        (30000144, 30000142),
    ]


def test_an_integer_destination_is_resolved_through_the_gate_index(tmp_path):
    """The shape is verified as an object today; an int naming the far gate is
    accepted rather than silently dropping every edge if CCP changes it."""
    integer_form = [
        {"_key": 1, "solarSystemID": 100, "destination": 2},
        {"_key": 2, "solarSystemID": 200, "destination": 1},
    ]
    rows, unresolved = parse_stargates(_bundle(tmp_path, stargates=integer_form))
    assert unresolved == 0
    assert sorted((row[1], row[2]) for row in rows) == [(100, 200), (200, 100)]


def test_a_bundle_whose_gates_all_fail_to_resolve_is_a_loud_error(tmp_path):
    """An empty map answers "no route" to everything, which looks like data."""
    broken = [{"_key": 1, "solarSystemID": 100, "destination": "elsewhere"}]
    with pytest.raises(SdeError, match="no resolvable edges"):
        parse_stargates(_bundle(tmp_path, stargates=broken))


def test_npc_stations_store_ids_because_there_is_no_name_to_store(tmp_path):
    rows = parse_npc_stations(_bundle(tmp_path))
    station = {row[0]: row for row in rows}[60003760]
    assert station[1] == 30000142
    assert station[2] == 1000035
    assert station[4] is None, "npcStations.jsonl has no name field; never invent one"


# -- 2. the load, end to end ------------------------------------------------


def test_loading_the_bundle_fills_the_map_tables(tmp_path, db):
    result = load_sde(None, db, bundle_path=_bundle(tmp_path))
    assert result.build == 3478781
    assert result.solar_systems == 3
    assert result.stargates == 2
    assert result.npc_stations == 2
    assert result.unresolved_stargates == 1
    assert db.system_security()[30005196] == pytest.approx(0.449)
    assert sorted(db.stargate_edges()) == [(30000142, 30000144), (30000144, 30000142)]
    assert db.station_systems()[60003760] == 30000142


def test_the_router_can_be_built_straight_off_the_database(tmp_path, db):
    from evescreener.routes import RouteGraph

    load_sde(None, db, bundle_path=_bundle(tmp_path))
    graph = RouteGraph.from_db(db)
    assert graph.sde_build == 3478781
    assert graph.route(30000142, 30000144).jumps == 1
    assert graph.is_highsec(30005196) is False, "0.449 displays as 0.4"


# -- 3. the migration, against a database built by the old schema -----------

OLD_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sde_solar_systems (
    solar_system_id INTEGER PRIMARY KEY,
    region_id INTEGER NOT NULL,
    name TEXT NOT NULL
);
CREATE TABLE watchlist (
    name TEXT PRIMARY KEY, type_id INTEGER, added_at TEXT NOT NULL,
    resolved_at TEXT, note TEXT
);
"""


def test_an_existing_database_gains_the_new_column_without_losing_a_row(tmp_path):
    path = tmp_path / "state.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(OLD_SCHEMA)
    legacy.execute("INSERT INTO sde_solar_systems VALUES (30000142, 10000002, 'Jita')")
    legacy.execute("INSERT INTO watchlist(name, added_at) VALUES ('PLEX', 'yesterday')")
    legacy.commit()
    legacy.close()

    with Database(path) as db:
        columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(sde_solar_systems)")}
        assert "security_status" in columns
        rows = list(db.conn.execute("SELECT * FROM sde_solar_systems"))
        assert len(rows) == 1 and rows[0]["name"] == "Jita"
        # An unfilled security is NULL, and NULL is UNKNOWN — not zero, which
        # would read as null-sec on a system that is nothing of the kind.
        assert rows[0]["security_status"] is None
        assert db.system_security() == {}
        # The tables that hold operator-entered, non-regenerable data survive.
        assert [row["name"] for row in db.conn.execute("SELECT name FROM watchlist")] == ["PLEX"]
        # New tables are created normally rather than migrated into existence.
        assert db.stargate_edges() == []
        assert db.station_systems() == {}


def test_the_migration_is_idempotent(tmp_path):
    path = tmp_path / "state.db"
    for _ in range(3):
        with Database(path) as db:
            assert db.get_meta("schema_version") == "3"
