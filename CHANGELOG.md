# EveTradingbot implemented history

Authoritative for what exists and the sequence of revisions. Remaining work:
`plan.md`. Labels keep the source-repo meanings: `IMPLEMENTED` = code exists,
`GREEN` = deterministic tests pass, `LIVE_VALIDATED` = real-market evidence
recorded, `PROMOTED` = explicit operator decision.

## 2026-08-20 — FORGE stops printing other people's typos

**Status: IMPLEMENTED + GREEN.** `plan.md` §17 D-22, D-23. `uv run pytest -q`
→ **527 passed, 7 deselected**, ruff check + format clean.

### What was wrong

FORGE had run **1,000 → 69,243** with single-day prints of **+1,661%**
(2026-08-02), +94% and +57%, against a median daily move of 0.029%.

Decomposition against the real lake named it exactly. On 2026-08-02 one
member — *Vanguard Resonant Cypher*, type 95640 — printed `close 10.07 →
22,450.00`, a **+222,839.4%** return, at a **0.75%** live weight. That single
member-day contributed **+1,661.59%** of the +1,661.37% the index moved. All
100 members were priced; no gap, no NaN, no missing bar.

**The chain-link was never at fault.** §19.1's composition-churn fixture is
correct and stayed green the whole way through. The poison was one line
upstream of it: `returns = closes.pct_change()` consumed **raw** closes, and
this repo's own §0 check #4 had already measured that CCP does not filter
outlier prints (`close/low` reaching 12.8 billion×). The ATR path has
winsorized for precisely that reason since Phase 2. The index path never did.

And "it reverts tomorrow" is not a defence: an arithmetic weighted-return
index can gain 222,839% and can only ever give back 100%. The level ratchets.

### What it cost

`power_index = Δref/ATR_ref` measured **1,478**, so every printed RRS sat in a
−1,479 band — digest, `board`, `brief`, the desk columns. RRS is one of the
four gates in the built-in setup, so the digest's "Nothing clears costs today"
was not an honest zero. It was a broken gate.

### The fix

- **`winsorized_member_returns`** clamps each member's daily return at `k ×`
  its own rolling median absolute return — same `k`, same window, same
  clamp-and-flag shape as `atr.winsorized_true_range`, because it answers the
  same measured fact. Config: `composite_return_clamp_k` (8.0), `_window`
  (60), `_floor` (0.20).
- **The floor is a fallback, never a lower bound.** Where a member has fewer
  than five observations the ceiling is UNKNOWN, and UNKNOWN clamps rather
  than passing through (§4). Clipping the ceiling *upward* to the floor would
  have handed a normally-stable member permission to print exactly the outlier
  this exists to catch.
- **Returns are computed explicitly**, not by `pct_change`, so a member needs
  a real bar on both `t-1` and `t`. pandas 3.0 no longer pads; pandas 2.x did,
  and would have booked a member's whole post-gap re-rating as one day. The
  code no longer depends on which is installed.
- **Clamped-day counts ride in every index's diagnostics** — clamping is
  visible, never silent.
- **`clamp_settings()`** reads the knobs in one place, because five call sites
  build indices and §19.1's whole point is that they share one engine.

### Fixtures first (§11 D5)

- `forge_outlier_2026-08-02.csv` — the **real** 100 members around the
  incident, with a provenance JSON naming the culprit type, its two closes and
  the measured returns.
- A test that builds the same fixture with the clamp **disabled** and asserts
  the >1,000% day reappears, so the clamp is demonstrably the fix.
- Synthetic spike-and-revert and 45-day-gap fixtures, each paired with an
  unclamped control.
- **The existing golden index fixture needed no regeneration.** On clean data
  the clamp touches nothing — the strongest evidence available that it is
  surgical rather than a general smoothing.

### Measured after

| | before | after |
|---|---|---|
| FORGE level over 415 bars | 1,000 → 69,243 | 1,000 → **981** |
| median abs daily move | 0.029% (with +1,661% days) | **0.340%** |
| max abs daily move | +1,661% | **2.08%** |
| `power_index` | 1,478.27 | **−3.28** |
| RRS, middle 84% of the universe | all ≈ −1,479 | p5 **−2.20**, p50 **+3.12**, p95 **+6.73** |
| digest candidates | 0 ("honest zero") | **25** |
| clamped member-days | — | 101,616 of 590,522 (17.2%) |

