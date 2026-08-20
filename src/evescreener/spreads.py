"""Maker spreads — the station-trading read (plan.md §20.2, §17 D-31).

**This measures the opposite side of the trade the backtest rejected.**

§17's NOT PLAUSIBLE verdict was measured on a *taker*: cross the spread to get
in, cross it again to get out, and 14.7% of round-trip friction swallows a
+2.80% gross edge. A maker does the reverse — posts a buy order, waits, posts
a sell order, waits — and **collects** that spread instead of paying it. The
98.8% median Forge spread that killed the taker is the maker's revenue line.

So this is not a re-run of a rejected idea. It is the same measured book read
from the other side, and nothing here contradicts §17: both readings are true
at once, because they are prices paid by opposite participants.

**The dust bid is the failure mode, and it was measured.** Ranking a swept
book by raw spread produces garbage: a 0.02 ISK bid against a 129,000 ISK ask
reads as a 608,000,000% edge, and nothing will ever sell into that bid. On the
Forge book, of 16,381 types with both a two-sided book and a traded average:

* **39.7%** have a best bid under **half** the traded average — 19.8% under a
  tenth of it, 9.3% under a hundredth;
* **23.6%** have a best ask above **twice** the traded average.

So every row is anchored to the **traded average** (the ESI daily mean, the
one price we know transactions actually happened at). `bid_vs_avg` and
`ask_vs_avg` say where each side of the book sits relative to real trading,
and a book whose bid is nowhere near it is flagged `DUST_BID` rather than
ranked. With the default guards — bid at least half the average, ask at most
twice it, at least 100 units/day — 2,230 Forge names survive, 1,590 of them
with a positive net maker edge, median **+13.0%** and p90 **+57.3%**.

**What this still does not know, and says so.** The lake has never measured
whether a posted order *fills*. Every ISK of this edge is contingent on two
events the data is silent about:

* **being undercut** — another trader can post 0.01 ISK inside your order for
  a fraction of your capital, and the only defence is relisting, which costs a
  broker fee every time (`CostModel.relist_cost`);
* **waiting** — a spread is realised only when *both* sides fill, and nothing
  here bounds how long that takes, or whether it happens at all.

Neither is modelled. `fill_note` says which one binds hardest for that name.
Volume, top-of-book depth and the top order's share of volume are the only
fill evidence available, and they are reported as evidence, never as a
probability.

**Staleness is not priced.** A book older than `costs.book_staleness_minutes`
yields rows whose priced columns are UNKNOWN, because a stale quote is not a
cheap quote — it is an unmeasured one (§4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .books import load_validated_book, spread_view
from .config import Config
from .costs import CostModel
from .store.lake import BookLake
from .timeutil import utcnow

__all__ = [
    "COLUMNS",
    "DEFAULT_MAX_ASK_VS_AVG",
    "DEFAULT_MIN_BID_VS_AVG",
    "DEFAULT_MIN_UNITS",
    "HubSpreads",
    "filter_rows",
    "hub_choices",
    "maker_edge_frame",
    "maker_spreads",
]

#: Columns the page renders, in order.
COLUMNS = [
    "type_id",
    "name",
    "hub",
    "best_bid",
    "avg",
    "best_ask",
    "net_pct",
    "net_isk",
    "bid_vs_avg",
    "ask_vs_avg",
    "median_units",
    "bid_depth",
    "fill_note",
    "state",
]

ALL_HUBS = "All hubs"

# Operator-facing defaults, derived from the measurement in the module
# docstring rather than chosen. They are page controls, not constants: the
# page shows them and lets them move, because a hidden filter is a hidden
# opinion.
DEFAULT_MIN_UNITS = 100.0
DEFAULT_MIN_BID_VS_AVG = 0.50
DEFAULT_MAX_ASK_VS_AVG = 2.00


def hub_choices(config: Config) -> list[tuple[str, tuple[int, ...]]]:
    """The dropdown: every configured hub, then an all-hubs entry.

    Driven by `[freight].hub_systems`, which is the one place the hub list is
    already declared. A hub with no sweep on disk still appears — it reports
    that it has no book, which is information. Omitting it would read as a hub
    with no spreads, which is a different and false claim.
    """
    hubs = [
        (str(entry.get("system", entry.get("region_id"))), (int(entry["region_id"]),))
        for entry in config.freight.hub_systems
    ]
    if len(hubs) > 1:
        hubs.append((ALL_HUBS, tuple(region for _name, (region,) in hubs)))
    return hubs


@dataclass(slots=True)
class HubSpreads:
    """One hub's read, with the age of the book it was priced against."""

    region_id: int
    hub: str
    rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    sweep_ts: str | None = None
    age_minutes: float | None = None
    stale: bool = True
    note: str = ""

    @property
    def known(self) -> bool:
        return not self.rows.empty and not self.stale


