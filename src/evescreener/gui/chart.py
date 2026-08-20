"""The chart window (plan.md §19 Part 2 page 2).

**One window that re-points, never a stack.** Clicking a name anywhere on the
desk aims this window at that type. A pile of chart windows is how a desk
becomes unusable, and it is also how you end up comparing two names against
two different anchor sets without noticing.

**Range candles.** The EVE bar contract is
`["datetime","high","low","close","volume","order_count"]` with `close ← ESI
average`; there is no `open` and none is ever synthesized (§4).

The obvious rescue — EVE trades 24/7, so yesterday's close *is* today's open,
no session gap — is right about the market and wrong about this data, and it
was measured rather than argued. `close` is not a last trade, it is the day's
**mean transaction price**, and yesterday's mean lands *outside* today's
measured `[low, high]` on **55.7%** of all 4,034,697 bars — **69.0%** of
tier-OK bars and **58.1%** of watchlist bars. A prev-close body would hang off
the end of its own wick on the majority of bars: not merely dishonest, visibly
broken. See plan.md §17 D-30.

So the body is the part that *is* measured. Each candle is a filled body
spanning the day's **low→high**, crossed by a notch at the **average**, and
coloured against the previous average. Body height is the day's true range,
notch height is where the volume actually transacted inside it, and colour is
a comparison between two real numbers. Nothing is invented, and a fat body
reads at a glance where a one-pixel line did not.

What it deliberately cannot show is intraday direction. ESI records no
sequence within a day — no open, no last, no ticks — so no chart drawn from
this lake can tell you whether the day rose or fell inside itself, at any
price. The notch is the honest replacement: high in the range means the
trading happened high in the range.

What is drawn, all of it from the frozen or already-computed layers:

* anchored VWAP and its ±1/2/3σ ladder (the frozen formula, not a re-derivation),
* configurable SMA/EMA overlays,
* the EMA cloud as a **shaded ribbon** between two EMAs,
* high-volume levels from `signals/levels.py` — those were already computed and
  never drawn; this draws them,
* pivots and round-ISK levels,
* a volume subpane and a participation subpane,
* setup markers, and open paper positions with their entry, stop and target.

Stale books are stamped on the chart rather than hidden, and a name below the
trading floor renders with its badge.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..signals.atr import atr_last
from ..signals.avwap import anchored_vwap_bands, zone_from_position
from ..signals.levels import build_level_store
from ..signals.moving import ema, ema_cloud, sma
from ..signals.setup import anchor_grid, evaluate_setups
from .widgets import BLANK, format_isk

__all__ = ["ChartPanel", "ChartSeries", "bar_colours", "build_series"]

PRICE_COLOUR = QColor(220, 224, 232)
ENVELOPE_COLOUR = QColor(90, 100, 120, 70)
UP_COLOUR = QColor(56, 200, 120)
DOWN_COLOUR = QColor(232, 88, 88)
FLAT_COLOUR = QColor(146, 152, 164)
AVERAGE_COLOUR = QColor(246, 248, 252)
VWAP_COLOUR = QColor(120, 190, 255)
SIGMA_COLOURS = (
    QColor(120, 190, 255, 150),
    QColor(120, 190, 255, 100),
    QColor(120, 190, 255, 60),
)
MA_COLOURS = (
    QColor(255, 190, 90),
    QColor(150, 220, 150),
    QColor(220, 140, 220),
    QColor(140, 200, 220),
)
CLOUD_COLOUR = QColor(120, 200, 160, 60)
HV_COLOUR = QColor(90, 200, 140, 190)
PIVOT_COLOUR = QColor(230, 140, 120, 170)
ROUND_COLOUR = QColor(160, 160, 200, 130)
SETUP_COLOUR = QColor(255, 215, 0)
ENTRY_COLOUR = QColor(120, 220, 255)
STOP_COLOUR = QColor(240, 110, 110)
TARGET_COLOUR = QColor(120, 230, 150)
GRID_COLOUR = QColor(70, 74, 82)
TEXT_COLOUR = QColor(190, 195, 205)
BACKGROUND = QColor(24, 26, 30)

# Pixels per bar, and what survives at each width. Below CANDLE_MIN_SLOT the
# bodies merge into a solid block that would read as more data than it is.
CANDLE_MIN_SLOT = 1.2  # under this: shaded envelope and a close line
CANDLE_BODY_SLOT = 2.5  # under this: a one-pixel coloured range, no body
CANDLE_NOTCH_SLOT = 4.0  # under this: no average notch, it would not resolve
DEFAULT_VISIBLE_BARS = 120


def bar_colours(close: np.ndarray) -> list[QColor]:
    """Colour each bar against the **previous close**, never against an open.

    Both sides of that comparison are measured numbers, which is the whole
    reason it is allowed to carry colour. A bar with nothing behind it — the
    first one, or one following a gap in the series — is FLAT: it has no
    direction to report, and guessing one would be the same fabrication as
    drawing a body.
    """
    colours: list[QColor] = []
    previous = np.nan
    for value in close:
        colour = FLAT_COLOUR
        if np.isfinite(value) and np.isfinite(previous):
            if value > previous:
                colour = UP_COLOUR
            elif value < previous:
                colour = DOWN_COLOUR
            previous = value
        elif np.isfinite(value):
            previous = value
        colours.append(colour)
    return colours


@dataclass(slots=True)
class ChartSeries:
    """Everything the painter needs, computed once, off the Qt thread's path."""

    type_id: int
    type_name: str
    tier: str | None = None
    watched: bool = False
    stamps: list = field(default_factory=list)
    close: np.ndarray | None = None
    high: np.ndarray | None = None
    low: np.ndarray | None = None
    volume: np.ndarray | None = None
    participation: np.ndarray | None = None
    vwap: np.ndarray | None = None
    sigma: np.ndarray | None = None
    moving: dict = field(default_factory=dict)
    cloud: tuple | None = None
    levels: list = field(default_factory=list)
    setups: np.ndarray | None = None
    positions: list = field(default_factory=list)
    atr: float | None = None
    band_zone: str = "UNKNOWN"
    dip_sigma: float | None = None
    book_stamp: str = ""
    note: str = ""

    @property
    def known(self) -> bool:
        return self.close is not None and len(self.close) > 1

    @property
    def ranged(self) -> bool:
        """True when these bars carry an intraday range worth a body.

        A composite index does not. `signals/composite.py` builds it with
        `high == low == close` by construction — an index level is one number
        per day and has no intraday range to report — so a candle drawn from
        one is a row of zero-height dashes with a notch floating in the middle
        of each. Such a series is a *level* series and is drawn as a line.
        """
        return self.high is not None and self.low is not None and bool(np.any(self.high > self.low))

    def tail(self, count: int) -> ChartSeries:
        """The last `count` bars, with every parallel array sliced together.

        Zooming is a view, not a reload: the series is built once and the
        canvas asks for a window of it. Slicing every array in one place is
        what keeps an overlay from drifting a bar out of step with price.
        """
        if self.close is None or count <= 0 or count >= len(self.close):
            return self
        cut = slice(-count, None)

        def sliced(array):
            return None if array is None else array[cut]

        return replace(
            self,
            stamps=list(self.stamps[cut]),
            close=sliced(self.close),
            high=sliced(self.high),
            low=sliced(self.low),
            volume=sliced(self.volume),
            participation=sliced(self.participation),
            vwap=sliced(self.vwap),
            sigma=sliced(self.sigma),
            setups=sliced(self.setups),
            moving={name: values[cut] for name, values in self.moving.items()},
            cloud=None if self.cloud is None else (self.cloud[0][cut], self.cloud[1][cut]),
        )