Nothing here touches a frozen formula, and the NOT PLAUSIBLE verdict is
unaffected: it rests on measured round-trip friction from the live book and
never reads RRS.

### Found while verifying, NOT fixed — the RRS tail is a per-type ATR problem

With the index fixed, **84.3%** of the tracked universe lands at p5 −2.20 /
p50 +3.12 / p95 +6.73. The remaining **15.7%** carries abs(RRS) > 10 and
**2.6%** exceeds 1,000 — because those types' own ATR is effectively zero
(measured: *Power Couplings* ATR **4.16e-11**, i.e. 1.6e-10% of close, giving
RRS −905 billion). `atr_last` rejects `atr <= 0` but a tiny positive ATR
passes, and `rrs = (Δsym − power_index × ATR_sym) / ATR_sym` divides by it.
This is independent of the index and was entirely masked by the −1,479 offset.
It lives on the frozen RRS/ATR surface, so it is recorded rather than patched.

## 2026-08-20 — The compatibility-date guard, salvaged from the parallel Phase-0 build

**Status: IMPLEMENTED + GREEN.** `plan.md` §17 D-21. `uv run pytest -q` →
**509 passed, 7 deselected** on Windows, ruff check + format clean, selftest
**12/12**.

Branch `claude/phase-0-gate-checklist-oucoil` was a parallel Phase-0 build
from another session, not an ancestor of this line. Before it was retired it
measured one thing this line did not know, and that measurement is now here.

### The guard

- **A pinned `X-Compatibility-Date` that is still in the future on CCP's
  UTC-11 clock is rejected on every ESI route** with a plain HTTP 400. The
  parallel branch hit it on 2026-08-18 (commit a7f5872) and lost every request
  until the pin was corrected. It is not a degraded run; it is a total outage
  caused by one config value, and it is invisible offline.
- **`timeutil.esi_compatibility_today`** is now the single place that clock is
  computed — UTC minus eleven hours, as a date.
- **`selftest` check 12, `compatibility date`**, fails any pin that is not at
  least **one full day** past on that clock. ESI itself would accept a pin
  equal to its own UTC-11 date; the extra day exists so a pin cannot clear
  offline and then start failing mid-run as the clock rolls over. A malformed
  pin is a named failure, never a crash. `selftest` is 11 checks → **12**.
- **The §11 D2 decision is untouched** — pinned, never floated — and so is the
  pinned value `2026-08-18`, which the guard measures as two days past and
  therefore sendable.
- The retired branch is preserved as tag **`archive/phase-0-first-light`**.

### Test-gate portability (Windows)

- `test_selftest_parity_check_passes_on_a_matching_file` interpolated a
  `tmp_path` straight into a TOML basic string. On Windows that lands
  backslashes where TOML reads escape sequences, and a `\U` inside
  `C:\Users\...` aborts the parse — the offline gate failed on the operator's
  own machine and nowhere else. The path is now written with `as_posix()`.
  Same class as the preceding GL-less-machine fix: the gate has to be green
  where it is actually run.

## 2026-08-20 — The desk: indices, operator setups, and the learning loop (third + fourth directives)

**Status: IMPLEMENTED + GREEN.** `plan.md` §19 and its §17 D-14…D-20 rows.
**509 offline tests green** (151 new, including 31 GUI tests run offscreen),
ruff clean, selftest **11/11**. LOC: **27,399** — 18,049 product (2,972 of it
the desk), 1,435 vendored, 7,880 tests, 35 launcher.

The hard line did not move: **no order execution, no client automation**. The
paper ledger and real-fill recording remain the whole execution surface.

### The index layer — `indices.py`, `config/sectors.jsonl`

- **One index engine.** `signals/composite.py` gained turnover/equal weighting,
  explicit membership and a ticker, and now serves FORGE, FORGE-EW and every
  sector index — no second construction path to drift.
- **FORGE** is turnover-weighted, chain-linked, base 1000, OK-tier members
  only. **Weighting is ISK turnover (units × price), never raw units** — raw
  units would make the index ~100% Tritanium. **FORGE-EW** inherits FORGE's
  membership exactly; `FORGE-EW − FORGE` is the breadth read and renders
  wherever FORGE does.
