"""The audit artefact, the CLI, and the paper-haul ledger (plan.md §23.12).

A scan on the desk is a view; the report is the record. What it has to survive
is being read six months later without the lake: the profile it was ranked for,
both generations per row, the SDE build behind the routes, the calc version,
which levels each walk consumed, and the full histogram of what was rejected.

The ledger is the §19.4 discipline applied to hauls: a thesis and a like tag to
open, a dislike tag to pass, and — the part §22 S7 found missing in `paper.py`
— the **refusal itself is written down**, because the decision made wrongly is
exactly the one worth keeping.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from evescreener.hauling import HaulProfile, HaulScan, Rejection, ShipProfile
from evescreener.haulledger import HaulLedger, HaulRefusal, render_haul_tally
from evescreener.haulreport import (
    CALC_VERSION,
    build_haul_report,
    latest_haul_report,
    render_haul_report,
    write_haul_report,
)
from evescreener.reasons import ReasonVocabulary, load_reasons

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _profile_for(config) -> HaulProfile:
    return HaulProfile(
        current_system=30000142,
        ship=ShipProfile(name="test", usable_cargo_m3=60_000.0),
        capital_isk=250_000_000.0,
        max_exposure_isk=250_000_000.0,
    )


def _empty_scan(config) -> HaulScan:
    scan = HaulScan(generated_at="2026-08-25T12:00:00+00:00", profile=_profile_for(config))
    scan.pairs_considered = 20
    scan.rejected.append(
        Rejection(reason="STALE_BOOK", source_station=60003760, dest_station=60008494, detail="old")
    )
    scan.unknown_pairs.append(
        {
            "source": {"label": "Jita — station 60003760"},
            "destination": {"label": "Amarr — station 60008494"},
            "reason": "depth 400 min old — STALE",
            "state": "STALE_BOOK",
        }
    )
    return scan


# -- 1. the report ---------------------------------------------------------


def test_the_report_carries_everything_needed_to_re_derive_it(config):
    report = build_haul_report(_empty_scan(config), config=config)
    assert report["calc_version"] == CALC_VERSION
    assert report["profile"]["ship"]["usable_cargo_m3"] == 60_000.0
    assert report["rejection_counts"] == {"STALE_BOOK": 1}
    assert report["counts"]["pairs_considered"] == 20
    assert (
        report["assumptions"]["destination_share_prior"] == config.hauling.destination_share_prior
    )
    assert "LABELLED ASSUMPTIONS" in report["assumptions"]["note"]
    assert any("snapshot" in caveat.lower() for caveat in report["caveats"])


def test_an_empty_scan_renders_an_honest_zero_with_its_denominator(config):
    text = render_haul_report(build_haul_report(_empty_scan(config), config=config))
    assert "Nothing clears costs today" in text
    assert "STALE_BOOK" in text
    assert "station pairs considered: 20" in text
    assert "Pairs that priced nothing" in text


def test_the_report_writes_two_files_atomically_and_survives_a_republish(config):
    report = build_haul_report(_empty_scan(config), config=config)
    json_path, md_path = write_haul_report(config, report)
    assert json_path.exists() and md_path.exists()
    assert ":" not in json_path.name, "a Windows desktop cannot hold a colon in a filename"
    stored = json.loads(json_path.read_text(encoding="utf-8"))
    assert stored["calc_version"] == CALC_VERSION
    assert latest_haul_report(config.paths) == json_path


def test_a_stored_report_is_never_edited_only_added_to(config):
    first = build_haul_report(_empty_scan(config), config=config)
    write_haul_report(config, first)
    second = dict(first, generated_at="2026-08-25T13:00:00+00:00")
    write_haul_report(config, second)
    stored = sorted(config.paths.reports.glob("hauling-*.json"))
    assert len(stored) == 2, "a new scan is a new file, never an edit of the old one"


# -- 2. the ledger ---------------------------------------------------------


@pytest.fixture
def vocabulary(repo_root) -> ReasonVocabulary:
    return load_reasons(repo_root / "config" / "reasons.jsonl")


@pytest.fixture
def ledger(config) -> HaulLedger:
    return HaulLedger(config.paths.ensure().paper_hauls, config)


def _open(ledger, vocabulary, **overrides):
    payload = {
        "type_id": 34,
        "type_name": "Tritanium",
        "quantity": 1200.0,
        "source_station": 60003760,
        "dest_station": 60008494,
        "thesis": "the Amarr bid is 15% over the Jita ask and I fly it anyway",
        "like_tags": [vocabulary.likes[0].tag],
        "expected_cost_isk": 122_900_000.0,
        "expected_net_isk": 13_196_312.5,
        "vocabulary": vocabulary,
        "now": NOW,
    }
    payload.update(overrides)
    return ledger.record_open(**payload)


def test_a_haul_is_recorded_with_its_reasons(ledger, vocabulary):
    record = _open(ledger, vocabulary)
    assert record["event"] == "open"
    assert record["like_tags"] == [vocabulary.likes[0].tag]
    assert record["expected_net_isk"] == 13_196_312.5
    assert ledger.tally().opened == 1


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"thesis": "  "}, "thesis"),
        ({"like_tags": []}, "like"),
        ({"quantity": 0.0}, "no units"),
    ],
)
def test_an_unqualified_haul_is_refused_and_the_refusal_is_the_record(
    ledger, vocabulary, overrides, fragment
):
    with pytest.raises(HaulRefusal, match=fragment):
        _open(ledger, vocabulary, **overrides)
    refusals = ledger.refusals()
    assert len(refusals) == 1
    assert refusals[0]["action"] == "open"
    assert ledger.tally().opened == 0, "a refused haul is not a haul"


def test_an_unknown_tag_is_refused_and_still_recorded(ledger, vocabulary):
    """§22 S7's defect, not repeated: validation must not raise before the
    refusal is written, or the ledger loses the decisions made wrongly."""
    with pytest.raises(HaulRefusal, match="unknown like tag"):
        _open(ledger, vocabulary, like_tags=["definitely_not_a_tag"])
    refusal = ledger.refusals()[0]
    assert refusal["attempted_like_tags"] == ["definitely_not_a_tag"]


def test_a_pass_needs_a_dislike_tag_and_is_a_record_in_its_own_right(ledger, vocabulary):
    with pytest.raises(HaulRefusal, match="dislike"):
        ledger.record_pass(
            type_id=34, type_name="Tritanium", dislike_tags=[], vocabulary=vocabulary
        )
    record = ledger.record_pass(
        type_id=34,
        type_name="Tritanium",
        dislike_tags=[vocabulary.dislikes[0].tag],
        vocabulary=vocabulary,
    )
    assert record["event"] == "pass"
    tally = ledger.tally()
    assert tally.passed == 1 and tally.refused == 1


def test_a_bad_pass_action_is_refused_before_anything_is_recorded_as_a_pass(ledger, vocabulary):
    with pytest.raises(HaulRefusal, match="pass action"):
        ledger.record_pass(
            type_id=34,
            type_name="Tritanium",
            action="shrug",
            dislike_tags=[vocabulary.dislikes[0].tag],
            vocabulary=vocabulary,
        )
    assert ledger.passes() == []
    assert ledger.refusals()[0]["action"] == "shrug"


def test_a_close_records_what_really_happened_and_the_forecast_error(ledger, vocabulary):
    """**Amended 2026-08-25.** This test previously asserted that proceeds
    alone produced a `realized_net_isk` — by borrowing the forecast cost. That
    is the defect, not the contract: a realized number needs both sides
    actual, and the forecast error needs a realized number."""
    opened = _open(ledger, vocabulary)
    close = ledger.record_close(
        haul_id=opened["haul_id"],
        actual_proceeds_isk=130_000_000.0,
        actual_cost_isk=122_900_000.0,
        note="sold into a thinner bid",
    )
    assert close["realized_net_isk"] == pytest.approx(130_000_000.0 - 122_900_000.0)
    assert close["forecast_error_isk"] == pytest.approx(close["realized_net_isk"] - 13_196_312.5)
    tally = ledger.tally()
    assert tally.closed == 1 and tally.open_ids == ()


def test_a_close_with_no_numbers_is_unresolved_rather_than_assumed_to_have_worked(
    ledger, vocabulary
):
    opened = _open(ledger, vocabulary)
    ledger.record_close(haul_id=opened["haul_id"])
    tally = ledger.tally()
    assert tally.unresolved_closes == 1
    assert tally.realized_net_isk is None
    assert "UNKNOWN" in render_haul_tally(tally)


def test_closing_twice_or_closing_nothing_is_refused(ledger, vocabulary):
    opened = _open(ledger, vocabulary)
    ledger.record_close(haul_id=opened["haul_id"], actual_proceeds_isk=1.0)
    with pytest.raises(HaulRefusal, match="already closed"):
        ledger.record_close(haul_id=opened["haul_id"])
    with pytest.raises(HaulRefusal, match="no open haul"):
        ledger.record_close(haul_id="nope")


def test_the_tally_leads_with_refusals(ledger, vocabulary):
    with pytest.raises(HaulRefusal):
        _open(ledger, vocabulary, like_tags=[])
    text = render_haul_tally(ledger.tally())
    assert text.index("refused") < text.index("opened")


def test_two_hauls_in_the_same_second_stay_two_hauls(ledger, vocabulary):
    """The `paper.py` collision of 2026-08-21, not repeated here."""
    first = _open(ledger, vocabulary)
    second = _open(ledger, vocabulary)
    assert first["haul_id"] != second["haul_id"]
    assert len(ledger.hauls()) == 2


# -- 3. an actual is an actual -------------------------------------------


def test_a_close_with_no_cost_does_not_borrow_the_forecast(ledger, vocabulary):
    """This ledger is the path by which §23.7's priors become measurements.

    Borrowing `expected_cost_isk`, storing it as `actual_cost_isk`, and then
    computing a "realized" net and a "forecast error" from it makes the
    forecast grade its own homework — and counts the close as resolved while
    doing it.
    """
    opened = _open(ledger, vocabulary)
    close = ledger.record_close(haul_id=opened["haul_id"], actual_proceeds_isk=130_000_000.0)
    assert close["actual_cost_isk"] is None, "the operator did not say what he paid"
    assert close["cost_source"] == "expected"
    assert close["realized_net_isk"] is None
    assert close["forecast_error_isk"] is None
    # The arithmetic is still shown — labelled as resting on the forecast.
    assert close["assumed_net_isk"] == pytest.approx(130_000_000.0 - 122_900_000.0)

    tally = ledger.tally()
    assert tally.closed == 1
    assert tally.unresolved_closes == 1, "a half-measured close is not evidence"
    assert tally.realized_net_isk is None


def test_a_fully_actual_close_is_resolved_and_scores_the_forecast(ledger, vocabulary):
    opened = _open(ledger, vocabulary)
    close = ledger.record_close(
        haul_id=opened["haul_id"],
        actual_proceeds_isk=130_000_000.0,
        actual_cost_isk=121_000_000.0,
    )
    assert close["cost_source"] == "actual"
    assert close["realized_net_isk"] == pytest.approx(9_000_000.0)
    assert close["forecast_error_isk"] == pytest.approx(9_000_000.0 - 13_196_312.5)
    assert close["assumed_net_isk"] is None
    tally = ledger.tally()
    assert tally.unresolved_closes == 0
    assert tally.realized_net_isk == pytest.approx(9_000_000.0)


def test_a_close_with_no_numbers_at_all_records_neither(ledger, vocabulary):
    opened = _open(ledger, vocabulary)
    close = ledger.record_close(haul_id=opened["haul_id"])
    assert close["cost_source"] is None
    assert close["realized_net_isk"] is None and close["assumed_net_isk"] is None
    assert ledger.tally().unresolved_closes == 1
