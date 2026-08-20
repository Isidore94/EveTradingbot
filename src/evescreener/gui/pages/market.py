"""MARKET — FORGE, its breadth read, and sector rotation (§19 Part 2 page 1)."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSplitter, QWidget

from ...indices import FORGE, FORGE_EW
from ..chart import ChartCanvas, ChartSeries
from ..widgets import BLANK, BannerLabel, SortableTable, section
from .base import DeskPage

__all__ = ["MarketPage"]

ROTATION_HEADERS = ["sector", "name", "RRS vs FORGE", "1d %", "5d %", "20d %", "members", "state"]


def _index_series(composite, name: str) -> ChartSeries:
    """An index drawn with the same painter every price chart uses."""
    series = ChartSeries(type_id=0, type_name=name)
    if composite is None or not composite.known:
        series.note = "UNKNOWN — not enough members cleared the floor to build this index"
        return series
    frame = composite.frame
    closes = frame["close"].to_numpy(dtype="float64")
    series.stamps = list(frame["datetime"])
    series.close = closes
    series.high = frame["high"].to_numpy(dtype="float64")
    series.low = frame["low"].to_numpy(dtype="float64")
    series.volume = frame["volume"].to_numpy(dtype="float64")
    return series


class MarketPage(DeskPage):
    """The market read: FORGE with its bands, breadth, and the rotation table."""

    title = "MARKET"

    def build(self) -> None:
        self.banner = BannerLabel()
        self.layout.addWidget(self.banner)

        self.headline = QLabel("")
        self.headline.setStyleSheet("QLabel { font-size: 15px; font-weight: 600; }")
        self.layout.addWidget(self.headline)

        self.diagnostics = QLabel("")
        self.diagnostics.setWordWrap(True)
        self.layout.addWidget(self.diagnostics)

        splitter = QSplitter(Qt.Vertical)
        charts = QWidget()
        row = QHBoxLayout(charts)
        self.forge_canvas = ChartCanvas()
        self.forge_canvas.show_levels = False
        self.breadth_canvas = ChartCanvas()
        self.breadth_canvas.show_bands = False
        self.breadth_canvas.show_levels = False
        self.breadth_canvas.show_cloud = False
        row.addWidget(section("FORGE — turnover weighted", self.forge_canvas), 2)
        row.addWidget(section("FORGE-EW — equal weight (breadth)", self.breadth_canvas), 1)
        splitter.addWidget(charts)

        self.rotation = SortableTable(ROTATION_HEADERS)
        self.rotation.cellDoubleClicked.connect(self._sector_clicked)
        splitter.addWidget(
            section("Sector rotation — double-click a row to chart it", self.rotation)
        )
        splitter.setSizes([460, 300])
        self.layout.addWidget(splitter, 1)

        self.breadth_note = QLabel("")
        self.breadth_note.setWordWrap(True)
        self.layout.addWidget(self.breadth_note)
        self.repopulate()

    def set_banner(self, text: str) -> None:
        self.banner.set_banner(text)

    def repopulate(self) -> None:
        index_set = self.data.index_set
        forge = index_set.forge if index_set else self.data.composite
        forge_ew = index_set.forge_ew if index_set else None
        self.forge_canvas.set_series(_index_series(forge, FORGE))
        self.breadth_canvas.set_series(_index_series(forge_ew, FORGE_EW))

        if forge is None or not forge.known:
            self.headline.setText("FORGE — UNKNOWN")
            self.diagnostics.setText(
                "the market index could not be built; every RRS scoped to it is UNKNOWN "
                "rather than silently replaced"
            )
        else:
            diagnostics = forge.diagnostics
            level = float(forge.frame["close"].iloc[-1])
            previous = float(forge.frame["close"].iloc[-2]) if len(forge.frame) > 1 else None
            change = (level / previous - 1.0) * 100.0 if previous else None
            self.headline.setText(
                f"FORGE {level:,.2f}"
                + (f"  ({change:+.2f}% on the day)" if change is not None else "  (Δ UNKNOWN)")
            )
            top_weight = diagnostics.get("top_weight")
            entropy = diagnostics.get("weight_entropy")
            self.diagnostics.setText(
                f"{diagnostics.get('members', BLANK)} members · top weight "
                f"{f'{top_weight:.1%}' if isinstance(top_weight, float) else BLANK} · entropy "
                f"{f'{entropy:.3f}' if isinstance(entropy, float) else BLANK} · "
                f"{diagnostics.get('rebalances', 0)} rebalance(s). "
                "Weighted by ISK turnover, not raw units — raw units would make this "
                "index almost entirely Tritanium."
            )

        breadth = index_set.breadth() if index_set else None
        if breadth is None or breadth.empty:
            self.breadth_note.setText(
                "Breadth UNKNOWN — FORGE-EW could not be built from FORGE's membership."
            )
        else:
            latest = float(breadth.iloc[-1])
            reading = (
                "the average member is outrunning the turnover-weighted market: broad participation"
                if latest > 0
                else "a few large names are carrying the market"
            )
            self.breadth_note.setText(f"FORGE-EW − FORGE = {latest:+.2f} points — {reading}.")

        rows, payloads = [], []
        for row in self.data.rotation():
            rows.append(
                [
                    (row["ticker"], row["ticker"]),
                    (row.get("name") or row["ticker"], row.get("name") or row["ticker"]),
                    self._cell(row.get("rrs"), "+.2f"),
                    self._cell(row.get("change_1d"), "+.2f"),
                    self._cell(row.get("change_5d"), "+.2f"),
                    self._cell(row.get("change_20d"), "+.2f"),
                    self._cell(row.get("members"), ",.0f"),
                    (row.get("status", "UNKNOWN"), row.get("status", "UNKNOWN")),
                ]
            )
            payloads.append(row)
        self.rotation.set_rows(rows, payloads)

    @staticmethod
    def _cell(value, spec: str):
        if value is None or (isinstance(value, float) and not np.isfinite(value)):
            return (BLANK, None)
        return (format(float(value), spec), float(value))

    def _sector_clicked(self, view_row: int, _column: int) -> None:
        payload = self.rotation.payload(view_row)
        if not payload or payload.get("status") == "UNKNOWN":
            return
        index_set = self.data.index_set
        composite = index_set.sectors.get(payload["ticker"]) if index_set else None
        if composite is not None:
            self.forge_canvas.set_series(_index_series(composite, payload["ticker"]))
            self.headline.setText(
                f"{payload['ticker']} — {payload.get('name')} "
                f"({composite.diagnostics.get('members', BLANK)} members)"
            )