- **Nine seeded sectors** with real market-group subtree roots read from the
  live SDE, each able to set its own unit floor. A sector under its minimum
  member count renders **UNKNOWN with its reason**, never merged into a
  neighbour. `sector_for_type` returns None rather than falling back to the
  market index, so an unresolvable RRS scope is UNKNOWN.
- **Golden fixtures first** (§11 D5), including an adversarial churn case: a
  member joining at bar 60 priced 1,000× the rest, with dominant turnover,
  leaves the level at exactly 1000.0 across all four rebalances.

### Membership and trading floors (Amendment 1)

- The gate is **median 30-day UNIT volume**; turnover stays the weighting
  input. Three tiers: **OK** (≥1,000/day), **THIN** (100–999/day — carried,
  charted, scanned, badged on the board and the brief, excluded from FORGE),
  **below** (lookup only).
- **Price-pinned types are excluded from the index**: a close that did not move
  at all across the window is held by an NPC vendor, and a flat line absorbs
  index weight while reporting nothing. Still tradeable, still chartable.
- Rebuilt against the full lake and recorded in `plan.md` §11 D3 with the old
  derived floor left visible as superseded text: **OK 1,002 · THIN 999 ·
  below 17,151**, tradeable universe **2,001**, index-eligible **999** after
  3 pinned names came out; added 1,418, dropped 2,071.
- The cost is recorded beside the rule: the OK tier carries **33.1%** of the
  region's median daily ISK turnover, THIN another 9.9%. That ISK is given up
  on purpose, buying exit-ability with coverage.
- `state.db` gained an additive column migration (schema v2) — it holds the
  paper ledger and the watchlist, so it is migrated, never rebuilt.

### The operator setup engine — `setups.py`, `config/setups.jsonl`

- Nine typed condition kinds, all from daily H/L/C/V/order_count. Long-only.
  Validated loudly on load: an unknown kind, a misspelled parameter, a bad
  enum or an out-of-range value **stops the load and names the file and line**.
- Evaluation is tri-state; any UNKNOWN sinks the setup, and every result
  carries the reason it came out as it did.
- `backtest --setup NAME` measures an operator setup on the built-in rule's
  cost realism, horizons and limitations statement. The per-bar evaluator this
  needed is pinned to the last-bar evaluator by a parametrised test over every
  condition kind.
- `near_level` is **refused over history** rather than approximated: the level
  store is built from the whole series, so evaluating it per-bar is lookahead.
  The setup produces zero instances and the study says why.
- SMA/EMA/`ema_cloud`/`cross_within` are new indicator code and got golden
  fixtures first. An EMA is seeded on the SMA of its first `length` bars, not
  on bar 1, so "above the rising 21 EMA" cannot fire on bar 2.
- Three example setups ship, all marked `"example": true`.

### The scanner — `scanner.py`, `scan` / `setups` CLI

Built-in rule plus every enabled operator setup, grouped, with **honest zero
per setup next to its examined count**, UNKNOWN counted separately from
rejection, friction and book age on every hit, and the THIN badge. The
backtest banner is now one function in `backtest.py`, used verbatim by the
digest, MARKET and SCANNER.

### Qualified reasons — `reasons.py`, `config/reasons.jsonl`

- An opening requires a thesis, a setup tag and **at least one like tag**; a
  pass (`not_today` / `bad_signal`) requires **at least one dislike tag**. No
  tags, no record — and the refusal itself lands in the ledger.
- A typo'd tag is a loud error, not a dropped one.
- `not_today` clears today's queue only and **never** touches Focus.
- New CLI: `paper pass`, `reasons`. **Breaking:** every `paper open` call site
  now requires `--setup` and `--like`.

### The learning loop — `learning.py`, `learning` CLI

- Per setup and per tag: sample count, win rate with **Wilson lower bound**,
  average and median net R, expected R by shrinkage toward a **zero prior**,
  freshness decay — through the vendored `expected_r` engine.
- Ranking is evidence-weighted: 3-for-3 cannot outrank 40-for-70, and every
  UNKNOWN sorts below every measured setup. Below 20 closed trades everything
  reads UNKNOWN.
- **Regret tracking**: every recorded pass is measured forward on the
  backtest's horizons and cost realism. A pass is "right" only when the
  avoided trade would have lost money net of both haircuts and sales tax.
  Pending windows are pending; unpriceable passes are UNKNOWN.
