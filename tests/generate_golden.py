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


def synthetic_instances(seed: int = 20260820, count: int = 400) -> pd.DataFrame:
    """A deterministic instance set for freezing the backtest's metrics."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-09-01", periods=count, freq="D", tz="UTC")
    entry = 1_000_000 * np.exp(rng.normal(0, 0.3, count))
    forward = rng.normal(0.01, 0.08, count)
    return pd.DataFrame(
        {
            "type_id": rng.integers(30, 60, count),
            "cohort": "synthetic",
            "datetime": dates,
            "horizon_days": 10,
            "entry_close": entry,
            "exit_close": entry * (1.0 + forward),
        }
    )


def index_lake(seed: int = 424242, bars: int = 120, types: int = 12) -> pd.DataFrame:
    """A deterministic multi-type lake for freezing the index series."""
    rng = np.random.default_rng(seed)
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    rows = []
    for index in range(types):
        close = 100.0 * (index + 1) * np.exp(np.cumsum(rng.normal(0.0005, 0.02, bars)))
        units = 10_000.0 * (index + 1)
        for position, stamp in enumerate(stamps):
            rows.append(
                {
                    "type_id": 1000 + index,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": close[position] * 1.01,
                    "low": close[position] * 0.99,
                    "close": close[position],
                    "volume": units,
                    "order_count": 50,
                    "isk_value": close[position] * units,
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    return pd.DataFrame(rows)


def churn_lake() -> pd.DataFrame:
    """A lake where a huge new member joins mid-series at a wildly different
    price level, to prove the chain-link holds the index level flat.

    Without chain-linking, admitting a member priced 1,000x the incumbents at a
    rebalance would print as an enormous index move. It must print as nothing.
    """
    bars = 120
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    rows = []
    for index in range(8):
        for stamp in stamps:
            close = 100.0 + index
            rows.append(
                {
                    "type_id": 2000 + index,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 10_000.0,
                    "order_count": 50,
                    "isk_value": close * 10_000.0,
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    # The interloper: appears at bar 60, priced 1,000x, with enormous turnover
    # so it dominates the next basket. Its own price never moves either.
    for position, stamp in enumerate(stamps):
        if position < 60:
            continue
        rows.append(
            {
                "type_id": 2999,
                "region_id": 10000002,
                "datetime": stamp,
                "high": 100_000.0,
                "low": 100_000.0,
                "close": 100_000.0,
                "volume": 1_000_000.0,
                "order_count": 500,
                "isk_value": 100_000.0 * 1_000_000.0,
                "fetched_at": "2026-08-20T00:00:00+00:00",
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    from evescreener.backtest import _stats, price_instances, verdict
    from evescreener.signals.atr import atr_series, true_range, winsorized_true_range
    from evescreener.signals.avwap import anchored_vwap_bands
    from evescreener.signals.composite import EQUAL, TURNOVER, build_composite
    from evescreener.signals.levels import build_level_store
    from evescreener.signals.moving import cloud_state, cross_within, ema, sma

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

    instances = synthetic_instances()
    haircuts = {
        int(type_id): {250e6: {"entry": 0.015, "exit": 0.02, "round_trip": 0.035}}
        for type_id in instances["type_id"].unique()
    }
    # Withdrawn by plan.md §21 R3 and preserved rather than deleted: no
    # historical number is erased, and the metric stays out of the report
    # until a reproducible portfolio model exists to justify it.
    backtest_withdrawn = {
        "_reason": (
            "max_drawdown_pct was sequential compounding of OVERLAPPING trades "
            "in date order, with no portfolio or capital-allocation model "
            "behind it. It was not a drawdown. The -100% readings at 2x and 3x "
            "are the artefact that gives it away."
        ),
        "_withdrawn_on": "2026-08-20",
        "1x": {"max_drawdown_pct": -99.99999999727139},
        "2x": {"max_drawdown_pct": -100.0},
        "3x": {"max_drawdown_pct": -100.0},
    }
    backtest_cells = {}
    for multiple in (1.0, 2.0, 3.0):
        priced, excluded = price_instances(
            instances, haircuts, tier=250e6, multiple=multiple, sales_tax_pct=3.375
        )
        stats = _stats(
            priced,
            horizon=10,
            tier=250e6,
            multiple=multiple,
            wilson_z=1.96,
            sales_tax_pct=3.375,
        )
        backtest_cells[f"{multiple:.0f}x"] = {
            **stats.as_dict(),
            "excluded_haircut_unknown": excluded,
            "verdict": verdict(stats)["verdict"],
        }

    moving = {
        "sma20_tail": [None if pd.isna(v) else float(v) for v in sma(frame, 20).tail(5)],
        "sma50_tail": [None if pd.isna(v) else float(v) for v in sma(frame, 50).tail(5)],
        "ema9_tail": [None if pd.isna(v) else float(v) for v in ema(frame, 9).tail(5)],
        "ema21_tail": [None if pd.isna(v) else float(v) for v in ema(frame, 21).tail(5)],
        "ema21_first_value_index": int(ema(frame, 21).notna().idxmax()),
        "cloud_9_21": cloud_state(frame, 9, 21).as_dict(),
        "cross_9_21_up_within_10": cross_within(
            frame,
            fast_kind="ema",
            fast_length=9,
            slow_kind="ema",
            slow_length=21,
            bars=10,
            direction="up",
        ),
        "cross_9_21_down_within_10": cross_within(
            frame,
            fast_kind="ema",
            fast_length=9,
            slow_kind="ema",
            slow_length=21,
            bars=10,
            direction="down",
        ),
    }

    lake = index_lake()
    forge = build_composite(lake, members=100, weighting=TURNOVER, ticker="FORGE")
    forge_ew = build_composite(
        lake,
        members=100,
        single_cap=1.0,
        weighting=EQUAL,
        member_ids=forge.member_ids,
        ticker="FORGE-EW",
    )
    churn = build_composite(churn_lake(), members=100, rebalance_days=30, ticker="CHURN")
    churn_levels = [float(value) for value in churn.frame["close"]]

    indices = {
        "forge_tail": [float(value) for value in forge.frame["close"].tail(5)],
        "forge_members": list(forge.member_ids),
        "forge_top_weight": forge.diagnostics["top_weight"],
        "forge_entropy": forge.diagnostics["weight_entropy"],
        "forge_ew_tail": [float(value) for value in forge_ew.frame["close"].tail(5)],
        "forge_ew_top_weight": forge_ew.diagnostics["top_weight"],
        "forge_ew_members": list(forge_ew.member_ids),
        "churn_level_min": min(churn_levels),
        "churn_level_max": max(churn_levels),
        "churn_rebalances": churn.diagnostics["rebalances"],
        "churn_final_members": churn.diagnostics["members"],
    }

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
        "backtest": backtest_cells,
        "backtest_withdrawn_pre_r3": backtest_withdrawn,
        "moving": moving,
        "indices": indices,
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
