# 0008 — Costs are netted inside the screen, at notional tiers, from the depth walk

Date: 2026-08-18

## Context
The obvious screener design ranks on gross margin — `(bid − ask) / ask` — and
reports fees alongside. EVE's fee structure (3.375% sales tax at Accounting V,
~1% broker fee on posted orders only) plus the depth cost of actually filling a
position frequently exceeds the entire gross margin of the widest-looking rows.

## Decision
The ranked quantity is **net** expected value at an intended size, computed from
effective entry and exit prices, never a gross margin with fees applied
afterward (`plan.md` §5). Concretely:

```
entry_price(S)  = ask-walk VWAP for notional S      # depth_fill_price, sell side
exit_taker(S)   = bid-walk VWAP for S × (1 − tax)
exit_maker(S)   = target × (1 − tax − broker)       # fill risk flagged, never netted away
```

Breakevens are computed at 0.25B / 1.0B / 2.5B ISK tiers. A candidate that does
not clear breakeven at the **smallest** tier is not shown as opportunity. Depth
cost comes from the most recent book sweep with its `sweep_ts` displayed; a
stale book renders it UNKNOWN (ADR 0007).

Skills are configuration (`accounting_level`, `broker_relations_level`), not
constants.

## Rationale
This is the screener's namesake failure mode (`plan.md` §9 R5): gorgeous
percentage margins that cannot absorb 0.25B without eating the whole edge,
because **the wide margin *is* the illiquidity premium**. Netting at a real
notional makes an unfillable margin net to approximately zero, so it never
ranks. That is a structural fix, not an advisory warning the operator has to
remember to read.

Phase 0 confirmed the shape of the answer: across the 50-name seed roster,
nothing cleared costs at 0.25B, with the deepest names sitting at −3.5% to −7%
net. That is exactly what a ~3.4% tax plus a two-sided depth walk should produce,
and it is why the honest-zero contract (ADR 0012) is the *default* outcome rather
than an edge case.

The maker exit stays advisory and is never chosen for the operator: its fee edge
over a taker exit is real, but queue risk cannot be priced from a snapshot, and
a number that pretends otherwise is worse than two numbers and a caveat.
