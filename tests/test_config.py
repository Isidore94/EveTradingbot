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
