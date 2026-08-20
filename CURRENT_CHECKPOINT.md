# Current checkpoint

This file names the one active item, its working state, and the last
verification stamp. `plan.md` owns the roadmap; `CHANGELOG.md` owns history.

## Active item

**The consolidated live-validation gate** (plan.md §17 D-1), now covering the
desk and the operator setup engine as well (plan.md §19, checklist section I).
Everything is **IMPLEMENTED + GREEN**. Nothing is `LIVE_VALIDATED`, and
nothing may be promoted to real ISK until the checklist below is worked
through.

**The system already has an answer to the question it was built for, and it is
a negative one.** Measured on the full lake — 3,116,848 bars, 2,654 tracked
types, 108,441 setup instances:

- the setup class is **NOT PLAUSIBLE at every horizon**, on **friction rather
  than direction** (10-day gross **+2.80%** at a 55.7% win rate against
  **14.7%** round-trip friction before tax);
- the destruction lead-lag effect **does not survive** (ρ=0.027 on 473,606
  observations against a 0.10 threshold);
- of 6,672 fillable Forge types, only **27** have round-trip friction under
  1%, and only **2 of 315** measured tracked types are tight enough for the
  measured edge to survive costs;
- the one measurably positive finding is **cross-region**: 10 of 151,113 hub
  pairs clear real PushX freight and sales tax at 0.25B, best **+13.63%** —
  and that is a simultaneous-snapshot number for a haul that takes days.

All of it is in `plan.md` §17 and `data/reports/viability-*.md`.

That answer is *provisional on the cost model being right*, which is exactly
what gate E measures. Do that one first.

Every item on it is an **operator action**. The build cannot self-certify: the
whole point of the ladder is that a machine's confidence in itself is not
evidence.

## Verification baseline (2026-08-20)

- **Latest — the desk, indices, setups and the learning loop (plan.md §19,
  §17 D-14…D-20):** `uv run pytest -q` → **509 passed, 7 deselected**, ruff
  check + format clean, `python -m evescreener selftest` → **11/11**. The desk
  was opened against the real data directory: all eight pages rendered on
  2,001 tracked types, with a 223-minute-old book correctly shown as STALE and
  every friction column correctly UNKNOWN because of it. LOC now **27,399**
  (18,049 product / 1,435 vendored / 7,880 tests / 35 launcher); the desk plus
  its tests plus the launcher is **3,477** of the 12,000 the third directive
  authorized for it.
- **Earlier the same day — operator workflow port (plan.md §18, §17 D-13):**
  `watch`/`brief`/`board` and the digest watchlist section landed;
  `uv run pytest -q` → **358 passed, 7 deselected**, ruff check + format
  clean, selftest 7/7. LOC now 18,296 (11,575 product / 1,435 vendored /
  5,286 tests). The baseline below is the v1 build it extends.
- `uv run pytest -q` → **337 passed, 7 deselected** (network).
- `uv run pytest -m network -q` → **7 passed in 139 s** against real endpoints:
  real history against the frozen bar contract, a second call skipped as
  still-fresh, a full 415-page Forge sweep inside the token self-cap, the
  telemetry ledger, a real digest, a real paper open priced off a live book,
  one real day of EVE Ref killmails, and a live PushX quote.
- `uv run ruff check .` → clean. `uv run ruff format --check .` → clean.
- `python -m evescreener selftest` → 7/7 checks passed.
- **17,134 LOC** at the v1 build (10,751 product + 1,435 vendored + 4,948
  tests) — superseded by the count above; the budget exception is recorded in
  §17 D-9 and D-20.
- **Environment note for a Linux box only:** PySide6 needs system libraries
  that a bare container lacks (`libegl1 libgl1 libxkbcommon0 libdbus-1-3
  libfontconfig1`, after `apt-get update`). The operator's Windows machine
  needs none of this — `uv sync --extra gui` is the whole install there.

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