def build_series(data, type_id: int, *, positions=None) -> ChartSeries:
    """Assemble one type's chart from the desk's already-loaded data.

    Every layer here is read from the shared signal modules — the chart never
    re-derives a formula it could import, because a chart that computes its
    own AVWAP is a chart that can disagree with the screen.
    """
    config = data.config
    frame = data.frame_for(type_id)
    series = ChartSeries(
        type_id=int(type_id),
        type_name=data.type_name(type_id),
        tier=data.tier(type_id),
        watched=int(type_id) in data.watch_ids,
        positions=list(positions or []),
    )
    if frame.empty:
        series.note = (
            f"no bars in the lake for this type — run `ingest-history --type-id {int(type_id)}`"
        )
        return series
    limit = int(config.gui.chart_bars)
    if len(frame) > limit:
        frame = frame.tail(limit).reset_index(drop=True)

    series.stamps = list(pd.to_datetime(frame["datetime"], utc=True))
    series.close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64")
    series.high = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64")
    series.low = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64")
    series.volume = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(dtype="float64")

    from ..screen import setup_params

    params = setup_params(config)
    evaluated = evaluate_setups(
        frame, getattr(data.composite, "frame", None), params, anchor_dates=data.anchor_dates
    )
    if not evaluated.empty:
        series.vwap = evaluated["vwap"].to_numpy(dtype="float64")
        series.sigma = evaluated["sigma"].to_numpy(dtype="float64")
        series.participation = evaluated["participation"].to_numpy(dtype="float64")
        series.setups = evaluated["is_setup"].to_numpy()
        last = evaluated.iloc[-1]
        series.dip_sigma = float(last["dip_sigma"]) if np.isfinite(last["dip_sigma"]) else None
        series.band_zone = zone_from_position(series.dip_sigma)

    for length in config.gui.sma_lengths:
        series.moving[f"SMA{length}"] = sma(frame, length).to_numpy(dtype="float64")
    for length in config.gui.ema_lengths:
        series.moving[f"EMA{length}"] = ema(frame, length).to_numpy(dtype="float64")

    cloud = ema_cloud(frame, config.gui.cloud_fast, config.gui.cloud_slow)
    if not cloud.empty:
        series.cloud = (
            cloud["lower"].to_numpy(dtype="float64"),
            cloud["upper"].to_numpy(dtype="float64"),
        )

    series.atr = atr_last(
        frame,
        length=params.atr_length,
        winsor_k=params.atr_winsor_k,
        winsor_window=params.atr_winsor_window,
    )
    if series.atr:
        store = build_level_store(
            frame,
            atr20=series.atr,
            round_steps=tuple(config.signals.round_number_levels_isk),
            anchor_dates=data.anchor_dates,
        )
        wanted = set()
        if config.gui.show_hv_levels:
            wanted.add("hv_horizontal")
        if config.gui.show_pivots:
            wanted.add("pivot")
        if config.gui.show_round_levels:
            wanted.add("round_isk")
        series.levels = [
            level for level in store.get("levels", []) if str(level.get("kind")) in wanted
        ]

    if not data.anchor_dates:
        series.note = "no confirmed anchor — bands run from a synthetic anchor grid"
    anchors = anchor_grid(
        frame, step_days=params.anchor_lookback_days, anchor_dates=data.anchor_dates
    )
    bands = anchored_vwap_bands(frame, anchors[-1] if anchors else 0)
    if not bands.known:
        series.note = "anchored VWAP UNKNOWN for this series"

    age = data.book_age_minutes
    if age is None:
        series.book_stamp = "book: UNKNOWN — no sweep on disk"
    else:
        series.book_stamp = f"book: {age:.0f} min old" + (" — STALE" if data.book_is_stale else "")
    return series


