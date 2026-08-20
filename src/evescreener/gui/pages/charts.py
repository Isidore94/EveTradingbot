"""CHARTS — the one re-pointing chart window (§19 Part 2 page 2).

There is exactly one `ChartPanel` on the desk and it lives here. Every other
page reaches it by emitting `chart_requested`; nothing anywhere opens a second
chart. A stack of chart windows is how a desk becomes unusable, and it is also
how two names quietly end up compared against two different anchor sets.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from ..chart import ChartPanel, build_series
from .base import DeskPage

__all__ = ["ChartsPage"]


class ChartsPage(DeskPage):
    title = "CHARTS"

    def build(self) -> None:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("type a name and press Enter (resolved against the SDE)")
        self.search.returnPressed.connect(self._search)
        self.resolve_note = QLabel("")
        self.resolve_note.setStyleSheet("QLabel { color: #ff8080; }")

        self.buy = QPushButton("Paper buy")
        self.buy.setEnabled(False)
        self.pass_button = QPushButton("Not today")
        self.pass_button.setEnabled(False)
        self.bad = QPushButton("Bad signal")
        self.bad.setEnabled(False)

        row.addWidget(self.search, 1)
        row.addWidget(self.resolve_note)
        row.addWidget(self.buy)
        row.addWidget(self.pass_button)
        row.addWidget(self.bad)
        self.layout.addWidget(bar)

        self.panel = ChartPanel()
        self.layout.addWidget(self.panel, 1)
        self.current: int | None = None

        self.buy.clicked.connect(self._buy)
        self.pass_button.clicked.connect(lambda: self._pass("not_today"))
        self.bad.clicked.connect(lambda: self._pass("bad_signal"))

    def repopulate(self) -> None:
        if self.current is not None:
            self.show_type(self.current)

    def show_type(self, type_id: int, *, setup_tag: str = "discretionary") -> None:
        """Re-point at `type_id`. Always the same panel."""
        self.current = int(type_id)
        self.setup_tag = setup_tag
        positions = [
            position
            for position in self._open_positions()
            if int(position.get("type_id", -1)) == int(type_id)
        ]
        self.panel.show_series(build_series(self.data, int(type_id), positions=positions))
        for button in (self.buy, self.pass_button, self.bad):
            button.setEnabled(True)

    def _open_positions(self) -> list[dict]:
        from ...paper import PaperLedger

        ledger = PaperLedger(self.data.config.paths.paper_ledger, self.data.config)
        return [position for position in ledger.positions().values() if not position.get("close")]

    def _search(self) -> None:
        name = self.search.text().strip()
        if not name:
            return
        row = self.data.db.type_by_name(name)
        if row is None:
            # An unresolvable name is a loud error, never a guess (§11 D4).
            self.resolve_note.setText(f"no type named {name!r} in the SDE")
            return
        self.resolve_note.setText("")
        self.show_type(int(row["type_id"]))

    def _buy(self) -> None:
        if self.current is None:
            return
        from ..paperform import PaperOpenDialog, prefill_for

        dialog = PaperOpenDialog(
            self.data,
            prefill_for(
                self.data, self.current, setup_tag=getattr(self, "setup_tag", "discretionary")
            ),
            parent=self,
        )
        if dialog.exec():
            self.ledger_changed.emit()
            self.repopulate()

    def _pass(self, action: str) -> None:
        if self.current is None:
            return
        from ..paperform import PassDialog

        dialog = PassDialog(self.data, self.current, action=action, parent=self)
        if dialog.exec():
            self.ledger_changed.emit()
