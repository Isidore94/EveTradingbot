"""Atomic writes, the data-dir resolver, and the offline selftest."""

from __future__ import annotations

import json
import os
from datetime import date, datetime

import pytest

from evescreener.paths import (
    ENV_DATA_DIR,
    DataPaths,
    append_jsonl,
    atomic_write_bytes,
    atomic_write_text,
    read_jsonl,
    resolve_data_dir,
)
from evescreener.selftest import compatibility_date_check, run_selftest, selftest_report
from evescreener.timeutil import esi_compatibility_today

# -- the failed-publish invariant -------------------------------------------


def test_a_failed_write_never_destroys_the_last_verified_output(tmp_path, monkeypatch):
    target = tmp_path / "report.md"
    atomic_write_text(target, "the last verified output")

    real_replace = os.replace

    def exploding_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", exploding_replace)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "the doomed new version")
    monkeypatch.setattr(os, "replace", real_replace)

    assert target.read_text() == "the last verified output"
    leftovers = [path for path in tmp_path.iterdir() if path.name.startswith(".report.md")]
    assert leftovers == [], "the temp file must be cleaned up on failure"


def test_atomic_write_replaces_in_place(tmp_path):
    target = tmp_path / "a" / "b" / "c.json"
    atomic_write_bytes(target, b'{"n": 1}')
    atomic_write_bytes(target, b'{"n": 2}')
    assert json.loads(target.read_text())["n"] == 2
    assert list(target.parent.iterdir()) == [target]


# -- append-only streams ----------------------------------------------------


def test_jsonl_streams_only_append(tmp_path):
    stream = tmp_path / "decisions.jsonl"
    assert append_jsonl(stream, [{"a": 1}, {"a": 2}]) == 2
    assert append_jsonl(stream, [{"a": 3}]) == 1
    assert [row["a"] for row in read_jsonl(stream)] == [1, 2, 3]


def test_a_missing_stream_is_empty_not_an_error(tmp_path):
    assert read_jsonl(tmp_path / "nope.jsonl") == []


def test_jsonl_survives_non_serializable_values(tmp_path):
    import datetime

    stream = tmp_path / "s.jsonl"
    append_jsonl(stream, [{"at": datetime.datetime(2026, 8, 20)}])
    assert read_jsonl(stream)[0]["at"].startswith("2026-08-20")


# -- data dir ---------------------------------------------------------------


def test_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "override"))
    assert resolve_data_dir("./ignored") == (tmp_path / "override").resolve()


