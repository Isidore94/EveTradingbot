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

No candidates were recorded when this file was created on 2026-09-04. Work that is
deferred but already authorized in principle stays where `plan.md` put it and is **not**
duplicated here: §20.4 REGIONS and §20.5 ALERTS + ntfy are plan items; §23 H0 is a
keep/park gate after the shadow period (§23.20); §23 H5 and H6 are out of scope because
they need authenticated ESI (§17 D-33).

| Idea | Status | Note |
|---|---|---|
| — | — | — |

## Permanent no

Recorded so they are never proposed as discoveries. Each is `plan.md` §10.

| Idea | Status | Why |
|---|---|---|
| Order placement, modification or cancellation through ESI | `PERMANENT_NO` | Decision-support only; no SSO scope that acts on a character |
| Automating the EVE client in any form | `PERMANENT_NO` | Product boundary |
| Fetching before `Expires`, or any cache circumvention | `PERMANENT_NO` | Bannable; a correctness invariant |
| A synthesized `open` column | `PERMANENT_NO` | The data does not carry one and 56% of bars would contradict it |
| Momentum or breakout-continuation logic in the system's own engine | `PERMANENT_NO` | `plan.md` §6; operator-defined setups may express it (§17 D-15) |
