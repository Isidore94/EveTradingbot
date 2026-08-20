"""Golden fixtures for the signal layer.

These lock the frozen AVWAP sigma formula (invariant #1), ATR winsorization,
and the level pipeline. A change to any detector regenerates
`tests/fixtures/golden_signals.json` FIRST, the diff is reviewed, and only then
does the consuming code move (plan.md §8, §11 D5).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from evescreener.signals.atr import atr_series, true_range, winsorized_true_range  # noqa: E402
from evescreener.signals.avwap import (  # noqa: E402
    anchored_vwap_bands,
    anchored_vwap_history,
    band_position,
    classify_band,
)
from evescreener.signals.levels import build_level_store  # noqa: E402
from generate_golden import reference_avwap_bands, synthetic_frame  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "golden_signals.json").read_text())


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return synthetic_frame()


# -- invariant #1: the frozen sigma formula ---------------------------------


@pytest.mark.parametrize("anchor", [0, 60, 120])
def test_vectorized_avwap_matches_the_upstream_row_loop(frame, anchor):
    """The port is proven against the formula it froze, not asserted."""
    computed = anchored_vwap_bands(frame, anchor)
    vwap, sigma, bands = reference_avwap_bands(frame, anchor)
    assert computed.vwap == pytest.approx(vwap, abs=1e-9)
    assert computed.sigma == pytest.approx(sigma, abs=1e-9)
    for name, level in bands.items():
        assert computed.bands[name] == pytest.approx(level, abs=1e-9)


@pytest.mark.parametrize("anchor", ["0", "60", "120"])
def test_avwap_matches_the_golden_fixture(frame, anchor):
    computed = anchored_vwap_bands(frame, int(anchor))
    expected = GOLDEN["avwap"][anchor]
    assert computed.vwap == pytest.approx(expected["vwap"], rel=1e-12)
    assert computed.sigma == pytest.approx(expected["sigma"], rel=1e-12)
    assert computed.bars == expected["bars"]
    for name, level in expected["bands"].items():
        assert computed.bands[name] == pytest.approx(level, rel=1e-12)


def test_typical_price_is_close_not_ohlc4(frame):
    """tp = close (= ESI average). There is no open to build an OHLC4 from."""
    shifted = frame.copy()
    shifted["high"] = shifted["high"] * 3
    shifted["low"] = shifted["low"] / 3
    assert anchored_vwap_bands(shifted, 0).vwap == pytest.approx(anchored_vwap_bands(frame, 0).vwap)


def test_zero_volume_bars_are_skipped_exactly_as_upstream(frame):
    """A ghost day contributes nothing — the reference loop `continue`s on it."""
    without_ghost = frame.drop(index=40).reset_index(drop=True)
    assert anchored_vwap_bands(frame, 0).vwap == pytest.approx(
        anchored_vwap_bands(without_ghost, 0).vwap
    )


def test_sigma_is_running_avwap_deviation_not_distribution_stdev(frame):
    """The running variant runs tighter than the final-AVWAP distribution stdev."""
    close = frame["close"]
    volume = frame["volume"]
    mask = volume > 0
    final_vwap = (close[mask] * volume[mask]).sum() / volume[mask].sum()
    distribution = (
        ((close[mask] - final_vwap) ** 2 * volume[mask]).sum() / volume[mask].sum()
    ) ** 0.5
    running = anchored_vwap_bands(frame, 0).sigma
    assert running != pytest.approx(distribution)
    assert running < distribution


def test_band_history_tracks_the_last_bar_of_the_window(frame):
    history = anchored_vwap_history(frame, 60)
    bands = anchored_vwap_bands(frame, 60)
    assert history["vwap"].iloc[-1] == pytest.approx(bands.vwap)
    assert history["sigma"].iloc[-1] == pytest.approx(bands.sigma)
    assert history["UPPER_2"].iloc[-1] == pytest.approx(bands.bands["UPPER_2"])


def test_empty_and_out_of_range_anchors_are_unknown_not_zero(frame):
    assert not anchored_vwap_bands(pd.DataFrame(), 0).known
    assert not anchored_vwap_bands(frame, len(frame) + 5).known
    assert classify_band(None, anchored_vwap_bands(frame, 0)) == "UNKNOWN"
    assert band_position(1.0, anchored_vwap_bands(pd.DataFrame(), 0)) is None


def test_band_classification_names_the_dip_zones(frame):
    bands = anchored_vwap_bands(frame, 0)
    assert classify_band(bands.bands["LOWER_2"] - 1, bands) == "LOWER_2_3"
    assert classify_band(bands.vwap, bands) == "VWAP_UPPER_1"
    assert classify_band(bands.bands["LOWER_1"] + 1, bands) == "VWAP_LOWER_1"
    assert classify_band(bands.bands["UPPER_3"] + 1, bands) == "ABOVE_UPPER_3"


def test_truncated_anchor_says_so(frame):
    bands = anchored_vwap_bands(frame, 0, truncated=True)
    assert bands.truncated
    assert bands.known, "a truncated anchor still computes; it just declares itself"


# -- ATR --------------------------------------------------------------------


def test_atr_matches_the_golden_fixture(frame):
    series = atr_series(frame, length=20)
    assert float(series.iloc[-1]) == pytest.approx(GOLDEN["atr20_last"], rel=1e-12)
    assert [float(value) for value in series.tail(5)] == pytest.approx(
        GOLDEN["atr20_tail"], rel=1e-12
    )


def test_winsorization_clamps_the_scam_print(frame):
    raw = true_range(frame)
    clean, clamped = winsorized_true_range(frame, k=8.0, window=60)
    assert float(raw.max()) == pytest.approx(GOLDEN["true_range_max_raw"], rel=1e-12)
    assert float(clean.max()) == pytest.approx(GOLDEN["true_range_max_winsorized"], rel=1e-12)
    assert int(clamped.sum()) == GOLDEN["true_range_clamped_bars"] == 1
    assert clean.max() < raw.max() / 100, "one 5,000% print must not own the risk unit"


def test_atr_needs_no_open(frame):
    assert "open" not in frame.columns
    assert atr_series(frame, length=20).notna().any()


def test_ghost_days_are_excluded_from_true_range(frame):
    clean, _ = winsorized_true_range(frame, k=8.0, window=60)
    # Bars 40 and 41 carry order_count == 0.
    assert pd.isna(clean.iloc[40])
    assert pd.isna(clean.iloc[41])


def test_atr_of_a_short_frame_is_unknown():
    from evescreener.signals.atr import atr_last

    short = synthetic_frame().head(5)
    assert atr_last(short, length=20) is None


# -- levels -----------------------------------------------------------------


def test_level_pipeline_matches_the_golden_fixture(frame):
    atr = float(atr_series(frame, length=20).iloc[-1])
    store = build_level_store(frame, atr20=atr, round_steps=[1_000_000.0])
    expected = GOLDEN["levels"]
    assert len(store["levels"]) == expected["count"]
    prices = [round(float(level["price"]), 4) for level in store["levels"][:12]]
    assert prices == pytest.approx(expected["prices"], rel=1e-9)
    strengths = [float(level["strength"]) for level in store["levels"][:12]]
    assert strengths == pytest.approx(expected["strengths"], rel=1e-9)


def test_round_isk_levels_are_a_real_family(frame):
    atr = float(atr_series(frame, length=20).iloc[-1])
    store = build_level_store(frame, atr20=atr, round_steps=[1_000_000.0])
    rounds = [level for level in store["levels"] if level["kind"] == "round_isk"]
    assert rounds, "1M ISK is a player anchor and must appear as a level"
    assert all(level["price"] % 1_000_000 == 0 for level in rounds)


def test_round_levels_use_close_not_a_scam_high(frame):
    from evescreener.signals.levels import round_number_levels

    # The frame carries a 50x print in `high` at bar 90; it must not generate
    # forty spurious levels above the traded range.
    levels = round_number_levels(frame, [1_000_000.0])
    assert max(level["price"] for level in levels) <= frame["close"].max()


# -- backtest metrics -------------------------------------------------------


def test_backtest_metrics_match_the_golden_fixture():
    """Every backtest metric is frozen, not just the signal layer's.

    A scoring change must regenerate this fixture FIRST and have the diff
    reviewed, exactly like a detector change (plan.md §8).
    """
    from evescreener.backtest import _stats, price_instances, verdict
    from generate_golden import synthetic_instances

    instances = synthetic_instances()
    haircuts = {
        int(type_id): {250e6: {"entry": 0.015, "exit": 0.02, "round_trip": 0.035}}
        for type_id in instances["type_id"].unique()
    }
    for multiple in (1.0, 2.0, 3.0):
        expected = GOLDEN["backtest"][f"{multiple:.0f}x"]
        priced, excluded = price_instances(
            instances, haircuts, tier=250e6, multiple=multiple, sales_tax_pct=3.375
        )
        stats = _stats(priced, horizon=10, tier=250e6, multiple=multiple, wilson_z=1.96)
        assert excluded == expected["excluded_haircut_unknown"]
        assert stats.samples == expected["samples"]
        for field in (
            "win_rate",
            "wilson_lb",
            "breakeven_win_rate",
            "expectancy_pct",
            "median_pct",
            "max_drawdown_pct",
            "gross_expectancy_pct",
            "gross_win_rate",
            "round_trip_haircut_pct",
            "first_half_wilson_lb",
            "second_half_wilson_lb",
        ):
            assert getattr(stats, field) == pytest.approx(expected[field], rel=1e-12), field
        assert verdict(stats)["verdict"] == expected["verdict"]


def test_a_bigger_haircut_monotonically_worsens_every_metric():
    cells = [GOLDEN["backtest"][f"{multiple}x"] for multiple in (1, 2, 3)]
    assert [cell["expectancy_pct"] for cell in cells] == sorted(
        (cell["expectancy_pct"] for cell in cells), reverse=True
    )
    assert [cell["wilson_lb"] for cell in cells] == sorted(
        (cell["wilson_lb"] for cell in cells), reverse=True
    )
    assert [cell["breakeven_win_rate"] for cell in cells] == sorted(
        cell["breakeven_win_rate"] for cell in cells
    )
    # The gross figure is haircut-independent by construction.
    assert len({cell["gross_expectancy_pct"] for cell in cells}) == 1


# -- moving averages and the EMA cloud --------------------------------------


def test_moving_averages_match_the_golden_fixture(frame):
    from evescreener.signals.moving import cloud_state, cross_within, ema, sma

    expected = GOLDEN["moving"]
    for name, series in (
        ("sma20_tail", sma(frame, 20)),
        ("sma50_tail", sma(frame, 50)),
        ("ema9_tail", ema(frame, 9)),
        ("ema21_tail", ema(frame, 21)),
    ):
        actual = [None if pd.isna(value) else float(value) for value in series.tail(5)]
        assert actual == pytest.approx(expected[name], rel=1e-12), name
    assert cloud_state(frame, 9, 21).as_dict() == pytest.approx(expected["cloud_9_21"])
    assert (
        cross_within(
            frame,
            fast_kind="ema",
            fast_length=9,
            slow_kind="ema",
            slow_length=21,
            bars=10,
            direction="up",
        )
        is expected["cross_9_21_up_within_10"]
    )


def test_an_ema_is_seeded_on_the_sma_not_on_bar_one(frame):
    """pandas' ewm gives a value at bar 1 that is just the first close.

    "Price above the rising 21 EMA" must not fire on bar 2 against a line that
    is not an EMA yet.
    """
    from evescreener.signals.moving import ema, sma

    series = ema(frame, 21)
    assert series.iloc[:20].isna().all(), "no EMA(21) value may exist before 21 bars"
    assert float(series.iloc[20]) == pytest.approx(float(sma(frame, 21).iloc[20]))
    assert series.notna().idxmax() == GOLDEN["moving"]["ema21_first_value_index"]


def test_warm_up_is_unknown_not_zero(frame):
    from evescreener.signals.moving import cross_within, sma

    short = frame.head(5)
    assert sma(short, 50).isna().all()
    assert (
        cross_within(
            short,
            fast_kind="ema",
            fast_length=9,
            slow_kind="ema",
            slow_length=21,
            bars=5,
            direction="up",
        )
        is None
    ), "a cross cannot be observed where a line does not yet exist"


def test_cloud_state_reads_position_and_slope_independently():
    from evescreener.signals.moving import cloud_state

    rising = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01 11:00", periods=80, freq="D", tz="UTC"),
            "close": np.linspace(100.0, 200.0, 80),
        }
    )
    state = cloud_state(rising, 9, 21)
    assert state.position == "above"
    assert state.slope == "rising"

    falling = rising.copy()
    falling["close"] = falling["close"].to_numpy()[::-1]
    state = cloud_state(falling, 9, 21)
    assert state.position == "below"
    assert state.slope == "falling"


def test_an_unmeasurable_cloud_is_unknown():
    from evescreener.signals.moving import cloud_state

    state = cloud_state(pd.DataFrame(), 9, 21)
    assert not state.known
    assert state.as_dict()["position"] is None


def test_a_cross_is_detected_only_inside_its_window():
    from evescreener.signals.moving import cross_within

    values = np.concatenate([np.linspace(100, 80, 40), np.linspace(80, 140, 40)])
    frame = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01 11:00", periods=80, freq="D", tz="UTC"),
            "close": values,
        }
    )
    assert cross_within(
        frame,
        fast_kind="ema",
        fast_length=9,
        slow_kind="ema",
        slow_length=21,
        bars=40,
        direction="up",
    )
    assert not cross_within(
        frame,
        fast_kind="ema",
        fast_length=9,
        slow_kind="ema",
        slow_length=21,
        bars=2,
        direction="up",
    )


def test_an_unknown_average_kind_is_a_loud_error():
    from evescreener.signals.moving import cross_within

    with pytest.raises(ValueError, match="unknown moving-average kind"):
        cross_within(
            synthetic_frame(),
            fast_kind="wma",
            fast_length=9,
            slow_kind="ema",
            slow_length=21,
            bars=5,
        )
