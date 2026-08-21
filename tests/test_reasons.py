"""Qualified reasons, both directions (§19 Amendment 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from evescreener.paper import PaperLedger, Refusal
from evescreener.reasons import (
    DISLIKE,
    LIKE,
    ReasonError,
    ReasonVocabulary,
    load_reasons,
    normalise_tags,
)

REPO_REASONS = Path(__file__).resolve().parents[1] / "config" / "reasons.jsonl"
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
TIER = 250_000_000.0


def book(sweep=NOW) -> pd.DataFrame:
    """The same shape the paper tests use — a book that can actually fill."""
    rows = []
    for side, best, fill in (("sell", 105.0, 106.0), ("buy", 95.0, 94.0)):
        row = {
            "type_id": 34,
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
            # R1/S2: a fixture must say where its quotes rested, or every
            # pricing path correctly refuses it.
            "best_location_id": 60003760,
            "exec_location_id": 60003760,
            "exec_price": best,
            "exec_is_structure": False,
        }
        for index in range(3):
            row[f"depth_fill_price_{index}"] = fill if index == 0 else None
            row[f"depth_fill_qty_{index}"] = 10_000_000.0 if index == 0 else None
        rows.append(row)
    return pd.DataFrame(rows)


# -- the vocabulary ---------------------------------------------------------


def test_the_committed_vocabulary_covers_both_directions():
    vocabulary = load_reasons(REPO_REASONS)
    assert len(vocabulary.likes) >= 7
    assert len(vocabulary.dislikes) >= 6
    tags = {reason.tag for reason in vocabulary.reasons}
    assert {"level_confluence", "spread_too_wide", "too_thin"} <= tags
    assert all(reason.label for reason in vocabulary.reasons)


def test_a_malformed_reason_names_its_line(tmp_path):
    path = tmp_path / "reasons.jsonl"
    path.write_text('{"tag": "a", "direction": "like", "label": "A"}\nbroken\n', encoding="utf-8")
    with pytest.raises(ReasonError, match=r"reasons\.jsonl:2"):
        load_reasons(path)


def test_an_unknown_direction_is_refused(tmp_path):
    path = tmp_path / "reasons.jsonl"
    path.write_text('{"tag": "a", "direction": "maybe", "label": "A"}\n', encoding="utf-8")
    with pytest.raises(ReasonError, match="direction must be"):
        load_reasons(path)


def test_a_duplicate_tag_in_one_direction_is_refused(tmp_path):
    path = tmp_path / "reasons.jsonl"
    line = '{"tag": "a", "direction": "like", "label": "A"}'
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(ReasonError, match="duplicate like tag"):
        load_reasons(path)


def test_the_same_tag_may_exist_in_both_directions(tmp_path):
    path = tmp_path / "reasons.jsonl"
    path.write_text(
        '{"tag": "volume", "direction": "like", "label": "volume there"}\n'
        '{"tag": "volume", "direction": "dislike", "label": "volume gone"}\n',
        encoding="utf-8",
    )
    vocabulary = load_reasons(path)
    assert vocabulary.tags(LIKE) == {"volume"}
    assert vocabulary.tags(DISLIKE) == {"volume"}


def test_a_typod_tag_is_a_loud_error_not_a_dropped_one():
    """A decision recorded with a typo'd reason is a reason never measured."""
    vocabulary = load_reasons(REPO_REASONS)
    with pytest.raises(ReasonError, match="unknown like tag"):
        normalise_tags(["level_confluance"], vocabulary, LIKE)


def test_a_like_tag_is_not_accepted_as_a_dislike():
    vocabulary = load_reasons(REPO_REASONS)
    with pytest.raises(ReasonError, match="unknown dislike tag"):
        normalise_tags(["level_confluence"], vocabulary, DISLIKE)


def test_tags_are_cleaned_and_deduplicated():
    vocabulary = load_reasons(REPO_REASONS)
    assert normalise_tags([" Level Confluence ", "level_confluence", ""], vocabulary, LIKE) == (
        "level_confluence",
    )


def test_with_no_vocabulary_tags_pass_through_cleaned():
    empty = ReasonVocabulary(reasons=())
    assert normalise_tags(["Made Up"], empty, LIKE) == ("made_up",)


