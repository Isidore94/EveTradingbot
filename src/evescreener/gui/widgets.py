"""Shared desk widgets — the idioms every page has to obey (plan.md §18, §19).

Two of these carry rules rather than styling:

* `SortableTable` sorts **blanks to the bottom whichever way the column is
  sorted**. A blank is a value the desk could not measure, not a zero and not
  a minimum. Letting UNKNOWN float to the top of an ascending sort would put
  the least-known rows where the eye goes first, which is precisely backwards.
* `BannerLabel` renders the backtest verdict in the **same wording** the
  digest uses, because it is the same finding. It is placed above content,
  never in a footer: if the setup class failed its own pre-stated test, no one
  should read a ranked list without knowing that first.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

__all__ = [
    "BLANK",
    "BannerLabel",
    "SortableTable",
    "StampLabel",
    "format_isk",
    "section",
]

# What an unmeasurable cell shows. One string, everywhere.
BLANK = "—"

_THIN_COLOUR = QColor(196, 138, 32)
_UNKNOWN_COLOUR = QColor(128, 128, 128)


def format_isk(value, digits: int = 2) -> str:
    """Compact ISK. Prices span twelve orders of magnitude in this market."""
    if value is None:
        return "UNKNOWN"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if not np.isfinite(number):
        return "UNKNOWN"
    magnitude = abs(number)
    if magnitude >= 1e9:
        return f"{number / 1e9:,.{digits}f}B"
    if magnitude >= 1e6:
        return f"{number / 1e6:,.{digits}f}M"
    return f"{number:,.{digits}f}"


class _Cell(QTableWidgetItem):
    """A cell that knows whether its value is UNKNOWN, and sorts accordingly."""

    def __init__(self, text: str, value=None) -> None:
        super().__init__(text)
        self.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.value = value
        self.blank = value is None or (isinstance(value, float) and not np.isfinite(value))
        if self.blank:
            self.setForeground(_UNKNOWN_COLOUR)

    def __lt__(self, other) -> bool:
        """Blanks lose every comparison, so they land at the bottom ascending.

        Qt reverses the comparison for a descending sort, which would float
        blanks back to the top — `SortableTable` re-parks them after every
        sort, which is what actually enforces the idiom in both directions.
        """
        if not isinstance(other, _Cell):  # pragma: no cover - Qt always pairs cells
            return super().__lt__(other)
        if self.blank:
            return False
        if other.blank:
            return True
        if isinstance(self.value, str) or isinstance(other.value, str):
            return str(self.value) < str(other.value)
        return float(self.value) < float(other.value)


class SortableTable(QTableWidget):
    """A table whose blanks stay at the bottom, and which never refetches.

    Sorting is done here rather than by `QTableWidget.sortItems`, on purpose.
    Qt's comparator is simply reversed for a descending sort, so any ordering
    that keeps blanks last ascending puts them first descending — the one
    behaviour §18.1 rules out. Ordering the rows ourselves makes "blanks at the
    bottom whichever way the column is sorted" a property of the code rather
    than something that happens to fall out of a comparator.

    Sorting is also a pure view operation over rows already in memory. A click
    on a column header must never cost an ESI request, and re-running a scan on
    a sort would silently change what the operator is comparing.
    """

    def __init__(self, headers: list[str], parent=None) -> None:
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)
        # Qt's own sort stays off. See the class docstring.
        self.setSortingEnabled(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSortIndicatorShown(True)
        self.horizontalHeader().sectionClicked.connect(self.sort_by)
        self._rows: list[list] = []
        self._payloads: list[object] = []
        self._order: list[int] = []
        self._sort_column: int | None = None
        self._descending = False
        self._badges: dict[int, str] = {}

    # -- population --------------------------------------------------------
    def set_rows(self, rows: list[list], payloads: list | None = None) -> None:
        """Replace the contents. Each cell is `(text, value)` or a plain value."""
        self._rows = [list(row) for row in rows]
        self._payloads = list(payloads or [None] * len(rows))
        self._badges = {}
        self._order = list(range(len(self._rows)))
        if self._sort_column is not None:
            self._reorder()
        self._render()

    def _value(self, source_row: int, column: int):
        cell = self._rows[source_row][column]
        return cell[1] if isinstance(cell, tuple) else cell

    def _text(self, source_row: int, column: int) -> str:
        cell = self._rows[source_row][column]
        return str(cell[0] if isinstance(cell, tuple) else cell)

    @staticmethod
    def _is_blank(value) -> bool:
        return value is None or (isinstance(value, float) and not np.isfinite(value))

    def _render(self) -> None:
        self.setRowCount(len(self._order))
        for view_row, source_row in enumerate(self._order):
            for column in range(self.columnCount()):
                value = self._value(source_row, column)
                item = _Cell(self._text(source_row, column), value)
                item.setData(Qt.UserRole, source_row)
                self.setItem(view_row, column, item)
            badge = self._badges.get(source_row)
            if badge == "THIN":
                self._paint_row(view_row, _THIN_COLOUR)

    def _paint_row(self, view_row: int, colour: QColor) -> None:
        for column in range(self.columnCount()):
            item = self.item(view_row, column)
            if item is not None:
                item.setForeground(colour)

    def payload(self, view_row: int):
        """The record behind a visible row, after any sort."""
        item = self.item(view_row, 0)
        if item is None:
            return None
        index = item.data(Qt.UserRole)
        if index is None or index >= len(self._payloads):
            return None
        return self._payloads[index]

    def badge_row(self, view_row: int, badge: str) -> None:
        item = self.item(view_row, 0)
        if item is None:
            return
        source = item.data(Qt.UserRole)
        if source is not None:
            self._badges[source] = badge
        if badge == "THIN":
            self._paint_row(view_row, _THIN_COLOUR)

    # -- the idiom ---------------------------------------------------------
    def sort_by(self, column: int, descending: bool | None = None) -> None:
        """Sort by one column. Blanks land at the bottom either direction."""
        if descending is None:
            descending = not self._descending if self._sort_column == column else False
        self._sort_column = int(column)
        self._descending = bool(descending)
        self._reorder()
        self.horizontalHeader().setSortIndicator(
            self._sort_column, Qt.DescendingOrder if self._descending else Qt.AscendingOrder
        )
        self._render()

    def _reorder(self) -> None:
        column = self._sort_column
        if column is None:
            return

        def key(source_row: int):
            value = self._value(source_row, column)
            blank = self._is_blank(value)
            if blank:
                # Blank first in the tuple, so it always trails after the
                # measured rows — and it is NOT negated for a descending sort,
                # which is what keeps it at the bottom both ways.
                return (1, 0.0, "")
            if isinstance(value, str):
                return (0, 0.0, value.lower() if not self._descending else _reverse(value))
            number = float(value)
            return (0, -number if self._descending else number, "")

        self._order = sorted(self._order, key=key)

    def blank_rows_are_last(self) -> bool:
        """Test hook: is every blank row below every measured one?"""
        if self._sort_column is None:
            return True
        seen_blank = False
        for source_row in self._order:
            blank = self._is_blank(self._value(source_row, self._sort_column))
            if blank:
                seen_blank = True
            elif seen_blank:
                return False
        return True


def _reverse(text: str) -> str:
    """A descending key for strings, without reversing the blank rule."""
    return "".join(chr(0x10FFFF - ord(char)) for char in text.lower())


class BannerLabel(QLabel):
    """The backtest verdict, in the digest's words, above the content."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.PlainText)
        self.set_banner(text)

    def set_banner(self, text: str) -> None:
        self.setText(text or "")
        self.setVisible(bool(text))
        if text:
            self.setStyleSheet(
                "QLabel { background: #4a2c00; color: #ffd79a; padding: 8px;"
                " border: 1px solid #7a4a00; }"
            )


class StampLabel(QLabel):
    """A freshness stamp. UNKNOWN is written out, never left blank."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)

    def set_stamp(self, *, sweep_ts: str | None, age_minutes: float | None, stale: bool) -> None:
        if sweep_ts is None or age_minutes is None:
            self.setText("book: UNKNOWN — no sweep on disk")
            self.setStyleSheet("QLabel { color: #c48a20; }")
            return
        text = f"book: swept {sweep_ts[:16]}Z, {age_minutes:.0f} min old"
        if stale:
            text += " — STALE, nothing prices off it"
        self.setText(text)
        self.setStyleSheet("QLabel { color: #c48a20; }" if stale else "")


def section(title: str, *widgets: QWidget) -> QWidget:
    """A titled vertical block. Pure layout, no behaviour."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(title)
    label.setStyleSheet("QLabel { font-weight: 600; }")
    layout.addWidget(label)
    for widget in widgets:
        layout.addWidget(widget)
    return holder
