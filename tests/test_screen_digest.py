"""The screen's honest-zero behaviour and the digest's delivery contract."""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from evescreener.digest import (
    AMBIGUOUS,
    DELIVERED,
    RATE_LIMITED,
    REJECTED,
    UNCONFIGURED,
    build_digest,
    post_digest,
    split_content,
)
from evescreener.screen import run_screen
from evescreener.signals.composite import build_composite

NOW = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)


def bars_for(type_ids, *, bars=200, dip_at=None, seed=9):
    rng = np.random.default_rng(seed)
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    rows = []
    for offset, type_id in enumerate(type_ids):
        close = 1_000_000 * np.exp(np.cumsum(rng.normal(0.0, 0.01, bars)))
        if dip_at is not None:
            close[dip_at:] = close[dip_at:] * 0.75
        for index, stamp in enumerate(stamps):
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": close[index] * 1.01,
                    "low": close[index] * 0.99,
                    "close": close[index],
                    "volume": 100_000.0 + offset,
                    "order_count": 400,
                    "isk_value": close[index] * 100_000.0,
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    return pd.DataFrame(rows)


def book_for(type_ids, *, sweep=NOW, ask=760_000.0, bid=740_000.0):
    rows = []
    for type_id in type_ids:
        for side, fill in (("sell", ask), ("buy", bid)):
            row = {
                "type_id": type_id,
                "region_id": 10000002,
                "side": side,
                "sweep_ts": sweep.isoformat(),
                "expires_ts": None,
                "best_price": fill,
                "total_volume": 1e9,
                "order_count": 40,
                "p5_price": fill,
                "top_order_volume_share": 0.05,
                "station_volume_share": 1.0,
                "partial_sweep": False,
                # R1/S2: a fixture must say where its quotes rested, or every
                # pricing path correctly refuses it.
                "best_location_id": 60003760,
                "exec_location_id": 60003760,
                "exec_price": fill,
                "exec_is_structure": False,
            }
            for index in range(3):
                row[f"depth_fill_price_{index}"] = fill
                row[f"depth_fill_qty_{index}"] = 1e6
            rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def seeded_db(db):
    db.replace_types([(tid, f"Type {tid}", 1857, 1.0, 1.0, 1) for tid in range(34, 50)])
    return db


# -- the screen -------------------------------------------------------------


def test_an_empty_lake_screens_to_an_honest_zero(config, seeded_db):
    result = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    assert result.honest_zero
    assert "bar lake is empty" in " ".join(result.notes)


