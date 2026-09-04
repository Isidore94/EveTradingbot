# EveTradingbot wishlist

Last reconciled: **2026-09-04**

This file is the operator's parking lot for ideas that may be useful but are **not
authorized build work**. The authoritative implementation order is `plan.md`.

## Rules

- An AI may suggest, clarify, compare or estimate a wishlist item.
- **An AI must not implement an item from this file.**
- Only the operator may promote an item into `plan.md`.
- Promotion requires a defined operator outcome, prerequisites, scope, invariants, tests,
  the live gate that would validate it, and an insertion point in the roadmap.
- Moving an item to the roadmap changes its status here to `ROADMAP`; it is not deleted,
  so the decision stays visible.
- Retired ideas stay recorded to prevent accidental resurrection.

Statuses:

| Status | Meaning |
|---|---|
| `ROADMAP` | Accepted and ordered in `plan.md`; follow the roadmap, not this file |
| `CANDIDATE` | Worth discussing; not authorized |
| `TRIGGERED_LATER` | Consider only if the named condition occurs |
| `RETIRED` | Deliberately abandoned |
| `PERMANENT_NO` | Conflicts with the product boundary (`plan.md` §10) or a hard invariant |

## Ideas

Work that is deferred but already authorized in principle stays where `plan.md` put it
and is **not** duplicated here: §20.4 REGIONS and §20.5 ALERTS + ntfy are plan items; §23
H0 is a keep/park gate after the shadow period (§23.20); §23 H5 and H6 are out of scope
because they need authenticated ESI (§17 D-33).

The candidates below were recorded on 2026-09-04 from
[`docs/reviews/2026-09-04-HAULING_ARBITRAGE_ANALYSIS.md`](docs/reviews/2026-09-04-HAULING_ARBITRAGE_ANALYSIS.md)
(finding numbers F2–F10 there carry the evidence). None is authorized.

| Idea | Status | Note |
|---|---|---|
| **Persistence-weighted hauling rank** — keep every hourly depth generation for N days; per (type, source, destination) compute survival and re-priced net over the last K generations; rank on persistence-weighted net per active minute, unweighted shown beside it | `CANDIDATE` | F2. Measured 2026-08-26 → 08-28: 44.5% of plans survive, top-25 net realised at 36%. Needs F1 (fresh hourly generations) first. Additive; no walk or fee change |
| **Basket objective by binding constraint, one destination per basket** — or a numpy-only two-constraint fractional knapsack over the existing marginal chunks | `CANDIDATE` | F3. Greedy-by-ISK/m³ filled 250 M ISK with 1.8 m³ of formulas across four hubs and earned 42–66% of the best single plan |
| **Loop and circuit composition** — pair the best plan each way (and 3–5-stop circuits over the hubs) from plans already priced | `CANDIDATE` | F4. Jita ↔ Dodixie loop 23.9 M / 57 min on the 08-28 books; no new pricing |
| **Single-bid-exit flag, minimum-quantity and hide-BELOW controls** on the HAULING page and CLI | `CANDIDATE` | F5. Quantity ≤ 5 plans survive at 33% vs 51%; ten of the top 25 were 1–6-unit hulls |
| **Show the `OVER_TIME`/`OVER_JUMPS` pair count in the control strip** | `CANDIDATE` | F6. The 30-minute default silently drops Jita ↔ Amarr (39 min) before pricing |
| **Per-hub destination-share proxy from `station_volume_share`**, labelled as a book-share proxy, still replaced by recorded fills | `CANDIDATE` | F7. Replaces a flat 0.25 with a per-hub, per-type number that is still an assumption |
| **`extra_source_station_ids`**, and a WARM sweep of the high-sec regions adjacent to the hubs | `CANDIDATE` | F8. Plan-level (§11 D3 cadence). Token cost ≈ +1,800/hour per five regions against a 6,000/15-min self-cap |
| **Route risk from hauler killmails per route system** (90-day count as a column, never a multiplier) | `CANDIDATE` | F9. The only use found for the killmail lake after §14 failed |
| **Park `cross-region` after the shadow period** | `TRIGGERED_LATER` | F10. Only if the §23 tab is kept at H0 |
| **Charge capital cost and hull risk on hauling rows** | `TRIGGERED_LATER` | F11. Only after checklist C proves the fee arithmetic against a real round trip |

## Permanent no

Recorded so they are never proposed as discoveries. Each is `plan.md` §10.

| Idea | Status | Why |
|---|---|---|
| Order placement, modification or cancellation through ESI | `PERMANENT_NO` | Decision-support only; no SSO scope that acts on a character |
| Automating the EVE client in any form | `PERMANENT_NO` | Product boundary |
| Fetching before `Expires`, or any cache circumvention | `PERMANENT_NO` | Bannable; a correctness invariant |
| A synthesized `open` column | `PERMANENT_NO` | The data does not carry one and 56% of bars would contradict it |
| Momentum or breakout-continuation logic in the system's own engine | `PERMANENT_NO` | `plan.md` §6; operator-defined setups may express it (§17 D-15) |
