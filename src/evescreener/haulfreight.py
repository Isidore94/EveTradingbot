"""Self-haul versus paying someone: the comparison column (plan.md §23, H4).

The question this answers is not "is hauling profitable" — the scan already
answers that. It is *"is flying it myself worth what PushX would charge?"*, and
the honest form of that answer is **what an hour of the operator's flying is
worth** on this particular haul.

Three rules, all of them about not letting a third party's API become
load-bearing:

* **`crossregion.quote_freight` is reused verbatim.** Same cache, same
  staleness haircut, same "a failure is UNKNOWN with its reason, never zero".
  A second quoting path would be a second thing to get wrong.
* **No quote → the comparison column reads UNKNOWN.** The self-haul row itself
  never depends on PushX: the plan is priced from swept depth and stays priced
  whether or not a third-party site answers.
* **`scan_cross_region`'s fill logic is deliberately NOT copied.** Its
  same-notional two-leg walk is known-optimistic, and `q_walk` supersedes it.
  Nor is its "needs docking rights" flag inherited: **range decides
  reachability**, not station ownership (§22 S2a), and the depth reduction has
  already applied that rule.
"""

from __future__ import annotations

from dataclasses import replace

from .config import Config

__all__ = ["attach_freight", "freight_comparison"]

NOT_QUOTED = "not quoted — run `haul scan --freight` to ask PushX"


def _hub_system(config: Config, region_id: int | None) -> str | None:
    if region_id is None:
        return None
    for entry in config.freight.hub_systems:
        if int(entry.get("region_id", 0)) == int(region_id):
            return str(entry.get("system"))
    return None


def freight_comparison(config: Config, db, plan, *, client=None, now=None, quote_fn=None) -> dict:
    """One plan's self-haul-versus-freight read. Never raises, never blocks."""
    from .crossregion import quote_freight

    quote_fn = quote_fn or quote_freight
    if not config.freight.enabled:
        return {"state": "UNKNOWN", "reason": "freight is disabled in config"}
    start = _hub_system(config, plan.source.region_id)
    end = _hub_system(config, plan.destination.region_id)
    if not start or not end:
        return {
            "state": "UNKNOWN",
            "reason": "no hub system configured for one end of this route — not guessed",
        }
    if not plan.cargo_m3:
        return {"state": "UNKNOWN", "reason": "packaged volume UNKNOWN, so nothing can be quoted"}

    quote = quote_fn(
        config,
        db,
        start_system=start,
        end_system=end,
        volume_m3=plan.cargo_m3,
        collateral=plan.source_cost * config.freight.collateral_multiple,
        client=client,
        now=now,
    )
    if not quote.known:
        return {
            "state": "UNKNOWN",
            "reason": quote.unknown_reason or "no quote",
            "route": quote.route,
        }
    freight = quote.effective_price or 0.0
    minutes = plan.active_minutes
    return {
        "state": "OK",
        "route": quote.route,
        "freight_isk": freight,
        "cached": quote.cached,
        "haircut_pct": quote.haircut_pct,
        "net_if_shipped": plan.net_profit - freight,
        # What flying it yourself is worth: the fee avoided, per minute spent.
        # It is the honest form of "should I fly this?" — and it says nothing
        # about whether the haul itself is worth taking.
        "your_time_isk_per_minute": (freight / minutes) if minutes else None,
        "note": (
            "PushX quotes a contract, not an invoice, and a cached quote carries a "
            "staleness haircut. The self-haul row does not depend on this column."
        ),
    }


def attach_freight(
    config: Config, db, scan, *, limit: int = 5, client=None, now=None, quote_fn=None
):
    """Quote the top `limit` plans and attach the comparison to each.

    Bounded on purpose: each quote is a request to somebody else's service, and
    quoting a hundred losers would be rude and pointless — the same discipline
    `scan_cross_region` applies. Plans past the bound keep an explicit
    `not quoted` state rather than a blank that could read as "no freight".
    """
    plans = []
    for index, plan in enumerate(scan.plans):
        if index < max(0, int(limit)):
            comparison = freight_comparison(
                config, db, plan, client=client, now=now, quote_fn=quote_fn
            )
        else:
            comparison = {"state": "UNKNOWN", "reason": NOT_QUOTED}
        plans.append(replace(plan, freight=comparison))
    scan.plans = plans
    return scan
