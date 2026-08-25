"""Command-line surface.

One process model: `daemon` owns every cadence; each other subcommand runs the
same job once, for manual and backfill use (plan.md §11 D1, extended by
operator directive 2026-08-20 §17 D-5). Every command prints what it did *and
what it did not do* — a skipped fetch, an UNKNOWN cost and an honest zero are
all first-class outcomes here.
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

    sde = sub.add_parser("sde", help="refresh the static-data tables")
    sde.add_argument("--force", action="store_true", help="reload even if the build is unchanged")
    sde.add_argument("--bundle", help="use a local SDE jsonl zip instead of downloading")

    census = sub.add_parser("census", help="universe census: discover, crawl, measure the map")
    census.add_argument("--no-crawl", action="store_true", help="measure the existing lake only")
    census.add_argument("--max-types", type=int, help="cap the crawl (safety valve, not a target)")

    ingest = sub.add_parser("ingest-history", help="refresh daily bars for the tracked universe")
    ingest.add_argument(
        "--scope",
        choices=("tracked", "watchlist", "all", "hauling"),
        default="tracked",
        help="which types to refresh (default: the tracked universe). `hauling` "
        "fetches DESTINATION bars: the hauling candidates in each non-home hub "
        "region, which is the only history that can say how long an exit takes",
    )
    ingest.add_argument(
        "--max-types",
        type=int,
        default=400,
        help="cap per region on the `hauling` scope (default: 400) — a bound, not a target",
    )
    ingest.add_argument("--type-id", type=int, action="append", help="one type; repeatable")

    sweep = sub.add_parser("sweep-books", help="one governed order-book sweep, reduced on write")
    sweep.add_argument("--secondary", action="store_true", help="sweep the WARM secondary hubs too")
    sweep.add_argument("--debug-raw", help="persist a raw page sample here (fixture-building only)")

    digest = sub.add_parser("digest", help="build and post the daily digest")
    digest.add_argument("--dry-run", action="store_true", help="print it, do not post it")

    backtest = sub.add_parser("backtest", help="the historical viability study (plan.md §13)")
    backtest.add_argument("--max-types", type=int, help="cap the types scanned")
    backtest.add_argument(
        "--setup",
        help="measure one of config/setups.jsonl's setups instead of the built-in rule; "
        "same costs, same horizons, same limitations statement",
    )

    killmails = sub.add_parser("killmails", help="destruction backfill, live poll, or the study")
    killmails.add_argument(
        "--backfill", type=int, metavar="DAYS", help="backfill N days of archives"
    )
    killmails.add_argument("--poll", action="store_true", help="poll R2Z2 once")
    killmails.add_argument("--study", action="store_true", help="run the lead-lag study (§14)")

    cross = sub.add_parser("cross-region", help="hub-to-hub scan with real freight netting")
    cross.add_argument("--tier", type=int, default=0, help="notional tier index (default: 0)")

    paper = sub.add_parser("paper", help="the paper trading experiment (plan.md §12)")
    paper_sub = paper.add_subparsers(dest="paper_command", required=True)
    paper_open = paper_sub.add_parser("open", help="price and record an entry")
    paper_open.add_argument("--type-id", type=int, help="type id (or use --name)")
    paper_open.add_argument("--name", help="type name, resolved against the SDE")
    paper_open.add_argument("--notional", type=float, help="ISK notional (default: config)")
    paper_open.add_argument("--thesis", required=True, help="why — one sentence you can argue with")
    paper_open.add_argument(
        "--setup",
        required=True,
        help="the setup that fired, by name, or 'discretionary'",
    )
    paper_open.add_argument(
        "--like",
        action="append",
        default=[],
        metavar="TAG",
        help="why you like it, from config/reasons.jsonl (repeatable; at least one required)",
    )
    paper_open.add_argument("--reason-text", default="", help="optional free text")
    paper_open.add_argument("--stop", type=float, help="stop price, for R sizing")
    paper_open.add_argument("--target", type=float, help="target price, for planned R")
    paper_open.add_argument(
        "--fill-model",
        choices=("taker", "maker"),  # authority: paper.FILL_MODELS
        help="taker: walk the asks (default, the only fill the snapshot proves). "
        "maker: post one tick above the executable bid, pay the broker fee, and "
        "wait — recorded as an ASSUMED fill",
    )
    paper_close = paper_sub.add_parser("close", help="price and record an exit")
    paper_close.add_argument("--position-id", required=True)
    paper_close.add_argument("--note", default="")
    paper_close.add_argument(
        "--fill-model",
        choices=("taker", "maker"),  # authority: paper.FILL_MODELS
        help="override the position's own model — use it when a posted exit had "
        "to be dumped into the bid instead",
    )
    paper_close.add_argument(
        "--actual-price",
        type=float,
        help="gross unit price you REALLY sold at; the only way to close a "
        "position whose book can no longer price it",
    )
    paper_pass = paper_sub.add_parser(
        "pass", help="record a decision NOT to take something — same rigour as an open"
    )
    paper_pass.add_argument("--type-id", type=int, help="type id (or use --name)")
    paper_pass.add_argument("--name", help="type name, resolved against the SDE")
    paper_pass.add_argument(
        "--action",
        choices=("not_today", "bad_signal"),
        default="not_today",
        help="not_today clears it from today's queue only and NEVER touches the "
        "watchlist; bad_signal says the setup itself misfired",
    )
    paper_pass.add_argument(
        "--dislike",
        action="append",
        default=[],
        metavar="TAG",
        help="why you passed, from config/reasons.jsonl (repeatable; at least one required)",
    )
    paper_pass.add_argument("--reason-text", default="", help="optional free text")
    paper_pass.add_argument("--setup", help="the setup that surfaced it, if any")

    learning = sub.add_parser(
        "learning", help="what's working: per-setup and per-reason calibration"
    )
    learning.add_argument(
        "--horizon",
        type=int,
        default=10,
        help="forward days a recorded pass is measured over (default: 10)",
    )

    reasons_cmd = sub.add_parser("reasons", help="the reason vocabulary, validated on load")
    reasons_cmd.add_argument(
        "--direction", choices=("like", "dislike"), help="show only one direction"
    )

    paper_sub.add_parser("mark", help="daily mark-to-market with staleness stamps")
    paper_sub.add_parser("report", help="the §12.4 report and verdict")
    paper_fill = paper_sub.add_parser(
        "real-fill", help="record an actual fill (the SMALL-REAL rung)"
    )
    paper_fill.add_argument("--position-id", required=True)
    paper_fill.add_argument("--side", choices=("buy", "sell"), required=True)
    paper_fill.add_argument("--price", type=float, required=True)
    paper_fill.add_argument("--units", type=float, required=True)

    watch = sub.add_parser("watch", help="the operator watchlist: add, remove, list")
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)
    watch_add = watch_sub.add_parser("add", help="add one name (resolved against the SDE, loudly)")
    watch_add.add_argument("--name", required=True, help="exact type name")
    watch_add.add_argument("--type-id", type=int, help="skip name resolution and pin the id")
    watch_add.add_argument("--note", help="why this name is on the list")
    watch_remove = watch_sub.add_parser(
        "remove", help="remove one name — operator action, the only removal path"
    )
    watch_remove.add_argument("--name", required=True)
    watch_sub.add_parser("list", help="every entry, unresolved names included")

    brief = sub.add_parser("brief", help="one type, fully read — the chart, in text")
    brief.add_argument("--type-id", type=int, help="type id (or use --name)")
    brief.add_argument("--name", help="type name, resolved against the SDE")

    board = sub.add_parser("board", help="the D1 observation board across the tracked universe")
    board.add_argument("--top", type=int, default=20, help="rows to show (default: 20)")
    board.add_argument(
        "--sort",
        choices=("value", "strength", "change"),
        default="value",
        help="value = deepest below anchored value; strength = RRS; change = day move",
    )

    scan = sub.add_parser("scan", help="run the built-in setup and every enabled operator setup")
    scan.add_argument("--setup", help="run only this setup by name")
    scan.add_argument("--top", type=int, default=15, help="hits to show per setup (default: 15)")

    setups_cmd = sub.add_parser("setups", help="list config/setups.jsonl, validated on load")
    setups_cmd.add_argument("--all", action="store_true", help="include disabled setups")

    anchors = sub.add_parser(
        "anchors", help="patch-notes watcher: append anchor CANDIDATES for confirmation"
    )
    anchors.add_argument("--list", action="store_true", help="show the calendar and stop")
    anchors.add_argument("--all", action="store_true", help="include non-market-relevant posts")

    haul = sub.add_parser(
        "haul", help="the personalized hauling scan, ship profiles, and paper hauls (§23)"
    )
    haul_sub = haul.add_subparsers(dest="haul_command", required=True)

    haul_scan = haul_sub.add_parser("scan", help="rank hauling plans for one profile")
    haul_scan.add_argument("--from", dest="current_system", help="the system you are in, by name")
    haul_scan.add_argument("--from-id", dest="current_system_id", type=int, help="…or by id")
    haul_scan.add_argument("--to", dest="intended_destination", help="where you were going anyway")
    haul_scan.add_argument("--to-id", dest="intended_destination_id", type=int)
    haul_scan.add_argument(
        "--mode",
        choices=("dedicated", "along_route"),  # authority: hauling.MODES
        default="dedicated",
        help="dedicated: the trip IS the haul. along_route: charge only the detour",
    )
    haul_scan.add_argument("--ship", help="a saved ship profile, by name")
    haul_scan.add_argument("--cargo", type=float, help="usable cargo m³ (overrides the profile)")
    haul_scan.add_argument("--seconds-per-jump", type=float)
    haul_scan.add_argument("--handling-minutes", type=float)
    haul_scan.add_argument("--capital", type=float, help="ISK you can actually commit")
    haul_scan.add_argument("--exposure", type=float, help="most ISK in one trade")
    haul_scan.add_argument("--minutes", type=float, help="how long you have (session minutes)")
    haul_scan.add_argument("--max-wait-days", type=float)
    haul_scan.add_argument("--max-jumps", type=int)
    haul_scan.add_argument(
        "--security",
        choices=("highsec", "safer", "shortest"),  # authority: routes.PROFILES
        help="highsec: never leave it. safer: penalise leaving it. shortest: fewest jumps",
    )
    haul_scan.add_argument(
        "--objective",
        choices=("isk_per_active_minute", "net_profit", "net_roi", "isk_per_m3"),
        help="what 'best' means for this run (default: config)",
    )
    haul_scan.add_argument("--top", type=int, default=15, help="rows to print (default: 15)")
    haul_scan.add_argument(
        "--show-rejected",
        action="store_true",
        help="print the rejected candidates and their reasons",
    )
    haul_scan.add_argument(
        "--no-write", action="store_true", help="print it, do not write a report"
    )
    haul_scan.add_argument(
        "--freight",
        action="store_true",
        help="quote PushX for the top plans and show what flying it yourself is worth. "
        "Off by default: it is a request to somebody else's service, and the "
        "self-haul row never depends on it",
    )
    haul_scan.add_argument(
        "--freight-top", type=int, default=5, help="how many plans to quote (default: 5)"
    )

    haul_profile = haul_sub.add_parser("profile", help="ship profiles, stored in state.db")
    profile_sub = haul_profile.add_subparsers(dest="profile_command", required=True)
    profile_add = profile_sub.add_parser("add", help="add or update one ship profile")
    profile_add.add_argument("--name", required=True)
    profile_add.add_argument("--cargo", type=float, required=True, help="usable cargo m³")
    profile_add.add_argument("--ehp", type=float, help="effective hitpoints, for the risk read")
    profile_add.add_argument("--value", type=float, help="hull value in ISK, exposure if ganked")
    profile_add.add_argument("--seconds-per-jump", type=float)
    profile_add.add_argument("--handling-minutes", type=float)
    profile_sub.add_parser("list", help="every stored ship profile")
    profile_remove = profile_sub.add_parser("remove", help="operator action, the only removal path")
    profile_remove.add_argument("--name", required=True)

    haul_record = haul_sub.add_parser(
        "record", help="paper hauls: what you took, what you passed, and why"
    )
    record_sub = haul_record.add_subparsers(dest="record_command", required=True)
    record_open = record_sub.add_parser("open", help="record a haul you are taking")
    record_open.add_argument("--type-id", type=int)
    record_open.add_argument("--name", help="type name, resolved against the SDE")
    record_open.add_argument("--quantity", type=float, required=True)
    record_open.add_argument("--source", type=int, help="source station id")
    record_open.add_argument("--dest", type=int, help="destination station id")
    record_open.add_argument("--cost", type=float, help="ISK committed at the source")
    record_open.add_argument("--expected-net", type=float, help="what the scan said it would net")
    record_open.add_argument("--jumps", type=int)
    record_open.add_argument(
        "--thesis", required=True, help="why — one sentence you can argue with"
    )
    record_open.add_argument(
        "--like",
        action="append",
        default=[],
        metavar="TAG",
        help="from config/reasons.jsonl (repeatable; at least one required)",
    )
    record_open.add_argument("--reason-text", default="")
    record_close = record_sub.add_parser("close", help="record what the haul really paid")
    record_close.add_argument("--haul-id", required=True)
    record_close.add_argument("--proceeds", type=float, help="ISK actually received, net of tax")
    record_close.add_argument("--cost", type=float, help="ISK actually paid, if it differed")
    record_close.add_argument("--note", default="")
    record_pass = record_sub.add_parser("pass", help="record a haul you deliberately did not take")
    record_pass.add_argument("--type-id", type=int)
    record_pass.add_argument("--name")
    record_pass.add_argument("--action", choices=("not_today", "bad_signal"), default="not_today")
    record_pass.add_argument(
        "--dislike",
        action="append",
        default=[],
        metavar="TAG",
        help="from config/reasons.jsonl (repeatable; at least one required)",
    )
    record_pass.add_argument("--reason-text", default="")
    record_pass.add_argument("--source", type=int)
    record_pass.add_argument("--dest", type=int)
    record_sub.add_parser("report", help="the tally: refusals first")

    sub.add_parser("report", help="regenerate the viability report (plan.md §16)")

    sub.add_parser(
        "gui", help="open the desk (needs the optional `gui` extra: uv sync --extra gui)"
    )

    daemon = sub.add_parser("daemon", help="run all cadences in one asyncio process")
    daemon.add_argument("--ticks", type=int, help="stop after N scheduler ticks (testing)")

    return parser


def resolve_config(args: argparse.Namespace) -> Config:
    if args.example_config:
        return example_config()
    return load_config(args.config)


def _region(config: Config, args: argparse.Namespace) -> int:
    return getattr(args, "region", None) or config.esi.home_region_id


def _open_db(config: Config):
    from .store.db import Database

    return Database(config.paths.ensure().db)


def _latest_book(config: Config, region: int):
    from .store.lake import BookLake

    return BookLake(config.paths).latest(region)


def _composite_and_bars(config: Config, db, region: int):
    """Load the lake, build the benchmark. Returns (tracked bars, composite, ALL bars).

    The third element carries the unfiltered lake: watchlist names live outside
    the tracked universe by design, and their bars must not vanish with the floor.
    """
    from .indices import FORGE
    from .signals.composite import TURNOVER, build_composite, clamp_settings
    from .store.lake import BarLake
    from .universe import index_eligible_type_ids, tracked_type_ids

    all_bars = BarLake(config.paths).read(region)
    bars = all_bars
    tracked = tracked_type_ids(db, region)
    if tracked and not bars.empty:
        bars = bars[bars["type_id"].isin(tracked)]
    # FORGE holds OK-tier names only. THIN names stay in `bars` — they are
    # charted, scanned and briefed — but a name you cannot get out of at size
    # does not get to move the market read (§11 D3, amended).
    eligible = index_eligible_type_ids(db, region)
    composite = build_composite(
        bars,
        members=config.signals.composite_members,
        single_cap=config.signals.composite_single_weight_cap,
        rebalance_days=config.signals.composite_rebalance_days,
        **clamp_settings(config.signals),
        weighting=TURNOVER,
        member_ids=eligible or None,
        ticker=FORGE,
        name="Forge Composite",
    )
    return bars, composite, all_bars


def _anchor_dates(config: Config) -> list[str]:
    """Only CONFIRMED anchors reach a computation (plan.md §11 D7)."""
    from .signals.anchors import load_anchors

    path = Path.cwd() / "config" / "anchors.jsonl"
    return [anchor.anchor_date.isoformat() for anchor in load_anchors(path) if anchor.confirmed]


# -- commands ---------------------------------------------------------------


def _cmd_selftest(config: Config, args) -> int:
    from .selftest import run_selftest, selftest_report

    checks = run_selftest(config, repo_root=Path.cwd())
    print(selftest_report(checks))
    return 0 if all(check.ok for check in checks) else 1


def _cmd_sde(config: Config, args) -> int:
    from .sde import load_sde

    with _open_db(config) as db:
        result = load_sde(
            config, db, force=args.force, bundle_path=Path(args.bundle) if args.bundle else None
        )
    print(json.dumps(result.as_dict(), indent=2))
    return 0


def _cmd_census(config: Config, args) -> int:
    from .census import render_census, run_census, write_census
    from .esi.client import EsiClient

    def progress(index, total, result):
        print(
            f"  ... {index}/{total} types (fetched {result.fetched}, "
            f"fresh {result.skipped_fresh}, no-history {result.no_history}, "
            f"failed {result.failed})",
            flush=True,
        )

    async def run() -> int:
        with _open_db(config) as db:
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


def _hauling_history_scope(config: Config, db, limit: int) -> dict[int, list[int]]:
    """Destination types per non-home hub region (plan.md §23, H3).

    The exit is what the liquidity scenarios are about, and the exit happens in
    the destination region — whose history this system has never fetched,
    because every earlier surface lives in The Forge. The candidate set is what
    the depth generations actually carry a **bid** for at that hub: fetching
    the whole catalogue of five regions would be tens of thousands of requests
    for bars nothing reads.

    Bounded per region, and the bound is reported rather than silently applied.
    """
    from .books import depth_stations
    from .store.lake import DepthLake

    lake = DepthLake(config.paths)
    scope: dict[int, list[int]] = {}
    for region in config.esi.secondary_region_ids:
        if not depth_stations(config, db, int(region)):
            continue
        frame = lake.latest(int(region))
        if frame.empty:
            continue
        bids = frame[frame["side"] == "buy"]
        if bids.empty:
            continue
        ranked = bids.groupby("type_id")["cumulative_notional"].max().sort_values(ascending=False)
        scope[int(region)] = [int(value) for value in ranked.index[: max(0, int(limit))]]
    return scope


def _cmd_ingest_history(config: Config, args) -> int:
    from .bars import ingest_history
    from .esi.client import EsiClient
    from .store.lake import BarLake
    from .universe import tracked_type_ids, watchlist_type_ids

    region = _region(config, args)

    async def run_hauling() -> int:
        """Destination bars, one hub region at a time, inside the 150/min cap."""
        with _open_db(config) as db:
            scope = _hauling_history_scope(config, db, args.max_types or 400)
            if not scope:
                print(
                    "no hauling destinations with a swept bid book — run `sweep-books "
                    "--secondary` first (honest zero, not an error)"
                )
                return 0
            client = EsiClient(config, db)
            outcomes = []
            try:
                for hub_region, ids in sorted(scope.items()):
                    result = await ingest_history(
                        client,
                        BarLake(config.paths),
                        ids,
                        region_id=hub_region,
                        skip_type_ids=db.history_missing(hub_region),
                    )
                    if result.missing_type_ids:
                        db.mark_history_missing(result.missing_type_ids, hub_region)
                    outcomes.append(
                        {
                            "region_id": hub_region,
                            "requested_cap": args.max_types,
                            **result.as_dict(),
                        }
                    )
            finally:
                await client.aclose()
        print(json.dumps(outcomes, indent=2))
        return 0

    async def run() -> int:
        with _open_db(config) as db:
            if args.type_id:
                ids = list(args.type_id)
            elif args.scope == "watchlist":
                ids = watchlist_type_ids(db)
            elif args.scope == "all":
                ids = [
                    int(row["type_id"])
                    for row in db.conn.execute(
                        "SELECT type_id FROM universe WHERE region_id=? ORDER BY type_id",
                        (region,),
                    )
                ]
            else:
                ids = tracked_type_ids(db, region)
            if not ids:
                print("no types in scope — run `census` first (honest zero, not an error)")
                return 0
            client = EsiClient(config, db)
            try:
                result = await ingest_history(
                    client,
                    BarLake(config.paths),
                    ids,
                    region_id=region,
                    skip_type_ids=db.history_missing(region),
                )
            finally:
                await client.aclose()
            if result.missing_type_ids:
                db.mark_history_missing(result.missing_type_ids, region)
        print(json.dumps(result.as_dict(), indent=2))
        return 0

    return asyncio.run(run_hauling() if args.scope == "hauling" else run())


def _cmd_sweep_books(config: Config, args) -> int:
    from .books import depth_bound, depth_jump_distance, depth_stations, sweep_region
    from .esi.client import EsiClient
    from .store.lake import BookLake, DepthLake

    async def run() -> int:
        regions = [_region(config, args)]
        if args.secondary:
            regions.extend(config.esi.secondary_region_ids)
        outcomes = []
        with _open_db(config) as db:
            client = EsiClient(config, db)
            lake = BookLake(config.paths.ensure())
            # The same pages, reduced a second way (plan.md §23.6). No extra
            # request, no cadence change, and the two products share a
            # generation id by construction.
            depth_lake = DepthLake(config.paths)
            bound = depth_bound(config, db)
            jumps = depth_jump_distance(db)
            try:
                for region in regions:
                    result = await sweep_region(
                        config,
                        client,
                        lake,
                        region,
                        persist_raw_to=Path(args.debug_raw) if args.debug_raw else None,
                        depth_lake=depth_lake,
                        stations=depth_stations(config, db, region),
                        bound=bound,
                        jump_distance=jumps,
                    )
                    outcomes.append(result.as_dict())
            finally:
                await client.aclose()
        print(json.dumps(outcomes, indent=2))
        return 0

    return asyncio.run(run())


def _build_screen(config: Config, db, region: int, *, with_watchlist: bool = False):
    """The ranked screen, plus (optionally) the always-shown watchlist rows."""
    from .brief import watchlist_summary
    from .killmails import destruction_frame, destruction_z
    from .paper import PaperLedger
    from .screen import run_screen

    bars, composite, all_bars = _composite_and_bars(config, db, region)
    lake_types = sorted(bars["type_id"].unique().tolist()) if not bars.empty else []
    destruction = destruction_z(
        destruction_frame(db, type_ids=lake_types),
        recent_days=config.killmails.destruction_recent_days,
        baseline_days=config.killmails.destruction_baseline_days,
    )
    book = _latest_book(config, region)
    anchor_dates = _anchor_dates(config)
    screen = run_screen(
        config,
        db,
        bars,
        composite,
        book,
        destruction=destruction,
        anchor_dates=anchor_dates,
        region_id=region,
        paper_records=PaperLedger(config.paths.ensure().paper_ledger, config).records(),
    )
    if not with_watchlist:
        return screen
    watch_rows = watchlist_summary(
        config,
        db,
        all_bars,
        getattr(composite, "frame", None),
        book,
        anchor_dates=anchor_dates,
        region_id=region,
    )
    return screen, watch_rows


def _cmd_digest(config: Config, args) -> int:
    from .digest import build_digest, post_digest
    from .paper import PaperLedger
    from .report import _latest, _load

    region = _region(config, args)
    with _open_db(config) as db:
        screen, watch_rows = _build_screen(config, db, region, with_watchlist=True)
        paper = PaperLedger(config.paths.ensure().paper_ledger, config).report()
        reports = config.paths.reports
        backtest = _load(_latest(reports, "backtest")) or {}
        lead_lag = _load(_latest(reports, "leadlag")) or {}
        cross = _load(_latest(reports, "crossregion"))
        content = build_digest(
            config,
            screen,
            paper_report=paper,
            cross_region=_ScanView(cross) if cross else None,
            backtest_verdict=backtest.get("verdicts"),
            lead_lag_outcome=lead_lag.get("outcome"),
            watchlist=watch_rows,
        )
    if args.dry_run:
        print(content)
        return 0
    result = post_digest(config, content, archive_path=config.paths.digests)
    print(content)
    print(f"\ndelivery: {json.dumps(result.as_dict())}")
    return 0 if result.kind in {"delivered", "unconfigured"} else 1


class _ScanView:
    """Adapt a stored cross-region JSON payload to the digest's expectations."""

    def __init__(self, payload: dict) -> None:
        self.rows = payload.get("rows") or []
        self.dropped_no_freight = payload.get("dropped_no_freight", 0)


