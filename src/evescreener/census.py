"""The universe census — plan.md §8 Phase 1.

The one-time full-catalog crawl: `/markets/{region}/types` for the active
universe, then history for every one of them (~20k types at the 150/min
self-cap ≈ 2 h 13 m). Run once, diff-append forever after.

Its deliverable is not "a job ran". It is the **measured opportunity map**:
how many types clear each candidate liquidity floor, what the turnover,
`order_count` and spread distributions actually look like, and the
empirically-derived floor that replaces the planning estimate. This census is
the denominator for every later "the universe is N" claim in the system.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

from .bars import HistoryIngestResult, ingest_history
from .books import spread_view
from .config import Config
from .esi.client import EsiClient
from .paths import atomic_write_text
from .store.db import Database
from .store.lake import BarLake, BookLake
from .timeutil import iso, utcnow
from .universe import (
    active_type_ids,
    apply_floor,
    liquidity_table,
    sync_universe,
)

# Candidate floors the census scores, so the chosen floor is a measurement and
# not a preference. ISK turnover x order_count, spanning three decades each.
# The grid was extended on 2026-08-20 after the first live census: its original
# loosest corner (10M ISK / 5 orders) captured only 88.2% of median daily
# turnover, so the frozen rule below could not resolve at all. The RULE is
# unchanged; only the candidates it chooses among were widened downward.
FLOOR_GRID_ISK = (0.0, 1e6, 10e6, 50e6, 100e6, 250e6, 500e6, 1e9, 5e9)
FLOOR_GRID_ORDERS = (0, 1, 5, 10, 30, 50, 100)

# Percentiles reported for every distribution. p50 and p90 are what the floor
# discussion actually turns on; the tails are there to show the shape.
PERCENTILES = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)

# Round-trip friction thresholds worth counting outright. A strategy's gross
# edge has to clear its friction plus sales tax, so "how many types are below
# X%" is the question that scopes every later idea.
HAIRCUT_THRESHOLDS = (0.5, 1.0, 2.0, 3.0, 5.0, 10.0)


@dataclass(slots=True)
class CensusResult:
    """The census as data, not prose. `report_path` renders it for humans."""

    region_id: int
    generated_at: str
    active_types: int
    types_with_bars: int
    total_bars: int
    lookback_days: int
    ingest: dict = field(default_factory=dict)
    turnover_percentiles: dict = field(default_factory=dict)
    order_count_percentiles: dict = field(default_factory=dict)
    spread_percentiles: dict = field(default_factory=dict)
    haircut_percentiles: dict = field(default_factory=dict)
    depth_coverage: dict = field(default_factory=dict)
    spoof_share: float | None = None
    structure_share: dict = field(default_factory=dict)
    book_sweep_ts: str | None = None
    unit_volume_percentiles: dict = field(default_factory=dict)
    membership: dict = field(default_factory=dict)
    floor_grid: list[dict] = field(default_factory=list)
    derived_floor: dict = field(default_factory=dict)
    market_group_breakdown: list[dict] = field(default_factory=list)
    quality: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "region_id": self.region_id,
            "generated_at": self.generated_at,
            "active_types": self.active_types,
            "types_with_bars": self.types_with_bars,
            "total_bars": self.total_bars,
            "lookback_days": self.lookback_days,
            "ingest": self.ingest,
            "turnover_percentiles": self.turnover_percentiles,
            "order_count_percentiles": self.order_count_percentiles,
            "spread_percentiles": self.spread_percentiles,
            "haircut_percentiles": self.haircut_percentiles,
            "depth_coverage": self.depth_coverage,
            "spoof_share": self.spoof_share,
            "structure_share": self.structure_share,
            "book_sweep_ts": self.book_sweep_ts,
            "unit_volume_percentiles": self.unit_volume_percentiles,
            "membership": self.membership,
            "floor_grid": self.floor_grid,
            "derived_floor": self.derived_floor,
            "market_group_breakdown": self.market_group_breakdown,
            "quality": self.quality,
            "notes": self.notes,
        }


def _percentiles(series: pd.Series) -> dict[str, float]:
    if series.empty:
        return {}
    values = series.dropna()
    if values.empty:
        return {}
    return {f"p{int(q * 100)}": float(values.quantile(q)) for q in PERCENTILES}


def score_floor_grid(table: pd.DataFrame) -> list[dict]:
    """How many types survive each candidate floor, and what they carry.

    The floor is chosen from this grid by the rule in `derive_floor`, stated
    before the numbers are looked at.
    """
    rows: list[dict] = []
    if table.empty:
        return rows
    total_turnover = float(table["median_isk_value"].sum())
    for isk_floor in FLOOR_GRID_ISK:
        for order_floor in FLOOR_GRID_ORDERS:
            survivors = table[
                (table["median_isk_value"] >= isk_floor)
                & (table["median_order_count"] >= order_floor)
            ]
            captured = float(survivors["median_isk_value"].sum())
            rows.append(
                {
                    "min_median_isk_value": isk_floor,
                    "min_median_order_count": order_floor,
                    "types": int(len(survivors)),
                    "share_of_types": round(len(survivors) / max(1, len(table)), 4),
                    "captured_daily_isk": captured,
                    "share_of_turnover": round(captured / total_turnover, 4)
                    if total_turnover
                    else 0.0,
                }
            )
    return rows


def derive_floor(grid: list[dict], *, target_turnover_share: float = 0.95) -> dict:
    """Pick the floor from the measurement, by a rule stated before the data.

    **Rule (frozen):** among the candidate floors that still capture at least
    `target_turnover_share` of the region's median daily ISK turnover, take the
    one that admits the *fewest* types. Rationale: the floor exists to remove
    series that cannot absorb a real notional, not to shrink the universe for
    its own sake — so we keep essentially all the money and drop as many
    un-tradeable names as that allows.
    """
    eligible = [row for row in grid if row["share_of_turnover"] >= target_turnover_share]
    if not eligible:
        return {
            "rule": "fewest types while capturing >= "
            f"{target_turnover_share:.0%} of median daily ISK turnover",
            "resolved": False,
            "reason": "no candidate floor captured the target turnover share",
        }
    best = min(eligible, key=lambda row: (row["types"], -row["share_of_turnover"]))
    return {
        "rule": "fewest types while capturing >= "
        f"{target_turnover_share:.0%} of median daily ISK turnover",
        "resolved": True,
        "min_median_isk_value": best["min_median_isk_value"],
        "min_median_order_count": best["min_median_order_count"],
        "types": best["types"],
        "share_of_turnover": best["share_of_turnover"],
    }


def _haircut_percentiles(book: pd.DataFrame, tiers: Sequence[float]) -> dict:
    """Percentiles of the measured taker round-trip cost at the smallest tier."""
    from .backtest import measure_haircuts

    haircuts = measure_haircuts(book, tuple(float(value) for value in tiers))
    if not haircuts:
        return {}
    smallest = float(tiers[0])
    values = pd.Series(
        [entry[smallest]["round_trip"] for entry in haircuts.values() if smallest in entry]
    )
    if values.empty:
        return {}
    percent = values * 100.0
    return {
        "tier_isk": smallest,
        "types_measured": int(len(percent)),
        "min": round(float(percent.min()), 4),
        **{f"p{int(q * 100)}": round(float(percent.quantile(q)), 4) for q in PERCENTILES},
        "types_below": {
            f"{threshold}": int((percent < threshold).sum()) for threshold in HAIRCUT_THRESHOLDS
        },
    }


def _volume_weighted_structure_share(side: pd.DataFrame) -> float | None:
    """Share of one side's resting volume that sits in player structures."""
    if side.empty:
        return None
    volume = pd.to_numeric(side["total_volume"], errors="coerce").fillna(0.0)
    station = pd.to_numeric(side["station_volume_share"], errors="coerce").fillna(1.0)
    total = float(volume.sum())
    if total <= 0:
        return None
    return round(float(((1.0 - station) * volume).sum() / total), 4)


