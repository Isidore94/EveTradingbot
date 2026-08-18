"""Shared test scaffolding.

Everything here is offline. Live calls live behind ``@pytest.mark.network``
and are run intentionally, never by the default gate (plan.md §11 D5).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from evescreener.config import _build
from evescreener.paths import REPO_ROOT
from evescreener.state import StateStore

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a recorded fixture, asserting it carries its provenance (D5)."""
    doc = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    provenance = doc["_provenance"]
    for key in ("source_url", "acquired_at", "x_compatibility_date"):
        assert provenance.get(key), f"{name} is missing provenance key {key!r}"
    return doc


@pytest.fixture
def config(tmp_path):
    """A Config built from the committed example, pointed at a temp data dir."""
    with (REPO_ROOT / "config.example.toml").open("rb") as handle:
        raw = tomllib.load(handle)
    raw["storage"]["data_dir"] = str(tmp_path / "data")
    built = _build(raw, REPO_ROOT / "config.example.toml")
    built.paths.ensure()
    return built


@pytest.fixture
def store(config):
    with StateStore(config.paths.state_db) as opened:
        yield opened
