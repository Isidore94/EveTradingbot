"""Golden-fixture generator. Run deliberately, review, then freeze.

Fixtures are the gate on every detector and scoring change from here forward
(plan.md §8): a change regenerates fixtures FIRST, the diff is reviewed, and
only then does the code that consumes them move. Nothing in the test suite
calls this module.

Usage: `uv run python tests/generate_golden.py`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def synthetic_frame(seed: int = 20260818, bars: int = 180) -> pd.DataFrame:
    """A deterministic EVE-shaped daily frame: no open, ISK-scale prices."""
    rng = np.random.default_rng(seed)
    close = 1_000_000 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, bars)))
    spread = np.abs(rng.normal(0.0, 0.015, bars))
    high = close * (1 + spread)
    low = close * (1 - spread)
    volume = np.round(np.abs(rng.normal(50_000, 15_000, bars)))
    order_count = np.round(np.abs(rng.normal(400, 80, bars))).astype(int)
    # Two deliberate ghost days: zero volume and zero order_count.
    volume[40] = 0
    order_count[40] = 0
    order_count[41] = 0
    # One deliberate outlier print, to exercise TR winsorization.
    high[90] = close[90] * 50
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "datetime": stamps,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "order_count": order_count,
        }
    )


def reference_avwap_bands(frame: pd.DataFrame, anchor_idx: int):
    """The upstream row loop, verbatim except `tp = close` (plan.md §4).

    This function exists to prove the vectorized implementation is identical to
    the formula it froze. It is never used in production code.
    """
    cum_volume = 0.0
    cum_value = 0.0
    cum_sd = 0.0
    for index in range(anchor_idx, len(frame)):
        row = frame.iloc[index]
        volume = float(row["volume"])
        if volume <= 0:
            continue
        typical = float(row["close"])
        cum_volume += volume
        cum_value += typical * volume
        running = cum_value / cum_volume
        deviation = typical - running
        cum_sd += deviation * deviation * volume
    if cum_volume == 0:
        return float("nan"), float("nan"), {}
    vwap = cum_value / cum_volume
    sigma = (cum_sd / cum_volume) ** 0.5
    return (
        vwap,
        sigma,
        {
            "UPPER_1": vwap + sigma,
            "LOWER_1": vwap - sigma,
            "UPPER_2": vwap + 2 * sigma,
            "LOWER_2": vwap - 2 * sigma,
            "UPPER_3": vwap + 3 * sigma,
            "LOWER_3": vwap - 3 * sigma,
            "VWAP": vwap,
        },
    )


def main() -> None:
    from evescreener.signals.atr import atr_series, true_range, winsorized_true_range
    from evescreener.signals.avwap import anchored_vwap_bands
    from evescreener.signals.levels import build_level_store

    FIXTURES.mkdir(parents=True, exist_ok=True)
    frame = synthetic_frame()
    frame.assign(datetime=frame["datetime"].astype(str)).to_json(
        FIXTURES / "golden_frame.json", orient="records", indent=2
    )

    bands = {}
    for anchor in (0, 60, 120):
        computed = anchored_vwap_bands(frame, anchor)
        reference = reference_avwap_bands(frame, anchor)
        assert abs(computed.vwap - reference[0]) < 1e-9, "vectorized AVWAP diverged"
        assert abs(computed.sigma - reference[1]) < 1e-9, "vectorized sigma diverged"
        bands[str(anchor)] = computed.as_dict()

    atr = atr_series(frame, length=20)
    raw_tr = true_range(frame)
    clean_tr, clamped = winsorized_true_range(frame, k=8.0, window=60)
    levels = build_level_store(frame, atr20=float(atr.iloc[-1]), round_steps=[1_000_000.0])

    payload = {
        "_provenance": {
            "generator": "tests/generate_golden.py",
            "formula": "running-AVWAP volume-weighted sigma, tp = close (plan.md §4, frozen)",
            "note": "regenerate FIRST when a detector changes, review the diff, then freeze",
        },
        "avwap": bands,
        "atr20_last": float(atr.iloc[-1]),
        "atr20_tail": [float(value) for value in atr.tail(5)],
        "true_range_max_raw": float(raw_tr.max()),
        "true_range_max_winsorized": float(clean_tr.max()),
        "true_range_clamped_bars": int(clamped.sum()),
        "levels": {
            "count": len(levels["levels"]),
            "prices": [round(float(level["price"]), 4) for level in levels["levels"][:12]],
            "strengths": [float(level["strength"]) for level in levels["levels"][:12]],
            "round_number_levels": [
                float(level["price"]) for level in levels["levels"] if level["kind"] == "round_isk"
            ],
        },
    }
    (FIXTURES / "golden_signals.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {FIXTURES / 'golden_signals.json'}")


if __name__ == "__main__":
    main()
