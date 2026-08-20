"""BOARD — the observation board (§18, §19 Part 2 page 3).

Observation, never opportunity. This board deliberately shows names that do
NOT clear costs, with their measured friction printed beside them, because
learning why the screen rejects a name is the point of looking. Nothing here
ranks on net edge and nothing here calls itself a pick.

Sorting is a view operation. Clicking a header re-orders rows already in
memory — it never refetches, and blanks stay at the bottom whichever way the
column is sorted.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QWidget

from ...brief import build_board
from ..widgets import BLANK, SortableTable, format_isk
from .base import DeskPage

__all__ = ["BoardPage"]

HEADERS = [
    "name",
    "close",
    "Δ1d %",
    "dipσ",
    "RRS",
    "part",
    "friction %",
    "thin",
    "setup",
]


class BoardPage(DeskPage):
    title = "BOARD"

    def build(self) -> None:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.watch_only = QCheckBox("watchlist only")
        self.watch_only.toggled.connect(self.repopulate)
        self.buy = QPushButton("Paper buy")
        self.buy.setEnabled(False)
        self.buy.clicked.connect(self._buy)
        row.addWidget(self.watch_only)
        row.addStretch(1)
        row.addWidget(self.buy)
        self.layout.addWidget(bar)

        self.caption = QLabel(
            "Observation, not opportunity: friction is printed beside every row and never "
            "used to hide one. Click a column to sort — blanks stay at the bottom either way."
        )
        self.caption.setWordWrap(True)
        self.layout.addWidget(self.caption)

        self.table = SortableTable(HEADERS)
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.cellDoubleClicked.connect(self._charted)
        self.layout.addWidget(self.table, 1)

        self.footer = QLabel("")
        self.layout.addWidget(self.footer)
        self.repopulate()

    def repopulate(self) -> None:
        data = self.data
        frame = data.bars
        if self.watch_only.isChecked() and data.watch_ids and not data.all_bars.empty:
            frame = data.all_bars[data.all_bars["type_id"].isin(sorted(data.watch_ids))]
        board = build_board(
            data.config,
            data.db,
            frame,
            getattr(data.composite, "frame", None),
            data.book,
            watch_ids=data.watch_ids,
            anchor_dates=data.anchor_dates,
            region_id=data.region_id,
            now=data.loaded_at,
            top=400,
        )
        rows, payloads = [], []
        for record in board.rows:
            name = record.get("type_name") or f"type {record['type_id']}"
            if record["type_id"] in data.watch_ids:
                name = f"★ {name}"
            rows.append(
                [
                    (name, name),
                    (format_isk(record.get("close")), record.get("close")),
                    self._cell(record.get("day_change_pct"), "+.2f"),
                    self._cell(record.get("dip_sigma"), "+.2f"),
                    self._cell(record.get("rrs"), "+.2f"),
                    self._cell(record.get("participation"), ".2f"),
                    self._cell(record.get("friction_pct"), ".2f"),
                    (record.get("tier") if record.get("tier") == "THIN" else "", None),
                    ("yes" if record.get("is_setup") else "", record.get("is_setup")),
                ]
            )
            payloads.append(record)
        self.table.set_rows(rows, payloads)
        for index in range(self.table.rowCount()):
            payload = self.table.payload(index)
            if payload:
                self.table.badge_row(index, payload.get("tier") or "")
        self.footer.setText(
            f"{len(board.rows)} of {board.measured} measured shown "
            f"({board.universe} in scope) · {board.unknown_friction} friction UNKNOWN "
            f"(stale or thin book) · {board.setups} setup(s) today"
        )

    @staticmethod
    def _cell(value, spec: str):
        if value is None or (isinstance(value, float) and not np.isfinite(value)):
            return (BLANK, None)
        return (format(float(value), spec), float(value))

    def _selected(self) -> None:
        self.buy.setEnabled(bool(self.table.selectedItems()))

    def _current(self) -> dict | None:
        rows = {item.row() for item in self.table.selectedItems()}
        return self.table.payload(next(iter(rows))) if rows else None

    def _charted(self, view_row: int, _column: int) -> None:
        payload = self.table.payload(view_row)
        if payload:
            self.chart_requested.emit(int(payload["type_id"]))

    def _buy(self) -> None:
        payload = self._current()
        if not payload:
            return
        from ..paperform import PaperOpenDialog, prefill_for

        dialog = PaperOpenDialog(
            self.data, prefill_for(self.data, int(payload["type_id"])), parent=self
        )
        if dialog.exec():
            self.ledger_changed.emit()
