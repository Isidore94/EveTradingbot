"""Shared fixtures. Every test here is offline; live calls carry `network`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evescreener.config import Config, config_from_mapping, load_example
from evescreener.paths import DataPaths
from evescreener.store.db import Database

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]


def load_fixture(name: str):
    with (FIXTURES / name).open("r", encoding="utf-8") as stream:
        return json.load(stream)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def config(tmp_path: Path) -> Config:
    raw = load_example(REPO_ROOT)
    raw["app"]["data_dir"] = str(tmp_path / "data")
    return config_from_mapping(raw)


@pytest.fixture
def paths(config: Config, monkeypatch) -> DataPaths:
    monkeypatch.delenv("EVESCREENER_DATA_DIR", raising=False)
    return config.paths.ensure()


@pytest.fixture
def db(paths: DataPaths):
    database = Database(paths.db)
    yield database
    database.close()