- The digest may name a best and worst setup, gated at 20 closed trades.
- It never edits a setup, changes a frozen formula, or promotes anything.

### The desk — `src/evescreener/gui/`, `gui` CLI, `launch_gui.py`

- Eight pages: **MARKET · CHARTS · BOARD · FOCUS · SCANNER · PAPER ·
  LEARNING · HEALTH**.
- **Qt is optional and proven so**: `tests/test_headless.py` walks the import
  graph and a subprocess check asserts the CLI never puts PySide6 in
  `sys.modules`. §10.6's no-GUI non-goal is revoked (§17 D-14); §2's 42k-LOC
  lesson is now enforced structurally. The desk is 2,972 LOC.
- **The refresh timer is safe by construction**: `gui/data.py` has no ESI
  client, and a test proves nothing under `gui/` imports `httpx`, `urllib` or
  anything named `esi`. The desk shows staleness rather than curing it.
- **No candlesticks** — the bar contract has no `open`. Price is a line with
  the measured high/low envelope, over the frozen AVWAP σ ladder, SMA/EMA
  overlays, a shaded EMA-cloud ribbon, and the **HV levels, pivots and
  round-ISK levels `levels.py` has computed since Phase 2 and nothing had ever
  drawn**. Volume and participation subpanes, setup markers, open positions.
- **One chart window that re-points**, never a stack.
- **Blanks at the bottom whichever way a column sorts** — the table orders its
  own rows, because Qt's comparator reverses under a descending sort. Sorting
  never refetches.
- **Focus never auto-removes**; the only path is a button behind a confirm.
- **Paper Buy on every surface** through one prefilled form (live ask walk with
  book age, ATR stop, anchored-value target, setup tag from whichever setup
  fired) calling the same `PaperLedger` methods the CLI calls — a stale-book
  refusal renders inline *and* is recorded. A prefill that could not be
  computed is left empty with its reason.
- Verified on the live data directory: all eight pages open against 2,001
  tracked types with a 223-minute-old book correctly rendered as STALE.

### Config

New `[gui]` section (refresh, chart bars, SMA/EMA lengths, cloud lengths,
overlay toggles) and two new `[universe]` keys. `selftest` grew from 7 checks
to **11**: membership floors, sector map, setups, reason vocabulary.

## 2026-08-20 — Operator workflow port: watch, brief, board (second directive)

**Status: IMPLEMENTED + GREEN.** The desk surfaces the operator lives in on
TradingBotV3, ported to the CLI/digest world per the new `plan.md` §18 and its
§17 D-13 deviation row. **358 offline tests green** (21 new), ruff clean,
selftest 7/7. LOC: 18,296 (11,575 product, 1,435 vendored, 5,286 tests).

- `brief.py` — the new module. `build_brief`/`render_brief`: one type fully
  read (bands + σ zone, tri-state gates, RRS, participation, ATR/risk unit,
  nearby levels, priced tiers with breakeven AND round-trip friction, book
  freshness, flags) — the per-symbol desk chart, in text.
  `build_board`/`render_board`: the D1 strength-board analogue over the
  tracked universe plus watchlist, sortable by value/strength/change, blanks
  at the bottom, honest footer counts. `watchlist_summary`: the compact rows
  the digest carries. All three are **observation surfaces** (§18.1): types
  that cannot clear costs are shown with their friction printed, never hidden
  and never presented as opportunity; the screen's honest-zero panel is
  untouched.
- `watch add|remove|list` CLI over the existing watchlist table
  (`universe.add_watch/remove_watch/watchlist_entries`): add resolves against
  the SDE loudly; remove is the only removal path and only the operator
  reaches it; re-adding updates, never duplicates.
- Digest: a **Watchlist** section renders every name every day — unresolved
  and bar-less names say so and say what to run. Wired into `digest` and the
  daemon's digest job.
- `screen.setup_params()` extracted so screen, backtest and the new surfaces
  evaluate the ONE setup definition; `_composite_and_bars` now also returns
  the unfiltered lake so watchlist names below the liquidity floor keep their
  bars.

## 2026-08-20 — v1 built in one push (operator directive 2026-08-20)

**Status: IMPLEMENTED + GREEN. Nothing is LIVE_VALIDATED yet** — the
consolidated live-validation checklist in `CURRENT_CHECKPOINT.md` is owed, and
every gate on it is an operator action.