def book_statistics(book: pd.DataFrame, tiers: Sequence[float]) -> dict:
    """Spread and depth distributions from the latest reduced sweep.

    This is the half of the opportunity map that history cannot answer: how
    wide the book actually is, and **what fraction of types can absorb a real
    notional at all**. A margin that cannot take 0.25B is not an opportunity,
    so the coverage numbers here are the honest ceiling on how many of the
    floored types are ever tradeable (plan.md §9 R5).
    """
    if book is None or book.empty:
        return {"reason": "no book sweep available; spread and depth are UNKNOWN"}
    view = spread_view(book)
    sells = book[book["side"] == "sell"]
    stats: dict = {
        "sweep_ts": str(book["sweep_ts"].max()),
        "types_with_two_sided_book": int(len(view)),
        "spread_percentiles": _percentiles(view["spread_pct"]) if not view.empty else {},
        "depth_coverage": {
            f"{float(tier):.0f}": round(float(sells[f"depth_fill_price_{index}"].notna().mean()), 4)
            for index, tier in enumerate(tiers)
            if f"depth_fill_price_{index}" in sells.columns
        },
        # §22 S2a redefined `top_order_volume_share` to describe the EXECUTABLE
        # book. The census is a diagnostic of the whole region, and §17's
        # recorded figure was taken region-wide — so it reads the region-wide
        # column and stays comparable with what is already written down.
        "spoof_flagged_share": round(
            float(
                (
                    pd.to_numeric(
                        sells.get("region_top_order_volume_share", sells["top_order_volume_share"]),
                        errors="coerce",
                    )
                    > 0.5
                ).mean()
            ),
            4,
        ),
        # The round-trip haircut a taker actually pays at the smallest tier —
        # half the spread in, half out, before tax. This is the number any
        # strategy's edge has to clear, so it belongs in the opportunity map
        # rather than inside a study.
        "round_trip_haircut_percentiles": _haircut_percentiles(book, tiers),
        # A bid above the ask is a data-quality event, not an arbitrage: the
        # pages of one sweep are not a perfectly atomic snapshot (§0 check #2).
        "crossed_books": int((view["spread_pct"] < 0).sum()) if not view.empty else 0,
        "structure_share": {
            "ask_volume_weighted": _volume_weighted_structure_share(sells),
            "bid_volume_weighted": _volume_weighted_structure_share(book[book["side"] == "buy"]),
        },
    }
    return stats


