"""The frozen bar contract. There is no `open` and none is ever synthesized."""

from __future__ import annotations

import pandas as pd
import pytest

from evescreener.bars import (
    EVE_DAILY_BAR_COLUMNS,
    empty_bar_frame,
    frame_from_history,
    participation,
    quality_flags,
)
from evescreener.store.lake import BarLake

SAMPLE = [
    {
        "average": 4.17,
        "date": "2026-08-16",
        "highest": 4.2,
        "lowest": 4.15,
        "order_count": 1592,
        "volume": 4595669625,
    },
    {
        "average": 4.07,
        "date": "2026-08-17",
        "highest": 4.13,
        "lowest": 4.02,
        "order_count": 1546,
        "volume": 5325465859,
    },
]


def test_contract_has_no_open_column():
    assert EVE_DAILY_BAR_COLUMNS == [
        "datetime",
        "high",
        "low",
        "close",
        "volume",
        "order_count",
    ]
    assert "open" not in EVE_DAILY_BAR_COLUMNS
    assert "open" not in empty_bar_frame().columns


def test_close_maps_from_esi_average_exactly_once():
    frame = frame_from_history(SAMPLE, type_id=34, region_id=10000002)
    assert list(frame["close"]) == [4.17, 4.07]
    assert list(frame["high"]) == [4.2, 4.13]
    assert list(frame["low"]) == [4.15, 4.02]
    assert "open" not in frame.columns


def test_datetime_is_tz_aware_at_the_downtime_boundary():
    frame = frame_from_history(SAMPLE, type_id=34, region_id=10000002)
    assert str(frame["datetime"].dt.tz) == "UTC"
    assert frame["datetime"].dt.hour.tolist() == [11, 11]


def test_isk_value_is_derived_at_write():
    frame = frame_from_history(SAMPLE, type_id=34, region_id=10000002)
    assert frame["isk_value"].iloc[0] == pytest.approx(4.17 * 4595669625)


def test_malformed_rows_are_dropped_not_repaired():
    rows = [*SAMPLE, {"date": "2026-08-18", "average": None}]
    frame = frame_from_history(rows, type_id=34, region_id=10000002)
    assert len(frame) == 2


def test_quality_flags_count_ghost_days():
    rows = [
        *SAMPLE,
        {
            "average": 4.0,
            "date": "2026-08-18",
            "highest": 4.0,
            "lowest": 4.0,
            "order_count": 0,
            "volume": 0,
        },
    ]
    flags = quality_flags(frame_from_history(rows, type_id=34, region_id=10000002))
    assert flags["rows"] == 3
    assert flags["zero_order_count"] == 1


def test_participation_is_order_count_versus_its_own_baseline():
    frame = pd.DataFrame({"order_count": [10] * 20 + [30]})
    series = participation(frame, window=20)
    assert series.iloc[-1] == pytest.approx(3.0)


def test_lake_round_trips_and_diff_appends(paths):
    lake = BarLake(paths)
    frame = frame_from_history(SAMPLE, type_id=34, region_id=10000002)
    assert lake.write(frame) == 2
    # A re-crawl of the same rows writes nothing new.
    assert lake.write(frame) == 0
    stored = lake.read(10000002)
    assert len(stored) == 2
    assert "open" not in stored.columns
    assert lake.type_ids(10000002) == [34]


def test_lake_read_filters_by_type_and_window(paths):
    lake = BarLake(paths)
    lake.write(frame_from_history(SAMPLE, type_id=34, region_id=10000002))
    lake.write(frame_from_history(SAMPLE, type_id=35, region_id=10000002))
    assert len(lake.read(10000002, type_ids=[34])) == 2
    cutoff = pd.Timestamp("2026-08-17", tz="UTC")
    assert len(lake.read(10000002, type_ids=[34], start=cutoff)) == 1


def test_missing_region_reads_empty_not_error(paths):
    assert BarLake(paths).read(99999999).empty
