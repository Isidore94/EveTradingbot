"""Real relative strength, benchmark-agnostic.

Ported from `bounce_bot_lib/legacy.py:real_relative_strength` (branch
`phase05-integration-blitz`, commit d60cbaf). The formula reduces
algebraically to `Δsym/ATR_sym − Δref/ATR_ref`, so **any** reference series
works — the equity version's SPY coupling lived in its callers, not here
(plan.md §0).

The one upstream behaviour deliberately **not** ported: the sector/industry
resolver's fallback to the literal string `"SPY"`. Here an unresolvable scope
returns UNKNOWN and the read drops out, never silently substituting the
market-wide composite for a cohort (plan.md §6). UNKNOWN always fails.

Daily bars only. There is no intraday timeframe in this system and none can be
manufactured from a 5-minute cache that is identical for everyone.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .atr import MIN_ATR_FRACTION, atr_last, measurable_fraction

__all__ = [
    "RelativeStrength",
    "cross_sectional_percentile",
    "real_relative_strength",
    "rrs_series",
]


@dataclass(frozen=True, slots=True)
class RelativeStrength:
    """One RRS read, with the reason it is unknown when it is."""

    rrs: float | None
    power_index: float | None
    scope: str
    members: int | None = None
    unknown_reason: str | None = None

    @property
    def known(self) -> bool:
        return self.rrs is not None

    def as_dict(self) -> dict:
        return {
            "rrs": self.rrs,
            "power_index": self.power_index,
            "scope": self.scope,
            "members": self.members,
            "unknown_reason": self.unknown_reason,
        }


def real_relative_strength(
    symbol_bars: pd.DataFrame,
    reference_bars: pd.DataFrame,
    *,
    length: int = 20,
    scope: str = "composite",
    members: int | None = None,
    winsor_k: float = 8.0,
    winsor_window: int = 60,
    min_atr_fraction: float = MIN_ATR_FRACTION,
) -> RelativeStrength:
    """`(Δsym − power_index × ATR_sym) / ATR_sym`, where power_index = Δref/ATR_ref.

    Both frames must carry at least `length + 2` bars and a positive ATR; any
    shortfall is UNKNOWN, not zero.
    """
    if symbol_bars is None or reference_bars is None:
        return RelativeStrength(None, None, scope, members, "missing series")
    minimum = length + 2
    if len(symbol_bars) < minimum or len(reference_bars) < minimum:
        return RelativeStrength(None, None, scope, members, f"needs {minimum} bars on both series")
    symbol_close = pd.to_numeric(symbol_bars["close"], errors="coerce").to_numpy(dtype="float64")
    reference_close = pd.to_numeric(reference_bars["close"], errors="coerce").to_numpy(
        dtype="float64"
    )
    symbol_move = symbol_close[-1] - symbol_close[-1 - length]
    reference_move = reference_close[-1] - reference_close[-1 - length]
    # ATR excludes the final bar, exactly as upstream (`bars[:-1]`).
    symbol_atr = atr_last(
        symbol_bars.iloc[:-1],
        length=length,
        winsor_k=winsor_k,
        winsor_window=winsor_window,
        min_fraction=min_atr_fraction,
    )
    # The reference is an index: `high == low == close` by construction, so its
    # ATR is a close-to-close proxy (§17 D-23) and a price-relative floor does
    # not describe it. Only the per-type denominator is guarded here.
    reference_atr = atr_last(
        reference_bars.iloc[:-1],
        length=length,
        winsor_k=winsor_k,
        winsor_window=winsor_window,
        min_fraction=0.0,
    )
    if not symbol_atr or not reference_atr:
        return RelativeStrength(
            None,
            None,
            scope,
            members,
            "ATR unavailable, zero, or too small a fraction of price to be a risk unit",
        )
    power_index = reference_move / reference_atr
    rrs = (symbol_move - (power_index * symbol_atr)) / symbol_atr
    if not np.isfinite(rrs):
        return RelativeStrength(None, None, scope, members, "non-finite result")
    return RelativeStrength(float(rrs), float(power_index), scope, members)


def rrs_series(
    symbol_frame: pd.DataFrame,
    reference_frame: pd.DataFrame,
    *,
    length: int = 20,
    winsor_k: float = 8.0,
    winsor_window: int = 60,
    min_atr_fraction: float = MIN_ATR_FRACTION,
) -> pd.Series:
    """RRS at every bar, vectorized, aligned to `symbol_frame`.

    Same algebra as `real_relative_strength`, including the upstream detail
    that the ATR excludes the bar being measured (`bars[:-1]` upstream becomes
    `.shift(1)` here). Bars where either ATR is unknown are NaN — UNKNOWN, not
    zero.
    """
    from .atr import atr_series

    if symbol_frame.empty or reference_frame is None or reference_frame.empty:
        return pd.Series(dtype="float64", index=symbol_frame.index)
    symbol = symbol_frame.set_index(pd.to_datetime(symbol_frame["datetime"], utc=True))
    reference = reference_frame.set_index(pd.to_datetime(reference_frame["datetime"], utc=True))

    symbol_atr = atr_series(
        symbol_frame, length=length, winsor_k=winsor_k, winsor_window=winsor_window
    )
    symbol_atr.index = symbol.index
    symbol_atr = symbol_atr.shift(1)
    reference_atr = atr_series(
        reference_frame, length=length, winsor_k=winsor_k, winsor_window=winsor_window
    )
    reference_atr.index = reference.index
    reference_atr = reference_atr.shift(1)

    symbol_close = pd.to_numeric(symbol["close"], errors="coerce")
    reference_close = pd.to_numeric(reference["close"], errors="coerce")
    symbol_move = symbol_close - symbol_close.shift(length)
    reference_move = reference_close - reference_close.shift(length)

    aligned_reference_move = reference_move.reindex(symbol.index)
    aligned_reference_atr = reference_atr.reindex(symbol.index)
    with np.errstate(invalid="ignore", divide="ignore"):
        power_index = aligned_reference_move / aligned_reference_atr
        values = (symbol_move - power_index * symbol_atr) / symbol_atr
    values = values.where(np.isfinite(values))
    # Same floor as the scalar path: a per-type ATR that is float noise is not
    # a denominator, and dividing by it is what produced RRS in the billions
    # (§17 D-29). The reference is an index and is deliberately not guarded.
    usable = measurable_fraction(
        symbol_atr.to_numpy(dtype="float64"),
        symbol_close.to_numpy(dtype="float64"),
        min_fraction=min_atr_fraction,
    )
    values = values.where(pd.Series(usable, index=symbol_atr.index))
    values.index = symbol_frame.index
    return values


def cross_sectional_percentile(values: dict[int, float | None]) -> dict[int, float | None]:
    """Percentile rank of each type's RRS within the cohort it was measured in.

    This is the engine `scripts/relative_strength.py` was built around and it
    needs no benchmark at all — the cohort *is* the benchmark. Types with an
    UNKNOWN read stay UNKNOWN rather than being ranked at the bottom, which
    would read as measured weakness.
    """
    known = {
        key: value for key, value in values.items() if value is not None and np.isfinite(value)
    }
    if not known:
        return dict.fromkeys(values)
    series = pd.Series(known)
    ranked = series.rank(pct=True)
    output: dict[int, float | None] = {key: None for key in values}
    for key, value in ranked.items():
        output[int(key)] = float(value)
    return output
