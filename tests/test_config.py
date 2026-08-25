"""Config is loud, frozen, and in lockstep with the committed example."""

from __future__ import annotations

import pytest

from evescreener.config import ConfigError, config_from_mapping, key_set, load_example


def test_example_parses_and_is_frozen(repo_root):
    config = config_from_mapping(load_example(repo_root))
    assert config.esi.home_region_id == 10000002
    with pytest.raises((AttributeError, TypeError)):
        config.esi.home_region_id = 1  # frozen dataclass


def test_user_agent_is_descriptive_with_contact(config):
    agent = config.app.user_agent
    assert agent.startswith("EveTradingbot/")
    assert "@" in agent and "github.com" in agent


def test_missing_key_is_a_loud_error(repo_root):
    raw = load_example(repo_root)
    del raw["esi"]["timeout_seconds"]
    with pytest.raises(ConfigError, match="missing keys"):
        config_from_mapping(raw)


def test_unknown_key_is_a_loud_error(repo_root):
    raw = load_example(repo_root)
    raw["esi"]["turbo_mode"] = True
    with pytest.raises(ConfigError, match="unknown keys"):
        config_from_mapping(raw)


def test_example_carries_no_secret(repo_root):
    assert load_example(repo_root)["discord"]["webhook_url"] == ""


def test_key_set_is_flat_and_complete(repo_root):
    keys = key_set(load_example(repo_root))
    assert "app.contact" in keys
    assert "budget.orders_token_self_cap" in keys


def test_self_caps_sit_below_the_hard_limits(config):
    assert config.budget.orders_token_self_cap * 2 == config.budget.orders_token_limit
    assert config.budget.history_requests_per_minute <= 150


def test_watchlist_is_the_locked_fifty(config):
    assert len(config.universe.watchlist) == 50
    assert "PLEX" in config.universe.watchlist
    assert "Tritanium" in config.universe.watchlist


# -- §23: a whole section may be optional, or an operator's config breaks ---


def test_a_config_written_before_the_hauling_track_still_loads(repo_root, tmp_path):
    """The operator's deployed `config.toml` predates `[hauling]` and `[routes]`.

    A section every one of whose fields declares a default carries no
    information when it is absent, so requiring it would reject a config that
    is already correct — the same reasoning §21 R2 applied per field, applied
    per section.
    """
    raw = load_example(repo_root)
    del raw["hauling"]
    del raw["routes"]
    loaded = config_from_mapping(raw)
    assert loaded.hauling.enabled is True
    assert loaded.routes.security_profile == "highsec"
    assert loaded.hauling.hub_station_ids[0] == 60003760


def test_a_section_with_a_required_field_is_still_required(repo_root):
    raw = load_example(repo_root)
    del raw["esi"]
    with pytest.raises(ConfigError, match="missing sections"):
        config_from_mapping(raw)


def test_parity_tolerates_the_new_sections_being_absent(repo_root, tmp_path):
    """`selftest`'s parity check must agree with the loader, or it fails a
    perfectly valid install the moment a new optional setting ships."""
    import tomllib

    from evescreener.config import key_set
    from evescreener.selftest import optional_config_keys

    example_keys = key_set(load_example(repo_root))
    raw = tomllib.loads((repo_root / "config.example.toml").read_text(encoding="utf-8"))
    del raw["hauling"]
    del raw["routes"]
    live_keys = key_set(raw)
    assert not sorted(example_keys - live_keys - optional_config_keys())


def test_the_hub_stations_are_the_five_resolved_from_the_sde(config):
    """Resolved from build 3478781 and checked against their systems, not
    remembered: Jita 60003760, Amarr 60008494, Dodixie 60011866, Rens 60004588,
    Hek 60005686."""
    assert config.hauling.hub_station_ids == (60003760, 60008494, 60011866, 60004588, 60005686)


def test_the_liquidation_assumptions_are_present_and_labelled_as_defaults(config):
    assert config.hauling.destination_share_prior == 0.25
    assert config.hauling.capture_share == (0.05, 0.15, 0.35)
    assert config.hauling.min_liquidity_bars >= 1
