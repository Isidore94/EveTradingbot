"""TOP — the strongest names over a week and a month (plan.md §20.3).

The operator's ask was "find the strongest items in the market quickly". This
is that list, and it is deliberately only that: a ranking of what already
happened.

**It is not a recommendation, and the page says so.** This system's own
measurement is that continuation does not pay in an elastic-supply market
(§6, §17) — a spike is arbitraged flat by production rather than extended. A
name at the top of this table is a name worth *looking at*, on a chart, with
the rest of the desk's evidence around it.
"""

from __future__ import annotations

import pandas as pd
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from ...performers import (
    DEFAULT_MIN_UNITS,
    ENDPOINT_BARS,
    MONTH_BARS,
    WEEK_BARS,
    top_performers,
)
from ..widgets import BLANK, SortableTable, format_isk
from .base import DeskPage

__all__ = ["PerformersPage"]

HEADERS = [
    "name",
    "tier",
    "close",
    f"{WEEK_BARS}d %",
    f"{WEEK_BARS}d % raw",
    f"{MONTH_BARS}d %",
    f"{MONTH_BARS}d % raw",
    "units/day",
    "last bar",
    "state",
]

CAVEAT = (
    f"Ranked on a PRINT-RESISTANT return over {WEEK_BARS} and {MONTH_BARS} completed "
    f"bars — an EVE week and month, because this market never closes ({WEEK_BARS}/"
    f"{MONTH_BARS}, not the equity 5/20). Each end of a window is the median of "
    f"{ENDPOINT_BARS} bars: CCP does not filter outlier prints, and the raw "
    "close-to-close reading on the real Forge lake reaches +49,699,900% over a "
    "week. The 'raw' columns show that unguarded number beside it — when the two "
    "disagree, a single print is doing the work. This ranks what already happened "
    "and is NOT a recommendation: the system's own measurement is that "
    "continuation does not pay here, because elastic supply arbitrages a spike "
    "flat (plan.md §6). Stale bars and short histories report UNKNOWN."
)


