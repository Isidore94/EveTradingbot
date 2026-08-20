"""The learning loop: what earns, what bleeds, and what is still UNKNOWN."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from evescreener.learning import (
    MIN_SAMPLES_FOR_A_READ,
    build_learning_report,
    measure_passes,
    render_learning,
)
from evescreener.paper import PaperLedger

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def synthetic_ledger(tmp_path, config, records) -> PaperLedger:
    """Write a ledger directly. The maths is what is under test, not the writer."""
    path = tmp_path / "paper.jsonl"
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")
    return PaperLedger(path, config)


def trade(index: int, setup: str, realized_r: float, *, likes=("rrs_strong",), days_ago=30):
    opened = NOW - timedelta(days=days_ago)
    closed = opened + timedelta(days=5)
    position_id = f"{34}-{index}"
    return [
        {
            "event": "open",
            "position_id": position_id,
            "at": opened.isoformat(),
            "type_id": 34,
            "type_name": "Tritanium",
            "setup_tag": setup,
            "like_tags": list(likes),
            "thesis": "t",
        },
        {
            "event": "close",
            "position_id": position_id,
            "at": closed.isoformat(),
            "realized_r": realized_r,
            "net_return_pct": realized_r * 2.0,
        },
    ]


def bars(type_id=34, *, start="2026-07-01", days=60, drift=0.0):
    stamps = pd.date_range(start, periods=days, freq="D", tz="UTC")
    closes = [100.0 * (1.0 + drift) ** offset for offset in range(days)]
    return pd.DataFrame(
        {
            "type_id": type_id,
            "region_id": 10000002,
            "datetime": stamps,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 10_000.0,
            "order_count": 20,
            "isk_value": [value * 10_000.0 for value in closes],
            "fetched_at": "x",
        }
    )


# -- setups -----------------------------------------------------------------


def test_a_small_sample_is_unknown_not_a_win_rate(tmp_path, config):
    records = []
    for index in range(4):
        records.extend(trade(index, "Cloud reclaim", 1.5))
    ledger = synthetic_ledger(tmp_path, config, records)
    report = build_learning_report(config, ledger, now=NOW)
    record = next(item for item in report.setups if item.name == "Cloud reclaim")
    assert record.closed == 4
    assert record.state == "UNKNOWN"
    assert record.validation == "UNVALIDATED"
    assert not report.has_enough_for_a_digest_mention
    assert any("below the 20" in note for note in report.notes)


def test_a_full_sample_gets_a_read(tmp_path, config):
    records = []
    for index in range(MIN_SAMPLES_FOR_A_READ):
        records.extend(trade(index, "Cloud reclaim", 1.0 if index % 2 == 0 else -0.5))
    ledger = synthetic_ledger(tmp_path, config, records)
    report = build_learning_report(config, ledger, now=NOW)
    record = report.setups[0]
    assert record.state == "MEASURED"
    assert record.validation == "VALIDATED"
    assert record.win_rate == pytest.approx(0.5)
    assert record.win_rate_lower < record.win_rate, "the bound is below the estimate"
    assert record.average_r == pytest.approx(0.25)
    assert report.has_enough_for_a_digest_mention


def test_a_lucky_small_sample_does_not_outrank_a_measured_one(tmp_path, config):
    """3-for-3 must not beat 40-for-70. That is the whole point of the bound."""
    records = []
    for index in range(3):
        records.extend(trade(index, "Lucky", 3.0))
    for index in range(MIN_SAMPLES_FOR_A_READ + 10):
        records.extend(trade(100 + index, "Grinder", 0.6 if index % 3 else -0.2))
    ledger = synthetic_ledger(tmp_path, config, records)
    report = build_learning_report(config, ledger, now=NOW)
    assert report.setups[0].name == "Grinder"
    assert report.setups[-1].name == "Lucky"
    assert report.setups[-1].state == "UNKNOWN"


def test_shrinkage_pulls_a_small_sample_toward_the_zero_prior(tmp_path, config):
    records = []
    for index in range(2):
        records.extend(trade(index, "Two trades", 4.0))
    ledger = synthetic_ledger(tmp_path, config, records)
    record = build_learning_report(config, ledger, now=NOW).setups[0]
    assert record.average_r == pytest.approx(4.0)
    assert record.expected_r < record.average_r, "two winners are not a 4R setup"
    assert record.blend_weight < 1.0


def test_freshness_decays_a_stale_setup(tmp_path, config):
    stale = synthetic_ledger(tmp_path, config, trade(0, "Old", 1.0, days_ago=200))
    fresh_path = tmp_path / "fresh"
    fresh_path.mkdir()
    fresh = synthetic_ledger(fresh_path, config, trade(0, "New", 1.0, days_ago=1))
    old = build_learning_report(config, stale, now=NOW).setups[0]
    new = build_learning_report(config, fresh, now=NOW).setups[0]
    assert old.freshness < new.freshness


def test_a_setup_with_no_trades_still_appears_with_its_notes(tmp_path, config):
    from evescreener.setups import Condition, Setup

    setup = Setup(
        name="Never taken",
        notes="I have not pulled the trigger on this yet.",
        conditions=(Condition("change", {"bars": 5, "op": "above", "value": 0.0}),),
    )
    ledger = synthetic_ledger(tmp_path, config, [])
    report = build_learning_report(config, ledger, setups=[setup], now=NOW)
    record = next(item for item in report.setups if item.name == "Never taken")
    assert record.closed == 0
    assert record.state == "UNKNOWN"
    assert record.notes.startswith("I have not")


# -- like tags --------------------------------------------------------------


def test_like_tags_are_measured_across_the_trades_that_carried_them(tmp_path, config):
    records = []
    for index in range(MIN_SAMPLES_FOR_A_READ):
        records.extend(trade(index, "A", 1.0, likes=("level_confluence",)))
    for index in range(4):
        records.extend(trade(100 + index, "B", -1.0, likes=("patch_catalyst",)))
    ledger = synthetic_ledger(tmp_path, config, records)
    report = build_learning_report(config, ledger, now=NOW)
    by_tag = {record.tag: record for record in report.like_tags}
    assert by_tag["level_confluence"].state == "MEASURED"
    assert by_tag["level_confluence"].average_r == pytest.approx(1.0)
    assert by_tag["patch_catalyst"].state == "UNKNOWN", "four samples is not a finding"


# -- regret tracking --------------------------------------------------------


def test_a_pass_whose_window_has_not_elapsed_is_pending_not_a_win():
    passes = [
        {
            "event": "pass",
            "at": "2026-08-19T12:00:00+00:00",
            "type_id": 34,
            "dislike_tags": ["too_thin"],
        }
    ]
    rows = measure_passes(passes, bars(days=60), horizon_days=10, now=NOW)
    assert rows[0]["right"] is None
    assert "bars have elapsed" in rows[0]["reason"]


def test_a_pass_on_a_type_with_no_haircut_is_unknown_not_free():
    passes = [
        {
            "event": "pass",
            "at": "2026-07-05T12:00:00+00:00",
            "type_id": 34,
            "dislike_tags": ["spread_too_wide"],
        }
    ]
    rows = measure_passes(passes, bars(), haircuts={}, horizon_days=10, now=NOW)
    assert rows[0]["right"] is None
    assert "UNKNOWN, not free" in rows[0]["reason"]


def test_a_pass_is_judged_net_of_both_haircuts_and_tax():
    """A gross-positive move that costs die on must score the pass RIGHT."""
    haircuts = {34: {250_000_000.0: {"entry": 0.05, "exit": 0.05}}}
    passes = [
        {
            "event": "pass",
            "at": "2026-07-05T12:00:00+00:00",
            "type_id": 34,
            "dislike_tags": ["spread_too_wide"],
        }
    ]
    # +0.5%/day for 10 days is about +5% gross — real money, and entirely eaten
    # by a 5% entry haircut, a 5% exit haircut and 3.375% tax.
    rows = measure_passes(
        passes,
        bars(drift=0.005),
        haircuts=haircuts,
        sales_tax_pct=3.375,
        horizon_days=10,
        now=NOW,
    )
    assert rows[0]["forgone_net_pct"] < 0
    assert rows[0]["right"] is True

    # The same move with negligible friction should score the pass WRONG.
    cheap = measure_passes(
        passes,
        bars(drift=0.005),
        haircuts={34: {250_000_000.0: {"entry": 0.0001, "exit": 0.0001}}},
        sales_tax_pct=0.0,
        horizon_days=10,
        now=NOW,
    )
    assert cheap[0]["forgone_net_pct"] > 0
    assert cheap[0]["right"] is False


def test_dislike_tags_are_reported_with_their_pending_count(tmp_path, config):
    records = [
        {
            "event": "pass",
            "at": "2026-07-05T12:00:00+00:00",
            "type_id": 34,
            "type_name": "Tritanium",
            "action": "not_today",
            "dislike_tags": ["spread_too_wide"],
        },
        {
            "event": "pass",
            "at": "2026-08-19T12:00:00+00:00",
            "type_id": 34,
            "type_name": "Tritanium",
            "action": "not_today",
            "dislike_tags": ["spread_too_wide"],
        },
    ]
    ledger = synthetic_ledger(tmp_path, config, records)
    report = build_learning_report(
        config,
        ledger,
        bars=bars(drift=0.005),
        haircuts={34: {250_000_000.0: {"entry": 0.05, "exit": 0.05}}},
        now=NOW,
    )
    record = report.dislike_tags[0]
    assert record.tag == "spread_too_wide"
    assert record.passes == 2
    assert record.measured == 1
    assert record.pending == 1
    assert record.state == "UNKNOWN", "one measured pass is not a finding"
    assert record.right_rate == pytest.approx(1.0)


def test_the_report_says_it_never_promotes_anything(tmp_path, config):
    ledger = synthetic_ledger(tmp_path, config, [])
    report = build_learning_report(config, ledger, now=NOW)
    assert any("never edits a setup" in note for note in report.notes)
    text = render_learning(report)
    assert "never edits a setup" in text
    assert "half the decision record is missing" in text


# -- the digest mention is gated -------------------------------------------


def test_the_digest_mentions_setups_only_past_the_threshold(tmp_path, config):
    """A daily message is exactly the wrong place to publish noise."""
    from evescreener.digest import build_digest

    class Screen:
        generated_at = "2026-08-20T16:00:00+00:00"
        region_id = 10000002
        universe = 2001
        honest_zero = True
        setups_found = 0
        below_breakeven = 0
        unknown_cost = 0
        stale_book = 0
        candidates = []
        composite = {}

    thin = synthetic_ledger(tmp_path, config, trade(0, "Cloud reclaim", 2.0))
    small = build_learning_report(config, thin, now=NOW)
    assert "What's working" not in build_digest(config, Screen(), learning=small)

    records = []
    for index in range(MIN_SAMPLES_FOR_A_READ):
        records.extend(trade(index, "Cloud reclaim", 1.0))
    big_path = tmp_path / "big"
    big_path.mkdir()
    full = build_learning_report(config, synthetic_ledger(big_path, config, records), now=NOW)
    body = build_digest(config, Screen(), learning=full)
    assert "What's working" in body
    assert "Cloud reclaim" in body
