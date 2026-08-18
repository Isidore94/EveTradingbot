# Current checkpoint

This file names the one active item, its working state, and the last
verification stamp. `plan.md` owns the roadmap; `CHANGELOG.md` owns history.

## Active item

**Phase 0 — First light** (`plan.md` §8). **NOT STARTED.**

Scope, exactly: repo scaffold per §11 D1–D2; minimal ESI client per §3.1 with
telemetry ledger; the §11 D4 seed watchlist resolved against the SDE; history
ingest for those 50 types into the Parquet bar store under the §4 contract; one
Forge orders sweep reduced to `book_summary` (§3.4); the net-cost screen
(spread, 30d turnover, breakeven at 0.25B per §5) posted to the Discord
webhook. Tests offline against recorded fixtures; one `network`-marked live
smoke path.

## Gate owed before Phase 1

- Five types spot-checked in-game (prices/volumes within cache-window
  tolerance) — **operator action**.
- Fee arithmetic reproduced against one real fill to ±0.1% — **operator
  action**.
- Telemetry ledger shows every request honored `Expires` and stayed inside the
  §11 D3 self-caps.

## Verification baseline

None yet — no code exists. First green `uv run pytest -q` + clean ruff stamp
goes here.

## Notes for the next session

- Planning, decisions (§11), and governance files landed 2026-08-18 on
  `claude/eve-market-screener-plan-nn82ms`.
- Open named checks #1–#6 live in `plan.md` §0; #1 (ESI `average` semantics)
  and #2 (page-snapshot consistency) can be resolved cheaply during Phase 0
  and their answers recorded in §0.
