"""Digest rendering and delivery (plan.md §11 D6, §5, §8 Phase 0).

Two rules shape everything here:

* **Honest zero.** "Nothing clears costs today" is a valid, expected digest.
  The candidate section says exactly that rather than dredging up the least-bad
  negative row and presenting it as an idea.
* **Nothing is dropped silently.** The content cap is 2,000 characters per
  Discord message, so the digest splits into numbered messages; if a single
  line still cannot fit, the digest says so instead of truncating it.

The measurement table below the candidates is Phase 0 gate machinery: the
operator needs prices and volumes in front of him to spot-check five types
against the in-game market window. It is labelled as measurement, not as
opportunity.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from . import __version__
from .clock import now_utc
from .config import Config
from .costs import PRICED
from .paths import append_jsonl
from .screen import ScreenResult

FENCE = "```"
MEASUREMENT_ROWS = 12


@dataclass(frozen=True)
class Digest:
    messages: list[str]
    lines: list[str]
    dropped_lines: int
    built_at: dt.datetime

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def isk(value: float) -> str:
    """Compact ISK rendering. EVE unit prices span 12 orders of magnitude."""
    if value is None or not _finite(value):
        return "—"
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(value) >= cutoff:
            return f"{value / cutoff:,.2f}{suffix}"
    return f"{value:,.2f}"


def pct(value: float, places: int = 2) -> str:
    if value is None or not _finite(value):
        return "—"
    return f"{value:+.{places}f}%"


def _finite(value: object) -> bool:
    try:
        return (
            pd.notna(value)
            and float(value) == float(value)
            and abs(float(value)) != float("inf")
        )
    except (TypeError, ValueError):
        return False


def render(
    config: Config,
    result: ScreenResult,
    *,
    telemetry: dict[str, int] | None = None,
) -> list[str]:
    """Render the digest as a list of lines (pre-split)."""
    lines: list[str] = []
    stamp = result.as_of.strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"**EVE net-cost screen — {stamp}**")

    book_note = (
        "no book sweep on record"
        if result.sweep_ts is None
        else (
            f"book {result.book_age_minutes:.0f} min old "
            f"(sweep {result.sweep_ts.strftime('%H:%M UTC')})"
            + (" — STALE, depth cost UNKNOWN" if result.book_is_stale else "")
        )
    )
    lines.append(
        f"Forge {config.market.region_id} · notional {isk(result.notional_isk)} ISK · "
        f"{book_note}"
    )
    lines.append(
        f"Fees netted: sales tax {config.costs.sales_tax_rate * 100:.3f}% "
        f"(Accounting {config.costs.accounting_level}), broker "
        f"{config.costs.broker_fee_rate * 100:.3f}% "
        f"(Broker Relations {config.costs.broker_relations_level})"
    )
    lines.append("")

    lines += _candidate_section(result)
    lines.append("")
    lines += _measurement_section(result)

    unknown = (
        result.rows[result.rows["status"] != PRICED]
        if not result.rows.empty
        else result.rows
    )
    if not unknown.empty:
        lines.append("")
        lines.append(f"__Unpriced — UNKNOWN ({len(unknown)})__")
        for _, row in unknown.iterrows():
            lines.append(f"• {row['name']} — {row['reason']}")

    if telemetry:
        lines.append("")
        lines += _telemetry_section(telemetry)

    lines.append("")
    lines.append(
        f"_evescreener {__version__} · decision support only, no orders are placed_"
    )
    return lines


def _candidate_section(result: ScreenResult) -> list[str]:
    candidates = result.candidates
    header = f"__Clears costs at {isk(result.notional_isk)} ISK__"
    if candidates.empty:
        return [
            header,
            "Nothing clears costs today. That is a result, not a gap — the "
            "round trip at this size does not beat tax plus the depth walk.",
        ]
    lines = [header, FENCE]
    lines.append(f"{'name':<26}{'net%':>8}{'be-taker%':>11}{'entry':>12}{'units':>9}")
    for _, row in candidates.iterrows():
        lines.append(
            f"{str(row['name'])[:25]:<26}"
            f"{row['net_margin_pct']:>+8.2f}"
            f"{row['breakeven_move_taker_pct']:>+11.2f}"
            f"{isk(row['entry_price']):>12}"
            f"{int(row['entry_units']):>9,}"
        )
    lines.append(FENCE)
    return lines


def _measurement_section(result: ScreenResult) -> list[str]:
    priced = (
        result.rows[result.rows["status"] == PRICED]
        if not result.rows.empty
        else result.rows
    )
    lines = [
        f"__Measurements, not recommendations — top {MEASUREMENT_ROWS} by net margin__"
    ]
    if priced.empty:
        lines.append("No priced rows: the book gave nothing to measure.")
        return lines
    lines.append(FENCE)
    lines.append(
        f"{'name':<24}{'bid':>11}{'ask':>11}{'spr%':>8}{'net%':>8}"
        f"{'be-tk%':>9}{'turn30d':>10}"
    )
    crossed = 0
    for _, row in priced.head(MEASUREMENT_ROWS).iterrows():
        mark = "*" if row["crossed_book"] else " "
        crossed += int(bool(row["crossed_book"]))
        lines.append(
            f"{(mark + str(row['name']))[:23]:<24}"
            f"{isk(row['best_bid']):>11}"
            f"{isk(row['best_ask']):>11}"
            f"{row['spread_pct']:>8.2f}"
            f"{row['net_margin_pct']:>+8.2f}"
            f"{row['breakeven_move_taker_pct']:>+9.2f}"
            f"{isk(row['median_isk_value_30d']):>10}"
        )
    lines.append(FENCE)
    lines.append(
        "bid/ask are top of book; net% and be-tk% are netted on the depth walk "
        f"at {isk(result.notional_isk)} ISK, so they will not match a top-of-book "
        "calculation."
    )
    if crossed:
        lines.append(
            f"* {crossed} row(s) have a crossed region-wide book — the best bid "
            "sits above the best ask, across stations or behind a lone cheap "
            "order. Read the netted number, not the spread."
        )
    return lines


def _telemetry_section(telemetry: dict[str, int]) -> list[str]:
    early = telemetry.get("early_fetches", 0)
    verdict = (
        "all requests honoured Expires" if early == 0 else f"{early} EARLY FETCHES"
    )
    return [
        "__Telemetry (last 24h)__",
        f"• requests {telemetry.get('requests', 0)} · "
        f"tokens {telemetry.get('tokens', 0)} · "
        f"peak X-Ratelimit-Used {telemetry.get('peak_tokens_used', 0)}",
        f"• 304s {telemetry.get('not_modified', 0)} · "
        f"skipped-still-fresh {telemetry.get('skipped_fresh', 0)} · "
        f"4xx {telemetry.get('client_errors', 0)} · "
        f"5xx {telemetry.get('server_errors', 0)}",
        f"• {verdict}",
    ]


def split_messages(lines: list[str], max_chars: int) -> tuple[list[str], int]:
    """Pack ``lines`` into numbered messages of at most ``max_chars``.

    Code fences are closed and reopened across a split so a table never leaks
    its monospace formatting into the next message. Returns
    ``(messages, dropped_lines)``; a line too long to ever fit is counted and
    replaced by a visible marker rather than silently truncated.
    """
    prefix_budget = len("[99/99] ")
    budget = max_chars - prefix_budget
    chunks: list[list[str]] = []
    current: list[str] = []
    length = 0
    in_fence = False
    dropped = 0

    def flush() -> None:
        nonlocal current, length
        if current:
            chunks.append(current)
        current = []
        length = 0

    for raw in lines:
        line = raw
        if len(line) + 1 > budget:
            line = f"[line dropped: {len(raw)} chars exceeds the message budget]"
            dropped += 1
        closing = len(FENCE) + 1 if in_fence else 0
        if length + len(line) + 1 + closing > budget and current:
            if in_fence:
                current.append(FENCE)
            flush()
            if in_fence:
                current.append(FENCE)
                length = len(FENCE) + 1
        current.append(line)
        length += len(line) + 1
        if line.strip() == FENCE:
            in_fence = not in_fence
    if in_fence:
        current.append(FENCE)
    flush()

    total = len(chunks)
    return [
        f"[{index}/{total}] " + "\n".join(chunk)
        for index, chunk in enumerate(chunks, start=1)
    ], dropped


def build(
    config: Config,
    result: ScreenResult,
    *,
    telemetry: dict[str, int] | None = None,
) -> Digest:
    lines = render(config, result, telemetry=telemetry)
    messages, dropped = split_messages(lines, config.discord.max_content_chars)
    return Digest(
        messages=messages, lines=lines, dropped_lines=dropped, built_at=now_utc()
    )


def archive(
    config: Config, digest: Digest, result: ScreenResult, delivery: dict
) -> None:
    """Append the digest to the JSONL archive before anything can fail (§3.5)."""
    append_jsonl(
        config.paths.digest_archive,
        {
            "built_at": digest.built_at,
            "as_of": result.as_of,
            "region_id": config.market.region_id,
            "notional_isk": result.notional_isk,
            "sweep_ts": result.sweep_ts,
            "book_is_stale": result.book_is_stale,
            "rows": len(result.rows),
            "candidates": len(result.candidates),
            "dropped_lines": digest.dropped_lines,
            "messages": digest.messages,
            "delivery": delivery,
            "version": __version__,
        },
    )
