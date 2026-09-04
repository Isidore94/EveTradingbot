"""Configuration: `config.toml` -> frozen dataclasses.

stdlib `tomllib` only; no pydantic, no ORM (plan.md §11 D1). Every key in
`config.example.toml` exists here and vice versa — `selftest` fails the build
if they diverge, which is how a secret-bearing local config stays honest.
"""

from __future__ import annotations

import tomllib
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .paths import DataPaths, resolve_data_dir

CONFIG_FILENAME = "config.toml"
EXAMPLE_FILENAME = "config.example.toml"


class ConfigError(RuntimeError):
    """Raised loudly; a malformed config is never silently defaulted."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    contact: str
    project_url: str
    compatibility_date: str
    data_dir: str

    @property
    def user_agent(self) -> str:
        """Descriptive UA with contact info — never a library default (§3.1)."""
        return f"EveTradingbot/{__version__} ({self.contact}; +{self.project_url})"


@dataclass(frozen=True, slots=True)
class EsiConfig:
    base_url: str
    home_region_id: int
    secondary_region_ids: tuple[int, ...]
    timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    circuit_breaker_failures: int
    circuit_breaker_cooldown_minutes: int
    expiry_jitter_seconds: float


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    orders_token_limit: int
    orders_window_minutes: int
    orders_token_self_cap: int
    history_requests_per_minute: int
    error_limit_stop_seconds: int
    error_limit_pause_remaining: int


@dataclass(frozen=True, slots=True)
class CadenceConfig:
    digest_utc: str
    history_job_utc: str
    book_hot_start_utc: str
    book_hot_end_utc: str
    book_cold_interval_minutes: int
    secondary_hub_interval_minutes: int
    universe_refresh_utc: str
    killmail_poll_interval_seconds: int


@dataclass(frozen=True, slots=True)
class CostsConfig:
    accounting_level: int
    broker_relations_level: int
    sales_tax_base_pct: float
    sales_tax_per_level_reduction: float
    broker_fee_base_pct: float
    broker_fee_per_level_pct: float
    broker_fee_standings_pct: float
    relist_surcharge_multiple: float
    notional_tiers_isk: tuple[float, ...]
    book_staleness_minutes: int
    annual_capital_cost_pct: float
    #: Operator-**observed** effective broker rates, per station id (§22 S6).
    #: Transcribed from what the client actually charged; never derived from
    #: standings, which this system cannot read. Empty means "use the
    #: skill-derived base everywhere", which is the previous behaviour.
    broker_fee_overrides: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    # The membership rule (§11 D3, amended 2026-08-20): a MEDIAN 30-day unit
    # volume floor decides who is tradeable. Median, never mean — one
    # wash-trade day must not lift a dead item over the floor. The ISK/order
    # keys below are retained as measurements and as the index *weighting*
    # input; they no longer gate membership.
    min_median_unit_volume: float
    absolute_min_unit_volume: float
    min_median_isk_value: float
    min_median_order_count: float
    liquidity_lookback_days: int
    census_max_types: int
    watchlist: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GuiConfig:
    """The desk (plan.md §19 Part 2). Optional tier; core never imports Qt."""

    refresh_seconds: int
    chart_bars: int
    sma_lengths: tuple[int, ...]
    ema_lengths: tuple[int, ...]
    cloud_fast: int
    cloud_slow: int
    show_hv_levels: bool
    show_pivots: bool
    show_round_levels: bool


@dataclass(frozen=True, slots=True)
class SdeConfig:
    latest_url: str
    bundle_url_template: str
    refresh_days: int


@dataclass(frozen=True, slots=True)
class SignalsConfig:
    atr_length: int
    atr_winsor_k: float
    atr_winsor_window: int
    rrs_length: int
    min_atr_fraction: float
    composite_members: int
    composite_single_weight_cap: float
    composite_rebalance_days: int
    composite_return_clamp_k: float
    composite_return_clamp_window: int
    composite_return_clamp_floor: float
    cohort_min_members: int
    round_number_levels_isk: tuple[float, ...]
    anchor_fresh_days: int


@dataclass(frozen=True, slots=True)
class ScreenConfig:
    max_candidates: int
    min_net_edge_pct: float
    participation_window: int
    top_order_share_flag: float
    #: Bar freshness, independent of the book (§21 R2). A bar is stale when it
    #: is this many completed EVE days behind, or when ingestion last wrote
    #: longer ago than the refresh budget — a lake whose history job has been
    #: failing still holds a bar dated the day it stopped.
    max_bar_age_days: int = 3
    max_refresh_age_hours: float = 36.0


@dataclass(frozen=True, slots=True)
class DiscordConfig:
    webhook_url: str
    max_content_chars: int
    username: str


@dataclass(frozen=True, slots=True)
class PaperConfig:
    stale_book_minutes: int
    self_impact_turnover_share: float
    fill_tolerance_pct_of_notional: float
    verdict_first_read_closed: int
    verdict_falsify_negative_closed: int
    default_notional_isk: float
    #: Minimum price improvement that puts a posted order in front of the
    #: queue. EVE quotes to 0.01 ISK (plan.md §12.2, amended 2026-08-21).
    maker_tick_isk: float = 0.01
    #: Which fill model a new paper entry starts on. `taker` is the frozen
    #: default and the only one whose fill is guaranteed by the snapshot.
    default_fill_model: str = "taker"


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    horizons_days: tuple[int, ...]
    anchor_lookback_days: int
    entry_band_sigma: float
    min_bars: int
    min_rrs: float
    participation_floor: float
    haircut_multipliers: tuple[float, ...]
    wilson_z: float
    max_concurrent_positions: int


@dataclass(frozen=True, slots=True)
class KillmailsConfig:
    everef_base_url: str
    r2z2_base_url: str
    backfill_days: int
    destruction_recent_days: int
    destruction_baseline_days: int
    lead_lag_max_lag_days: int


@dataclass(frozen=True, slots=True)
class RoutesConfig:
    """The route engine (plan.md §23.8). Every field is operator-editable.

    Every field declares a default, which is what makes the whole section
    optional: an operator's existing `config.toml` predates it and must keep
    loading unchanged.
    """

    #: Which profile the hauling scan uses unless a run overrides it.
    security_profile: str = "highsec"
    #: Extra cost, in jumps, charged for entering a non-high-sec system on the
    #: `safer` profile. 50 makes one low-sec hop worth fifty high-sec ones.
    safer_penalty: float = 50.0
    #: Systems removed from the graph before any search. Ids, not names.
    avoid_systems: tuple[int, ...] = ()
    cache: bool = True


@dataclass(frozen=True, slots=True)
class HaulingConfig:
    """The hauling tab (plan.md §23). All defaults; the section is optional."""

    enabled: bool = True
    #: Execution stations, resolved from the SDE and never from memory
    #: (build 3478781): Jita IV-4 60003760, Amarr VIII (Oris) 60008494,
    #: Dodixie IX-20 60011866, Rens VI-8 60004588, Hek VIII-12 60005686.
    hub_station_ids: tuple[int, ...] = (60003760, 60008494, 60011866, 60004588, 60005686)
    #: Operator-added NPC destinations that are not trade hubs (§23, H4).
    extra_destination_station_ids: tuple[int, ...] = ()
    #: How deep the depth reduction bothers to store, before the safety margin.
    max_scan_capital_isk: float = 5_000_000_000.0
    depth_safety_margin: float = 1.5
    default_objective: str = "isk_per_active_minute"
    liquidity_quantiles: tuple[float, ...] = (0.25, 0.5, 0.75)
    #: LABELLED ASSUMPTIONS, not measurements (§23.7). Regional history carries
    #: no station split, so neither of these is derivable from the lake.
    destination_share_prior: float = 0.25
    capture_share: tuple[float, ...] = (0.05, 0.15, 0.35)
    min_liquidity_bars: int = 10
    #: The window daily units are measured over. Bars outside it are not
    #: evidence about it — a wider window is a different claim, not more data.
    liquidity_window_days: int = 30
    default_session_minutes: int = 30
    default_max_wait_days: int = 3
    default_seconds_per_jump: float = 55.0
    default_handling_minutes: float = 4.0
    max_exposure_pct_per_trade: float = 25.0
    max_exposure_pct_per_destination: float = 50.0
    # -- §23.21 (H7) ---------------------------------------------------------
    #: Operator-added NPC stations to BUY from, beyond the hubs. A source is
    #: not thereby a destination and vice versa.
    extra_source_station_ids: tuple[int, ...] = ()
    #: How many stored prior generations a plan's persistence is measured
    #: over, and how few make it UNKNOWN rather than a comfortable 100%.
    persistence_generations: int = 12
    persistence_min_generations: int = 3
    #: The window the route-loss column counts over, in days of ingested
    #: killmails. A window nothing was ingested for is UNKNOWN, never zero.
    route_risk_days: int = 90
    #: Market-group names whose hull members count as haulers, resolved by
    #: ancestry under the "Ships" root so blueprints and SKINs never qualify.
    hauler_group_names: tuple[str, ...] = (
        "Haulers",
        "Transport Ships",
        "Freighters",
        "Jump Freighters",
        "Industrial Command Ships",
    )


@dataclass(frozen=True, slots=True)
class FreightConfig:
    pushx_quote_url: str
    hub_systems: tuple[dict, ...]
    quote_cache_minutes: int
    staleness_haircut_pct: float
    collateral_multiple: float
    enabled: bool


@dataclass(frozen=True, slots=True)
class Config:
    app: AppConfig
    esi: EsiConfig
    budget: BudgetConfig
    cadence: CadenceConfig
    costs: CostsConfig
    universe: UniverseConfig
    gui: GuiConfig
    sde: SdeConfig
    signals: SignalsConfig
    screen: ScreenConfig
    discord: DiscordConfig
    paper: PaperConfig
    backtest: BacktestConfig
    killmails: KillmailsConfig
    freight: FreightConfig
    routes: RoutesConfig = field(default_factory=RoutesConfig)
    hauling: HaulingConfig = field(default_factory=HaulingConfig)
    source_path: Path | None = field(default=None, compare=False)

    @property
    def paths(self) -> DataPaths:
        return DataPaths(resolve_data_dir(self.app.data_dir))

    @property
    def headers(self) -> dict[str, str]:
        """Every ESI request carries these. `X-Compatibility-Date` is pinned."""
        return {
            "User-Agent": self.app.user_agent,
            "X-Compatibility-Date": self.app.compatibility_date,
            "Accept": "application/json",
        }


_SECTIONS: dict[str, type] = {
    "app": AppConfig,
    "esi": EsiConfig,
    "budget": BudgetConfig,
    "cadence": CadenceConfig,
    "costs": CostsConfig,
    "universe": UniverseConfig,
    "gui": GuiConfig,
    "sde": SdeConfig,
    "signals": SignalsConfig,
    "screen": ScreenConfig,
    "discord": DiscordConfig,
    "paper": PaperConfig,
    "backtest": BacktestConfig,
    "killmails": KillmailsConfig,
    "freight": FreightConfig,
    "routes": RoutesConfig,
    "hauling": HaulingConfig,
}


def optional_sections() -> set[str]:
    """Sections every one of whose fields declares a default.

    Such a section can be **absent entirely** from an operator's `config.toml`
    without changing a single value, so requiring it would break a config that
    is already correct. A section with even one required field stays required,
    and drift in it still fails loudly — which is the property this check
    exists for (the same rule `build_section` applies per field, §21 R2).
    """
    return {
        name
        for name, section_type in _SECTIONS.items()
        if all(
            f.default is not MISSING or f.default_factory is not MISSING
            for f in fields(section_type)
        )
    }


def _coerce(annotation: Any, value: Any, where: str) -> Any:
    """Coerce a TOML scalar/array to the dataclass annotation, or fail loudly."""
    if annotation in (int, "int"):
        return int(value)
    if annotation in (float, "float"):
        return float(value)
    if annotation in (bool, "bool"):
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: expected a boolean, got {value!r}")
        return value
    if annotation in (str, "str"):
        return str(value)
    text = str(annotation)
    if text.startswith("tuple["):
        if not isinstance(value, list):
            raise ConfigError(f"{where}: expected an array, got {value!r}")
        inner = text[len("tuple[") : -1].split(",")[0].strip()
        if inner == "dict":
            return tuple(dict(item) for item in value)
        return tuple(_coerce(inner, item, where) for item in value)
    return value


def build_section(section_type: type, data: dict, name: str):
    """Build one frozen section, rejecting missing and unknown keys loudly."""
    if not is_dataclass(section_type):  # pragma: no cover - programming error
        raise ConfigError(f"{name} is not a config section")
    expected = {f.name: f.type for f in fields(section_type)}
    # A field that declares a default is an *optional* setting, not drift: an
    # operator's existing config.toml must keep loading when a later phase adds
    # one. A field with no default is still required and still fails loudly,
    # which is the property this check exists for.
    optional = {
        f.name
        for f in fields(section_type)
        if f.default is not MISSING or f.default_factory is not MISSING
    }
    missing = sorted(set(expected) - set(data) - optional)
    unknown = sorted(set(data) - set(expected))
    if missing:
        raise ConfigError(f"[{name}] missing keys: {', '.join(missing)}")
    if unknown:
        raise ConfigError(f"[{name}] unknown keys: {', '.join(unknown)}")
    kwargs = {
        key: _coerce(expected[key], data[key], f"[{name}].{key}") for key in expected if key in data
    }
    return section_type(**kwargs)


def config_from_mapping(raw: dict, source_path: Path | None = None) -> Config:
    optional = optional_sections()
    missing = sorted(set(_SECTIONS) - set(raw) - optional)
    unknown = sorted(set(raw) - set(_SECTIONS))
    if missing:
        raise ConfigError(f"config is missing sections: {', '.join(missing)}")
    if unknown:
        raise ConfigError(f"config has unknown sections: {', '.join(unknown)}")
    sections = {
        name: build_section(kind, raw.get(name, {}), name) for name, kind in _SECTIONS.items()
    }
    return Config(source_path=source_path, **sections)


def load_config(path: str | Path | None = None) -> Config:
    """Load `config.toml`, falling back to nothing — a missing file is an error."""
    candidate = Path(path) if path else Path.cwd() / CONFIG_FILENAME
    if not candidate.exists():
        raise ConfigError(
            f"no config at {candidate}; copy {EXAMPLE_FILENAME} to {CONFIG_FILENAME} and fill it in"
        )
    with candidate.open("rb") as stream:
        raw = tomllib.load(stream)
    return config_from_mapping(raw, source_path=candidate.resolve())


def load_example(root: Path | None = None) -> dict:
    path = (root or Path.cwd()) / EXAMPLE_FILENAME
    with path.open("rb") as stream:
        return tomllib.load(stream)


def key_set(raw: dict) -> set[str]:
    """Flat `section.key` set, for the example/config parity check."""
    return {f"{section}.{key}" for section, body in raw.items() for key in body}


def example_config(root: Path | None = None) -> Config:
    """The committed example, parsed. Used by offline tests and `selftest`."""
    return config_from_mapping(load_example(root))
