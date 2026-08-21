"""Two fill models, scored apart — plan.md §12.2 as amended 2026-08-21.

The operator asked for fills that simulate what really happens. In EVE that
is a choice between two things, and only two: cross the spread now (taker), or
post and wait (maker). **Mid is not one of them** — no order type executes
there, and with a 98.8% median spread (§17) half of it is not a rounding error,
it is the entire result. These tests pin that boundary, the arithmetic on each
side of it, and the one property that keeps the record honest: a maker fill is
ASSUMED, and it is never averaged into a taker result without saying so.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from evescreener.paper import PaperLedger, Refusal, book_quote

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
TIER = 250_000_000.0
JITA_44 = 60003760
REASONS = {"setup_tag": "discretionary", "like_tags": ["clean_dip_below_value"]}


def book(*, type_id=34, ask=105.0, bid=95.0, sweep=NOW, ask_fill=106.0, bid_fill=94.0):
    """A wide two-sided book: the two models must disagree loudly on it."""
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
            "best_location_id": JITA_44,
            "exec_location_id": JITA_44,
            "exec_price": best,
            "exec_volume": 4_000.0,
            "exec_is_structure": False,
        }
        for index in range(3):
            row[f"depth_fill_price_{index}"] = fill if index == 0 else None
            row[f"depth_fill_qty_{index}"] = 1000.0 if index == 0 else None
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def ledger(config, paths):
    return PaperLedger(paths.paper_ledger, config)


def _open(ledger, **kwargs):
    return ledger.open_position(
        type_id=34,
        type_name="Tritanium",
        notional_isk=TIER,
        book=kwargs.pop("book", None) if "book" in kwargs else book(),
        thesis="wide book, worth posting into",
        now=NOW,
        **REASONS,
        **kwargs,
    )


# -- the boundary -----------------------------------------------------------


def test_there_is_no_mid_fill_and_asking_for_one_is_a_recorded_refusal(ledger):
    """The one thing the operator must not be able to buy at."""
    with pytest.raises(Refusal, match="no mid fill"):
        _open(ledger, fill_model="mid")
    refusals = ledger.refusals()
    assert refusals, "a refused fill model is recorded like any other refusal"
    assert refusals[-1]["fill_model"] == "mid"


def test_the_frozen_taker_default_is_unchanged_when_no_model_is_named(ledger):
    record = _open(ledger)
    assert record["fill_model"] == "taker"
    # 106.0 is the ask WALK, not the 105.0 best ask: §12.2's original rule.
    assert record["entry_effective_price"] == pytest.approx(106.0)
    assert record["fill_assumed"] is False
    assert record["fill_assumption"] is None


# -- maker arithmetic -------------------------------------------------------


def test_a_maker_entry_posts_one_tick_above_the_executable_bid_with_the_broker_fee(ledger, config):
    record = _open(ledger, fill_model="maker")
    broker = ledger.costs.broker_fee_at(JITA_44)
    quoted = 95.0 + config.paper.maker_tick_isk
    assert record["entry_quote_price"] == pytest.approx(quoted)
    # A posted buy pays the broker fee; the effective price says so, and the
    # units are computed on money actually spent.
    assert record["entry_effective_price"] == pytest.approx(quoted * (1 + broker / 100.0))
    assert record["entry_units"] == pytest.approx(TIER / record["entry_effective_price"])
    assert record["exec_location_id"] == JITA_44


def test_a_maker_entry_is_stamped_assumed_and_carries_the_queue_ahead_of_it(ledger):
    record = _open(ledger, fill_model="maker")
    assert record["fill_assumed"] is True
    assert "ASSUMED" in record["fill_assumption"]
    # `exec_volume` is what is already resting there — what you queue behind,
    # never a fill you got.
    assert record["queue_ahead_units"] == pytest.approx(4_000.0)
    assert record["entry_walk_qty"] is None


def test_a_maker_entry_beats_a_taker_entry_by_almost_the_whole_spread(ledger):
    taker = _open(ledger, fill_model="taker")
    maker = _open(ledger, fill_model="maker")
    assert maker["entry_effective_price"] < taker["entry_effective_price"]
    # That gap is the reason the model must be recorded on every row: it is
    # roughly the entire measured result of this system.
    edge = 1 - maker["entry_effective_price"] / taker["entry_effective_price"]
    assert edge > 0.08


def test_a_maker_exit_pays_tax_and_the_broker_fee_a_taker_exit_only_tax(ledger):
    taker = _open(ledger, fill_model="taker")
    maker = _open(ledger, fill_model="maker")
    taker_close = ledger.close_position(position_id=taker["position_id"], book=book(), now=NOW)
    maker_close = ledger.close_position(position_id=maker["position_id"], book=book(), now=NOW)

    tax = ledger.costs.sales_tax_pct
    broker = ledger.costs.broker_fee_at(JITA_44)
    # Taker exits by walking the bids: 94.0, net of tax only.
    assert taker_close["exit_walk_price"] == pytest.approx(94.0)
    assert taker_close["exit_effective_price"] == pytest.approx(94.0 * (1 - tax / 100.0))
    assert taker_close["broker_fee_pct"] == 0.0
    # Maker exits one tick inside the executable ask: 105.0 - 0.01, net of both.
    assert maker_close["exit_walk_price"] == pytest.approx(104.99)
    assert maker_close["exit_effective_price"] == pytest.approx(
        104.99 * (1 - (tax + broker) / 100.0)
    )
    assert maker_close["fill_assumed"] is True


def test_an_operator_supplied_close_price_is_evidence_not_an_assumed_fill(ledger):
    maker = _open(ledger, fill_model="maker")
    closed = ledger.close_position(
        position_id=maker["position_id"], book=book(), actual_price=101.0, now=NOW
    )
    assert closed["priced_from"] == "operator_actual_fill"
    assert closed["fill_assumed"] is False, "a price he really got is not an assumption"


def test_a_posted_exit_that_had_to_be_dumped_is_recorded_as_the_taker_it_was(ledger):
    maker = _open(ledger, fill_model="maker")
    closed = ledger.close_position(
        position_id=maker["position_id"], book=book(), fill_model="taker", now=NOW
    )
    assert closed["fill_model"] == "taker"
    assert closed["exit_walk_price"] == pytest.approx(94.0)
    assert closed["fill_assumed"] is False


# -- marks ------------------------------------------------------------------


def test_a_maker_position_marks_on_its_own_model_and_shows_liquidation_beside_it(ledger):
    maker = _open(ledger, fill_model="maker")
    marks = ledger.mark(book=book(), now=NOW)
    assert len(marks) == 1
    mark = marks[0]
    assert mark["fill_model"] == "maker"
    assert mark["mark_price"] == pytest.approx(104.99)
    # What walking out today would actually pay, always recorded beside it.
    tax = ledger.costs.sales_tax_pct
    assert mark["liquidation_net_price"] == pytest.approx(94.0 * (1 - tax / 100.0))
    assert mark["liquidation_net_isk"] < mark["unrealized_net_isk"]
    assert maker["fill_model"] == "maker"


# -- the report -------------------------------------------------------------


def test_the_two_populations_are_scored_apart_under_the_same_frozen_rule(ledger):
    for model in ("taker", "maker", "maker"):
        record = _open(ledger, fill_model=model)
        ledger.close_position(position_id=record["position_id"], book=book(), now=NOW)

    report = ledger.report(now=NOW)
    assert set(report.by_fill_model) == {"taker", "maker"}
    assert report.by_fill_model["taker"]["closed"] == 1
    assert report.by_fill_model["maker"]["closed"] == 2
    assert report.by_fill_model["maker"]["assumed_fills"] == 2
    # The frozen §12.4 thresholds are applied to each population unchanged.
    for block in report.by_fill_model.values():
        assert block["verdict"]["verdict"] == "TOO_EARLY"
    # Taker loses the whole spread here; maker is paid it. Averaging the two
    # would describe neither trade.
    assert report.by_fill_model["taker"]["cumulative_net_isk"] < 0
    assert report.by_fill_model["maker"]["cumulative_net_isk"] > 0


def test_the_rendered_report_names_the_assumption_rather_than_burying_it(ledger):
    from evescreener.paper import render_report

    record = _open(ledger, fill_model="maker")
    ledger.close_position(position_id=record["position_id"], book=book(), now=NOW)
    text = render_report(ledger.report(now=NOW))
    assert "By fill model" in text
    assert "ASSUMED fills" in text
    assert "never that anyone traded into it" in text


# -- book_quote itself ------------------------------------------------------


def test_a_maker_quote_still_refuses_a_stale_book(ledger):
    old = book(sweep=datetime(2026, 8, 19, 12, 0, tzinfo=UTC))
    quote = book_quote(old, type_id=34, side="buy", tier_index=0, now=NOW, fill_model="maker")
    assert quote.price is None
    assert "refusing the fill rather than pricing off history" in quote.reason


def test_a_maker_quote_still_refuses_a_pre_r1_snapshot(ledger):
    legacy = book().drop(columns=["exec_location_id", "exec_price", "exec_is_structure"])
    quote = book_quote(legacy, type_id=34, side="buy", tier_index=0, now=NOW, fill_model="maker")
    assert quote.price is None
    assert "predates the executable-quote contract" in quote.reason


def test_a_maker_quote_needs_something_to_post_in_front_of(ledger):
    frame = book()
    frame.loc[frame["side"] == "buy", "exec_price"] = None
    quote = book_quote(frame, type_id=34, side="buy", tier_index=0, now=NOW, fill_model="maker")
    assert quote.price is None
    assert "no executable buy-side quote to post in front of" in quote.reason


def test_an_unknown_fill_model_is_a_programming_error_not_a_silent_taker():
    with pytest.raises(ValueError, match="fill_model must be one of"):
        book_quote(book(), type_id=34, side="sell", tier_index=0, now=NOW, fill_model="mid")


# -- ledger integrity -------------------------------------------------------


def test_two_entries_in_the_same_second_are_two_positions_not_one(ledger):
    """Ids were `{type_id}-{second}`, so the second entry overwrote the first.

    The record survived on disk — the ledger is append-only — but `positions()`
    replayed it into the same key, so one position vanished from the tally,
    could never be closed, and its notional was silently missing from every
    number the verdict tracker reads.
    """
    first = _open(ledger, fill_model="taker")
    second = _open(ledger, fill_model="maker")
    assert first["position_id"] != second["position_id"]

    positions = ledger.positions()
    assert len(positions) == 2
    assert {position["fill_model"] for position in positions.values()} == {"taker", "maker"}
    # Both are closeable, by their own ids.
    for position_id in positions:
        ledger.close_position(position_id=position_id, book=book(), now=NOW)
    assert len(ledger.report(now=NOW).closed) == 2


def test_a_legacy_colliding_id_is_recovered_on_read_rather_than_dropped(ledger, paths):
    """Ledgers written before the fix still hold the shadowed position."""
    from evescreener.paths import append_jsonl

    _open(ledger)
    records = ledger.records()
    shadowed = {**records[-1], "type_name": "Shadowed"}
    append_jsonl(paths.paper_ledger, [shadowed])

    positions = ledger.positions()
    assert len(positions) == 2, "nothing on disk is lost"
    recovered = [p for p in positions.values() if p.get("duplicate_id")]
    assert len(recovered) == 1
    assert recovered[0]["type_name"] == "Shadowed"
    assert recovered[0]["position_id"].endswith("#2")
