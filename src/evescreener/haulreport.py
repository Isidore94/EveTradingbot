"""The hauling audit artefact (plan.md §23.12).

A scan on the desk is a view; this is the record. It is written once, never
edited, and carries everything needed to re-derive the ranking six months later
without the lake: the profile, both generations per row, the SDE build the
routes came from, the calculation version, the levels each walk consumed, the
fee arithmetic, the route decomposition, why *that* size, and — at least as
important — the count of everything that was rejected and why.

**A failed publish never destroys the last verified report.** Both files are
written through `atomic_write_text`, so a crash mid-write leaves the previous
report exactly as it was (§5's failed-publish invariant).
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .hauling import ORDER_AGE_CAVEAT, SNAPSHOT_CAVEAT, HaulScan
from .paths import atomic_write_text
from .timeutil import parse_iso

__all__ = [
    "CALC_VERSION",
    "build_haul_report",
    "haul_basket",
    "latest_haul_report",
    "render_haul_report",
    "write_haul_report",
]

#: Bumped whenever the arithmetic behind a stored row changes, so two reports
#: can be told apart by what computed them rather than by their dates.
CALC_VERSION = "haul-1"

#: How many rejected candidates keep their full detail, **per reason**. The
#: counts are always whole — they are the denominator — but a five-hub scan can
#: reject hundreds of thousands of candidates, and serialising every one turned
#: a report into hundreds of megabytes nobody could open. Truncation is
#: reported, never silent: a shortened list that does not say so is a claim
#: that nothing else was rejected.
REJECTED_DETAIL_CAP = 50

REPORT_PREFIX = "hauling"


def haul_basket(scan: HaulScan, *, config: Config | None = None):
    """The mixed-cargo read for a scan, built one way for every surface.

    Computed here rather than in the engine so the CLI and the desk cannot
    drift into two baskets, and so the engine stays pure arithmetic over depth
    and routes.

    `max_exposure_pct_per_destination` is applied **here**, because it is a cap
    on a basket rather than on a plan: one haul to one hub cannot breach it, and
    four of them can. Passing it is what makes the config key reachable at all —
    a setting nothing reads is the §22 S6 defect wearing a different name.
    """
    from .positioning import greedy_basket

    profile = scan.profile
    per_destination = None
    if config is not None and config.hauling.max_exposure_pct_per_destination:
        per_destination = (
            profile.capital_isk * float(config.hauling.max_exposure_pct_per_destination) / 100.0
        )
    # The overlap guard now travels with `greedy_basket` itself — it used to
    # live here, one caller away from the packing it guards, and the bare
    # primitive happily packed 2,000 units out of a 1,000-unit ask.
    return greedy_basket(
        scan.plans,
        capital_isk=profile.capital_isk,
        cargo_m3=profile.ship.usable_cargo_m3,
        exposure_per_trade_isk=profile.max_exposure_isk,
        exposure_per_destination_isk=per_destination,
        objective=profile.objective,
    )


def build_haul_report(
    scan: HaulScan,
    *,
    config: Config | None = None,
    rejected_detail_cap: int = REJECTED_DETAIL_CAP,
) -> dict:
    """The immutable payload. Everything a reader needs to argue with it."""
    rejected, omitted = _capped_rejections(scan, rejected_detail_cap)
    report = {
        "kind": "hauling_scan",
        "calc_version": CALC_VERSION,
        "generated_at": scan.generated_at,
        "sde_build": scan.sde_build,
        "route_profile": scan.profile.security_profile,
        "objective": scan.profile.objective,
        "profile": scan.profile.as_dict(),
        "generations": {str(key): value for key, value in scan.generations.items()},
        "counts": {
            "plans": len(scan.plans),
            "pairs_considered": scan.pairs_considered,
            "types_considered": scan.types_considered,
            "candidates_considered": scan.candidates_considered,
            "rejected": len(scan.rejected),
        },
        "rejection_counts": scan.rejection_counts,
        # Priced plans this run's objective could not score. Not rejections —
        # another objective would rank them — but never silent either.
        "dropped_unrankable": scan.dropped_unrankable,
        "unknown_pairs": scan.unknown_pairs,
        "rows": [_row(plan) for plan in scan.plans],
        "basket": haul_basket(scan, config=config).as_dict(),
        "rejected": rejected,
        "rejected_truncated": omitted,
        "notes": scan.notes,
        "caveats": [SNAPSHOT_CAVEAT, ORDER_AGE_CAVEAT],
        "limitations": list(LIMITATIONS),
    }
    if config is not None:
        report["assumptions"] = {
            "destination_share_prior": config.hauling.destination_share_prior,
            "capture_share": list(config.hauling.capture_share),
            "liquidity_quantiles": list(config.hauling.liquidity_quantiles),
            "min_liquidity_bars": config.hauling.min_liquidity_bars,
            "note": (
                "These are LABELLED ASSUMPTIONS, not measurements. Regional history "
                "carries no station split, so the destination share cannot be derived "
                "from the lake; it becomes a measurement only when the operator's own "
                "recorded fills can replace it (plan.md §23.7)."
            ),
        }
    return report


LIMITATIONS = (
    "Both ladders are ONE MOMENT of two order books. The haul is not "
    "instantaneous, and the destination bid you are pricing against can be "
    "gone before you dock. This scan overstates realizable profit by exactly "
    "as much as the destination book moves while you fly, and that direction "
    "is unknowable at scan time.",
    "Nothing here models the other trader who read the same spread. Every "
    "public book in this game is public.",
    "Getting IN is measured from swept depth. Getting OUT in a hurry is "
    "measured; getting out at a price is ASSUMED, and every assumption is "
    "labelled on the row rather than folded into the profit.",
    "`min_volume`-blocked bids are excluded from executable depth and reported "
    "separately, so exit depth is under-stated on purpose.",
    "Order age is ESI's `issued` — last placed OR repriced. Whether repricing "
    "updates it is unverified in either direction.",
    "Routes come from the local SDE graph at the build stamped on this report. "
    "A patch that changes the map invalidates them and nothing here can tell.",
)


def _capped_rejections(scan: HaulScan, cap: int) -> tuple[list[dict], dict[str, int]]:
    """Full detail up to `cap` per reason, plus what that left out."""
    kept: list[dict] = []
    omitted: dict[str, int] = {}
    seen: dict[str, int] = {}
    for rejection in scan.rejected:
        count = seen.get(rejection.reason, 0)
        seen[rejection.reason] = count + 1
        if cap and count >= cap:
            omitted[rejection.reason] = omitted.get(rejection.reason, 0) + 1
            continue
        kept.append(rejection.as_dict())
    return kept, omitted


def _row(plan) -> dict:
    """One plan, with the audit trail attached rather than summarised away."""
    payload = plan.as_dict()
    payload["audit"] = {
        "why_this_size": {
            "objective": plan.rank_score,
            "breakpoints": [
                {"quantity": quantity, "capital_isk": cost, "net_profit": net}
                for quantity, cost, net in plan.breakpoints
            ],
            "marginal_net_isk": plan.marginal_net_isk,
            "alternatives": plan.alternatives,
        },
        "walks": {
            "source_levels_consumed": plan.source_levels,
            "source_marginal_next_price": plan.source_marginal_next_price,
            "destination_levels_consumed": plan.dest_levels,
            "destination_marginal_next_price": plan.dest_marginal_next_price,
            "source_depth_complete": plan.source_depth_complete,
            "destination_depth_complete": plan.dest_depth_complete,
            "min_volume_excluded_qty": plan.min_volume_excluded_qty,
        },
        "fees": {
            "gross_sale": plan.gross_sale,
            "sales_tax_isk": plan.sales_tax_isk,
            "source_cost": plan.source_cost,
            "net_profit": plan.net_profit,
            "broker_fee_isk": 0.0,
            "broker_note": "a taker pays no broker fee; only a posted order does",
        },
        "route": {
            "pickup": plan.pickup.as_dict(),
            "haul": plan.haul.as_dict(),
            "total_jumps": plan.total_jumps,
            "detour_jumps": plan.detour_jumps,
            "active_minutes": plan.active_minutes,
        },
        # Self-haul versus paying PushX. UNKNOWN unless the operator asked for
        # a quote, and never a condition of the row above it (§23, H4).
        "freight_comparison": plan.freight or {"state": "UNKNOWN", "reason": "not quoted"},
        "generations": {
            "source": list(plan.source_generation) if plan.source_generation else None,
            "destination": list(plan.dest_generation) if plan.dest_generation else None,
            "source_age_minutes": plan.source_age_minutes,
            "destination_age_minutes": plan.dest_age_minutes,
            "row_age_minutes": plan.generation_age_minutes,
        },
    }
    return payload


def _isk(value) -> str:
    if value is None:
        return "UNKNOWN"
    number = float(value)
    if abs(number) >= 1e9:
        return f"{number / 1e9:,.2f}B"
    if abs(number) >= 1e6:
        return f"{number / 1e6:,.2f}M"
    return f"{number:,.0f}"


def render_haul_report(report: dict) -> str:
    """Markdown, with the honest zero and the refusals before the table."""
    profile = report.get("profile", {})
    ship = profile.get("ship", {})
    lines = [
        "# Hauling scan",
        "",
        f"Generated {report['generated_at']} · calc `{report['calc_version']}` · "
        f"SDE build {report.get('sde_build')} · route profile "
        f"`{report.get('route_profile')}` · objective `{report.get('objective')}`",
        "",
        "## The profile this was ranked for",
        "",
        f"- current system: {profile.get('current_system')}",
        f"- ship: {ship.get('name')} — {ship.get('usable_cargo_m3'):,.0f} m³ usable, "
        f"{ship.get('seconds_per_jump')}s/jump, {ship.get('handling_minutes')} min handling"
        if ship.get("usable_cargo_m3") is not None
        else f"- ship: {ship.get('name')}",
        f"- capital: {_isk(profile.get('capital_isk'))} · exposure cap "
        f"{_isk(profile.get('max_exposure_isk'))}",
        f"- session: {profile.get('session_minutes')} min · max jumps "
        f"{profile.get('max_jumps')} · security `{profile.get('security_profile')}`",
        "",
        "## Generations",
        "",
    ]
    for region, generation in sorted(report.get("generations", {}).items()):
        age = generation.get("age_minutes")
        lines.append(
            f"- region {region}: "
            + (f"{age:.0f} min old" if age is not None else "UNKNOWN")
            + (" — STALE, prices nothing" if generation.get("stale") else "")
        )
    counts = report.get("counts", {})
    lines.extend(
        [
            "",
            "## What was examined",
            "",
            f"- station pairs considered: {counts.get('pairs_considered', 0):,}",
            f"- (type, pair) candidates priced: {counts.get('candidates_considered', 0):,}",
            f"- rejected: {counts.get('rejected', 0):,}",
            "",
        ]
    )
    dropped = report.get("dropped_unrankable") or {}
    if dropped:
        lines.append(
            "Priced but unrankable under this objective: "
            + ", ".join(f"{reason} {count:,}" for reason, count in sorted(dropped.items()))
            + " — another objective would rank them."
        )
        lines.append("")
    rejection_counts = report.get("rejection_counts") or {}
    if rejection_counts:
        lines.append("| reason | count |")
        lines.append("|---|---:|")
        omitted = report.get("rejected_truncated") or {}
        for reason, count in rejection_counts.items():
            lines.append(f"| `{reason}` | {count:,} |")
        lines.append("")
        if omitted:
            lines.append(
                "Detail was capped per reason; the counts above are whole. Omitted "
                "detail: "
                + ", ".join(f"{reason} {count:,}" for reason, count in sorted(omitted.items()))
                + "."
            )
            lines.append("")

    rows = report.get("rows") or []
    lines.append("## Plans")
    lines.append("")
    if not rows:
        lines.append(
            "**Nothing clears costs today.** That is a valid, expected result — the "
            "Forge's median spread is 98.8% and §17 measured 10–14 hub pairs clearing "
            "at 0.25B out of 151,113 considered."
        )
    else:
        lines.append("| item | route | qty | capital | net | ROI | m³ | jumps | min | ISK/min |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in rows:
            source = row["source"]["label"]
            destination = row["destination"]["label"]
            lines.append(
                f"| {row.get('type_name') or row['type_id']} | {source} → {destination} "
                f"| {row['quantity']:,.0f} | {_isk(row['source_cost'])} "
                f"| {_isk(row['net_profit'])} | {row['net_roi_pct']:.2f}% "
                f"| {(row['cargo_m3'] or 0):,.0f} | {row.get('total_jumps')} "
                f"| {(row.get('active_minutes') or 0):.0f} "
                f"| {_isk(row.get('isk_per_active_minute'))} |"
            )
    basket = report.get("basket") or {}
    if basket.get("items"):
        lines.extend(
            [
                "",
                f"## Mixed cargo — {basket.get('method', 'HEURISTIC')} (not an optimum)",
                "",
                f"{basket['capital_isk']:,.0f} ISK committed · {basket['net_isk']:,.0f} ISK net "
                f"· {basket['volume_m3']:,.0f} of {basket['cargo_m3']:,.0f} m³",
                "",
                "| item | qty | capital | net | m³ |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for item in basket["items"]:
            lines.append(
                f"| {item.get('type_name') or item['type_id']} | {item['quantity']:,.0f} "
                f"| {_isk(item['capital_isk'])} | {_isk(item['net_isk'])} "
                f"| {item['volume_m3']:,.0f} |"
            )
        for note in basket.get("notes", []):
            lines.extend(["", f"_{note}_"])

    unknown = report.get("unknown_pairs") or []
    if unknown:
        lines.extend(["", "## Pairs that priced nothing", ""])
        for entry in unknown:
            lines.append(
                f"- {entry['source']['label']} → {entry['destination']['label']}: "
                f"**{entry['state']}** — {entry['reason']}"
            )
    lines.extend(["", "## What this scan cannot tell you", ""])
    for index, limitation in enumerate(report.get("limitations", LIMITATIONS), start=1):
        lines.append(f"{index}. {limitation}")
    if report.get("notes"):
        lines.extend(["", "## Notes", ""])
        for note in dict.fromkeys(report["notes"]):
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def _stem(generated_at: str) -> str:
    """A filename a Windows desktop can actually hold: no colons."""
    stamp = parse_iso(generated_at)
    if stamp is None:  # pragma: no cover - generated_at is always ours
        return f"{REPORT_PREFIX}-unknown"
    return f"{REPORT_PREFIX}-{stamp.strftime('%Y%m%dT%H%M%SZ')}"


def write_haul_report(config: Config, report: dict) -> tuple[Path, Path]:
    """Write both files atomically. Returns `(json_path, markdown_path)`."""
    paths = config.paths.ensure()
    stem = _stem(report["generated_at"])
    json_path = paths.reports / f"{stem}.json"
    md_path = paths.reports / f"{stem}.md"
    atomic_write_text(json_path, json.dumps(report, indent=2, sort_keys=True, default=str))
    atomic_write_text(md_path, render_haul_report(report))
    return json_path, md_path


def latest_haul_report(paths) -> Path | None:
    """The newest stored scan, or None. Used by the desk's input key."""
    directory = paths.reports
    if not directory.exists():
        return None
    found = sorted(directory.glob(f"{REPORT_PREFIX}-*.json"))
    return found[-1] if found else None
