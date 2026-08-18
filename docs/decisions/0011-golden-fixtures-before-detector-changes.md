# 0011 — Golden fixtures are regenerated before a detector or scoring change, never after

Date: 2026-08-18

## Context
Inherited directly from TradingBotV3 decision 0009. A detector change that
breaks a test presents two paths: fix the code, or update the expected values.
The second is faster and is almost always the wrong one, because it converts a
regression into a passing build.

## Decision
From **Phase 2 forward**, no detector or scoring change lands without
regenerating the affected golden fixtures **first**, as a reviewed, separate
step (`plan.md` §8, §11 D5). Fixtures live under `tests/fixtures/` and each one
carries `acquired_at`, its source URL, and the `X-Compatibility-Date` it was
recorded under.

Tests are **offline by default** — `pytest -q` deselects the `network` marker —
and live calls run intentionally via `pytest -m network`.

## Rationale
"Regenerate first" makes the diff the review artifact. When the expected values
move in their own commit, a human sees *what* moved and by how much, and can say
whether that was the intent. When they move inside the change that caused them,
nobody sees anything and the build is green.

Phase 0 already exercised the fixture discipline in the direction that matters:
the frozen `book_summary` reduction for three Forge types was generated once,
checked by hand (each depth walk and percentile verified arithmetically), and
locked. Changing the reduction now means regenerating that frame deliberately —
not editing a test until it passes.

Offline-by-default is a separate concern with the same root: a test suite that
needs the network is a test suite that gets skipped, and a skipped gate is not a
gate. It also keeps the ESI budget (ADR 0004) out of the inner development loop.
