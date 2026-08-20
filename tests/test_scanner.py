"""The scanner: honest zero per setup, UNKNOWN counted, costs travelling."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evescreener.scanner import BUILTIN_SETUP_NAME, render_scan, run_scan
from evescreener.setups import Condition, Setup


def lake(type_ids, *, bars=200, seed=5):
    rng = np.random.default_rng(seed)
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    rows = []
    for offset, type_id in enumerate(type_ids):
        close = 100.0 * (offset + 1) * np.exp(np.cumsum(rng.normal(0.0, 0.02, bars)))
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


@pytest.fixture
def scan_db(db):
    db.replace_market_groups([(4, None, "Ships")])
    db.replace_types([(600 + n, f"Thing {n}", 4, 1.0, 1.0, 1) for n in range(6)])
    return db


def test_a_setup_that_matches_nothing_reports_an_honest_zero(config, scan_db):
    impossible = Setup(
        name="Never",
        conditions=(Condition("change", {"bars": 5, "op": "at_least", "value": 100000.0}),),
    )
    result = run_scan(
        config, scan_db, lake(range(600, 606)), None, pd.DataFrame(), setups=[impossible]
    )
    scan = next(item for item in result.setups if item.name == "Never")
    assert scan.hits == []
    assert scan.examined > 0, "an honest zero needs a denominator"
    assert scan.honest_zero
    assert "Nothing cleared this setup today" in render_scan(result)


def test_the_builtin_setup_always_appears_even_with_no_operator_setups(config, scan_db):
    result = run_scan(config, scan_db, lake(range(600, 603)), None, pd.DataFrame())
    assert [scan.name for scan in result.setups] == [BUILTIN_SETUP_NAME]
    assert result.setups[0].builtin


def test_a_disabled_setup_is_not_run(config, scan_db):
    disabled = Setup(
        name="Off",
        enabled=False,
        conditions=(Condition("change", {"bars": 5, "op": "at_least", "value": -100.0}),),
    )
    result = run_scan(
        config, scan_db, lake(range(600, 603)), None, pd.DataFrame(), setups=[disabled]
    )
    assert "Off" not in {scan.name for scan in result.setups}


def test_unmeasurable_names_are_counted_not_silently_rejected(config, scan_db):
    """A setup nobody can measure must not look like a setup nobody matched."""
    unmeasurable = Setup(
        name="Needs a sector",
        conditions=(Condition("rrs", {"scope": "sector", "op": "at_least", "value": 0.0}),),
    )
    result = run_scan(
        config, scan_db, lake(range(600, 606)), None, pd.DataFrame(), setups=[unmeasurable]
    )
    scan = next(item for item in result.setups if item.name == "Needs a sector")
    assert scan.hits == []
    assert scan.unmeasurable == scan.examined > 0
    assert "unmeasurable" in render_scan(result)


def test_a_hit_carries_its_friction_and_its_staleness(config, scan_db):
    always = Setup(
        name="Always",
        conditions=(Condition("change", {"bars": 5, "op": "at_least", "value": -100.0}),),
    )
    result = run_scan(config, scan_db, lake(range(600, 603)), None, pd.DataFrame(), setups=[always])
    scan = next(item for item in result.setups if item.name == "Always")
    assert scan.hits, "this setup matches anything with five bars of history"
    hit = scan.hits[0]
    assert hit["friction_pct"] is None, "no book means UNKNOWN friction, never a guess"
    assert hit["stale_reason"], "and the reason is printed, not swallowed"
    assert "conditions" in hit and hit["conditions"], "why it fired travels with the hit"


def test_the_thin_badge_reaches_every_hit(config, scan_db):
    scan_db.conn.execute(
        "INSERT INTO universe(type_id, region_id, first_seen, last_seen, tier, tracked, source)"
        " VALUES(600, 10000002, 'x', 'y', 'THIN', 1, 'test')"
    )
    always = Setup(
        name="Always",
        conditions=(Condition("change", {"bars": 5, "op": "at_least", "value": -100.0}),),
    )
    result = run_scan(
        config,
        scan_db,
        lake(range(600, 603)),
        None,
        pd.DataFrame(),
        setups=[always],
        region_id=10000002,
    )
    scan = next(item for item in result.setups if item.name == "Always")
    badges = {hit["type_id"]: hit["badge"] for hit in scan.hits}
    assert badges[600] == "THIN"
    assert badges[601] == ""


def test_the_backtest_banner_reaches_the_scanner_verbatim(config, scan_db):
    from evescreener.backtest import verdict_banner

    verdicts = {"5": {"verdict": "NOT PLAUSIBLE"}, "10": {"verdict": "NOT PLAUSIBLE"}}
    result = run_scan(
        config,
        scan_db,
        lake(range(600, 603)),
        None,
        pd.DataFrame(),
        backtest_verdict=verdicts,
    )
    assert result.banner == verdict_banner(verdicts)
    assert result.banner in render_scan(result)


def test_an_empty_lake_says_so_rather_than_reporting_a_clean_scan(config, scan_db):
    result = run_scan(config, scan_db, pd.DataFrame(), None, pd.DataFrame())
    assert result.evaluated == 0
    assert any("nothing to scan" in note for note in result.notes)


def test_a_setup_reports_its_validation_label(config, scan_db):
    setup = Setup(
        name="Tested",
        conditions=(Condition("change", {"bars": 5, "op": "at_least", "value": -100.0}),),
    )
    result = run_scan(
        config,
        scan_db,
        lake(range(600, 603)),
        None,
        pd.DataFrame(),
        setups=[setup],
        validation={"Tested": "VALIDATED"},
    )
    scan = next(item for item in result.setups if item.name == "Tested")
    assert scan.validation == "VALIDATED"
    assert "VALIDATED" in render_scan(result)
