"""Atomic writes, the data-dir resolver, and the offline selftest."""

from __future__ import annotations

import json
import os

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
from evescreener.selftest import run_selftest, selftest_report

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
    assert len(checks) == 7


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
    live.write_text(
        body.replace('data_dir = "./data"', f'data_dir = "{tmp_path / "data"}"'),
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
