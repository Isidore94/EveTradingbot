"""Command-line surface.

One process model: `daemon` owns every cadence; each other subcommand runs the
same job once, for manual and backfill use (plan.md §11 D1). Every command
prints what it did *and what it did not do* — a skipped fetch, an UNKNOWN
cost, and an honest zero are all first-class outcomes here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import Config, ConfigError, example_config, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evescreener",
        description="EVE Online market screener — decision support only, never automation.",
    )
    parser.add_argument("--config", help="path to config.toml (default: ./config.toml)")
    parser.add_argument(
        "--example-config",
        action="store_true",
        help="run against the committed config.example.toml (offline use, no secrets)",
    )
    parser.add_argument("--region", type=int, help="override the region id for this run")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("selftest", help="offline installation health check")

    sde = sub.add_parser("sde", help="refresh the static-data tables (types, market groups)")
    sde.add_argument("--force", action="store_true", help="reload even if the build is unchanged")
    sde.add_argument("--bundle", help="use a local SDE jsonl zip instead of downloading")

    census = sub.add_parser("census", help="universe census: discover, crawl, measure the map")
    census.add_argument(
        "--no-crawl",
        action="store_true",
        help="measure the existing lake without the full history crawl",
    )
    census.add_argument("--max-types", type=int, help="cap the crawl (safety valve, not a target)")

    ingest = sub.add_parser("ingest-history", help="refresh daily bars for the tracked universe")
    ingest.add_argument(
        "--scope",
        choices=("tracked", "watchlist", "all"),
        default="tracked",
        help="which types to refresh (default: the tracked universe)",
    )
    ingest.add_argument("--type-id", type=int, action="append", help="refresh one type; repeatable")

    return parser


def resolve_config(args: argparse.Namespace) -> Config:
    if args.example_config:
        return example_config()
    return load_config(args.config)


def _region(config: Config, args: argparse.Namespace) -> int:
    return args.region or config.esi.home_region_id


def _cmd_selftest(config: Config, args: argparse.Namespace) -> int:
    from .selftest import run_selftest, selftest_report

    checks = run_selftest(config, repo_root=Path.cwd())
    print(selftest_report(checks))
    return 0 if all(check.ok for check in checks) else 1


def _cmd_sde(config: Config, args: argparse.Namespace) -> int:
    from .sde import load_sde
    from .store.db import Database

    with Database(config.paths.ensure().db) as db:
        result = load_sde(
            config,
            db,
            force=args.force,
            bundle_path=Path(args.bundle) if args.bundle else None,
        )
    print(json.dumps(result.as_dict(), indent=2))
    return 0


def _cmd_census(config: Config, args: argparse.Namespace) -> int:
    from .census import render_census, run_census, write_census
    from .esi.client import EsiClient
    from .store.db import Database

    def progress(index: int, total: int, result) -> None:
        print(
            f"  ... {index}/{total} types "
            f"(fetched {result.fetched}, fresh {result.skipped_fresh}, failed {result.failed})",
            flush=True,
        )

    async def run() -> int:
        with Database(config.paths.ensure().db) as db:
            client = EsiClient(config, db)
            try:
                result = await run_census(
                    config,
                    db,
                    client,
                    region_id=_region(config, args),
                    crawl=not args.no_crawl,
                    max_types=args.max_types,
                    progress=progress,
                )
            finally:
                await client.aclose()
            json_path, md_path = write_census(config, result)
        print(render_census(result))
        print(f"\nwritten: {json_path}\n         {md_path}")
        return 0

    return asyncio.run(run())


def _cmd_ingest_history(config: Config, args: argparse.Namespace) -> int:
    from .bars import ingest_history
    from .esi.client import EsiClient
    from .store.db import Database
    from .store.lake import BarLake
    from .universe import tracked_type_ids, watchlist_type_ids

    region = _region(config, args)

    async def run() -> int:
        with Database(config.paths.ensure().db) as db:
            if args.type_id:
                ids = list(args.type_id)
            elif args.scope == "watchlist":
                ids = watchlist_type_ids(db)
            elif args.scope == "all":
                ids = [
                    int(row["type_id"])
                    for row in db.conn.execute(
                        "SELECT type_id FROM universe WHERE region_id=? ORDER BY type_id", (region,)
                    )
                ]
            else:
                ids = tracked_type_ids(db, region)
            if not ids:
                print("no types in scope — run `census` first (honest zero, not an error)")
                return 0
            client = EsiClient(config, db)
            try:
                result = await ingest_history(client, BarLake(config.paths), ids, region_id=region)
            finally:
                await client.aclose()
        print(json.dumps(result.as_dict(), indent=2))
        return 0

    return asyncio.run(run())


HANDLERS = {
    "selftest": _cmd_selftest,
    "sde": _cmd_sde,
    "census": _cmd_census,
    "ingest-history": _cmd_ingest_history,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = resolve_config(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    handler = HANDLERS.get(args.command)
    if handler is None:
        parser.error(f"unknown command {args.command!r}")
        return 2
    return handler(config, args)
