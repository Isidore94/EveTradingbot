"""The command surface: dispatch, config resolution, and honest exit codes."""

from __future__ import annotations

import pytest

from evescreener.cli import HANDLERS, build_parser, main


def test_every_documented_subcommand_is_wired():
    """plan.md §11 D1 plus the §17 D-5 additions. A listed command must dispatch."""
    expected = {
        "daemon",
        "ingest-history",
        "sweep-books",
        "census",
        "digest",
        "paper",
        "backtest",
        "killmails",
        "selftest",
        # §17 D-5 additions
        "cross-region",
        "report",
        "sde",
        "anchors",
    }
    assert expected <= set(HANDLERS)


def test_the_parser_requires_a_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_paper_requires_a_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["paper"])


def test_paper_open_requires_a_thesis_and_a_setup_tag():
    """A pick with no stated reason is not a decision worth recording."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["paper", "open", "--type-id", "34"])
    with pytest.raises(SystemExit):
        # A thesis without a setup tag is still an unattributable trade.
        build_parser().parse_args(
            ["paper", "open", "--type-id", "34", "--thesis", "dip below value"]
        )
    args = build_parser().parse_args(
        [
            "paper",
            "open",
            "--type-id",
            "34",
            "--thesis",
            "dip below value",
            "--setup",
            "discretionary",
            "--like",
            "clean_dip_below_value",
        ]
    )
    assert args.thesis == "dip below value"
    assert args.setup == "discretionary"
    assert args.like == ["clean_dip_below_value"]


def test_a_pass_is_a_recorded_decision_with_its_own_reasons():
    args = build_parser().parse_args(
        [
            "paper",
            "pass",
            "--type-id",
            "34",
            "--action",
            "not_today",
            "--dislike",
            "spread_too_wide",
        ]
    )
    assert args.action == "not_today"
    assert args.dislike == ["spread_too_wide"]


def test_a_missing_config_is_a_clean_exit_code_not_a_traceback(tmp_path, capsys):
    code = main(["--config", str(tmp_path / "nope.toml"), "selftest"])
    assert code == 2
    assert "config error" in capsys.readouterr().err


def test_selftest_runs_against_the_committed_example(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "selftest"]) == 0
    assert "checks passed" in capsys.readouterr().out


def test_region_override_is_accepted_everywhere():
    args = build_parser().parse_args(["--region", "10000043", "census"])
    assert args.region == 10000043


def test_anchors_list_needs_no_network(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "anchors", "--list"]) == 0
    out = capsys.readouterr().out
    assert "CANDIDATE" in out
    assert "confirmed" not in out.replace("CANDIDATE", "")


def test_report_runs_with_nothing_measured(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "report"]) == 0
    out = capsys.readouterr().out
    assert "Not enough has been measured" in out
    assert out.count("**UNKNOWN**") == 5


def test_ingest_history_with_an_empty_universe_is_an_honest_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "ingest-history"]) == 0
    assert "honest zero, not an error" in capsys.readouterr().out


def test_paper_report_on_an_empty_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "paper", "report"]) == 0
    out = capsys.readouterr().out
    assert "TOO_EARLY" in out
    assert "**0** decisions were refused" in out


def test_a_refused_paper_open_exits_three_and_says_it_was_recorded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    code = main(
        [
            "--example-config",
            "paper",
            "open",
            "--type-id",
            "34",
            "--thesis",
            "no book",
            "--setup",
            "discretionary",
            "--like",
            "clean_dip_below_value",
        ]
    )
    assert code == 3
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err
    assert "a result, not a crash" in captured.out


def test_paper_open_accepts_a_name_instead_of_a_type_id():
    args = build_parser().parse_args(
        [
            "paper",
            "open",
            "--name",
            "Tritanium",
            "--thesis",
            "dip",
            "--setup",
            "discretionary",
        ]
    )
    assert args.name == "Tritanium"
    assert args.type_id is None


def test_paper_open_with_neither_id_nor_name_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    code = main(
        [
            "--example-config",
            "paper",
            "open",
            "--thesis",
            "dip",
            "--setup",
            "discretionary",
        ]
    )
    assert code == 2
    assert "must name what it is buying" in capsys.readouterr().err


def test_an_unresolvable_name_is_a_loud_error_not_a_guess(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    code = main(
        [
            "--example-config",
            "paper",
            "open",
            "--name",
            "Rifter Mk III",
            "--thesis",
            "x",
            "--setup",
            "discretionary",
        ]
    )
    assert code == 2
    assert "no type named 'Rifter Mk III'" in capsys.readouterr().err
