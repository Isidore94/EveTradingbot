"""ATR(20) on high/low/close — one implementation, not three.

Wilder's ATR, ported from `bounce_bot_lib/legacy.py:_wilder_atr_last` (branch
`phase05-integration-blitz`, commit d60cbaf). No `open` is needed and none
exists (plan.md §4).

The EVE-specific addition is **TR winsorization**. ESI's `highest`/`lowest`
can carry absurd off-market prints — a scam trade, a fat-fingered 10,000%
sale — and one such day must never own the risk unit that sizes every
position (plan.md §6, §9 R7). Each bar's true range is clamped at `k x` the
rolling median TR and the clamped bars are *flagged*, not hidden: a risk unit
computed off clamped data says so.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "atr_last",
    "atr_series",
    "risk_unit",
    "true_range",
    "winsorized_true_range",
]


def true_range(frame: pd.DataFrame) -> pd.Series:
    """Wilder true range. Needs high/low/close only."""
    if frame.empty:
        return pd.Series(dtype="float64")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    previous = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - previous).abs(), (low - previous).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def winsorized_true_range(
    frame: pd.DataFrame, *, k: float = 8.0, window: int = 60
) -> tuple[pd.Series, pd.Series]:
    """TR clamped at `k x` its own rolling median, plus the clamped-bar mask.

    Returns `(true_range, clamped)`. Bars with `order_count == 0` are ghost
    days and are excluded from the range entirely rather than clamped — a day
    with no trades has no true range to speak of (plan.md §4).
    """
    raw = true_range(frame)
    if raw.empty:
        return raw, pd.Series(dtype="bool")
    if "order_count" in frame.columns:
        ghosts = pd.to_numeric(frame["order_count"], errors="coerce").fillna(0) <= 0
        raw = raw.mask(ghosts)
    median = raw.rolling(window, min_periods=max(5, window // 4)).median()
    ceiling = median * float(k)
    clamped = raw.notna() & ceiling.notna() & (raw > ceiling)
    cleaned = raw.where(~clamped, ceiling)
    return cleaned, clamped.fillna(False)


def atr_series(
    frame: pd.DataFrame, *, length: int = 20, winsor_k: float = 8.0, winsor_window: int = 60
) -> pd.Series:
    """Wilder ATR over winsorized true range, as a series aligned to `frame`."""
    if frame.empty:
        return pd.Series(dtype="float64")
    ranges, _ = winsorized_true_range(frame, k=winsor_k, window=winsor_window)
    values = ranges.to_numpy(dtype="float64")
    out = np.full(values.shape, np.nan)
    running: float | None = None
    seeded = 0
    seed_sum = 0.0
    for index, value in enumerate(values):
        if not np.isfinite(value):
            out[index] = running if running is not None else np.nan
            continue
        if running is None:
            seed_sum += value
            seeded += 1
            if seeded == length:
                running = seed_sum / float(length)
                out[index] = running
            continue
        running = ((running * (length - 1)) + value) / float(length)
        out[index] = running
    return pd.Series(out, index=frame.index, name=f"atr{length}")


def atr_last(
    frame: pd.DataFrame, *, length: int = 20, winsor_k: float = 8.0, winsor_window: int = 60
) -> float | None:
    """The current ATR, or None. None means UNKNOWN and UNKNOWN always fails."""
    series = atr_series(frame, length=length, winsor_k=winsor_k, winsor_window=winsor_window)
    if series.empty:
        return None
    value = series.iloc[-1]
    if not np.isfinite(value) or value <= 0:
        return None
    return float(value)


def risk_unit(
    frame: pd.DataFrame, *, atr_multiple: float = 1.5, length: int = 20, **kwargs
) -> float | None:
    """The ISK-per-unit stop distance that one R is measured in.

    Long only — there is no shorting in EVE and none is modelled (plan.md §6).
    """
    atr = atr_last(frame, length=length, **kwargs)
    if atr is None:
        return None
    return atr * float(atr_multiple)
