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

## ACTIVE — plan.md §21 remediation track (operator-authorized 2026-08-20)

An adversarial repository review found defects in how the order book was
reduced and how snapshots were validated. The operator authorized this track
to take priority over the queued §20.3 work. **§20.3 is paused, not
cancelled**, and the consolidated live-validation checklist below is untouched
and still owed in full. Nothing in this track retracts a measurement.

**One phase per session.** A later phase is never started because it is
adjacent.

| phase | scope | state |
|---|---|---|
| **R1** | Executable order-book identity and validated snapshots | **IMPLEMENTED + GREEN** |
| **R2** | Completed-bar enforcement and independent bar freshness | **IMPLEMENTED + GREEN** |
| **R3** | Backtest price bounds, statistics and friction labels | **IMPLEMENTED + GREEN** |
| **R4** | Maker analysis and location-specific cost semantics | **IMPLEMENTED + GREEN** |
| **R5** | Killmail lead-lag hypothesis fidelity | **IMPLEMENTED + GREEN** |
| **R6** | Learning freshness and eligible-sample handling | **NEXT** |
| R7 | Desk threading, invalidation and worker lifecycle | not started |
| R8 | GUI network isolation, chart parity, regional data, stale docs | not started |

### R1 owed live gate

Nothing R1 produces has been checked against a live client. After the next
`sweep-books`:

- [ ] Confirm `exec_location_id` for a handful of liquid Forge types is the
      station the operator actually trades at (expected: Jita 4-4, 60003760).
- [ ] Confirm a type whose region-wide best bid rests in a structure is
      flagged `exec_is_structure` or excluded, rather than priced.
- [ ] Confirm the SPREADS page prices rows again once a complete sweep exists.

### R1 operational consequence — read this before the next run

**The stored Forge book (35,858 rows) now prices nothing.** It predates the
executable-quote contract and genuinely cannot say where its quotes rested, so
`load_validated_book` reports it UNKNOWN. This is missing data reported
honestly, not a regression. `sweep-books` restores pricing.

## Superseded sub-track: plan.md §20 — the daily desk

The operator asked (2026-08-20) for a consolidated daily-review page, alerts
with ntfy, top performers, region trading and a spreads tab. That is recorded
as **plan.md §20**, phased, one per session, each gated green.

| phase | state |
|---|---|
| §20.1 DESK — consolidated review page | **IMPLEMENTED + GREEN** |
| §20.2 SPREADS — maker / station trading | **IMPLEMENTED + GREEN** (§17 D-31) |
| §20.3 TOP PERFORMERS 1w / 1m | **NEXT** |
| §20.4 REGIONS — cross-region hauling | not started |
| §20.5 ALERTS + ntfy | not started — SETTINGS form landed early, delivery not built |

**Setups are out of scope for this track** by operator decision; the action on
every surface is a paper trade.

**§20.2 is the one with an analytical claim behind it.** §17's NOT PLAUSIBLE
verdict was measured on a *taker* strategy — cross in, cross out, 14.7%
round-trip friction against a +2.80% gross edge. A maker posts both sides and
**collects** the spread, so the 98.8% median Forge spread that killed the
taker is the maker's revenue. `books.spread_view()` and
`CostModel.sell_proceeds(maker=...)` already exist. The open question the tab
must not paper over is **fill plausibility and undercut risk**, neither of
which any existing measurement covers.

## Verification baseline (2026-08-20)

- **Latest — §21 R1, executable book identity and validated snapshots:**
  `uv run pytest -q` → **602 passed, 7 deselected**, ruff check + format clean.
  The reduction preserves `location_id` and buy-order `range`; a spread is now
  the pair executable at one named venue, with the region-wide extrema kept
  visible as diagnostics. Partial sweeps are quarantined and `latest()` returns
  the newest *complete* snapshot. `load_validated_book()` is the single
  contract deciding completeness, executability and staleness.
- **SPREADS and SETTINGS (plan.md §20.2, §17 D-31):**
  `uv run pytest -q` → **583 passed, 7 deselected**, ruff check + format clean.
  The maker read of the book, anchored to the traded average because 39.7% of
  two-sided Forge books have a bid under half of it. 1,590 Forge names carry a
  positive net maker edge on a simulated-fresh book (median +13.0%); on the
  real 121-minute-old sweep the page correctly showed an honest zero.
  **Owed live gate:** run `sweep-books`, then confirm the SPREADS list against
  the in-game market for a handful of names — no row on this page has ever
  been checked against a real order book, and fill probability is unmodelled.
