"""S6 and S7 — a setting that never loads, and a refusal that never records.

**S6.** `CostModel.with_broker_overrides()` worked only in tests.
`CostModel.from_config()` always built an empty override map and
`maker_spreads()` used that untuned model, so the feature could not affect a
single production number. A test that constructs the model by hand proves the
arithmetic and nothing about whether it is reachable.

**S7.** §19.4 says the refusal itself goes in the ledger. Validation failures
raised **before** `_refuse()` was called, so an unknown tag or an invalid pass
action left no record at all — the one class of decision the ledger silently
lost was the one made wrongly.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from evescreener.books import reduce_orders
from evescreener.costs import CostModel
from evescreener.paper import PaperLedger, Refusal
from evescreener.paths import read_jsonl
from evescreener.reasons import load_reasons
from evescreener.spreads import maker_spreads
from evescreener.store.lake import BookLake


@pytest.fixture
def vocabulary(repo_root):
    """The repository's committed reason vocabulary."""
    return load_reasons(repo_root / "config" / "reasons.jsonl")


JITA = 60003760
AMARR = 60008494
TIERS = (250_000_000.0,)


# -- S6: the override must reach production ---------------------------------


def _config_with_overrides(config, entries):
    costs = dataclasses.replace(config.costs, broker_fee_overrides=tuple(entries))
    return dataclasses.replace(config, costs=costs)


def test_broker_overrides_load_from_config_not_only_from_a_test_helper(config):
    """`from_config` built an empty map, so the feature was unreachable."""
    tuned = _config_with_overrides(
        config,
        [
            {"location_id": JITA, "broker_fee_pct": 0.90},
            {"location_id": AMARR, "broker_fee_pct": 2.50},
        ],
    )
    model = CostModel.from_config(tuned)
    assert model.broker_fee_at(JITA) == pytest.approx(0.90)
    assert model.broker_fee_at(AMARR) == pytest.approx(2.50)
    # An unlisted station still uses the skill-derived base.
    assert model.broker_fee_at(60003761) == pytest.approx(model.broker_fee_pct)


def test_no_overrides_configured_is_byte_identical_to_before(config):
    model = CostModel.from_config(config)
    assert model.broker_fee_overrides == {}
    assert model.broker_fee_at(JITA) == model.broker_fee_pct


def _book_at(location, *, bid, ask, type_id=34):
    orders = []
    for index, (is_buy, price) in enumerate(((False, ask), (True, bid))):
        for offset, volume in ((0.0, 4000.0), (0.01, 1000.0)):
            record = {
                "order_id": index * 10 + int(offset * 100) + location % 97,
                "type_id": type_id,
                "price": float(price) + (-offset if is_buy else offset),
                "volume_remain": volume,
                "is_buy_order": is_buy,
                "location_id": location,
            }
            if is_buy:
                record["range"] = "station"
            orders.append(record)
    return orders


def test_two_configured_stations_produce_different_maker_margins(config, paths):
    """The integration Sol asked for: through `maker_spreads()`, not by hand."""
    lake = BookLake(config.paths)
    for region, location in ((10000002, JITA), (10000043, AMARR)):
        frame = reduce_orders(
            _book_at(location, bid=90.0, ask=110.0),
            region_id=region,
            notional_tiers=TIERS,
            sweep_ts="2026-08-20T12:00:00+00:00",
        ).frame
        lake.write(frame)

    tuned = _config_with_overrides(
        config,
        [
            {"location_id": JITA, "broker_fee_pct": 0.10},
            {"location_id": AMARR, "broker_fee_pct": 5.00},
        ],
    )
    averages = {10000002: {34: 100.0}, 10000043: {34: 100.0}}
    volumes = {10000002: {34: 5000.0}, 10000043: {34: 5000.0}}
    now = pd.Timestamp("2026-08-20T12:05:00+00:00").to_pydatetime()

    hubs = maker_spreads(
        tuned,
        [10000002, 10000043],
        names={34: "Tritanium"},
        volumes_by_region=volumes,
        averages_by_region=averages,
        now=now,
    )
    margins = {}
    for hub in hubs:
        assert not hub.rows.empty, hub.note
        margins[hub.region_id] = float(hub.rows.iloc[0]["quoted_margin_pct"])

    assert margins[10000002] > margins[10000043], (
        "the cheap-broker station must quote the better margin"
    )


# -- S7: a refusal is a record ----------------------------------------------


def _ledger(config, paths):
    return PaperLedger(config.paths.paper_ledger, config)


def _events(config):
    return [row for row in read_jsonl(config.paths.paper_ledger)]


def test_an_invalid_pass_action_is_refused_AND_recorded(config, paths, vocabulary):
    ledger = _ledger(config, paths)
    with pytest.raises(Refusal):
        ledger.record_pass(
            type_id=34,
            type_name="Tritanium",
            action="definitely_not_an_action",
            dislike_tags=["spread_too_wide"],
            vocabulary=vocabulary,
        )
    refusals = [row for row in _events(config) if row.get("event") == "refused"]
    assert refusals, "the refusal must appear in the append-only ledger (§19.4)"
    assert refusals[-1]["action"] == "definitely_not_an_action"
    assert "pass action must be one of" in refusals[-1]["reason"]


def test_an_unknown_dislike_tag_is_refused_AND_recorded(config, paths, vocabulary):
    ledger = _ledger(config, paths)
    with pytest.raises(Refusal):
        ledger.record_pass(
            type_id=34,
            type_name="Tritanium",
            action="not_today",
            dislike_tags=["not_a_real_tag"],
            vocabulary=vocabulary,
        )
    refusals = [row for row in _events(config) if row.get("event") == "refused"]
    assert refusals
    last = refusals[-1]
    assert last["action"] == "not_today"
    assert last["attempted_dislike_tags"] == ["not_a_real_tag"], (
        "what was attempted must be preserved, or the refusal teaches nothing"
    )


def test_the_decision_is_still_refused_and_the_tag_never_accepted(config, paths, vocabulary):
    """Recording the attempt must not become accepting it."""
    ledger = _ledger(config, paths)
    with pytest.raises(Refusal):
        ledger.record_pass(
            type_id=34,
            type_name="Tritanium",
            action="not_today",
            dislike_tags=["not_a_real_tag"],
            vocabulary=vocabulary,
        )
    passes = [row for row in _events(config) if row.get("event") == "pass"]
    assert not passes, "no pass may be recorded from a refused decision"


def test_a_valid_pass_still_records_a_pass_and_no_refusal(config, paths, vocabulary):
    ledger = _ledger(config, paths)
    ledger.record_pass(
        type_id=34,
        type_name="Tritanium",
        action="not_today",
        dislike_tags=[sorted(vocabulary.tags("dislike"))[0]],
        vocabulary=vocabulary,
    )
    events = _events(config)
    assert any(row.get("event") == "pass" for row in events)
    assert not any(row.get("event") == "refused" for row in events)
