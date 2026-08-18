# 0009 — No momentum or breakout-continuation logic

Date: 2026-08-18

## Context
The source system is a swing-trading stack: D1 band-walk continuation,
trend-following entries, five-day breakout flags, RVOL as breakout confirmation.
Those are the modules a port would naturally carry across first, because they
are the ones the operator's instincts are trained on.

## Decision
Breakout-continuation logic is **not ported and must not be re-introduced by
habit** (`plan.md` §6). The screen inverts the read: strength into a value zone
is distribution risk; the tradeable pattern is *dips below anchored value with
intact demand*.

RVOL survives, **repurposed**: `volume` and `order_count` against their own
20-day baselines become a demand-*event* detector feeding the catalyst and
anchor layer, not a breakout confirmation.

## Rationale
Equity float is fixed on any horizon that matters. EVE supply is **elastic and
player-produced**: a price spike is an invitation to industrialists, blueprints
do not sleep, and the supply response arrives within days. Spikes are arbitraged
flat, not continued. Chasing a breakout in EVE means buying at the top of a
supply response.

The same reasoning kills the M5 bounce stack outright: no intraday bars exist in
ESI, and the five-minute order cache is identical for every participant, so
there is no microstructure edge to detect. Building pseudo-M5 bars from book
snapshots would be modelling the cache, not the market (`plan.md` §10.3).

This ADR exists because the decision is *counter-instinctive for this operator
specifically*. Every other non-goal in `plan.md` §10 is something nobody would
add by accident. This one is muscle memory.
