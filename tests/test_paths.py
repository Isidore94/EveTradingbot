import pytest

from evescreener.paths import DATA_DIR_ENV, atomic_write_path, resolve_data_dir


def test_env_override_wins_over_config(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "elsewhere"))
    assert resolve_data_dir("./data") == tmp_path / "elsewhere"


def test_a_relative_config_path_resolves_against_the_repo(monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    resolved = resolve_data_dir("./data")
    assert resolved.is_absolute()
    assert resolved.name == "data"


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path):
    target = tmp_path / "verified.txt"
    target.write_text("last verified output", encoding="utf-8")

    with (
        pytest.raises(RuntimeError, match="publish failed"),
        atomic_write_path(target) as tmp,
    ):
        tmp.write_text("half a file", encoding="utf-8")
        raise RuntimeError("publish failed")

    assert target.read_text(encoding="utf-8") == "last verified output"
    assert list(tmp_path.iterdir()) == [target], "no temp file may be left behind"


def test_a_successful_write_replaces_the_file(tmp_path):
    target = tmp_path / "verified.txt"
    target.write_text("old", encoding="utf-8")
    with atomic_write_path(target) as tmp:
        tmp.write_text("new", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "new"
