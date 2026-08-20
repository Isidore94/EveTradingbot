# Current checkpoint

This file names the one active item, its working state, and the last
verification stamp. `plan.md` owns the roadmap; `CHANGELOG.md` owns history.

## Active item

**The consolidated live-validation gate** (plan.md §17 D-1). The v1 system is
**IMPLEMENTED + GREEN**. Nothing is `LIVE_VALIDATED`, and nothing may be
promoted to real ISK until the checklist below is worked through.

**The system already has an answer to the question it was built for, and it is
a negative one.** The setup class is NOT PLAUSIBLE at every horizon tested, on
friction rather than direction; the destruction lead-lag effect does not
survive its own pass rule; and of 315 tracked types only two have books tight
enough for the measured edge to survive costs. The one measurably positive
finding is cross-region: 14 of 151,123 hub pairs clear real freight and tax at
0.25B, best +14.44%. All of it is in `plan.md` §17 and
`data/reports/viability-*.md`.

That answer is *provisional on the cost model being right*, which is exactly
what gate E measures. Do that one first.

Every item on it is an **operator action**. The build cannot self-certify: the
whole point of the ladder is that a machine's confidence in itself is not
evidence.

## Verification baseline (2026-08-20)

- `uv run pytest -q` → **317 passed, 7 deselected** (network).
- `uv run pytest -m network -q` → **7 passed in 139 s** against real endpoints:
  real history against the frozen bar contract, a second call skipped as
  still-fresh, a full 415-page Forge sweep inside the token self-cap, the
  telemetry ledger, a real digest, a real paper open priced off a live book,
  one real day of EVE Ref killmails, and a live PushX quote.
- `uv run ruff check .` → clean. `uv run ruff format --check .` → clean.
- `python -m evescreener selftest` → 7/7 checks passed.
- **16,580 LOC** (10,561 product + 1,435 vendored + 4,584 tests) — 1,580 over
  §1's budget, authorized by operator directive 2026-08-20 and recorded in
  §17 D-9.

## The consolidated live-validation checklist

### A. Data honesty (was Phase 0's gate)

- [ ] **Spot-check five types in-game.** Open the market window for five
      tracked types and compare price and volume against the lake and the last
      sweep, within cache-window tolerance. *Falsified if any disagrees beyond
      that tolerance.*
- [ ] **Reproduce the fee arithmetic against one real fill.** Make one real
      trade, then check `costs.py`'s numbers against the wallet to ±0.1%.
      *This is also what closes §0 checks #5 and #6* (relist surcharge, and
      whether the 7.5%/3% base rates are still current).
- [ ] **Read the telemetry ledger.** `SELECT feed, COUNT(*), MIN(expires_at)
      FROM sweep_ledger GROUP BY feed` — confirm every request honoured its
      `Expires` and stayed inside the self-caps. Any row with `from_cache=0`
      whose `requested_at` precedes the previous response's `expires_at` for
      the same URL is a bug and must be reported, not tuned around.

### B. Universe (was Phase 1's gate)

- [ ] **48 hours unattended**, zero 429 and zero 420 in the ledger, orders
      budget peak below 25% of the 12,000-token window.
- [ ] **Census committed and read.** The derived floor replaces §11 D3's
      pre-census estimate in `plan.md`. This gate is the licence for every
      later "the universe is N" claim.

### C. Signals (was Phase 2's gate)

- [ ] **Bands and levels spot-checked** against Adam4EVE or the in-game chart
      for 10 types across 3 market groups.
- [ ] **Confirm the anchor calendar.** `config/anchors.jsonl` now carries seven
      **real** dates pulled from the official patch-notes feed (Revenant,
      Legion, Catalyst, Cradle of War and three version patches). They ship as
      `confirmed: false` because which of them counts as a live anchor for
      *your* items is your judgement, not the feed's — a version patch that
      rebalances one hull is not the same event as an expansion. Flip the ones
      you want to `true`. **Until then the system anchors on a synthetic
      90-day grid**, which works but is not the patch-anchored read the design
      is built around. `python -m evescreener anchors` refreshes candidates
      (it can never confirm one); `--list` shows the calendar.