def test_a_dip_that_clears_costs_becomes_a_candidate(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    result = run_screen(config, seeded_db, bars, composite, book_for(ids), now=NOW)
    assert result.setups_found > 0
    if result.candidates:
        row = result.candidates[0]
        assert row["net_edge_pct"] > 0
        assert row["expected_move_pct"] > row["tier_breakevens"][0]["breakeven_move_pct"]
        assert row["freshness"] == "fresh"


def test_a_stale_book_is_unknown_not_priced_off_history(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    stale = book_for(ids, sweep=NOW - timedelta(hours=6))
    result = run_screen(config, seeded_db, bars, composite, stale, now=NOW)
    assert result.candidates == []
    assert result.stale_book > 0
    assert result.unknown_cost > 0


def test_no_book_at_all_is_unknown_not_zero_cost(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    result = run_screen(config, seeded_db, bars, composite, pd.DataFrame(), now=NOW)
    assert result.candidates == []
    assert result.unknown_cost > 0


def test_a_setup_that_cannot_clear_breakeven_is_not_shown(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    # A 40% spread: nothing can clear this.
    wide = book_for(ids, ask=1_400_000.0, bid=700_000.0)
    result = run_screen(config, seeded_db, bars, composite, wide, now=NOW)
    assert result.candidates == []
    assert result.below_breakeven > 0 or result.unknown_cost > 0


def test_a_spoofed_book_is_flagged(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    book = book_for(ids)
    book.loc[book["side"] == "sell", "top_order_volume_share"] = 0.9
    result = run_screen(config, seeded_db, bars, composite, book, now=NOW)
    if result.candidates:
        assert any("one order holds" in flag for flag in result.candidates[0]["flags"])


# -- the digest -------------------------------------------------------------


def test_split_numbers_the_parts_rather_than_truncating():
    text = "\n".join(f"line {index} " + "x" * 100 for index in range(60))
    parts = split_content(text, 2000)
    assert len(parts) > 1
    assert parts[0].startswith("(1/")
    assert all(len(part) <= 2000 for part in parts)
    rejoined = "".join(part.split("\n", 1)[1] for part in parts)
    assert "line 59" in rejoined


def test_a_single_short_message_is_not_numbered():
    assert split_content("hello", 2000) == ["hello"]


def test_an_over_long_line_announces_its_own_split():
    parts = split_content("z" * 5000, 2000)
    assert len(parts) > 1
    assert any("line split" in part for part in parts)


def test_honest_zero_digest_explains_itself(config, seeded_db):
    result = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    text = build_digest(config, result)
    assert "Nothing clears costs today" in text
    assert "could not be priced" in text


def test_digest_warns_when_rejections_are_mostly_unknown(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    result = run_screen(config, seeded_db, bars, composite, pd.DataFrame(), now=NOW)
    text = build_digest(config, result)
    if result.unknown_cost:
        assert "absence of opportunity" in text


def test_digest_carries_the_composite_diagnostics_footer(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids)
    composite = build_composite(bars, members=10, min_members=5)
    result = run_screen(config, seeded_db, bars, composite, book_for(ids), now=NOW)
    assert "composite:" in build_digest(config, result)


def test_digest_never_mentions_everyone(config, seeded_db):
    result = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    text = build_digest(config, result)
    assert "@everyone" not in text and "@here" not in text


def test_a_lead_lag_payload_without_a_cohort_reports_H2_UNKNOWN(config, seeded_db):
    """§22 S4: the digest asserted a test of H2 that never happened.

    A payload declaring no cohort cannot be shown to be confirmatory, so it
    fails closed: the run is reported, and H2 stays UNKNOWN.
    """
    result = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    text = build_digest(
        config, result, lead_lag_outcome={"outcome": "DOES NOT SURVIVE", "reason": "weak"}
    )
    assert "H2 UNKNOWN" in text
    assert "tested and not supported" not in text
    assert "annotation only" in text
    assert "DOES NOT SURVIVE" in text, "the run itself is still reported"


def test_a_pooled_run_is_labelled_exploratory_in_the_digest(config, seeded_db):
    from evescreener.killmails import LeadLagResult

    study = LeadLagResult(generated_at="2026-08-20T00:00:00+00:00")
    study.outcome = {"outcome": "DOES NOT SURVIVE", "reason": "weak"}
    result = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    text = build_digest(config, result, lead_lag_outcome=study.as_dict())
    assert "H2 UNKNOWN" in text
    assert "exploratory" in text
    assert "doctrine cohort has never been measured" in text


# -- delivery ---------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=204):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status


def test_no_webhook_is_unconfigured_not_an_error(config, paths):
    result = post_digest(config, "hello", archive_path=paths.digests)
    assert result.kind == UNCONFIGURED
    assert paths.digests.exists(), "the digest is archived even with nowhere to post it"


def test_a_failed_publish_never_destroys_the_archive(config, paths, repo_root):
    from evescreener.config import config_from_mapping, load_example

    raw = load_example(repo_root)
    raw["app"]["data_dir"] = str(paths.root)
    raw["discord"]["webhook_url"] = "https://discord.example/webhook"
    live = config_from_mapping(raw)

    def opener(request, timeout=None):
        raise urllib.error.URLError("network down")

    result = post_digest(live, "content", opener=opener, archive_path=paths.digests)
    assert result.kind == AMBIGUOUS
    from evescreener.paths import read_jsonl

    assert len(read_jsonl(paths.digests)) == 1


def webhook_config(repo_root, paths):
    from evescreener.config import config_from_mapping, load_example

    raw = load_example(repo_root)
    raw["app"]["data_dir"] = str(paths.root)
    raw["discord"]["webhook_url"] = "https://discord.example/webhook"
    return config_from_mapping(raw)


def test_successful_delivery_reports_message_count(repo_root, paths):
    config = webhook_config(repo_root, paths)
    posted = []

    def opener(request, timeout=None):
        posted.append(json.loads(request.data))
        return FakeResponse(204)

    result = post_digest(config, "a\nb\nc", opener=opener, archive_path=paths.digests)
    assert result.kind == DELIVERED
    assert result.messages == 1
    assert posted[0]["username"] == config.discord.username


def test_a_429_is_rate_limited_with_its_retry_after(repo_root, paths):
    config = webhook_config(repo_root, paths)

    def opener(request, timeout=None):
        raise urllib.error.HTTPError("url", 429, "Too Many Requests", {"Retry-After": "3.5"}, None)

    result = post_digest(config, "hello", opener=opener)
    assert result.kind == RATE_LIMITED
    assert result.retry_after == 3.5


def test_a_4xx_is_rejected(repo_root, paths):
    config = webhook_config(repo_root, paths)

    def opener(request, timeout=None):
        raise urllib.error.HTTPError("url", 400, "Bad Request", {}, None)

    assert post_digest(config, "hello", opener=opener).kind == REJECTED


def test_a_partial_send_reports_how_many_landed(repo_root, paths):
    config = webhook_config(repo_root, paths)
    calls = {"n": 0}

    def opener(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise urllib.error.HTTPError("url", 500, "Server Error", {}, None)
        return FakeResponse(204)

    text = "\n".join("x" * 150 for _ in range(40))
    result = post_digest(config, text, opener=opener)
    assert result.kind == REJECTED
    assert result.messages == 1
    assert "1/" in result.detail


# -- the net-edge metric ----------------------------------------------------


def test_net_edge_prices_the_actual_round_trip_not_a_difference_of_percentages():
    """The naive `expected_move - breakeven` form flatters wide books.

    Both percentages are measured against different reference points (the close
    and the bid), so subtracting them understates the cost of a wide spread —
    the exact failure mode plan.md §9 R5 warns about. This is a real Forge
    candidate from a live sweep: a 44% spread that the naive form scored at
    +16%.
    """
    from evescreener.costs import CostModel
    from evescreener.screen import _net_edge_pct

    costs = CostModel.from_config(__import__("evescreener.config", fromlist=["x"]).example_config())
    wide = _net_edge_pct(
        vwap=1_496_450.47,
        close=742_600.0,
        ask_walk=979_080.95,
        bid_walk=546_153.16,
        costs=costs,
    )
    naive = (1_496_450.47 / 742_600.0 - 1) * 100 - (
        979_080.95 / ((1 - 0.03375) * 546_153.16) - 1
    ) * 100
    assert wide == pytest.approx(8.62, abs=0.05)
    assert naive == pytest.approx(15.98, abs=0.05)
    assert wide < naive, "the honest form must penalise the spread, not hide it"


def test_a_tight_book_keeps_its_edge():
    from evescreener.costs import CostModel
    from evescreener.screen import _net_edge_pct

    costs = CostModel.from_config(__import__("evescreener.config", fromlist=["x"]).example_config())
    tight = _net_edge_pct(
        vwap=1_496_450.47,
        close=742_600.0,
        ask_walk=745_000.0,
        bid_walk=740_000.0,
        costs=costs,
    )
    assert tight > 90.0


def test_net_edge_is_unknown_without_both_walks():
    from evescreener.costs import CostModel
    from evescreener.screen import _net_edge_pct

    costs = CostModel.from_config(__import__("evescreener.config", fromlist=["x"]).example_config())
    assert _net_edge_pct(vwap=100, close=50, ask_walk=None, bid_walk=50, costs=costs) is None
    assert _net_edge_pct(vwap=100, close=50, ask_walk=60, bid_walk=None, costs=costs) is None
    assert _net_edge_pct(vwap=None, close=50, ask_walk=60, bid_walk=50, costs=costs) is None


def test_tax_is_inside_the_net_edge():
    from evescreener.costs import CostModel
    from evescreener.screen import _net_edge_pct

    costs = CostModel.from_config(__import__("evescreener.config", fromlist=["x"]).example_config())
    # A perfectly flat book already at value: the only thing left is the tax.
    flat = _net_edge_pct(vwap=100.0, close=100.0, ask_walk=100.0, bid_walk=100.0, costs=costs)
    assert flat == pytest.approx(-3.375, abs=1e-6)


# -- expected-R -------------------------------------------------------------


def test_expected_r_is_the_structural_prior_until_trades_close():
    from evescreener.scoring import score_candidate

    score = score_candidate(dip_sigma=-1.7, rrs=1.7, participation=0.9, net_edge_pct=8.0)
    assert score.closed_samples == 0
    assert score.blend_weight == 0.0
    assert score.expected_r == score.prior_r
    assert "no closed trades have been recorded yet" in score.evidence


def test_the_tracker_leads_once_the_ledger_has_evidence():
    from evescreener.scoring import score_candidate

    prior = score_candidate(dip_sigma=-1.7, rrs=1.7, participation=0.9, net_edge_pct=8.0)
    tracked = score_candidate(
        dip_sigma=-1.7,
        rrs=1.7,
        participation=0.9,
        net_edge_pct=8.0,
        realized_r=-0.6,
        closed_samples=25,
    )
    assert tracked.blend_weight > 0.8
    assert tracked.expected_r < 0 < prior.expected_r
    assert "tracker-led" in tracked.evidence


def test_the_net_edge_is_an_input_to_the_score_not_only_a_filter():
    from evescreener.scoring import quality_points

    thin = quality_points(dip_sigma=-1.7, rrs=1.7, participation=0.9, net_edge_pct=0.5)
    fat = quality_points(dip_sigma=-1.7, rrs=1.7, participation=0.9, net_edge_pct=15.0)
    assert fat > thin, "a setup whose edge the spread eats must score lower AS a setup"


def test_an_unmeasurable_setup_scores_unknown_not_middling():
    from evescreener.scoring import score_candidate

    score = score_candidate(dip_sigma=None, rrs=1.7, participation=0.9)
    assert score.expected_r is None
    assert score.evidence == "UNKNOWN"


def test_realized_r_ignores_trades_that_never_defined_their_risk():
    from evescreener.scoring import realized_from_ledger

    records = [
        {"event": "close", "realized_r": 1.5},
        {"event": "close", "realized_r": None},
        {"event": "close", "realized_r": -0.5},
        {"event": "open"},
    ]
    mean, count = realized_from_ledger(records)
    assert count == 2
    assert mean == pytest.approx(0.5)
    assert realized_from_ledger([]) == (None, 0)


def test_candidates_rank_on_expected_r_with_net_edge_as_tie_break(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    result = run_screen(config, seeded_db, bars, composite, book_for(ids), now=NOW)
    if len(result.candidates) > 1:
        scores = [row["rank_score"] for row in result.candidates]
        assert scores == sorted(scores, reverse=True)
        assert all(row["evidence"] for row in result.candidates)


# -- the verdict banner -----------------------------------------------------


def test_a_failed_backtest_warns_ABOVE_the_candidates(config, seeded_db):
    """No one should read a ranked list without knowing the class failed."""
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    result = run_screen(config, seeded_db, bars, composite, book_for(ids), now=NOW)
    text = build_digest(
        config,
        result,
        backtest_verdict={
            "5": {"verdict": "NOT PLAUSIBLE"},
            "10": {"verdict": "NOT PLAUSIBLE"},
        },
    )
    assert "NOT PLAUSIBLE at every horizon" in text
    assert "not evidence the class works" in text
    banner_at = text.index("NOT PLAUSIBLE at every horizon")
    if result.candidates:
        assert banner_at < text.index("candidate(s) clearing costs")


def test_a_plausible_backtest_needs_no_banner(config, seeded_db):
    result = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    text = build_digest(config, result, backtest_verdict={"10": {"verdict": "PLAUSIBLE"}})
    assert "NOT PLAUSIBLE" not in text


def test_an_all_unknown_backtest_says_unknown_is_not_a_pass(config, seeded_db):
    result = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    text = build_digest(config, result, backtest_verdict={"10": {"verdict": "UNKNOWN"}})
    assert "not the same as a pass" in text


def test_no_backtest_means_no_banner(config, seeded_db):
    result = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    assert "⚠ **The backtest" not in build_digest(config, result)


def test_the_digest_calls_the_target_a_distance_not_a_forecast(config, seeded_db):
    """ "Expected move" would claim a confidence the system does not have."""
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    result = run_screen(config, seeded_db, bars, composite, book_for(ids), now=NOW)
    text = build_digest(config, result)
    if result.candidates:
        assert "to anchored value)" in text
        assert "expected move" not in text
