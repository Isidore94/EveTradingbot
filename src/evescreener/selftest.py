"""`python -m evescreener selftest` — the offline health check.

It answers one question honestly: *is this installation coherent?* Config and
example parity, data-dir writability, schema creation, the frozen bar
contract, and the cost model's arithmetic. It never touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config, key_set, load_example
from .store.db import Database
from .store.lake import EVE_DAILY_BAR_COLUMNS


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str

    def render(self) -> str:
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.name}: {self.detail}"


def run_selftest(config: Config, repo_root: Path | None = None) -> list[Check]:
    checks: list[Check] = []
    root = repo_root or Path.cwd()

    # 1. config/example key parity — the secret-bearing file stays honest.
    try:
        example_keys = key_set(load_example(root))
        if config.source_path is None:
            checks.append(
                Check("config parity", True, "running from the committed example; parity trivial")
            )
        else:
            import tomllib

            with config.source_path.open("rb") as stream:
                live_keys = key_set(tomllib.load(stream))
            missing = sorted(example_keys - live_keys)
            extra = sorted(live_keys - example_keys)
            ok = not missing and not extra
            detail = "identical key sets" if ok else f"missing={missing} extra={extra}"
            checks.append(Check("config parity", ok, detail))
    except Exception as exc:  # noqa: BLE001 - selftest reports, never raises
        checks.append(Check("config parity", False, f"{type(exc).__name__}: {exc}"))

    # 2. User-Agent must be descriptive with contact info (plan.md §3.1).
    agent = config.app.user_agent
    ua_ok = "@" in agent and agent.startswith("EveTradingbot/") and len(agent) > 30
    checks.append(Check("user agent", ua_ok, agent))

    # 3. No secret may reach the committed example.
    example = load_example(root)
    leaked = bool(example.get("discord", {}).get("webhook_url"))
    checks.append(
        Check("example has no secrets", not leaked, "webhook_url empty in config.example.toml")
    )

    # 4. Data dir writable + schema creates.
    try:
        paths = config.paths.ensure()
        with Database(paths.db) as db:
            version = db.get_meta("schema_version")
        checks.append(Check("state.db", True, f"schema v{version} at {paths.db}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("state.db", False, f"{type(exc).__name__}: {exc}"))

    # 5. The bar contract is frozen and has no `open`.
    contract_ok = EVE_DAILY_BAR_COLUMNS == [
        "datetime",
        "high",
        "low",
        "close",
        "volume",
        "order_count",
    ]
    checks.append(
        Check(
            "bar contract",
            contract_ok and "open" not in EVE_DAILY_BAR_COLUMNS,
            ", ".join(EVE_DAILY_BAR_COLUMNS),
        )
    )

    # 6. Cost model arithmetic reproduces the documented rates.
    from .costs import CostModel

    model = CostModel.from_config(config)
    tax_ok = abs(model.sales_tax_pct - 3.375) < 1e-9
    checks.append(
        Check(
            "cost model",
            tax_ok,
            f"sales tax {model.sales_tax_pct:.4f}% / broker {model.broker_fee_pct:.4f}% "
            f"at Accounting {config.costs.accounting_level} / "
            f"Broker Relations {config.costs.broker_relations_level}",
        )
    )

    # 7. Budget self-caps are actually below the hard limits.
    caps_ok = (
        config.budget.orders_token_self_cap < config.budget.orders_token_limit
        and config.budget.history_requests_per_minute <= 150
    )
    checks.append(
        Check(
            "self-caps",
            caps_ok,
            f"orders {config.budget.orders_token_self_cap}/{config.budget.orders_token_limit} "
            f"tokens per {config.budget.orders_window_minutes} min; "
            f"history {config.budget.history_requests_per_minute}/min",
        )
    )

    # 8. The membership floors are coherent (§11 D3, amended).
    universe = config.universe
    floors_ok = 0 < universe.absolute_min_unit_volume <= universe.min_median_unit_volume
    checks.append(
        Check(
            "membership floors",
            floors_ok,
            f"tradeable >= {universe.min_median_unit_volume:,.0f} units/day; "
            f"THIN band {universe.absolute_min_unit_volume:,.0f}-"
            f"{universe.min_median_unit_volume:,.0f}; below that, lookup only",
        )
    )

    # 9. The committed sector map parses. A malformed one must fail here, on a
    #    command the operator runs deliberately, not halfway through a digest.
    from .indices import SECTORS_FILE, load_sectors

    sectors_path = root / "config" / SECTORS_FILE
    try:
        sectors = load_sectors(sectors_path)
        if not sectors_path.exists():
            checks.append(
                Check("sector map", False, f"no {sectors_path}; the index layer has no sectors")
            )
        else:
            checks.append(
                Check(
                    "sector map",
                    True,
                    f"{len(sectors)} sector(s): " + ", ".join(sector.ticker for sector in sectors),
                )
            )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("sector map", False, f"{type(exc).__name__}: {exc}"))

    # 10. The operator's setups parse. A malformed file must fail here rather
    #     than halfway through a scan.
    from .setups import SETUPS_FILE, load_setups

    try:
        setups = load_setups(root / "config" / SETUPS_FILE)
        enabled = [setup for setup in setups if setup.enabled]
        checks.append(
            Check(
                "setups",
                True,
                f"{len(setups)} setup(s), {len(enabled)} enabled"
                + (f": {', '.join(setup.name for setup in enabled)}" if enabled else ""),
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("setups", False, f"{type(exc).__name__}: {exc}"))

    # 11. The reason vocabulary parses and covers both directions. A vocabulary
    #     with no dislike tags cannot record a pass, and half the decision
    #     record would silently go missing (§19 Amendment 3).
    from .reasons import REASONS_FILE, load_reasons

    try:
        vocabulary = load_reasons(root / "config" / REASONS_FILE)
        both = bool(vocabulary.likes) and bool(vocabulary.dislikes)
        checks.append(
            Check(
                "reason vocabulary",
                both,
                f"{len(vocabulary.likes)} like / {len(vocabulary.dislikes)} dislike tag(s)"
                if both
                else "both directions are required; a pass cannot be recorded without dislike tags",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("reason vocabulary", False, f"{type(exc).__name__}: {exc}"))

    return checks


def selftest_report(checks: list[Check]) -> str:
    lines = [check.render() for check in checks]
    failures = sum(1 for check in checks if not check.ok)
    lines.append(f"{len(checks) - failures}/{len(checks)} checks passed")
    return "\n".join(lines)
