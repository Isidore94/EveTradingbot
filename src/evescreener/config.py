"""Configuration: stdlib ``tomllib`` into frozen dataclasses (plan.md §11 D1).

No pydantic, no ORM, no settings framework. ``config.toml`` is gitignored and
mirrored by the committed ``config.example.toml``; ``selftest`` fails if their
key sets diverge (D2).
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .clock import parse_hhmm
from .paths import REPO_ROOT, DataPaths, resolve_data_dir

CONFIG_NAME = "config.toml"
EXAMPLE_NAME = "config.example.toml"


class ConfigError(RuntimeError):
    """Configuration is missing, malformed, or diverges from the example."""


@dataclass(frozen=True)
class EsiConfig:
    base_url: str
    user_agent: str
    compatibility_date: str
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    jitter_seconds: float
    orders_concurrency: int


@dataclass(frozen=True)
class BudgetConfig:
    orders_tokens_per_window: int
    orders_token_hard_stop: int
    history_requests_per_minute: int
    error_limit_stop_seconds: int


@dataclass(frozen=True)
class MarketConfig:
    region_id: int
    notional_tiers_isk: tuple[float, ...]
    book_staleness_minutes: int


@dataclass(frozen=True)
class ScheduleConfig:
    digest_utc: dt.time
    history_job_utc: dt.time
    book_hot_start_utc: dt.time
    book_hot_end_utc: dt.time
    book_cold_interval_minutes: int


@dataclass(frozen=True)
class CostConfig:
    accounting_level: int
    broker_relations_level: int
    sales_tax_base_pct: float
    broker_fee_effective_pct: float

    @property
    def sales_tax_rate(self) -> float:
        """Effective sales tax as a fraction: 7.5% * (1 - 0.11 * Accounting)."""
        base = self.sales_tax_base_pct / 100.0
        return base * (1.0 - 0.11 * self.accounting_level)

    @property
    def broker_fee_rate(self) -> float:
        """Effective broker fee as a fraction. Posting/modifying only (§5)."""
        return self.broker_fee_effective_pct / 100.0


@dataclass(frozen=True)
class LiquidityConfig:
    min_median_isk_value_30d: float
    min_median_order_count_30d: float


@dataclass(frozen=True)
class DiscordConfig:
    webhook_url: str
    max_content_chars: int


@dataclass(frozen=True)
class SdeConfig:
    manifest_url: str
    bundle_url_template: str


@dataclass(frozen=True)
class Config:
    esi: EsiConfig
    budget: BudgetConfig
    market: MarketConfig
    schedule: ScheduleConfig
    costs: CostConfig
    liquidity: LiquidityConfig
    discord: DiscordConfig
    sde: SdeConfig
    paths: DataPaths
    source: Path

    @property
    def user_agent(self) -> str:
        return self.esi.user_agent.replace("{version}", __version__)


def _key_paths(table: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in table.items():
        path = f"{prefix}{key}"
        keys.add(path)
        if isinstance(value, dict):
            keys |= _key_paths(value, f"{path}.")
    return keys


def config_key_diff(
    config_path: Path | None = None, example_path: Path | None = None
) -> tuple[set[str], set[str]]:
    """Return ``(missing_from_config, missing_from_example)`` key paths (D2)."""
    config_path = config_path or REPO_ROOT / CONFIG_NAME
    example_path = example_path or REPO_ROOT / EXAMPLE_NAME
    with config_path.open("rb") as handle:
        config_keys = _key_paths(tomllib.load(handle))
    with example_path.open("rb") as handle:
        example_keys = _key_paths(tomllib.load(handle))
    return example_keys - config_keys, config_keys - example_keys


def load_config(path: Path | None = None) -> Config:
    """Load and validate configuration."""
    path = path or REPO_ROOT / CONFIG_NAME
    if not path.exists():
        raise ConfigError(
            f"{path} is missing. Copy {EXAMPLE_NAME} to {CONFIG_NAME} and fill it in."
        )
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    try:
        return _build(raw, path)
    except KeyError as exc:  # a missing key is a loud error, never a default
        raise ConfigError(f"{path} is missing required key {exc.args[0]!r}") from exc


def _build(raw: dict, source: Path) -> Config:
    esi = raw["esi"]
    budget = raw["budget"]
    market = raw["market"]
    schedule = raw["schedule"]
    costs = raw["costs"]
    liquidity = raw["liquidity"]
    discord = raw["discord"]
    sde = raw["sde"]
    storage = raw["storage"]

    compat = str(esi["compatibility_date"])
    dt.date.fromisoformat(compat)  # fail loudly on a malformed pin

    return Config(
        esi=EsiConfig(
            base_url=str(esi["base_url"]).rstrip("/"),
            user_agent=str(esi["user_agent"]),
            compatibility_date=compat,
            timeout_seconds=float(esi["timeout_seconds"]),
            max_retries=int(esi["max_retries"]),
            retry_backoff_seconds=float(esi["retry_backoff_seconds"]),
            jitter_seconds=float(esi["jitter_seconds"]),
            orders_concurrency=int(esi["orders_concurrency"]),
        ),
        budget=BudgetConfig(
            orders_tokens_per_window=int(budget["orders_tokens_per_window"]),
            orders_token_hard_stop=int(budget["orders_token_hard_stop"]),
            history_requests_per_minute=int(budget["history_requests_per_minute"]),
            error_limit_stop_seconds=int(budget["error_limit_stop_seconds"]),
        ),
        market=MarketConfig(
            region_id=int(market["region_id"]),
            notional_tiers_isk=tuple(float(x) for x in market["notional_tiers_isk"]),
            book_staleness_minutes=int(market["book_staleness_minutes"]),
        ),
        schedule=ScheduleConfig(
            digest_utc=parse_hhmm(str(schedule["digest_utc"])),
            history_job_utc=parse_hhmm(str(schedule["history_job_utc"])),
            book_hot_start_utc=parse_hhmm(str(schedule["book_hot_start_utc"])),
            book_hot_end_utc=parse_hhmm(str(schedule["book_hot_end_utc"])),
            book_cold_interval_minutes=int(schedule["book_cold_interval_minutes"]),
        ),
        costs=CostConfig(
            accounting_level=int(costs["accounting_level"]),
            broker_relations_level=int(costs["broker_relations_level"]),
            sales_tax_base_pct=float(costs["sales_tax_base_pct"]),
            broker_fee_effective_pct=float(costs["broker_fee_effective_pct"]),
        ),
        liquidity=LiquidityConfig(
            min_median_isk_value_30d=float(liquidity["min_median_isk_value_30d"]),
            min_median_order_count_30d=float(liquidity["min_median_order_count_30d"]),
        ),
        discord=DiscordConfig(
            webhook_url=str(discord["webhook_url"]),
            max_content_chars=int(discord["max_content_chars"]),
        ),
        sde=SdeConfig(
            manifest_url=str(sde["manifest_url"]),
            bundle_url_template=str(sde["bundle_url_template"]),
        ),
        paths=DataPaths(resolve_data_dir(str(storage["data_dir"]))),
        source=source,
    )