- **DESK, the consolidated review page (plan.md §20.1):**
  `uv run pytest -q` → **566 passed, 7 deselected**, ruff check + format clean.
  FOCUS/BOARD/SCANNER as tabs on the left, the one chart on the right, nothing
  removed from the rail. The window now owns the single `ChartPanel` and docks
  it into the visible host, so §19's one-chart rule holds literally across two
  pages. The chart opens on the whole series. Presentation and composition
  only — no detector, formula or verdict rule moved.
- **The chart draws range candles (plan.md §19.2, §17 D-30):**
  `uv run pytest -q` → **560 passed, 7 deselected**, ruff check + format clean.
  Body = the day's measured low→high, notch = the average, colour = the move
  against the previous average; no `open` is synthesized. A conventional
  open→close body was refused **on measurement**: yesterday's close falls
  outside today's range on **55.7%** of 4,034,697 bars and **69.0%** of
  tier-OK bars, so it would hang off its own wick on most bars. The chart now
  opens at 120 bars with a 60/120/250/all selector. A composite index carries
  no intraday range (`high == low == close`), so MARKET draws FORGE and
  FORGE-EW as level lines rather than zero-height candles, and the canvas now
  expands instead of sharing its height with its own title. Presentation only — no
  detector, formula or verdict rule moved, so nothing on the live-validation
  checklist below changes state.
- **The relative measurable gate (plan.md §13.2 amended, §17 D-29):**
  `uv run pytest -q` → **556 passed, 7 deselected**, ruff check + format clean.
  39 types (1.33%) now read UNKNOWN rather than carrying a float-noise risk
  unit; max abs RRS falls 9.05e11 → 1.19e7 with the median unchanged at +3.18.
  Backtest 145,655 instances, still NOT PLAUSIBLE at every horizon. The digest
  produced the same 25 candidates.
- **Latest — FORGE's outlier clamp and the desk's threading contract (plan.md
  §17 D-22…D-28, §19.2 amended):** `uv run pytest -q` → **544 passed, 7
  deselected**, ruff check + format clean, `selftest` → **12/12**. FORGE now
  runs 1,000 → 981 over 415 bars with a 0.34% median daily move and a
  `power_index` of −3.28; the desk opens in **8.6 s** against 2,947 tracked
  types and 4,052,335 bars, with a 15 ms timer tick. The digest finds 25
  candidates where the broken RRS gate had reported an "honest zero".
- **Latest — deployed to the operator's Windows desk; compatibility-date guard
  salvaged (plan.md §17 D-21):** `uv run pytest -q` → **509 passed, 7
  deselected** on Windows (Python 3.12.13, uv 0.12.3), ruff check + format
  clean, `python -m evescreener selftest` → **12/12** against the real
  `config.toml`. Two changes only: the new `compatibility date` check, and a
  Windows path-escaping fix in the parity test that had been failing the
  offline gate on this machine and nowhere else. Config is Accounting **V** /
  Broker Relations **IV** → sales tax 3.375%, broker fee 1.300%; the Discord
  webhook is deliberately empty, so delivery reports `unconfigured`.
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

## Deployment on the operator's desk (2026-08-20)

The system now lives at `C:\Users\Aaron\EveTradingbot` on the always-on
mini-PC, in its own directory and its own `.venv`, fully isolated from
TradingBotV3 as §11 D1 requires. Nothing in this deployment reads, writes or
schedules anything under `C:\Users\Aaron\TradingBotV3` or `C:\TradingBotData`.

**Standing, and what is still owed:**

