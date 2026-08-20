"""Anchored VWAP and sigma bands — **this repo's frozen formula**.

Ported from TradingBotV3's `calc_anchored_vwap_bands`
(`scripts/master_avwap_lib/legacy.py`, branch `phase05-integration-blitz`,
commit d60cbaf) with exactly two deliberate, documented changes made before
any consumer existed (plan.md §4):

1. **Typical price is `close` (= ESI `average`), not OHLC4.** ESI's `average`
   is a whole-day trade-derived mean — it *is* the day's typical price, and
   there is no `open` to build an OHLC4 from anyway.
2. **The row loop is vectorized.** Three cumulative sums replace it, with
   identical semantics including the `volume <= 0 -> skip` rule.

Everything else is preserved: sigma accumulates each bar's deviation from the
**running** AVWAP at that bar (not the distribution stdev around the final
AVWAP), weighted by that bar's volume. That variant runs tighter on trending
tapes and the operator's band instincts are calibrated to it.

**INVARIANT #1 — the sigma formula is frozen from the first golden fixture
forward.** Changing it requires regenerating every golden fixture and
re-validating every band consumer, together, with operator sign-off. It is not
a session-level convenience.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "BAND_NAMES",
    "AvwapBands",
    "anchored_vwap_bands",
    "anchored_vwap_history",
    "band_position",
    "classify_band",
]

BAND_NAMES = ("LOWER_3", "LOWER_2", "LOWER_1", "VWAP", "UPPER_1", "UPPER_2", "UPPER_3")


@dataclass(frozen=True, slots=True)
class AvwapBands:
    """The band ladder at the last bar of an anchored window."""

    vwap: float | None
    sigma: float | None
    bands: dict[str, float]
    bars: int
    anchor_index: int
    truncated: bool = False

    @property
    def known(self) -> bool:
        return self.vwap is not None and self.sigma is not None

    def as_dict(self) -> dict:
        return {
            "vwap": self.vwap,
            "sigma": self.sigma,
            "bands": dict(self.bands),
            "bars": self.bars,
            "anchor_index": self.anchor_index,
            "truncated": self.truncated,
        }


def _series(frame: pd.DataFrame, anchor_index: int) -> tuple[np.ndarray, np.ndarray]:
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64")
    volume = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(dtype="float64")
    close = close[anchor_index:]
    volume = volume[anchor_index:]
    # The reference loop skips zero/negative-volume bars entirely; a ghost day
    # is not a price (plan.md §4).
    mask = np.isfinite(close) & np.isfinite(volume) & (volume > 0)
    return close[mask], volume[mask]


def anchored_vwap_history(frame: pd.DataFrame, anchor_index: int = 0) -> pd.DataFrame:
    """Running AVWAP and sigma for every bar from the anchor forward.

    Columns: `datetime, vwap, sigma` plus the seven band levels. Bars skipped
    for zero volume do not appear — they contributed nothing to the reference
    accumulator either.
    """
    if frame.empty or anchor_index >= len(frame):
        return pd.DataFrame(columns=["datetime", "vwap", "sigma", *BAND_NAMES])
    close, volume = _series(frame, anchor_index)
    if close.size == 0:
        return pd.DataFrame(columns=["datetime", "vwap", "sigma", *BAND_NAMES])

    cum_volume = np.cumsum(volume)
    cum_value = np.cumsum(close * volume)
    vwap = cum_value / cum_volume
    deviation = close - vwap
    cum_sd = np.cumsum(deviation * deviation * volume)
    sigma = np.sqrt(cum_sd / cum_volume)

    stamps = frame["datetime"].to_numpy()[anchor_index:]
    close_all = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64")[
        anchor_index:
    ]
    volume_all = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(dtype="float64")[
        anchor_index:
    ]
    mask = np.isfinite(close_all) & np.isfinite(volume_all) & (volume_all > 0)

    history = pd.DataFrame(
        {
            "datetime": stamps[mask],
            "vwap": vwap,
            "sigma": sigma,
        }
    )
    for name in BAND_NAMES:
        history[name] = _band_level(name, history["vwap"], history["sigma"])
    return history


def _band_level(name: str, vwap, sigma):
    if name == "VWAP":
        return vwap
    direction = 1.0 if name.startswith("UPPER") else -1.0
    multiple = float(name.rsplit("_", 1)[1])
    return vwap + direction * multiple * sigma


def anchored_vwap_bands(
    frame: pd.DataFrame, anchor_index: int = 0, *, truncated: bool = False
) -> AvwapBands:
    """AVWAP, sigma and the 1/2/3-sigma ladder at the last bar.

    `truncated=True` marks an anchor older than the lake's history horizon —
    the band is computed from what exists and *says* it is truncated, rather
    than silently pretending the anchor was honoured (plan.md §9 R7).
    """
    if frame.empty or anchor_index >= len(frame) or anchor_index < 0:
        return AvwapBands(None, None, {}, 0, anchor_index, truncated)
    close, volume = _series(frame, anchor_index)
    if close.size == 0:
        return AvwapBands(None, None, {}, 0, anchor_index, truncated)

    cum_volume = float(volume.sum())
    cum_value = float((close * volume).sum())
    vwap_running = np.cumsum(close * volume) / np.cumsum(volume)
    deviation = close - vwap_running
    cum_sd = float((deviation * deviation * volume).sum())

    final_vwap = cum_value / cum_volume
    final_sigma = float(np.sqrt(cum_sd / cum_volume))
    bands = {name: float(_band_level(name, final_vwap, final_sigma)) for name in BAND_NAMES}
    return AvwapBands(
        vwap=float(final_vwap),
        sigma=final_sigma,
        bands=bands,
        bars=int(close.size),
        anchor_index=anchor_index,
        truncated=truncated,
    )


def band_position(price: float | None, bands: AvwapBands) -> float | None:
    """Where `price` sits on the sigma ladder: 0 at VWAP, -1 at LOWER_1, ..."""
    if price is None or not bands.known or not bands.sigma:
        return None
    return (float(price) - float(bands.vwap)) / float(bands.sigma)


def classify_band(price: float | None, bands: AvwapBands) -> str:
    """Name the zone the price is in. UNKNOWN when it cannot be measured.

    The tradeable read is a **dip below anchored value**, so the below-VWAP
    zones are the interesting ones. Nothing here rewards strength into a band:
    strength into a value zone is distribution risk (plan.md §6).
    """
    position = band_position(price, bands)
    if position is None:
        return "UNKNOWN"
    if position >= 3:
        return "ABOVE_UPPER_3"
    if position >= 2:
        return "UPPER_2_3"
    if position >= 1:
        return "UPPER_1_2"
    if position >= 0:
        return "VWAP_UPPER_1"
    if position >= -1:
        return "VWAP_LOWER_1"
    if position >= -2:
        return "LOWER_1_2"
    if position >= -3:
        return "LOWER_2_3"
    return "BELOW_LOWER_3"
