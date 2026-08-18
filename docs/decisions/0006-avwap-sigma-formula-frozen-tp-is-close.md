# 0006 — The AVWAP σ formula is frozen from Phase 2; typical price is `close`

Date: 2026-08-18

## Context
TradingBotV3's `calc_anchored_vwap_bands` uses an OHLC4 typical price and a
volume-weighted **running-AVWAP** σ accumulator (`cumSD += (tp − vw)²·v`,
σ = `sqrt(cumSD/cumVol)`), in a per-row Python loop. Upstream, that formula is a
hard invariant: every band consumer is calibrated to it (see TradingBotV3
decision 0008). Here there is no OHLC4 to compute — there is no open (ADR 0005).

## Decision
Two parts.

- **`tp ← close`** (i.e. ESI `average`) directly, not a reconstructed OHLC4.
- **The σ *shape* is inherited unchanged** — volume-weighted deviation against
  the **running** AVWAP — and is **frozen from Phase 2 forward**. Changing it
  afterwards requires regenerating every golden fixture and re-validating every
  band consumer, together, with operator sign-off.

## Rationale
On `tp`: ESI's `average` is a whole-day, trade-derived mean. It *is* the day's
typical price — strictly better than a four-point proxy of it. (Whether it is
volume-weighted or a plain mean of trades is `plan.md` §0 check #1, still open;
the decision tolerates either answer.)

On the freeze: the operator's band instincts — 1/2/3σ ladders, band-walk reads —
are calibrated to the running-deviation variant's tighter-on-trend behaviour.
That calibration is the asset. A "more correct" formula would silently shift
every threshold at once.

On *when* the freeze starts: the tp change is a deliberate, documented departure
made **before any consumer exists**, which is the only moment such a change is
free. The upstream invariant binds TradingBotV3's consumers, not this repo; what
is carried over is the discipline, applied from Phase 2 when this repo's own
consumers begin to exist.

Implementation note that removes the usual objection to the formula: with
`tp = close` the whole computation is three NumPy cumulative sums per
(type, anchor) — `vw = cumsum(tp·v)/cumsum(v)`, `dev = tp − vw`,
`σ = sqrt(cumsum(dev²·v)/cumsum(v))`. Identical semantics, no row loop.