# -- the ledger requires them, both ways ------------------------------------


@pytest.fixture
def ledger(tmp_path, config):
    return PaperLedger(tmp_path / "paper.jsonl", config)


def test_an_open_without_like_tags_is_refused_and_the_refusal_is_recorded(ledger):
    with pytest.raises(Refusal, match="at least one 'why I like it' tag"):
        ledger.open_position(
            type_id=34,
            type_name="Tritanium",
            notional_isk=TIER,
            book=book(),
            thesis="dip",
            setup_tag="discretionary",
            like_tags=[],
            now=NOW,
        )
    refusals = ledger.refusals()
    assert len(refusals) == 1
    assert refusals[0]["action"] == "open"


def test_an_open_without_a_setup_tag_is_refused(ledger):
    with pytest.raises(Refusal, match="needs a setup tag"):
        ledger.open_position(
            type_id=34,
            type_name="Tritanium",
            notional_isk=TIER,
            book=book(),
            thesis="dip",
            setup_tag="",
            like_tags=["clean_dip_below_value"],
            now=NOW,
        )


def test_an_open_without_a_thesis_is_refused(ledger):
    with pytest.raises(Refusal, match="thesis sentence"):
        ledger.open_position(
            type_id=34,
            type_name="Tritanium",
            notional_isk=TIER,
            book=book(),
            thesis="   ",
            setup_tag="discretionary",
            like_tags=["clean_dip_below_value"],
            now=NOW,
        )


def test_a_qualified_open_carries_its_reasons_into_the_ledger(ledger):
    record = ledger.open_position(
        type_id=34,
        type_name="Tritanium",
        notional_isk=TIER,
        book=book(),
        thesis="dip below anchored value",
        setup_tag="Dip into value, strength intact",
        like_tags=["clean_dip_below_value", "rrs_strong"],
        reason_text="cheapest it has been since the patch",
        now=NOW,
        vocabulary=load_reasons(REPO_REASONS),
    )
    assert record["setup_tag"] == "Dip into value, strength intact"
    assert record["like_tags"] == ["clean_dip_below_value", "rrs_strong"]
    assert record["reason_text"].startswith("cheapest")


def test_a_pass_without_dislike_tags_is_refused_with_the_same_rigour(ledger):
    with pytest.raises(Refusal, match="at least one 'why I don't like it' tag"):
        ledger.record_pass(
            type_id=34, type_name="Tritanium", action="not_today", dislike_tags=[], now=NOW
        )
    assert ledger.refusals()[0]["action"] == "not_today"


def test_a_qualified_pass_is_a_recorded_decision_in_the_same_ledger(ledger):
    record = ledger.record_pass(
        type_id=34,
        type_name="Tritanium",
        action="not_today",
        dislike_tags=["spread_too_wide", "too_thin"],
        reason_text="not at this haircut",
        setup_tag="Cloud reclaim",
        close=5.0,
        now=NOW,
        vocabulary=load_reasons(REPO_REASONS),
    )
    assert record["event"] == "pass"
    assert record["dislike_tags"] == ["spread_too_wide", "too_thin"]
    assert ledger.passes() == [record]
    # One ledger, two doors: the pass lives beside the opens.
    assert record in ledger.records()


def test_an_unknown_pass_action_is_refused(ledger):
    with pytest.raises(Refusal, match="pass action must be"):
        ledger.record_pass(
            type_id=34,
            type_name="X",
            action="never_ever",
            dislike_tags=["too_thin"],
            now=NOW,
        )


def test_a_pass_never_touches_the_watchlist(ledger, db):
    """'Not today' clears today's queue only — Focus names are never auto-removed."""
    from evescreener.universe import add_watch, watchlist_entries

    db.replace_types([(34, "Tritanium", 1857, 0.01, 0.01, 1)])
    add_watch(db, name="Tritanium", type_id=34)
    ledger.record_pass(
        type_id=34, type_name="Tritanium", action="not_today", dislike_tags=["too_thin"], now=NOW
    )
    assert [row["name"] for row in watchlist_entries(db)] == ["Tritanium"]
