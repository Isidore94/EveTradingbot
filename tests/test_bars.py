import datetime as dt

import pandas as pd
import pytest

from evescreener.bars import (
    EVE_DAILY_BAR_COLUMNS,
    bars_from_history,
    read_bars,
    turnover_stats,
    write_bars,
)
from evescreener.clock import UTC

from .conftest import load_fixture

FETCHED_AT = dt.datetime(2026, 8, 18, 2, 34, 9, tzinfo=UTC)
AS_OF = dt.datetime(2026, 8, 18, 2, 34, 9, tzinfo=UTC)


@pytest.fixture
def history():
    return load_fixture("esi_history_forge_type34.json")["response"]


def _bars(history, **kwargs):
    return bars_from_history(
        history,
        type_id=34,
        region_id=10000002,
        fetched_at=FETCHED_AT,
        last_modified="Mon, 17 Aug 2026 11:23:36 GMT",
        as_of=AS_OF,
        **kwargs,
    )


def test_the_bar_contract_has_no_open_and_never_will():
    assert "open" not in EVE_DAILY_BAR_COLUMNS
    assert EVE_DAILY_BAR_COLUMNS == [
        "datetime",
        "high",
        "low",
        "close",
        "volume",
        "order_count",
    ]


def test_close_maps_from_esi_average_and_high_low_from_highest_lowest(history):
    frame, _ = _bars(history)
    first = history[0]
    row = frame[frame["date"] == dt.date.fromisoformat(first["date"])].iloc[0]
    assert row["close"] == first["average"]
    assert row["high"] == first["highest"]
    assert row["low"] == first["lowest"]
    assert row["volume"] == first["volume"]
    assert row["order_count"] == first["order_count"]


def test_isk_value_is_volume_times_close(history):
    frame, _ = _bars(history)
    assert (frame["isk_value"] == frame["volume"] * frame["close"]).all()


def test_only_completed_bars_are_kept(history):
    frame, dropped = _bars(history)
    assert dropped == 0
    assert frame["date"].max() == dt.date(2026, 8, 16)
    assert len(frame) == 412


def test_a_still_accumulating_bar_is_dropped_not_carried(history):
    partial = history + [
        {
            "date": "2026-08-17",
            "average": 4.0,
            "highest": 4.1,
            "lowest": 3.9,
            "volume": 1,
            "order_count": 1,
        }
    ]
    frame, dropped = _bars(partial)
    assert dropped == 1
    assert dt.date(2026, 8, 17) not in set(frame["date"])


def test_datetimes_are_tz_aware_utc_at_the_downtime_boundary(history):
    frame, _ = _bars(history)
    assert str(frame["datetime"].dt.tz) == "UTC"
    assert (frame["datetime"].dt.hour == 11).all()


def test_write_then_read_round_trips_and_dedupes_on_identity(config, history):
    frame, _ = _bars(history)
    write_bars(config.paths, 10000002, frame)
    write_bars(config.paths, 10000002, frame)  # a re-fetch must not duplicate
    stored = read_bars(config.paths, 10000002)
    assert len(stored) == len(frame)
    assert not stored.duplicated(subset=["type_id", "region_id", "date"]).any()


def test_a_refetched_bar_replaces_the_stored_row(config, history):
    frame, _ = _bars(history)
    write_bars(config.paths, 10000002, frame)
    corrected = frame.tail(1).copy()
    corrected["close"] = 99.0
    write_bars(config.paths, 10000002, corrected)
    stored = read_bars(config.paths, 10000002)
    assert stored.iloc[-1]["close"] == 99.0
    assert len(stored) == len(frame)


def test_read_bars_on_an_empty_lake_returns_an_empty_frame(config):
    assert read_bars(config.paths, 10000002).empty


def test_turnover_uses_a_median_over_the_trailing_window(history):
    frame, _ = _bars(history)
    stats = turnover_stats(frame, days=30, as_of=AS_OF)
    row = stats.iloc[0]
    window = frame[frame["datetime"] >= AS_OF - dt.timedelta(days=30)]
    assert row["bars"] == len(window)
    assert row["median_isk_value_30d"] == pytest.approx(window["isk_value"].median())
    assert row["median_order_count_30d"] == pytest.approx(
        window["order_count"].median()
    )


def test_turnover_of_an_empty_frame_is_empty_not_an_error():
    assert turnover_stats(pd.DataFrame()).empty
