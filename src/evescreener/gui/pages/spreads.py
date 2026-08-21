"""SPREADS — the maker / station-trading read (plan.md §20.2).

Buy at the bid, sell at the ask, collect the difference. This is the *maker*
side of the same book §17 measured from the taker side, and the 98.8% median
Forge spread that made taking hopeless is what a maker is paid.

The page's real job is not to find wide spreads — those are everywhere and
most of them are worthless. It is to keep the operator from mistaking a dust
bid for an opportunity. Every row is anchored to the **traded average**, the
guards are visible controls rather than hidden constants, and "show excluded"
puts the rejects back on screen with their flags so the guard can be checked
rather than trusted.
"""

from __future__ import annotations

import pandas as pd
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from ...spreads import DEFAULT_MIN_UNITS, filter_rows, hub_choices, maker_spreads
from ..widgets import BLANK, SortableTable, format_isk
from .base import DeskPage

__all__ = ["SpreadsPage"]

HEADERS = [
    "name",
    "hub",
    "bid",
    "traded avg",
    "ask",
    "quoted margin %",
    "quoted margin ISK/unit",
    "bid/avg",
    "ask/avg",
    "units/day",
    "bid depth",
    "fill risk",
    "state",
]

CAVEAT = (
    "QUOTED MARGIN, BEFORE EXECUTION RISK — not an edge and not a net return. "
    "It is the margin the book is quoting between two resting orders, minus "
    "broker fees at that station and sales tax. It does NOT model queue "
    "position, fill probability, waiting time, undercut risk or relist fees, "
    "because nothing in this lake measures them; what you would actually keep "
    "is a strictly smaller and unmeasured number. Rows are anchored to the "
    "traded average, and the 0.5x bid / 2.0x ask guards are OPERATOR "
    "HEURISTICS rather than derived thresholds (plan.md §21 R4)."
)


class SpreadsPage(DeskPage):
    title = "SPREADS"
    heavy = True

    def build(self) -> None:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self.hub = QComboBox()
        self._hubs = hub_choices(self.data.config)
        for label, regions in self._hubs:
            self.hub.addItem(label, regions)
        self.hub.currentIndexChanged.connect(self._controls_changed)
        row.addWidget(QLabel("hub"))
        row.addWidget(self.hub)

        self.min_units = QDoubleSpinBox()
        self.min_units.setRange(0.0, 1_000_000.0)
        self.min_units.setDecimals(0)
        self.min_units.setValue(DEFAULT_MIN_UNITS)
        self.min_units.valueChanged.connect(self._repaint_only)
        row.addWidget(QLabel("min units/day"))
        row.addWidget(self.min_units)

        self.show_excluded = QCheckBox("show excluded")
        self.show_excluded.toggled.connect(self._repaint_only)
        row.addWidget(self.show_excluded)

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

    # -- controls ----------------------------------------------------------
    def _controls_changed(self) -> None:
        """Changing the hub means a different book: recompute."""
        self.ensure_current(force=True)

    def _repaint_only(self) -> None:
        """Thresholds are a view over what is already computed."""
        if self._result is not None:
            self.paint(self._result)

    # -- compute -----------------------------------------------------------
    def job_input(self) -> tuple:
        """The hub selection, read on the GUI thread (§21 R7).

        `compute()` used to call `QComboBox.currentData()` on a worker thread.
        Qt widgets are not thread-safe and the selection can change mid-read,
        so the regions are frozen into a tuple here and handed to the job.
        """
        if not hasattr(self, "hub"):
            return ()
        return tuple(int(region) for region in (self.hub.currentData() or ()))

    def compute(self, data, job_input=()):
        """Off-thread. Builds its own name/volume/average maps, per region.

        Per region deliberately: judging Amarr's book against Jita's traded
        average would be a quiet lie, and a hub the census has never run on
        gets an empty map and reports NO_AVG, which is the true answer.
        """
        # The hub selection, frozen on the GUI thread by `job_input()` before
        # this job was dispatched. Reading the combo box here would be a
        # cross-thread widget access (§21 R7).
        regions = [int(region) for region in (job_input or ())]
        volumes: dict[int, dict[int, float]] = {}
        averages: dict[int, dict[int, float]] = {}
        names: dict[int, str] = {}
        with data.thread_local_db() as db:
            for region in regions:
                rows = db.conn.execute(
                    "SELECT type_id, median_unit_volume FROM universe WHERE region_id=?",
                    (region,),
                ).fetchall()
                volumes[region] = {int(row[0]): float(row[1]) for row in rows if row[1] is not None}
            for region in regions:
                # Keyed by region, so Amarr is never judged against Jita's
                # traded averages (§21 R8).
                region_averages = data.last_close_by_region(region)
                if region_averages:
                    averages[region] = region_averages
            wanted = {tid for mapping in averages.values() for tid in mapping}
            wanted |= {tid for mapping in volumes.values() for tid in mapping}
            if wanted:
                names = db.type_names(wanted)
        return maker_spreads(
            data.config,
            regions,
            names=names,
            volumes_by_region=volumes,
            averages_by_region=averages,
        )

    def paint(self, result) -> None:
        self._result = result
        frames = [hub.rows for hub in result if not hub.rows.empty]
        notes = [f"{hub.hub}: {hub.note}" for hub in result if hub.note]
        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        shown = combined
        if not combined.empty:
            shown = filter_rows(
                combined,
                min_units=float(self.min_units.value()),
                only_ok=not self.show_excluded.isChecked(),
                positive_only=not self.show_excluded.isChecked(),
            )
        self._rows = shown.reset_index(drop=True)

        head = (
            f"{len(self._rows):,} of {len(combined):,} two-sided books shown"
            if not combined.empty
            else "no two-sided book to read"
        )
        self.summary.setText(head + ((" · " + " · ".join(notes)) if notes else ""))

        self.table.set_rows(
            [
                [
                    row["name"],
                    row["hub"],
                    _cell(format_isk(row["best_bid"]), row["best_bid"]),
                    _cell(format_isk(row["avg"]), row["avg"]),
                    _cell(format_isk(row["best_ask"]), row["best_ask"]),
                    _cell(_pct(row["quoted_margin_pct"]), row["quoted_margin_pct"]),
                    _cell(format_isk(row["quoted_margin_isk"]), row["quoted_margin_isk"]),
                    _cell(_ratio(row["bid_vs_avg"]), row["bid_vs_avg"]),
                    _cell(_ratio(row["ask_vs_avg"]), row["ask_vs_avg"]),
                    _cell(_units(row["median_units"]), row["median_units"]),
                    _cell(_units(row["bid_depth"]), row["bid_depth"]),
                    row["fill_note"],
                    row["state"],
                ]
                for _index, row in self._rows.iterrows()
            ],
            payloads=[row.to_dict() for _index, row in self._rows.iterrows()],
        )

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
    """(text, value) so a header click sorts on the number, not the string.

    `SortableTable` keeps blanks at the bottom either way, and it can only do
    that if it is handed the real value rather than its formatting.
    """
    return (text, None if value is None or value != value else float(value))


def _pct(value) -> str:
    return BLANK if value is None or value != value else f"{float(value):+.2f}%"


def _ratio(value) -> str:
    return BLANK if value is None or value != value else f"{float(value):.2f}x"


def _units(value) -> str:
    return BLANK if value is None or value != value else f"{float(value):,.0f}"
