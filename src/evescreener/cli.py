"""Command-line surface.

One process model: `daemon` owns every cadence; each other subcommand runs the
same job once, for manual and backfill use (plan.md §11 D1).
"""

from __future__ import annotations

import argparse
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
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("selftest", help="offline installation health check")
    return parser


def resolve_config(args: argparse.Namespace) -> Config:
    if args.example_config:
        return example_config()
    return load_config(args.config)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = resolve_config(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "selftest":
        from .selftest import run_selftest, selftest_report

        checks = run_selftest(config, repo_root=Path.cwd())
        print(selftest_report(checks))
        return 0 if all(check.ok for check in checks) else 1

    parser.error(f"unknown command {args.command!r}")
    return 2