def market_group_breakdown(db: Database, table: pd.DataFrame, limit: int = 25) -> list[dict]:
    """Tracked-type counts and turnover by top-level market group."""
    if table.empty:
        return []
    rows: list[dict] = []
    for record in table.itertuples():
        type_row = db.type_by_id(int(record.type_id))
        if type_row is None or type_row["market_group_id"] is None:
            root_name = "(no market group)"
        else:
            chain = db.market_group_chain(int(type_row["market_group_id"]))
            root = chain[-1] if chain else None
            group = (
                db.conn.execute(
                    "SELECT name FROM sde_market_groups WHERE market_group_id=?", (root,)
                ).fetchone()
                if root is not None
                else None
            )
            root_name = group["name"] if group else "(unknown group)"
        rows.append(
            {
                "group": root_name,
                "isk": float(record.median_isk_value or 0.0),
                "tracked": bool(getattr(record, "tracked", False)),
            }
        )
    frame = pd.DataFrame(rows)
    grouped = (
        frame.groupby("group")
        .agg(types=("isk", "size"), tracked=("tracked", "sum"), daily_isk=("isk", "sum"))
        .reset_index()
        .sort_values("daily_isk", ascending=False)
        .head(limit)
    )
    return grouped.to_dict("records")


async def run_census(
    config: Config,
    db: Database,
    client: EsiClient,
    *,
    region_id: int | None = None,
    crawl: bool = True,
    max_types: int | None = None,
    progress=None,
) -> CensusResult:
    """Discover the active universe, crawl its history, and measure the map."""
    region = region_id or config.esi.home_region_id
    lake = BarLake(config.paths.ensure())
    notes: list[str] = []

    ids, fetched = await active_type_ids(client, region)
    if not fetched:
        ids = [
            int(row["type_id"])
            for row in db.conn.execute("SELECT type_id FROM universe WHERE region_id=?", (region,))
        ]
        notes.append(
            "/markets/types was still inside its cache window; reused the stored "
            "universe rather than fetching early"
        )
    cap = max_types if max_types is not None else config.universe.census_max_types
    if len(ids) > cap:
        notes.append(f"census capped at {cap} of {len(ids)} active types by config")
        ids = ids[:cap]

    known_missing = db.history_missing(region)
    if known_missing:
        notes.append(
            f"{len(known_missing)} types are recorded as having no history in this "
            "region and were skipped (they are listed by /markets/types but 404 on "
            "/markets/history — a catalogue gap, measured, not an error)"
        )
    ingest = HistoryIngestResult()
    if crawl:
        # A large write batch matters at census scale: every flush rewrites the
        # whole year partition, so 20k types must not become 100 rewrites.
        ingest = await ingest_history(
            client,
            lake,
            ids,
            region_id=region,
            batch_size=2500,
            skip_type_ids=known_missing,
            on_missing=lambda batch: db.mark_history_missing(batch, region),
            progress=progress,
        )
        if ingest.missing_type_ids:
            notes.append(
                f"{len(ingest.missing_type_ids)} further types 404'd on history and "
                "were recorded so tomorrow's crawl skips them"
            )
    else:
        notes.append("history crawl skipped by request; measurements use the existing lake")

    table = liquidity_table(lake, region, lookback_days=config.universe.liquidity_lookback_days)
    grid = score_floor_grid(table)
    derived = derive_floor(grid)
    # The turnover grid is still measured and still reported — it is how the
    # weighting input is understood — but it no longer decides membership.
    # The floor is the operator's stated unit-volume rule (§11 D3, amended
    # 2026-08-20): a name you cannot move 1,000 units of a day is not a name
    # you can get out of, whatever ISK it prints on its good days.
    min_units = float(config.universe.min_median_unit_volume)
    absolute_units = float(config.universe.absolute_min_unit_volume)
    scored = apply_floor(table, min_unit_volume=min_units, absolute_min_unit_volume=absolute_units)
    universe = sync_universe(
        db,
        region,
        ids,
        table,
        min_unit_volume=min_units,
        absolute_min_unit_volume=absolute_units,
        source="census",
    )
    notes.append(
        f"membership floor: median 30d units >= {min_units:,.0f} "
        f"({universe.index_eligible} types), THIN band "
        f"{absolute_units:,.0f}-{min_units:,.0f} ({universe.thin} types, badged and "
        f"excluded from FORGE), below floor {universe.below}"
    )

    book = BookLake(config.paths).latest(region)
    book_stats = book_statistics(book, tiers=config.costs.notional_tiers_isk)
    if "reason" in book_stats:
        notes.append(book_stats["reason"])

    db.checkpoint()
    frame = lake.read(region)
    return CensusResult(
        region_id=region,
        generated_at=iso(utcnow()),
        active_types=len(ids),
        types_with_bars=int(frame["type_id"].nunique()) if not frame.empty else 0,
        total_bars=int(len(frame)),
        lookback_days=config.universe.liquidity_lookback_days,
        ingest=ingest.as_dict(),
        unit_volume_percentiles=_percentiles(table["median_unit_volume"])
        if not table.empty
        else {},
        membership=universe.as_dict(),
        turnover_percentiles=_percentiles(table["median_isk_value"]) if not table.empty else {},
        order_count_percentiles=_percentiles(table["median_order_count"])
        if not table.empty
        else {},
        spread_percentiles=book_stats.get("spread_percentiles", {}),
        haircut_percentiles=book_stats.get("round_trip_haircut_percentiles", {}),
        depth_coverage=book_stats.get("depth_coverage", {}),
        spoof_share=book_stats.get("spoof_flagged_share"),
        structure_share=book_stats.get("structure_share", {}),
        book_sweep_ts=book_stats.get("sweep_ts"),
        floor_grid=grid,
        derived_floor=derived,
        market_group_breakdown=market_group_breakdown(db, scored),
        quality=ingest.quality,
        notes=notes,
    )


