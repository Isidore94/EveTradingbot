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
        choices=("tracked", "watchlist", "all"),
        default="tracked",
        help="which types to refresh (default: the tracked universe)",
    )
    ingest.add_argument("--type-id", type=int, action="append", help="one type; repeatable")

    sweep = sub.add_parser("sweep-books", help="one governed order-book sweep, reduced on write")
    sweep.add_argument("--secondary", action="store_true", help="sweep the WARM secondary hubs too")
    sweep.add_argument("--debug-raw", help="persist a raw page sample here (fixture-building only)")

    digest = sub.add_parser("digest", help="build and post the daily digest")
    digest.add_argument("--dry-run", action="store_true", help="print it, do not post it")

    backtest = sub.add_parser("backtest", help="the historical viability study (plan.md §13)")
    backtest.add_argument("--max-types", type=int, help="cap the types scanned")

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
    paper_open = paper_sub.add_parser("open", help="price and record a taker entry")
    paper_open.add_argument("--type-id", type=int, help="type id (or use --name)")
    paper_open.add_argument("--name", help="type name, resolved against the SDE")
    paper_open.add_argument("--notional", type=float, help="ISK notional (default: config)")
    paper_open.add_argument("--thesis", required=True, help="why — one sentence you can argue with")
    paper_open.add_argument("--stop", type=float, help="stop price, for R sizing")
    paper_open.add_argument("--target", type=float, help="target price, for planned R")
    paper_close = paper_sub.add_parser("close", help="price and record a taker exit")
    paper_close.add_argument("--position-id", required=True)
    paper_close.add_argument("--note", default="")
    paper_close.add_argument(
        "--actual-price",
        type=float,
        help="gross unit price you REALLY sold at; the only way to close a "
        "position whose book can no longer price it",
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

    anchors = sub.add_parser(
        "anchors", help="patch-notes watcher: append anchor CANDIDATES for confirmation"
    )
    anchors.add_argument("--list", action="store_true", help="show the calendar and stop")
    anchors.add_argument("--all", action="store_true", help="include non-market-relevant posts")

    sub.add_parser("report", help="regenerate the viability report (plan.md §16)")

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
    from .signals.composite import build_composite
    from .store.lake import BarLake
    from .universe import tracked_type_ids

    all_bars = BarLake(config.paths).read(region)
    bars = all_bars
    tracked = tracked_type_ids(db, region)
    if tracked and not bars.empty:
        bars = bars[bars["type_id"].isin(tracked)]
    composite = build_composite(
        bars,
        members=config.signals.composite_members,
        single_cap=config.signals.composite_single_weight_cap,
        rebalance_days=config.signals.composite_rebalance_days,
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


def _cmd_ingest_history(config: Config, args) -> int:
    from .bars import ingest_history
    from .esi.client import EsiClient
    from .store.lake import BarLake
    from .universe import tracked_type_ids, watchlist_type_ids

    region = _region(config, args)

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

    return asyncio.run(run())


def _cmd_sweep_books(config: Config, args) -> int:
    from .books import sweep_region
    from .esi.client import EsiClient
    from .store.lake import BookLake

    async def run() -> int:
        regions = [_region(config, args)]
        if args.secondary:
            regions.extend(config.esi.secondary_region_ids)
        outcomes = []
        with _open_db(config) as db:
            client = EsiClient(config, db)
            lake = BookLake(config.paths.ensure())
            try:
                for region in regions:
                    result = await sweep_region(
                        config,
                        client,
                        lake,
                        region,
                        persist_raw_to=Path(args.debug_raw) if args.debug_raw else None,
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

    region = _region(config, args)

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
                    stop_price=args.stop,
                    target_price=args.target,
                    median_daily_turnover=median,
                )
                print(json.dumps(record, indent=2, default=str))
            elif args.paper_command == "close":
                print(
                    json.dumps(
                        ledger.close_position(
                            position_id=args.position_id, book=book, note=args.note
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
    from .books import sweep_region
    from .digest import build_digest, post_digest
    from .esi.client import EsiClient
    from .killmails import poll_r2z2
    from .paper import PaperLedger
    from .store.lake import BarLake, BookLake
    from .universe import tracked_type_ids

    region = _region(config, args)

    async def run() -> int:
        from .daemon import run_daemon

        with _open_db(config) as db:
            client = EsiClient(config, db)
            bar_lake = BarLake(config.paths.ensure())
            book_lake = BookLake(config.paths)

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
                return (await sweep_region(config, client, book_lake, region)).as_dict()

            async def books_secondary():
                out = []
                for secondary in config.esi.secondary_region_ids:
                    out.append((await sweep_region(config, client, book_lake, secondary)).as_dict())
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
    "anchors": _cmd_anchors,
    "report": _cmd_report,
    "daemon": _cmd_daemon,
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
