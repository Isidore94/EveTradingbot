"""Command-line entry point: ``python -m evescreener <cmd>`` (plan.md §11 D1).

Subcommands are the ones D1 locks. ``daemon`` and ``census`` belong to later
phases and say so rather than pretending to work: one phase at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys

from . import __version__
from .clock import now_utc
from .config import Config, ConfigError, config_key_diff, load_config
from .esi import EsiClient
from .state import StateStore

PHASE_1_NOTICE = (
    "not implemented yet: the scheduler and the universe census land in Phase 1 "
    "(plan.md §8). Phase 0 exposes ingest-history, sweep-books, digest, selftest."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evescreener",
        description="Decision-support market screener for EVE Online (read-only ESI).",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("daemon", help="scheduler owning all cadences (Phase 1)")
    subparsers.add_parser("census", help="universe census (Phase 1)")

    history = subparsers.add_parser(
        "ingest-history", help="refresh daily bars for the watchlist"
    )
    history.add_argument(
        "--refresh-sde",
        action="store_true",
        help="re-download and reload the static data export first",
    )

    subparsers.add_parser("sweep-books", help="one order-book sweep, reduced on write")

    digest_parser = subparsers.add_parser("digest", help="build and post the digest")
    digest_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="render and archive the digest without posting to Discord",
    )

    subparsers.add_parser("selftest", help="check config, storage, and invariants")

    args = parser.parse_args(argv)

    if args.command in {"daemon", "census"}:
        print(f"{args.command}: {PHASE_1_NOTICE}", file=sys.stderr)
        return 2

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1

    config.paths.ensure()

    handlers = {
        "ingest-history": lambda: _ingest_history(config, args),
        "sweep-books": lambda: _sweep_books(config),
        "digest": lambda: _digest(config, args),
        "selftest": lambda: _selftest(config),
    }
    return handlers[args.command]()


def _ingest_history(config: Config, args: argparse.Namespace) -> int:
    from .sde import refresh as refresh_sde
    from .watchlist import SEED_WATCHLIST, UnresolvedTypeNames, seed_watchlist

    with StateStore(config.paths.state_db) as store:
        if args.refresh_sde or store.sde_type_count() == 0:
            print("loading the static data export (one ~99 MB download)...")
            loaded = refresh_sde(config, store, force=args.refresh_sde)
            print(
                f"SDE build {loaded.build} ({loaded.release_date}): "
                f"{store.sde_type_count():,} types"
            )

        try:
            resolved = seed_watchlist(store)
        except UnresolvedTypeNames as exc:
            print(f"watchlist error: {exc}", file=sys.stderr)
            return 1
        print(f"watchlist: {len(resolved)}/{len(SEED_WATCHLIST)} names resolved")

        type_ids = [type_id for _, type_id in sorted(resolved.items())]
        result = asyncio.run(_run_ingest(config, store, type_ids))

    print(
        f"history: fetched {result.types_fetched}, 304 {result.types_not_modified}, "
        f"still-fresh {result.types_skipped_fresh}, "
        f"rows in lake {result.rows_written:,}"
    )
    print(
        f"data quality: partial bars dropped {result.partial_bars_dropped}, "
        f"zero-order_count bars {result.zero_order_count_bars}"
    )
    if result.failures:
        print(f"failures ({len(result.failures)}):", file=sys.stderr)
        for type_id, detail in result.failures.items():
            print(f"  type {type_id}: {detail}", file=sys.stderr)
        return 1
    return 0


async def _run_ingest(config: Config, store: StateStore, type_ids: list[int]):
    from .bars import ingest_history

    async with EsiClient(config, store) as client:
        return await ingest_history(client, config, type_ids)


def _sweep_books(config: Config) -> int:

    with StateStore(config.paths.state_db) as store:
        result = asyncio.run(_run_sweep(config, store))

    print(
        f"sweep {result.sweep_ts.isoformat()}: {result.pages} pages "
        f"(fetched {result.pages_fetched}, 304 {result.pages_not_modified}, "
        f"still-fresh {result.pages_skipped_fresh})"
    )
    print(
        f"orders {result.orders:,} · "
        f"duplicate order_ids {result.duplicate_order_ids} · "
        f"types {result.types:,} · tokens charged {result.tokens_charged}"
    )
    if result.expires_ts is not None:
        print(f"this book expires {result.expires_ts.isoformat()}")
    return 0


async def _run_sweep(config: Config, store: StateStore):
    from .books import sweep_books

    async with EsiClient(config, store) as client:
        return await sweep_books(client, config)


def _digest(config: Config, args: argparse.Namespace) -> int:
    from . import digest as digest_module
    from . import notify
    from .bars import read_bars, turnover_stats
    from .books import latest_book_summary
    from .screen import build_screen

    with StateStore(config.paths.state_db) as store:
        watchlist = store.watchlist()
        if not watchlist:
            print(
                "watchlist is empty: run `python -m evescreener ingest-history` first",
                file=sys.stderr,
            )
            return 1
        names = {type_id: name for type_id, name in watchlist}
        type_ids = sorted(names)
        telemetry = store.ledger_summary(now_utc() - dt.timedelta(days=1))

        bars = read_bars(config.paths, config.market.region_id, type_ids=type_ids)
        result = build_screen(
            config,
            book=latest_book_summary(config.paths, config.market.region_id),
            turnover=turnover_stats(bars),
            names=names,
            type_ids=type_ids,
        )

    built = digest_module.build(config, result, telemetry=telemetry)
    print(built.text)
    print()

    if args.dry_run:
        delivery = {"kind": "dry_run", "detail": "not posted (--dry-run)"}
    else:
        sent = notify.send(config.discord.webhook_url, built.messages)
        delivery = {"kind": sent.kind, "detail": sent.detail}
        print(f"discord: {sent.kind} — {sent.detail}")

    digest_module.archive(config, built, result, delivery)
    if built.dropped_lines:
        print(f"warning: {built.dropped_lines} line(s) exceeded the message budget")
    return 0


def _selftest(config: Config) -> int:
    from .bars import EVE_DAILY_BAR_COLUMNS
    from .watchlist import SEED_WATCHLIST, UnresolvedTypeNames, resolve_seed

    failures: list[str] = []
    notes: list[str] = []

    missing_from_config, missing_from_example = config_key_diff()
    if missing_from_config:
        failures.append(f"config.toml is missing keys: {sorted(missing_from_config)}")
    if missing_from_example:
        failures.append(
            f"config.example.toml is missing keys: {sorted(missing_from_example)}"
        )

    if "open" in EVE_DAILY_BAR_COLUMNS:
        failures.append("bar contract grew an `open` column (plan.md §4 forbids it)")

    if len(set(SEED_WATCHLIST)) != 50:
        failures.append(
            f"seed watchlist is {len(set(SEED_WATCHLIST))} unique names, expected 50"
        )

    agent = config.user_agent
    if "@" not in agent or "{version}" in agent:
        failures.append(f"User-Agent is not a descriptive contact string: {agent!r}")

    compat = dt.date.fromisoformat(config.esi.compatibility_date)
    if compat >= now_utc().date():
        failures.append(
            f"X-Compatibility-Date {compat} is not safely in the past; ESI rejects a "
            "date that is still in the future on its UTC-11 clock"
        )

    probe = config.paths.root / ".selftest"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        failures.append(f"data dir {config.paths.root} is not writable: {exc}")

    with StateStore(config.paths.state_db) as store:
        types = store.sde_type_count()
        if types == 0:
            notes.append("SDE not loaded yet — run `ingest-history` to populate it")
        else:
            notes.append(f"SDE build {store.get_meta('sde_build')}: {types:,} types")
            try:
                resolve_seed(store)
                notes.append("all 50 watchlist names resolve against the SDE")
            except UnresolvedTypeNames as exc:
                failures.append(str(exc))

    if not config.discord.webhook_url:
        notes.append("discord.webhook_url is empty — digests build but are not posted")

    print(f"evescreener {__version__} selftest")
    print(f"  config    {config.source}")
    print(f"  data dir  {config.paths.root}")
    print(f"  region    {config.market.region_id}")
    print(f"  compat    {config.esi.compatibility_date}")
    for note in notes:
        print(f"  note      {note}")
    for failure in failures:
        print(f"  FAIL      {failure}")
    print("selftest: " + ("FAILED" if failures else "ok"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
