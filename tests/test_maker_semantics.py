"""R4 — a quoted margin is not an expected edge.

Four claims that outran their evidence:

**"net edge"** named a number with no execution model behind it. Queue
position, fill probability, waiting time, undercut risk and relists are all
absent, and every one of them is a cost. A positive value here is what the
book is *quoting*, not what the operator would *keep*.

**The 0.5x / 2x guards were called derived.** §17 D-31 said they came from
measurement. The measurement counted how many observations fell beyond
cutoffs that had already been chosen — which describes the cutoffs, it does
not derive them. They are operator heuristics and must say so.

**One broker rate was applied to every hub.** Broker fee varies with standings,
which are per-corporation and therefore per-station.

**`relist_cost` was not the game's order-change formula**, and a wrong cost
model is worse than an absent one because it looks answered.
"""

from __future__ import annotations

import numpy as np
import pytest

from evescreener.books import reduce_orders
from evescreener.costs import CostModel
from evescreener.spreads import (
    DEFAULT_MAX_ASK_VS_AVG,
    DEFAULT_MIN_BID_VS_AVG,
    GUARD_PROVENANCE,
    maker_edge_frame,
)

JITA_44 = 60003760
TIERS = (250_000_000.0, 1_000_000_000.0, 2_500_000_000.0)


def _book(rows):
    orders = []
    order_id = 0
    for type_id, bid, ask in rows:
        for is_buy, price in ((False, ask), (True, bid)):
            for offset, volume in ((0.0, 400.0), (0.01, 100.0)):
                order_id += 1
                record = {
                    "order_id": order_id,
                    "type_id": int(type_id),
                    "price": float(price) + (-offset if is_buy else offset),
                    "volume_remain": volume,
                    "is_buy_order": is_buy,
                    "location_id": JITA_44,
                }
                if is_buy:
                    record["range"] = "station"
                orders.append(record)
    return reduce_orders(orders, region_id=10000002, notional_tiers=TIERS).frame


@pytest.fixture
def costs(config) -> CostModel:
    return CostModel.from_config(config)


# -- 1. the number says what it is ------------------------------------------


def test_the_maker_column_is_a_quoted_margin_not_an_edge(costs):
    """No execution model stands behind it, so it cannot be called an edge."""
    frame = maker_edge_frame(
        _book([(1, 100.0, 130.0)]), costs, averages={1: 110.0}, volumes={1: 5000.0}
    )
    assert "quoted_margin_pct" in frame.columns
    assert "net_pct" not in frame.columns, (
        "'net' implies costs have been netted out; fill risk has not been"
    )
    assert "expected" not in " ".join(frame.columns)


def test_the_unmodelled_costs_are_named_on_every_row(costs):
    """Omissions must be impossible to mistake for a modelled zero (§21 R4)."""
    frame = maker_edge_frame(
        _book([(1, 100.0, 130.0)]), costs, averages={1: 110.0}, volumes={1: 5000.0}
    )
    row = frame.iloc[0]
    assert row["execution_model"] == "none"
    unmodelled = row["unmodelled_costs"]
    for cost in ("queue position", "fill probability", "waiting time", "undercut", "relist"):
        assert cost in unmodelled


def test_the_page_caveat_refuses_the_word_edge():
    from evescreener.gui.pages.spreads import CAVEAT, HEADERS

    assert "quoted margin" in CAVEAT.lower()
    assert "before execution risk" in CAVEAT.lower()
    assert any("quoted margin" in header.lower() for header in HEADERS)
    assert not any(header.strip().lower() in {"net %", "edge %"} for header in HEADERS)


# -- 2. the guards are heuristics and say so --------------------------------


def test_the_guard_thresholds_are_labelled_operator_heuristics():
    """§17 D-31 called them derived. Counting past a chosen cutoff is not
    deriving it (§21 R4)."""
    assert DEFAULT_MIN_BID_VS_AVG == 0.50
    assert DEFAULT_MAX_ASK_VS_AVG == 2.00
    text = GUARD_PROVENANCE.lower()
    assert "heuristic" in text
    assert "not derived" in text or "does not derive" in text
    assert "out-of-sample" in text or "outcome" in text


