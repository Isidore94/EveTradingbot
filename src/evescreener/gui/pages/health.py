"""HEALTH — is this installation telling the truth? (§19 Part 2 page 8)

Token budget headroom, feed health, sweep ages, lake counts, last digest
delivery. Everything here is read from the local telemetry and state database;
nothing on this page can cause a request, which is exactly why it is safe for
it to be on a refresh timer alongside the rest of the desk.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from ...selftest import run_selftest
from ..widgets import BLANK, SortableTable
from .base import DeskPage

__all__ = ["HealthPage"]

HEADERS = ["check", "value", "state"]


class HealthPage(DeskPage):
    title = "HEALTH"

    def build(self) -> None:
        self.headline = QLabel("")
        self.headline.setStyleSheet("QLabel { font-size: 14px; font-weight: 600; }")
        self.layout.addWidget(self.headline)
        self.table = SortableTable(HEADERS)
        self.layout.addWidget(self.table, 1)
        self.footer = QLabel(
            "Nothing on this page fetches. The desk shows staleness; it never cures it "
            "by fetching before Expires (§3.2)."
        )
        self.footer.setWordWrap(True)
        self.layout.addWidget(self.footer)
        self.repopulate()

    def repopulate(self) -> None:
        data = self.data
        rows: list[list] = []

        age = data.book_age_minutes
        rows.append(
            [
                ("book sweep age", "book sweep age"),
                (f"{age:.0f} min" if age is not None else "UNKNOWN", age),
                ("STALE" if data.book_is_stale else "fresh", None),
            ]
        )
        rows.append(
            [
                ("last bar in the lake", "last bar in the lake"),
                (str(data.last_bar)[:16] if data.last_bar else "UNKNOWN", None),
                ("" if data.last_bar else "UNKNOWN", None),
            ]
        )
        types = int(data.bars["type_id"].nunique()) if not data.bars.empty else 0
        rows.append([("tracked types with bars", ""), (f"{types:,}", types), ("", None)])
        rows.append([("bars in the lake", ""), (f"{len(data.bars):,}", len(data.bars)), ("", None)])

        tiers: dict[str, int] = {}
        for tier in data.tiers.values():
            tiers[tier or "UNMEASURED"] = tiers.get(tier or "UNMEASURED", 0) + 1
        for tier in ("OK", "THIN", "BELOW", "UNMEASURED"):
            if tier in tiers:
                rows.append(
                    [(f"universe · {tier}", ""), (f"{tiers[tier]:,}", tiers[tier]), ("", None)]
                )

        budget = data.db.get_meta("orders_tokens_spent")
        limit = data.config.budget.orders_token_self_cap
        rows.append(
            [
                ("order tokens this window", ""),
                (f"{budget or BLANK} / {limit}", None),
                ("" if budget else "UNKNOWN", None),
            ]
        )
        digest = data.db.get_meta("last_digest_delivery")
        rows.append(
            [
                ("last digest delivery", ""),
                (digest or "UNKNOWN", None),
                ("" if digest else "UNKNOWN", None),
            ]
        )

        for check in run_selftest(data.config):
            rows.append(
                [
                    (f"selftest · {check.name}", ""),
                    (check.detail, None),
                    ("PASS" if check.ok else "FAIL", None),
                ]
            )
        self.table.set_rows(rows)
        failures = sum(1 for check in run_selftest(data.config) if not check.ok)
        self.headline.setText(
            f"region {data.region_id} · {types:,} tracked types · "
            + ("all selftest checks pass" if not failures else f"{failures} selftest FAILURE(s)")
        )
