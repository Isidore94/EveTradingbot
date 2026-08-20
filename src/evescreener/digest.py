"""The daily Discord digest — plan.md §11 D6.

Webhook, not a bot. One channel. The ported `push_notify` result contract
(`unconfigured / delivered / rejected / ambiguous`) plus a `rate_limited` kind
for Discord's 429 + `retry_after`. The injectable opener is kept from upstream
so every path is testable offline.

Two content rules carried from the source repo:

* content is capped at 2,000 characters per message and the digest **splits
  into numbered messages rather than truncating silently** — and when a line
  really is dropped, the digest says so;
* an **honest zero** is a first-class digest. "Nothing clears costs today" is
  the expected output on many days, and it is published with the counts that
  explain it, so an ESI outage never looks like an absence of opportunity.

No `@here`/`@everyone`. Nothing in a daily screener is urgent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Config
from .paths import append_jsonl
from .timeutil import iso, utcnow

__all__ = ["DigestResult", "build_digest", "post_digest", "split_content"]

UNCONFIGURED = "unconfigured"
DELIVERED = "delivered"
REJECTED = "rejected"
AMBIGUOUS = "ambiguous"
RATE_LIMITED = "rate_limited"


@dataclass(slots=True)
class DigestResult:
    kind: str
    messages: int = 0
    detail: str = ""
    retry_after: float | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "messages": self.messages,
            "detail": self.detail,
            "retry_after": self.retry_after,
        }


def split_content(text: str, limit: int) -> list[str]:
    """Split into <= `limit` chunks on line boundaries, numbering the parts.

    A line longer than the limit is hard-split and the split is *announced* —
    silence about a dropped or mangled line is the failure mode this guards.
    """
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        # Reserve room for the "(part n/m)" header added below.
        room = limit - 20
        if len(line) > room:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            for start in range(0, len(line), room):
                piece = line[start : start + room]
                chunks.append(piece + ("  …(line split)" if len(line) > room else ""))
            continue
        if size + len(line) + 1 > room and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    if len(chunks) <= 1:
        return chunks or [""]
    total = len(chunks)
    return [f"({index}/{total})\n{chunk}" for index, chunk in enumerate(chunks, start=1)]


def _verdict_banner(backtest_verdict: dict | None) -> str:
    """A one-line warning when the backtest says this setup class did not pass."""
    if not backtest_verdict:
        return ""
    verdicts = {
        horizon: judgement.get("verdict")
        for horizon, judgement in backtest_verdict.items()
        if isinstance(judgement, dict)
    }
    if not verdicts:
        return ""
    if any(value == "PLAUSIBLE" for value in verdicts.values()):
        return ""
    if all(value == "UNKNOWN" for value in verdicts.values()):
        return (
            "⚠ **The backtest returned UNKNOWN at every horizon** — too small a "
            "sample to judge, which is not the same as a pass."
        )
    return (
        "⚠ **The backtest says this setup class is NOT PLAUSIBLE at every horizon "
        "tested**, against a rule frozen before the measurement. The rows below are "
        "what the screen found today; they are not evidence the class works."
    )


def _fmt(value, digits=2, suffix="") -> str:
    if value is None:
        return "UNKNOWN"
    return f"{value:,.{digits}f}{suffix}"


def build_digest(
    config: Config,
    screen,
    *,
    paper_report=None,
    cross_region=None,
    backtest_verdict: dict | None = None,
    lead_lag_outcome: dict | None = None,
) -> str:
    """Assemble the digest body. UTC only; the operator's clock is EVE's clock."""
    lines = [
        f"**EVE screener — {screen.generated_at[:16]}Z**",
        f"region {screen.region_id} · universe {screen.universe:,} tracked types",
        "",
    ]
    # The caveat goes ABOVE the candidates, not in a footer. If the setup class
    # failed its own pre-stated test, no one should read a ranked list without
    # knowing that first.
    banner = _verdict_banner(backtest_verdict)
    if banner:
        lines.extend([banner, ""])
    if screen.honest_zero:
        lines.append("**Nothing clears costs today.**")
        lines.append(
            f"{screen.setups_found} setup(s) found; {screen.below_breakeven} did not clear "
            f"breakeven at the smallest tier; {screen.unknown_cost} could not be priced "
            f"({screen.stale_book} on a stale book)."
        )
        if screen.unknown_cost and screen.unknown_cost >= max(1, screen.setups_found):
            lines.append(
                "⚠ Most rejections are UNKNOWN, not measured failures — check the book "
                "sweep before reading this as an absence of opportunity."
            )
    else:
        lines.append(f"**{len(screen.candidates)} candidate(s) clearing costs**")
        lines.append("")
        for index, row in enumerate(screen.candidates, start=1):
            name = row.get("type_name") or f"type {row['type_id']}"
            lines.append(
                f"**{index}. {name}** — expected {_fmt(row.get('expected_r'), 2, 'R')} "
                f"· net edge {_fmt(row.get('net_edge_pct'), 2, '%')} "
                f"(expected move {_fmt(row.get('expected_move_pct'), 2, '%')})"
            )
            lines.append(f"    {row.get('thesis', '')}")
            if row.get("evidence"):
                lines.append(f"    evidence: {row['evidence']}")
            tiers = " · ".join(
                f"{tier['notional_isk'] / 1e9:.2f}B "
                + (
                    f"{tier['breakeven_move_pct']:.2f}%"
                    if tier.get("breakeven_move_pct") is not None
                    else "UNKNOWN"
                )
                for tier in row.get("tier_breakevens", [])
            )
            lines.append(f"    breakeven: {tiers}")
            lines.append(
                f"    entry {_fmt(row.get('entry_price'))} → exit {_fmt(row.get('exit_price'))} "
                f"(maker advisory {_fmt(row.get('maker_exit_advisory'))}) · "
                f"book {row.get('freshness')} "
                f"{_fmt(row.get('book_age_minutes'), 0, ' min old')}"
            )
            if row.get("flags"):
                lines.append("    ⚠ " + "; ".join(row["flags"]))
            lines.append("")
        lines.append(
            f"_{screen.below_breakeven} setup(s) rejected below breakeven; "
            f"{screen.unknown_cost} could not be priced._"
        )

    if cross_region is not None:
        lines.append("")
        lines.append("**Cross-region (freight netted)**")
        if not cross_region.rows:
            lines.append(
                f"_no route clears freight today ({cross_region.dropped_no_freight} dropped "
                "for want of a quote — no quote, no row)_"
            )
        else:
            for row in cross_region.rows[:5]:
                lines.append(
                    f"· {row.get('type_name') or row['type_id']} {row['freight_route']}: "
                    f"net {row['net_pct']:.2f}% ({row['net_isk']:,.0f} ISK) after "
                    f"{row['freight_isk']:,.0f} freight"
                    + (" [cached quote]" if row.get("freight_cached") else "")
                )

    if paper_report is not None:
        lines.append("")
        lines.append("**Paper trading**")
        lines.append(
            f"refused/UNKNOWN {paper_report.refused} · closed {len(paper_report.closed)} · "
            f"net {paper_report.cumulative_net_isk:,.0f} ISK · "
            f"verdict {paper_report.verdict.get('verdict', 'UNKNOWN')}"
        )
        if paper_report.open_positions:
            lines.append(f"open positions: {len(paper_report.open_positions)}")

    if backtest_verdict or lead_lag_outcome:
        lines.append("")
        lines.append("**Studies**")
        if backtest_verdict:
            for horizon, judgement in sorted(backtest_verdict.items()):
                if isinstance(judgement, dict):
                    lines.append(f"· backtest {horizon}d: {judgement.get('verdict', 'UNKNOWN')}")
        if lead_lag_outcome:
            outcome = lead_lag_outcome.get("outcome", "UNKNOWN")
            lines.append(f"· destruction lead-lag: {outcome}")
            if outcome != "SURVIVES":
                lines.append(
                    "  (destruction is shown as an annotation only — the lead-lag claim "
                    "was tested and not supported)"
                )

    diagnostics = screen.composite or {}
    lines.append("")
    lines.append(
        "_composite: "
        + (
            f"{diagnostics.get('members', 'UNKNOWN')} members, top weight "
            f"{_fmt(diagnostics.get('top_weight'), 3)}, entropy "
            f"{_fmt(diagnostics.get('weight_entropy'), 3)}, "
            f"{diagnostics.get('rebalances', 0)} rebalances_"
            if diagnostics
            else "UNKNOWN — no composite could be built_"
        )
    )
    return "\n".join(lines)


def post_digest(
    config: Config,
    content: str,
    *,
    opener=urllib.request.urlopen,
    archive_path=None,
) -> DigestResult:
    """Post to the Discord webhook. Every outcome is a named kind, never a raise.

    A failed publish never destroys the last verified output: the digest is
    archived to JSONL before any network call, so an outage costs delivery, not
    the record.
    """
    messages = split_content(content, config.discord.max_content_chars)
    if archive_path is not None:
        append_jsonl(
            archive_path,
            [{"at": iso(utcnow()), "messages": messages, "content": content}],
        )
    if not config.discord.webhook_url:
        return DigestResult(
            UNCONFIGURED,
            len(messages),
            "no webhook_url in config.toml; the digest was archived but not posted",
        )
    delivered = 0
    for message in messages:
        payload = json.dumps({"content": message, "username": config.discord.username}).encode(
            "utf-8"
        )
        request = urllib.request.Request(
            config.discord.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": config.app.user_agent},
            method="POST",
        )
        try:
            with opener(request, timeout=30) as response:
                status = getattr(response, "status", None) or response.getcode()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = None
                try:
                    retry_after = float(json.loads(exc.read()).get("retry_after"))
                except (ValueError, TypeError, AttributeError):
                    retry_after = float(exc.headers.get("Retry-After") or 0) or None
                return DigestResult(
                    RATE_LIMITED,
                    delivered,
                    f"Discord rate limited after {delivered}/{len(messages)} messages",
                    retry_after,
                )
            return DigestResult(
                REJECTED,
                delivered,
                f"HTTP {exc.code} after {delivered}/{len(messages)} messages",
            )
        except (urllib.error.URLError, OSError) as exc:
            return DigestResult(
                AMBIGUOUS,
                delivered,
                f"{type(exc).__name__}: {exc} — delivery of message "
                f"{delivered + 1}/{len(messages)} is unknown",
            )
        if status is None or not (200 <= int(status) < 300):
            return DigestResult(AMBIGUOUS, delivered, f"unexpected status {status!r}")
        delivered += 1
    return DigestResult(DELIVERED, delivered, f"{delivered} message(s) posted")
