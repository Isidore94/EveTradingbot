"""The tradeable setup, defined mechanically once (plan.md §13.2).

**Dips below anchored value with intact demand.** That is the entire thesis,
and the definition lives here so the screen, the backtest and the digest all
read the same rule — a backtest that tests a different setup than the screener
shows is worse than no backtest.

There is deliberately **no momentum or breakout-continuation branch**. EVE
supply is player-produced and elastic: a price spike is an invitation to
industrialists, who respond within days, so chasing a breakout means buying at
the top of a supply response (plan.md §6). This module cannot express such a
setup and must not grow one.

Gates are **tri-state**: a gate that cannot be evaluated is UNKNOWN, and
UNKNOWN always fails. "Could not measure" is never "measured and passed".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..bars import participation
from .atr import MIN_ATR_FRACTION, atr_series, measurable_fraction
from .avwap import segmented_band_series
from .rrs import rrs_series

__all__ = [
    "GATE_NAMES",
    "SetupParams",
    "anchor_grid",
    "evaluate_setups",
    "gate_summary",
]

GATE_NAMES = (
    "below_anchored_value",
    "relative_strength_intact",
    "participation_intact",
    "measurable",
)


@dataclass(frozen=True, slots=True)
class SetupParams:
    """The frozen setup parameters (plan.md §13.2). Config-supplied, not magic."""

    entry_band_sigma: float = 1.0
    min_rrs: float = -0.5
    participation_floor: float = 0.7
    min_bars: int = 120
    anchor_lookback_days: int = 90
    rrs_length: int = 20
    atr_length: int = 20
    participation_window: int = 20
    atr_winsor_k: float = 8.0
    atr_winsor_window: int = 60
    min_atr_fraction: float = MIN_ATR_FRACTION


def anchor_grid(frame: pd.DataFrame, *, step_days: int, anchor_dates=()) -> list[int]:
    """Bar indices of the anchors in force across `frame`.

    Confirmed calendar anchors are used where they exist. Where they do not,
    a synthetic grid every `step_days` fills in — still a set of *events*, not
    a sliding window, because the running-AVWAP sigma is path-dependent on
    where its accumulation began (see `segmented_band_series`).
    """
    if frame.empty:
        return []
    stamps = pd.to_datetime(frame["datetime"], utc=True).reset_index(drop=True)
    indices: set[int] = {0}
    for value in anchor_dates:
        target = pd.Timestamp(value)
        if target.tzinfo is None:
            target = target.tz_localize("UTC")
        position = int(stamps.searchsorted(target))
        if 0 <= position < len(stamps):
            indices.add(position)
    step = max(1, int(step_days))
    first = stamps.iloc[0]
    cursor = first + pd.Timedelta(days=step)
    last = stamps.iloc[-1]
    while cursor <= last:
        position = int(stamps.searchsorted(cursor))
        if 0 <= position < len(stamps):
            indices.add(position)
        cursor += pd.Timedelta(days=step)
    return sorted(indices)


def evaluate_setups(
    frame: pd.DataFrame,
    reference_frame: pd.DataFrame | None,
    params: SetupParams,
    *,
    anchor_dates=(),
) -> pd.DataFrame:
    """Evaluate every gate at every bar. One row per bar, aligned to `frame`.

    Columns: the four gate booleans, a matching `*_unknown` flag for each, the
    measurements behind them (`dip_sigma`, `rrs`, `participation`, `atr`), and
    `is_setup` — true only when every gate is TRUE, never when one is UNKNOWN.
    """
    if frame.empty:
        return pd.DataFrame()
    work = frame.reset_index(drop=True)
    size = len(work)

    bands = segmented_band_series(
        work, anchor_grid(work, step_days=params.anchor_lookback_days, anchor_dates=anchor_dates)
    )
    atr = atr_series(
        work,
        length=params.atr_length,
        winsor_k=params.atr_winsor_k,
        winsor_window=params.atr_winsor_window,
    )
    thrust = participation(work, window=params.participation_window)
    if reference_frame is not None and not reference_frame.empty:
        rrs = rrs_series(
            work,
            reference_frame,
            length=params.rrs_length,
            winsor_k=params.atr_winsor_k,
            winsor_window=params.atr_winsor_window,
        )
    else:
        rrs = pd.Series(np.nan, index=work.index)

    order_count = pd.to_numeric(work["order_count"], errors="coerce")
    bar_number = pd.Series(np.arange(1, size + 1), index=work.index)

    close_series = pd.to_numeric(work["close"], errors="coerce")
    # One epsilon, both denominators. A series that does not move makes the
    # AVWAP sigma float noise exactly as it does the ATR, and dip-σ divides by
    # sigma (§17 D-29).
    sigma_usable = pd.Series(
        measurable_fraction(
            bands["sigma"].to_numpy(dtype="float64"),
            close_series.to_numpy(dtype="float64"),
            min_fraction=params.min_atr_fraction,
        ),
        index=work.index,
    )

    dip = bands["dip_sigma"] if "dip_sigma" in bands else pd.Series(np.nan, index=work.index)
    dip_unknown = ~np.isfinite(dip) | ~sigma_usable
    below = (dip <= -abs(params.entry_band_sigma)).fillna(False) & ~dip_unknown

    rrs_unknown = ~np.isfinite(rrs)
    strength_ok = (rrs >= params.min_rrs).fillna(False) & ~rrs_unknown

    thrust_unknown = ~np.isfinite(thrust)
    thrust_ok = (thrust >= params.participation_floor).fillna(False) & ~thrust_unknown

    atr_usable = pd.Series(
        measurable_fraction(
            atr.to_numpy(dtype="float64"),
            close_series.to_numpy(dtype="float64"),
            min_fraction=params.min_atr_fraction,
        ),
        index=work.index,
    )
    atr_unknown = ~np.isfinite(atr) | (atr <= 0) | ~atr_usable
    measurable = ~atr_unknown & (order_count > 0).fillna(False) & (bar_number >= params.min_bars)

    result = pd.DataFrame(
        {
            "datetime": pd.to_datetime(work["datetime"], utc=True),
            "close": pd.to_numeric(work["close"], errors="coerce"),
            "vwap": bands["vwap"],
            "sigma": bands["sigma"],
            "dip_sigma": dip,
            "rrs": rrs,
            "participation": thrust,
            "atr": atr,
            "below_anchored_value": below,
            "below_anchored_value_unknown": dip_unknown,
            "relative_strength_intact": strength_ok,
            "relative_strength_intact_unknown": rrs_unknown,
            "participation_intact": thrust_ok,
            "participation_intact_unknown": thrust_unknown,
            "measurable": measurable,
            "measurable_unknown": atr_unknown,
        }
    )
    result["is_setup"] = (
        result["below_anchored_value"]
        & result["relative_strength_intact"]
        & result["participation_intact"]
        & result["measurable"]
    )
    return result


def gate_summary(evaluated: pd.DataFrame) -> dict:
    """Why setups were rejected. UNKNOWN counts are reported separately.

    A screen that says "nothing today" must be able to say *why* — a day where
    every gate failed on UNKNOWN is an outage, not an absence of opportunity.
    """
    if evaluated.empty:
        return {"bars": 0}
    summary: dict = {"bars": int(len(evaluated)), "setups": int(evaluated["is_setup"].sum())}
    for gate in GATE_NAMES:
        summary[f"{gate}_pass"] = int(evaluated[gate].sum())
        summary[f"{gate}_unknown"] = int(evaluated[f"{gate}_unknown"].sum())
    return summary