class ChartCanvas(QWidget):
    """The painter. HLC bars — range and close only, because there is no open."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.series: ChartSeries | None = None
        self.visible = DEFAULT_VISIBLE_BARS
        self.show_bands = True
        self.show_moving = True
        self.show_cloud = True
        self.show_levels = True
        self.setAutoFillBackground(True)

    def set_series(self, series: ChartSeries | None) -> None:
        self.series = series
        self.update()

    # -- geometry ----------------------------------------------------------
    def _panes(self) -> tuple[QRectF, QRectF, QRectF]:
        rect = QRectF(self.rect()).adjusted(58, 12, -12, -22)
        price_height = rect.height() * 0.66
        sub_height = (rect.height() - price_height) / 2.0
        price = QRectF(rect.x(), rect.y(), rect.width(), price_height)
        volume = QRectF(rect.x(), price.bottom() + 4, rect.width(), sub_height - 4)
        thrust = QRectF(rect.x(), volume.bottom() + 4, rect.width(), sub_height - 4)
        return price, volume, thrust

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), BACKGROUND)
        series = self.series
        if series is None or not series.known:
            painter.setPen(QPen(TEXT_COLOUR))
            message = (series.note if series else "") or "nothing charted yet"
            painter.drawText(QRectF(self.rect()), Qt.AlignCenter, message)
            painter.end()
            return
        series = series.tail(self.visible)
        price_pane, volume_pane, thrust_pane = self._panes()
        low, high = self._price_range(series)
        self._draw_grid(painter, price_pane, low, high)
        if self.show_bands:
            self._draw_bands(painter, price_pane, series, low, high)
        if self.show_cloud and series.cloud is not None:
            self._draw_cloud(painter, price_pane, series, low, high)
        if self.show_levels:
            self._draw_levels(painter, price_pane, series, low, high)
        self._draw_candles(painter, price_pane, series, low, high)
        if self.show_moving:
            self._draw_moving(painter, price_pane, series, low, high)
        self._draw_positions(painter, price_pane, series, low, high)
        self._draw_setups(painter, price_pane, series, low, high)
        self._draw_subpane(painter, volume_pane, series.volume, QColor(90, 120, 180), "volume")
        self._draw_subpane(
            painter, thrust_pane, series.participation, QColor(180, 140, 90), "participation"
        )
        painter.end()

    # -- helpers -----------------------------------------------------------
    def _price_range(self, series: ChartSeries) -> tuple[float, float]:
        stack = [series.low, series.high]
        if self.show_bands and series.vwap is not None and series.sigma is not None:
            stack.extend([series.vwap - 3 * series.sigma, series.vwap + 3 * series.sigma])
        values = np.concatenate([array[np.isfinite(array)] for array in stack if array is not None])
        if values.size == 0:
            return 0.0, 1.0
        low, high = float(values.min()), float(values.max())
        if high <= low:
            high = low + 1.0
        pad = (high - low) * 0.06
        return low - pad, high + pad

    def _x(self, pane: QRectF, index: int, count: int) -> float:
        if count <= 1:
            return pane.x()
        return pane.x() + pane.width() * index / (count - 1)

    def _y(self, pane: QRectF, value: float, low: float, high: float) -> float:
        return pane.bottom() - pane.height() * (value - low) / (high - low)

    def _polyline(self, pane, values, low, high) -> QPolygonF:
        points = QPolygonF()
        count = len(values)
        for index, value in enumerate(values):
            if not np.isfinite(value):
                continue
            points.append(QPointF(self._x(pane, index, count), self._y(pane, value, low, high)))
        return points

    def _draw_grid(self, painter, pane, low, high) -> None:
        painter.setPen(QPen(GRID_COLOUR, 1, Qt.DotLine))
        font = QFont(painter.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        painter.setFont(font)
        for step in range(5):
            value = low + (high - low) * step / 4
            y = self._y(pane, value, low, high)
            painter.drawLine(QPointF(pane.x(), y), QPointF(pane.right(), y))
            painter.setPen(QPen(TEXT_COLOUR))
            painter.drawText(QPointF(4, y + 4), format_isk(value))
            painter.setPen(QPen(GRID_COLOUR, 1, Qt.DotLine))

    def _draw_bands(self, painter, pane, series, low, high) -> None:
        if series.vwap is None or series.sigma is None:
            return
        for multiple, colour in zip((1, 2, 3), SIGMA_COLOURS, strict=False):
            for sign in (1, -1):
                values = series.vwap + sign * multiple * series.sigma
                painter.setPen(QPen(colour, 1, Qt.DashLine))
                painter.drawPolyline(self._polyline(pane, values, low, high))
        painter.setPen(QPen(VWAP_COLOUR, 1.6))
        painter.drawPolyline(self._polyline(pane, series.vwap, low, high))

    def _draw_cloud(self, painter, pane, series, low, high) -> None:
        lower, upper = series.cloud
        path = QPainterPath()
        started = False
        count = len(lower)
        for index in range(count):
            if not (np.isfinite(lower[index]) and np.isfinite(upper[index])):
                continue
            point = QPointF(self._x(pane, index, count), self._y(pane, upper[index], low, high))
            if not started:
                path.moveTo(point)
                started = True
            else:
                path.lineTo(point)
        if not started:
            return
        for index in range(count - 1, -1, -1):
            if not (np.isfinite(lower[index]) and np.isfinite(upper[index])):
                continue
            path.lineTo(
                QPointF(self._x(pane, index, count), self._y(pane, lower[index], low, high))
            )
        path.closeSubpath()
        painter.fillPath(path, QBrush(CLOUD_COLOUR))

    def _draw_levels(self, painter, pane, series, low, high) -> None:
        colours = {
            "hv_horizontal": HV_COLOUR,
            "pivot": PIVOT_COLOUR,
            "round_isk": ROUND_COLOUR,
        }
        for level in series.levels:
            price = level.get("price")
            if price is None or not (low <= float(price) <= high):
                continue
            colour = colours.get(str(level.get("kind")), ROUND_COLOUR)
            y = self._y(pane, float(price), low, high)
            painter.setPen(QPen(colour, 1.2, Qt.SolidLine))
            painter.drawLine(QPointF(pane.x(), y), QPointF(pane.right(), y))

    def _draw_envelope(self, painter, pane, series, low, high) -> None:
        """High/low as a shaded band — the dense fallback when bars cannot resolve."""
        path = QPainterPath()
        count = len(series.high)
        started = False
        for index in range(count):
            if not np.isfinite(series.high[index]):
                continue
            point = QPointF(
                self._x(pane, index, count), self._y(pane, series.high[index], low, high)
            )
            path.moveTo(point) if not started else path.lineTo(point)
            started = True
        if not started:
            return
        for index in range(count - 1, -1, -1):
            if not np.isfinite(series.low[index]):
                continue
            path.lineTo(
                QPointF(self._x(pane, index, count), self._y(pane, series.low[index], low, high))
            )
        path.closeSubpath()
        painter.fillPath(path, QBrush(ENVELOPE_COLOUR))

    def _draw_price(self, painter, pane, series, low, high) -> None:
        painter.setPen(QPen(PRICE_COLOUR, 1.8))
        painter.drawPolyline(self._polyline(pane, series.close, low, high))

    def _draw_candles(self, painter, pane, series, low, high) -> None:
        """One candle per day: body = the measured range, notch = the average.

        The body is the day's low→high because that is the interval the data
        actually establishes. There is no `open` to anchor a conventional body
        to, and the nearest substitute — yesterday's close — is outside today's
        measured range on 55.7% of the lake, so a conventional body would hang
        off its own wick more often than not (plan.md §17 D-30).

        Colour compares this average with the previous one. The notch says
        where inside the range the trading actually happened: near the top is
        a day that transacted high in its range, which is the closest thing to
        intraday direction that a daily average can honestly support.

        Density decides how much survives — body and notch, then bare range,
        then the shaded envelope with a close line — rather than smearing into
        a mass that would read as more data than it is.
        """
        if not series.ranged:
            # A level series (an index): no range, so no body. Drawing candles
            # here produced a field of floating notches and nothing else.
            self._draw_price(painter, pane, series, low, high)
            return
        count = len(series.close)
        slot = pane.width() / max(count, 1)
        if slot < CANDLE_MIN_SLOT:
            self._draw_envelope(painter, pane, series, low, high)
            self._draw_price(painter, pane, series, low, high)
            return
        colours = bar_colours(series.close)
        solid = slot >= CANDLE_BODY_SLOT
        notched = slot >= CANDLE_NOTCH_SLOT
        width = max(1.0, min(slot * 0.72, 16.0))
        for index in range(count):
            top, bottom = series.high[index], series.low[index]
            close = series.close[index]
            x = self._x(pane, index, count)
            if np.isfinite(top) and np.isfinite(bottom):
                y_top = self._y(pane, top, low, high)
                y_bottom = self._y(pane, bottom, low, high)
                if solid:
                    painter.fillRect(
                        QRectF(x - width / 2, y_top, width, max(y_bottom - y_top, 1.0)),
                        QBrush(colours[index]),
                    )
                else:
                    painter.setPen(QPen(colours[index], 1.0))
                    painter.drawLine(QPointF(x, y_top), QPointF(x, y_bottom))
            if notched and np.isfinite(close):
                y = self._y(pane, close, low, high)
                painter.setPen(QPen(AVERAGE_COLOUR, 1.2))
                painter.drawLine(QPointF(x - width / 2, y), QPointF(x + width / 2, y))

    def _draw_moving(self, painter, pane, series, low, high) -> None:
        for index, (name, values) in enumerate(series.moving.items()):
            colour = MA_COLOURS[index % len(MA_COLOURS)]
            painter.setPen(QPen(colour, 1.2))
            painter.drawPolyline(self._polyline(pane, values, low, high))
            painter.drawText(QPointF(pane.right() - 46, pane.y() + 14 + index * 13), name)

    def _draw_setups(self, painter, pane, series, low, high) -> None:
        if series.setups is None:
            return
        count = len(series.setups)
        painter.setBrush(QBrush(SETUP_COLOUR))
        painter.setPen(QPen(SETUP_COLOUR))
        for index, fired in enumerate(series.setups):
            if not bool(fired) or not np.isfinite(series.low[index]):
                continue
            x = self._x(pane, index, count)
            y = self._y(pane, series.low[index], low, high) + 8
            painter.drawEllipse(QPointF(x, y), 3.0, 3.0)
        painter.setBrush(Qt.NoBrush)

    def _draw_positions(self, painter, pane, series, low, high) -> None:
        """Open paper positions, so an entry is visible on the chart itself."""
        for position in series.positions:
            for key, colour, label in (
                ("entry_effective_price", ENTRY_COLOUR, "entry"),
                ("stop_price", STOP_COLOUR, "stop"),
                ("target_price", TARGET_COLOUR, "target"),
            ):
                value = position.get(key)
                if value is None or not (low <= float(value) <= high):
                    continue
                y = self._y(pane, float(value), low, high)
                painter.setPen(QPen(colour, 1.4, Qt.DashDotLine))
                painter.drawLine(QPointF(pane.x(), y), QPointF(pane.right(), y))
                painter.drawText(QPointF(pane.x() + 4, y - 3), label)

    def _draw_subpane(self, painter, pane, values, colour, label) -> None:
        painter.setPen(QPen(GRID_COLOUR))
        painter.drawRect(pane)
        painter.setPen(QPen(TEXT_COLOUR))
        painter.drawText(QPointF(pane.x() + 4, pane.y() + 12), label)
        if values is None:
            painter.drawText(QPointF(pane.x() + 70, pane.y() + 12), "UNKNOWN")
            return
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            painter.drawText(QPointF(pane.x() + 70, pane.y() + 12), "UNKNOWN")
            return
        top = float(finite.max()) or 1.0
        count = len(values)
        painter.setPen(QPen(colour, 1.0))
        for index, value in enumerate(values):
            if not np.isfinite(value):
                continue
            x = self._x(pane, index, count)
            height = pane.height() * (value / top) if top else 0.0
            painter.drawLine(QPointF(x, pane.bottom()), QPointF(x, pane.bottom() - height))


class ChartPanel(QWidget):
    """The chart plus its header and overlay toggles. One instance, re-pointed."""

    charted = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.series: ChartSeries | None = None
        layout = QVBoxLayout(self)
        self.title = QLabel("nothing charted yet")
        self.title.setStyleSheet("QLabel { font-size: 15px; font-weight: 600; }")
        self.subtitle = QLabel("")
        self.stamp = QLabel("")
        self.stamp.setStyleSheet("QLabel { color: #c48a20; }")
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        layout.addWidget(self.stamp)

        toggles = QHBoxLayout()
        self.canvas = ChartCanvas()
        self._boxes = {}
        for key, text in (
            ("show_bands", "anchored VWAP ±σ"),
            ("show_moving", "SMA/EMA"),
            ("show_cloud", "EMA cloud"),
            ("show_levels", "levels"),
        ):
            box = QCheckBox(text)
            box.setChecked(True)
            box.toggled.connect(lambda state, name=key: self._toggle(name, state))
            toggles.addWidget(box)
            self._boxes[key] = box
        toggles.addStretch(1)
        toggles.addWidget(QLabel("bars"))
        self.zoom = QComboBox()
        for label, value in (("60", 60), ("120", 120), ("250", 250), ("all", 0)):
            self.zoom.addItem(label, value)
        self.zoom.setCurrentIndex(1)
        self.zoom.currentIndexChanged.connect(self._zoom)
        toggles.addWidget(self.zoom)
        layout.addLayout(toggles)
        layout.addWidget(self.canvas, 1)

    def _zoom(self) -> None:
        """Fewer bars, wider candles. A 400-bar window cannot resolve a body."""
        self.canvas.visible = int(self.zoom.currentData())
        self.canvas.update()

    def _toggle(self, name: str, state: bool) -> None:
        setattr(self.canvas, name, bool(state))
        self.canvas.update()

    def show_series(self, series: ChartSeries) -> None:
        """Re-point at a new type. This never opens a second window."""
        self.series = series
        badges = []
        if series.watched:
            badges.append("WATCHLIST")
        if series.tier == "THIN":
            badges.append("THIN")
        elif series.tier == "BELOW":
            badges.append("BELOW FLOOR")
        suffix = f" — {' · '.join(badges)}" if badges else ""
        self.title.setText(f"{series.type_name} (type {series.type_id}){suffix}")
        close = format_isk(series.close[-1]) if series.known else BLANK
        dip = f"{series.dip_sigma:+.2f}σ" if series.dip_sigma is not None else "σ UNKNOWN"
        self.subtitle.setText(
            f"close {close} · zone {series.band_zone} · {dip} · "
            f"ATR {format_isk(series.atr)} · {len(series.levels)} level(s) drawn"
        )
        self.stamp.setText(series.book_stamp + (f" · {series.note}" if series.note else ""))
        self.canvas.set_series(series)
        self.charted.emit(series.type_id)