def _fill_note(median_units, top_share) -> str:
    """Which unmodelled risk binds hardest for this name."""
    if not np.isfinite(median_units) or median_units <= 0:
        return "no volume measured — fill UNKNOWN"
    if median_units < 100:
        return "under 100 units/day — waiting risk"
    if np.isfinite(top_share) and top_share > 0.5:
        return "one order is most of the book — undercut risk"
    return "undercut risk unmodelled"


def _state(net_pct, bid_vs_avg, ask_vs_avg) -> str:
    """The row's own verdict on whether it is worth reading.

    Ordered by which problem is disqualifying first. A dust bid is checked
    before the arithmetic because an enormous `net_pct` computed off a 0.02
    ISK bid is not a large edge, it is a meaningless one.
    """
    if not np.isfinite(bid_vs_avg) or not np.isfinite(ask_vs_avg):
        return "NO_AVG"
    if bid_vs_avg < DEFAULT_MIN_BID_VS_AVG:
        return "DUST_BID"
    if ask_vs_avg > DEFAULT_MAX_ASK_VS_AVG:
        return "WIDE_ASK"
    if not np.isfinite(net_pct):
        return "UNKNOWN"
    return "OK"


def maker_edge_frame(
    book: pd.DataFrame,
    costs: CostModel,
    *,
    names: dict[int, str] | None = None,
    volumes: dict[int, float] | None = None,
    averages: dict[int, float] | None = None,
    hub: str = "",
    stale: bool = False,
) -> pd.DataFrame:
    """Price a swept book from the maker's side. Pure; no I/O.

    `net_pct` is the return on the ISK actually committed — the bid *plus* the
    broker fee paid to post it. Not the mid, and not the bare bid: a maker who
    measures against the mid is counting half the spread twice.
    """
    view = spread_view(book)
    if view.empty:
        return pd.DataFrame(columns=COLUMNS)
    names = names or {}
    volumes = volumes or {}
    averages = averages or {}

    bid = pd.to_numeric(view["best_bid"], errors="coerce")
    ask = pd.to_numeric(view["best_ask"], errors="coerce")
    outlay = bid.map(lambda value: costs.buy_outlay(value, maker=True))
    proceeds = ask.map(lambda value: costs.sell_proceeds(value, maker=True))
    net_isk = proceeds - outlay
    net_pct = np.where(outlay > 0, net_isk / outlay * 100.0, np.nan)

    frame = pd.DataFrame({"type_id": view["type_id"].astype(int)})
    frame["name"] = frame["type_id"].map(lambda tid: names.get(int(tid), f"type {int(tid)}"))
    frame["hub"] = hub
    frame["best_bid"] = bid
    frame["best_ask"] = ask

    # The traded average is the reality anchor: the one price we know
    # transactions actually happened at. Without it a book cannot be judged.
    avg = frame["type_id"].map(lambda tid: averages.get(int(tid), np.nan))
    avg = pd.to_numeric(avg, errors="coerce")
    avg = avg.where(avg > 0)
    frame["avg"] = avg
    frame["bid_vs_avg"] = bid / avg
    frame["ask_vs_avg"] = ask / avg

    frame["median_units"] = frame["type_id"].map(lambda tid: volumes.get(int(tid), np.nan))
    frame["bid_depth"] = pd.to_numeric(view.get("bid_qty_0"), errors="coerce")
    top_share = pd.to_numeric(view.get("ask_top_share"), errors="coerce")

    if stale:
        # A stale quote is not a cheap quote, it is an unmeasured one (§4).
        frame["net_pct"] = np.nan
        frame["net_isk"] = np.nan
        frame["state"] = "STALE"
        frame["fill_note"] = "book too old to price"
    else:
        frame["net_pct"] = net_pct
        frame["net_isk"] = net_isk
        frame["state"] = [
            _state(net, low, high)
            for net, low, high in zip(
                net_pct, frame["bid_vs_avg"], frame["ask_vs_avg"], strict=False
            )
        ]
        frame["fill_note"] = [
            _fill_note(units, share)
            for units, share in zip(frame["median_units"], top_share, strict=False)
        ]
    return frame[COLUMNS]


