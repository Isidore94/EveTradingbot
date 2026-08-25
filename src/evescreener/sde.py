"""SDE loader: `types` and `marketGroups` into SQLite (plan.md §3.6).

CCP's reworked static data ships as one jsonl bundle per build. We read
exactly two members out of it — `types.jsonl` (type_id, English name, market
group, packaged volume for freight) and `marketGroups.jsonl` (the parent
chain) — and drop the rest on the floor. The build number is the cache key:
re-running against the same build is a no-op.

The market-group tree is what replaces the equity sector/industry ETF map: an
N-level walk to the nearest ancestor with enough tracked members (plan.md §6).
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Config
from .store.db import Database

TYPES_MEMBER = "types.jsonl"
MARKET_GROUPS_MEMBER = "marketGroups.jsonl"
SOLAR_SYSTEMS_MEMBER = "mapSolarSystems.jsonl"
STARGATES_MEMBER = "mapStargates.jsonl"
NPC_STATIONS_MEMBER = "npcStations.jsonl"


class SdeError(RuntimeError):
    """Raised loudly: a broken SDE is never worked around by guessing."""


@dataclass(slots=True)
class SdeLoadResult:
    build: int
    types: int
    market_groups: int
    solar_systems: int
    downloaded: bool
    bundle_path: Path
    stargates: int = 0
    npc_stations: int = 0
    unresolved_stargates: int = 0

    def as_dict(self) -> dict:
        return {
            "build": self.build,
            "types": self.types,
            "market_groups": self.market_groups,
            "solar_systems": self.solar_systems,
            "stargates": self.stargates,
            "npc_stations": self.npc_stations,
            "unresolved_stargates": self.unresolved_stargates,
            "downloaded": self.downloaded,
            "bundle_path": str(self.bundle_path),
        }


def _english(value) -> str:
    """SDE names are localized maps; English is the operator's language."""
    if isinstance(value, dict):
        return str(value.get("en") or next(iter(value.values()), ""))
    return str(value or "")


def latest_build(config: Config, client: httpx.Client | None = None) -> int:
    owns = client is None
    client = client or httpx.Client(headers=config.headers, timeout=config.esi.timeout_seconds)
    try:
        response = client.get(config.sde.latest_url)
        response.raise_for_status()
        first = response.text.strip().splitlines()[0]
        build = json.loads(first).get("buildNumber")
    finally:
        if owns:
            client.close()
    if not build:
        raise SdeError(f"no buildNumber at {config.sde.latest_url}")
    return int(build)


def download_bundle(
    config: Config, build: int, destination: Path, client: httpx.Client | None = None
) -> Path:
    """Stream the build's jsonl bundle to disk. An existing bundle is reused."""
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"sde-{build}-jsonl.zip"
    if target.exists() and target.stat().st_size > 0:
        return target
    url = config.sde.bundle_url_template.format(build=build)
    owns = client is None
    client = client or httpx.Client(headers=config.headers, timeout=300.0, follow_redirects=True)
    partial = target.with_suffix(".partial")
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(chunk_size=1 << 20):
                    stream.write(chunk)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        if owns:
            client.close()
    partial.replace(target)
    return target


def parse_types(bundle: Path) -> list[tuple]:
    rows: list[tuple] = []
    with zipfile.ZipFile(bundle) as archive, archive.open(TYPES_MEMBER) as member:
        for line in member:
            record = json.loads(line)
            type_id = record.get("_key")
            if type_id is None:
                continue
            rows.append(
                (
                    int(type_id),
                    _english(record.get("name")),
                    record.get("marketGroupID"),
                    record.get("volume"),
                    record.get("packagedVolume") or record.get("volume"),
                    1 if record.get("published") else 0,
                )
            )
    return rows


def parse_market_groups(bundle: Path) -> list[tuple]:
    rows: list[tuple] = []
    with zipfile.ZipFile(bundle) as archive, archive.open(MARKET_GROUPS_MEMBER) as member:
        for line in member:
            record = json.loads(line)
            group_id = record.get("_key")
            if group_id is None:
                continue
            rows.append((int(group_id), record.get("parentGroupID"), _english(record.get("name"))))
    return rows