def _cmd_backtest(config: Config, args) -> int:
    from .backtest import render_backtest, run_backtest, write_backtest
    from .setups import SETUPS_FILE, load_setups

    region = _region(config, args)
    setup = None
    if getattr(args, "setup", None):
        wanted = args.setup.casefold()
        matched = [
            candidate
            for candidate in load_setups(Path.cwd() / "config" / SETUPS_FILE)
            if candidate.name.casefold() == wanted
        ]
        if not matched:
            print(f"no setup named {args.setup!r}; run `setups` to list them")
            return 2
        setup = matched[0]

    def progress(index, total, found):
        print(f"  ... {index}/{total} types scanned, {found} instances", flush=True)

    with _open_db(config) as db:
        bars, composite, _all_bars = _composite_and_bars(config, db, region)
        if args.max_types and not bars.empty:
            keep = sorted(bars["type_id"].unique())[: args.max_types]
            bars = bars[bars["type_id"].isin(keep)]
        result = run_backtest(
            config,
            bars,
            composite.frame,
            _latest_book(config, region),
            db=db,
            setup=setup,
            region_id=region,
            anchor_dates=_anchor_dates(config),
            progress=progress,
        )
        json_path, md_path = write_backtest(config, result)
    print(render_backtest(result))
    print(f"\nwritten: {json_path}\n         {md_path}")
    return 0


