"""Moving averages and the EMA cloud — plan.md §19, Part 3's new indicators.

The setup DSL needs price-vs-average, cross, and cloud-state conditions, and
none of that existed: the v1 signal layer is AVWAP, ATR, levels and RRS. This
module adds exactly what the DSL names and nothing more.

Two rules carried from everything else here:

* **Daily H/L/C/V only.** There is no `open` (§4) and none is synthesized, so
  every average is a close average. That is also why the desk draws lines and
  areas and never candlesticks.
* **Warm-up is UNKNOWN, not zero.** An SMA(50) has no value at bar 10, and a
  condition asking about it must fail rather than compare against a
  half-formed number (§8's tri-state idiom).

Golden fixtures freeze these before the DSL consumes them (§11 D5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "CLOUD_SLOPE_BARS",
    "CloudState",
    "cloud_state",
    "cross_within",
    "ema",
    "ema_cloud",
    "moving_average",
    "sma",
]

# How many bars the cloud's slope is read over. One constant, so the last-bar
# read and the per-bar backtest read cannot disagree about what "rising" means.
CLOUD_SLOPE_BARS = 3


def _closes(frame: pd.DataFrame) -> pd.Series:
    if frame is None or frame.empty or "close" not in frame.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame["close"], errors="coerce")


def sma(frame: pd.DataFrame, length: int) -> pd.Series:
    """Simple moving average of `close`. NaN until `length` bars exist."""
    closes = _closes(frame)
    if closes.empty:
        return closes
    length = max(1, int(length))
    return closes.rolling(length, min_periods=length).mean().rename(f"sma{length}")


def ema(frame: pd.DataFrame, length: int) -> pd.Series:
    """Exponential moving average of `close`, seeded on the SMA of the first
    `length` bars.

    Seeding on the SMA rather than on bar 1 matters: `pandas.ewm(adjust=True)`
    produces a value at bar 1 that is simply the first close, which reads as a
    fully-formed EMA when it is nothing of the sort. A condition like "price
    above the rising 21 EMA" must not fire on bar 2.
    """
    closes = _closes(frame)
    length = max(1, int(length))
    if closes.empty:
        return closes
    values = closes.to_numpy(dtype="float64")
    out = np.full(values.shape, np.nan)
    if values.size < length:
        return pd.Series(out, index=closes.index, name=f"ema{length}")
    seed_window = values[:length]
    if not np.isfinite(seed_window).all():
        finite = np.isfinite(values)
        if finite.sum() < length:
            return pd.Series(out, index=closes.index, name=f"ema{length}")
    multiplier = 2.0 / (length + 1.0)
    running = float(np.nanmean(values[:length]))
    out[length - 1] = running
    for index in range(length, values.size):
        value = values[index]
        if not np.isfinite(value):
            out[index] = running
            continue
        running = (value - running) * multiplier + running
        out[index] = running
    return pd.Series(out, index=closes.index, name=f"ema{length}")


def moving_average(frame: pd.DataFrame, kind: str, length: int) -> pd.Series:
    """`sma` or `ema` by name. An unknown name is a loud error, not a default."""
    kind = str(kind).lower()
    if kind == "sma":
        return sma(frame, length)
    if kind == "ema":
        return ema(frame, length)
    raise ValueError(f"unknown moving-average kind {kind!r}; expected 'sma' or 'ema'")


_series_for = moving_average


def cross_within(
    frame: pd.DataFrame,
    *,
    fast_kind: str,
    fast_length: int,
    slow_kind: str,
    slow_length: int,
    bars: int,
    direction: str = "up",
) -> bool | None:
    """Did `fast` cross `slow` in the last `bars` bars, in `direction`?

    Returns None (UNKNOWN) when either average is still in warm-up over the
    window — a cross cannot be observed where a line does not yet exist.
    """
    fast = _series_for(frame, fast_kind, fast_length)
    slow = _series_for(frame, slow_kind, slow_length)
    if fast.empty or slow.empty:
        return None
    window = max(1, int(bars))
    # A cross at bar i needs bar i-1 too, so look one bar further back.
    tail_fast = fast.tail(window + 1).to_numpy(dtype="float64")
    tail_slow = slow.tail(window + 1).to_numpy(dtype="float64")
    if tail_fast.size < 2 or not np.isfinite(tail_fast).all() or not np.isfinite(tail_slow).all():
        return None
    above = tail_fast > tail_slow
    for index in range(1, above.size):
        if direction == "up" and not above[index - 1] and above[index]:
            return True
        if direction == "down" and above[index - 1] and not above[index]:
            return True
    return False


@dataclass(frozen=True, slots=True)
class CloudState:
    """Where price sits against a two-EMA ribbon, and which way it points."""

    position: str | None
    slope: str | None
    fast: float | None
    slow: float | None
    close: float | None

    @property
    def known(self) -> bool:
        return self.position is not None

    def as_dict(self) -> dict:
        return {
            "position": self.position,
            "slope": self.slope,
            "fast": self.fast,
            "slow": self.slow,
            "close": self.close,
        }


def ema_cloud(frame: pd.DataFrame, fast_length: int, slow_length: int) -> pd.DataFrame:
    """The two EMA lines plus the ribbon's upper/lower edge, per bar."""
    fast = ema(frame, fast_length)
    slow = ema(frame, slow_length)
    if fast.empty or slow.empty:
        return pd.DataFrame(columns=["fast", "slow", "upper", "lower"])
    return pd.DataFrame(
        {
            "fast": fast,
            "slow": slow,
            "upper": np.maximum(fast, slow),
            "lower": np.minimum(fast, slow),
        }
    )


def cloud_state(
    frame: pd.DataFrame, fast_length: int, slow_length: int, *, slope_bars: int = CLOUD_SLOPE_BARS
) -> CloudState:
    """Cloud read at the last bar: above / inside / below, rising / falling.

    `slope` is the *cloud's* direction, taken from the slow line over
    `slope_bars`, not price's. A ribbon can be falling while price sits above
    it, and the DSL wants to be able to say so.
    """
    cloud = ema_cloud(frame, fast_length, slow_length)
    closes = _closes(frame)
    if cloud.empty or closes.empty:
        return CloudState(None, None, None, None, None)
    last = cloud.iloc[-1]
    close = float(closes.iloc[-1]) if np.isfinite(closes.iloc[-1]) else None
    if close is None or not np.isfinite(last["upper"]) or not np.isfinite(last["lower"]):
        return CloudState(None, None, None, None, close)
    if close > last["upper"]:
        position = "above"
    elif close < last["lower"]:
        position = "below"
    else:
        position = "inside"
    slope = None
    slow_series = cloud["slow"].dropna()
    if len(slow_series) > slope_bars:
        earlier = float(slow_series.iloc[-1 - slope_bars])
        latest = float(slow_series.iloc[-1])
        if np.isfinite(earlier) and np.isfinite(latest) and earlier != 0:
            slope = "rising" if latest > earlier else "falling" if latest < earlier else "flat"
    return CloudState(
        position=position,
        slope=slope,
        fast=float(last["fast"]),
        slow=float(last["slow"]),
        close=close,
    )
