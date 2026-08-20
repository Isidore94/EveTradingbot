"""LEARNING — what's working (§19 Part 4, Amendment 3).

Three tables: setups ranked by evidence-weighted expected net R, the "why I
liked it" tags measured across the trades that carried them, and the "why I
passed" tags measured forward on the backtest's cost terms.

Nothing on this page changes anything. It correlates and reports; the operator
promotes, demotes and edits. The footer says so, on every render.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from ...learning import MIN_SAMPLES_FOR_A_READ, build_learning_report
from ..widgets import BLANK, SortableTable
from .base import DeskPage

__all__ = ["LearningPage"]

SETUP_HEADERS = [
    "setup",
    "closed",
    "open",
    "win %",
    "win LB %",
    "avg R",
    "median R",
    "expected R",
    "fresh",
    "state",
]
LIKE_HEADERS = ["why I liked it", "n", "win %", "win LB %", "avg R", "state"]
PASS_HEADERS = [
    "why I passed",
    "passes",
    "measured",
    "pending",
    "right %",
    "right LB %",
    "avg forward %",
    "state",
]


def _pct(value):
    return (f"{value * 100:,.0f}%", value) if value is not None else (BLANK, None)


def _num(value, spec="+.2f"):
    return (format(float(value), spec), float(value)) if value is not None else (BLANK, None)


class LearningPage(DeskPage):
    title = "LEARNING"
    heavy = True

    def build(self) -> None:
        self.headline = QLabel("")
        self.headline.setStyleSheet("QLabel { font-size: 14px; font-weight: 600; }")
        self.layout.addWidget(self.headline)

        self.layout.addWidget(QLabel("Setups, ranked by evidence-weighted expected net R"))
        self.setups = SortableTable(SETUP_HEADERS)
        self.layout.addWidget(self.setups, 2)

        self.layout.addWidget(QLabel("Why I liked it — do my reasons earn?"))
        self.likes = SortableTable(LIKE_HEADERS)
        self.layout.addWidget(self.likes, 1)

        self.layout.addWidget(
            QLabel(
                "Why I passed — was I right? A pass is 'right' when the avoided trade "
                "would not have made money net of both haircuts and sales tax."
            )
        )
        self.passes = SortableTable(PASS_HEADERS)
        self.layout.addWidget(self.passes, 1)

        self.footer = QLabel("")
        self.footer.setWordWrap(True)
        self.layout.addWidget(self.footer)

    def compute(self, data):
        from ...backtest import measure_haircuts
        from ...paper import PaperLedger

        ledger = PaperLedger(data.config.paths.paper_ledger, data.config)
        return build_learning_report(
            data.config,
            ledger,
            bars=data.all_bars,
            haircuts=measure_haircuts(data.book, tuple(data.config.costs.notional_tiers_isk)),
            setups=data.setups,
            vocabulary=data.vocabulary,
            now=data.loaded_at,
        )

    def paint(self, report) -> None:
        self.headline.setText(
            f"{report.closed_trades} closed trade(s), {report.recorded_passes} recorded "
            f"pass(es). A read needs {MIN_SAMPLES_FOR_A_READ}."
        )
        self.setups.set_rows(
            [
                [
                    (record.name, record.name),
                    (str(record.closed), record.closed),
                    (str(record.open_now), record.open_now),
                    _pct(record.win_rate),
                    _pct(record.win_rate_lower),
                    _num(record.average_r),
                    _num(record.median_r),
                    _num(record.expected_r),
                    _num(record.freshness, ".2f"),
                    (f"{record.state} · {record.validation}", record.state),
                ]
                for record in report.setups
            ],
            [record.as_dict() for record in report.setups],
        )
        self.likes.set_rows(
            [
                [
                    (record.label or record.tag, record.label or record.tag),
                    (str(record.closed), record.closed),
                    _pct(record.win_rate),
                    _pct(record.win_rate_lower),
                    _num(record.average_r),
                    (record.state, record.state),
                ]
                for record in report.like_tags
            ]
        )
        self.passes.set_rows(
            [
                [
                    (record.label or record.tag, record.label or record.tag),
                    (str(record.passes), record.passes),
                    (str(record.measured), record.measured),
                    (str(record.pending), record.pending),
                    _pct(record.right_rate),
                    _pct(record.right_rate_lower),
                    _num(record.average_forgone_pct),
                    (record.state, record.state),
                ]
                for record in report.dislike_tags
            ]
        )
        self.footer.setText(" ".join(report.notes))
