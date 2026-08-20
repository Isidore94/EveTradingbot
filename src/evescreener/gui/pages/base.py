"""What every page shares (plan.md §19 Part 2)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

__all__ = ["DeskPage"]


class DeskPage(QWidget):
    """A page over one `DeskData`.

    `chart_requested` is how every surface reaches the single chart window:
    a page never opens a chart itself, it says which type it wants charted and
    the window re-points. That is what keeps "one window, re-pointed" true no
    matter how many pages grow a clickable name.
    """

    chart_requested = Signal(int)
    ledger_changed = Signal()
    title = "PAGE"

    def __init__(self, data, parent=None) -> None:
        super().__init__(parent)
        self.data = data
        self.layout = QVBoxLayout(self)
        self.build()

    def build(self) -> None:  # pragma: no cover - overridden by every page
        raise NotImplementedError

    def refresh(self, data) -> None:
        """Re-read from a newer `DeskData`. Local files only — never ESI."""
        self.data = data
        self.repopulate()

    def repopulate(self) -> None:  # pragma: no cover - overridden by every page
        raise NotImplementedError
