# EveTradingbot implemented history

Last reconciled: **2026-09-04**

Authoritative for what exists and the sequence of revisions. Remaining work:
`plan.md`. Where we are now: `CURRENT_CHECKPOINT.md`. Labels keep the
source-repo meanings: `IMPLEMENTED` = code exists, `GREEN` = deterministic
tests pass, `LIVE_VALIDATED` = real-market evidence recorded, `PROMOTED` =
explicit operator decision. **As of 2026-09-04 nothing is `LIVE_VALIDATED`.**

## Current implemented inventory

**This is the contract: what exists, by area. Search it before building anything
so you do not rebuild landed work.** One entry per capability, stating what
exists and the one thing about it that is easy to get wrong. It was extracted on
2026-09-04 from the dated entries below and in `docs/CHANGELOG_ARCHIVE_2026-08.md`;
where the code and this list disagree, the code is the fact — fix the list.

### ESI data layer (`esi/`, `store/`, plan.md §3)

- **`esi/client.py`** — descriptive UA, pinned `X-Compatibility-Date`, ETag on
  every request, never a fetch before the stored `Expires`; a page needing a
  longer wait leaves the sweep reported **partial**. 5xx-only bounded retries,
  429 sleeps `Retry-After`, 420 is a full stop, per-feed circuit breaker that
  a 4xx never trips. The trap: a malformed `Expires` on a 304 must **wait**
  (`unknown_expiry_boundary`, `safe_expiry`) — never restore the lapsed stored
  expiry (§22 S1).
- **`esi/budget.py`** — orders token accountant self-capped at 6,000 of 12,000
  per 15 min, history limiter at 150/min, error-limit guard yielding at 25
  remaining. The server's headers are believed over the local tally.
- **`timeutil.esi_compatibility_today`** — the UTC−11 clock; `selftest` check
  12 rejects a pin less than one full day past on it (§17 D-21). A future pin
  is a total outage, invisible offline.
- **`store/db.py`** — SQLite (WAL, `busy_timeout`): ETags, `sweep_ledger`
  telemetry, SDE tables (types, market groups, systems with `security_status`,
  `sde_stargates`, `sde_npc_stations`), universe, watchlist, anchors, feed
  health, freight quotes, destruction, `history_missing`, `haul_profiles`,
  `haul_ledger`, `meta`. Migrated by additive `ALTER TABLE`, never rebuilt —
  it holds the paper ledger and the watchlist. The trap: **`state.db` is not
  regenerable.**
- **`store/lake.py`** — Parquet lake, atomic writes, diff-append.
- **`sde.py`** — types, marketGroups, mapSolarSystems, stargates and NPC
  stations from CCP's per-build jsonl bundle. `load_sde`'s same-build no-op
  also requires every table non-empty (2026-08-26) — a stamp alone once left
  the map empty on the operator's lake. `npcStations` has no name field;
  unnamed stations render as `<system> — station <id>`.

### Bars, universe, census (`bars.py`, `universe.py`, `census.py`, plan.md §4)

- **The frozen bar contract** `["datetime","high","low","close","volume",
  "order_count"]`, `close ← ESI average`, **no `open`**. Completed days only,
  enforced at the one ESI-to-bar site (`frame_from_history`); drops counted in
  `frame.attrs["incomplete_dropped"]`; the boundary is the 11:05 UTC roll.
- **`bars.bar_freshness()`** judges bars on their own evidence, never the
  book's: days behind, and time since ingestion last wrote. Stale bars
  downgrade every gate to UNKNOWN, not FAIL. `bars_for_region()` /
  `last_close_by_region()` key by region. `ingest-history --scope hauling`
  fetches destination bars per non-home hub, bounded (default 400).
- **`universe.py`** — three tiers on median 30-day **unit** volume: OK
  (≥1,000/day), THIN (100–999, carried and badged, excluded from FORGE), below
  (lookup only); price-pinned types excluded from the index. Types falling
  out are flagged, never deleted. `watch add|remove|list` is the only path to
  the watchlist; `seed_watchlist` was deleted (§17 D-28).
- **`census.py`** — scores a floor grid by a rule stated before measurement;
  the derived floor is recorded in `plan.md` §11 D3 with the superseded ISK
  floor left visible.

### Signals (`signals/`, plan.md §4, §6 — frozen surfaces)

- **`signals/avwap.py`** — running-AVWAP volume-weighted σ, `tp = close`;
  `tests/generate_golden.py` asserts a 1e-9 match to the upstream row loop.
  `segmented_band_series` treats anchors as events. **Frozen.**
- **`signals/atr.py`** — one Wilder ATR, TR winsorized at 8× rolling median
  with clamped bars flagged. `atr.measurable_fraction` (`min_atr_fraction`
  1e-6, §17 D-29) is the **only** floor site and is enforced inside `atr_last`,
  so RRS, screen, brief, scanner, chart and paper prefill all inherit it; it
  also governs the AVWAP σ. The composite reference ATR is deliberately not
  floored.
- **`signals/rrs.py`** — benchmark-agnostic RRS; an unresolvable cohort is
  UNKNOWN (no `"SPY"` fallback). The trap: 51 healthy types still read
  |RRS| > 1,000 because they really fell that far — that is RRS working.
- **`signals/composite.py` + `indices.py`** — one index engine for FORGE,
  FORGE-EW and nine sectors: turnover-weighted, chain-linked, base 1000, 10%
  cap, member returns winsorized by `winsorized_member_returns` (k 8.0,
  window 60, floor 0.20; §17 D-22) with clamped-day counts in diagnostics.
  Returns are computed explicitly (pandas 2 vs 3 padding). An index carries
  `high == low == close` and draws as a level line.
