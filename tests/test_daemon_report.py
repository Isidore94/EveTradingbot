"""The daemon's cadences and the viability report's honesty."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from evescreener.daemon import Job, Scheduler, build_jobs, run_daemon
from evescreener.report import build_viability_report, render_viability

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def run(coro):
    import asyncio

    return asyncio.run(coro)


# -- the daemon -------------------------------------------------------------


def test_locked_cadences_are_wired(config):
    jobs = {
        job.name: job
        for job in build_jobs(
            config,
            dict.fromkeys(
                ["universe", "history", "books_home", "books_secondary", "digest", "killmails"],
                lambda: None,
            ),
        )
    }
    assert jobs["digest"].daily_at == "16:00"
    assert jobs["history"].daily_at == "11:20"
    assert jobs["books_home"].hot_window == ("15:00", "17:00")
    assert jobs["books_home"].hot_interval == timedelta(minutes=5)
    assert jobs["books_home"].interval == timedelta(minutes=60)


def test_a_missing_handler_simply_is_not_scheduled(config):
    jobs = build_jobs(config, {"history": lambda: None})
    assert [job.name for job in jobs] == ["history"]


def test_the_hot_window_tightens_the_book_cadence(config):
    job = next(job for job in build_jobs(config, {"books_home": lambda: None}))
    inside = job.schedule(datetime(2026, 8, 20, 16, 0, tzinfo=UTC))
    outside = job.schedule(datetime(2026, 8, 20, 3, 0, tzinfo=UTC))
    assert inside == datetime(2026, 8, 20, 16, 5, tzinfo=UTC)
    assert outside == datetime(2026, 8, 20, 4, 0, tzinfo=UTC)


def test_daily_jobs_wait_for_their_hour_instead_of_firing_on_boot():
    scheduler = Scheduler(jobs=[Job(name="digest", run=lambda: None, daily_at="16:00")])
    scheduler.prime(NOW)
    assert scheduler.jobs[0].next_run == datetime(2026, 8, 20, 16, 0, tzinfo=UTC)
    assert scheduler.due(NOW) == []


def test_interval_jobs_run_once_at_startup():
    scheduler = Scheduler(jobs=[Job(name="books", run=lambda: None, interval=timedelta(hours=1))])
    scheduler.prime(NOW)
    assert scheduler.due(NOW)


def test_one_failing_job_never_stops_the_others():
    calls = []

    def boom():
        raise RuntimeError("feed down")

    scheduler = Scheduler(
        jobs=[
            Job(name="bad", run=boom, interval=timedelta(minutes=5)),
            Job(name="good", run=lambda: calls.append(1), interval=timedelta(minutes=5)),
        ]
    )
    outcomes = run(scheduler.tick(NOW))
    assert calls == [1]
    assert [outcome["ok"] for outcome in outcomes] == [False, True]
    assert "feed down" in scheduler.jobs[0].last_error
    assert scheduler.jobs[0].next_run > NOW, "a failed job is rescheduled, not abandoned"


def test_async_handlers_are_awaited():
    seen = []

    async def handler():
        seen.append("ran")
        return {"ok": True}

    scheduler = Scheduler(jobs=[Job(name="a", run=handler, interval=timedelta(minutes=1))])
    outcomes = run(scheduler.tick(NOW))
    assert seen == ["ran"]
    assert outcomes[0]["result"] == {"ok": True}


def test_status_reports_every_job(config):
    scheduler = Scheduler(jobs=build_jobs(config, {"history": lambda: None}))
    scheduler.prime(NOW)
    status = scheduler.status()
    assert status[0]["job"] == "history"
    assert status[0]["next_run"] is not None


def test_daemon_runs_a_bounded_number_of_ticks(config):
    calls = []

    async def sleep(seconds):
        return None

    scheduler = run(
        run_daemon(
            config,
            {"killmails": lambda: calls.append(1)},
            stop_after=3,
            sleep=sleep,
            now=lambda: NOW,
        )
    )
    assert len(calls) == 1, "the 5-minute job fires once inside three same-instant ticks"
    assert scheduler.jobs[0].runs == 1


# -- the viability report ---------------------------------------------------


def test_missing_inputs_render_unknown_with_a_reason(config, paths):
    report = build_viability_report(config, reports_dir=paths.reports)
    text = render_viability(report)
    assert text.count("**UNKNOWN**") == 5
    assert "no census has been run" in text
    assert "no backtest has been run" in text
    assert "the experiment has not started" in text


def test_no_measurement_means_no_answer(config, paths):
    report = build_viability_report(config, reports_dir=paths.reports)
    assert "Not enough has been measured" in report.headline


def test_a_plausible_backtest_is_reported_as_plausible_not_proven(config, paths):
    report = build_viability_report(
        config,
        census={
            "generated_at": "2026-08-20T00:00:00+00:00",
            "active_types": 19152,
            "types_with_bars": 2264,
            "total_bars": 768450,
            "turnover_percentiles": {"p50": 1.0, "p90": 2.0, "p99": 3.0},
            "derived_floor": {
                "resolved": True,
                "rule": "r",
                "min_median_isk_value": 1e6,
                "min_median_order_count": 5,
                "types": 800,
                "share_of_turnover": 0.95,
            },
        },
        backtest={
            "generated_at": "2026-08-20T00:00:00+00:00",
            "verdicts": {"10": {"verdict": "PLAUSIBLE", "reason": "ok"}},
            "instances": 5000,
            "universe": 800,
            "cells": [],
            "limitations": ["a"],
        },
        reports_dir=paths.reports,
    )
    assert "PLAUSIBLE" in report.headline
    assert "Plausible is not proven" in report.headline
    assert "no historical order books" in report.headline


def test_a_failing_backtest_argues_against_loosening_the_rule(config, paths):
    report = build_viability_report(
        config,
        census={
            "generated_at": "x",
            "derived_floor": {"resolved": False, "reason": "r"},
            "turnover_percentiles": {},
        },
        backtest={
            "generated_at": "x",
            "verdicts": {"10": {"verdict": "NOT PLAUSIBLE", "reason": "expectancy negative"}},
            "cells": [],
            "limitations": [],
        },
        reports_dir=paths.reports,
    )
    assert "did not clear its own pre-stated bar" in report.headline
    assert "not for" in report.headline and "loosening the rule" in report.headline


def test_unknown_verdicts_are_not_a_negative_result(config, paths):
    report = build_viability_report(
        config,
        census={"generated_at": "x", "derived_floor": {}, "turnover_percentiles": {}},
        backtest={
            "generated_at": "x",
            "verdicts": {"10": {"verdict": "UNKNOWN", "reason": "n"}},
            "cells": [],
            "limitations": [],
        },
        reports_dir=paths.reports,
    )
    assert "not the same as a negative result" in report.headline


def test_a_paper_verdict_outranks_the_backtest(config, paths):
    report = build_viability_report(
        config,
        census={"generated_at": "x", "derived_floor": {}, "turnover_percentiles": {}},
        backtest={
            "generated_at": "x",
            "verdicts": {"10": {"verdict": "PLAUSIBLE", "reason": ""}},
            "cells": [],
            "limitations": [],
        },
        paper={
            "generated_at": "x",
            "verdict": {"verdict": "FALSIFIED", "detail": "d", "rule": "r"},
            "refused": 3,
            "closed_count": 40,
            "cumulative_net_isk": -1e9,
        },
        reports_dir=paths.reports,
    )
    assert "FALSIFIED" in report.headline
    assert "actually pricing decisions at a real size" in report.headline


def test_every_section_cites_its_source(config, paths):
    text = render_viability(build_viability_report(config, reports_dir=paths.reports))
    assert text.count("_source:") == 5


def test_the_report_disclaims_being_a_recommendation(config, paths):
    text = render_viability(build_viability_report(config, reports_dir=paths.reports))
    assert "It is not a recommendation" in text
    assert "a negative answer is a possible" in text


def test_cross_region_zero_is_reported_as_valid(config, paths):
    report = build_viability_report(
        config,
        cross_region={
            "generated_at": "x",
            "rows": [],
            "pairs_considered": 500,
            "dropped_no_freight": 12,
            "dropped_no_depth": 400,
            "dropped_negative": 88,
        },
        reports_dir=paths.reports,
    )
    text = render_viability(report)
    assert "valid, expected result" in text
    assert "no quote, no row" in text


def test_a_friction_failure_is_distinguished_from_a_direction_failure(config, paths):
    """Why the backtest failed changes what the operator would do next."""
    report = build_viability_report(
        config,
        census={"generated_at": "x", "derived_floor": {}, "turnover_percentiles": {}},
        backtest={
            "generated_at": "x",
            "verdicts": {
                "10": {
                    "verdict": "NOT PLAUSIBLE",
                    "reason": "expectancy negative",
                    "gross_context": "before costs +3.08% ... the frictions are larger "
                    "than the edge.",
                }
            },
            "cells": [],
            "limitations": [],
        },
        reports_dir=paths.reports,
    )
    assert "failed on friction, not on direction" in report.headline
    assert "new study with its own pre-stated rule" in report.headline
    assert "not a" in report.headline and "re-run of this one" in report.headline


def test_a_direction_failure_does_not_claim_a_friction_excuse(config, paths):
    report = build_viability_report(
        config,
        census={"generated_at": "x", "derived_floor": {}, "turnover_percentiles": {}},
        backtest={
            "generated_at": "x",
            "verdicts": {
                "10": {
                    "verdict": "NOT PLAUSIBLE",
                    "reason": "expectancy negative",
                    "gross_context": "before costs -1.20% ... no pre-cost edge to lose.",
                }
            },
            "cells": [],
            "limitations": [],
        },
        reports_dir=paths.reports,
    )
    assert "failed on friction" not in report.headline
    assert "did not clear its own pre-stated bar" in report.headline