def _cmd_killmails(config: Config, args) -> int:
    from .killmails import (
        backfill_archives,
        destruction_frame,
        destruction_z,
        poll_r2z2,
        render_lead_lag,
        run_lead_lag_study,
    )
    from .paths import atomic_write_text

    region = _region(config, args)
    with _open_db(config) as db:
        if args.backfill:

            def progress(index, total, result):
                print(f"  ... {index}/{total} days, {result.killmails:,} killmails", flush=True)

            print(
                json.dumps(
                    backfill_archives(config, db, days=args.backfill, progress=progress).as_dict(),
                    indent=2,
                )
            )
        if args.poll:
            print(json.dumps(poll_r2z2(config, db).as_dict(), indent=2))
        if args.study:
            bars, _, _all_bars = _composite_and_bars(config, db, region)
            lake_types = sorted(bars["type_id"].unique().tolist()) if not bars.empty else []
            scores = destruction_z(
                destruction_frame(db, type_ids=lake_types),
                recent_days=config.killmails.destruction_recent_days,
                baseline_days=config.killmails.destruction_baseline_days,
            )
            result = run_lead_lag_study(config, bars, scores)
            stem = f"leadlag-{result.generated_at[:10]}"
            paths = config.paths.ensure()
            atomic_write_text(
                paths.reports / f"{stem}.json",
                json.dumps(result.as_dict(), indent=2, sort_keys=True, default=str),
            )
            atomic_write_text(paths.reports / f"{stem}.md", render_lead_lag(result))
            print(render_lead_lag(result))
            print(f"\nwritten: {paths.reports / stem}.json / .md")
    return 0


