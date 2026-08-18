"""Static Data Export loader.

Pulls CCP's official reworked static data (plan.md §0, §3.6) and loads the two
tables v1 needs into SQLite: ``types`` (type_id, English name, market group,
packaged volume) and ``marketGroups`` (the group tree).

The bundle is one ~99 MB zip per build; we download it, extract the two
members with the stdlib, load them, and delete the archive. Refresh is monthly
or on demand — this is not a hot path.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Config
from .paths import atomic_write_path
from .state import StateStore

TYPES_MEMBER = "types.jsonl"
MARKET_GROUPS_MEMBER = "marketGroups.jsonl"


class SdeError(RuntimeError):
    """The SDE could not be fetched or parsed. Never degraded silently."""


@dataclass(frozen=True)
class SdeLoadResult:
    build: str
    release_date: str
    types: int
    market_groups: int


def fetch_manifest(config: Config) -> tuple[str, str]:
    """Return ``(build_number, release_date)`` from the SDE manifest."""
    response = httpx.get(
        config.sde.manifest_url,
        headers={"User-Agent": config.user_agent},
        timeout=config.esi.timeout_seconds,
        follow_redirects=True,
    )
    response.raise_for_status()
    line = response.text.strip().splitlines()[0]
    payload = json.loads(line)
    try:
        return str(payload["buildNumber"]), str(payload["releaseDate"])
    except KeyError as exc:
        raise SdeError(f"SDE manifest is missing {exc.args[0]!r}: {payload!r}") from exc


def download_bundle(config: Config, build: str, target: Path) -> Path:
    """Download the jsonl bundle for ``build`` to ``target``, atomically."""
    url = config.sde.bundle_url_template.format(build=build)
    with (
        atomic_write_path(target) as tmp,
        tmp.open("wb") as handle,
        httpx.stream(
            "GET",
            url,
            headers={"User-Agent": config.user_agent},
            timeout=None,
            follow_redirects=True,
        ) as response,
    ):
        response.raise_for_status()
        for chunk in response.iter_bytes(1 << 20):
            handle.write(chunk)
    return target


def _type_rows(lines: list[bytes]) -> list[tuple]:
    rows = []
    for raw in lines:
        record = json.loads(raw)
        name = record.get("name", {}).get("en")
        if name is None:
            continue
        rows.append(
            (
                int(record["_key"]),
                name,
                int(bool(record.get("published", False))),
                record.get("groupID"),
                record.get("marketGroupID"),
                record.get("volume"),
                record.get("packagedVolume"),
                record.get("portionSize"),
            )
        )
    return rows


def _market_group_rows(lines: list[bytes]) -> list[tuple]:
    rows = []
    for raw in lines:
        record = json.loads(raw)
        name = record.get("name", {}).get("en")
        if name is None:
            continue
        rows.append(
            (
                int(record["_key"]),
                name,
                record.get("parentGroupID"),
                int(bool(record.get("hasTypes", False))),
            )
        )
    return rows


def load_bundle(archive: Path, store: StateStore) -> tuple[int, int]:
    """Load ``types`` and ``marketGroups`` from ``archive`` into SQLite."""
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        missing = {TYPES_MEMBER, MARKET_GROUPS_MEMBER} - names
        if missing:
            raise SdeError(f"SDE bundle {archive.name} is missing {sorted(missing)}")
        with bundle.open(TYPES_MEMBER) as handle:
            type_rows = _type_rows(handle.read().splitlines())
        with bundle.open(MARKET_GROUPS_MEMBER) as handle:
            group_rows = _market_group_rows(handle.read().splitlines())

    store.replace_sde_types(type_rows)
    store.replace_sde_market_groups(group_rows)
    return len(type_rows), len(group_rows)


def refresh(config: Config, store: StateStore, *, force: bool = False) -> SdeLoadResult:
    """Fetch and load the SDE unless the stored snapshot is already current."""
    build, release_date = fetch_manifest(config)
    if not force and store.get_meta("sde_build") == build and store.sde_type_count():
        return SdeLoadResult(
            build=build,
            release_date=release_date,
            types=store.sde_type_count(),
            market_groups=0,
        )

    archive = config.paths.cache_dir / f"sde-{build}-jsonl.zip"
    config.paths.cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        if not archive.exists():
            download_bundle(config, build, archive)
        types, groups = load_bundle(archive, store)
    finally:
        archive.unlink(missing_ok=True)

    store.set_meta("sde_build", build)
    store.set_meta("sde_release_date", release_date)
    return SdeLoadResult(
        build=build, release_date=release_date, types=types, market_groups=groups
    )