- [x] **Budget discipline held during the build.** Zero 429s and zero 420s
      across ~26,000 requests; the orders token floor observed was 10,176 of
      12,000, i.e. a peak of **15.2%** against the gate's 25% target. Recorded
      in the `sweep_ledger`.
- [x] **Census run and the derived floor written into `plan.md` §11 D3**
      (≥500M ISK median daily turnover, 2,654 types, 95.1% of turnover). This
      gate is the licence for every later "the universe is N" claim, and it is
      now earned.
- [ ] **48 hours unattended** is still owed — the build ran the crawl once
      under supervision, not the daemon across two days.
- [ ] **Decide the `order_count` floor question** raised in §11 D3: the derived
      floor's `order_count` component came out **zero**, so the anti-wash-trade
      guard §3.6 wanted is not active in it. Either constrain the rule or keep
      `config.toml`'s own `min_median_order_count` above it. This is a
      plan-level decision, not a re-derivation.

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
- [ ] **Read the new desk surfaces once against the in-game market** (plan.md
      §18): `board --top 20` beside the in-game browser — do the movers and
      dips look like the market you see? — and `brief --name <a type you
      know>` — are the bands, levels and friction numbers believable? These
      are the ported TradingBotV3 chart/strength-board reads; §2.7's "does the
      board resemble your scan" judgement is yours to make, not the build's.

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

### I. The desk and the operator setups (plan.md §19) — NEW

Everything in §19 is IMPLEMENTED and GREEN offline. Nothing in it is
LIVE_VALIDATED, and the LEARNING page is correctly showing UNKNOWN for
everything because there is not one tagged closed trade yet.

- [ ] **Open the desk on the real data directory.** `uv sync --extra gui`,
      then `python -m evescreener gui` (or double-click `launch_gui.py`). Walk
      all eight pages. The thing to look for is not "does it render" — it did
      here — but whether any number on it disagrees with the same number from
      the CLI (`board`, `brief`, `scan`, `paper report`, `learning`). They read
      the same code, so a disagreement is a real bug.
- [ ] **Eyeball FORGE against Adam4EVE or the MER.** The index has never been
      compared to an outside source. It does not need to match — different
      membership, different weighting — but it must not disagree in *shape*.
      If FORGE rises through a month the MER shows falling, something is wrong
      with membership or with the chain-link, and the diagnostics beside the
      chart (members, top weight, entropy) are where to start.
- [ ] **Skim the sector membership.** Open each sector on MARKET and check the
      member count and top weight look like the sector's name. A sector is a
      subtree of market groups read from the SDE; a plausible failure is a
      root that pulls in far more than intended. `config/sectors.jsonl` is
      yours to edit — that is what it is for.
- [ ] **Check the THIN band by hand.** Pick three THIN names off the board and
      look at them in-game. The claim is "100–999 units a day — you may not
      get out of this at size". If that reads wrong for EVE, the floor in
      `config.toml` is one number and §11 D3 records what the old one was.
- [ ] **Define one setup end to end.** Write it in `config/setups.jsonl`, run
      `setups` to see it validate, `scan --setup "<name>"` to see it fire (or
      honestly not), chart a hit, `paper` buy it with a setup tag and a like
      tag, close it, and confirm it appears on LEARNING. **That single loop is
      the acceptance test for the whole of §19** — everything else in this
      section is inspection.
- [ ] **Pass on something, deliberately.** Use "not today" with a dislike tag
      on a name you would genuinely skip. In 5, 10 and 20 days the LEARNING
      page will tell you whether that reason was a good one. This half of the
      record is the half nobody keeps, and it is the half that is cheap.
- [ ] **Run `backtest --setup NAME`** for the setup you defined. Note that a
      setup containing a `near_level` condition will correctly produce **zero**
      instances and say why — that is not a bug, it is the refusal to
      backtest a condition that would need lookahead.

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
