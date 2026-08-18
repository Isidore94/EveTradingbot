# 0007 — Completed bars only; gates are tri-state and UNKNOWN always fails

Date: 2026-08-18

## Context
ESI's history endpoint expires daily at 11:05 UTC. A bar dated `D` covers the
EVE day ending at downtime on `D+1` and is only published once that roll
happens, so a naive "take the last row" reads a day that is still accumulating.
Separately, a screener has three possible answers about any candidate — yes, no,
and *could not measure* — and the third is routinely collapsed into one of the
first two.

## Decision
- **Completed bars only.** `clock.last_completed_bar_date()` is the boundary; a
  history row dated on or after it is dropped, not carried. Verified against
  live ESI: at 02:20 UTC on 2026-08-18 the newest published Forge bar for type
  34 was dated 2026-08-16, which is what the rule returns.
- **Tri-state everywhere.** A stale book (>60 min), a missing side of the book,
  or a notional tier the book cannot absorb renders the row `UNKNOWN` with a
  stated reason. **UNKNOWN always fails** — it is never treated as a pass and
  never silently priced.
- Every lake row carries `fetched_at` and the response's `Last-Modified`, so
  freshness is a value consumers can read, not an assumption.

## Rationale
Three idioms from the source repo's M5 gating layer survive its deletion because
they are about epistemics, not timeframes: *completed bars only*, *"could not
measure" ≠ "measured and failed"*, and *tri-state gates where UNKNOWN fails*.

The failure mode they prevent is specific and expensive. A screener that prices
off a stale book presents yesterday's depth as today's, and the operator cannot
tell by looking — the number has the same shape either way. Rendering it UNKNOWN
with `book is 84 min old; depth cost unknown` costs one row of the digest and
buys the operator the ability to trust the rows that *are* priced.

The same logic drives ADR 0005 (no synthetic open) and ADR 0012 (honest zero).
They are one decision applied at three layers.
