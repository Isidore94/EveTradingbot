from evescreener.__main__ import main


def test_daemon_says_it_belongs_to_a_later_phase(capsys):
    assert main(["daemon"]) == 2
    assert "Phase 1" in capsys.readouterr().err


def test_census_says_it_belongs_to_a_later_phase(capsys):
    assert main(["census"]) == 2
    assert "Phase 1" in capsys.readouterr().err


def test_phase_guards_run_before_any_config_is_touched(monkeypatch, capsys):
    def explode(*args, **kwargs):
        raise AssertionError("config must not be loaded for a Phase 1 subcommand")

    monkeypatch.setattr("evescreener.__main__.load_config", explode)
    assert main(["census"]) == 2
    capsys.readouterr()