def _cmd_cross_region(config: Config, args) -> int:
    from .crossregion import render_cross_region, scan_cross_region
    from .paths import atomic_write_text
    from .store.lake import BookLake

    lake = BookLake(config.paths.ensure())
    regions = [config.esi.home_region_id, *config.esi.secondary_region_ids]
    books = {region: lake.latest(region) for region in regions}
    with _open_db(config) as db:
        scan = scan_cross_region(config, db, books, tier_index=args.tier)
    stem = f"crossregion-{scan.generated_at[:10]}"
    paths = config.paths.ensure()
    atomic_write_text(
        paths.reports / f"{stem}.json", json.dumps(scan.as_dict(), indent=2, sort_keys=True)
    )
    atomic_write_text(paths.reports / f"{stem}.md", render_cross_region(scan))
    print(render_cross_region(scan))
    return 0


def _cmd_paper(config: Config, args) -> int:
    from .paper import PaperLedger, Refusal, render_report
    from .store.lake import BarLake
    from .universe import liquidity_table

    region = _region(config, args)
    paths = config.paths.ensure()
    ledger = PaperLedger(paths.paper_ledger, config)
    book = _latest_book(config, region)

    with _open_db(config) as db:
        try:
            if args.paper_command == "open":
                type_id = args.type_id
                if type_id is None:
                    if not args.name:
                        print(
                            "give --type-id or --name; a paper open must name what it is buying",
                            file=sys.stderr,
                        )
                        return 2
                    row = db.type_by_name(args.name)
                    if row is None:
                        # An unresolvable name is a loud error, never a guess.
                        print(
                            f"no type named {args.name!r} in the SDE — "
                            "run `sde` first, or check the spelling",
                            file=sys.stderr,
                        )
                        return 2
                    type_id = int(row["type_id"])
                turnover = liquidity_table(
                    BarLake(config.paths),
                    region,
                    lookback_days=config.universe.liquidity_lookback_days,
                )
                median = None
                if not turnover.empty:
                    match = turnover[turnover["type_id"] == type_id]
                    if not match.empty:
                        median = float(match.iloc[0]["median_isk_value"])
                type_row = db.type_by_id(type_id)
                record = ledger.open_position(
                    type_id=type_id,
                    type_name=type_row["name"] if type_row else None,
                    notional_isk=args.notional or config.paper.default_notional_isk,
                    book=book,
                    thesis=args.thesis,
                    setup_tag=args.setup,
                    like_tags=args.like,
                    reason_text=args.reason_text,
                    stop_price=args.stop,
                    target_price=args.target,
                    median_daily_turnover=median,
                    vocabulary=_vocabulary(),
                    fill_model=args.fill_model,
                )
                print(json.dumps(record, indent=2, default=str))
            elif args.paper_command == "pass":
                type_id = args.type_id
                if type_id is None:
                    if not args.name:
                        print("give --type-id or --name", file=sys.stderr)
                        return 2
                    row = db.type_by_name(args.name)
                    if row is None:
                        print(
                            f"no type named {args.name!r} in the SDE — "
                            "run `sde` first, or check the spelling",
                            file=sys.stderr,
                        )
                        return 2
                    type_id = int(row["type_id"])
                type_row = db.type_by_id(type_id)
                record = ledger.record_pass(
                    type_id=type_id,
                    type_name=type_row["name"] if type_row else None,
                    action=args.action,
                    dislike_tags=args.dislike,
                    reason_text=args.reason_text,
                    setup_tag=args.setup,
                    vocabulary=_vocabulary(),
                )
                print(json.dumps(record, indent=2, default=str))
            elif args.paper_command == "close":
                print(
                    json.dumps(
                        ledger.close_position(
                            position_id=args.position_id,
                            book=book,
                            note=args.note,
                            fill_model=args.fill_model,
                        ),
                        indent=2,
                        default=str,
                    )
                )
            elif args.paper_command == "mark":
                print(json.dumps(ledger.mark(book=book), indent=2, default=str))
            elif args.paper_command == "real-fill":
                print(
                    json.dumps(
                        ledger.record_real_fill(
                            position_id=args.position_id,
                            side=args.side,
                            actual_price=args.price,
                            actual_units=args.units,
                        ),
                        indent=2,
                        default=str,
                    )
                )
            else:
                print(render_report(ledger.report()))
        except Refusal as refusal:
            print(f"REFUSED: {refusal}", file=sys.stderr)
            print("The refusal is recorded in the ledger; it is a result, not a crash.")
            return 3
    return 0


