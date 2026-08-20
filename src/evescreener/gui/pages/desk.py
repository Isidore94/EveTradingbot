"""DESK — the one page the operator actually works from (plan.md §20.1).

The daily loop this exists to serve: open the desk, walk the lists on the
left, chart each name on the right, paper-trade the ones worth trading, and
close it. Everything in one place on one monitor, so checking the market is a
few minutes rather than a tour of eight tabs.

**It composes; it does not fork.** Each tab on the left is the real page class
from the rail — `FocusPage`, `BoardPage`, `ScannerPage` — over the same
`DeskData`. There is no second implementation of the watchlist or the board to
drift out of step with the first, and a fix to either lands in both places at
once.

**Still one chart.** DESK does not build a `ChartPanel`; the window owns
exactly one and moves it into whichever page is showing (`DeskPage.dock_chart`,
§19 Part 2 page 2). Two panels would mean two anchor sets, and comparing two
names against two of those without noticing is the failure the single-window
rule exists to prevent.

**Lazy, like every other page.** A tab computes when it is first looked at and
not before, so opening DESK costs the price of its first tab rather than the
sum of all of them. That matters here more than anywhere: SCANNER evaluates
the whole tracked universe and is by far the slowest thing on the desk.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QTabWidget, QVBoxLayout, QWidget

from .base import DeskPage
from .board import BoardPage
from .focus import FocusPage
from .scanner import ScannerPage

__all__ = ["DeskReviewPage"]

#: Left-hand tabs, in the order the operator walks them. FOCUS first because
#: the watchlist is the list they own; SCANNER last because it is the slowest.
TABS = (
    ("FOCUS", FocusPage),
    ("BOARD", BoardPage),
    ("SCANNER", ScannerPage),
)


class DeskReviewPage(DeskPage):
    title = "DESK"

    def build(self) -> None:
        splitter = QSplitter(Qt.Horizontal)

        self.tabs = QTabWidget()
        self.panes: dict[str, DeskPage] = {}
        for title, factory in TABS:
            pane = factory(self.data)
            # A name clicked in a tab charts on the right of *this* page. The
            # window routes it to whoever is hosting the chart, so it does not
            # navigate away from the review the operator is in the middle of.
            pane.chart_requested.connect(self.chart_requested)
            pane.ledger_changed.connect(self.ledger_changed)
            self.panes[title] = pane
            self.tabs.addTab(pane, title)
        self.tabs.currentChanged.connect(self._tab_changed)
        splitter.addWidget(self.tabs)

        holder = QWidget()
        self.chart_slot = QVBoxLayout(holder)
        self.chart_slot.setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(holder)

        # The chart is the wider half: the lists are for picking, the chart is
        # for deciding.
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        self.layout.addWidget(splitter, 1)
        self.current: int | None = None

    # -- the visible tab is the only one that works ------------------------
    def _current_pane(self) -> DeskPage | None:
        return self.tabs.currentWidget()

    def _tab_changed(self, _index: int) -> None:
        pane = self._current_pane()
        if pane is not None:
            pane.ensure_current()

    def repopulate(self) -> None:
        """DESK itself holds no rows; it brings the visible tab current."""
        pane = self._current_pane()
        if pane is not None:
            pane.ensure_current()

    def refresh(self, data) -> None:
        super().refresh(data)
        for pane in self.panes.values():
            pane.refresh(data)

    # -- charting ----------------------------------------------------------
    def show_type(self, type_id: int, *, setup_tag: str = "discretionary") -> None:
        """Chart `type_id` in the docked panel, without leaving this page."""
        self.current = int(type_id)
        self.setup_tag = setup_tag
        if self.panel is None:  # pragma: no cover - the window always docks one
            return
        from ..chart import build_series

        self.panel.show_series(build_series(self.data, int(type_id)))