| Step | State |
|---|---|
| `uv sync --extra dev --extra gui` | Done. Python 3.12.13, uv 0.12.3, PySide6 6.11.2. |
| `config.toml` | Written, gitignored. Accounting **V**, Broker Relations **IV** → sales tax 3.375%, broker fee 1.300%. Discord webhook deliberately **empty**, so delivery reports `unconfigured` — correct behaviour, and gate D still owes the real webhook. |
| `pytest -q` / `ruff` / `selftest` | **515 passed, 7 deselected**; ruff check + format clean; selftest **12/12**. |
| `sde` | Build **3475087** — 52,863 types, 2,106 market groups, 8,490 systems. |
| `census` | **RUNNING** at the 150 req/min self-cap, ~2h10m expected. Notably it is already past **2,363** history requests — the exact point where the pre-D-12 circuit breaker latched open — with `history_missing` still empty, so the D-12 fix holds against live ESI rather than only against fixtures. |
| `anchors` | Run. 8 posts in the feed, 1 new candidate. **See the duplicate below.** |
| `census` result | **COMPLETE, 2h07m.** 19,150 active types · 18,946 fetched · **201** no-history 404s · 3 failed · **4,052,335 bars** across 17,638 types. Zero 429, zero 420, no breaker trip. Membership: OK **1,633** (9 price-pinned), THIN **1,314**, below floor 16,203 → tradeable universe **2,947**. |
| `sweep-books` | **COMPLETE.** 413/413 pages, 412,972 orders, 19,149 types, **0 duplicate `order_id`s**, structure volume share 14.0%. |
| `ingest-history` | **2,947 requested, 2,947 skipped-fresh, 0 fetched.** The never-fetch-before-expiry rule doing exactly its job on a lake an hour old. |
| `backtest` | **COMPLETE. NOT PLAUSIBLE at 5, 10 and 20 days**, reproduced independently on this machine's own lake: 2,947 types, **125,254** instances, friction 61.9–62.2% against gross edges of the same order. The banner is restored on MARKET and SCANNER. |
| `digest --dry-run` | **COMPLETE.** Banner present, honest zero ("Nothing clears costs today"), and all 50 watchlist names render — PLEX correctly saying it has no bars, which is §17's known Forge-cannot-price-PLEX finding showing up on its own. |
| `backtest` | **NOT RUN on this machine, and it must be.** `data/` is gitignored, so the previous build's `reports/backtest-*.json` did not come with the clone, and `verdict_banner` returns an **empty string** when no stored verdict exists. Until `backtest` runs here, MARKET and SCANNER show **no NOT-PLAUSIBLE banner at all** — the system's own headline finding is invisible on the desk. Run it after `ingest-history`; it reads the lake and costs no ESI traffic. |
| Killmail backfill | **SKIPPED** by operator decision (1.3 GB, and §14's lead-lag already returned negative). |
| The desk | Constructed offscreen against the **real** data directory: all eight pages built, `window.refresh()` fed them one local read, all eight selected without error. `book_age_minutes` is `None` and the book renders **STALE**, which is correct with no sweep yet. This is a smoke test of the Qt stack on this machine, **not** checklist I — that is still the operator's to walk. |
| Desktop shortcut | `EVE Screener Desk.lnk` → `.venv\Scripts\pythonw.exe launch_gui.py`, working dir the repo. |
| Daemon task | Registered as **`\EveScreener daemon`**, currently **Disabled**. Logon trigger for this operator, `PT2M` delay, action `uv.exe run python -m evescreener daemon` in the repo directory, `MultipleInstancesPolicy=IgnoreNew`, no execution time limit. **Enable it once the bootstrap finishes** — while the census runs, a logon would start a second independent ESI consumer against one IP. Distinct from all three TradingBotV3 tasks in name, executable and working directory. |

### Defects found during deployment

#### 1. The §11 D4 seed watchlist never reaches a fresh install

`config/anchors.jsonl` now holds *Patch Notes - Version 24.01* **twice**, on
2026-08-19 and 2026-08-20, with the **same** `source` URL. `patchnotes.py`
dedupes on `(date, label)` and on date-occupancy, but never on the article
URL, so an article CCP re-dates is appended again as a second candidate for
what is one real event. The daemon runs this watcher **daily**, so it will
keep happening.

It is not urgent and it is not silently harmful: candidates are inert until
`confirmed: true`, and growth is bounded to one row per date. But it lands
directly in gate C, where the operator confirms anchors by hand — and if both
rows were confirmed, the signal layer would anchor twice on one patch.

**Left for a decision rather than patched here**, because "what counts as the
same anchor event" is a plan-level question about a signal-layer input, not a
janitorial fix. The obvious answer is to add `source` to the dedup key and
prefer the newest date for a given URL.

`universe.seed_watchlist` exists, reads `config.universe.watchlist`, and
resolves each of the 50 D4 names against the SDE — and **nothing in `src/`
calls it.** The only callers are in `tests/test_universe_census.py`. So on any
fresh install `watch list` is empty, and with it:

* §18.2's "every watchlist name renders in **every** digest" renders nothing;
* the desk's FOCUS page starts empty;
* gate I's "check which of my watchlist hulls landed in the THIN tier" has
  nothing to check.

The roster has been **seeded operationally**, not by a code change: the 50
names were added through the documented `watch add` path, one call each, all
50 resolving against the SDE with zero unresolved. They are therefore
operator-owned entries, reachable by `watch remove` like any other, and
`config.toml` is unchanged.

**Wiring the seeder into a production path is left as a decision**, because it
has an invariant edge: if the universe refresh re-seeds, a name the operator
deliberately `watch remove`d would come back, which is the never-auto-removed
rule failing in the other direction. A one-shot seed on an empty watchlist is
probably the right shape, but that is a call to make deliberately.

#### 2. A legacy console codepage could kill a finished command — FIXED

`backtest` computed 125,254 instances, wrote **both** report files, and then
died on `print(render_backtest(result))` with `UnicodeEncodeError`: this
console is **cp1252** and the report contains `→`. Every renderer in the
package emits UTF-8, so `cli.main` now calls `_force_utf8_console()` before
anything else. The same crash was waiting in `digest`, `board`, `brief` and
`learning`, which all emit `→`, `σ` or `≥`.

Note what saved the run: `write_backtest` happens **before** the `print`, so
the reports survived the crash. That ordering is the failed-publish invariant
earning its keep by accident.

#### 3. The anchor watcher can double-count one event

### 3. FORGE printed composition artifacts, and RRS with it — RESOLVED 2026-08-20

Fixed under plan.md §17 **D-22**. Full write-up in `CHANGELOG.md`.

**Verified diagnosis, not the assumed one.** The chain-link was sound and
§19.1's churn fixture stayed green throughout. Decomposition against the real
lake named a single member-day: on 2026-08-02 *Vanguard Resonant Cypher*
(type 95640) printed `close 10.07 → 22,450.00`, a **+222,839.4%** return, at a
**0.75%** live weight — contributing **+1,661.59%** of the +1,661.37% the
index moved. All 100 members were priced that day, so no gap or NaN path was
involved. The same shape explains 2026-05-17 (*HyperCore*, +2,298%, 4.11%
weight → +94.38% of a +94.07% day) and 2026-08-18 (*HyperCore* again,
+1,385% → +58.91% of +57.13%).

**One assumed mechanism was ruled out.** The gap-reappearance path does not
occur here: pandas 3.0.5's `pct_change` no longer pads (`fill_method=None`),
so a member returning after a gap already yielded NaN. The returns are now
computed explicitly anyway, so the answer does not depend on which pandas is
installed.

**Fix:** member daily returns are winsorized at `k ×` each member's own
rolling median absolute return before aggregation, mirroring the ATR path's
TR clamp, with clamped-day counts in every index's diagnostics.

**Acceptance, measured against the criteria stated before the fix:**

| criterion | before | after |
|---|---|---|
| FORGE level, 415 bars | 1,000 → 69,243 | 1,000 → **981.10** |
| median abs daily move | 0.029%, punctuated by +1,661% days | **0.3396%** |
| p95 / max abs daily move | — | **1.03% / 2.08%** |
| `power_index` | **1,478.27** | **−3.280** |
| RRS, middle 84% of universe | every name ≈ −1,479 | p5 **−2.20** · p50 **+3.12** · p95 **+6.73** |
| digest | "Nothing clears costs today" | **25 candidates** |

That last row is the one that mattered: the honest zero was not honest. RRS
is one of the four gates, so a −1,479 offset was failing every name in the
universe and the digest was reporting a broken gate as an absence of
opportunity.

**Nothing persisted needed rebuilding** — indices are computed live from the
lake on every read, so there was no cached series to invalidate. The stored
backtest *was* regenerated, since its gate counts were computed against the
broken RRS: instances rose 125,254 → **147,140** and the verdict is still
**NOT PLAUSIBLE at every horizon**, which is the expected result because that
verdict rests on measured friction and never reads RRS.

### 3b. A degenerate per-type ATR — RESOLVED 2026-08-20, with a caveat

Fixed under plan.md §13.2 (amended) and §17 **D-29**. §13.2's `measurable`
gate is now **relative**: `atr / close >= signals.min_atr_fraction`, below
which the gate reads UNKNOWN and UNKNOWN fails. One epsilon governs the ATR
and the AVWAP sigma, enforced inside `atr_last` so no scalar consumer can
bypass it.

**The default is derived from the lake, not invented.** `atr/close` is
bimodal — a degenerate cluster at 1e-14…1e-11, then p1 = **1.6e-08**, p2 =
**2.4e-05**, p50 = **5.8e-02**. `1e-6` sits at the top of that empty band.

| | before | after |
|---|---|---|
| types blocked | — | **39 (1.33%)** |
| max abs RRS | 9.05e11 | **1.19e7** |
| abs RRS > 1,000 | 77 | **51** |
| RRS p1 / p99 | −1,966 / +2,661 | **−677 / +710** |
| RRS median | +3.18 | **+3.18** |
| backtest instances | 147,140 | **145,655** |
| backtest verdict | NOT PLAUSIBLE | **NOT PLAUSIBLE** |
| digest candidates | 25 | **25, none dropped** |

No golden fixture needed regenerating: on clean data the gate changes nothing.

**The caveat, because the acceptance was written expecting more.** The board's
value sort still shows large magnitudes at the top, and this fix was never
going to change that. Those rows are **not** ghost names: *Hemorphite
II-Grade* has `atr/close` of 1.55e-04 — ordinary — and reads RRS −2,932
because it fell 45% in twenty bars, i.e. **2,936× its own ATR**. That is a
correct measurement of an extreme move. The one visible board row this fix did
change is *Second-hand Parts* (`atr/close` 1.22e-10, a genuine ghost), whose
RRS is now UNKNOWN.

What is left at the top of that sort is mostly **unfiltered ESI prints in the
per-type `close` series** — the §0 check #4 phenomenon — and reporting stays
unclamped by design. If the operator wants the board's *ordering* to stop
selecting for those, that is a separate decision about the sort key, not about
the measurable gate.


### 4. The desk blocked its own GUI thread — RESOLVED 2026-08-20

Fixed under plan.md §19.2 (amended) and §17 **D-24**. Full write-up in
`CHANGELOG.md`.

**Contract now recorded in §19.2: the GUI thread never computes; it paints.**
Lazy pages, a `compute`/`paint` split on a `QThreadPool` worker,
last-good-on-failure under a visible stamp, and recomputation keyed on input
fingerprints rather than on the clock.

**Measured on the real lake, before and after:**

| | before | after |
|---|---|---|
| open to interactive | **217 s** | **8.6 s** |
| timer tick, unchanged inputs | full 217 s rescan every 60 s | **15 ms** |
| revisit an already-computed page | full recompute | **0.000 s** |
| first visit to SCANNER | blocking the window | 162.8 s off-thread, stamped |
| first visit to BOARD | blocking the window | 63.5 s off-thread, stamped |

BOARD and SCANNER still cost real time on their first visit — that work is
genuinely expensive — but it happens on a worker while the window stays
responsive and the page says what it is doing. That is what the amendment
permits; what it forbids is paying for it before the operator has asked, and
paying for it again every sixty seconds.

Four offscreen tests pin it: `build()` runs no computation (asserted against
the scan entry point itself), an unchanged input key does not recompute, a
completed background result lands on the GUI thread and repaints, and the
stamp renders while work is in flight — plus one that a failed recompute keeps
the last good result and names the error.

**Still owed, and honest about it:** the 8.6 s open is mostly `load_desk()`
(5.6 s) plus Qt startup, still on the GUI thread. It is inside the
"interactive in seconds" the amendment asks for, so it was left alone rather
than moved to a worker for its own sake.

### 5. Four small items — CLOSED 2026-08-20

- **`verdict_banner` renders UNKNOWN instead of nothing** (§17 D-25). A fresh
  clone has no stored study, and MARKET and SCANNER were carrying **no banner
  at all** — indistinguishable from a desk that measured and passed.
- **The patch-notes watcher dedupes on the article URL** (§17 D-26); the
  duplicate *Version 24.01* row is gone and seven distinct candidates remain.
- **`selftest`'s cost-model check derives tax and fee from config** (§17
  D-27) rather than hardcoding Accounting V.
- **`universe.seed_watchlist` deleted** (§17 D-28), its test with it. The
  roster is seeded and operator-owned; an automatic seeder was deliberately
  not wired in, because a re-seed would resurrect a name the operator removed.


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
