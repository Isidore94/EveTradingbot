"""The report and the CLI render carry loops, filters and pair counts (§23.21)."""

from __future__ import annotations

from evescreener.hauling import HaulProfile, HaulScan, Rejection, ShipProfile
from evescreener.haulreport import CALC_VERSION, build_haul_report, render_haul_report
from test_loops import AMARR, JITA, _plan


def _scan(config) -> HaulScan:
    ship = ShipProfile(
        name="t", usable_cargo_m3=60_000.0, seconds_per_jump=60.0, handling_minutes=5.0
    )
    profile = HaulProfile(
        current_system=30000142,
        ship=ship,
        capital_isk=250e6,
        max_exposure_isk=250e6,
        session_minutes=600.0,
        min_quantity=2.0,
    )
    scan = HaulScan(generated_at="2026-09-04T12:00:00+00:00", profile=profile)
    scan.plans = [
        _plan(1, "A", JITA, AMARR, cost=100e6, net=10e6, haul_jumps=10),
        _plan(2, "B", AMARR, JITA, cost=105e6, net=8e6, haul_jumps=10),
    ]
    scan.withheld_by_filter = {"MIN_QUANTITY": 3}
    scan.rejected.append(
        Rejection(reason="OVER_TIME", source_station=1, dest_station=2, detail="too long")
    )
    scan.pairs_considered = 3
    return scan


def test_the_calc_version_moved_with_the_basket_arithmetic():
    assert CALC_VERSION == "haul-2"


def test_the_report_carries_loops_filters_and_pair_counts(config):
    report = build_haul_report(_scan(config), config=config)
    assert report["loops"]["loops"], "two opposite legs make a loop"
    assert report["loops"]["loops"][0]["net_isk"] == 18e6
    assert report["withheld_by_filter"] == {"MIN_QUANTITY": 3}
    assert report["pair_rejection_counts"] == {"OVER_TIME": 1}
    assert report["profile"]["min_quantity"] == 2.0
    assert report["basket"]["destination"] is not None


def test_the_render_shows_loops_and_withheld_plans(config):
    text = render_haul_report(build_haul_report(_scan(config), config=config))
    assert "## Loops" in text
    assert "A" in text and "B" in text
    assert "withheld by filter" in text.lower()
    assert "OVER_TIME" in text and "pair" in text
    assert "| persist |" in text or "persist" in text