def _resolve_type_id(db, args) -> int | None:
    """`--type-id` wins; `--name` resolves loudly or not at all (never a guess)."""
    if getattr(args, "type_id", None) is not None:
        return int(args.type_id)
    if not getattr(args, "name", None):
        print("give --type-id or --name", file=sys.stderr)
        return None
    row = db.type_by_name(args.name)
    if row is None:
        print(
            f"no type named {args.name!r} in the SDE — run `sde` first, or check the spelling",
            file=sys.stderr,
        )
        return None
    return int(row["type_id"])


def _cmd_watch(config: Config, args) -> int:
    from .universe import add_watch, remove_watch, watchlist_entries

    with _open_db(config) as db:
        if args.watch_command == "add":
            type_id = _resolve_type_id(db, args)
            if type_id is None:
                return 2
            record = add_watch(db, name=args.name, type_id=type_id, note=args.note)
            print(json.dumps(record, indent=2))
            return 0
        if args.watch_command == "remove":
            if remove_watch(db, args.name):
                print(f"removed {args.name!r} — an operator action, recorded by its absence")
                return 0
            print(f"{args.name!r} is not on the watchlist; nothing removed", file=sys.stderr)
            return 1
        entries = watchlist_entries(db)
        if not entries:
            print("watchlist is empty — add names with `watch add --name ...`")
            return 0
        for row in entries:
            resolved = row["type_id"] if row["type_id"] is not None else "UNRESOLVED"
            note = f" · {row['note']}" if row["note"] else ""
            print(f"{row['name']}  (type {resolved}, added {row['added_at'][:10]}){note}")
        return 0


