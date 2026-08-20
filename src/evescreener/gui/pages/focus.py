"""FOCUS — the operator watchlist (§19 Part 2 page 4, §11 D4).

The one rule this page exists to enforce visibly: **an operator-entered name
is never auto-removed.** Removal is a deliberate act with a confirm behind it,
and nothing automatic can reach the code path — not a floor change, not a
"not today", not a name falling out of the tracked universe. A name that no
longer resolves stays on the list, loudly unresolved, because a name that
silently vanished is a name you stop looking for.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ...universe import add_watch, remove_watch, watchlist_entries
from ..widgets import BLANK, SortableTable, format_isk
from .base import DeskPage

__all__ = ["FocusPage"]

HEADERS = ["name", "type id", "close", "median units/day", "tier", "note", "state"]


class FocusPage(DeskPage):
    title = "FOCUS"

    def build(self) -> None:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("add a name — resolved against the SDE, loudly")
        self.entry.returnPressed.connect(self._add)
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self._add)
        self.note_button = QPushButton("Note")
        self.note_button.clicked.connect(self._note)
        self.remove_button = QPushButton("Remove (deliberate)")
        self.remove_button.clicked.connect(self._remove)
        self.buy = QPushButton("Paper buy")
        self.buy.clicked.connect(self._buy)
        for widget in (self.entry, self.add_button, self.note_button, self.remove_button, self.buy):
            row.addWidget(widget)
        self.layout.addWidget(bar)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.layout.addWidget(self.message)

        self.table = SortableTable(HEADERS)
        self.table.cellDoubleClicked.connect(self._charted)
        self.layout.addWidget(self.table, 1)

        self.footer = QLabel(
            "Watchlist names are NEVER auto-removed (§11 D4). Removal is this button "
            "and nothing else — no floor change and no pass can reach it."
        )
        self.footer.setWordWrap(True)
        self.layout.addWidget(self.footer)
        self.repopulate()

    def repopulate(self) -> None:
        measurements = {
            int(row["type_id"]): row
            for row in self.data.db.conn.execute(
                "SELECT type_id, median_unit_volume, tier FROM universe WHERE region_id=?",
                (self.data.region_id,),
            )
            if row["type_id"] is not None
        }
        rows, payloads = [], []
        for entry in watchlist_entries(self.data.db):
            type_id = entry["type_id"]
            frame = self.data.frame_for(type_id) if type_id else None
            close = (
                float(frame["close"].iloc[-1]) if frame is not None and not frame.empty else None
            )
            measured = measurements.get(int(type_id)) if type_id else None
            units = measured["median_unit_volume"] if measured else None
            tier = measured["tier"] if measured else None
            state = "resolved" if type_id else "UNRESOLVED — check the spelling"
            rows.append(
                [
                    (entry["name"], entry["name"]),
                    (str(type_id) if type_id else BLANK, type_id),
                    (format_isk(close) if close else BLANK, close),
                    (f"{units:,.0f}" if units else BLANK, units),
                    (tier or BLANK, tier),
                    (entry["note"] or "", entry["note"] or ""),
                    (state, state),
                ]
            )
            payloads.append(dict(entry))
        self.table.set_rows(rows, payloads)

    def _current(self) -> dict | None:
        rows = {item.row() for item in self.table.selectedItems()}
        return self.table.payload(next(iter(rows))) if rows else None

    def _add(self) -> None:
        name = self.entry.text().strip()
        if not name:
            return
        row = self.data.db.type_by_name(name)
        if row is None:
            self.message.setText(
                f"no type named {name!r} in the SDE — run `sde` first, or check the spelling"
            )
            self.message.setStyleSheet("QLabel { color: #ff8080; }")
            return
        add_watch(self.data.db, name=row["name"], type_id=int(row["type_id"]))
        self.message.setText(f"added {row['name']}")
        self.message.setStyleSheet("")
        self.entry.clear()
        self.data.watch_ids.add(int(row["type_id"]))
        self.repopulate()

    def _note(self) -> None:
        entry = self._current()
        if not entry:
            return
        text, ok = QInputDialog.getText(
            self, "Note", f"note for {entry['name']}", text=entry.get("note") or ""
        )
        if not ok:
            return
        add_watch(
            self.data.db,
            name=entry["name"],
            type_id=int(entry["type_id"] or 0),
            note=text,
        )
        self.repopulate()

    def _remove(self) -> None:
        entry = self._current()
        if not entry:
            return
        from ..paperform import confirm

        if not confirm(
            self,
            "Remove from Focus",
            f"Remove {entry['name']} from the watchlist?\n\n"
            "This is the only removal path in the system and nothing automatic "
            "can reach it.",
        ):
            return
        remove_watch(self.data.db, entry["name"])
        self.data.watch_ids.discard(int(entry["type_id"] or 0))
        self.repopulate()

    def _charted(self, view_row: int, _column: int) -> None:
        entry = self.table.payload(view_row)
        if entry and entry.get("type_id"):
            self.chart_requested.emit(int(entry["type_id"]))

    def _buy(self) -> None:
        entry = self._current()
        if not entry or not entry.get("type_id"):
            return
        from ..paperform import PaperOpenDialog, prefill_for

        dialog = PaperOpenDialog(
            self.data, prefill_for(self.data, int(entry["type_id"])), parent=self
        )
        if dialog.exec():
            self.ledger_changed.emit()
