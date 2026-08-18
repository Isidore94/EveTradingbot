import pytest

from evescreener.config import ConfigError, config_key_diff, load_config
from evescreener.paths import REPO_ROOT


def test_config_and_example_key_sets_agree():
    """D2: selftest fails if the real config and the example diverge."""
    config_path = REPO_ROOT / "config.toml"
    if not config_path.exists():
        pytest.skip("config.toml is gitignored and absent in this checkout")
    missing_from_config, missing_from_example = config_key_diff()
    assert not missing_from_config, f"config.toml lacks {sorted(missing_from_config)}"
    assert not missing_from_example, (
        f"config.example.toml lacks {sorted(missing_from_example)}"
    )


def test_missing_config_is_a_loud_error(tmp_path):
    with pytest.raises(ConfigError, match="is missing"):
        load_config(tmp_path / "nope.toml")


def test_user_agent_interpolates_the_version_and_carries_contact(config):
    agent = config.user_agent
    assert "{version}" not in agent
    assert "@" in agent, "CCP requires a descriptive UA with contact details"


def test_sales_tax_at_accounting_v_is_3_375_percent(config):
    assert config.costs.accounting_level == 5
    assert config.costs.sales_tax_rate == pytest.approx(0.03375)


def test_broker_fee_default_is_one_percent(config):
    assert config.costs.broker_fee_rate == pytest.approx(0.01)


def test_notional_tiers_are_the_locked_trio(config):
    assert config.market.notional_tiers_isk == (0.25e9, 1.0e9, 2.5e9)


def test_compatibility_date_is_pinned_not_floated(config):
    assert config.esi.compatibility_date == "2026-08-17"
