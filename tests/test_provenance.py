"""S8 — a number in prose is not a measurement.

`plan.md` §20.3 and `performers.py` quoted "2,944 tracked types", "0.88 pp
median difference", "39/23 readings above 1000%" and a worst raw reading of
"49,699,900%" with no as-of date, no membership definition, no denominators and
no way to re-run them. An independent reproduction disagreed with all of them,
and a third run disagreed again — and none of the three can be shown right or
wrong, because not one recorded what it measured. The differing numbers are the
symptom; the missing provenance is the defect.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from evescreener.performers import measure_top_performers
from evescreener.provenance import MeasurementReport, Statistic, code_version, file_identity

NOW = pd.Timestamp("2026-08-20T12:00:00+00:00").to_pydatetime()


def _bars(type_ids, days=45):
    stamps = pd.date_range(end=pd.Timestamp("2026-08-19", tz="UTC"), periods=days, freq="D")
    rows = []
    for type_id in type_ids:
        closes = np.full(days, 100.0)
        for index, stamp in enumerate(stamps):
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": stamp.replace(hour=11),
                    "high": closes[index],
                    "low": closes[index],
                    "close": closes[index],
                    "volume": 10_000.0,
                    "order_count": 30,
                    "isk_value": 1.0,
                    "fetched_at": "2026-08-20T11:30:00+00:00",
                }
            )
    return pd.DataFrame(rows)


# -- 1. every statistic carries what it was measured over -------------------


def test_a_report_records_when_what_and_with_which_code():
    report = MeasurementReport.start(
        "example", command="python -m evescreener report", membership="tracked types", now=NOW
    )
    payload = report.as_dict()
    assert payload["as_of"].startswith("2026-08-20T12:00")
    assert payload["command"] == "python -m evescreener report"
    assert payload["membership"] == "tracked types"
    assert payload["code_version"] == code_version()


def test_a_count_carries_its_denominator_and_share():
    stat = Statistic("above 1000%", 39, denominator=2944, is_count=True).as_dict()
    assert stat["denominator"] == 2944
    assert stat["share"] == pytest.approx(round(39 / 2944, 6), abs=1e-9)


def test_a_magnitude_gets_no_share_because_a_share_of_it_means_nothing():
    """'worst reading / population size' is not a number about anything."""
    stat = Statistic("worst raw reading", 59900.0, denominator=2539, unit="%").as_dict()
    assert stat["denominator"] == 2539
    assert "share" not in stat


def test_input_identity_notices_a_changed_file_and_says_what_it_is(tmp_path):
    path = tmp_path / "bars.parquet"
    path.write_bytes(b"one")
    first = file_identity([path])[0]
    assert first["present"] is True
    assert "not of the contents" in first["identity_is"], (
        "the digest must not imply it hashed the bytes"
    )

    path.write_bytes(b"a different length entirely")
    second = file_identity([path])[0]
    assert second["identity"] != first["identity"]


def test_a_missing_input_is_recorded_as_absent_not_skipped(tmp_path):
    record = file_identity([tmp_path / "nope.parquet"])[0]
    assert record["present"] is False


# -- 2. the TOP figures are re-derivable ------------------------------------


def test_the_top_report_states_its_membership_filters_and_denominators():
    report = measure_top_performers(
        _bars([34, 35]), now=NOW, volumes={34: 5000.0, 35: 5000.0}, min_units=100.0
    )
    payload = report.as_dict()
    assert "100" in payload["membership"]
    assert payload["filters"]["week_days"] == 7
    assert payload["filters"]["min_endpoint_bars"] == 3
    names = {stat["name"] for stat in payload["statistics"]}
    assert "names after the volume floor" in names
    assert "median |raw - robust|" in names
    for stat in payload["statistics"]:
        assert stat["denominator"] is not None, stat["name"]


def test_the_old_figures_are_labelled_historical_rather_than_replaced():
    """Their inputs cannot be recovered, so they are not overwritten (§22 S8)."""
    report = measure_top_performers(_bars([34]), now=NOW, volumes={34: 5000.0})
    joined = " ".join(report.notes).lower()
    assert "historical snapshot" in joined
    assert "not replaced" in joined


def test_the_report_round_trips_through_json_and_markdown(tmp_path):
    report = measure_top_performers(_bars([34]), now=NOW, volumes={34: 5000.0})
    target = report.write(tmp_path / "top.md")
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "**as of**" in text and "**membership**" in text
    payload = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
    assert payload["as_of"] == report.as_of
    assert payload["code_version"] == report.code_version


def test_two_runs_over_the_same_inputs_agree():
    """Reproducibility is the property being bought; assert it directly."""
    bars = _bars([34, 35])
    first = measure_top_performers(bars, now=NOW, volumes={34: 5000.0, 35: 5000.0})
    second = measure_top_performers(bars, now=NOW, volumes={34: 5000.0, 35: 5000.0})
    assert [s.as_dict() for s in first.statistics] == [s.as_dict() for s in second.statistics]


# -- 3. the isolation guard is wider than two exact names -------------------


@pytest.mark.parametrize(
    ("module", "forbidden"),
    [
        ("httpx", True),
        ("httpx.transports", True),
        ("requests", True),
        ("urllib.request", True),
        ("urllib3", True),
        ("aiohttp", True),
        ("evescreener.esi.client", True),
        ("evescreener.esi.budget", True),
        ("evescreener.esi", True),
        ("urllib.parse", False),
        ("socket", False),
        ("evescreener.gui.app", False),
        ("evescreener.performers", False),
    ],
)
def test_the_import_guard_covers_more_than_two_exact_names(module, forbidden):
    """R8 rejected only `httpx` and `evescreener.esi.client` (§22 S8)."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from _import_probe import is_forbidden

    assert is_forbidden(module) is forbidden, module


def test_socket_and_ssl_are_deliberately_allowed():
    """Qt and the stdlib load them regardless; flagging them proves nothing."""
    from pathlib import Path

    text = (Path(__file__).parent / "_import_probe.py").read_text(encoding="utf-8")
    assert "Deliberately NOT forbidden" in text