Phases 0–6 of `plan.md` §8 collapsed into one build, plus the two promoted
studies. **337 offline tests green, 7 network-marked live tests** (all passing
against real endpoints), ruff clean. **17,134 LOC** — 10,751 product, 1,435
vendored, 4,948 tests. That is **2,134 over §1's ≤15k budget**, which operator
directive 2026-08-20 authorized for the added studies while requiring the count
be stated; the product surface alone is 10.5k.

### Plan-level edits

- `plan.md` §12 — paper trading platform, with the §12.4 verdict tracker
  **frozen before the first trade**.
- `plan.md` §13 — historical viability backtest: hypothesis H1, the setup
  defined mechanically, the data-derived slippage haircut, the §13.6 verdict
  rule and the §13.7 limitations, all **frozen before the study ran**.
- `plan.md` §14 — destruction lead-lag: hypothesis H2 and the §14.3 pass rule
  including a placebo control, **frozen before measurement**.
- `plan.md` §15–§16 — cross-region scan, viability report.
- `plan.md` §17 — every deviation under operator directive 2026-08-20 (D-1…D-9)
  and the status of the six §0 named checks.

### Foundation

- uv/`pyproject.toml` with the locked runtime set (`httpx[http2]`, `pandas`,
  `pyarrow`, `numpy`) and dev set (`pytest`, `ruff`); no per-file lint
  exemptions anywhere, including vendored code.
- `config.example.toml` with every key commented; `config.toml` gitignored;
  `selftest` fails on key-set divergence.
- `paths.py` — one data-dir resolver, atomic writes, append-only JSONL.
- `timeutil.py` — tz-aware UTC everywhere; downtime is the only boundary.

### Data layer (§3)

- `esi/client.py` — descriptive UA, pinned `X-Compatibility-Date`, ETag on
  every request, and **never a fetch before the stored `Expires`**. A
  marginally-fresh page inside a multi-page sweep is waited out; a page needing
  a longer wait leaves the sweep reported **partial**, never silently presented
  as complete. 5xx-only bounded retries, 429 sleeps `Retry-After`, 420 is a
  full stop, per-feed circuit breaker.
- `esi/budget.py` — 12,000-token/15-min `market-order` accountant self-capped
  at 6,000 with the server's headers believed over the local tally; history
  limiter at 150/min; legacy error-limit guard that yields at 25 remaining
  instead of riding to the 420.
- `store/db.py` — SQLite (WAL): ETags, the sweep telemetry ledger, SDE tables,
  universe, watchlist, anchors, feed health, freight quotes, destruction,
  history gaps.
- `store/lake.py` — Parquet lake, atomic writes, diff-append.

### Bars, universe, census (§3.6, §4, §8 Phase 1)

- **The frozen bar contract**: `["datetime","high","low","close","volume",
  "order_count"]`, `close ← ESI average`, **no `open` and none synthesized**.
- `sde.py` — types, marketGroups and mapSolarSystems from CCP's per-build jsonl
  bundle; watchlist resolution names every unresolvable entry loudly.
- `universe.py` — liquidity floor on median 30d ISK turnover **and** median
  `order_count`; types falling out are flagged, never deleted.
- `census.py` — scores a 9×7 grid of candidate floors and derives the floor by
  a rule stated before the measurement; emits percentile distributions, a
  market-group breakdown and data-quality counters.

### Signals (§4, §6)

- `signals/avwap.py` — **invariant #1**: running-AVWAP volume-weighted σ with
  `tp = close`, vectorized. `tests/generate_golden.py` carries the upstream row
  loop verbatim and asserts a 1e-9 match, so the port is proven, not claimed.
  `segmented_band_series` applies it piecewise: anchors are events, not sliding
  windows.
- `signals/atr.py` — one Wilder ATR; TR winsorized at 8× rolling median with
  clamped bars flagged; ghost days excluded rather than clamped.
- `signals/levels.py` — the `levels.py` port with `open` dropped, plus a
  round-ISK level family that earns conviction through the same touch
  statistics as any other level.
- `signals/rrs.py` — benchmark-agnostic RRS, vectorized across bars. An
  unresolvable cohort is UNKNOWN; the upstream fallback-to-`"SPY"` is not
  ported.
- `signals/composite.py` — the Forge Composite: turnover-weighted,
  chain-linked, 10% single-name cap, auditable diagnostics.
