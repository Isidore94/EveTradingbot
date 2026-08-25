"""The command surface: dispatch, config resolution, and honest exit codes."""

from __future__ import annotations

import sys

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


# -- console encoding (Windows) ---------------------------------------------


def test_the_cli_forces_utf8_because_a_windows_console_defaults_to_cp1252(monkeypatch):
    """A legacy codepage must not be able to kill a finished command.

    `backtest` on the operator's desk computed 125,254 setup instances, wrote
    both report files, and then raised UnicodeEncodeError printing them,
    because cp1252 has no mapping for `→`. Every renderer here emits
    UTF-8; the entry point states that instead of inheriting the locale.
    """
    import io

    from evescreener.cli import _force_utf8_console

    legacy = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", legacy)
    with pytest.raises(UnicodeEncodeError):
        legacy.write("gross → net")
        legacy.flush()

    _force_utf8_console()
    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
    sys.stdout.write("gross → net")  # the character that killed the command
    sys.stdout.flush()


def test_forcing_utf8_tolerates_a_stream_that_cannot_reconfigure(monkeypatch):
    """pytest's capture object has no `reconfigure`; that is not an error."""
    from evescreener.cli import _force_utf8_console

    class Bare:
        pass

    monkeypatch.setattr(sys, "stdout", Bare())
    monkeypatch.setattr(sys, "stderr", Bare())
    _force_utf8_console()


# -- §23: the haul surface --------------------------------------------------


def test_the_haul_command_is_wired_with_its_three_verbs():
    assert "haul" in HANDLERS
    parser = build_parser()
    for verb in ("scan", "profile", "record"):
        assert parser.parse_args(["haul", verb, *_verb_args(verb)]).haul_command == verb


def _verb_args(verb: str) -> list[str]:
    return {
        "scan": ["--cargo", "60000"],
        "profile": ["list"],
        "record": ["report"],
    }[verb]


def test_haul_requires_a_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["haul"])


def test_a_haul_scan_with_no_ship_refuses_rather_than_guessing_a_hold(
    tmp_path, monkeypatch, capsys
):
    """Cargo is what caps the size. A guessed hold ranks plans you cannot carry."""
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "haul", "scan"]) == 2
    assert "give --cargo or --ship" in capsys.readouterr().err


def test_a_haul_scan_on_an_empty_lake_is_an_honest_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "haul", "scan", "--cargo", "60000", "--no-write"]) == 0
    out = capsys.readouterr().out
    assert "Nothing clears costs today" in out
    assert "STALE_BOOK" in out, "the refusals are the denominator, and they are printed"


def test_a_haul_scan_writes_an_immutable_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "haul", "scan", "--cargo", "60000"]) == 0
    reports = sorted((tmp_path / "data" / "reports").glob("hauling-*.json"))
    assert len(reports) == 1
    assert "written:" in capsys.readouterr().out


def test_ship_profiles_round_trip_through_the_state_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert (
        main(
            ["--example-config", "haul", "profile", "add", "--name", "Bestower", "--cargo", "6000"]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["--example-config", "haul", "profile", "list"]) == 0
    listed = capsys.readouterr().out
    assert "Bestower" in listed
    # An omitted flag stores the configured default, never a NULL that would
    # read back as an instantaneous jump.
    assert '"seconds_per_jump": 55.0' in listed
    assert main(["--example-config", "haul", "profile", "remove", "--name", "Bestower"]) == 0
    assert main(["--example-config", "haul", "profile", "remove", "--name", "Bestower"]) == 1


def test_a_scan_naming_an_unknown_ship_says_which_ones_exist(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "haul", "scan", "--ship", "Charon"]) == 2
    assert "no ship profile named 'Charon'" in capsys.readouterr().err


def test_an_unresolvable_origin_system_is_a_loud_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    code = main(["--example-config", "haul", "scan", "--cargo", "1", "--from", "Jitta"])
    assert code == 2
    assert "no solar system named 'Jitta'" in capsys.readouterr().err


def test_a_refused_haul_record_exits_three_and_says_it_was_recorded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    code = main(
        [
            "--example-config",
            "haul",
            "record",
            "open",
            "--type-id",
            "34",
            "--quantity",
            "1200",
            "--thesis",
            "the Amarr bid is over the Jita ask",
        ]
    )
    assert code == 3
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err
    assert "recorded in the ledger" in captured.out
    assert (tmp_path / "data" / "streams" / "paper_hauls.jsonl").exists()


def test_the_haul_tally_leads_with_refusals(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "haul", "record", "report"]) == 0
    out = capsys.readouterr().out
    assert "refused: **0**" in out
    assert "UNKNOWN" in out


def test_hauling_history_is_an_honest_zero_before_the_hubs_are_swept(tmp_path, monkeypatch, capsys):
    """The exit happens in the destination region, whose history this system
    has never fetched — but there is nothing to fetch until a hub book exists."""
    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    assert main(["--example-config", "ingest-history", "--scope", "hauling"]) == 0
    assert "sweep-books --secondary" in capsys.readouterr().out


def test_the_hauling_history_scope_is_bounded_per_region_and_says_so(tmp_path, monkeypatch):
    """A cap that is not reported reads as 'we fetched everything'."""
    import pandas as pd

    from evescreener.cli import _hauling_history_scope, resolve_config
    from evescreener.store.db import Database
    from evescreener.store.lake import DEPTH_COLUMNS, DepthLake

    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    args = build_parser().parse_args(["--example-config", "ingest-history"])
    config = resolve_config(args)
    config.paths.ensure()
    with Database(config.paths.db) as db:
        db.replace_solar_systems([(30002187, 10000043, "Amarr", 0.9)])
        db.replace_npc_stations([(60008494, 30002187, 1, 1, None)])
        rows = [
            {
                "region_id": 10000043,
                "sweep_ts": "2026-08-25T11:00:00+00:00",
                "fetched_at": "2026-08-25T11:00:00+00:00",
                "expires_ts": None,
                "execution_location_id": 60008494,
                "type_id": type_id,
                "side": "buy",
                "price": 100.0,
                "level_qty": 10.0,
                "cumulative_qty": 10.0,
                "cumulative_notional": 1000.0 * type_id,
                "level_order_count": 1,
                "min_volume_excluded_qty": 0.0,
                "oldest_issued": None,
                "newest_issued": None,
                "structure_share": 0.0,
                "depth_complete": True,
            }
            for type_id in range(1, 11)
        ]
        DepthLake(config.paths).write(pd.DataFrame(rows, columns=DEPTH_COLUMNS))
        scope = _hauling_history_scope(config, db, 3)
    assert list(scope) == [10000043]
    assert len(scope[10000043]) == 3, "the bound binds"
    assert scope[10000043][0] == 10, "the deepest bid books come first"