def _cmd_brief(config: Config, args) -> int:
    from .brief import build_brief, render_brief
    from .killmails import destruction_frame, destruction_z

    region = _region(config, args)
    with _open_db(config) as db:
        type_id = _resolve_type_id(db, args)
        if type_id is None:
            return 2
        _bars, composite, all_bars = _composite_and_bars(config, db, region)
        frame = all_bars[all_bars["type_id"] == type_id] if not all_bars.empty else all_bars
        scores = destruction_z(
            destruction_frame(db, type_ids=[type_id]),
            recent_days=config.killmails.destruction_recent_days,
            baseline_days=config.killmails.destruction_baseline_days,
        )
        latest_z = None
        if scores is not None and not scores.empty:
            latest_z = float(scores.sort_values("day").iloc[-1]["destruction_z"])
        brief = build_brief(
            config,
            db,
            frame,
            getattr(composite, "frame", None),
            _latest_book(config, region),
            type_id,
            region_id=region,
            anchor_dates=_anchor_dates(config),
            destruction_z=latest_z,
        )
    print(render_brief(brief))
    return 0


def _cmd_board(config: Config, args) -> int:
    from .brief import build_board, render_board
    from .universe import watchlist_type_ids

    region = _region(config, args)
    with _open_db(config) as db:
        bars, composite, all_bars = _composite_and_bars(config, db, region)
        watch_ids = set(watchlist_type_ids(db))
        # The board covers the tracked universe PLUS the watchlist: an
        # operator's name renders even below the liquidity floor (§11 D4).
        if watch_ids and not all_bars.empty:
            scope = set(bars["type_id"].unique().tolist()) | watch_ids
            frame = all_bars[all_bars["type_id"].isin(sorted(scope))]
        else:
            frame = bars
        board = build_board(
            config,
            db,
            frame,
            getattr(composite, "frame", None),
            _latest_book(config, region),
            watch_ids=watch_ids,
            anchor_dates=_anchor_dates(config),
            region_id=region,
            top=args.top,
            sort=args.sort,
        )
    print(render_board(board))
    return 0


def _sector_context(config: Config, db, bars, region: int):
    """Sector definitions and their index frames, or empty when unbuilt.

    A sector that could not be built simply is not in the returned mapping,
    which makes every `rrs scope=sector` condition on its members UNKNOWN
    rather than silently answered with FORGE.
    """
    from .indices import build_index_set, load_sectors
    from .universe import index_eligible_type_ids

    sectors = load_sectors(Path.cwd() / "config" / "sectors.jsonl")
    if not sectors or bars is None or bars.empty:
        return sectors, {}
    volumes = {
        int(row["type_id"]): float(row["median_unit_volume"] or 0.0)
        for row in db.conn.execute(
            "SELECT type_id, median_unit_volume FROM universe WHERE region_id=?", (region,)
        )
    }
    index_set = build_index_set(
        config,
        db,
        bars,
        member_ids=index_eligible_type_ids(db, region) or None,
        unit_volume=volumes,
        sectors=sectors,
    )
    frames = {
        ticker: composite.frame
        for ticker, composite in index_set.sectors.items()
        if composite.known
    }
    return sectors, frames


def _vocabulary():
    """The committed reason vocabulary, or an empty one if none is present."""
    from .reasons import REASONS_FILE, load_reasons

    return load_reasons(Path.cwd() / "config" / REASONS_FILE)


def _cmd_learning(config: Config, args) -> int:
    from .backtest import measure_haircuts
    from .learning import build_learning_report, render_learning
    from .paper import PaperLedger
    from .report import _latest, _load
    from .setups import SETUPS_FILE, load_setups
    from .store.lake import BarLake

    region = _region(config, args)
    ledger = PaperLedger(config.paths.paper_ledger, config)
    setups = load_setups(Path.cwd() / "config" / SETUPS_FILE)
    stored = _load(_latest(config.paths.reports, "backtest")) or {}
    tested = {stored.get("params", {}).get("setup")} - {None}
    report = build_learning_report(
        config,
        ledger,
        bars=BarLake(config.paths).read(region),
        haircuts=measure_haircuts(
            _latest_book(config, region), tuple(config.costs.notional_tiers_isk)
        ),
        setups=setups,
        vocabulary=_vocabulary(),
        backtested=tested,
        horizon_days=args.horizon,
    )
    print(render_learning(report))
    return 0


def _cmd_reasons(config: Config, args) -> int:
    vocabulary = _vocabulary()
    if not vocabulary:
        print("no config/reasons.jsonl — decisions cannot be qualified until there is one")
        return 2
    for direction, reasons in (("like", vocabulary.likes), ("dislike", vocabulary.dislikes)):
        if args.direction and args.direction != direction:
            continue
        print(f"# why I {'like' if direction == 'like' else 'do not like'} it")
        for reason in reasons:
            print(f"  {reason.tag:<24} {reason.label}")
            if reason.notes:
                print(f"      {reason.notes}")
    return 0


def _cmd_setups(config: Config, args) -> int:
    from .setups import SETUPS_FILE, describe_condition, load_setups

    path = Path.cwd() / "config" / SETUPS_FILE
    setups = load_setups(path)
    if not setups:
        print(f"no setups in {path} — the scanner will run the built-in rule only")
        return 0
    for setup in setups:
        if not setup.enabled and not args.all:
            continue
        marks = ["enabled" if setup.enabled else "DISABLED"]
        if setup.example:
            marks.append("example")
        print(f"{setup.name} [{', '.join(marks)}]")
        if setup.notes:
            print(f"  {setup.notes}")
        for condition in setup.conditions:
            print(f"    - {describe_condition(condition)}")
    return 0


def _cmd_scan(config: Config, args) -> int:
    from .report import _latest, _load
    from .scanner import render_scan, run_scan
    from .setups import SETUPS_FILE, load_setups

    region = _region(config, args)
    setups = load_setups(Path.cwd() / "config" / SETUPS_FILE)
    if args.setup:
        wanted = args.setup.casefold()
        matched = [setup for setup in setups if setup.name.casefold() == wanted]
        if not matched:
            print(f"no setup named {args.setup!r}; run `setups` to list them")
            return 2
        setups = matched
    reports = config.paths.reports
    backtest = _load(_latest(reports, "backtest")) or {}
    with _open_db(config) as db:
        bars, composite, _all_bars = _composite_and_bars(config, db, region)
        sectors, sector_frames = _sector_context(config, db, bars, region)
        result = run_scan(
            config,
            db,
            bars,
            getattr(composite, "frame", None),
            _latest_book(config, region),
            setups=setups,
            sectors=sectors,
            sector_frames=sector_frames,
            anchor_dates=_anchor_dates(config),
            region_id=region,
            backtest_verdict=backtest.get("verdicts"),
        )
    for scan in result.setups:
        scan.hits = scan.hits[: max(1, int(args.top))]
    print(render_scan(result))
    return 0


