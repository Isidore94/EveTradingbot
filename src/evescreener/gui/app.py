"""The desk window (plan.md §19 Part 2).

Left rail of pages, a chart that re-points rather than stacking, and a status
bar that says how old everything on screen is.

**The refresh timer is safe by construction.** It calls `load_desk`, which
reads the local Parquet lake, the local state database and the local book
snapshot and has no ESI client to reach for. The desk therefore shows
staleness rather than curing it: a 40-minute-old book renders as 40 minutes
old and refuses to price a fill, exactly as the CLI refuses it. Nothing on a
timer may cause a fetch before `Expires` — that is a correctness invariant and
circumventing it is a bannable offence (§3.2).

No sounds and no urgency styling in v1. Nothing a D1 screener finds is urgent,
and an alert tone is a commitment to a latency this system does not have.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QStatusBar,
)

from ..config import Config
from .chart import ChartPanel
from .data import desk_input_key, load_desk
from .pages import PAGES

__all__ = ["DeskWindow", "launch"]

DARK = """
QWidget { background: #1a1c20; color: #dfe3ea; }
QLabel { color: #dfe3ea; }
QListWidget { background: #141619; border: none; font-size: 13px; }
QListWidget::item { padding: 10px 14px; }
QListWidget::item:selected { background: #2a2f38; color: #ffffff; }
QTableWidget { background: #1e2126; gridline-color: #2c3037;
    alternate-background-color: #23262c; }
QTabWidget::pane { border: 1px solid #2c3037; }
QTabBar::tab { background: #23262c; padding: 6px 14px; border: 1px solid #2c3037; }
QTabBar::tab:selected { background: #2a2f38; color: #ffffff; }
QHeaderView::section { background: #23262c; padding: 4px; border: 0; }
QPushButton { background: #2a2f38; border: 1px solid #3a4049; padding: 5px 10px; }
QPushButton:disabled { color: #6a707a; }
QLineEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox { background: #23262c;
    border: 1px solid #3a4049; padding: 3px; }
"""


class DeskWindow(QMainWindow):
    """The desk. One window, one chart, nine pages."""

    def __init__(self, config: Config, *, region_id: int | None = None, data=None) -> None:
        super().__init__()
        self.config = config
        self.region_id = region_id or config.esi.home_region_id
        self.data = data if data is not None else load_desk(config, region_id=self.region_id)
        self.setWindowTitle(f"EveTradingbot desk — region {self.region_id}")
        self.resize(1500, 940)

        # The one chart panel. It belongs to the window and is *moved* into
        # whichever page is showing a chart, so DESK and CHARTS share a single
        # panel, a single anchor set and a single set of overlays (§19).
        self.chart_panel = ChartPanel()

        splitter = QSplitter(Qt.Horizontal)
        self.rail = QListWidget()
        self.rail.setFixedWidth(150)
        self.stack = QStackedWidget()
        self.pages: dict[str, object] = {}

        for title, factory in PAGES:
            page = factory(self.data)
            page.chart_requested.connect(self.chart_type)
            page.ledger_changed.connect(self.refresh)
            self.pages[title] = page
            self.stack.addWidget(page)
            self.rail.addItem(QListWidgetItem(title))

        self.rail.currentRowChanged.connect(self._page_selected)
        self.rail.setCurrentRow(0)
        splitter.addWidget(self.rail)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self.status = QStatusBar()
        self.stamp = QLabel("")
        self.status.addPermanentWidget(self.stamp)
        self.setStatusBar(self.status)

        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut(QKeySequence("F5"))
        refresh_action.triggered.connect(self.refresh)
        self.addAction(refresh_action)

        self.timer = QTimer(self)
        self.timer.setInterval(max(5, int(config.gui.refresh_seconds)) * 1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

        self._apply_banner()
        self._update_status()
        # Only the page the operator is looking at computes. MARKET is first in
        # the rail and cheap, so the window is interactive immediately.
        self._page_selected(self.rail.currentRow())

    # -- navigation --------------------------------------------------------
    def show_page(self, title: str) -> None:
        for index in range(self.rail.count()):
            if self.rail.item(index).text() == title:
                self.rail.setCurrentRow(index)
                return

    def _page_selected(self, index: int) -> None:
        """Show a page and let it bring itself current.

        This is the lazy half of §19.2's amendment: a page that has never been
        looked at has never computed, and a page whose inputs have not moved
        does nothing when it is looked at again.
        """
        self.stack.setCurrentIndex(index)
        page = self.stack.widget(index)
        if page is not None:
            page.dock_chart(self.chart_panel)
            page.ensure_current()

    def current_page(self):
        return self.stack.currentWidget()

    def chart_type(self, type_id: int) -> None:
        """Re-point the one chart window, without losing the operator's place.

        If the page they are on can host the chart — DESK, CHARTS — it charts
        there and they stay put. Only a page with nowhere to put a chart sends
        them to CHARTS, which is the old behaviour and still the right one for
        a click on MARKET or PAPER.
        """
        host = self.current_page()
        if getattr(host, "chart_slot", None) is None:
            host = self.pages.get("CHARTS")
            if host is None:  # pragma: no cover - CHARTS is always registered
                return
            self.show_page("CHARTS")
        host.show_type(int(type_id))

    # -- refresh -----------------------------------------------------------
    def tick(self) -> None:
        """The timer's job: notice whether anything on disk actually moved.

        Cheap by construction. It stats the lake and the operator's config
        files and compares the key; only if that key moved does it pay for a
        full `load_desk`. Daily bars change once a day, so on almost every tick
        this does nothing but restamp the status bar — which is the point, and
        the reason the old 60-second full rescan was modelling the timer rather
        than the data.
        """
        if desk_input_key(self.config, self.region_id) == self.data.input_key:
            self._update_status()
            return
        self.reload()

    def reload(self) -> None:
        """Re-read local data and recompute the visible page.

        This cannot cause an ESI fetch (see the module docstring). Pages that
        are not on screen take the new data and recompute when next looked at.
        """
        self.data = load_desk(self.config, region_id=self.region_id)
        for page in self.pages.values():
            page.refresh(self.data)
        self._apply_banner()
        self._update_status()
        page = self.current_page()
        if page is not None:
            page.ensure_current()

    def refresh(self) -> None:
        """Explicit operator refresh (F5): recompute the visible page now."""
        self.reload()
        page = self.current_page()
        if page is not None:
            page.ensure_current(force=True)

    def _backtest_verdict(self) -> dict | None:
        from ..report import _latest, _load

        stored = _load(_latest(self.config.paths.reports, "backtest")) or {}
        return stored.get("verdicts")

    def _apply_banner(self) -> None:
        """The same sentence, on MARKET and SCANNER, in the digest's words."""
        from ..backtest import verdict_banner

        banner = verdict_banner(self._backtest_verdict())
        for title in ("MARKET", "SCANNER"):
            page = self.pages.get(title)
            if page is not None and hasattr(page, "set_banner"):
                page.set_banner(banner)

    def _update_status(self) -> None:
        age = self.data.book_age_minutes
        book = (
            f"book {age:.0f} min old" + (" · STALE" if self.data.book_is_stale else "")
            if age is not None
            else "book UNKNOWN — no sweep on disk"
        )
        types = int(self.data.bars["type_id"].nunique()) if not self.data.bars.empty else 0
        self.stamp.setText(
            f"{types:,} tracked types · last bar {str(self.data.last_bar)[:10]} · {book} · "
            f"read {str(self.data.loaded_at)[11:16]}Z (local files only)"
        )


def launch(config: Config, argv=None) -> int:
    """Start the desk. Returns the Qt exit code."""
    app = QApplication.instance() or QApplication(list(argv or sys.argv[:1]))
    app.setStyleSheet(DARK)
    window = DeskWindow(config)
    window.show()
    return int(app.exec())