def render_census(result: CensusResult) -> str:
    """Human-readable census. Every number here is a measurement, not an estimate."""
    lines = [
        f"# Universe census — region {result.region_id}",
        "",
        f"Generated {result.generated_at}. This table is the denominator for every",
        '"the universe is N" claim the system makes.',
        "",
        f"- Active types (live orders in region): **{result.active_types}**",
        f"- Types with daily bars in the lake: **{result.types_with_bars}**",
        f"- Total bars stored: **{result.total_bars}**",
        f"- Liquidity window: {result.lookback_days} days",
        "",
        "## Ingest",
        f"- requested {result.ingest.get('requested', 0)}, "
        f"fetched {result.ingest.get('fetched', 0)}, "
        f"skipped-fresh {result.ingest.get('skipped_fresh', 0)}, "
        f"304 {result.ingest.get('not_modified', 0)}, "
        f"no-history-404 {result.ingest.get('no_history', 0)}, "
        f"failed {result.ingest.get('failed', 0)}",
        f"- rows written: {result.ingest.get('rows_written', 0)}",
        "",
        "## Median daily ISK turnover, by percentile",
    ]
    for key, value in result.turnover_percentiles.items():
        lines.append(f"- {key}: {value:,.0f} ISK")
    lines.append("")
    lines.append("## Median daily order_count, by percentile")
    for key, value in result.order_count_percentiles.items():
        lines.append(f"- {key}: {value:,.1f}")
    if result.spread_percentiles or result.depth_coverage:
        lines.append("")
        lines.append(f"## Book statistics (sweep {result.book_sweep_ts})")
        lines.append("")
        if result.spread_percentiles:
            lines.append("Spread, best ask vs best bid, by percentile:")
            for key, value in result.spread_percentiles.items():
                lines.append(f"- {key}: {value:,.2f}%")
        if result.haircut_percentiles:
            lines.append("")
            lines.append(
                "**Round-trip taker haircut** at "
                f"{result.haircut_percentiles.get('tier_isk', 0) / 1e9:.2f}B "
                f"({result.haircut_percentiles.get('types_measured', 0):,} types measured) "
                "— half the spread in, half out, BEFORE the 3.375% sales tax. "
                "Any strategy's edge has to clear this:"
            )
            for key, value in result.haircut_percentiles.items():
                if key.startswith("p") or key == "min":
                    lines.append(f"- {key}: {value:,.3f}%")
            below = result.haircut_percentiles.get("types_below") or {}
            if below:
                lines.append("")
                lines.append(
                    "**How many types are tight enough at all.** Any strategy's gross "
                    "edge must clear its friction *plus* the 3.375% sales tax, so this "
                    "count is the ceiling on how many names any idea could ever use:"
                )
                measured = result.haircut_percentiles.get("types_measured", 0) or 1
                for threshold, count in sorted(below.items(), key=lambda item: float(item[0])):
                    lines.append(
                        f"- round-trip friction < {threshold}%: **{count:,}** types "
                        f"({count / measured:.2%})"
                    )
        if result.depth_coverage:
            lines.append("")
            lines.append(
                "**Depth coverage** — share of sell books that can absorb the notional "
                "at all. This is the honest ceiling on how many floored types are ever "
                "tradeable:"
            )
            for tier, share in result.depth_coverage.items():
                lines.append(f"- {float(tier) / 1e9:.2f}B ISK: {share:.1%}")
        if result.spoof_share is not None:
            lines.append("")
            lines.append(
                f"- Sell books where one order holds >50% of resting volume: "
                f"**{result.spoof_share:.1%}** (the spoof/thin-book flag)"
            )
        if result.structure_share:
            ask = result.structure_share.get("ask_volume_weighted")
            bid = result.structure_share.get("bid_volume_weighted")
            lines.append(
                "- Volume-weighted share resting in **player structures**: "
                f"ask side {ask:.1%}, bid side {bid:.1%}. "
                if ask is not None and bid is not None
                else "- Structure share UNKNOWN"
            )
            lines.append(
                "  The exposure is on the **exit**: what you can buy is visible in NPC "
                "stations, but part of what you would sell into may need docking rights "
                "you do not have."
            )
    lines.append("")
    lines.append("## Membership (the floor that decides who is tradeable)")
    lines.append("")
    membership = result.membership
    if membership:
        lines.append(
            "Rule: **median 30-day UNIT volume**. Median, never mean — one wash-trade "
            "day must not lift a dead item over the floor. Turnover decides how much a "
            "name *counts* in the index; units decide whether it is in at all."
        )
        lines.append("")
        lines.append("| tier | rule | types |")
        lines.append("|---|---|---:|")
        lines.append(
            f"| OK | >= {membership.get('floor_unit_volume', 0):,.0f} units/day | "
            f"{membership.get('index_eligible', 0) + membership.get('price_pinned', 0)} |"
        )
        lines.append(
            "| — of which NPC-price-seeded | price did not move across the window; carried "
            f"and charted, never an index member | {membership.get('price_pinned', 0)} |"
        )
        lines.append(
            f"| THIN | {membership.get('absolute_floor_unit_volume', 0):,.0f}-"
            f"{membership.get('floor_unit_volume', 0):,.0f} units/day, badged everywhere, "
            f"excluded from FORGE | {membership.get('thin', 0)} |"
        )
        lines.append(
            f"| below floor | under {membership.get('absolute_floor_unit_volume', 0):,.0f} "
            f"units/day, direct lookup only | {membership.get('below', 0)} |"
        )
        lines.append("")
        lines.append(f"Tradeable universe (OK + THIN): **{membership.get('tracked', 0)}** types.")
    else:
        lines.append("UNKNOWN — the universe was not synced on this run.")
    if result.unit_volume_percentiles:
        lines.append("")
        lines.append("Median daily unit volume, percentiles across measured types:")
        lines.append("")
        lines.append("| " + " | ".join(result.unit_volume_percentiles) + " |")
        lines.append("|" + "---:|" * len(result.unit_volume_percentiles))
        lines.append(
            "| " + " | ".join(f"{v:,.0f}" for v in result.unit_volume_percentiles.values()) + " |"
        )
    lines.append("")
    lines.append("## Liquidity floor grid")
    lines.append("")
    lines.append("| min ISK/day | min order_count | types | % of types | % of turnover |")
    lines.append("|---:|---:|---:|---:|---:|")
    for row in result.floor_grid:
        lines.append(
            f"| {row['min_median_isk_value']:,.0f} | {row['min_median_order_count']:,.0f} "
            f"| {row['types']} | {row['share_of_types']:.1%} | {row['share_of_turnover']:.1%} |"
        )
    lines.append("")
    lines.append("## Derived turnover floor (superseded as a membership rule)")
    lines.append("")
    lines.append(
        "Kept because it is still the honest read of where the region's ISK is, and "
        "because the weighting input is turnover. It no longer decides membership — "
        "the unit-volume rule above does (§11 D3, amended 2026-08-20)."
    )
    lines.append("")
    derived = result.derived_floor
    lines.append(f"Rule (stated before measurement): {derived.get('rule')}")
    if derived.get("resolved"):
        lines.append(f"- **min median daily ISK turnover: {derived['min_median_isk_value']:,.0f}**")
        lines.append(f"- **min median order_count: {derived['min_median_order_count']:,.0f}**")
        lines.append(
            f"- admits {derived['types']} types carrying "
            f"{derived['share_of_turnover']:.1%} of median daily turnover"
        )
    else:
        lines.append(f"- UNRESOLVED: {derived.get('reason')}")
    if result.market_group_breakdown:
        lines.append("")
        lines.append("## Top market groups by daily ISK turnover")
        lines.append("")
        lines.append("| market group | types | tracked | daily ISK |")
        lines.append("|---|---:|---:|---:|")
        for row in result.market_group_breakdown:
            lines.append(
                f"| {row['group']} | {row['types']} | {int(row['tracked'])} "
                f"| {row['daily_isk']:,.0f} |"
            )
    if result.quality:
        lines.append("")
        lines.append("## Data quality counters")
        for key, value in result.quality.items():
            lines.append(f"- {key}: {value}")
    if result.notes:
        lines.append("")
        lines.append("## Notes")
        for note in result.notes:
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def write_census(config: Config, result: CensusResult) -> tuple[str, str]:
    """Persist the census as JSON (machine) and Markdown (operator)."""
    paths = config.paths.ensure()
    stem = f"census-{result.region_id}-{result.generated_at[:10]}"
    json_path = paths.reports / f"{stem}.json"
    md_path = paths.reports / f"{stem}.md"
    atomic_write_text(json_path, json.dumps(result.as_dict(), indent=2, sort_keys=True))
    atomic_write_text(md_path, render_census(result))
    return str(json_path), str(md_path)