- **`signals/levels.py`**, **`signals/anchors.py`**, **`signals/moving.py`**
  (SMA/EMA/`ema_cloud`/`cross_within`, EMA seeded on the SMA of its first
  `length` bars, golden-fixtured), **`signals/setup.py`** (the mechanical
  built-in setup; tri-state gates; no momentum branch), **`scoring.py`**
  (net-expected-R via the vendored engine; an empty ledger reads "structural
  prior only").
- **`patchnotes.py`** — appends anchor **candidates** only, dedupes on URL as
  well as (date, label) (§17 D-26), refuses any DOCTYPE/ENTITY document. It can
  never confirm an anchor; `anchors --list` shows the calendar.

### Books, depth and costs (`books.py`, `costs.py`, `spreads.py`, plan.md §5, §21, §23.6)

- **`books.reduce_orders`** keeps `best_*` (region-wide, diagnostic) and
  `exec_*` (the one venue a round trip can happen at, anchored on the asks).
  Range fails closed. `BookLake.write_partial` quarantines partial sweeps under
  a name `latest()` does not glob. **`load_validated_book()`** is the single
  completeness/executability/staleness contract; `spreads.py`, `backtest.py`
  and `paper.book_quote` read through it, and a pre-R1 snapshot prices
  nothing. The trap: a `book_summary` older than `costs.book_staleness_minutes`
  prices **nothing**, and that is correct.
- **`books.reduce_depth` / `DepthLake` / `load_validated_depth`** — a
  price-level curve per execution station from the same in-memory pages as
  `reduce_orders`; generation `(region_id, sweep_ts)` identical to the book's.
  Reachability: rests there / `region` / `solarsystem` match / numeric range
  covering stargate jump distance; everything else excluded and **counted**.
  `min_volume` orders excluded into `min_volume_excluded_qty`. `books.issued_stamp`
  reads a non-string or empty `issued` as UNKNOWN (2026-08-26). The trap: the
  station-first closure in `reduce_depth` must forward `.knows` (§17 D-35a).
- **`books.q_walk`** prices a quantity; **`depth_walk`** prices a notional.
  Past the stored curve is UNKNOWN, and a truncated curve says so. Nothing is
  extrapolated. `curves_from_depth` sorts once and walks rows (18.8 s → 1.4 s).
- **`costs.py`** — sales tax on every sell, broker fee on posted orders only
  with per-station operator-**observed** overrides (`[costs].broker_fee_overrides`,
  reaching production via `from_config`, §22 S6), escrow as capital-days,
  depth-walk impact, `breakeven_move_pct` per tier. Friction is a ratio of the
  gross move and compounds (`1 − (1−book)(1−tax)`, §22 S5a). `relist_cost` is
  withdrawn to `relist_cost_unverified()` and nothing under `src/` consumes it.
- **`spreads.py` / SPREADS page** — maker round trips anchored to the traded
  average with `DUST_BID` / `WIDE_ASK` / `NO_AVG` guards as page controls;
  `quoted_margin_pct` (never "net"), `execution_model = "none"`, an explicit
  `unmodelled_costs` list. Fill probability is not modelled and the page says so.

### Screen, digest, daemon, report (plan.md §5, §11 D6, §16)

- **`screen.py`** — ranked candidates; a setup that cannot clear breakeven at
  the smallest tier is not shown; a stale book drops the row with a count;
  `net_edge` priced multiplicatively; the structure flag is on the **bid**.
  `setup_params()` is the one setup definition shared with backtest, brief
  and scanner.
- **`digest.py`** — webhook with `rate_limited`, numbered splits, an honest
  zero published with the counts that explain it, a Watchlist section every
  day. **`daemon.py`** owns the §11 D3 cadences in one asyncio scheduler.
- **`report.py`** — the viability report; every number cites source and date;
  an untouched ledger reads UNKNOWN, not "0 closed, 0 ISK".
- **`provenance.py`** — `MeasurementReport` (as-of, membership, filters, input
  identity, denominators, command, git revision); `measure_top_performers()`
  produces the TOP figures through it (§22 S8).
- **`selftest.py`** — 12 checks including config parity with optional keys,
  the cost model derived from config, membership floors, sector map, setups,
  reasons and the compatibility date.

### Studies (plan.md §13, §14, §15)

- **`backtest.py`** — §13.6 applied literally; per-type haircuts from live
  sweeps, `haircut_unknown` counted, stressed haircut clamped to 1.0,
  `effective_samples()` from a real non-overlapping subset (§22 S5b), Wilson
  bound labelled one-sided 97.5%, `max_drawdown_pct` withdrawn (values kept
  under `backtest_withdrawn_pre_r3`). `backtest --setup NAME` measures an
  operator setup; `near_level` is refused over history (lookahead). Verdict on
  the full lake: **NOT PLAUSIBLE at every horizon, on friction**.
- **`killmails.py`** — EVE Ref backfill and R2Z2 poller (RedisQ is sunset),
  `destruction_z`, the §14.3 study with a placebo. `exact_lag_frame()` joins
  calendar days; `rotation_permutation_p()` is the cluster-aware p-value; the
  frozen rule still uses the naive one. H2 renders **`H2 UNKNOWN — confirmatory
  run absent`** everywhere; the pooled run is exploratory by declaration.
- **`crossregion.py`** — hub-to-hub scan netting real PushX quotes; no quote,
  no row. `quote_freight` is reused verbatim by hauling.

### Paper ledger and the operator's reasons (`paper.py`, `reasons.py`, plan.md §12, §19.4)

- **`paper.py`** — ask-walk taker entries and bid-walk exits from validated
  books, `fill_model` `taker`|`maker` on every open/mark/close (maker posts one
  tick inside the executable quote, pays per-station broker fee both legs,
  records queue ahead, stamped `fill_assumed`), no mid fill (a recorded
  refusal), position ids with a sequence suffix and legacy collisions recovered
  as `…#2`, SMALL-REAL real fills, and the frozen §12.4 verdict tracker
  scoring taker and maker populations apart. The trap: `prefill_for()` prices
  through `paper.book_quote`, never straight off the lake.
- **`reasons.py`** — an opening needs thesis + setup tag + like tag; a pass
  needs a dislike tag; a typo is a loud error; the refusal itself is recorded
  through `_refuse()` (§22 S7). `not_today` never touches Focus.
- **`learning.py`** — per setup and tag: Wilson lower bound, shrinkage to a
  zero prior, freshness decay through `effective_expected_r()` (the one
  expected-R contract; decay scales, never penalises), `eligible_outcomes()`
  as the shrinkage denominator, regret tracking on passes. Below 20 closed
  trades everything is UNKNOWN. It never writes `setups.jsonl`.

### Operator surfaces (`brief.py`, `performers.py`, `setups.py`, `scanner.py`, plan.md §18–§20)

- **`brief.py`** — `build_brief`/`render_brief` (one type fully read),
  `build_board`/`render_board` (the strength board, blanks at the bottom),
  `watchlist_summary`. Observation surfaces: friction is printed, nothing is
  hidden for failing to clear costs. `h2_statement()` is the only H2 renderer.
- **`performers.py`** — TOP PERFORMERS over 7 and 30 **calendar** days with a
  three-day median at each end (`MIN_ENDPOINT_BARS` 3); the raw number sits
  beside the robust one. The §20.3 prose figures are a labelled historical
  snapshot.
- **`setups.py` + `config/setups.jsonl`** — nine typed condition kinds,
  long-only, validated loudly on load (file and line named); tri-state
  evaluation; per-bar evaluator pinned to the last-bar one by a parametrised
  test; three `"example": true` setups ship.
- **`scanner.py`** — built-in rule plus every enabled setup, honest zero per
  setup beside its examined count, UNKNOWN counted apart from rejection, THIN
  badge. The backtest banner is one function used by digest, MARKET and SCANNER.

### The desk (`gui/`, plan.md §19.2, §20 — PySide6, optional)

- **Twelve pages**: DESK (FOCUS/BOARD/SCANNER tabs + the chart), MARKET,
  CHARTS, BOARD, FOCUS, SCANNER, PAPER, LEARNING, HEALTH, SPREADS, TOP,
  HAULING, SETTINGS. Exactly one `ChartPanel`, moved into whichever page
  declares a `chart_slot`.
- **Threading contract** — `heavy` pages compute on a `QThreadPool` worker from
  an immutable `Generation` (token, key, data, `job_input`) captured on the
  GUI thread; last-good-on-failure; `desk_input_key` stats the lake, book and
  depth partitions, stored hauling reports and the operator config files (not
  the clock); workers open their own SQLite connection. The AST guard fails on
  widget access or `self._running*` reads inside `compute()`. 217 s → 8.6 s.
- **Isolation** — `tests/_import_probe.py` imports every GUI module in a cold
  subprocess and rejects `httpx`, `requests`, `urllib`, `aiohttp` and any
  `esi` path; `tests/test_headless.py` proves the CLI never loads PySide6.
- **The chart** — range candles (body low→high, notch at the average, coloured
  against the previous average), degrading by slot width; AVWAP σ ladder,
  SMA/EMA, EMA cloud, HV levels, pivots, round-ISK levels, volume and
  participation subpanes, setup markers, positions. Opens on the whole series
  with a 60/120/250/all selector. No `open` anywhere.
- **Paper Buy on every surface** through one prefilled form calling the same
  `PaperLedger` methods as the CLI; the notional is a picker of
  `costs.notional_tiers_isk`; an unpriceable book reads UNKNOWN and greys the
  button. **SETTINGS** writes ntfy config to `state.db`'s `meta` table, never
  `config.toml`; nothing is delivered yet and the page says so.
- **Focus never auto-removes**; tables order their own rows; sorting never
  refetches; `verdict_banner` says `UNKNOWN — no study has run on this machine`
  rather than nothing.

### Hauling (§23 — `routes.py`, `hauling.py`, `liquidity.py`, `positioning.py`, `haulreport.py`, `haulledger.py`, `haulfreight.py`, `gui/pages/hauling.py`)

- **`routes.py`** — BFS shortest, Dijkstra `safer` with a configured
  non-high-sec penalty, `high-sec-only` on a restricted graph; UNKNOWN with
  reason and **no jump count** for a disconnected pair or unmapped system.
  `display_security` rounds half-up (0.45 is high-sec, 0.4499 is not; `0 <
  sec ≤ 0.05 → 0.1`). `jump_distance` memoised per (origin, bound), rooted at
  the station once per station. Route cache keyed on build, endpoints,
  profile, avoid list **and penalty**; UNKNOWN is cached as UNKNOWN. Jita →
  Amarr is 11 jumps via Ahbazon (0.4), 34 high-sec-only (build 3478781 fixture).
- **`hauling.py`** — ranks (item, source, destination, quantity) plans over
  every cumulative breakpoint of both books, capped by capital, exposure and
  cargo. The first chunk's marginal is its net and the search stops at the
  first non-paying size (§17 D-35). Both generations pinned per row, the older
  decides staleness, a stale leg prices nothing. Thirteen rejection reasons,
  exactly one per rejected candidate, histogrammed on page, report and CLI;
  detail capped at 50 per reason with `rejected_truncated`. `NO_ROUTE` for a
  blank or off-graph current system (D-35b); `VOLUME_UNKNOWN` for an unknown
  packaged volume; `dropped_unrankable` counted. The reliability grade is
  **quarantined**: a tripwire test fails if a grade reaches a comparison, sort
  key, filter or branch under `src/` (attribute, subscript and `getattr`
  spellings). A scan with no ship refuses; a zero hold is named on
  `scan.notes`.
- **`liquidity.py`** — measured (completed-bar daily units at the destination,
  quantiles, zero/missing days, dispersion) vs **ASSUMED** (`destination_share_prior`,
  `capture_share`), labelled on every surface; `liquidation_days` UNKNOWN on a
  zero quantile or under `min_liquidity_bars`; window from `[hauling]
  liquidity_window_days`. No `tail(window)` fallback exists (D-35).
- **`positioning.py`** — `greedy_basket` over the ranker's marginal chunks by
  conservative profit per m³, caps re-tested before each chunk,
  `non_overlapping` applied inside it (at most one plan per (type, source) and
  (type, destination), `withheld_for_overlap`), maker-refused breakpoints
  removed before packing (D-35b). Labelled HEURISTIC; always beside the best
  single-item plan.
- **`haulreport.py`** — the immutable artefact (profile, generations, SDE
  build, `calc_version = "haul-1"`, consumed levels, fee arithmetic, route
  decomposition, why-this-size with `rejected` flags, the rejected set),
  written atomically under a colon-free filename.
- **`haulledger.py` / `haul record`** — requires thesis + like tag to open, a
  dislike tag to pass, writes the refusal; resolved means both sides actual,
  proceeds alone yields a labelled `assumed_net_isk` (a forecast never grades
  itself, D-35).
- **`haulfreight.py`** — self-haul vs PushX as a **column**, opt-in (`haul scan
  --freight`), bounded to the top plans, using the plan's actual endpoint
  systems (D-35b); no quote → UNKNOWN. Fee avoided per active minute is the
  number.
- **`haul scan | profile | record` CLI** — `--from`/`--from-id` required;
  `immediate`/`maker` exit with max-wait days; `profile add` stores ship
  profiles in `haul_profiles` (omitted flags store the configured default,
  never NULL). **HAULING page** — `heavy`, off local data only, control strip
  with system autocomplete, ship picker (`no ship profiles — run: haul profile
  add` until one exists, never treated as a ship), filters in `meta`, detail
  drawer with both ladders, breakpoints, route, fee audit, liquidity pane and
  the rejected view. `along_route` mode charges the incremental detour and two
  lots of handling; extra stations are destinations only.
- **The §23.17 worked example** survives all six steps end to end: 1,200 units,
  102,416.67 / 117,375 WAPs, 4,753,687.50 tax, **13,196,312.50 ISK** net,
  10.74% ROI (`tests/test_haul_end_to_end.py`).

### Config

- `config.example.toml` mirrors `config.toml` key for key; `build_section`
  honours declared defaults so optional keys and whole optional sections
  (`[hauling]`, `[routes]`, `[gui]`) load on an old file; `selftest` parity
  knows `optional_config_keys()`. Hub station ids resolved from the SDE (Jita
  60003760, Amarr 60008494, Dodixie 60011866, Rens 60004588, Hek 60005686).
  `config/sectors.jsonl`, `config/setups.jsonl`, `config/reasons.jsonl` and
  `config/anchors.jsonl` are operator data — the desk never writes them.

### Tests, lint and build

- `uv run pytest -q` offline by default (`addopts = "-m 'not network'"`, 7
  network tests deselected); GUI tests offscreen via `conftest.py`; the desk
  fixture lives in `conftest.py`. Golden fixtures under `tests/fixtures/` pin
  AVWAP, ATR, levels, the composite (including the real FORGE outlier day),
  moving averages and the backtest. `ruff` with no per-file exemptions. The
  measured count lives in `CURRENT_CHECKPOINT.md`.
- **The agent team** (2026-09-04): `.claude/agents/` and `.codex/agents/`
  (tester, builder, reviewer, recon), `.claude/packets/`, `docs/AGENT_TEAM.md`,
  `docs/CODEX_NOTES.md`, `docs/INTERNALS.md`, `docs/README.md`, `WISHLIST.md`,
  `docs/decisions/`; `AGENTS.md` generated from `CLAUDE.md`. Checked by
  JumpStarter's `jumpstart.py check` (not vendored).

## Recent changes (2026-08-25 onward)

The last build days only. **When this section passes ~800 lines, move the older
entries into `docs/CHANGELOG_ARCHIVE_<period>.md` and leave a pointer here.**
Everything before 2026-08-25 is in `docs/CHANGELOG_ARCHIVE_2026-08.md`.

### 2026-09-04 — The JumpStarter control set: agent team, bounded read, evidence behind the rules

**Status: DOCS + CONTROL FILES ONLY. No product code changed.** Gate on the
unchanged code: `uv run pytest -q` → **1,090 passed, 7 deselected in 49.12 s,
process exit 0**; ruff check + format clean; `selftest` **12/12**.

Applied from `C:\Users\Aaron\JumpStarter` (not vendored) following its
`playbooks/retrofit.md`. The audit found 15 gaps of 25 checks before; the
control-set principles were added without rewriting what was there.

- **The agent team.** `.claude/agents/{tester,builder,reviewer,recon}.md` and
  `.codex/agents/*.toml` with this repo's toolchain (`uv run`, the `gui`
  extra per worktree), live stores (`./data/`, `config.toml`), frozen surfaces
  and an ask-first list **derived from the §11 locks and not yet confirmed by
  the operator**; `.claude/packets/PACKET_TEMPLATE.md` (packets are tracked);
  `docs/AGENT_TEAM.md` and `docs/CODEX_NOTES.md`; `.gitignore` tracks the role
  directories and packets, ignores the rest of `.claude/`. A machine-local
  `.claude/settings.json` allow-lists the gates and denies force-push, hard
  reset, `git stash` and `git add -A`; no ESI-fetching subcommand is allowed.