def test_the_guards_are_still_the_same_numbers_so_no_measurement_moves():
    """Relabelled, not retuned: D-31's counts remain reproducible."""
    from evescreener import spreads

    assert spreads.DEFAULT_MIN_BID_VS_AVG == 0.50
    assert spreads.DEFAULT_MAX_ASK_VS_AVG == 2.00


# -- 3. a stale anchor cannot bless a row -----------------------------------


def test_a_stale_traded_average_cannot_classify_a_row_ok(costs):
    """The anchor is what makes DUST_BID decidable; a stale one decides nothing."""
    frame = maker_edge_frame(
        _book([(1, 100.0, 130.0)]),
        costs,
        averages={1: 110.0},
        volumes={1: 5000.0},
        average_is_stale=True,
    )
    row = frame.iloc[0]
    assert row["state"] == "STALE_AVG"
    assert not np.isfinite(row["quoted_margin_pct"])


def test_a_fresh_anchor_still_classifies_normally(costs):
    frame = maker_edge_frame(
        _book([(1, 100.0, 130.0)]), costs, averages={1: 110.0}, volumes={1: 5000.0}
    )
    assert frame.iloc[0]["state"] == "OK"


# -- 4. broker rates are per hub --------------------------------------------


def test_broker_fee_can_differ_by_station(config):
    """Standings are per-corporation, so the fee is per-station (§21 R4)."""
    costs = CostModel.from_config(config)
    base = costs.broker_fee_pct
    at_jita = costs.broker_fee_at(JITA_44)
    assert at_jita == base, "no override configured means the base rate"

    tuned = costs.with_broker_overrides({JITA_44: 0.9})
    assert tuned.broker_fee_at(JITA_44) == pytest.approx(0.9)
    assert tuned.broker_fee_at(60003761) == pytest.approx(base)
    # The override is an operator-observed effective rate, not a derivation.
    assert "observed" in CostModel.with_broker_overrides.__doc__.lower()


def test_a_location_specific_fee_changes_the_quoted_margin(config):
    costs = CostModel.from_config(config)
    cheap = costs.with_broker_overrides({JITA_44: 0.1})
    book = _book([(1, 100.0, 130.0)])
    base_row = maker_edge_frame(book, costs, averages={1: 110.0}, volumes={1: 5000.0}).iloc[0]
    cheap_row = maker_edge_frame(book, cheap, averages={1: 110.0}, volumes={1: 5000.0}).iloc[0]
    assert cheap_row["quoted_margin_pct"] > base_row["quoted_margin_pct"]


# -- 5. relist cost is withdrawn until it can be verified -------------------


def test_relist_cost_is_not_consumed_by_any_analytical_path():
    """A wrong cost model is worse than an absent one: it looks answered."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "evescreener"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if "relist_cost" in line and "def " not in line and not line.strip().startswith("#"):
                if path.name == "costs.py":
                    continue
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"relist cost is unverified and must not be consumed: {offenders}"


def test_relist_cost_names_its_own_uncertainty():
    from evescreener.costs import CostModel

    doc = (CostModel.relist_cost_unverified.__doc__ or "").lower()
    assert "unverified" in doc
    assert "old" in doc and "new" in doc, "the game charges on the price change"
    assert not hasattr(CostModel, "relist_cost"), "the misleading single-price form is gone"


def test_relist_cost_uses_the_price_change_not_the_whole_order(config):
    costs = CostModel.from_config(config)
    small = costs.relist_cost_unverified(old_price=100.0, new_price=101.0, quantity=10)
    large = costs.relist_cost_unverified(old_price=100.0, new_price=150.0, quantity=10)
    assert 0.0 < small < large
    assert costs.relist_cost_unverified(old_price=100.0, new_price=100.0, quantity=10) == 0.0