- [ ] **Resolve §0 check #1** (is ESI `average` volume-weighted or a plain
      mean?) by comparing ~20 types' `average` against Fuzzwork's same-day
      `weightedAverage`. The bar contract tolerates either answer; the answer
      belongs in `plan.md` §4.
- [x] **§0 check #4 is ANSWERED** — CCP does *not* filter outlier prints.
      Measured over 1,854,651 real bars: `high/close` reaches 1,940,777× and
      `close/low` reaches 12.8 billion×. Winsorization clamps 7.9% of bars and
      touches 79% of tracked types; without it **20.5% of types would carry a
      risk unit more than twice too large** (worst case 2,433×). Nothing is
      owed here — it is recorded in `plan.md` §17.

### D. Ranking and delivery (was Phase 3's gate)

- [ ] **Two-week shadow period.** Every digest archived, every decision logged
      with its planned net-R, outcomes tracked.
- [ ] **Survive one ESI outage day** with honest staleness — UNKNOWN rows and
      an explained honest zero, never stale numbers presented as fresh.
- [ ] **Set the Discord webhook** in `config.toml`. Until then delivery
      reports `unconfigured` and the digest is archived but not posted, which
      is correct behaviour, not a failure.

### E. Cost netting (was Phase 4's gate)

- [ ] **Predicted vs actual effective fill on ≥ 10 real trades**, within
      **±0.5% of notional**. Record each with
      `python -m evescreener paper real-fill`. The tolerance was stated in
      `plan.md` §12.3 before any fill was recorded and is not to be adjusted
      after seeing the results.

### F. Destruction (was Phase 5's gate)

- [ ] **Read the lead-lag study's outcome** against the §14.3 rule frozen
      before measurement. If it did not survive, destruction stays an
      annotation — do not let it into the ranking by feel.

### G. Cross-region (was Phase 6's gate)

- [ ] **One full cross-region cycle validated on real freight**: quoted vs
      invoiced cost within tolerance, and the token budget still under 25% peak.
- [ ] **Resolve §0 check #3** (structure blind spot) by comparing
      `station_volume_share` against the in-game regional view. The number is
      already carried per row; what is owed is reading it.

### H. The experiment itself (plan.md §12)

**Read this section against what the backtest already found.** The setup class
was tested and came back NOT PLAUSIBLE at every horizon — on friction, not
direction. Running the paper experiment on the general setup would mostly
reproduce that, expensively and slowly. So the sequencing that actually earns
something:

- [ ] **First, use the paper platform to validate the cost model, not the
      setup.** That is gate E: ten real fills recorded against predicted
      effective prices. It works regardless of whether any setup pays, and it
      is what makes every other number in the system trustworthy. If the cost
      model is wrong, the backtest's verdict is wrong too — in either
      direction.
- [ ] **Only then decide whether to test a setup at all**, and if so, scope it
      to the two tracked types whose round-trip friction is under the ~0.78%
      the measured gross edge can absorb (*100MN Afterburner II*, *Mexallon*).
      That is a **new study needing its own pre-stated rule** (plan.md §13
      discipline), not a re-run of the old one with the losers removed.
- [ ] **If you do run it: 20 closed trades for the first read, 40 for the
      verdict.** Below 20 the tracker reports `TOO_EARLY` and offers no read —
      take none.
- [ ] **Accept that `FALSIFIED` is a real possible outcome.** The rule was
      frozen before the first trade precisely so that a negative answer cannot
      be argued away afterwards. Given the backtest, it is the likely one.

## Notes for the next session

- One phase at a time resumes after this gate. The single-push override was
  scoped to this build only (plan.md §17 D-1).
- `plan.md` §0 checks **#1, #5 and #6 remain OPEN** — all three need the
  operator (a Fuzzwork cross-check for #1, one real fill for #5 and #6).
  **#3 and #4 are ANSWERED** by measurement and #2 is partially answered; §17
  records all of it.
- The unconfirmed anchor calendar is the single largest gap between what the
  system does today and what it was designed to do. It is now a *confirmation*
  task — the real dates are already in the file — not a data-entry task.