class PerformersPage(DeskPage):
    title = "TOP"
    heavy = True

    def build(self) -> None:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self.window_box = QComboBox()
        self.window_box.addItem(f"{WEEK_BARS}-day", "week_pct")
        self.window_box.addItem(f"{MONTH_BARS}-day", "month_pct")
        self.window_box.currentIndexChanged.connect(self._repaint_only)
        row.addWidget(QLabel("rank by"))
        row.addWidget(self.window_box)

        self.min_units = QDoubleSpinBox()
        self.min_units.setRange(0.0, 1_000_000.0)
        self.min_units.setDecimals(0)
        self.min_units.setValue(DEFAULT_MIN_UNITS)
        self.min_units.valueChanged.connect(self._repaint_only)
        row.addWidget(QLabel("min units/day"))
        row.addWidget(self.min_units)

        self.buy = QPushButton("Paper buy")
        self.buy.setEnabled(False)
        self.buy.clicked.connect(self._buy)
        row.addStretch(1)
        row.addWidget(self.buy)
        self.layout.addWidget(bar)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.layout.addWidget(self.summary)

        self.caveat = QLabel(CAVEAT)
        self.caveat.setWordWrap(True)
        self.caveat.setStyleSheet("QLabel { color: #c48a20; }")
        self.layout.addWidget(self.caveat)

        self.table = SortableTable(HEADERS)
        self.table.cellDoubleClicked.connect(self._charted)
        self.table.itemSelectionChanged.connect(self._selected)
        self.layout.addWidget(self.table, 1)
        self._rows = pd.DataFrame()

    # -- scheduling --------------------------------------------------------
    def _repaint_only(self) -> None:
        """Window and floor are views over what is already computed."""
        if self._result is not None:
            self.paint(self._result)

    def compute(self, data, job_input=()):
        """Off-thread. One region's bars, its own names, tiers and volumes.

        Nothing here reads a widget: the ranking window and the volume floor
        are applied in `paint`, because both are views over the same computed
        table rather than different computations (§21 R7).
        """
        region = int(data.region_id)
        with data.thread_local_db() as db:
            volumes = {
                int(row[0]): float(row[1])
                for row in db.conn.execute(
                    "SELECT type_id, median_unit_volume FROM universe WHERE region_id=?",
                    (region,),
                ).fetchall()
                if row[1] is not None
            }
            bars = data.bars_for_region(region)
            wanted = set(bars["type_id"].unique()) if not bars.empty else set()
            names = db.type_names(wanted) if wanted else {}
        return top_performers(
            bars,
            now=data.loaded_at,
            names=names,
            tiers=dict(data.tiers),
            volumes=volumes,
            min_units=0.0,  # the floor is a view; see `paint`
            max_bar_age_days=data.config.screen.max_bar_age_days,
            max_refresh_age_hours=data.config.screen.max_refresh_age_hours,
        )

    def paint(self, result) -> None:
        self._result = result
        rank_by = self.window_box.currentData() or "week_pct"
        floor = float(self.min_units.value())

        shown = result
        if not shown.empty:
            if floor > 0:
                units = pd.to_numeric(shown["median_units"], errors="coerce")
                shown = shown[units.notna() & (units >= floor)]
            shown = shown.sort_values(rank_by, ascending=False, na_position="last")
        self._rows = shown.reset_index(drop=True)

        known = int((self._rows["state"] == "OK").sum()) if not self._rows.empty else 0
        self.summary.setText(
            f"{len(self._rows):,} of {len(result):,} names shown · {known:,} with a "
            f"measurable return · ranked by {rank_by.replace('_pct', '')}"
        )
        self.table.set_rows(
            [
                [
                    row["name"],
                    row["tier"] or BLANK,
                    _cell(format_isk(row["close"]), row["close"]),
                    _cell(_pct(row["week_pct"]), row["week_pct"]),
                    _cell(_pct(row["week_pct_raw"]), row["week_pct_raw"]),
                    _cell(_pct(row["month_pct"]), row["month_pct"]),
                    _cell(_pct(row["month_pct_raw"]), row["month_pct_raw"]),
                    _cell(_units(row["median_units"]), row["median_units"]),
                    row["last_bar"] or BLANK,
                    row["state"],
                ]
                for _index, row in self._rows.iterrows()
            ],
            payloads=[row.to_dict() for _index, row in self._rows.iterrows()],
        )
        # THIN is badged, never quietly mixed (§11 D3).
        for view_row in range(self.table.rowCount()):
            payload = self.table.payload(view_row)
            if payload and payload.get("tier") == "THIN":
                self.table.badge_row(view_row, "THIN")

    # -- row actions -------------------------------------------------------
    def _current(self) -> dict | None:
        view_row = self.table.currentRow()
        return self.table.payload(view_row) if view_row >= 0 else None

    def _selected(self) -> None:
        self.buy.setEnabled(self._current() is not None)

    def _charted(self, view_row: int, _column: int) -> None:
        row = self.table.payload(view_row)
        if row is not None:
            self.chart_requested.emit(int(row["type_id"]))

    def _buy(self) -> None:
        row = self._current()
        if row is None:
            return
        from ..paperform import PaperOpenDialog, prefill_for

        dialog = PaperOpenDialog(
            self.data,
            prefill_for(self.data, int(row["type_id"]), setup_tag="discretionary"),
            parent=self,
        )
        if dialog.exec():
            self.ledger_changed.emit()


def _cell(text: str, value):
    """(text, value) so a header click sorts on the number, not the string."""
    return (text, None if value is None or value != value else float(value))


def _pct(value) -> str:
    return BLANK if value is None or value != value else f"{float(value):+.2f}%"


def _units(value) -> str:
    return BLANK if value is None or value != value else f"{float(value):,.0f}"
