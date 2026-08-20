"""Shared fixtures. Every test here is offline; live calls carry `network`."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
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


def pytest_configure(config):
    """The desk is tested offscreen — never against a real display."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# -- the desk fixture, shared rather than forked ---------------------------
#
# It lives here so `test_gui` and `test_desk_lifecycle` cannot drift apart on
# what a DeskData looks like.

DESK_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

JITA_44 = 60003760


def desk_lake(type_ids, *, bars=200, seed=3):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    rows = []
    for type_id in type_ids:
        close = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.01, bars)))
        for position, stamp in enumerate(stamps):
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": close[position] * 1.02,
                    "low": close[position] * 0.98,
                    "close": close[position],
                    "volume": 50_000.0,
                    "order_count": 40,
                    "isk_value": close[position] * 50_000.0,
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    return pd.DataFrame(rows)


def desk_book(*, sweep="2026-08-20T11:58:00+00:00", type_ids=(600,)):
    """A swept book carrying R1's executable identity (plan.md §21 R1)."""
    import pandas as pd

    rows = []
    for type_id in type_ids:
        for side, best, fill in (("sell", 105.0, 106.0), ("buy", 95.0, 94.0)):
            row = {
                "type_id": type_id,
                "region_id": 10000002,
                "side": side,
                "sweep_ts": sweep,
                "expires_ts": None,
                "best_price": best,
                "total_volume": 1e9,
                "order_count": 20,
                "p5_price": best,
                "top_order_volume_share": 0.05,
                "station_volume_share": 1.0,
                "partial_sweep": False,
                "best_location_id": JITA_44,
                "best_range": "station" if side == "buy" else None,
                "exec_location_id": JITA_44,
                "exec_price": best,
                "exec_volume": 1e9,
                "exec_order_count": 20,
                "exec_is_structure": False,
            }
            for index in range(3):
                row[f"depth_fill_price_{index}"] = fill if index == 0 else None
                row[f"depth_fill_qty_{index}"] = 10_000_000.0 if index == 0 else None
            rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def desk(config, db):
    """A DeskData built by hand — no disk crawl, no network, no ESI client."""
    from evescreener.gui.data import DeskData

    db.replace_market_groups([(4, None, "Ships"), (100, 4, "Cruisers")])
    db.replace_types([(600 + n, f"Thing {n}", 100, 1.0, 1.0, 1) for n in range(6)])
    frame = desk_lake(range(600, 606))
    db.conn.execute(
        "INSERT INTO universe(type_id, region_id, first_seen, last_seen, tier, tracked,"
        " median_unit_volume, source) VALUES(600, 10000002, 'x', 'y', 'THIN', 1, 400, 't')"
    )
    for type_id in range(601, 606):
        db.conn.execute(
            "INSERT INTO universe(type_id, region_id, first_seen, last_seen, tier, tracked,"
            " median_unit_volume, source) VALUES(?, 10000002, 'x', 'y', 'OK', 1, 50000, 't')",
            (type_id,),
        )
    return DeskData(
        config=config,
        db=db,
        region_id=10000002,
        loaded_at=DESK_NOW,
        bars=frame,
        all_bars=frame,
        book=desk_book(type_ids=range(600, 606)),
        tiers={600: "THIN", **{n: "OK" for n in range(601, 606)}},
    )
