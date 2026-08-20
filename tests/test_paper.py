"""Paper trading: fill realism, refusals, and the frozen verdict tracker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from evescreener.paper import PaperLedger, Refusal, book_quote, render_report

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
TIER = 250_000_000.0


def book(
    *, type_id=34, ask=105.0, bid=95.0, sweep=NOW, ask_fill=106.0, bid_fill=94.0
) -> pd.DataFrame:
    rows = []
    for side, best, fill in (("sell", ask, ask_fill), ("buy", bid, bid_fill)):
        row = {
            "type_id": type_id,
            "region_id": 10000002,
            "side": side,
            "sweep_ts": sweep.isoformat(),
            "expires_ts": None,
            "best_price": best,
            "total_volume": 1e9,
            "order_count": 20,
            "p5_price": best,
            "top_order_volume_share": 0.05,
            "station_volume_share": 1.0,
            "partial_sweep": False,
        }
        for index in range(3):
            row[f"depth_fill_price_{index}"] = fill if index == 0 else None
            row[f"depth_fill_qty_{index}"] = 1000.0 if index == 0 else None
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def ledger(config, paths):
    return PaperLedger(paths.paper_ledger, config)


# -- fill realism -----------------------------------------------------------


def test_entry_is_the_ask_walk_not_the_best_ask(ledger):
    record = ledger.open_position(
        type_id=34,
        type_name="Tritanium",
        notional_isk=TIER,
        book=book(),
        thesis="dip below anchored value",
        now=NOW,
    )
    assert record["entry_effective_price"] == 106.0, "best ask is 105; the walk is 106"
    assert record["entry_units"] == pytest.approx(TIER / 106.0)


def test_exit_is_the_bid_walk_net_of_tax(ledger):
    opened = ledger.open_position(
        type_id=34,
        type_name="Tritanium",
        notional_isk=TIER,
        book=book(),
        thesis="t",
        now=NOW,
    )
    exit_at = NOW + timedelta(days=5)
    closed = ledger.close_position(
        position_id=opened["position_id"],
        book=book(bid_fill=120.0, sweep=exit_at),
        now=exit_at,
    )
    assert closed["exit_walk_price"] == 120.0
    assert closed["exit_effective_price"] == pytest.approx(120.0 * (1 - 0.03375))
    assert closed["net_return_pct"] < (120 / 106 - 1) * 100, "tax is inside every exit"


def test_a_stale_book_refuses_the_fill_and_never_prices_off_history(ledger):
    stale = book(sweep=NOW - timedelta(hours=4))
    with pytest.raises(Refusal, match="refusing the fill"):
        ledger.open_position(
            type_id=34, type_name="Tritanium", notional_isk=TIER, book=stale, thesis="t", now=NOW
        )
    refusals = ledger.refusals()
    assert len(refusals) == 1
    assert "min old" in refusals[0]["reason"]


def test_a_refusal_is_recorded_not_swallowed(ledger):
    with pytest.raises(Refusal):
        ledger.open_position(
            type_id=34,
            type_name="X",
            notional_isk=TIER,
            book=pd.DataFrame(),
            thesis="t",
            now=NOW,
        )
    assert ledger.refusals()[0]["reason"] == "no book sweep available"
    assert ledger.report(now=NOW).refused == 1


def test_a_book_too_thin_for_the_notional_is_refused(ledger):
    thin = book()
    thin.loc[:, "depth_fill_price_0"] = float("nan")
    with pytest.raises(Refusal, match="cannot fill this notional"):
        ledger.open_position(
            type_id=34, type_name="X", notional_isk=TIER, book=thin, thesis="t", now=NOW
        )


def test_an_off_tier_notional_is_refused_rather_than_interpolated(ledger):
    with pytest.raises(Refusal, match="not one of the configured tiers"):
        ledger.open_position(
            type_id=34,
            type_name="X",
            notional_isk=333_000_000,
            book=book(),
            thesis="t",
            now=NOW,
        )


def test_no_retro_entries_the_open_is_stamped_with_its_sweep(ledger):
    record = ledger.open_position(
        type_id=34, type_name="X", notional_isk=TIER, book=book(), thesis="t", now=NOW
    )
    assert record["book_sweep_ts"] == NOW.isoformat(timespec="seconds")
    assert record["book_age_minutes"] == pytest.approx(0.0, abs=0.02)


def test_maker_exit_is_advisory_and_never_realized(ledger):
    opened = ledger.open_position(
        type_id=34,
        type_name="X",
        notional_isk=TIER,
        book=book(),
        thesis="t",
        target_price=130.0,
        now=NOW,
    )
    assert opened["maker_exit_advisory_net"] == pytest.approx(130.0 * (1 - 0.04375))
    exit_at = NOW + timedelta(days=3)
    closed = ledger.close_position(
        position_id=opened["position_id"],
        book=book(bid_fill=120.0, sweep=exit_at),
        now=exit_at,
    )
    # The realized exit is the TAKER walk, never the advisory maker number.
    assert closed["exit_effective_price"] == pytest.approx(120.0 * (1 - 0.03375))


def test_self_impact_is_flagged_but_still_recorded(ledger):
    record = ledger.open_position(
        type_id=34,
        type_name="X",
        notional_isk=TIER,
        book=book(),
        thesis="t",
        median_daily_turnover=1_000_000_000,
        now=NOW,
    )
    assert record["self_impact"] is True
    quiet = ledger.open_position(
        type_id=35,
        type_name="Y",
        notional_isk=TIER,
        book=book(type_id=35),
        thesis="t",
        median_daily_turnover=100_000_000_000,
        now=NOW,
    )
    assert quiet["self_impact"] is False


def test_book_quote_reports_its_own_freshness():
    quote = book_quote(
        book(sweep=NOW - timedelta(minutes=30)), type_id=34, side="sell", tier_index=0, now=NOW
    )
    assert quote.price == 106.0
    assert quote.age_minutes == pytest.approx(30.0, abs=0.1)
    assert not quote.stale


# -- the ledger -------------------------------------------------------------


def test_ledger_round_trips_open_mark_close(ledger):
    opened = ledger.open_position(
        type_id=34,
        type_name="Tritanium",
        notional_isk=TIER,
        book=book(),
        thesis="dip",
        stop_price=100.0,
        target_price=130.0,
        now=NOW,
    )
    mark_at = NOW + timedelta(days=1)
    marks = ledger.mark(book=book(bid_fill=110.0, sweep=mark_at), now=mark_at)
    assert len(marks) == 1
    assert marks[0]["unrealized_net_isk"] > 0
    exit_at = NOW + timedelta(days=6)
    closed = ledger.close_position(
        position_id=opened["position_id"],
        book=book(bid_fill=125.0, sweep=exit_at),
        now=exit_at,
    )
    assert closed["net_isk"] > 0
    assert closed["held_days"] == pytest.approx(6.0, abs=0.01)
    report = ledger.report(now=NOW + timedelta(days=6))
    assert len(report.closed) == 1
    assert report.open_positions == []


def test_a_stale_mark_says_so_rather_than_pricing(ledger):
    ledger.open_position(
        type_id=34, type_name="X", notional_isk=TIER, book=book(), thesis="t", now=NOW
    )
    marks = ledger.mark(book=book(sweep=NOW), now=NOW + timedelta(hours=5))
    assert marks[0]["mark_price"] is None
    assert marks[0]["stale"] is True
    assert "refusing" in marks[0]["unknown_reason"]


def test_closing_twice_is_refused(ledger):
    opened = ledger.open_position(
        type_id=34, type_name="X", notional_isk=TIER, book=book(), thesis="t", now=NOW
    )
    ledger.close_position(position_id=opened["position_id"], book=book(), now=NOW)
    with pytest.raises(Refusal, match="already closed"):
        ledger.close_position(position_id=opened["position_id"], book=book(), now=NOW)


def test_the_ledger_is_append_only(ledger):
    opened = ledger.open_position(
        type_id=34, type_name="X", notional_isk=TIER, book=book(), thesis="t", now=NOW
    )
    ledger.close_position(position_id=opened["position_id"], book=book(), now=NOW)
    events = [record["event"] for record in ledger.records()]
    assert events == ["open", "close"], "nothing rewrites an earlier record"


def test_real_fill_measures_the_cost_model_against_reality(ledger):
    opened = ledger.open_position(
        type_id=34, type_name="X", notional_isk=TIER, book=book(), thesis="t", now=NOW
    )
    record = ledger.record_real_fill(
        position_id=opened["position_id"],
        side="buy",
        actual_price=106.5,
        actual_units=opened["entry_units"],
        now=NOW,
    )
    assert record["predicted_price"] == 106.0
    assert record["within_tolerance"] is True
    assert record["tolerance_pct_of_notional"] == 0.5
    report = ledger.report(now=NOW)
    assert report.fill_accuracy["samples"] == 1
    assert report.fill_accuracy["within_tolerance"] == 1


def test_a_real_fill_outside_tolerance_is_flagged(ledger):
    opened = ledger.open_position(
        type_id=34, type_name="X", notional_isk=TIER, book=book(), thesis="t", now=NOW
    )
    record = ledger.record_real_fill(
        position_id=opened["position_id"],
        side="buy",
        actual_price=115.0,
        actual_units=opened["entry_units"],
        now=NOW,
    )
    assert record["within_tolerance"] is False


# -- the frozen verdict tracker ---------------------------------------------


def run_trades(ledger, count, *, wins, win_fill, loss_fill, start=NOW):
    """Close `count` trades of which `wins` are winners.

    Deliberately mixed: a sample with no losses has no measurable payoff ratio,
    and the verdict rule turns on exactly that ratio.
    """
    for index in range(count):
        moment = start + timedelta(days=index)
        exit_at = moment + timedelta(days=5)
        fill = win_fill if index < wins else loss_fill
        opened = ledger.open_position(
            type_id=34 + index,
            type_name=f"T{index}",
            notional_isk=TIER,
            book=book(type_id=34 + index, sweep=moment),
            thesis="dip",
            stop_price=100.0,
            now=moment,
        )
        ledger.close_position(
            position_id=opened["position_id"],
            book=book(type_id=34 + index, sweep=exit_at, bid_fill=fill),
            now=exit_at,
        )


def test_no_read_is_offered_before_twenty_closed_trades(ledger):
    run_trades(ledger, 5, wins=4, win_fill=125.0, loss_fill=95.0)
    verdict = ledger.report(now=NOW + timedelta(days=30)).verdict
    assert verdict["verdict"] == "TOO_EARLY"
    assert "none should be taken" in verdict["detail"]


def test_first_read_lands_at_twenty(ledger):
    run_trades(ledger, 20, wins=14, win_fill=130.0, loss_fill=105.0)
    verdict = ledger.report(now=NOW + timedelta(days=40)).verdict
    assert verdict["verdict"] == "PROMISING"
    assert "neither outcome is a decision" in verdict["detail"]


def test_a_losing_sample_falsifies_at_forty(ledger):
    run_trades(ledger, 40, wins=10, win_fill=115.0, loss_fill=85.0)
    report = ledger.report(now=NOW + timedelta(days=60))
    assert report.cumulative_net_isk < 0
    assert report.verdict["verdict"] == "FALSIFIED"
    assert "the honest response is to stop" in report.verdict["detail"]


def test_a_winning_sample_is_only_provisionally_confirmed(ledger):
    run_trades(ledger, 40, wins=28, win_fill=130.0, loss_fill=105.0)
    verdict = ledger.report(now=NOW + timedelta(days=60)).verdict
    assert verdict["verdict"] == "PROVISIONALLY_CONFIRMED"
    assert "operator decision" in verdict["detail"]


def test_the_verdict_always_cites_its_frozen_rule(ledger):
    verdict = ledger.report(now=NOW).verdict
    assert "plan.md §12.4" in verdict["rule"]
    assert "frozen 2026-08-20 before the first trade" in verdict["rule"]


def test_report_leads_with_refusals(ledger):
    with pytest.raises(Refusal):
        ledger.open_position(
            type_id=34,
            type_name="X",
            notional_isk=TIER,
            book=pd.DataFrame(),
            thesis="t",
            now=NOW,
        )
    text = render_report(ledger.report(now=NOW))
    assert text.index("## Refused / UNKNOWN") < text.index("## Results")
    assert "**1** decisions were refused" in text


def test_an_empty_ledger_reports_honestly(ledger):
    report = ledger.report(now=NOW)
    assert report.closed == []
    assert report.verdict["verdict"] == "TOO_EARLY"
    assert "no read is offered" in render_report(report)


def test_a_position_whose_book_vanished_can_still_be_closed_at_a_real_fill(ledger):
    """Otherwise a position is stuck open forever with no honest way out."""
    opened = ledger.open_position(
        type_id=34, type_name="X", notional_isk=TIER, book=book(), thesis="t", now=NOW
    )
    gone = book(sweep=NOW + timedelta(days=3))
    gone = gone[gone["side"] == "sell"]  # the bid side has disappeared
    with pytest.raises(Refusal, match="no buy side"):
        ledger.close_position(
            position_id=opened["position_id"], book=gone, now=NOW + timedelta(days=3)
        )
    closed = ledger.close_position(
        position_id=opened["position_id"],
        book=gone,
        now=NOW + timedelta(days=3),
        actual_price=130.0,
        note="sold it for real",
    )
    assert closed["priced_from"] == "operator_actual_fill"
    assert closed["exit_walk_price"] == 130.0
    # Tax still applies; the operator supplies the price, not the arithmetic.
    assert closed["exit_effective_price"] == pytest.approx(130.0 * (1 - 0.03375))
    assert closed["net_isk"] > 0


def test_a_book_priced_close_is_labelled_as_such(ledger):
    opened = ledger.open_position(
        type_id=34, type_name="X", notional_isk=TIER, book=book(), thesis="t", now=NOW
    )
    closed = ledger.close_position(position_id=opened["position_id"], book=book(), now=NOW)
    assert closed["priced_from"] == "book_walk"


def test_a_nonsense_actual_price_is_refused(ledger):
    opened = ledger.open_position(
        type_id=34, type_name="X", notional_isk=TIER, book=book(), thesis="t", now=NOW
    )
    with pytest.raises(Refusal, match="must be positive"):
        ledger.close_position(
            position_id=opened["position_id"], book=book(), now=NOW, actual_price=0.0
        )
