"""The destruction layer and the frozen lead-lag pass rule (plan.md §14.3)."""

from __future__ import annotations

import io
import json
import tarfile

import numpy as np
import pandas as pd
import pytest

from evescreener.killmails import (
    MIN_OBSERVATIONS,
    PASS_RULE,
    LeadLagResult,
    destruction_z,
    evaluate_lead_lag,
    read_archive,
    reduce_killmails,
    render_lead_lag,
    run_lead_lag_study,
    spearman,
)

SYSTEM_REGIONS = {30000142: 10000002, 30002187: 10000043}


def killmail(*, time, system, hull, items):
    return {
        "killmail_id": 1,
        "killmail_time": time,
        "solar_system_id": system,
        "victim": {"ship_type_id": hull, "items": items},
        "attackers": [],
    }


# -- reduction --------------------------------------------------------------


def test_hulls_and_modules_are_counted_separately():
    counts, unmapped = reduce_killmails(
        [
            killmail(
                time="2026-08-18T12:00:00Z",
                system=30000142,
                hull=22546,
                items=[
                    {"item_type_id": 2048, "quantity_destroyed": 2},
                    {"item_type_id": 2048, "quantity_destroyed": 1},
                ],
            )
        ],
        SYSTEM_REGIONS,
    )
    assert counts[(22546, 10000002, "2026-08-18")] == [1, 0]
    assert counts[(2048, 10000002, "2026-08-18")] == [0, 3]
    assert unmapped == 0


def test_dropped_items_are_not_demand():
    """A dropped module survived; it does not need re-buying."""
    counts, _ = reduce_killmails(
        [
            killmail(
                time="2026-08-18T12:00:00Z",
                system=30000142,
                hull=22546,
                items=[{"item_type_id": 2048, "quantity_dropped": 5}],
            )
        ],
        SYSTEM_REGIONS,
    )
    assert (2048, 10000002, "2026-08-18") not in counts


def test_unmapped_systems_are_counted_not_guessed():
    counts, unmapped = reduce_killmails(
        [killmail(time="2026-08-18T12:00:00Z", system=39999999, hull=1, items=[])],
        SYSTEM_REGIONS,
    )
    assert counts == {}
    assert unmapped == 1


def test_losses_bucket_by_region():
    counts, _ = reduce_killmails(
        [
            killmail(time="2026-08-18T01:00:00Z", system=30000142, hull=22546, items=[]),
            killmail(time="2026-08-18T02:00:00Z", system=30002187, hull=22546, items=[]),
        ],
        SYSTEM_REGIONS,
    )
    assert counts[(22546, 10000002, "2026-08-18")] == [1, 0]
    assert counts[(22546, 10000043, "2026-08-18")] == [1, 0]