def filter_rows(
    frame: pd.DataFrame,
    *,
    min_units: float = DEFAULT_MIN_UNITS,
    only_ok: bool = True,
    positive_only: bool = True,
) -> pd.DataFrame:
    """Apply the operator's visible thresholds and sort by net edge.

    Rows are *excluded*, never silently repaired. Turning `only_ok` off shows
    the dust bids and fantasy asks with their flags intact, which is how the
    operator checks that the guard is doing what it claims.
    """
    if frame.empty:
        return frame
    out = frame
    if only_ok:
        out = out[out["state"] == "OK"]
    if min_units > 0:
        units = pd.to_numeric(out["median_units"], errors="coerce")
        out = out[units.notna() & (units >= min_units)]
    if positive_only:
        net = pd.to_numeric(out["net_pct"], errors="coerce")
        out = out[net.notna() & (net > 0)]
    return out.sort_values("net_pct", ascending=False, na_position="last")


def maker_spreads(
    config: Config,
    region_ids,
    *,
    names: dict[int, str] | None = None,
    volumes_by_region: dict[int, dict[int, float]] | None = None,
    averages_by_region: dict[int, dict[int, float]] | None = None,
    lake: BookLake | None = None,
    now=None,
) -> list[HubSpreads]:
    """One `HubSpreads` per requested region. A missing book is reported, never raised.

    Volumes and traded averages are keyed **by region**, deliberately. Amarr's
    book judged against Jita's traded average would be a quiet lie, and the
    census has only ever run on the home region — so a hub with no bars gets
    an empty average map and every one of its rows reports NO_AVG, which is
    the true answer.
    """
    lake = lake or BookLake(config.paths)
    now = now or utcnow()
    costs = CostModel.from_config(config)
    hubs = {
        int(entry["region_id"]): str(entry.get("system", ""))
        for entry in config.freight.hub_systems
    }

    out: list[HubSpreads] = []
    for raw in region_ids:
        region_id = int(raw)
        hub = hubs.get(region_id, str(region_id))
        # One contract decides completeness, executability and staleness, so
        # this page cannot forget one of the three (§21 R1).
        snapshot = load_validated_book(config, region_id, lake=lake, now=now)
        if snapshot.frame.empty:
            out.append(HubSpreads(region_id=region_id, hub=hub, note=snapshot.reason))
            continue
        rows = maker_edge_frame(
            snapshot.frame,
            costs,
            names=names,
            volumes=(volumes_by_region or {}).get(region_id, {}),
            averages=(averages_by_region or {}).get(region_id, {}),
            hub=hub,
            stale=not snapshot.known,
        )
        out.append(
            HubSpreads(
                region_id=region_id,
                hub=hub,
                rows=rows,
                sweep_ts=snapshot.sweep_ts,
                age_minutes=snapshot.age_minutes,
                stale=not snapshot.known,
                note=snapshot.reason,
            )
        )
    return out