def test_without_the_env_the_config_value_is_used(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    assert resolve_data_dir(tmp_path / "configured") == (tmp_path / "configured").resolve()


def test_ensure_creates_every_directory(tmp_path):
    paths = DataPaths(tmp_path / "lake").ensure()
    for directory in (paths.bars, paths.books, paths.killmails, paths.streams, paths.reports):
        assert directory.is_dir()
    assert paths.bars_partition(10000002, 2026).parent.name == "region=10000002"


# -- selftest ---------------------------------------------------------------


def test_selftest_passes_on_a_coherent_install(config, repo_root, monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    checks = run_selftest(config, repo_root=repo_root)
    failures = [check for check in checks if not check.ok]
    assert failures == [], selftest_report(checks)
    assert len(checks) == 12
    names = {check.name for check in checks}
    assert {
        "membership floors",
        "sector map",
        "setups",
        "reason vocabulary",
        "compatibility date",
    } <= names


def test_config_example_divergence_is_caught_at_LOAD_not_only_by_selftest(repo_root, tmp_path):
    """The strongest guarantee is the loader's, and it fires first.

    `selftest`'s parity check is a second line for a Config assembled some
    other way; a drifted `config.toml` never reaches it, because loading one
    is already a loud error.
    """
    from evescreener.config import ConfigError, load_config

    drifted = tmp_path / "config.toml"
    body = (repo_root / "config.example.toml").read_text()
    drifted.write_text(body.replace("[esi]", "[esi]\nsurprise_key = 1"), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown keys: surprise_key"):
        load_config(drifted)

    missing = tmp_path / "missing.toml"
    missing.write_text(body.replace("timeout_seconds = 30.0", ""), encoding="utf-8")
    with pytest.raises(ConfigError, match="missing keys: timeout_seconds"):
        load_config(missing)


def test_selftest_parity_check_passes_on_a_matching_file(repo_root, tmp_path, monkeypatch):
    from evescreener.config import load_config

    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    live = tmp_path / "config.toml"
    body = (repo_root / "config.example.toml").read_text()
    # `as_posix()` because a Windows tmp_path lands backslashes inside a TOML
    # basic string, where `\U` is an escape sequence and not a drive path.
    data_dir = (tmp_path / "data").as_posix()
    live.write_text(
        body.replace('data_dir = "./data"', f'data_dir = "{data_dir}"'),
        encoding="utf-8",
    )
    checks = run_selftest(load_config(live), repo_root=repo_root)
    parity = next(check for check in checks if check.name == "config parity")
    assert parity.ok
    assert parity.detail == "identical key sets"


def test_selftest_report_counts_passes(config, repo_root):
    text = selftest_report(run_selftest(config, repo_root=repo_root))
    assert "checks passed" in text
    assert text.count("[PASS]") >= 6


# -- the X-Compatibility-Date pin (plan.md §17 D-21) ------------------------
#
# Salvaged from branch `claude/phase-0-gate-checklist-oucoil` (commit a7f5872),
# which measured the failure against live ESI: a pin still in the future on
# CCP's UTC-11 clock is answered with HTTP 400 on every route, so a bad pin is
# not a degraded run — it is a total outage, and it must be caught offline.


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_a_future_pin_fails_because_esi_rejects_it_outright():
    check = compatibility_date_check("2026-08-25", _at("2026-08-20T12:00:00+00:00"))
    assert not check.ok
    assert "Newest safe pin: 2026-08-19" in check.detail


def test_a_pin_on_todays_utc11_date_fails_even_though_esi_would_take_it():
    """One full day of margin, so the UTC-11 rollover cannot break a live run."""
    moment = _at("2026-08-20T12:00:00+00:00")
    assert esi_compatibility_today(moment) == date(2026, 8, 20)
    assert not compatibility_date_check("2026-08-20", moment).ok


def test_the_utc11_clock_is_what_is_measured_not_utc():
    """At 05:00 UTC it is still yesterday at CCP, and the pin must follow."""
    moment = _at("2026-08-20T05:00:00+00:00")
    assert esi_compatibility_today(moment) == date(2026, 8, 19)
    # Safe under a UTC reading of the clock, rejected under the real one.
    assert not compatibility_date_check("2026-08-19", moment).ok
    assert compatibility_date_check("2026-08-18", moment).ok


def test_a_fully_past_pin_passes_and_says_how_far_past():
    check = compatibility_date_check("2026-08-17", _at("2026-08-20T12:00:00+00:00"))
    assert check.ok
    assert "3 day(s) past" in check.detail


def test_a_malformed_pin_is_a_named_failure_not_a_crash():
    check = compatibility_date_check("soon", _at("2026-08-20T12:00:00+00:00"))
    assert not check.ok
    assert "ISO-8601" in check.detail


def test_the_shipped_pin_is_sendable_right_now(config):
    """Guards the committed value itself, on the real clock."""
    check = compatibility_date_check(config.app.compatibility_date)
    assert check.ok, check.detail


def test_checkpoint_truncates_the_wal(paths):
    """Bulk ingest leaves a WAL that only a checkpoint reclaims."""
    from evescreener.store.db import Database

    db = Database(paths.db)
    db.conn.executemany(
        "INSERT INTO destruction(type_id, region_id, day, hull_losses, module_losses)"
        " VALUES(?,?,?,?,?)",
        [(index, 10000002, "2026-08-20", 1, 2) for index in range(5000)],
    )
    wal = paths.db.with_name(paths.db.name + "-wal")
    assert wal.exists() and wal.stat().st_size > 0
    result = db.checkpoint()
    assert result["busy"] == 0
    assert wal.stat().st_size == 0, "a truncating checkpoint must reclaim the space"
    db.close()


def test_the_cost_model_check_follows_the_operator_skills_not_accounting_v(repo_root, tmp_path):
    """It used to hardcode 3.375%, which asserts a skill level, not arithmetic.

    The operator trained Accounting V and Broker Relations IV, so the pinned
    value happened to hold — but anyone who has not trained Accounting to V
    would have seen `selftest` fail on a correct install.
    """
    from evescreener.config import load_config

    body = (repo_root / "config.example.toml").read_text()
    body = body.replace("accounting_level = 5", "accounting_level = 3")
    body = body.replace("broker_relations_level = 5", "broker_relations_level = 2")
    body = body.replace('data_dir = "./data"', f'data_dir = "{(tmp_path / "d").as_posix()}"')
    live = tmp_path / "config.toml"
    live.write_text(body, encoding="utf-8")

    checks = run_selftest(load_config(live), repo_root=repo_root)
    cost = next(check for check in checks if check.name == "cost model")
    assert cost.ok, cost.detail
    # 7.5 x (1 - 0.11 x 3) = 5.025 ; 3.0 - 0.3 x 2 - 0.5 = 1.9
    assert "5.0250%" in cost.detail and "1.9000%" in cost.detail
