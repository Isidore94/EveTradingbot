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
    "segmented_band_series",
    "classify_band",
    "zone_from_position",
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


def segmented_band_series(frame: pd.DataFrame, anchor_indices: list[int]) -> pd.DataFrame:
    """Running AVWAP and sigma where each bar reads from its own live anchor.

    **Anchors are events, not sliding windows.** Each anchor opens a segment;
    every bar inside it uses the running AVWAP accumulated since that anchor,
    exactly as `anchored_vwap_history` does — so this is the same frozen
    formula applied piecewise, not a second formula.

    A rolling-window sigma would be a *different* statistic (the running
    deviation is path-dependent on where the accumulation started), and there
    is no anchor event in EVE that corresponds to "90 bars ago, sliding". When
    no confirmed patch anchor is available the caller supplies a grid of
    synthetic anchor dates, which is still a set of events.

    Returns a frame aligned to `frame` with `vwap`, `sigma`, `dip_sigma` and
    `anchor_index`; bars before the first anchor are NaN.
    """
    columns = ["vwap", "sigma", "dip_sigma", "anchor_index"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    anchors = sorted({int(value) for value in anchor_indices if 0 <= int(value) < len(frame)})
    if not anchors:
        anchors = [0]
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64")
    volume = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(dtype="float64")
    size = len(frame)
    vwap_out = np.full(size, np.nan)
    sigma_out = np.full(size, np.nan)
    anchor_out = np.full(size, -1, dtype="int64")

    bounds = [*anchors, size]
    for position, start in enumerate(anchors):
        stop = bounds[position + 1]
        segment_close = close[start:stop]
        segment_volume = volume[start:stop]
        valid = np.isfinite(segment_close) & np.isfinite(segment_volume) & (segment_volume > 0)
        if not valid.any():
            continue
        cum_volume = np.cumsum(np.where(valid, segment_volume, 0.0))
        cum_value = np.cumsum(np.where(valid, segment_close * segment_volume, 0.0))
        with np.errstate(invalid="ignore", divide="ignore"):
            running = np.where(cum_volume > 0, cum_value / cum_volume, np.nan)
        deviation = np.where(valid, segment_close - running, 0.0)
        cum_sd = np.cumsum(np.where(valid, deviation * deviation * segment_volume, 0.0))
        with np.errstate(invalid="ignore", divide="ignore"):
            sigma = np.sqrt(np.where(cum_volume > 0, cum_sd / cum_volume, np.nan))
        vwap_out[start:stop] = running
        sigma_out[start:stop] = sigma
        anchor_out[start:stop] = start

    with np.errstate(invalid="ignore", divide="ignore"):
        dip = np.where(sigma_out > 0, (close - vwap_out) / sigma_out, np.nan)
    return pd.DataFrame(
        {
            "vwap": vwap_out,
            "sigma": sigma_out,
            "dip_sigma": dip,
            "anchor_index": anchor_out,
        },
        index=frame.index,
    )


def band_position(price: float | None, bands: AvwapBands) -> float | None:
    """Where `price` sits on the sigma ladder: 0 at VWAP, -1 at LOWER_1, ..."""
    if price is None or not bands.known or not bands.sigma:
        return None
    return (float(price) - float(bands.vwap)) / float(bands.sigma)


def zone_from_position(position: float | None) -> str:
    """Name the zone from a sigma position. The one threshold ladder.

    `dip_sigma` in the per-bar series IS this position, so the scanner, the
    charts and the backtest all classify through this function rather than
    each carrying its own copy of the thresholds. A second copy is a second
    thing to drift.
    """
    if position is None or not np.isfinite(position):
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


def classify_band(price: float | None, bands: AvwapBands) -> str:
    """Name the zone the price is in. UNKNOWN when it cannot be measured.

    The tradeable read is a **dip below anchored value**, so the below-VWAP
    zones are the interesting ones. Nothing here rewards strength into a band:
    strength into a value zone is distribution risk (plan.md §6).
    """
    return zone_from_position(band_position(price, bands))