- **`CLAUDE.md` rewritten around a bounded read** (the "Active state at a
  glance" block, named `plan.md` sections, the inventory searched not read), a
  short-chat rule, a working agreement for agents, and **seven core rules each
  citing its incident** in the new `docs/INTERNALS.md`. Every hard invariant
  kept its wording. `AGENTS.md` regenerated byte-identical.
- **`docs/INTERNALS.md`** — sixteen entries recovered from `plan.md` §17 and
  this changelog; three say "Evidence not recovered" rather than inventing one.
- **This file restructured**: the implemented inventory above (extracted from
  the dated entries), `Recent changes` bounded at 800 lines, and everything
  from 2026-08-18 to 2026-08-21 moved verbatim to
  `docs/CHANGELOG_ARCHIVE_2026-08.md`.
- **`CURRENT_CHECKPOINT.md`** gained the "Active state at a glance" block with
  the numbers above; the consolidated checklist is the open-gates list.
- **Seven root review prompts moved** unchanged to `docs/reviews/`, dated;
  `docs/README.md` classifies every Markdown file; `WISHLIST.md` created empty
  with the §10 permanent-noes recorded; `docs/decisions/0001` created with
  **status OPEN** — only statements already on record are quoted.
- **`plan.md` §11 D8 amended** (control set widened; `AGENTS.md` is a generated
  copy, not a second truth) with the old wording visible, and **§17 D-36**
  records the retrofit.
- **Not done, deliberately:** `plan.md` (2,960 lines) was not split — it is
  cited by § number everywhere and the split is the operator's decision. The
  owner questionnaire was not asked. The ask-first list awaits confirmation.
  `jumpstart.py check .` is red on exactly the plan-size line.

### 2026-08-26 — the ship picker says why it is empty

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **1,090 passed, 7
deselected**, ruff clean. Two GUI tests.

- **An empty ship picker now reads as a fact instead of a broken control.**
  `haul_profiles` is operator data — the desk must not invent a fit (§19.2) —
  so on a fresh lake the combo populated with nothing at all, which looks
  identical to a control that failed to load. It now shows
  `no ship profiles — run: haul profile add`, and that placeholder is never
  treated as a ship: `_ship_name()` maps it to `""`, so it cannot be stored as
  a saved filter or matched against a profile, and the scan falls back to the
  ad-hoc profile and says so in its notes. The first real profile replaces it.

### 2026-08-26 — a missing `issued` stamp no longer ends the scan

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **1,088 passed, 7
deselected**, ruff clean, `selftest` **12/12**. Found by running the first
hauling scan against a freshly swept five-hub lake; two regression tests
reproduce it.

- **`oldest_issued`/`newest_issued` are normalized where the depth frame
  becomes levels.** Parquet stores them as a nullable string and pandas returns
  the gaps as float `NaN`, which is **truthy** — so a gap survived the
  `if level.oldest_issued` filter, reached `min()` beside real ISO stamps, and
  raised `TypeError: '<' not supported between instances of 'float' and 'str'`.
  On the operator's lake 556 of the Forge's 314,793 depth rows carried a gap,
  and that was enough to abort an entire scan. `books.issued_stamp` reads a
  non-string or empty value as UNKNOWN; both `curves_from_depth` and
  `books.curve_from_frame` use it, and `_oldest_issued` re-applies it because a
  curve can be built by any caller and the failure mode is a crash, not a
  wrong number.

### 2026-08-26 — the SDE build stamp is not proof the load is complete

**Status: IMPLEMENTED + GREEN.** Two regression tests reproduced the failure
before the fix.

- **`load_sde`'s same-build no-op now also requires every SDE table to be
  non-empty.** `sde_stargates` and `sde_npc_stations` arrived after the
  operator's lake was first loaded, so that lake carried the current build
  stamp with both new tables empty — and build-equality alone made the no-op
  decline to fill them, permanently. The symptom appeared far from the cause:
  `RouteGraph.from_db` built an empty map, every hauling scan ended at
  `NO_ROUTE` with "current system 30000142 is not in the stargate graph", and
  the footer showed a correct-looking build. Emptiness, not the stamp, now
  decides. A complete lake at the current build is still a cheap no-op and
  still never re-downloads.

### 2026-08-26 — §23 operator audit remediation: pickup, maker caps, freight endpoints

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **1,084 passed, 7
deselected**, ruff check + format clean, `selftest` **12/12**. **Nothing is
`LIVE_VALIDATED`; the owed live checklist remains unchanged.** Seven regression
tests reproduced the three findings before the implementation changed.

- **A missing pickup no longer becomes a free pickup.** A blank current system,
  or a system id absent from the SDE graph, now ends the scan with `NO_ROUTE`.
  Previously the pickup was labelled UNKNOWN while the plan still ranked on the
  source→destination leg alone, understating jumps, session time and ISK/minute.
  The CLI requires `--from`/`--from-id`; the desk carries the same refusal and
  reason through the shared engine.
- **Maker wait is a reachable, binding constraint.** `haul scan` and the desk
  now expose `immediate`/`maker` exit selection and max-wait days together. A
  maker breakpoint refused as `LIQUIDATION_UNKNOWN` or `OVER_TIME` is removed
  from the feasible breakpoint sequence before mixed-cargo packing, so the
  basket cannot resurrect a size the single-item scan refused. Because §23.7's
  liquidation formula is linear in quantity, the maker search stops at that
  first failed size.
- **PushX receives the plan's actual endpoints.** The optional comparison now
  quotes the source and destination station systems carried on the plan, not
  the canonical hub configured for each region. An extra destination in The
  Forge therefore remains Perimeter (for example), rather than silently
  becoming Jita; an unresolved endpoint is UNKNOWN and is never substituted.

**+283 lines, −20 across four product and five test files** before the control
file reconciliation. The §23 calculation remains pre-live-validation; no
route, ladder, ranged bid, broker fee, haul, or PushX invoice was validated by
this code pass.

### 2026-08-26 — §23 closeout: the seven residues a second audit left (§17 D-35)

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **1,077 passed, 7
deselected**, ruff check + format clean, `selftest` **12/12**. **Nothing is
`LIVE_VALIDATED`, and the owed checklist is unchanged in full** — no code
session earns any of it.

A second adversarial pass verified the remediation: eleven of twelve fixes real
(including property-verified equivalence of the `curves_from_depth` rewrite over
60 randomized frames and of the `q_walk` shortcut over ~2,800 walks, zero
differences in both), one cosmetic in production, six Low residues. All seven
are closed here, each fixture-first with a test that fails on the previous head.
**With this the §23 track's code is done**; what remains is the operator's live
gate.

- **FIX 11b was cosmetic in the real sweep path.** `reduce_depth` wraps the
  jump-distance function in a closure that searches from the station outward,
  and the wrapper did not carry `.knows` — so every actual sweep read None off
  it and an order past the 40-jump search bound counted `range_unresolvable`,
  blaming the map for a fact about the order. All three of the fix's tests
  called the primitive directly and one blessed the stripped path. Repro: a
  45-system corridor through `reduce_depth` counts (0 range, 2 unresolvable);
  it now counts (1, 1).
- **The reliability-grade tripwire could not see the access it guarded.** It
  read attribute access only, so `row["reliability"]["grade"]` — how the report
  renderer and the drawer actually read row payloads — and
  `getattr(plan, "reliability")` both passed straight through. Both spellings
  are matched now. The whole-file `liquidity.py` exemption is deleted: it
  tripped zero offenders, so it protected nothing while blinding the check
  where `liquidity_attachment` lives. The docstring says what it is — a
  tripwire, not a proof.
- **A fresh install did not know its cargo was unbounded.** The cargo spin
  defaults to 0 ("use ship profile") and a fresh install has no profile, so the
  hold resolves to 0 m³ — and every cargo test reads
  `and profile.ship.usable_cargo_m3`, so `OVER_CARGO` cannot fire for any type
  and unknown-volume types take the no-cap branch. Correct arithmetic, silent
  on screen. A scan note now says so, once, on `scan.notes`, reaching the page
  summary, the report and the CLI identically.
- **The basket's overlap guard travelled with the wrong module.** `haul_basket`
  filtered; bare `greedy_basket` packed 2,000 units out of a 1,000-unit ask,
  and every test went through the wrapper. `non_overlapping` moves into
  `positioning.py`, `greedy_basket` applies it itself and records
  `withheld_for_overlap`. §23.10's shared-consumption-ledger refinement stays
  recorded, not built.
- **The liquidity window is config.** `window_days=30` was the last analytic
  parameter still a default argument nothing reached, while the quantiles, the
  bar minimum and both priors were all config. `[hauling]
  liquidity_window_days` (default 30, optional-with-default so existing configs
  keep loading).
- **The refused size is labelled refused.** `MARGINAL_NET_NEGATIVE` keeps the
  size that stopped the search in the audit — "why not bigger" is the question
  the table answers — but it rendered identically to a viable size the ranker
  passed over. `breakpoints` carries
  `(quantity, capital_isk, net_profit, rejected)`; the report emits
  `"rejected": true` and the drawer marks `<- refused (marginal <= 0)`.
- **Epsilon twins in the candidate sizes.** Set deduplication does not collapse
  100.0 and 100.0 + 5e-10, and the step between them is a chunk of ~zero units
  whose marginal is ~zero — so the search would stop on a chunk containing
  nothing. Latent with ESI's integer `volume_remain`, reachable with fractional
  synthetic data. Candidates within 1e-9 merge, larger kept.

**+360 lines, −74** across 14 code and test files (**+482, −96** with the plan, changelog, checkpoint and example config). Behaviour changes are corrected in
`plan.md` in place with the old wording visible: §23.7 (the window is config),
§23.10 (the audit's breakpoint shape), and §17 D-35's overstated quarantine
sentence.

### 2026-08-25 — §23 remediation: twelve defects an audit reproduced (§17 D-35)

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **1,068 passed, 7
deselected**, ruff check + format clean, `selftest` **12/12**. **Nothing is
`LIVE_VALIDATED`, and the owed checklist is unchanged** — none of it was live
evidence and none of it has been earned.

An adversarial first-build audit (Fable) ran the code with concrete inputs and
came back with twelve confirmed defects. Each is fixed fixture-first, in its own
commit, with a test that fails on the audited head. In the order they cost ISK:

- **A losing trade ranked as a plan.** The marginal-net rule ran only from the
  *second* breakpoint, so a book whose smallest fillable size loses money had
  nothing to refuse it: one ask of 100 @ 100 against one bid of 100 @ 50 ranked
  at **−51.7% with zero rejections**. On a market with a 98.8% median spread
  that is most books — the page would have filled with the hundred least-bad
  losses and "Nothing clears costs today" could never have fired. The first
  chunk's marginal is its net. The search also stops there rather than
  continuing: per-unit marginal is monotonically non-increasing, so nothing
  larger can pay.
- **An unknown packaged volume skipped the cargo cap.** `cargo=None` meant the
  check never ran — a million units ranked against a 60,000 m³ hold.
  `VOLUME_UNKNOWN` refuses it, and plans the chosen objective cannot score are
  counted in `dropped_unrankable` instead of being filtered out in silence.
- **The basket double-spent measured depth.** One 1,000-unit Jita ask sold to
  two hubs packed 2,000 units. At most one plan per `(type, source)` and per
  `(type, destination)` now, with the withheld count named on the basket.
- **Liquidity measured a dead market from year-old bars.** A
  `tail(window_days)` fallback reported 500 units/day, `known=True`, reason
  empty, for a type that had not traded in a year — feeding the maker caps and
  the reliability grade. The fallback is gone.
- **The ledger laundered a forecast into an actual.** A close with proceeds but
  no cost borrowed `expected_cost_isk`, stored it as `actual_cost_isk`, and
  computed a "realized" net and a "forecast error" from it — the forecast
  grading its own homework, in the one ledger meant to turn §23.7's priors into
  measurements. Resolved now means both sides actual; proceeds alone yields a
  labelled `assumed_net_isk`.
- **A NULL profile column deleted rows.** `handling_minutes` defaulted to 0.0
  where the class default is 4.0; zero handling on a zero-jump detour made
  ISK-per-minute UNKNOWN and the plan vanished from the ranking.
- **`along_route` with no destination charged the whole trip** — the opposite
  of what the mode is. The profile refuses to construct; the page, whose
  control strip can always be half-filled, falls back to dedicated and says so.
- **The avoid list was reported as a security block.** The probe re-ran the
  route with the default profile and no avoid list. It asks the two questions
  separately now, so the operator is told which of his own constraints severed
  the pair.
- **The reliability grade is quarantined and the quarantine is proved.** Its
  weights are invented, so a test fails if a grade reaches a comparison, a
  branch, a sort key, a filter or a comprehension anywhere under `src/` — and
  the detector is self-tested against three synthetic gates.
- **The scan was profiled rather than assumed.** One pair over 5,000 types took
  **18.8 s**, of which `curves_from_depth` was **18.4 s** and the ranking loop
  **0.4 s** — pandas' per-group constant paid five thousand times. The index
  sorts once and walks rows: **18.8 s → 1.4 s**, and 9.0 s → 0.5 s per region.
  Report detail is capped at 50 rejections per reason with `rejected_truncated`
  naming what was omitted (counts stay whole), the control strip gets a 500 ms
  debounce, and `q_walk` answers an exact breakpoint from the stored cumulative.
- **Three residues.** `sweep_region(stations=...)` without a `bound` used
  `DepthBound(0, 0)` and truncated every curve to one level — now a
  `ValueError`, because it looked like a thin market and was a caller mistake.
  An order beyond the graph's search bound is `range_out_of_reach` rather than
  `range_unresolvable`, which blamed the map for a fact about the order. The
  page's cargo box defaulted to 60,000 m³ and silently overrode the selected
  ship's hold; it defaults to "use ship profile" now.
- **Doc drift.** Gate stamps refreshed; §23.10, §23.3, §23.13 and §23.7
  corrected in place with the superseded wording left visible.

### 2026-08-25 — §23 H1–H4 handed off: the hauling tab, and what it owes

**Status: IMPLEMENTED + GREEN across H1a, H1b, H2, H3 and H4. NOTHING IS
`LIVE_VALIDATED`.** `uv run pytest -q` → **1,028 passed, 7 deselected**, ruff
check + format clean, `python -m evescreener selftest` → **12/12**.

- **The end-to-end test is the one that would catch a seam.** Synthetic ESI
  pages for two regions go in at the top; a governed sweep reduces them twice;
  the depth lake writes and the validator reads them back; the engine ranks;
  the report renders; and the desk page paints — and the **same
  13,196,312.50 ISK** survives all six steps. Every number in it was written
  into `plan.md` §23.17 before any of the code existed.
- **The desk cannot paint a generation the lake has replaced**: depth
  partitions and stored hauling reports are both in `desk_input_key`, with a
  test for each.
- **A boundary the page must never cross is now asserted by name.**
  `haulfreight` reaches PushX through `crossregion`, which imports `httpx` at
  module scope — so a test fails if any module under `gui/` imports either. The
  page may *show* a freight column; it may not be able to *fetch* one.
- **The rejection vocabulary is complete, and one label was wrong.**
  `MIN_VOLUME_BLOCKED` now fires where it belongs: when the bids that would
  have absorbed a bigger size are the ones demanding a minimum parcel, the
  refusal names that rather than reporting a destination that merely looks
  shallow. And a source with less depth than the destination would buy now
  refuses **nothing** — every quantity it could supply was priced, and the
  deeper ones were never candidate plans, so a `DEST_DEPTH_SHORT` there would
  have named a side that is not short at all.
- **The reachability search is rooted at the station, once per station.**
  Jump distance is symmetric on a stargate graph, so searching from each
  order's system gives identical answers — and a real Forge sweep carries
  orders resting in thousands of distinct systems, so it would have built
  thousands of distance maps to get them. Same numbers, three orders of
  magnitude less work and memory, and a test pins the direction because the
  expensive one is correct enough to pass everything else.
- **LOC: 8,953 for the track against a ≤7,000 target — 1,953 over, recorded as
  §17 D-34.** 4,024 in seven new core modules plus the page, 3,021 in eleven
  new test modules, 1,908 added to existing files (658 of them the depth
  reduction in `books.py`). Executable lines, excluding blanks, comments and
  docstrings, come to 5,387 — inside the target, but the target said "lines
  including tests" and the first number is the honest reading. Nothing was
  trimmed to make it: no test was dropped, and no explanation of *why* a rule
  exists was cut, because this repository's defence against re-litigating
  settled decisions is that the reasoning sits next to the code.
- **The consolidated owed live-validation checklist is in
  `CURRENT_CHECKPOINT.md`**, and every item on it is an operator action: ten
  in-game route checks including a 0.45–0.49 boundary system, ten quote/depth
  checks against the ladders, one unit sold into a ranged bid (one of them
  structure-resting) with where the goods and ISK landed, one `order_id`
  tracked to settle whether `issued` moves on reprice, the measured depth size
  per five-hub generation, broker-fee overrides for two hubs, a two-week
  shadow, and then the deferred H0 keep/park comparison.
- **Said plainly, because the tab will look empty and that is not a bug:** the
  Forge's median spread is 98.8%, ~932 types trade inside a 5% spread at all,
  and 10–14 of 151,113 hub pairs cleared costs when §17 measured them. A short
  list or an honest zero is the expected normal state, and the rejected view
  with its reason histogram is the more informative half of the page.

### 2026-08-25 — §23 H4: charge the detour, and price your own flying time

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **1,021 passed, 7
deselected**, ruff check + format clean, `selftest` **12/12**. **Not
LIVE_VALIDATED**: no detour has been flown and no quote here has become an
invoice.

- **`along_route` mode charges the incremental jumps, not the trip.** The
  baseline is the route the operator was flying anyway; the detour is
  `(current → source → destination → intended) − (current → intended)`. If the
  destination is on the way, the detour is **zero jumps** — and the plan still
  pays **two lots of handling**, because loading and unloading are real minutes
  even when the flying is free. An intended destination the graph cannot reach
  rejects the pair rather than quietly falling back to a dedicated trip.
- **Extra stations are destinations, never sources.** An operator's extra
  station is somewhere he wants to *deliver* to; ranking plans that buy from it
  would rank a book he chose that station despite, not because of.
- **Self-haul vs PushX is a column, never a dependency.**
  `crossregion.quote_freight` is reused **verbatim** — same cache, same
  staleness haircut, same "a failure is UNKNOWN with its reason". No quote →
  the column reads UNKNOWN and the self-haul row stays exactly as priced from
  swept depth. Quoting is **opt-in** (`haul scan --freight`) and bounded to the
  top plans, because each quote is a request to somebody else's service.
- **What the column actually answers** is "what is my flying time worth on this
  haul": the fee avoided, per active minute. That is the only form of the
  question with a number in it.
- **Two things from `scan_cross_region` are deliberately NOT inherited.** Its
  same-notional two-leg fill walk is known-optimistic and `q_walk` supersedes
  it; and its "needs docking rights" flag contradicts the reachability doctrine
  the depth reduction already applies — **range decides**, not ownership
  (§22 S2a). A test asserts the phrase never appears on a hauling row.

### 2026-08-25 — §23 H3: getting out is assumed, and the page says which parts

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **1,010 passed, 7
deselected**, ruff check + format clean. **Not LIVE_VALIDATED**: no liquidation
estimate here has met a real sell order.

- **`liquidity.py` separates measured from assumed, on the row.** Measured:
  daily units at the destination over **completed bars only** — median,
  low/base/high quantiles, zero days, missing days, a recent-vs-window volume
  ratio and a robust price dispersion. Assumed and **labelled ASSUMED wherever
  they appear**: `destination_share_prior` (regional history carries no station
  split, so it is not derivable from this lake at any price) and
  `capture_share`. `liquidation_days = q / (units × share × capture)`.
- **A zero or unmeasurable quantile is UNKNOWN, never fast.** Fewer than
  `min_liquidity_bars` completed bars does the same. A dead market does not
  become tradeable by dividing by something small.
- **The maker exit is the one the assumption may refuse.** Selecting it makes
  the liquidity caps binding: an UNKNOWN liquidation rejects the size as
  `LIQUIDATION_UNKNOWN`, and one slower than the operator's own patience is
  `OVER_TIME`. An immediate exit charges ISK-days over travel time, because
  that is the whole period the capital is committed; a maker exit does **not**
  inherit that number — travel time is no answer to "how long will this take to
  sell".
- **The maker scenario is display only and stamped as such.** Proposed list
  price one tick inside the destination's best ask, the queue ahead of it, the
  per-station broker fee (§21 R4), and the **downside**: what dumping into the
  bid today would actually pay. Undercutting the whole book puts nothing in
  front of you, which is exactly the position that invites being undercut back
  — so the competing depth is reported beside the zero rather than instead of it.
- **The reliability grade is about the data, and says so in its own note.**
  Generation freshness ×2, depth completeness ×2, destination bars, route
  provenance; **any UNKNOWN component caps the grade at D**. An A means
  "everything this row rests on was measured", not "this trade works".
- **`positioning.py` fills the rest of the hold, labelled HEURISTIC.** Greedy
  over the same marginal chunks the ranker already priced, ordered by
  conservative profit per m³, with every cap re-tested **before each chunk**
  rather than once at the end — a cap tested against the total is a cap already
  exceeded on the way there. An item whose packaged volume is unknown is
  skipped and named. The basket is built by one function for the CLI and the
  desk alike, and always sits **beside** the best single-item plan.
- **`ingest-history --scope hauling` fetches destination bars** for the
  candidates each non-home hub actually carries a bid for, bounded per region
  (default 400) with the bound reported, inside the existing 150/min self-cap,
  and 404s recorded in `history_missing` exactly as the home region's are.

### 2026-08-25 — §23 H2: the engine, the report, the `haul` CLI and the page

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **982 passed, 7
deselected**, ruff check + format clean, `selftest` **12/12**. **Not
LIVE_VALIDATED**: no plan this ranks has been flown, and no ladder has been
compared to a market window.

- **`hauling.py` ranks (item, source, destination, quantity) plans** against
  the operator's own constraints. Candidate sizes are **every cumulative
  breakpoint of both books**, capped by capital, exposure and cargo — between
  two breakpoints the marginal price does not move, so the best plan in an
  interval is always at one of its ends, and the whole quantity space reduces
  to a handful of numbers.
- **§23.17's worked example passes end to end**, from synthetic sweeps through
  `reduce_depth` to the ranked plan: 1,200 units, 102,416.67 / 117,375 WAPs,
  4,753,687.50 tax, **13,196,312.50** net, **10.74%** ROI.
- **Both generations are pinned on every row and the older one decides.** A
  row joining a 15-minute-old book to a 200-minute-old one reports 200, and if
  either region is stale the pair prices **nothing** — not the fresh leg, not a
  partial row. It renders as an UNKNOWN row carrying its reason.
- **The rejection vocabulary is enforced and queryable.** Thirteen reasons,
  every rejected candidate carrying exactly one, and a histogram on the page,
  in the report and in the CLI output. "Nothing cleared" now comes with its
  denominator.
- **The last chunk has to pay for itself.** `MARGINAL_NET_NEGATIVE` is what
  stops the ranker choosing the largest fillable size: somewhere the book stops
  rewarding volume, and the objective's chosen quantity is recorded beside what
  max-profit, max-ROI and max-ISK/m³ would have picked when they differ.
- **A blocked route says which kind of blocked.** A pair with no route at all
  is `NO_ROUTE`; one the operator's own security profile refuses is
  `ROUTE_BLOCKED_SECURITY`, which is a fact he can act on.
- **`haulreport.py` writes the immutable artefact** — profile, generations, SDE
  build, calc version `haul-1`, every walk's consumed levels, the fee
  arithmetic, the route decomposition, why-this-size, and the rejected set —
  atomically, under a colon-free filename a Windows desktop can hold.
- **`haul scan | profile | record`** joins the CLI, additively. `profile`
  stores ship profiles in `state.db` (an omitted flag stores the configured
  default rather than a NULL that would read back as an instantaneous jump);
  `record` is the paper-haul ledger, which requires a thesis and a like tag to
  open and a dislike tag to pass, and **writes the refusal itself** — §22 S7's
  defect, not repeated.
- **A scan with no ship refuses rather than guessing a hold.** Cargo is what
  caps the size; a guessed hold ranks plans the operator cannot carry.
- **The HAULING page** sits after SPREADS, `heavy = True`, computing in the
  PageJob worker off local data only. Control strip with system autocomplete,
  ship picker, capital/exposure/minutes/max-jumps/security/objective, filters
  remembered in `state.db`'s `meta` table (never `config.toml`, which is the
  hand-edited contract of §11 D1). Detail drawer: both ladders with the levels
  consumed, the breakpoint table, the route's systems, the fee audit, the
  liquidity pane and the rejected view with its reason codes.
- **`desk_input_key` now watches depth partitions and stored hauling reports**,
  so the desk cannot keep painting a generation the lake has replaced.

### 2026-08-25 — §23 H1b: what 1,200 units really cost, at one station

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **929 passed, 7
deselected**, ruff check + format clean. **Not LIVE_VALIDATED**: no ladder this
produces has been compared to a market window.

- **`reduce_depth` builds a price-level curve per execution station**, from the
  **same in-memory pages** `reduce_orders` already reads. One fetch, two
  products: no extra ESI request, no cadence change, no new feed. The depth
  generation is `(region_id, sweep_ts)` — **identical** to `book_summary`'s —
  so a depth row and a book row can be proved to come from one sweep.
- **`book_summary` did not move, and a test proves it.** The frame produced
  through the modified sweep path is compared column for column, dtype for
  dtype and value for value against the frame produced without it. The whole
  track is additive; this is where that stops being a claim.
- **The reachability doctrine is now decidable.** §21 R1 had to fail closed on
  `solarsystem` and numeric ranges because the reduction had no topology. It
  has one now: a bid is executable from a station if it rests there, or its
  range is `region`, or its range is `solarsystem` and the system matches, or
  its numeric range covers the **stargate-graph jump distance**. Everything
  else — unknown system, unrecognised range, a distance the graph cannot
  answer — is **excluded and counted**. A structure-resting region-ranged bid
  is **included**, because the seller never docks there and range is what
  decides (§22 S2a).
- **`min_volume` is a conservative v1 rule, recorded as one.** A buy order
  demanding a parcel bigger than one unit is excluded from executable levels
  and its volume accumulated into `min_volume_excluded_qty`, so depth that
  exists but cannot be used is **visible rather than missing**. This
  under-states reachable exit depth on purpose; the packing problem it would
  otherwise create interacts with every other level in the walk.
- **`q_walk` prices a quantity, not a notional.** The existing `depth_walk`
  ("what does 0.25B buy") is untouched and still used by everything that used
  it. A quantity past the stored curve is **UNKNOWN**, and when the curve was
  truncated by the storage bound the reason says so — the levels that would
  have answered were never written, which is a different fact from a shallow
  book. Nothing is extrapolated from the last known price.
- **§23.17's worked example is fixtured and passes at the walk level**: 1,200
  units at a 102,416.67 source WAP and a 117,375 destination WAP, 4,753,687.50
  of sales tax, **13,196,312.50** net.
- **`DepthLake` mirrors `BookLake` exactly**: atomic writes, partial sweeps
  quarantined under a filename `latest()` does not glob, complete-only reads.
  **`load_validated_depth`** is the single staleness contract, on the same
  budget as the book because it is the same sweep — reimplementing staleness
  per call site is how two surfaces end up disagreeing about one generation.
- **The bound is a storage heuristic, and truncation is safe.** Levels are kept
  until they cover `max_scan_capital_isk × depth_safety_margin` **and** the
  largest recorded hold × the same margin. With no ship profile recorded the
  cargo target is zero rather than a guess at what the operator flies.

### 2026-08-25 — §23 H1a: the map, and a router that says no

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **894 passed, 7
deselected**, ruff check + format clean, `selftest` **12/12**. **Not
LIVE_VALIDATED**: no route this engine produces has been flown.

- **The SDE now carries the map.** `mapSolarSystems` contributes
  `securityStatus`, `mapStargates` becomes a system-to-system edge table, and
  `npcStations` becomes `sde_npc_stations`. Verified against build **3478781**:
  `destination` is an object carrying `solarSystemID`, and **`npcStations`
  has no name field at all** — so a station with no operator-supplied name
  renders as "<system> — station <id>" rather than as a guess that cannot be
  checked in the client. A gate whose destination will not resolve is counted,
  not invented, and a bundle where *none* resolve fails loudly rather than
  producing a router that answers "no route" to everything.
- **`routes.py`: shortest, safer, high-sec-only, and UNKNOWN.** BFS, Dijkstra
  with a configured penalty for entering non-high-sec, and a graph restricted
  before the search. A disconnected pair, an unmapped system or a filter that
  empties the graph returns UNKNOWN **with its reason and no jump count** —
  there is no straight-line fallback anywhere in this module.
- **High-sec is what the client displays.** `display_security` rounds half-up
  to one decimal — deliberately not Python's `round`, whose banker's rounding
  would send 0.45 to 0.4 and move the boundary by a whole system class — with
  the one irregular case `0 < true_sec ≤ 0.05 → 0.1`. So 0.4499 is **not**
  high-sec and 0.45 **is**, which is the band a hauler is actually ganked in.
  An unknown security is never high-sec.
- **A route carrying an unmeasured system reports an UNKNOWN minimum**, not a
  minimum over the systems we happen to know — that would report the safest
  possible reading of missing data.
- **Measured on the real map, and fixtured.** Jita → Amarr is **11 jumps**
  through **Ahbazon (0.4)**, and **34** on the high-sec-only profile. The
  fixture is the whole gated k-space graph from build 3478781 (5,268 systems,
  6,989 edges) so the assertion is about EVE rather than about a stub.
- **The same BFS resolves buy-order ranges.** `jump_distance` is memoised per
  (origin, bound) because a sweep asks it once per resting order, and it
  ignores security entirely — an order's range reaches as far as it reaches.
  Beyond the bound is **None**, which fails closed at every call site.
- **The route cache is keyed, never edited.** SDE build, origin, destination,
  profile, avoid list **and penalty** are all in the key, so a new build cannot
  read the old build's routes and two `safer` runs at different penalties
  cannot be confused. An UNKNOWN route is cached *as* UNKNOWN: a failed search
  is expensive too.
- **The migration reaches a database that already exists.** `state.db` holds
  the paper ledger and the watchlist and is not regenerable, so
  `security_status` is added by `ALTER TABLE` and the next `sde` run fills it;
  a test drives the whole thing against a database built with the **old**
  three-column schema and asserts no row is lost and NULL stays NULL.
- **A whole config section can now be optional.** `[hauling]` and `[routes]`
  declare defaults for every field, so an operator's existing `config.toml`
  loads unchanged and `selftest` parity still passes — the same rule §21 R2
  applied per field, applied per section. **Hub station ids were resolved from
  the SDE**, each checked to sit in its hub system: Jita 60003760, Amarr
  60008494, Dodixie 60011866, Rens 60004588, Hek 60005686.

### 2026-08-25 — plan.md §23 opened: the personalized HAULING tab (§17 D-33)

**Status: PLAN ONLY in this commit — no code.** `plan.md` gains §23, the
contract for a hauling tab that answers "given where I am, what I fly, what ISK
I have and how long I have got, what should I put in the hold?" — a question no
existing surface asks, because it is decided at a **quantity** rather than at a
notional tier.

- **§17 D-33 records the single-push authorization** (operator, 2026-08-25:
  *"build first, evaluate against competitors and live gates afterwards"*),
  covering H1–H4 only. **H5 and H6 are out of scope entirely** — both need
  authenticated ESI. The per-phase gates are **batched, not waived**.
- **The competitor set was checked live the same day and corrected.**
  **ISK Scout** (`iskscout.com`) was missing from the directive and is now a
  first-tier benchmark; **EVE Flipper** is at v1.6.14 (Jul 2026) and already
  does VWAP depth walking, multi-hop route trading, contract arbitrage and
  paper backtesting; **eve-meta is defunct**; **evetrade.space has lapsed**;
  Trading Matrix's free tier is Jita-only. H0 therefore **moves after** the
  shadow period and becomes a **keep/park** gate rather than a build gate —
  parking is a real expected outcome, and cheaper than maintaining a worse copy
  of a live tool.
- **Recorded in the contract before any code:** `generation_id ≡ (region_id,
  sweep_ts)` with **both** regions' generations pinned on every row and the
  older one deciding staleness; the buy-side **reachability doctrine** (station
  / region / solarsystem / numeric jump range, everything else excluded and
  counted); the conservative **`min_volume` rule** (an order demanding a
  minimum parcel is excluded from executable levels and its volume carried as a
  diagnostic); **displayed-security** rounding (`round(true_sec, 1)` half-up,
  except `0 < true_sec ≤ 0.05 → 0.1`; high-sec is display ≥ 0.5); a
  zero or unmeasurable volume quantile making liquidation **UNKNOWN** and
  failing every maker cap; `destination_share_prior` and `capture_share` as
  **labelled assumptions** rather than estimates; and the merge of the
  duplicated capital-turnover metric into one `isk_per_capital_day`.
- **The relist fee formula is recorded and stays quarantined.**
  `max(0, BR·(P2−P1)) + (1−RD)·BR·P2` closes the *shape* of §0 check #5 and
  nothing else: it has never met a wallet, so `relist_cost_unverified` remains
  unconsumed by any analytical path and the test that enforces that is
  unchanged.
- **Compute ownership is stated** so it cannot drift: the daemon and CLI
  produce ingest products (depth generations, routes, destination bars), the
  GUI page computes per-profile feasibility in its own worker, and `haul scan`
  writes the immutable audit artefact.

### 2026-08-25 — Personalized HAULING decision tab researched and queued (§23; historical)

**Historical planning state, later superseded by the operator-directed build
and the implemented §23 contract above.** The original research remains
preserved in merge commit parent `f466c24`.

**Status at that point: RESEARCHED + PLANNED ONLY. No product code changed.**
The active paper/live-validation checkpoint then remained open and had
priority.
Commit gate: `uv run pytest -q` — **850 passed, 7 deselected**; ruff check and
format check clean.

- Reviewed current CCP ESI market, cache/rate-limit, route, SDE, security,
  broker/tax and read-only character capabilities as of August 2026.
- Compared Adam4EVE, EVE Tycoon, EVE Profits, Trading Matrix, EVE Flipper,
  EVE Console and eve-meta. The broad product is not unique; EVE Flipper and
  EVE Profits are mandatory H0 benchmarks before implementation.
- Expanded queued §20.4 into §23's twenty-part product/technical contract for
  an additive `HAULING` page. Existing GUI pages, station trading, scanner,
  cross-region CLI and frozen calculations are unchanged.
- Made current location a first-class input: pickup, haul, total and baseline
  detour distance/time are separate, sortable facts.
- Scoped the MVP to five NPC hubs, manual ship/location/capital/time/security
  profiles, immediate ask-to-reachable-bid execution, arbitrary-size
  station-level depth and transparent multiple rankings.
- Added an explicit stop rule: do not build H1 unless a real competitor trial
  finds a repeatable gap; do not fund H3 unless the two-week MVP shadow either
  changes decisions defensibly or saves meaningful daily time.


## Archived history

- `docs/CHANGELOG_ARCHIVE_2026-08.md` — every entry from 2026-08-18 (planning)
  through 2026-08-21 (the fill models), verbatim: v1 in one push, the desk and
  its threading contract, §20, §21 R1–R8, §22 S1–S8. Evidence for one specific
  question; never context to load.

## Retired or superseded implementations

Recorded so they are not accidentally resurrected.

- `universe.seed_watchlist` (deleted 2026-08-20, §17 D-28) — a re-seed would
  resurrect a name the operator removed; `watch add` is the path.
- `relist_cost` (withdrawn 2026-08-20, §21 R4) — charged the broker fee on the
  whole order value; EVE charges on the price change. Now
  `relist_cost_unverified()`, consumed by nothing under `src/`; the §23.5
  formula is recorded and quarantined until one real relist closes §0 check #5.
- `max_drawdown_pct` (withdrawn 2026-08-20, §21 R3) — compounding overlapping
  trades in date order is not an equity curve; values kept under
  `backtest_withdrawn_pre_r3`.
- `net_pct` → `quoted_margin_pct` (2026-08-20, §21 R4); `station_volume_share`
  → `exec_reachable_volume_share` (§22 S2) — accessibility is reachability,
  not NPC ownership.
- `liquidity`'s `tail(window_days)` fallback (deleted 2026-08-25, §17 D-35) —
  measured a dead market from year-old bars.
- The line-over-envelope chart, then HLC bars (2026-08-20) — replaced by range
  candles after the 55.70% measurement (§17 D-30).
- `scan_cross_region`'s two-leg fill walk and "needs docking rights" flag —
  deliberately not inherited by hauling (§23 H4); `q_walk` and the
  reachability doctrine supersede them.
- RedisQ (never built) — sunset; the R2Z2 poller is the live path.
- The §20.3 TOP PERFORMERS prose figures — labelled a historical snapshot;
  `provenance.py` produces the live ones.
