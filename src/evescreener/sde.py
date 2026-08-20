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


class SdeError(RuntimeError):
    """Raised loudly: a broken SDE is never worked around by guessing."""


@dataclass(slots=True)
class SdeLoadResult:
    build: int
    types: int
    market_groups: int
    downloaded: bool
    bundle_path: Path

    def as_dict(self) -> dict:
        return {
            "build": self.build,
            "types": self.types,
            "market_groups": self.market_groups,
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
    db.set_meta("sde_build", str(build))
    return SdeLoadResult(
        build=build,
        types=types,
        market_groups=groups,
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