- `signals/anchors.py` — patch dates as anchors, point-in-time, with the
  fresh-anchor ambiguity flag and truncation marking.
- `signals/setup.py` — the mechanical setup definition shared by screen and
  backtest. Tri-state gates; UNKNOWN always fails. No momentum branch exists.
- `scoring.py` — the bridge from the vendored expected-R engine to EVE inputs.
  Quality points come only from what this system measures, **including the net
  edge**, so the ranked quantity is net-expected-R (§5). Realized R comes from
  the operator's own closed paper trades; with an empty ledger the blend weight
  is 0 and every row says "structural prior only" rather than presenting a
  guess as a measurement.
- `patchnotes.py` — the patch-notes watcher (§2's one surviving `market_prep`
  idea, §9 R9's tripwire). It appends **candidates** and can never anchor. The
  third-party feed is size-capped and any document declaring a DOCTYPE or
  ENTITY is refused rather than parsed.
- `vendored/` — `expected_r.py` and `indicators/`, with `VENDORED.md`.

### Costs, screen, delivery (§5, §11 D6)

- `costs.py` — sales tax on every sell, broker fee on posted orders only,
  relist surcharge, escrow as capital-days, depth-walk impact,
  `breakeven_move_pct` per tier. Missing depth is UNKNOWN, never zero.
- `books.py` — sweeps reduced in memory to `book_summary` with depth walks,
  `p5_price`, the spoof share and the station/structure share. Raw books are
  never persisted.
- `screen.py` — ranked candidates; a setup that cannot clear breakeven at the
  smallest tier is not shown; a stale book drops the row with a count.
- `digest.py` — webhook with the ported result contract plus `rate_limited`;
  numbered splits, never silent truncation; honest zero publishes the counts
  that explain it.
- `daemon.py` — one asyncio scheduler owning the locked §11 D3 cadences.

### The studies and the experiment (§12, §13, §14, §15, §16)

- `backtest.py` — §13.6 applied literally, with per-type haircuts measured from
  live sweeps and `haircut_unknown` exclusions counted. Reports print their own
  limitations.
- `killmails.py` — EVE Ref archive backfill and R2Z2 poller (RedisQ is sunset
  and not built against), `destruction_z`, and the §14.3 study with a
  within-day shuffled placebo. Spearman significance by the stated normal
  approximation — no scipy.
- `crossregion.py` — hub-to-hub scan netting real PushX quotes. No quote, no
  row.
- `paper.py` — ask-walk taker entries from live sweeps, bid-walk exits net of
  tax, maker exit advisory-only, stale books refused, no retro-entries,
  self-impact flags, the SMALL-REAL real-fill rung, and the frozen §12.4
  verdict tracker.
- `report.py` — the viability report. Every number cites its source and date;
  missing inputs render UNKNOWN with the reason.

### Bugs found by running the real thing

- `/markets/{region}/types` lists type_ids that `/markets/{region}/history`
  404s on. `plan.md` §3.2 predicted 404s "should not occur in the steady
  state"; that is wrong, though by **far less than I first recorded**. See the
  correction below.
- The per-feed circuit breaker treated those 404s as feed failures and latched
  open permanently, turning a catalogue gap into a total ingest outage after
  2,363 of 19,152 types. 4xx no longer trips the breaker.
- The census floor grid's loosest corner captured only 88.2% of turnover, so
  the frozen derive rule could not resolve. The grid gained looser corners; the
  rule is unchanged.
- The network smoke module carried a `pytestmark` but markers alone do not
  deselect, so live tests were running in the default gate. `addopts` now
  excludes them.
- **The screen's ranking metric flattered wide books.** `net_edge` was
  `expected_move_pct − breakeven_move_pct`, but those percentages are measured
  against *different* references (the close and the bid), so subtracting them
  understated the cost of a wide spread — the exact §9 R5 failure the metric
  existed to prevent. On a real candidate with a 44% spread it scored +16.0%
  and ranked first; priced multiplicatively it scores +8.6% and ranks third.
- **`screen.py` flagged the wrong side of the structure exposure.** It warned
  when the *ask* book was structure-resident, which is always 0%; the exposure
  is entirely on the bid.
- **A 304 reported identically to a failed sweep** (`orders_seen=0`,
  `complete=false`). `SweepResult` now carries `not_modified` and a named
  `outcome`.
- **Per-type history 404s were only persisted at the end of a ~2h crawl**, so a
  killed run rediscovered thousands of them. They now flush every 200.
- **`find_instances` and `run_screen` masked the whole lake per type**, O(n×m)
  at census scale. One `groupby` up front.
- SQLite gained `busy_timeout`; without it a manual sweep during a crawl failed
  instantly with "database is locked".
- The backtest's half-split was by instance count; §13.6 says "sample
  **period**", so it is now by date.
- The viability report rendered an untouched paper ledger as "0 closed, 0 ISK"
  — an absence of evidence presented as a measurement. It reads UNKNOWN now.

### A number I got wrong and corrected

An earlier commit recorded "16,789 of 19,152 types 404 on history" and put it in
`plan.md`, `CHANGELOG.md` and `CLAUDE.md` as a measured fact. **It was not one.**
That figure was the circuit-breaker cascade — a bug in this repo, whose symptom
I mistook for a property of ESI. The completed crawl measures **241 real 404s
out of 17,325 history requests (1.3%)**. The fixes prompted by the wrong number
(a 404 must not trip a breaker; gaps belong in `history_missing`) were correct
and stand; the magnitude claim is retracted in `plan.md` §17 D-10.

### What the measurements said

The point of the build. All recorded in `plan.md` §17.

- **The universe is not what it looks like.** Of 19,152 Forge-active types,
  **14,013 have daily bars**, **4,978 return an empty history array** (an order
  book with no trades in 13.5 months) and only **241 genuinely 404**. The
  median spread across the 16,706 two-sided books is **98.8%**. Only ~932 types (5.6%) trade inside a 5%
  spread — anywhere near the 3.375% tax floor.
- **Depth is the binding constraint.** 77.1% / 55.8% / 39.6% of sell books can
  absorb 0.25B / 1.0B / 2.5B ISK. A quarter of sell books have one order
  holding more than half the resting volume.
- **The structure blind spot runs the opposite way from §9 R3's worry.** Across
  all five trade hubs, **0.0% of visible ask volume** is in player structures
  and **8.8%–98.3% of bid volume** is (Amarr is 98.3%). What you can buy is
  fully visible; part of what you would sell into may need docking rights.
- **CCP does not filter outlier prints.** `high/close` reaches 1,940,777×.
  Without TR winsorization **20.5% of tracked types would carry a risk unit
  more than twice too large**.
- **The backtest says NOT PLAUSIBLE at every horizon — on friction, not
  direction.** On the full lake (2,654 tracked types, **108,441** instances)
  the setup returns **+2.80% gross** over 10 days (55.7% win rate) and
  **+3.91%** over 20 days (56.0%), against **14.7% friction at 1× haircut**. The measured round-trip haircut
  distribution (p1 2.17%, p50 33.6%) plus 3.375% tax exceeds the 20-day gross
  edge of 4.15% even at the **first percentile**.
- **The destruction lead-lag effect does not survive.** On **473,606**
  observations, ρ=**0.027** at a 1-day lag (p=1.2e-76) against a 0.10
  threshold. The effect *halved* versus the smaller sample while p collapsed to
  1e-76 on sample size alone — exactly why the frozen rule demanded an effect
  size and a placebo rather than significance. Destruction ships as an annotation only.
- **Cross-region is the one bright spot.** Of 151,123 hub pairs, 14 clear real
  PushX freight and tax at 0.25B; best +14.44% net. It is a snapshot, not an
  edge — the haul takes days and the scan prices both legs simultaneously.
- **Rate-limit discipline held.** Zero 429s and zero 420s across 16,590+
  requests; the orders sweep used 830 of a 6,000-token self-cap.

## 2026-08-18 — Planning complete, decisions locked

- `plan.md` landed: port review of TradingBotV3, repo architecture decision
  (standalone repo + vendoring), module inventory, ESI data-layer spec with
  verified token arithmetic, bar contract (no `open`, `close ← average`),
  depth-aware cost model, signal translation table, zKillboard assessment, six
  phases with gates, risk register, non-goals.
- `plan.md` §11 added: locked implementation decisions D1–D8.
- `CLAUDE.md`, `CURRENT_CHECKPOINT.md`, this file: governance control set
  established. No product code existed yet.