def _cmd_anchors(config: Config, args) -> int:
    from .patchnotes import FeedError, fetch_patch_notes, sync_anchor_candidates
    from .signals.anchors import load_anchors

    calendar = Path.cwd() / "config" / "anchors.jsonl"
    if args.list:
        for anchor in load_anchors(calendar):
            mark = "confirmed" if anchor.confirmed else "CANDIDATE"
            print(f"{anchor.anchor_date} [{mark}] {anchor.label} ({anchor.scope})")
        return 0
    try:
        notes = fetch_patch_notes(config)
    except FeedError as exc:
        print(f"patch-notes feed unavailable: {exc}", file=sys.stderr)
        return 1
    added = sync_anchor_candidates(notes, calendar, market_relevant_only=not args.all)
    print(f"{len(notes)} post(s) in the feed; {len(added)} new candidate(s) appended")
    for note in added:
        print(f"  {note.published}  {note.title}")
    if added:
        print(
            "\nThese are CANDIDATES. The signal layer ignores them until you set"
            '\n"confirmed": true in config/anchors.jsonl. Nothing anchors itself.'
        )
    return 0


def _cmd_gui(config: Config, args) -> int:
    """Open the desk. Qt is imported inside `run_desk`, never at module scope."""
    from .gui import run_desk

    return run_desk(config)


def _resolve_system(db, name: str | None, system_id: int | None) -> int | None:
    """`--from-id` wins; a name resolves loudly or not at all (never a guess)."""
    if system_id is not None:
        return int(system_id)
    if not name:
        return None
    row = db.system_by_name(name)
    if row is None:
        raise ConfigError(
            f"no solar system named {name!r} in the SDE — run `sde` first, or check the spelling"
        )
    return int(row["solar_system_id"])


def _ship_profile(config: Config, db, args):
    """The ship this scan is for: a saved profile, flags, or a loud refusal."""
    from .hauling import ShipProfile

    ship = None
    if getattr(args, "ship", None):
        row = db.haul_profile(args.ship)
        if row is None:
            names = [entry["name"] for entry in db.haul_profiles()]
            raise ConfigError(
                f"no ship profile named {args.ship!r}; stored: {names or 'none'} — "
                "add one with `haul profile add`"
            )
        ship = ShipProfile.from_row(row)
    if ship is None:
        if args.cargo is None:
            raise ConfigError(
                "give --cargo or --ship: cargo is what caps the size, and guessing it "
                "would rank plans you cannot actually carry"
            )
        ship = ShipProfile.from_config(config, name="ad hoc", cargo_m3=args.cargo)
    overrides = {}
    if args.cargo is not None:
        overrides["usable_cargo_m3"] = float(args.cargo)
    if args.seconds_per_jump is not None:
        overrides["seconds_per_jump"] = float(args.seconds_per_jump)
    if args.handling_minutes is not None:
        overrides["handling_minutes"] = float(args.handling_minutes)
    if overrides:
        from dataclasses import replace as _replace

        ship = _replace(ship, **overrides)
    return ship


def _cmd_haul(config: Config, args) -> int:
    from .haulfreight import attach_freight
    from .hauling import HaulProfile, scan_hauls, scan_inputs
    from .haulledger import HaulLedger, HaulRefusal
    from .haulreport import build_haul_report, render_haul_report, write_haul_report
    from .liquidity import liquidity_attachment
    from .routes import RouteCache

    paths = config.paths.ensure()
    with _open_db(config) as db:
        if args.haul_command == "profile":
            return _cmd_haul_profile(config, db, args)
        if args.haul_command == "record":
            ledger = HaulLedger(paths.paper_hauls, config)
            try:
                return _cmd_haul_record(db, ledger, args)
            except HaulRefusal as refusal:
                print(f"REFUSED: {refusal}", file=sys.stderr)
                print("The refusal is recorded in the ledger; it is a result, not a crash.")
                return 3

        try:
            ship = _ship_profile(config, db, args)
            current = _resolve_system(db, args.current_system, args.current_system_id)
            intended = _resolve_system(db, args.intended_destination, args.intended_destination_id)
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        overrides = {"current_system": current, "intended_destination": intended, "mode": args.mode}
        for flag, key in (
            ("capital", "capital_isk"),
            ("exposure", "max_exposure_isk"),
            ("minutes", "session_minutes"),
            ("max_wait_days", "max_wait_days"),
            ("max_jumps", "max_jumps"),
            ("security", "security_profile"),
            ("objective", "objective"),
        ):
            value = getattr(args, flag, None)
            if value is not None:
                overrides[key] = value
        overrides = {key: value for key, value in overrides.items() if value is not None}

        try:
            profile = HaulProfile.from_config(config, ship=ship, **overrides)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        sources, destinations, depths, graph, names, badges, packaged = scan_inputs(config, db)
        liquidity = liquidity_attachment(config, db, depths, profile)
        scan = scan_hauls(
            config,
            profile,
            stations=sources,
            destinations=destinations,
            depths=depths,
            graph=graph,
            route_cache=RouteCache(db, enabled=config.routes.cache),
            names=names,
            badges=badges,
            packaged_volume=packaged,
            liquidity=liquidity,
        )
        if args.freight:
            # A third-party quote is asked for deliberately, never on every
            # scan: it is somebody else's service (§9 R10).
            attach_freight(config, db, scan, limit=args.freight_top)
        report = build_haul_report(scan, config=config)

    if not args.no_write:
        json_path, md_path = write_haul_report(config, report)
    print(render_haul_report({**report, "rows": report["rows"][: args.top]}))
    if args.show_rejected:
        print("\n## Rejected candidates\n")
        for rejection in scan.rejected[:200]:
            print(
                f"- `{rejection.reason}` {rejection.type_name or rejection.type_id or ''} "
                f"{rejection.source_station or ''}→{rejection.dest_station or ''} "
                f"{rejection.detail}"
            )
    if not args.no_write:
        print(f"\nwritten: {json_path}\n         {md_path}")
    return 0


def _cmd_haul_profile(config: Config, db, args) -> int:
    from .timeutil import iso, utcnow

    if args.profile_command == "add":
        # An omitted flag stores the configured default explicitly rather than
        # NULL: a profile that reads back 0 seconds per jump would price every
        # trip as instantaneous.
        db.put_haul_profile(
            {
                "name": args.name,
                "usable_cargo_m3": float(args.cargo),
                "ehp": args.ehp,
                "ship_value_isk": args.value,
                "seconds_per_jump": (
                    args.seconds_per_jump
                    if args.seconds_per_jump is not None
                    else config.hauling.default_seconds_per_jump
                ),
                "handling_minutes": (
                    args.handling_minutes
                    if args.handling_minutes is not None
                    else config.hauling.default_handling_minutes
                ),
                "created_at": iso(utcnow()),
            }
        )
        print(json.dumps({"stored": args.name, "usable_cargo_m3": args.cargo}, indent=2))
        return 0
    if args.profile_command == "remove":
        if db.delete_haul_profile(args.name):
            print(f"removed {args.name!r} — an operator action, the only removal path")
            return 0
        print(f"no ship profile named {args.name!r}", file=sys.stderr)
        return 1
    rows = [dict(row) for row in db.haul_profiles()]
    if not rows:
        print("no ship profiles yet — add one with `haul profile add`")
        return 0
    print(json.dumps(rows, indent=2, default=str))
    return 0