def parse_solar_systems(bundle: Path) -> list[tuple]:
    """Solar system -> region, name and RAW security.

    This is what turns a killmail into demand data, and — since H1a — what
    decides whether a route is high-sec. The security stored is the raw float;
    the displayed value that actually decides high-sec is computed once, in
    `routes.display_security` (plan.md §23.9).
    """
    rows: list[tuple] = []
    with zipfile.ZipFile(bundle) as archive, archive.open(SOLAR_SYSTEMS_MEMBER) as member:
        for line in member:
            record = json.loads(line)
            system_id = record.get("_key")
            region_id = record.get("regionID")
            if system_id is None or region_id is None:
                continue
            security = record.get("securityStatus")
            rows.append(
                (
                    int(system_id),
                    int(region_id),
                    _english(record.get("name")),
                    float(security) if security is not None else None,
                )
            )
    return rows


def _destination_system(record: dict) -> int | None:
    """The system on the far side of one stargate.

    Verified against build 3478781: `destination` is an object carrying both
    `solarSystemID` and `stargateID`. An integer form is accepted too and
    resolved through the gate index, because a schema this system does not own
    can change shape — but anything else is left **unresolved and counted**
    rather than guessed into an edge that does not exist.
    """
    destination = record.get("destination")
    if isinstance(destination, dict):
        system = destination.get("solarSystemID")
        return int(system) if system is not None else None
    return None


def parse_stargates(bundle: Path) -> tuple[list[tuple], int]:
    """The gate graph: `(stargate_id, system_id, destination_system_id)`.

    Returns the rows and the count of gates whose destination could not be
    resolved. A gate that cannot say where it goes is not an edge.
    """
    raw: list[dict] = []
    with zipfile.ZipFile(bundle) as archive, archive.open(STARGATES_MEMBER) as member:
        for line in member:
            record = json.loads(line)
            if record.get("_key") is not None and record.get("solarSystemID") is not None:
                raw.append(record)

    system_of_gate = {int(record["_key"]): int(record["solarSystemID"]) for record in raw}
    rows: list[tuple] = []
    unresolved = 0
    for record in raw:
        destination = _destination_system(record)
        if destination is None:
            # Integer destinations name the far *gate*; resolve it through the
            # index rather than dropping the edge.
            far = record.get("destination")
            if isinstance(far, int):
                destination = system_of_gate.get(int(far))
        if destination is None:
            unresolved += 1
            continue
        rows.append((int(record["_key"]), int(record["solarSystemID"]), int(destination)))
    if raw and not rows:
        raise SdeError(
            f"{STARGATES_MEMBER} yielded no resolvable edges from {len(raw)} gates — "
            "the destination field has changed shape and routing would be silently empty"
        )
    return rows, unresolved


def parse_npc_stations(bundle: Path) -> list[tuple]:
    """NPC stations: `(station_id, system_id, owner_id, operation_id, name)`.

    **There is no name field in `npcStations.jsonl`** (verified against build
    3478781: the record carries `solarSystemID`, `ownerID`, `operationID`,
    `typeID` and geometry, and nothing else that names it). The name column is
    therefore written NULL and the desk renders "<system> — station <id>". A
    plausible-looking guessed name is worse than an id, because an id can be
    checked in the client.
    """
    rows: list[tuple] = []
    with zipfile.ZipFile(bundle) as archive, archive.open(NPC_STATIONS_MEMBER) as member:
        for line in member:
            record = json.loads(line)
            station_id = record.get("_key")
            system_id = record.get("solarSystemID")
            if station_id is None or system_id is None:
                continue
            name = record.get("name")
            rows.append(
                (
                    int(station_id),
                    int(system_id),
                    record.get("ownerID"),
                    record.get("operationID"),
                    _english(name) if name else None,
                )
            )
    return rows


