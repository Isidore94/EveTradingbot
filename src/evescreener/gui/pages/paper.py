"""PAPER / JOURNAL — the ledger, with the §12.4 verdict (§19 Part 2 page 6).

The same ledger the CLI writes, through the same methods with the same
refusals. Marking is a local-data operation: it re-reads the book snapshot on
disk and stamps every mark with that book's age. It never fetches.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ...paper import PaperLedger
from ..widgets import BLANK, SortableTable, format_isk
from .base import DeskPage

__all__ = ["PaperPage"]

OPEN_HEADERS = [
    "position",
    "name",
    "opened",
    "entry",
    "stop",
    "target",
    "setup",
    "why",
    "book at entry",
]
CLOSED_HEADERS = ["name", "closed", "net %", "realized R", "setup", "priced"]
PASS_HEADERS = ["name", "at", "action", "why", "close at pass"]


class PaperPage(DeskPage):
    title = "PAPER"

    def build(self) -> None:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.mark_button = QPushButton("Mark to market (local data)")
        self.mark_button.clicked.connect(self._mark)
        self.close_button = QPushButton("Close position")
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self._close)
        row.addWidget(self.mark_button)
        row.addWidget(self.close_button)
        row.addStretch(1)
        self.layout.addWidget(bar)

        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.verdict.setStyleSheet("QLabel { font-weight: 600; }")
        self.layout.addWidget(self.verdict)

        self.layout.addWidget(QLabel("Open positions"))
        self.open_table = SortableTable(OPEN_HEADERS)
        self.open_table.itemSelectionChanged.connect(self._selected)
        self.open_table.cellDoubleClicked.connect(self._charted)
        self.layout.addWidget(self.open_table, 2)

        self.layout.addWidget(QLabel("Closed"))
        self.closed_table = SortableTable(CLOSED_HEADERS)
        self.closed_table.cellDoubleClicked.connect(self._charted_closed)
        self.layout.addWidget(self.closed_table, 2)

        self.layout.addWidget(QLabel("Recorded passes — the other half of the record"))
        self.pass_table = SortableTable(PASS_HEADERS)
        self.layout.addWidget(self.pass_table, 1)

        self.footer = QLabel("")
        self.footer.setWordWrap(True)
        self.layout.addWidget(self.footer)
        self.repopulate()

    # -- data --------------------------------------------------------------
    def _ledger(self) -> PaperLedger:
        return PaperLedger(self.data.config.paths.paper_ledger, self.data.config)

    def repopulate(self) -> None:
        ledger = self._ledger()
        report = ledger.report(now=self.data.loaded_at)
        verdict = report.verdict or {}
        self.verdict.setText(
            f"§12.4 verdict: {verdict.get('verdict', 'UNKNOWN')} — "
            f"{verdict.get('reason', 'no reason recorded')}"
        )

        positions = ledger.positions()
        open_rows, open_payloads = [], []
        for position in positions.values():
            if position.get("close"):
                continue
            marks = position.get("marks") or []
            age = marks[-1].get("book_age_minutes") if marks else position.get("book_age_minutes")
            open_rows.append(
                [
                    (position["position_id"], position["position_id"]),
                    (position.get("type_name") or BLANK, position.get("type_name")),
                    (str(position.get("at"))[:16], str(position.get("at"))),
                    (
                        format_isk(position.get("entry_effective_price")),
                        position.get("entry_effective_price"),
                    ),
                    (format_isk(position.get("stop_price")), position.get("stop_price")),
                    (format_isk(position.get("target_price")), position.get("target_price")),
                    (position.get("setup_tag") or BLANK, position.get("setup_tag")),
                    (", ".join(position.get("like_tags") or []) or BLANK, None),
                    (f"{age:.0f} min" if age is not None else "UNKNOWN", age),
                ]
            )
            open_payloads.append(position)
        self.open_table.set_rows(open_rows, open_payloads)

        closed_rows, closed_payloads = [], []
        for record in report.closed:
            closed_rows.append(
                [
                    (record.get("type_name") or BLANK, record.get("type_name")),
                    (str(record.get("at"))[:16], str(record.get("at"))),
                    _cell(record.get("net_return_pct"), "+.2f"),
                    _cell(record.get("realized_r"), "+.2f"),
                    (record.get("setup_tag") or BLANK, record.get("setup_tag")),
                    (record.get("exit_source") or "book", record.get("exit_source") or "book"),
                ]
            )
            closed_payloads.append(record)
        self.closed_table.set_rows(closed_rows, closed_payloads)

        pass_rows = []
        for record in ledger.passes():
            pass_rows.append(
                [
                    (record.get("type_name") or BLANK, record.get("type_name")),
                    (str(record.get("at"))[:16], str(record.get("at"))),
                    (record.get("action") or BLANK, record.get("action")),
                    (", ".join(record.get("dislike_tags") or []) or BLANK, None),
                    (
                        format_isk(record.get("close_at_pass")),
                        record.get("close_at_pass"),
                    ),
                ]
            )
        self.pass_table.set_rows(pass_rows)

        self.footer.setText(
            f"refused/UNKNOWN {report.refused} · closed {len(report.closed)} · "
            f"net {report.cumulative_net_isk:,.0f} ISK. Marking reads the local book "
            "snapshot only; a stale book is refused, never repriced."
        )

    # -- actions -----------------------------------------------------------
    def _current(self) -> dict | None:
        rows = {item.row() for item in self.open_table.selectedItems()}
        return self.open_table.payload(next(iter(rows))) if rows else None

    def _selected(self) -> None:
        self.close_button.setEnabled(bool(self.open_table.selectedItems()))

    def _charted(self, view_row: int, _column: int) -> None:
        payload = self.open_table.payload(view_row)
        if payload:
            self.chart_requested.emit(int(payload["type_id"]))

    def _charted_closed(self, view_row: int, _column: int) -> None:
        payload = self.closed_table.payload(view_row)
        if payload and payload.get("type_id"):
            self.chart_requested.emit(int(payload["type_id"]))

    def _mark(self) -> None:
        self._ledger().mark(book=self.data.book, now=self.data.loaded_at)
        self.ledger_changed.emit()
        self.repopulate()

    def _close(self) -> None:
        position = self._current()
        if not position:
            return
        from ..paperform import PaperCloseDialog

        dialog = PaperCloseDialog(self.data, position, parent=self)
        if dialog.exec():
            self.ledger_changed.emit()
        self.repopulate()


def _cell(value, spec: str):
    if value is None:
        return (BLANK, None)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return (BLANK, None)
    return (format(number, spec), number)