def _cmd_haul_record(db, ledger, args) -> int:
    from .haulledger import render_haul_tally

    if args.record_command == "report":
        print(render_haul_tally(ledger.tally()))
        return 0
    if args.record_command == "close":
        print(
            json.dumps(
                ledger.record_close(
                    haul_id=args.haul_id,
                    actual_proceeds_isk=args.proceeds,
                    actual_cost_isk=args.cost,
                    note=args.note,
                ),
                indent=2,
                default=str,
            )
        )
        return 0
    type_id = _resolve_type_id(db, args)
    if type_id is None:
        return 2
    row = db.type_by_id(type_id)
    name = row["name"] if row else None
    if args.record_command == "open":
        record = ledger.record_open(
            type_id=type_id,
            type_name=name,
            quantity=args.quantity,
            source_station=args.source,
            dest_station=args.dest,
            thesis=args.thesis,
            like_tags=args.like,
            reason_text=args.reason_text,
            expected_cost_isk=args.cost,
            expected_net_isk=args.expected_net,
            route_jumps=args.jumps,
            vocabulary=_vocabulary(),
        )
    elif args.record_command == "pass":
        record = ledger.record_pass(
            type_id=type_id,
            type_name=name,
            action=args.action,
            dislike_tags=args.dislike,
            reason_text=args.reason_text,
            source_station=args.source,
            dest_station=args.dest,
            vocabulary=_vocabulary(),
        )
    else:  # pragma: no cover - argparse restricts the set
        record = {}
    print(json.dumps(record, indent=2, default=str))
    return 0


def _cmd_report(config: Config, args) -> int:
    from .paper import PaperLedger
    from .report import build_viability_report, render_viability, write_viability

    paths = config.paths.ensure()
    paper = PaperLedger(paths.paper_ledger, config).report().as_dict()
    report = build_viability_report(config, paper=paper)
    json_path, md_path = write_viability(config, report)
    print(render_viability(report))
    print(f"\nwritten: {json_path}\n         {md_path}")
    return 0


def _cmd_daemon(config: Config, args) -> int:
    from .bars import ingest_history
    from .books import depth_bound, depth_jump_distance, depth_stations, sweep_region
    from .digest import build_digest, post_digest
    from .esi.client import EsiClient
    from .killmails import poll_r2z2
    from .paper import PaperLedger
    from .store.lake import BarLake, BookLake, DepthLake
    from .universe import tracked_type_ids

    region = _region(config, args)

    async def run() -> int:
        from .daemon import run_daemon

        with _open_db(config) as db:
            client = EsiClient(config, db)
            bar_lake = BarLake(config.paths.ensure())
            book_lake = BookLake(config.paths)
            depth_lake = DepthLake(config.paths)
            bound = depth_bound(config, db)
            jumps = depth_jump_distance(db)

            def depth_kwargs(target_region: int) -> dict:
                """The depth reduction rides along with every governed sweep."""
                return {
                    "depth_lake": depth_lake,
                    "stations": depth_stations(config, db, target_region),
                    "bound": bound,
                    "jump_distance": jumps,
                }

            async def history():
                ids = tracked_type_ids(db, region)
                if not ids:
                    return {"skipped": "no tracked universe yet"}
                result = await ingest_history(
                    client,
                    bar_lake,
                    ids,
                    region_id=region,
                    skip_type_ids=db.history_missing(region),
                )
                if result.missing_type_ids:
                    db.mark_history_missing(result.missing_type_ids, region)
                return result.as_dict()

            async def books_home():
                return (
                    await sweep_region(config, client, book_lake, region, **depth_kwargs(region))
                ).as_dict()

            async def books_secondary():
                out = []
                for secondary in config.esi.secondary_region_ids:
                    out.append(
                        (
                            await sweep_region(
                                config, client, book_lake, secondary, **depth_kwargs(secondary)
                            )
                        ).as_dict()
                    )
                return out

            async def universe():
                from .census import run_census

                return (
                    await run_census(config, db, client, region_id=region, crawl=False)
                ).as_dict()

            def digest():
                screen, watch_rows = _build_screen(config, db, region, with_watchlist=True)
                paper = PaperLedger(config.paths.paper_ledger, config)
                paper.mark(book=book_lake.latest(region))
                content = build_digest(
                    config, screen, paper_report=paper.report(), watchlist=watch_rows
                )
                return post_digest(config, content, archive_path=config.paths.digests).as_dict()

            def killmails():
                return poll_r2z2(config, db).as_dict()

            def patch_notes():
                from .patchnotes import FeedError, fetch_patch_notes, sync_anchor_candidates

                calendar = Path.cwd() / "config" / "anchors.jsonl"
                try:
                    added = sync_anchor_candidates(fetch_patch_notes(config), calendar)
                except FeedError as exc:
                    return {"error": str(exc)}
                return {"candidates_added": [note.title for note in added]}

            def on_tick(outcomes, scheduler):
                for outcome in outcomes:
                    print(json.dumps(outcome, default=str), flush=True)

            try:
                scheduler = await run_daemon(
                    config,
                    {
                        "history": history,
                        "books_home": books_home,
                        "books_secondary": books_secondary,
                        "universe": universe,
                        "digest": digest,
                        "killmails": killmails,
                        "patch_notes": patch_notes,
                    },
                    stop_after=args.ticks,
                    on_tick=on_tick,
                )
            finally:
                await client.aclose()
            print(json.dumps(scheduler.status(), indent=2))
        return 0

    return asyncio.run(run())


HANDLERS = {
    "selftest": _cmd_selftest,
    "sde": _cmd_sde,
    "census": _cmd_census,
    "ingest-history": _cmd_ingest_history,
    "sweep-books": _cmd_sweep_books,
    "digest": _cmd_digest,
    "backtest": _cmd_backtest,
    "killmails": _cmd_killmails,
    "cross-region": _cmd_cross_region,
    "paper": _cmd_paper,
    "watch": _cmd_watch,
    "brief": _cmd_brief,
    "board": _cmd_board,
    "scan": _cmd_scan,
    "setups": _cmd_setups,
    "reasons": _cmd_reasons,
    "learning": _cmd_learning,
    "anchors": _cmd_anchors,
    "haul": _cmd_haul,
    "report": _cmd_report,
    "gui": _cmd_gui,
    "daemon": _cmd_daemon,
}


def _force_utf8_console() -> None:
    """Render UTF-8 regardless of the console's legacy codepage.

    Windows consoles still default to an ANSI codepage — cp1252 on the
    operator's desk — and this package renders arrows, sigmas and box rules
    into ordinary reports. `backtest` computed its whole result, wrote both
    files, and then died on `print(render_backtest(result))` because a single
    `→` has no cp1252 mapping. The output is UTF-8; say so rather than
    trusting the locale to guess it.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pytest capture, pipes to non-text sinks
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):  # pragma: no cover - exotic streams
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
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
