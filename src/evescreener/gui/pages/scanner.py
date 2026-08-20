"""SCANNER — every setup, grouped, with honest zeros (§19 Part 2 page 5).

Each setup gets its own block with its own count of names examined. "Nothing
cleared this today" is printed as an answer, not left as an empty space — an
empty panel with no denominator cannot be told apart from a scan that never
ran.

The backtest banner sits at the top of this page in the digest's exact
wording, as it does on MARKET.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...scanner import run_scan
from ..widgets import BLANK, BannerLabel, SortableTable, format_isk
from .base import DeskPage

__all__ = ["ScannerPage"]

HEADERS = ["name", "close", "dipσ", "RRS", "sector", "friction %", "book age", "thin"]


class _SetupBlock(QWidget):
    """One setup's results, or its honest zero."""

    def __init__(self, scan, parent_page, parent=None) -> None:
        super().__init__(parent)
        self.scan = scan
        self.page = parent_page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)

        header = QHBoxLayout()
        marks = []
        if scan.builtin:
            marks.append("built-in")
        if scan.example:
            marks.append("example")
        marks.append(scan.validation)
        title = QLabel(f"{scan.name} — {' · '.join(marks)}")
        title.setStyleSheet("QLabel { font-weight: 600; }")
        header.addWidget(title)
        header.addStretch(1)
        self.buy = QPushButton("Paper buy")
        self.buy.setEnabled(False)
        self.buy.clicked.connect(self._buy)
        self.skip = QPushButton("Not today")
        self.skip.setEnabled(False)
        self.skip.clicked.connect(lambda: self._pass("not_today"))
        self.bad = QPushButton("Bad signal")
        self.bad.setEnabled(False)
        self.bad.clicked.connect(lambda: self._pass("bad_signal"))
        for button in (self.buy, self.skip, self.bad):
            header.addWidget(button)
        layout.addLayout(header)

        if scan.notes:
            note = QLabel(scan.notes)
            note.setWordWrap(True)
            note.setStyleSheet("QLabel { color: #9aa0aa; }")
            layout.addWidget(note)

        if not scan.hits:
            zero = QLabel(
                f"Nothing cleared this setup today ({scan.examined} examined, "
                f"{scan.unmeasurable} unmeasurable). That is an answer, not a gap."
            )
            zero.setWordWrap(True)
            layout.addWidget(zero)
            self.table = None
            return

        self.table = SortableTable(HEADERS)
        self.table.setMaximumHeight(min(260, 40 + 24 * len(scan.hits)))
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.cellDoubleClicked.connect(self._charted)
        rows, payloads = [], []
        for hit in scan.hits:
            name = hit.get("type_name") or f"type {hit['type_id']}"
            age = hit.get("book_age_minutes")
            rows.append(
                [
                    (name, name),
                    (format_isk(hit.get("close")), hit.get("close")),
                    _cell(hit.get("dip_sigma"), "+.2f"),
                    _cell(hit.get("rrs"), "+.2f"),
                    (hit.get("sector") or BLANK, hit.get("sector")),
                    _cell(hit.get("friction_pct"), ".2f"),
                    (f"{age:.0f} min" if age is not None else "UNKNOWN", age),
                    (hit.get("badge") or "", None),
                ]
            )
            payloads.append(hit)
        self.table.set_rows(rows, payloads)
        for index in range(self.table.rowCount()):
            payload = self.table.payload(index)
            if payload:
                self.table.badge_row(index, payload.get("badge") or "")
        layout.addWidget(self.table)
        layout.addWidget(
            QLabel(
                f"{len(scan.hits)} hit(s) of {scan.examined} examined; "
                f"{scan.unmeasurable} unmeasurable."
            )
        )

    def _current(self) -> dict | None:
        if self.table is None:
            return None
        rows = {item.row() for item in self.table.selectedItems()}
        return self.table.payload(next(iter(rows))) if rows else None

    def _selected(self) -> None:
        enabled = bool(self.table and self.table.selectedItems())
        for button in (self.buy, self.skip, self.bad):
            button.setEnabled(enabled)

    def _charted(self, view_row: int, _column: int) -> None:
        payload = self.table.payload(view_row)
        if payload:
            self.page.chart_requested.emit(int(payload["type_id"]))

    def _buy(self) -> None:
        hit = self._current()
        if not hit:
            return
        from ..paperform import PaperOpenDialog, prefill_for

        prefill = prefill_for(self.page.data, int(hit["type_id"]), setup_tag=self.scan.name)
        dialog = PaperOpenDialog(self.page.data, prefill, parent=self)
        if dialog.exec():
            self.page.ledger_changed.emit()

    def _pass(self, action: str) -> None:
        hit = self._current()
        if not hit:
            return
        from ..paperform import PassDialog

        dialog = PassDialog(
            self.page.data,
            int(hit["type_id"]),
            action=action,
            setup_tag=self.scan.name,
            parent=self,
        )
        if dialog.exec():
            self.page.ledger_changed.emit()


def _cell(value, spec: str):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return (BLANK, None)
    return (format(float(value), spec), float(value))


class ScannerPage(DeskPage):
    title = "SCANNER"
    heavy = True

    def build(self) -> None:
        self.banner = BannerLabel()
        self.layout.addWidget(self.banner)
        self.summary = QLabel("")
        self.layout.addWidget(self.summary)
        self.blocks = QWidget()
        self.blocks_layout = QVBoxLayout(self.blocks)
        self.layout.addWidget(self.blocks, 1)
        self.result = None

    def set_banner(self, text: str) -> None:
        self.banner.set_banner(text)

    def compute(self, data):
        sector_frames = {}
        if data.index_set is not None:
            sector_frames = {
                ticker: composite.frame
                for ticker, composite in data.index_set.sectors.items()
                if composite.known
            }
        with data.thread_local_db() as db:
            return run_scan(
                data.config,
                db,
                data.bars,
                getattr(data.composite, "frame", None),
                data.book,
                setups=data.setups,
                sectors=data.sectors,
                sector_frames=sector_frames,
                anchor_dates=data.anchor_dates,
                region_id=data.region_id,
                now=data.loaded_at,
            )

    def paint(self, result) -> None:
        self.result = result
        self.summary.setText(
            f"{self.result.evaluated} of {self.result.universe} names had enough bars "
            "to evaluate. Every hit prints its own friction and the age of the book "
            "it was priced against."
        )
        while self.blocks_layout.count():
            item = self.blocks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for scan in self.result.setups:
            self.blocks_layout.addWidget(_SetupBlock(scan, self))
        self.blocks_layout.addStretch(1)
