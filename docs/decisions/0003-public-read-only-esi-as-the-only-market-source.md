# 0003 — Public, unauthenticated ESI is the only market data source in v1

Date: 2026-08-18

## Context
Three classes of EVE market data exist: public ESI endpoints (region orders,
region history, `/markets/prices`, `/markets/{region}/types`); authenticated ESI
behind SSO (structure markets, character orders, wallet); and third-party
aggregators (Fuzzwork, Adam4EVE, EVE Ref).

## Decision
Public ESI only (`plan.md` §3, §10.8). No SSO in v1. Third parties are
cross-checks and enrichments, never load-bearing inputs. Revisiting this is a
plan-level decision, not a convenience patch.

## Rationale
SSO would buy public-Upwell-structure market visibility, and nothing else this
system wants. That blind spot is better *measured* than assumed, so
`book_summary` carries `station_volume_share` per row and Phase 0 measured it:
of 412,380 Forge orders, six Upwell structures appeared, all buy-side, and
**zero** structure sell orders; Jita 4-4 alone held 86.4% of Forge sell orders
(`plan.md` §0, Phase 0 measurements). The blind spot the scope creep would have
closed turned out to be nil on the sell side in the only region v1 trades.

Adding SSO would also mean holding a token that can act on a character, which
sits uncomfortably close to ADR 0001's line even when unused.

Third-party services are each one person or one community project (`plan.md` §9
R10). Fuzzwork's 5%-percentile statistic is replicated in our own reduction
rather than fetched, so a Fuzzwork outage is a lost cross-check, not a dead
screener.

If a market group turns out to be structure-dominated, it is **excluded and
labelled**, never guessed at.