def test_read_archive_parses_the_everef_tar_layout():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as archive:
        payload = json.dumps(
            killmail(time="2026-08-18T00:00:00Z", system=30000142, hull=670, items=[])
        ).encode()
        info = tarfile.TarInfo("killmails/1.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    killmails = read_archive(buffer.getvalue())
    assert len(killmails) == 1
    assert killmails[0]["victim"]["ship_type_id"] == 670


# -- destruction_z ----------------------------------------------------------


def destruction_history(type_id=22546, days=200, spike_at=150, base=10, spike=200):
    rows = []
    start = pd.Timestamp("2026-01-01", tz="UTC")
    for offset in range(days):
        rows.append(
            {
                "type_id": type_id,
                "region_id": 10000002,
                "day": start + pd.Timedelta(days=offset),
                "hull_losses": spike if offset == spike_at else base,
                "module_losses": 0,
            }
        )
    return pd.DataFrame(rows)


def test_destruction_z_spikes_on_a_war():
    scores = destruction_z(destruction_history(), recent_days=7, baseline_days=90)
    peak = scores.loc[scores["destruction_z"].idxmax()]
    assert peak["destruction_z"] > 3.0
    assert pd.Timestamp("2026-05-01", tz="UTC") < peak["day"]


def test_destruction_z_of_a_flat_series_is_not_a_signal():
    flat = destruction_history(spike_at=-1)
    scores = destruction_z(flat, recent_days=7, baseline_days=90)
    finite = scores["destruction_z"].dropna()
    assert finite.empty or finite.abs().max() < 1e-6


def test_destruction_z_of_nothing_is_empty_not_zero():
    assert destruction_z(pd.DataFrame()).empty


# -- spearman ---------------------------------------------------------------


def test_spearman_finds_a_monotone_relationship():
    x = np.arange(1000, dtype="float64")
    rho, p, n = spearman(x, x * 2 + 1)
    assert rho == pytest.approx(1.0)
    assert p < 1e-9
    assert n == 1000


def test_spearman_of_noise_is_near_zero():
    rng = np.random.default_rng(1)
    rho, p, _ = spearman(rng.normal(size=2000), rng.normal(size=2000))
    assert abs(rho) < 0.1
    assert p > 0.01


def test_spearman_of_a_tiny_sample_is_unknown():
    assert spearman(np.arange(5.0), np.arange(5.0)) == (None, None, 5)


def test_spearman_of_a_constant_series_is_unknown():
    rho, p, _ = spearman(np.ones(100), np.arange(100.0))
    assert rho is None


# -- the frozen pass rule ---------------------------------------------------


def lag_row(**overrides):
    row = {
        "lag_days": 2,
        "target": "participation",
        "rho": 0.20,
        "p_value": 1e-9,
        "observations": 5000,
        "first_half_rho": 0.18,
        "first_half_n": 2500,
        "second_half_rho": 0.22,
        "second_half_n": 2500,
    }
    row.update(overrides)
    return row


def result_with(lag, placebo_rho=0.02):
    result = LeadLagResult(generated_at="2026-08-20T00:00:00+00:00")
    result.lags = [lag]
    result.placebo = [
        {
            "lag_days": lag["lag_days"],
            "target": lag["target"],
            "rho": placebo_rho,
            "p_value": 0.5,
            "observations": lag["observations"],
        }
    ]
    return result


def test_a_real_effect_survives():
    outcome = evaluate_lead_lag(result_with(lag_row()))
    assert outcome["outcome"] == "SURVIVES"
    assert "after a shadow period" in outcome["consequence"]


def test_a_weak_effect_does_not_survive():
    outcome = evaluate_lead_lag(result_with(lag_row(rho=0.05)))
    assert outcome["outcome"] == "DOES NOT SURVIVE"
    assert "ANNOTATIONS ONLY" in outcome["consequence"]


def test_an_insignificant_effect_does_not_survive():
    outcome = evaluate_lead_lag(result_with(lag_row(p_value=0.20)))
    assert outcome["outcome"] == "DOES NOT SURVIVE"


def test_too_few_observations_does_not_survive():
    outcome = evaluate_lead_lag(result_with(lag_row(observations=MIN_OBSERVATIONS - 1)))
    assert outcome["outcome"] == "DOES NOT SURVIVE"


def test_a_sign_flip_across_halves_does_not_survive():
    outcome = evaluate_lead_lag(result_with(lag_row(second_half_rho=-0.19)))
    assert outcome["outcome"] == "DOES NOT SURVIVE"


def test_a_placebo_that_reproduces_the_effect_kills_it():
    outcome = evaluate_lead_lag(result_with(lag_row(), placebo_rho=0.15))
    assert outcome["outcome"] == "DOES NOT SURVIVE"


def test_unmeasurable_lags_are_unknown_not_a_null_result():
    outcome = evaluate_lead_lag(result_with(lag_row(rho=None, p_value=None)))
    assert outcome["outcome"] == "UNKNOWN"


def test_the_outcome_always_cites_the_frozen_rule():
    for outcome in (
        evaluate_lead_lag(result_with(lag_row())),
        evaluate_lead_lag(result_with(lag_row(rho=0.01))),
    ):
        assert outcome["rule"] == PASS_RULE
        assert "frozen 2026-08-20 before measurement" in outcome["rule"]


# -- the whole study --------------------------------------------------------


def test_study_without_data_is_unknown_never_a_null_result(config):
    result = run_lead_lag_study(config, pd.DataFrame(), pd.DataFrame())
    assert result.outcome["outcome"] == "UNKNOWN"
    assert "unmeasured effect is not a measured absence" in " ".join(result.notes)


def test_study_runs_end_to_end_on_synthetic_data(config):
    rng = np.random.default_rng(5)
    days = pd.date_range("2026-01-01 11:00", periods=300, freq="D", tz="UTC")
    rows = []
    for type_id in (22546, 670, 24698):
        close = 1e6 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
        for index, day in enumerate(days):
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": day,
                    "high": close[index] * 1.01,
                    "low": close[index] * 0.99,
                    "close": close[index],
                    "volume": 1e6,
                    "order_count": int(300 + rng.normal(0, 30)),
                    "isk_value": close[index] * 1e6,
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    bars = pd.DataFrame(rows)
    destruction = pd.concat(
        [destruction_history(type_id=tid, days=300) for tid in (22546, 670, 24698)],
        ignore_index=True,
    )
    scores = destruction_z(destruction, recent_days=7, baseline_days=90)
    result = run_lead_lag_study(config, bars, scores)
    assert result.observations > 0
    assert len(result.lags) == config.killmails.lead_lag_max_lag_days * 2
    assert len(result.placebo) == len(result.lags)
    assert result.outcome["outcome"] in {"SURVIVES", "DOES NOT SURVIVE", "UNKNOWN"}
    report = render_lead_lag(result)
    assert "frozen in plan.md §14.1 before this study ran" in report
    assert "placebo rho" in report