def load_sde(
    config: Config,
    db: Database,
    *,
    force: bool = False,
    bundle_path: Path | None = None,
    client: httpx.Client | None = None,
) -> SdeLoadResult:
    """Refresh the SDE tables. Same build + not forced = a cheap no-op."""
    if bundle_path is not None:
        # A locally-supplied bundle names its own build; the filename is the
        # only provenance we have, so a nameless bundle is recorded as 0.
        match = re.search(r"(\d{5,})", bundle_path.name)
        build = int(match.group(1)) if match else int(db.get_meta("sde_build") or 0)
        bundle = bundle_path
        downloaded = False
    else:
        build = latest_build(config, client=client)
        stored = db.get_meta("sde_build")
        if stored and int(stored) == build and not force:
            return SdeLoadResult(
                build=build,
                types=db.conn.execute("SELECT COUNT(*) AS n FROM sde_types").fetchone()["n"],
                market_groups=db.conn.execute(
                    "SELECT COUNT(*) AS n FROM sde_market_groups"
                ).fetchone()["n"],
                solar_systems=db.conn.execute(
                    "SELECT COUNT(*) AS n FROM sde_solar_systems"
                ).fetchone()["n"],
                stargates=db.conn.execute("SELECT COUNT(*) AS n FROM sde_stargates").fetchone()[
                    "n"
                ],
                npc_stations=db.conn.execute(
                    "SELECT COUNT(*) AS n FROM sde_npc_stations"
                ).fetchone()["n"],
                downloaded=False,
                bundle_path=config.paths.sde / f"sde-{build}-jsonl.zip",
            )
        bundle = download_bundle(config, build, config.paths.ensure().sde, client=client)
        downloaded = True

    type_rows = parse_types(bundle)
    group_rows = parse_market_groups(bundle)
    if not type_rows or not group_rows:
        raise SdeError(f"SDE bundle {bundle} yielded no types or no market groups")
    types = db.replace_types(type_rows)
    groups = db.replace_market_groups(group_rows)
    systems = db.replace_solar_systems(parse_solar_systems(bundle))
    stargate_rows, unresolved = parse_stargates(bundle)
    stargates = db.replace_stargates(stargate_rows)
    stations = db.replace_npc_stations(parse_npc_stations(bundle))
    db.set_meta("sde_build", str(build))
    return SdeLoadResult(
        build=build,
        types=types,
        market_groups=groups,
        solar_systems=systems,
        stargates=stargates,
        npc_stations=stations,
        unresolved_stargates=unresolved,
        downloaded=downloaded,
        bundle_path=bundle,
    )


def resolve_watchlist(db: Database, names: list[str]) -> tuple[dict[str, int], list[str]]:
    """Resolve operator names to type_ids.

    An unresolvable name is returned for a **loud error**, never a silent skip
    — names drift across patches and a quietly missing hull is a quietly
    missing thesis (plan.md §11 D4).
    """
    resolved: dict[str, int] = {}
    unresolved: list[str] = []
    for name in names:
        row = db.type_by_name(name)
        if row is None:
            unresolved.append(name)
        else:
            resolved[name] = int(row["type_id"])
    return resolved, unresolved


def cohort_scope(db: Database, type_id: int, member_counts: dict[int, int], min_members: int):
    """Nearest market-group ancestor with at least `min_members` tracked types.

    Returns `(market_group_id, member_count)` or `None`. An unresolvable scope
    returns None and the caller drops the read as UNKNOWN — it never silently
    substitutes the market-wide composite (the upstream `"SPY"` fallback bug is
    explicitly not ported; plan.md §6).
    """
    row = db.type_by_id(type_id)
    if row is None or row["market_group_id"] is None:
        return None
    for group_id in db.market_group_chain(int(row["market_group_id"])):
        count = member_counts.get(group_id, 0)
        if count >= min_members:
            return group_id, count
    return None


def market_group_members(db: Database, type_ids: list[int]) -> dict[int, list[int]]:
    """Map every ancestor market group to the tracked types beneath it."""
    members: dict[int, list[int]] = {}
    for type_id in type_ids:
        row = db.type_by_id(type_id)
        if row is None or row["market_group_id"] is None:
            continue
        for group_id in db.market_group_chain(int(row["market_group_id"])):
            members.setdefault(group_id, []).append(type_id)
    return members
