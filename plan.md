# EVE Market Screener — port review, architecture decision, and phased plan

Status: **v1 BUILT in one push under operator directive 2026-08-20 (§17).
Phases 0–6 are IMPLEMENTED and GREEN; the consolidated live-validation
checklist in `CURRENT_CHECKPOINT.md` is owed before anything here is
LIVE_VALIDATED. Implementation decisions are locked in §11; the studies'
hypotheses and pass rules are frozen in §12.4, §13.6 and §14.3 — they were
written before the studies ran and are not to be retrofitted. `CLAUDE.md`
governs working sessions.**
Prepared 2026-08-18 against `Isidore94/TradingBotV3` branches `phase05-r8-weekend-prep`
(primary, 208 commits ahead of `main`) and `phase05-r2-focus-gating-strength-board`
(relative-strength work), following the source repo's mandatory reading order
(`CHANGELOG.md` → `plan.md` §§5–7, §12 → `CURRENT_CHECKPOINT.md` → `docs/README.md` →
source). ESI, zKillboard, PushX, Fuzzwork, and SDE facts were verified against live
endpoints and official docs on 2026-08-18; corrections to the briefing are flagged
inline. Where a fact could not be verified, the check that would resolve it is named.

This document is the contract for the implementing agent. It follows the source
repo's discipline: decisions with rationale, invariants stated up front, and a
validation gate per phase. Nothing here authorizes order automation of any kind.

---

## 0. Verified facts that drive everything below

From the source repo (all verified in-tree, file:line cites are to the r8 branch
unless noted):

- `DAILY_BAR_COLUMNS = ["datetime","open","high","low","close","volume"]` at
  `master_avwap_lib/legacy.py:1655`. Every daily frame passes through
  `_normalize_daily_bar_frame` (`legacy.py:2485`), which **silently returns an empty
  frame** if `open` or `close` is missing (`:2510-2513`). The adapter seam is real,
  but the contract is enforced by a silent gate, not an error.
- `calc_anchored_vwap_bands` (`legacy.py:15786`) uses OHLC4 typical price and a
  volume-weighted **running-AVWAP** σ accumulator (`cumSD += (tp − vw)²·v`, σ =
  `sqrt(cumSD/cumVol)`), in a pure-Python row loop. Two siblings must move in
  lockstep: `calc_anchored_vwap_band_history` (`:4020`) and the vectorized, σ-free
  `_anchored_avwap_at_last_bar` (`:25414`).
- All three ATR implementations (`legacy.py:3195`, `:15982`, `:4001`) need only
  high/low/close — **no open**. `levels.py` (884 LOC, standalone, no legacy import)
  uses only high/low/close/volume; `open` appears solely in its required-column
  set (`levels.py:49,61`). `expected_r.py` (481 LOC) touches no bar data at all.
- `real_relative_strength` (`bounce_bot_lib/legacy.py:2353`, r2 branch) reduces
  algebraically to `Δsym/ATR_sym − Δref/ATR_ref` — the benchmark enters as a single
  scalar and **any reference series works**. The hard SPY couplings are elsewhere:
  a missing benchmark aborts all three scopes (`legacy.py:8396-8400`), and the
  sector/industry ETF resolver falls back to the literal string `"SPY"`
  (`:923,:933`). `scripts/relative_strength.py` (502 LOC) is the more sophisticated,
  benchmark-substitutable, cross-sectional engine — and is currently **unwired**.
- The modularisation is confirmed a facade: `master_avwap_lib` real code is 7 files
  (39,229 LOC, 79% in `legacy.py` at 31,137); 13 files are pure
  `expose_legacy_names` shims (339 LOC). `bounce_bot_lib`: 4 real files (14,394
  LOC, `legacy.py` 11,670), 7 shims. The shim allowlists are an exact map of the
  public API the rest of the system consumes.
- `order_count` is referenced **nowhere** in the source repo — a free column.
- GUI surface (`ui/` + `gui_app/` + `market_prep_gui/` + `desk_link/`) is 42,417
  LOC and cleanly separable — only three non-UI files import PySide6.
- `push_notify.py` (155 LOC, stdlib-only) is the entire ntfy transport; a Discord
  swap touches one builder function, one length constant, and a decision about
  the `urgent` priority semantics.

From live ESI / community sources (verified 2026-08-18):

- `/markets/{region_id}/orders`: no auth, `X-Pages` pagination (**The Forge = 413
  pages live**), ~5-minute cache. In the new token regime: group `market-order`,
  **12,000 tokens / 15-minute floating window**, costs 2XX = 2, 3XX (incl. 304) = 1,
  4XX = 5 (429 exempt), 5XX = 0. Headers `X-Ratelimit-Group/-Limit/-Remaining/-Used`,
  429 carries `Retry-After`. Live since 2026-02-24, and it covers **only** the
  orders endpoint.
- `/markets/{region_id}/history?type_id=`: fields confirmed
  (`date, average, highest, lowest, volume, order_count`), expires **daily at
  11:05 UTC**, depth ≈ 13.5 months (412 rows observed). **Correction to the
  briefing:** this endpoint is *not* in the token regime; it has a separate,
  CCP-stated limit of **300 requests/minute/IP**, with developer-app termination
  named as the sanction for circumvention.
- `/markets/{region_id}/types`: exists — paginated list of type_ids with active
  orders in the region (The Forge = 20 pages live, 10-min cache). This is the
  universe-discovery primitive.
- Legacy error limit still active alongside tokens: 100 non-2xx/3xx per minute →
  HTTP 420 on **all** routes.
- Caching: ETag/`If-None-Match` → 304 confirmed and rewarded (1 token). **Nuance:**
  on market routes today `Expires` is still the operative header (`cache-control:
  public` only); the "Cache-Control authoritative" shift applies so far to
  event-invalidated routes. Polling before expiry can be treated as cache
  circumvention — bannable. UA must be descriptive with contact info (CCP has
  publicly named default agents).
- zKillboard: API filters by `regionID`/`shipTypeID`/etc., but **`startTime`/
  `endTime` are deprecated** — historical windows come from EVE Ref's daily
  killmail archives (`data.everef.net/killmails/`, ~15,000–24,000 killmails/day).
  **RedisQ is sunset (2026-05-31)**; the live feed is R2Z2, a poll-based bucket
  (`r2z2.zkillboard.com/ephemeral/sequence.json`).
- PushX freight quote API exists and was live-tested
  (`api.pushx.net/api/quote/json/?...` → full JSON quote). Unversioned third-party
  schema — treat as best-effort.
- Fuzzwork aggregates: per type/region, buy+sell `weightedAverage, max, min,
  stddev, median, volume, orderCount, percentile` where `percentile` is the
  volume-weighted average of the top/bottom **5%** of orders; full New Eden book
  pulled every 30 minutes.
- SDE: official reworked static data at
  `developers.eveonline.com/static-data` (yaml/jsonl zips, includes `types` and
  `marketGroups`); Fuzzwork conversion now at
  `fuzzwork.co.uk/dump/latest-sqlite.db.gz` (the old `sqlite-latest.sqlite.bz2`
  URL 404s).

Named checks still open (do these in Phase 0/1, do not assume):

1. **Is ESI `average` volume-weighted or a plain mean of trades?** Check: for ~20
   types over a month, compare ESI `average` against Fuzzwork same-day
   `weightedAverage` and against `(price stream implied by volume×average)`
   consistency. The AVWAP typical-price decision (§4) tolerates either answer,
   but the answer belongs in the bar-contract doc.
2. **Are order-book pages a consistent snapshot within one cache generation?**
   Check: fetch all pages twice inside one `Expires` window; diff by `order_id`.
   Mitigation regardless: one sweep per window, reconcile by `order_id`, and
   treat cross-page duplicates/gaps as data-quality counters, not errors.
3. **Do public Upwell-structure orders appear in the region orders endpoint?**
   Check: scan a Forge sweep for `location_id` > 10¹² (structure IDs are 13-digit)
   and compare top-of-book against the in-game regional view. This decides how big
   the structure blind spot actually is (§9).
4. **Does CCP filter outlier trades out of history `highest`/`lowest`?** Check:
   find a known scam-trade day for a liquid type and inspect the bar. Drives the
   winsorization decision in §6 (ATR row).
5. **Relist/modify fee formula** (affects the maker-exit branch of the cost
   model): verify the current advanced-broker-fee surcharge in-client.
6. **Sales tax / broker fee base rates** (7.5% / 3% assumed): verify in-client;
   CCP has changed these before.

---

## 1. Repo architecture decision

**Decision: a new, standalone repository (this one, `EveTradingbot`), with the
small shared surface vendored in as copies. No monorepo, no branch of
TradingBotV3, no submodule, no published package.**

The operator's constraint is decisive on its own: TradingBotV3 is the system
earning real money, and its `AGENTS.md` working agreement enforces a file-scoped
ask-first rule, golden-fixture gates, and doc reconciliation on every change.
Grafting an EVE system into that governance would either subject hobby-speed
iteration to production-grade ceremony, or — worse — erode the ceremony that
protects the production system. The two systems also share almost nothing
concrete: the port surface that survives review (§2) is ~2,600 LOC of pure code
out of 257,738, and the biggest reusable assets are *designs* (expected-R,
levels, promotion discipline), not importable modules, because they live inside
a 31k-line monolith behind shims.

Sharing mechanism for the code that is genuinely reusable as-is
(`scripts/indicators/`, `expected_r.py`, and a handful of pure helpers):

| Mechanism | Failure modes | Verdict |
|---|---|---|
| **git submodule** | Couples every EVE checkout to the private TradingBotV3 repo's availability and auth; pins to a SHA that someone must advance; solo operators reliably end up with detached-HEAD drift and accidental cross-repo pushes; and it imports the *whole* repo to use 1% of it. | Rejected |
| **published package** (private index) | Packaging, versioning, and index infrastructure for exactly one consumer and one maintainer; every EVE-driven fix now demands a release train on the production repo — precisely the maintenance-branch dynamic the operator forbade. | Rejected |
| **vendored copy** | Drift from upstream. That is the *point*: the vendored modules are pure, stable, and documented as frozen (`indicators/` has "no importer yet" even upstream; `expected_r.py` is pure stdlib). Drift is acceptable because divergence must never flow back upstream anyway. The residual risk — silently missing an upstream bug fix — is bounded by the tiny surface and mitigated by a provenance manifest. | **Accepted** |

Mechanics: each vendored file keeps its upstream path in a header comment plus
source branch + commit SHA + date, and a root `VENDORED.md` manifest lists every
vendored file with its provenance so a future diff against upstream is one
command. Vendored files may be modified freely in this repo; when they are, the
manifest marks them `diverged`.

Governance: this repo adopts a *lightweight* version of the source repo's root
control set — `plan.md` (this file, which becomes the living roadmap),
`CHANGELOG.md`, and `CURRENT_CHECKPOINT.md` — and its status vocabulary
(`IMPLEMENTED → GREEN → LIVE_VALIDATED → PROMOTED`). It deliberately does not
adopt the file-scoped ask-first rule or the frozen-exe machinery; a solo hobby
system at this scale needs gates (§8), not ceremony.

Size discipline is an explicit goal: the target system is **≤ ~15,000 LOC
including tests**. A smaller correct system beats a faithful port.

---

## 2. Module inventory

Classification of every top-level module in TradingBotV3. `LIFT` = vendored copy,
near-zero edits. `PORT` = same design, mechanical adaptation (LOC = expected size
in this repo). `REIMPLEMENT` = keep the concept, write new code (LOC = expected).
`DROP` = do not bring over. Source LOC from the census of `phase05-r8-weekend-prep`.

| Source module | Src LOC | Verdict | Est. LOC | Reason |
|---|---:|---|---:|---|
| `scripts/indicators/` (smi, efficiency_lrsi, heikin_ashi, laguerre_rsi) | 904 | **LIFT** | 904 | Pure, offline, immutable-tuples-out. Note: `heikin_ashi` and `laguerre_rsi`'s OHLC validation need `open` — vendor them anyway, leave unimported until an intraday bar source exists (upstream they are unimported too). |
| `master_avwap_lib/expected_r.py` | 481 | **LIFT** | 481 | Pure stdlib; Wilson lower bound, shrinkage, isotonic calibration, freshness decay. Touches no bar data. Calibration state restarts from zero samples. |
| `master_avwap_lib/levels.py` | 884 | **PORT** | ~500 | Standalone already. Computations use only high/low/close/volume; the single `open` reference is its required-column gate. Port = new bar contract, drop `open` from the gate, keep pivots/HV-levels/clustering/touch-stats intact, re-tune the relvol thresholds for EVE volumes. |
| AVWAP core: `calc_anchored_vwap_bands` + `calc_anchored_vwap_band_history` + `_anchored_avwap_at_last_bar` (inside `legacy.py`) | ~150 | **REIMPLEMENT** | ~250 | Must be extracted from the monolith anyway; the row loop must be vectorized (§4); typical price changes to `average` (§4). Keep the running-deviation σ *shape* and freeze it as this repo's invariant #1. |
| `fetch_daily_bars` stack (cache tiers, staleness, cooldown, in `legacy.py`) | ~900 | **REIMPLEMENT** | ~700 | The design (two-tier cache, delta refresh, staleness = uncertainty, live-failure cooldown) is exactly right and is re-expressed in the ESI data layer (§3). The code is IBKR/yfinance/CSV-shaped and per-symbol; EVE ingest is per-region batch. |
| `scripts/relative_strength.py` | 502 | **PORT** | ~450 | The best RRS base: exact-timestamp alignment with coverage floor, side-signed beta residual, cross-sectional percentile composite, percentile+absolute-z tiering. Unwired upstream, so no regression risk. Benchmark input becomes the synthetic composite (§6). |
| `real_relative_strength` + Wilder ATR (`bounce_bot_lib/legacy.py:2353,2144`) | ~40 | **PORT** | ~60 | Trivial extraction; formula is reference-agnostic. Daily timeframe only. |
| Earnings-anchor machinery (`pick_current_earnings_anchor` etc.) | ~120 | **PORT** | ~120 | Maps onto patch dates — and *simplifies*: patch datetimes are exact and published, so the gap-index inference (`legacy.py:12120`, open-dependent, dead in EVE) is unnecessary. Point-in-time filtering (`_earnings_dates_as_of`) ports as-is. |
| Scan pipeline shape (`master_avwap_lib/runner.py` + scan slots) | 2,744 | **REIMPLEMENT** | ~500 | Keep the shape: build universe → fetch bars → compute → rank → publish, each sweep individually try/excepted, last-good-on-failure. Drop IB pacing, GUI callbacks, watchlist files. |
| `scripts/push_notify.py` | 155 | **PORT** | ~180 | ntfy → Discord webhook: rewrite `build_push_request` (JSON body, 2000-char content cap vs 3500), keep the `unconfigured/delivered/rejected/ambiguous` result contract, add a `rate_limited` kind for Discord 429+`retry_after`. Keep the injectable opener for tests. |
| `scripts/candidate_registry.py` | 362 | **PORT** | ~300 | Provenance, lifecycle, and the "user-entered names are never auto-removed" invariant carry over unchanged in spirit. |
| Decision/outcome recording (journal *concept*, `human_focus_tracking`, `pick_feedback`) | ~1,600 | **REIMPLEMENT** | ~250 | v1 needs an append-only JSONL decisions log (pick, thesis, planned cost-netted R, outcome) to feed expected-R. The tax-grade broker-import journal is meaningless here (no broker). |
| `scripts/market_calendar.py` / `market_session.py` | 438 | **REIMPLEMENT** | ~60 | EVE trades 23/7 with daily downtime ~11:00–11:15 UTC; the "day" boundary is downtime and history rolls at 11:05 UTC. A tiny downtime-aware date helper replaces NYSE sessions. |
| `market_prep/` (SEC, earnings, Fed/Treasury, news, LLM prep) | 12,160 | **DROP** (concept: ~150) | ~150 | Equity catalysts. The one EVE analogue worth having: a patch-notes/dev-blog RSS watcher that appends to the anchor calendar and flags affected market groups. Everything else has no referent. |
| `scripts/ui/` PySide6 desk + `gui_app/` + `market_prep_gui/` + `desk_link/` | 42,417 | **DROP** | — | Operator wants Discord, not a desktop GUI. Cleanly separable (3 non-UI Qt imports). |
| `bounce_bot_lib/` M5 engine (detectors, RRS scan loop, IB client) | 14,539 | **DROP** | — | No intraday bars exist in ESI; a 5-minute cache with no latency edge makes M5 microstructure trading a fiction. The RRS *formula* is ported (above); the engine is not. |
| `scripts/autopilot_core.py` + auto-modes/quiet-hours | ~5,000 | **DROP** | — | Presence-mode matrix, IB budgets, phone-report publication gates — all shaped by market hours and an always-on desk. The EVE screener is a cron-shaped pipeline; §8's runner covers it. |
| Writer locks (`writer_lease`, `writer_role`, `writer_health`, `local_writer_lock`) | 2,182 | **DROP** | — | Existed for a retired two-machine topology. One machine, one process, atomic writes suffice. |
| `scripts/research_warehouse/` | 10,247 | **DROP** | — | The point-in-time discipline survives as a design rule; the DAS-lake plumbing does not. The Parquet bar store (§3) is the lake. |
| Journal subsystem (`journal_*.py` + Qt tabs) | 10,861 | **DROP** | — | See decisions-log row above. |
| Shadow/champion machinery (`market_state*`, `greatness_*`, review-learning loop, AI jobs/summaries) | ~12,000 | **DROP** (discipline kept) | — | The promotion ladder is adopted as process (§8); the SPY regime engines and the desk-review learning loop have no v1 referent. |
| `scripts/universe_builder.py` | 632 | **REIMPLEMENT** | ~250 | NASDAQ directory + weeklys screens → SDE types/marketGroups + `/markets/{region}/types` + turnover floors (§3). |
| `scripts/strength_scan.py` / `weekend_strength.py` (r8) | 837 | **DROP** | — | M5/H1 body-move formula needs intraday opens. The *cross-sectional percentile cut* idea lives on in the ported `relative_strength.py`. |
| `focus_adoption_gate.py` / `prev_day_gate.py` / `completed_bars.py` | 337 | **DROP** (idioms kept) | — | M5/session-VWAP specific. Three idioms are adopted repo-wide: tri-state gates where UNKNOWN always fails; completed bars only; "could not measure" ≠ "measured and failed". |
| `technical_integrity.py`, `chart_*`, `d1_level_*`, `vold_recorder`, `market_internals`, `rvol` (M5), `TickerMover`, diagnostics, packaging, selftest | ~12,000 | **DROP** | — | Intraday, desk, or Windows-packaging specific. |
| `project_paths.py` | 1,001 | **REIMPLEMENT** | ~80 | One data-dir resolver + atomic-write helper. No home-folder discovery, no LOCALAPPDATA split, no cold-push. |
| `pyproject` layered requirements + constraints discipline | — | **PORT** | — | Keep pinned, layered deps (core only — there is no GUI layer). |

**Total new/ported code target: ≈ 4,300 LOC of product code** plus tests —
comfortably inside the ≤15k budget, and an order of magnitude smaller than the
source. The two `legacy.py` monoliths are not extracted from; the ~2,600 LOC that
matter (§0) are lifted/ported from the standalone files, and everything still
inside the monolith is reimplemented against this document rather than excavated.

---

## 3. Data layer specification

### 3.1 Shape

One async ingest process (`httpx.AsyncClient`, HTTP/2), one SQLite state store,
one Parquet lake. No live daemon requirement in v1: the ingest runs as scheduled
jobs (cron/systemd-timer or a single long-running scheduler — implementer's
choice), because every consumer is D1 and the operator's sessions are short and
irregular. Nothing downstream reads ESI directly; everything reads the lake.

Client rules (all non-negotiable; CCP bans for cache circumvention):

- **User-Agent** is descriptive and stable, with app name+version and the
  operator's contact (email preferred per CCP best practices), set once in
  config — never a library default.
- **Never fetch before expiry.** The scheduler keys refreshes off each response's
  `Expires` (still the operative header on market routes) plus 5–10 s jitter.
  A fetch that would land before expiry is skipped, not queued.
- **ETags always.** Per-URL ETag store in SQLite; send `If-None-Match`; a 304
  costs 1 token and confirms freshness. Expect real savings on cold regions and
  on `/types`, little on the Forge book (it churns every window).
- **`X-Compatibility-Date` header** pinned in config (the new-style spec requires
  it; legacy `/latest/` routes were removed 2026-02-24). Bump deliberately.

### 3.2 Endpoints, cadence tiers, and the token arithmetic

Token budget (verified): `market-order` group = **12,000 tokens / 15-min floating
window**, orders endpoint only. Costs: 2 per 2xx, 1 per 304, 5 per 4xx, 0 per 5xx.

| Feed | Endpoint | Tier / cadence | Cost per sweep | Cost per 15-min window |
|---|---|---|---|---:|
| The Forge order book | `/markets/10000002/orders` | HOT: every cache window (~5 min) **while the screener needs books** (see below) | 413 pages × 2 = **826 tokens** | 3 sweeps = **2,478** |
| Secondary hubs (Domain 10000043, Sinq Laison 10000032, Heimatar 10000030, Metropolis 10000042) | same | WARM: hourly (Phase 6; zero before that) | ~150 pages total × 2 ≈ 300 | ≈ **75** |
| Everything else (108 regions) | same | COLD: not fetched in v1 | — | — |
| Universe discovery | `/markets/10000002/types` | daily (10-min cache, 20 pages) | 40 tokens | ~0.5 |
| Global prices | `/markets/prices` | daily (1-h cache) | 2 | ~0 |
| **Worst-case total (Phase 6)** | | | | **≈ 2,560 / 12,000 = 21%** |

Headroom is ~5×, and CCP's own published math (every page of all 113 regions
every 5 min ≈ 10,338 tokens) confirms the design sits far inside the envelope.
The budget accountant still runs: track `X-Ratelimit-Remaining/-Used` per
response, persist a rolling window estimate, and **hard-stop orders fetches at
50% of budget consumed** (6,000 tokens/window) so a bug can never spend to the
cap. On 429: sleep `Retry-After`, log, resume. On 420 (legacy error limit, 100
non-2xx/3xx per minute, applies to **all** routes): full stop for 60 s. Keep 4xx
near zero structurally — type_ids come from `/types` before any history call, so
404s should not occur in the steady state.

A deliberate cadence note: the book poller does **not** need to run 24/7 at HOT
tier. Books feed the cost model's depth snapshots and the digest's live spreads;
the D1 signal layer runs off history bars. Default: HOT polling during a
configurable operator-active window (e.g. 2 h around each digest build), one
sweep per hour otherwise. This is a cost/freshness dial, not a correctness one.

**History** (`/markets/{region}/history?type_id=`) is *outside* the token regime:
**300 requests/min/IP**, expires daily 11:05 UTC, ~13.5 months of daily bars per
call. Self-imposed ceiling: 150/min (50%). Daily job at ~11:20 UTC (post-expiry,
post-downtime): refresh the tracked universe. At the Phase-1-measured universe
(planning estimate: 2,000–4,000 types, §9 R1), that is **13–27 minutes** of
history calls per day. A full-catalog crawl of every Forge-active type (~20,000,
20 pages × ~1,000 ids) at 150/min ≈ 2 h 13 m — done **once** in Phase 1 for the
universe census, then never again as a routine job. Each response is diffed
against stored bars; only new rows are appended, so a re-crawl is cheap on write.

### 3.3 Retry, backoff, failure semantics

Ported from the source repo's design (this is the part of `fetch_daily_bars`
worth keeping): bounded retries (3, exponential 2/4/8 s + jitter) on 5xx and
transport errors only — 5xx costs 0 tokens; 4xx is never retried, it is a bug
surfaced. Per-feed circuit breaker with a 15-min cooldown after consecutive
failures (mirrors `DAILY_BAR_LIVE_FAILURE_COOLDOWN_MINUTES`). **Staleness is
uncertainty, never confirmation**: every lake row carries `fetched_at` and the
response's `Last-Modified`; consumers get bars with an explicit freshness field
and must surface "stale" rather than compute on it silently. A failed sweep
never deletes or overwrites the last verified snapshot (the source repo's
failed-publish invariant, kept).

### 3.4 Reduction on write — no raw books are persisted

A full Forge book sweep is ~400k orders; at 288 sweeps/day that is ~10 GB/day
raw. Nothing downstream needs raw orders. Each sweep is reduced **in memory** to
one row per `(type_id, region_id, side)`:

```
book_summary:
  type_id, region_id, side, sweep_ts, expires_ts,
  best_price, total_volume, order_count,
  p5_price,                    # Fuzzwork-style: vol-weighted avg of best 5% of resting volume
  depth_fill_price[3],         # effective unit price walking the book for
                               # 0.25B / 1.0B / 2.5B ISK notional (config)
  depth_fill_qty[3],           # units obtainable at those notionals
  top_order_volume_share,      # largest single order / total (spoof indicator)
  station_volume_share         # NPC-station-resident volume share (blind-spot metric, see check #3)
```

plus a per-sweep `spread` view joining the two sides. Raw pages are discarded
after reduction; a debug flag can persist one raw sweep for fixture-building.
Estimated reduced size: ~20k types × 2 sides × ~100 B ≈ 4 MB/sweep worst case,
and only HOT-window sweeps are kept at full resolution — WARM sweeps and
end-of-day roll-ups thereafter (keep: last sweep per hour for 7 days, last sweep
per day forever).

**History bars are persisted as-is** (they are already daily aggregates):
Parquet, partitioned `region_id/year`, one row per `(type_id, region_id, date)`
with the ESI fields plus `isk_value = volume × average` computed at write.

### 3.5 Storage

- **Parquet (pyarrow) lake** under a single configurable data dir:
  `bars/region=…/year=….parquet`, `books/region=…/date=….parquet`. Pandas in,
  pandas out — same stack as the source repo, no new skills.
- **SQLite** (`state.db`, WAL): ETag store, sweep ledger (every fetch: URL,
  status, tokens observed, duration — this *is* the provider telemetry), SDE
  snapshot tables (`types`, `market_groups` from the official jsonl SDE; refresh
  monthly, keyed by build number), anchor calendar, decisions log index.
- **JSONL** for append-only human-adjacent streams (decisions log, digest
  archive), matching the source repo's evidence-file habit.
- Whole tree is one directory, rsync-able; no DAS, no cloud, no writer leases.

### 3.6 SDE and universe

Monthly (or on-demand) job pulls the official jsonl SDE, loads `types`
(type_id, name, volume m³ — needed for freight, packaged volume) and
`marketGroups` (the tree: group → parent chain) into SQLite. The tradeable
universe is `published types ∩ /markets/10000002/types`, annotated with the
30-day median `isk_value` and `order_count` from the lake; the **tracked
universe** applies the liquidity floor (initial: median daily `isk_value` ≥
100M ISK *and* median `order_count` ≥ 30 — both re-derived from the Phase 1
census, not sacred). New types entering the floor are auto-added; types falling
out are flagged, never silently dropped (candidate-registry invariant).

---

## 4. Bar contract decision

**Decision: a new contract. `DAILY_BAR_COLUMNS` is not preserved, and no `open`
is synthesized — ever.**

```
EVE_DAILY_BAR_COLUMNS = ["datetime", "high", "low", "close", "volume", "order_count"]
# close  ← ESI average      (documented, single mapping site in the adapter)
# high   ← ESI highest
# low    ← ESI lowest
# datetime = the ESI date at 11:00 UTC (downtime boundary), tz-aware UTC
# plus derived column isk_value = volume × close, carried in the lake
```

Rationale, option by option:

- **Option A — preserve `DAILY_BAR_COLUMNS` with `close ← average`, `open ←
  prior average`.** Rejected. It makes every open-consumer *run* while computing
  fiction: the earnings gap-index inference (`legacy.py:12120`) measures
  `|open−prior close|`, which becomes identically zero; `gap_atr_multiple`
  and the `MIN_GAP_ATR_MULTIPLE` filter go dark silently; `close>open` candle
  confirmations (`:12641`, `:13300`) degrade to day-over-day sign tests without
  saying so. Worse, it can *crash*: `laguerre_rsi._validated_ohlc` raises when
  open falls outside the bar's high–low range, and yesterday's average is
  routinely outside today's range. This is exactly the source repo's own
  invariant — "missing data is uncertainty, never confirmation" — a synthetic
  open launders uncertainty into confirmation. The seam is kept conceptually
  (one frame contract drives the stack) but the lie is not.
- **Option B — new contract (chosen).** What breaks, named: the
  `_normalize_daily_bar_frame` gate and the yfinance required-column set
  (adapter-level, reimplemented anyway); `levels.py`'s required set (one-line
  change, computations untouched); earnings gap machinery (replaced outright —
  patch anchors are exact dates, no inference needed); `heikin_ashi` and
  `laguerre_rsi` (vendored, stay unimported); `setup_playbook_study`'s
  enter-at-next-open model (dropped; EVE entries are taker fills against a live
  book, modeled by the cost layer instead); weekly resample `open:"first"`
  (weekly frames, if ever wanted, resample without open); candle-body strength
  formulas (`strength_scan`, dropped with the M5 stack). Everything else the
  port keeps — all ATR variants, `compute_indicator_frame`'s SMA/EMA stack,
  every `levels.py` computation, band classification, `expected_r`, RRS — reads
  only high/low/close/volume and survives unmodified.

**Typical price for AVWAP: `tp ← close` (i.e. ESI `average`) directly**, not an
OHLC4 reconstruction. ESI's `average` is a whole-day trade-derived mean — it *is*
the day's typical price, better than any 4-point proxy of it (check #1 pins down
exactly which mean). The σ accumulator keeps the source system's shape —
volume-weighted deviation against the **running** AVWAP, σ = `sqrt(Σ dev²·v / Σv)`
— because the operator's band instincts (1/2/3σ ladders, band-walk reads) are
calibrated to that variant's tighter-on-trend behavior. This repo's invariant #1,
stated now: **the σ formula is frozen from Phase 2 forward; changing it requires
regenerating every golden fixture and re-validating every band consumer.** The
upstream AGENTS.md invariant binds TradingBotV3's consumers, not this repo; what
is carried over is the *discipline*, applied to a formula whose tp differs by
deliberate, documented decision made before any consumer exists — the only
moment such a change is free.

Implementation note (kills the row-loop question): with `tp = close` the whole
computation is three `numpy` cumulative sums per (type, anchor) —
`vw = cumsum(tp·v)/cumsum(v)`, `dev = tp − vw`, `σ = sqrt(cumsum(dev²·v)/cumsum(v))`
— exact same semantics, no per-row Python. At the tracked-universe scale
(≤4,000 types × ≤412 bars × a handful of anchors) a full recompute is
sub-second; the 680k-series specter (§9 R1) never materializes because the
universe is floored first (§3.6). Vectorize **and** cap: both.

**`order_count`'s role** (new capability, zero upstream collisions):
1. liquidity floor input (§3.6) — `volume` alone is gameable by one wash trade;
2. `avg_trade_size = volume / order_count` — the spoof/outlier discriminator
   used by the data-quality layer and the cost model (a "cheap" book whose size
   sits in one order is not cheap);
3. participation RVOL — `order_count` vs its own 20-day mean is the EVE analogue
   of the volume-thrust read (a price move on collapsing order_count is a
   thin-book artifact, not demand);
4. `order_count == 0` rows are recorded as data-quality events, and such bars
   are excluded from ATR/σ warm-ups rather than treated as real prices.

---

## 5. Cost model specification

All costs are netted **inside** the screen: the ranked quantity is
net-expected-R computed from effective entry and exit prices at the intended
size, never a gross margin with fees applied afterward. Skills are config, not
constants (`accounting_level`, `broker_relations_level`, standings), defaulting
to Accounting V / Broker Relations V.

Components (rates per the briefing; verify in-client, check #6):

| Component | When paid | Rate (assumed) |
|---|---|---|
| Sales tax | every sell, always | 7.5% × (1 − 0.11·Accounting) → **3.375%** at V |
| Broker fee | posting/modifying an order only (never on taker fills) | 3% base → ~**1%** at Broker Relations V + standings; relist surcharge per check #5 |
| Buy escrow | posted buys escrow 100% (long-only, no leverage) | capital cost, not a fee — modeled as capital-days in R sizing |
| Freight | cross-region only (Phase 6) | PushX quote API: `f(volume m³, collateral)`; packaged volume from SDE |
| Market impact | both sides, always | depth-walk from `book_summary.depth_fill_price` at the intended notional |

The operator is a **taker on entry** (crosses the spread; tight spreads are good
for him). Two exit styles are modeled explicitly and the screen reports both:

```
entry_price(S)   = ask-walk VWAP for notional S            # depth_fill_price[ask]
exit_taker(S)    = bid-walk VWAP for S × (1 − tax)
exit_maker(S)    = target_price × (1 − tax − broker)       # + fill risk, not a number:
                                                           # flagged, never netted away
net_R(S)         = (exit(S) − entry_price(S)) / risk_unit  # risk_unit from ATR-based stop
breakeven_move   ≈ tax (+ broker if maker exit) + [ask-walk premium + bid-walk discount]
                 ≈ 3.4–4.4% + measured depth cost at S     # the screen's floor
```

Consequences the screen enforces:

- Every candidate row carries `breakeven_move_pct` at the configured notional
  tiers (0.25B/1.0B/2.5B). A setup whose expected move does not clear breakeven
  at the smallest tier is not shown as opportunity — honest-zero beats a filled
  panel (source-repo invariant, kept).
- Depth cost is computed from the **most recent book sweep**, with its
  `sweep_ts` displayed; a stale book (> 1 h) renders the cost as UNKNOWN and the
  row is flagged, not silently priced off history.
- The maker-exit branch is advisory: its fee edge over taker exit is real
  (~spread + walk vs ~1% broker) but carries queue risk that cannot be priced
  from a snapshot; the digest shows both numbers and never picks for the operator.
- Freight (Phase 6): `net_R` for a cross-region candidate subtracts the PushX
  `PriceNormal` quote at the SDE packaged volume and the position's collateral,
  plus a configured haircut for quote staleness. No freight, no cross-region row.

---

## 6. Signal translation table

| Equity signal (source) | EVE analogue | Survives? | Notes |
|---|---|---|---|
| **AVWAP anchoring at earnings gaps** (`pick_current_earnings_anchor`, gap-index inference, `calc_anchored_vwap_bands`) | AVWAP anchored at **patch/expansion dates** (curated calendar + patch-notes RSS), σ bands on `tp = average` | **YES — strengthened** | Patch datetimes are exact and global: no gap inference, no BMO/AMC ambiguity. The 10-day fresh-anchor ambiguity rule ports as-is. Secondary anchors: MER-visible supply shocks, hand-dropped anchors via config. |
| **RRS vs SPY / sector ETF / industry ETF** (`real_relative_strength`, three scopes) | RRS vs **synthetic Forge Composite** / vs `market_group` ancestor cohort / percentile-in-cohort | **YES — with construction work** | Formula is reference-agnostic (§0). Benchmark: ISK-turnover-weighted index over the top ~100 tracked types, weights re-derived monthly with a chain-link so composition drift doesn't fake index moves; MER indices are a monthly sanity anchor, too infrequent to be the benchmark. Scopes: the SPDR two-level map becomes an N-level `marketGroups` tree walk — nearest ancestor with ≥K tracked members, cohort index = turnover-weighted member composite. The upstream fallback-to-`"SPY"` bug pattern is explicitly *not* ported: an unresolvable scope returns UNKNOWN and drops, never silently substitutes the composite. Port `relative_strength.py` (cross-sectional percentiles need no benchmark at all) as the primary engine; `real_relative_strength` as the simple secondary read. |
| **ATR(20)** (three implementations, H/L/C) | ATR(20) on `highest/lowest/close` | **YES — with cleaning** | ESI `highest/lowest` may include absurd off-market prints (check #4). Guard: winsorize TR against `k × rolling median TR` and flag clamped bars; never let one 10,000%-spike day own the risk unit. One implementation this time, not three. |
| **Volume/liquidity floors** (`MIN_AVG_VOLUME_20D=1M sh`, `MIN_PRICE=$5`, `MIN_MARKET_CAP=$1B`) | median 30-day `isk_value` ≥ 100M ISK; median `order_count` ≥ 30; unit-price floor only as a spam cut | **PARTIAL** | Share-count thresholds are meaningless across items whose unit prices span 12 orders of magnitude — ISK turnover is the only common denominator. Market cap has **no analogue** (no shares outstanding); its screening role (avoid illiquid small names) is covered by turnover + order_count. `MIN_PRICE`'s manipulation-resistance role is covered by order_count and avg-trade-size checks. |
| **Level/pivot system** (`levels.py`: pivots, HV levels, clustering, touch stats, conviction) | Same, on EVE bars | **YES** | Uses only H/L/C/V. Re-tune: relvol green/red thresholds (3.0/2.0) against EVE volume distributions; ATR-fraction cluster tolerance stands. EVE-specific bonus: psychological round numbers (1M, 100M, 1B ISK) are strong player anchors — add a round-number level family, weight it via touch statistics like any other. |
| **Expected-R scoring** (`expected_r.py`: Wilson LB, shrinkage, isotonic calibration, freshness decay) | Identical | **YES — lift** | Pure math over (setup, outcome) samples. Calibration restarts at zero: prior anchors re-seeded from the first ~60 recorded EVE decisions; freshness decay is *more* important here (patches reshape items overnight). |
| **RVOL / volume thrust** (breakout confirmation) | participation events: `volume` and `order_count` vs own 20-day baselines | **REPURPOSED** | Survives as a *demand-event detector* (doctrine changes, patch announcements, war mobilization show up as participation spikes), feeding the catalyst/anchor layer. It does **not** survive as breakout confirmation — see below. |
| **Momentum / breakout continuation** (D1 band-walk continuation, trend-following entries, five-day breakout flags) | — | **NO — actively misleading** | EVE supply is elastic and player-produced: a price spike is an *invitation to industrialists*, who respond within days; blueprints don't sleep and there is no float. Spikes are arbitraged flat, not continued. Chasing a breakout means buying at the top of a supply response. The screen inverts the read: strength into a value zone is distribution risk; the tradeable pattern is **dips below anchored value with intact demand** (RRS holding, participation stable, destruction support once Phase 5 lands). Breakout-continuation logic is not ported and must not be re-introduced by habit. **NARROWED 2026-08-20 (third directive; §17 D-15): this row governs the SYSTEM's recommendation engine, which stays the frozen dip-below-value class. An OPERATOR-DEFINED setup (§19.3) may express trend or continuation, and one of the shipped examples deliberately does. The elasticity argument above is a claim about EVE, and a claim is something to measure, not something to make unexpressible.** |
| **M5 bounce stack** (BounceBot detectors, session VWAP, focus gating, strength boards) | — | **NO** | No intraday bars exist; the 5-minute cache is the same for everyone, so there is no microstructure edge to detect. Building pseudo-M5 bars from book snapshots would be modeling the cache, not the market. |
| **Earnings-proximity blocks** (`RECENT_EARNINGS_SESSION_BLOCK=12`) | patch-proximity handling | **YES — inverted default** | Equities block *entering* near earnings (binary risk). EVE patches are published in advance with readable patch notes: the analogue is a patch-week state on affected market groups — wider risk units and an explicit "thesis must reference the patch" flag, rather than a hard block. Hard-block only the unread case: patch affects the item's group and no thesis note exists. |
| **Shorting / short-side symmetry** (side-signed engines) | — | **NO** | No shorting, no leverage, 100% buy escrow. All side-symmetric code paths port long-only; the side dimension collapses at the contract level (keeps `side` in identity for future sell-order analytics, hardcodes `long` in v1). |
| **SPY regime / pause detection** (market_state, VWAP regimes, ±1% wake alarm) | composite-index regime read | **DEFERRED** | A Forge Composite drawdown/participation regime flag is cheap once the composite exists (Phase 3), and gates digest tone ("market-wide dump in progress"), not entries. The full pause-episode machinery is not ported. |

---

## 7. zKillboard integration (assessed as a differentiator)

**What it is.** Every ship loss in New Eden since 2011, with full fitting,
location, and timestamp, public. Destroyed ships *and their fitted modules* are
demand that must be re-bought — item-level **demand destruction telemetry** that
equities simply do not have. Joined as `destroyed_units(type_id, region_id ∪
adjacent, rolling 7d)` vs the market lake, it detects replacement demand *before*
it prints as volume/price.

**Feasibility — confirmed, with two design corrections from verification:**
- Historical joins do **not** come from the zKillboard API (its `startTime/
  endTime` are deprecated; `pastSeconds` caps at 7 days). They come from EVE
  Ref's daily killmail archives (`data.everef.net/killmails/`), which are
  complete, bulk-downloadable, and backfillable at leisure.
- The streaming feed is **R2Z2** (poll `sequence.json`, fetch per-sequence
  JSON); RedisQ sunsets 2026-05-31 and must not be built against.

**Data volume:** 15,000–24,000 killmails/day globally — a few tens of MB/day of
JSON, reduced on ingest to one row per `(type_id, region_bucket, date)`
counting hull losses and fitted-module losses separately. ~~Trivial next to the
market lake.~~ **Corrected 2026-08-20 by measurement: it is the opposite.** One
year of archives reduces to **15,696,593 rows**, a **1.3 GB** `state.db` with a
**731 MB** WAL beside it — roughly **30× the entire Parquet bar lake** (43 MB
for 1.85M bars across 7,264 types). The reduction is genuine (2.8M killmails
in, 41k rows per day out) but the row count is dominated by fitted modules:
every loss contributes ~20 `(type, region, day)` keys, not one. Mitigations
applied: bulk writes use `executemany`, queries aggregate by `(type_id, day)`
in SQL with a type filter so only lake-resident types are ever materialized,
and every bulk writer now checkpoints the WAL when it finishes. If this grows
past comfort, the table belongs in Parquet partitioned by month like the bars,
not in SQLite — a change worth making before a second year is backfilled. **Latency:** near-real-time via R2Z2 (minutes); irrelevant for the
daily-bar backfill path.

**Signal sketch (for the Phase 5 study, not v1):** `destruction_z = 7d destroyed
units vs 90d baseline`, joined per type × trade-hub catchment (losses in and
around Forge-adjacent war zones replace at Jita). Hypothesis to falsify:
`destruction_z` leads `order_count`/`volume` upticks and price firming in
doctrine-class hulls and their fitted modules by 1–5 days. Doctrine detection
(many identical fits dying together) is a second-order feature the fitting data
supports.

**Verdict: not v1 — Phase 5, and deferral costs nothing.** The archives are
complete and public, so waiting loses zero data (unlike a stream you must be
present for). The core screen must exist first to give destruction features
something to rank into, and the promotion ladder (§8) demands the lead-lag
claim be measured on the historical join before it may influence a single
ranking. It is, however, the system's genuine edge and the reason this port is
more than a toy: it stays on the roadmap as a first-class phase, not a wish.

---

## 8. Phased build order

Discipline carried from `plan.md` §7: each phase ends `IMPLEMENTED → GREEN`
(deterministic tests + golden fixtures frozen before inspection) and owes a
**live validation gate** before the next phase builds on its outputs. Detector
and scoring changes after Phase 2 require fixture regeneration first. One phase
active at a time; `CURRENT_CHECKPOINT.md` in this repo names it.

**Phase 0 — First light (one evening, falsifiable).**
Scaffold repo (pyproject, pinned deps, data dir, config). Minimal ESI client
(UA, ETag store, Expires-respecting scheduler, telemetry ledger). Hand-curated
~50-type Forge watchlist (hulls, ammo, minerals the operator already knows).
Pull history for those 50; build the Parquet bar store; one Forge orders sweep
reduced to `book_summary`; compute the **net-cost screen**: spread, 30-day
turnover, breakeven_move at 0.25B, ranked by net margin. Post the table to a
Discord webhook.
*Gate:* five types spot-checked against the in-game market window (prices,
volumes within the cache window); tax/fee arithmetic reproduced against one
real fill the operator makes; telemetry shows every request honored expiry and
budget headers. **Falsified if** any spot-check disagrees beyond cache-window
tolerance or the fee math misses the real fill by > 0.1%.

**Phase 1 — Universe census (weekend).**
`/markets/10000002/types` + full one-time history crawl (§3.2). Measure: type
count, turnover/order_count distributions, pages/tokens consumed, sweep timings.
Choose the liquidity floor from data; write the census into this file (replacing
§9 R1's estimates). Stand up the daily history job and HOT/WARM book scheduler.
*Gate:* 48 h unattended, zero 429/420, budget peak < 25%, census table
committed. This gate is the license for every later "the universe is N" claim.

**Phase 2 — Bars, indicators, anchors (1–2 weeks of evenings).**
Bar contract + adapter (§4). Vendored `indicators/` + `expected_r.py` with
`VENDORED.md`. Vectorized AVWAP core + σ bands; patch-anchor calendar (curated
file + patch-notes RSS appender); ATR with TR winsorization; `levels.py` port.
Golden fixtures: frozen input frames → expected bands/levels/ATR, generated
once, reviewed, then locked (σ invariant starts here).
*Gate:* fixtures green; bands/levels spot-checked against Adam4EVE/in-game
charts for 10 types across 3 market groups; checks #1 and #4 (§0) resolved and
their answers recorded in this file.

**Phase 3 — Ranking and digest (2 weeks).**
Forge Composite construction; `relative_strength.py` port with market-group
cohort scopes; expected-R wiring with seed priors; the daily **Discord digest**
(ranked candidates with thesis fields, breakeven, freshness stamps, honest-zero
states) and the append-only decisions log.
*Gate:* two-week shadow period — every digest archived, every operator decision
logged with planned net-R, outcomes tracked; digest must survive one ESI outage
day with honest staleness instead of stale numbers presented as fresh.
**Promotion of the ranking** (operator actually trading off it) is a separate,
explicit decision after the shadow window, per the §7 ladder.

**Phase 4 — Depth-aware cost netting (1 week).**
Full `book_summary` tiers netted inside the screen (§5); notional-tier
breakevens in the digest; maker-vs-taker exit surfaces.
*Gate:* predicted vs actual effective fill on ≥ 10 real operator trades within a
pre-stated tolerance (suggest ±0.5% of notional); the tolerance and results are
recorded before/after, not retrofitted.

**Phase 5 — zKillboard demand layer (2–3 weeks).**
EVE Ref archive backfill (≥ 1 year), destruction reduction, R2Z2 poller,
`destruction_z` features; the **lead-lag study** on the historical join.
*Gate:* the study's claim ("destruction leads demand by 1–5 days in classes X")
is stated and frozen before measurement; features influence ranking only if the
measured effect survives — otherwise they ship as digest annotations only.
Shadow → promote, never straight in.

**Phase 6 — Cross-region (open-ended).**
WARM-tier hub sweeps, freight netting via PushX, structure-blind-spot policy
from check #3's answer.
*Gate:* one full cross-region cycle class validated end-to-end on real freight
(quoted vs invoiced cost within tolerance), token budget still < 25% peak.

---

## 9. Risk register

| # | Risk | Assessment and mitigation |
|---|---|---|
| R1 | **Universe cardinality blowup** — 10k types × 113 regions ≈ 1M+ potential series; naive porting of the per-symbol row-loop stack dies here. | Planning estimate: ~20k Forge-active types, of which **2,000–4,000** clear any sane turnover floor (Phase 1 measures the truth; the number replaces this row). Forge-only in v1 kills the ×113. Vectorized AVWAP (§4) makes even the full 20k tractable; the floor makes it comfortable. Explicit non-risk after Phase 1's gate. |
| R2 | **Bait/spoof orders poisoning top-of-book.** A 1-unit sell at 10× fair, or a wall priced to lure, makes naive best-price screens buy garbage. Fuzzwork's 5% average is the community defence: vol-weighted mean of the best 5% of resting volume — robust to any *single* small bait order by construction, but **fails exactly where margins look widest**: in a thin book the bait *is* the top 5%. | Never rank on best price. Rank on `p5_price` **and** `depth_fill_price` at real notionals (the walk literally prices the bait in and dilutes it), floor on `order_count`, and flag `top_order_volume_share` > 0.5. The wide-margin/thin-book quadrant is additionally handled by R5. |
| R3 | **Upwell structure blind spot.** Player structures need SSO + docking rights; if meaningful Forge volume rests in structures, public books understate depth and history may diverge from executable reality. | Jita 4-4 is an NPC station, so the v1 heartland is clean. Check #3 measures the residual (structure-resident share per sweep via `location_id`); `station_volume_share` is carried per row so the exposure is *quantified, not assumed*. No SSO in v1 — if a market group turns out structure-dominated, it is excluded and labeled, not guessed at. |
| R4 | **ESI rate-limit and ban risk.** Cache circumvention is a bannable offence; the history endpoint's 300/min carries developer-app termination language; the token regime is 6 months old and may be re-tuned; history may migrate into it. | §3's client rules (never-before-expiry, ETags, descriptive UA, 50% self-caps, telemetry ledger) keep usage at ~21% worst case. The sweep ledger records observed `X-Ratelimit-*` per response, so a regime change is detected the day it happens, not the day of a 429 storm. Single operator, single IP, no fan-out. |
| R5 | **Thin liquidity in the wide-margin items.** The screener's namesake failure: gorgeous percentage margins that cannot absorb 0.25B without eating the whole edge — the wide margin *is* the illiquidity premium. | Structural, not advisory: breakeven and net-R are computed **at notional tiers from the depth walk** (§5), so an un-fillable margin nets to ~zero and never ranks. The honest-zero invariant means the digest says "nothing clears costs today" rather than dredging. |
| R6 | **Single-developer maintenance load against a production system.** The operator's attention is TradingBotV3's fuel; this repo must not siphon it. | New repo + vendoring (§1) makes cross-contamination structurally impossible. ≤15k LOC budget, cron-shaped runtime (no live desk to babysit), phases sized to evenings/weekends, and every phase leaves a working system — abandoning the project at any gate leaves no debt on the production side. |
| R7 | **Bad bars from the source.** `highest/lowest` outlier prints (check #4), `order_count`=0 ghost days, downtime-boundary duplication, the ~13.5-month history horizon limiting long anchors. | TR winsorization + data-quality counters (§4, §6); anchors older than the horizon are marked truncated; the lake keeps its own history forever, so the horizon only binds the first year. |
| R8 | **Benchmark construction risk.** A self-built composite can fake RRS signals via composition churn, one dominant type (PLEX), or its own thin members. | Chain-linked monthly reweighting, single-type weight cap (10%), members drawn only from the tracked (floored) universe, and the composite's own diagnostics (member count, weight entropy) published in the digest footer. MER indices as an independent monthly cross-check. |
| R9 | **Patch/meta risk.** CCP can rebalance or tiericide an item class overnight; anchors and calibration die with it. | This is also the opportunity (§6 row 1). Patch-week state widens risk units and demands a thesis note; expected-R freshness decay (vendored) already down-weights stale evidence; the patch-notes RSS is the tripwire. |
| R10 | **Third-party dependency fragility.** PushX schema unversioned; Fuzzwork is one person's service; EVE Ref archives are community-run. | Each is an enrichment, not a load-bearing wall: freight quotes cache with haircuts and cross-region simply pauses without them; Fuzzwork is used for cross-checks, not production data (own reduction replicates its statistic); killmail archives are mirrorable and deferral-safe (§7). |

---

## 10. Explicit non-goals

Carried from the source repo's invariants and the operator's constraints; these
are permanent, not deferred:

1. **No order automation of any kind.** ESI market endpoints are read-only;
   there is no order-entry API, and this system will never hold SSO scopes that
   act on a character. Decision-support only, same as the source.
2. **No client automation.** No input injection, no screen scraping, no
   cache-file reading of the EVE client. That is botting and bannable; nothing
   in this plan requires it and nothing may be added that does.
3. **No sub-cache-latency strategy.** The 5-minute order cache and 11:05 UTC
   history roll are the same for everyone. Any strategy whose edge depends on
   beating the cache is a fiction and is out of scope by construction (no
   pseudo-intraday bars, no M5 port).
4. **No cache circumvention, ever** — including "clever" multi-source merging
   to synthesize sub-cache freshness. Respecting `Expires` is a correctness
   invariant, not a courtesy.
5. **No market-maker strategy layer.** The operator is a taker-entry D1 swing
   trader; 0.01-ISK order games, station-trading margin harvesting, and
   maker-queue modeling beyond the advisory exit comparison (§5) are out.
6. ~~**No GUI desktop application.** Discord digest + repo artifacts. The
   42k-LOC lesson is learned once.~~ **REVOKED by operator directive
   2026-08-20 (third); see §19.2 and §17 D-14.** The PySide6 desk ships. The
   42k-LOC lesson is now enforced structurally instead of by abstention: Qt is
   an optional dependency tier, the core must run headless, and a test walks
   the import graph to prove it does. The desk is 2,972 LOC.
7. **No maintenance coupling to TradingBotV3.** No imports, no submodules, no
   shared state, no upstreaming obligations; vendored files may diverge freely.
8. **No authenticated ESI in v1.** Public endpoints only; the structure-market
   question (R3) is answered by exclusion and measurement, not by SSO scope
   creep. Revisiting this is a plan-level decision, not a convenience patch.

---

## 11. Locked implementation decisions

Decided 2026-08-18, before any code exists — the only moment these are free.
The implementing agent follows these without re-litigating; changing one is a
plan-level edit with a stated reason, not a session-level convenience. (This
mirrors the source repo's decision-record discipline, ADR-style, in one table.)

### D1 — Runtime, tooling, layout

| Decision | Value |
|---|---|
| Language / version | Python ≥ 3.12 |
| Package manager | **uv** (`pyproject.toml` + `uv.lock` committed). ~~no requirements.txt layering — there is no GUI tier to layer~~ — **amended 2026-08-20 (third directive, §17 D-14): there is now a GUI tier, and it is an optional extra rather than a layer.** `uv sync --extra gui` installs it; nothing outside `src/evescreener/gui/` may import it. |
| Package name / layout | `src/evescreener/` package; vendored files under `src/evescreener/vendored/` with `VENDORED.md` at repo root |
| Runtime deps | `httpx[http2]`, `pandas`, `pyarrow`, `numpy` — and nothing else in v1. No ORM, no pydantic; config is stdlib `tomllib` into frozen dataclasses |
| Optional deps | **`gui` extra: `pyside6`** (§19.2). The daemon, the digest and every CLI subcommand must run on a headless box with no Qt installed, and `tests/test_headless.py` enforces it by walking the import graph — a stray module-scope Qt import would otherwise fail no other test |
| Dev deps | `pytest`, `ruff` (lint **and** format), `pytest-qt` (GUI tests run offscreen under `QT_QPA_PLATFORM=offscreen`) |
| Lint policy | ruff defaults + `I` (isort); no per-file exemptions — this repo never grows a lint-exempt monolith |
| Entry point | `python -m evescreener <cmd>`. Subcommands, after §17 D-5 and this build: `selftest`, `sde`, `census`, `ingest-history`, `sweep-books`, `anchors`, `screen`, `digest`, `backtest [--setup NAME]`, `killmails`, `cross-region`, `paper {open,close,pass,mark,report,real-fill}`, `watch {add,remove,list}`, `brief`, `board`, **`scan`**, **`setups`**, **`reasons`**, **`learning`**, **`gui`**, **`haul {scan,profile,record}`** (§23), `report`, `daemon`. Additive only; nothing was removed or renamed |
| Process model | One asyncio scheduler process (`daemon`) owning all cadences (§3.2). Individual subcommands run the same jobs once, for manual/backfill use |
| Host | The always-on mini-PC, in its own directory and venv, fully isolated from TradingBotV3. Registered via Windows Task Scheduler at logon; the code stays OS-agnostic (no Windows-only APIs) |
| Timezone rule | All internal timestamps tz-aware UTC. EVE time *is* UTC; the digest displays UTC only |

### D2 — Configuration

| Decision | Value |
|---|---|
| Config file | `config.toml` at repo root, **gitignored**; `config.example.toml` committed with every key present and commented |
| Env override | `EVESCREENER_DATA_DIR` overrides the data dir; nothing else needs an env var in v1 |
| Data dir default | `./data/` (lake + `state.db` + JSONL streams, per §3.5) |
| Secrets | Discord webhook URL lives in `config.toml` only. No secret ever committed; `selftest` fails if the example file and real config key sets diverge |
| User-Agent | `EveTradingbot/{version} (aaron.siano@gmail.com; +https://github.com/Isidore94/EveTradingbot)` — operator contact per CCP best practices. The version string tracks `pyproject.toml` |
| X-Compatibility-Date | Pinned `2026-08-18`; bumped deliberately, never floated. **Constraint recorded 2026-08-20 (§17 D-21):** whatever the pin is, it must already have passed on CCP's **UTC-11** clock, or every route answers HTTP 400. `selftest` now enforces one full day of margin. The decision is unchanged; only the set of sendable values was ever in question |

### D3 — Cadence and cost defaults (config keys, with locked defaults)

| Decision | Value |
|---|---|
| Digest build time | daily **16:00 UTC** (also on demand via `digest`) |
| Forge book HOT window | **15:00–17:00 UTC**: sweep every cache window (~5 min). Outside it: hourly. (§3.2's dial, resolved) |
| History job | daily **11:20 UTC** (§3.2) |
| Notional tiers | 0.25B / 1.0B / 2.5B ISK (§3.4, §5) |
| Liquidity floor | **~~DERIVED FROM THE CENSUS, 2026-08-20~~ — SUPERSEDED as a membership rule by the operator floor below (directive 2026-08-20, fourth). Left visible, not deleted.** The derived rule, stated before the measurement, was: *fewest types while capturing ≥95% of median daily ISK turnover*. It resolved to **median 30d `isk_value` ≥ 500,000,000 ISK**, **median `order_count` ≥ 0** — admitting **2,654 types** carrying **95.1%** of median daily turnover, from 19,152 Forge-active types. It remains the honest read of *where the region's ISK is*, and turnover remains the index **weighting** input. It is no longer what decides who is tradeable. |
| **Membership floor (operative)** | **Median 30-day UNIT volume.** Median, never mean — one wash-trade day must not lift a dead item over the floor. Config keys `universe.min_median_unit_volume` (default **1,000 units/day**) and `universe.absolute_min_unit_volume` (default **100 units/day**). Three tiers: **OK** (≥ 1,000/day — tradeable, index-eligible), **THIN** (100–999/day — carried, charted, scanned, **badged THIN on every surface**, excluded from FORGE), **below** (< 100/day — not tradeable, resolves on direct lookup and says why). NPC-price-seeded types are excluded before tiering. Weighting stays median-30d ISK turnover with chain-link and single-name cap: **the unit floor decides who is IN, turnover decides how much they COUNT**, so 4-ISK dust clearing the unit gate cannot distort the index. |
| Membership floor — measured, 2026-08-20 | Rebuilt against the full lake (12,143 Forge types with bars in the trailing 30 days, from 19,152 Forge-active). **OK: 1,002 types. THIN (100–999 units/day): 999 types. Below the absolute floor: 17,151** (10,142 measured-and-below plus 7,009 with no bars in the window). Tradeable universe **2,001**. Against the previous tracked set this **added 1,418** names and **dropped 2,071**. |
| NPC-price-seeded exclusion — measured | A type whose close did not move at all across the window (tolerance 1e-7, ≥5 bars) has its price held by an NPC vendor, not by a market. Measured: **163 pinned of 12,143**, of which **3 sit inside the OK tier** and 5 inside THIN. Those 3 are excluded from FORGE — a flat line absorbs index weight and reports nothing — leaving **999 index-eligible names**. They stay tracked and chartable. The "did not move at all" test is a proxy for NPC seeding, chosen because it is measurable from the bar contract alone and because it targets the property that actually matters to an index; it is recorded as a proxy, not as ground truth about which items CCP seeds. |
| ⚠ What the unit floor costs, stated plainly | The OK tier carries only **33.1%** of the region's median daily ISK turnover; THIN adds **9.9%**. The 500M-ISK derived floor's 2,654 types split **356 OK / 227 THIN / 2,071 below**. So the operator's rule deliberately gives up ~57% of the region's ISK — and that ISK is real, it is concentrated in high-price low-unit goods (PLEX, injectors, capital hulls) where a day's volume is a handful of units. This is not a defect in the measurement: it is the trade the rule makes on purpose, buying exit-ability with coverage. It is recorded here so no later reader mistakes "we track 33% of the ISK" for a bug. |
| ⚠ ~~Floor caveat — `order_count` fell out~~ — RESOLVED by the amended membership rule | The derived floor's `order_count` component came out **zero**, leaving the anti-wash-trade protection §3.6 and §4 wanted from it inactive. The amended rule closes that gap differently and more directly: membership is now gated on **median** unit volume, and a median is not movable by one wash trade at all — the original concern was that `volume` *alone* was gameable, and it was the mean that made it so. `order_count` is still measured and still printed; it is simply no longer load-bearing. The old caveat text is kept above so the reasoning chain stays visible. |
| Skills | Accounting V (tax 3.375%), Broker Relations V (fee 1.0%) as config defaults (§5); checks #5/#6 may correct the constants |
| Budget self-caps | orders: 6,000 tokens/window hard stop; history: 150 req/min (§3.2) |

### D4 — Phase 0 seed watchlist (50 names)

Resolved to type_ids against the SDE at ingest; **an unresolvable name is a
loud error, never a silent skip** (names drift across patches). The operator
edits freely; this is the starting roster:

- **Minerals (8):** Tritanium, Pyerite, Mexallon, Isogen, Nocxium, Zydrine, Megacyte, Morphite
- **Fuel (6):** Nitrogen Fuel Block, Oxygen Fuel Block, Helium Fuel Block, Hydrogen Fuel Block, Nitrogen Isotopes, Helium Isotopes
- **Account-tier (3):** PLEX, Large Skill Injector, Skill Extractor
- **Consumables/charges (6):** Nanite Repair Paste, Antimatter Charge M, Antimatter Charge L, Scourge Light Missile, Scourge Heavy Missile, Inferno Heavy Missile
- **Drones (3):** Hobgoblin II, Hammerhead II, Ogre II
- **T2 module staples (12):** Damage Control II, Large Shield Extender II, Ballistic Control System II, Gyrostabilizer II, Heat Sink II, Magnetic Field Stabilizer II, Drone Damage Amplifier II, 10MN Afterburner II, 50MN Microwarpdrive II, Warp Disruptor II, Warp Scrambler II, Stasis Webifier II
- **Hulls (12):** Caracal, Vexor, Drake, Ferox, Hurricane, Myrmidon, Gila, Ishtar, Praxis, Dominix, Raven, Megathron

### D5 — Testing and fixtures

| Decision | Value |
|---|---|
| Test policy | Offline by default; live calls only under `@pytest.mark.network`, run intentionally. CI-equivalent gate = `pytest -q` green + `ruff check` clean before every commit |
| Fixtures | Golden fixtures under `tests/fixtures/`: recorded ESI responses (JSON) and frozen expected frames. From Phase 2, detector/scoring changes regenerate fixtures **first** (source-repo rule, kept) |
| Fixture provenance | Every fixture carries `acquired_at`, source URL, and the `X-Compatibility-Date` it was recorded under |

### D6 — Delivery

| Decision | Value |
|---|---|
| Discord mechanism | **Webhook** (not a bot) in v1. One channel. The ported `push_notify` contract keeps `unconfigured/delivered/rejected/ambiguous` + new `rate_limited` (429 + `retry_after`) |
| Digest length | Content ≤ 2,000 chars per message; the digest splits into numbered messages rather than truncating silently (the source repo's "say when a line was dropped" contract) |
| Urgency | No @here/@everyone in v1 — nothing in a D1 screener is urgent. Revisit only if a price-alert feature is ever added, as its own decision |

### D7 — Anchors

| Decision | Value |
|---|---|
| Anchor calendar | `config/anchors.jsonl` (committed — it is data, not secret): `{date, label, scope}` where scope is `global` or a `market_group_id` subtree. **Operator seeds it during Phase 2's gate** with the patch/expansion dates he considers live; the Phase 2 RSS watcher appends candidates for his confirmation, never auto-anchors |

### D8 — Governance

| Decision | Value |
|---|---|
| Control set | `CLAUDE.md` (agent operating rules), `plan.md` (this file — roadmap + contracts), `CHANGELOG.md` (implemented inventory), `CURRENT_CHECKPOINT.md` (single active item + verification stamp). No AGENTS.md copy — one file, one truth |
| Status vocabulary | `IMPLEMENTED → GREEN → LIVE_VALIDATED → PROMOTED`, as defined in the source repo; only the operator promotes |
| Session rule | One phase active at a time; a session that finishes a phase stops at its gate and hands the gate checklist to the operator |

---

## 12. Paper trading platform

**Status: specified 2026-08-20 under operator directive 2026-08-20, which
promotes paper trading from an implied Phase-3 activity to the centrepiece of
the build.** The reasoning is the operator's and it is correct: the backtest
(§13) only justifies *running the experiment*; paper trading **is** the
experiment. A screener nobody can score is a toy.

### 12.1 What it is and is not

An append-only decisions ledger with realistic fills, marked daily, reported
against a verdict rule frozen before the first trade. It is **not** a
simulator, not a broker adapter, and not a step toward automation (§10.1).
Nothing in it places an order or touches the EVE client.

### 12.2 Fill realism — non-negotiable

These rules exist because a paper record that flatters itself is worse than no
record: it manufactures the confidence to risk real ISK.

**AMENDED 2026-08-21 (operator directive, §17 D-32): the ledger records two
fill models, `taker` and `maker`.** The original rules are left visible below
rather than rewritten, because the taker rules were frozen before the first
trade and the §17 verdicts were measured under them. The amendment adds a
second population; it does not restate the first.

The rule that did **not** move, and is now stated explicitly because it was
the thing actually asked for: **there is no mid fill.** No EVE order type
executes at the midpoint. Asking for one is refused and the refusal is
recorded. With a 98.8% median Forge spread (§17) half of it is not a rounding
convention — it is larger than every effect this system has ever measured, and
a record built on it would be exactly the self-flattery this section exists to
prevent.

| Rule | Consequence |
|---|---|
| **Taker** entries are **ask-walk fills** at the operator's declared notional, taken from a **live book sweep** | Not best ask, not mid, not the daily close. The declared size sets the price. This is the default, and the only fill a snapshot *proves*. |
| **Taker** exits are **bid-walk fills** × (1 − sales tax) | The tax is inside every exit, always. |
| **Maker** entries post one tick above the executable bid; **maker** exits post one tick inside the executable ask. Both pay the **broker fee** as well as the tax | Size does not move a posted price, so the tier is not walked. What it costs instead is queue position — see below. Broker fee is read per station from the executable quote (§21 R4). |
| Every maker record carries `fill_assumed: true` and its assumption text | A snapshot proves the price was **postable**, never that anyone traded into it. Undercutting, queue position, waiting time and adverse selection are **unpriced**, and no number in this system bounds them (the same limit §17 D-31 states for SPREADS). |
| `paper report` scores each fill model as its **own** population under the same frozen §12.4 rule | A taker pays the spread and a maker is paid it; one blended win rate answers neither question. The frozen whole-sample verdict is still printed, and says so when the sample mixes both. |
| An operator-supplied close price is **evidence**, not an assumed fill | `fill_assumed` is false for a price he really got, whichever model opened it. |
| A maker position is marked on its **own** model, with the taker liquidation mark recorded beside it | What the plan says it is worth, and what walking out today would actually pay, are two different numbers and the reader gets both. |
| ~~Maker exit is shown **advisory only** and never realized~~ — superseded 2026-08-21 | The original rule, left visible. It was the right call while the maker side was unmodelled; D-31 measured that side for SPREADS, and the operator's directive extends it to the ledger. `maker_exit_advisory_net` is still written on every open. |
| A book older than `paper.stale_book_minutes` (60) **refuses the fill** | The position is not opened. `UNKNOWN` is the outcome, not a price off history. **Never prices off history, ever.** |
| No retro-entries | An open is stamped with the sweep that priced it. There is no "I would have bought it on Tuesday". |
| Self-impact flag when notional > 10% of 30-day median daily ISK turnover | The fill is still recorded; it is *labelled* as a size the market would have noticed. |
| Daily mark-to-market carries a staleness stamp | A mark computed off a 6-hour-old book says so. |

### 12.3 The ledger

`data/streams/paper.jsonl`, append-only, one record per event
(`open`, `mark`, `close`, `real_fill`). Nothing rewrites history; a correction
is a new record, not an edit. Every record carries the sweep timestamp, the
book age at decision time, the thesis fields, the planned net-R, and every
cost component separately so the arithmetic can be audited after the fact.

The **SMALL-REAL rung**: when the operator takes the same trade for real, he
records the actual fill beside the predicted effective price. That is how the
cost model gets validated against reality rather than against itself. The
tolerance is stated now, not retrofitted: **predicted vs actual effective
price within ±0.5% of notional** (this is the same tolerance §8 Phase 4's gate
names, and the two are deliberately the same number).

### 12.4 The verdict tracker — FROZEN 2026-08-20, before the first trade

`paper report` leads with refused/UNKNOWN counts (because a system that
refuses to price things is doing its job and that must be visible first), then
closed count, cumulative net P&L, win rate, R distribution, open positions,
and the verdict.

Definitions, fixed:

* A **closed trade** is one `open` matched by one `close`, both priced from
  live sweeps. Refused opens are not trades.
* `net_return_pct` = (exit effective price × (1 − tax) / entry effective
  price − 1) × 100. Every number is net; gross never appears.
* `breakeven_win_rate` = mean|loss| / (mean win + mean|loss|) over the closed
  sample. This is the win rate the observed payoff ratio *requires*.
* `wilson_lb` = Wilson 95% one-sided lower bound of the win rate at the
  observed sample size.

The rule:

| Closed trades | Verdict |
|---:|---|
| < 20 | `TOO_EARLY` — no read is offered, and none should be taken. |
| ≥ 20 | **First read.** Report the numbers; state `PROMISING` if cumulative net P&L > 0 **and** `wilson_lb > breakeven_win_rate`, else `WEAK`. Neither is a decision. |
| ≥ 40 | **Falsified** if cumulative net P&L < 0 **and** `wilson_lb < breakeven_win_rate`. That is the answer to the operator's question at this size and cadence: it is not a money-making activity for him, and the honest thing is to stop. |
| ≥ 40 | **Provisionally confirmed** if cumulative net P&L > 0 **and** `wilson_lb > breakeven_win_rate`. Promotion to real ISK remains an explicit operator decision (§8's ladder), never an automatic consequence. |
| ≥ 40, otherwise | `INCONCLUSIVE` — keep running. |

The tracker never moves these thresholds after the fact. If they turn out to
be the wrong thresholds, that is a plan-level edit made *before* the next
sample, with the reason stated and the old rule left visible in this file.

---

## 13. Historical viability backtest — hypothesis and verdict rule

**FROZEN 2026-08-20, before the study was run.** Promoted from "deferred" to
required by operator directive 2026-08-20. The purpose is to vet the setup
*class* on data before the operator risks his time on it.

### 13.1 The hypothesis

> **H1.** In The Forge, a type trading below its anchored value while its
> demand is still intact produces a positive net expectancy over 5–20 trading
> days, after all EVE frictions at a real notional.

"Below anchored value" and "demand intact" are defined mechanically in §13.2
so the study cannot be steered by interpretation after the fact.

Explicitly **not** hypothesised, and not testable by this study by
construction: any momentum or breakout-continuation effect. EVE supply is
player-produced and elastic; spikes are arbitraged flat by industrialists
(§6). The backtest does not search for continuation setups, and finding one
would not license building one — momentum is out of scope even as a study
(operator directive 2026-08-20).

### 13.2 The setup, defined mechanically

At bar `t`, a type is an instance of the setup iff **all** of:

1. **Below anchored value.** `close(t) < AVWAP(anchor) − entry_band_sigma × σ`
   where the anchor is the most recent *confirmed* applicable anchor at `t`
   (§11 D7), or — when none exists — a rolling anchor `anchor_lookback_days`
   (90) bars back. σ is the frozen running-AVWAP volume-weighted deviation
   (§4). Default `entry_band_sigma = 1.0`.
2. **Demand intact — relative strength.** `RRS(t)` vs the Forge Composite
   `≥ min_rrs` (default −0.5). A type in freefall relative to the market is
   not a dip, it is a decline.
3. **Demand intact — participation.** `order_count(t) / mean(order_count,
   trailing 20, excluding t) ≥ participation_floor` (default 0.7). A price
   move on collapsing `order_count` is a thin-book artifact (§4).
4. **Measurable.** ATR(20) is known **and is at least
   `signals.min_atr_fraction` (1e-6) of the close**, the bar is not a ghost
   day (`order_count > 0`), and the type has at least `min_bars` (120) bars of
   history at `t`. The same floor governs the AVWAP sigma, because dip-σ
   divides by it and fails the same way.

   > **Superseded text, left visible (amended 2026-08-20, §17 D-29).** This
   > gate previously read *"ATR(20) is known, the bar is not a ghost day
   > (`order_count > 0`), and the type has at least `min_bars` (120) bars of
   > history at `t`"* — an **absolute** test that asked only `atr > 0`. On the
   > real lake that admitted 1.33% of tracked types whose ATR is float noise
   > (measured as low as 1.7e-14 of price), and everything that divides by a
   > risk unit then exploded: RRS reached **−905 billion**. An unmeasurable
   > risk unit is uncertainty, not a pass (§4). This is a change to a frozen
   > §13.2 definition, made with fixtures first and the old wording kept
   > here.
5. **Tradeable.** The type clears the census-derived liquidity floor.

Any gate that cannot be evaluated is **UNKNOWN and fails** (tri-state, §8).

### 13.3 Forward measurement

Each instance is measured at horizons 5, 10 and 20 trading days. Entry and
exit both bear full costs at each notional tier.

### 13.4 Fill realism — and its hard limitation, stated up front

**There are no historical order books.** ESI publishes daily aggregates, not
depth, and nothing reconstructs a 2025 book. This study therefore prices
historical entries and exits at the daily close (= ESI `average`) with a
**conservative slippage haircut derived from data**:

* For each type, measure from **live** `book_summary` sweeps, per notional
  tier: `entry_haircut = ask_walk(tier)/mid − 1` and
  `exit_haircut = 1 − bid_walk(tier)/mid`.
* Apply that type's own measured `round_trip_haircut` to its own history.
* A type whose **current** book cannot fill the tier is flagged
  `haircut_unknown` and **excluded from that tier's results**, with the count
  of exclusions reported. It is never silently priced at zero slippage.

Then:

```
entry_effective = close(t)   × (1 + entry_haircut × m)
exit_effective  = close(t+h) × (1 − exit_haircut  × m) × (1 − sales_tax)
net_return_pct  = (exit_effective / entry_effective − 1) × 100
```

for haircut multiplier `m ∈ {1, 2, 3}` (§13.6).

### 13.5 What is reported

Per **setup variant** (the σ threshold and demand gates) and per **market-group
cohort**, at each horizon and each notional tier:

- sample count `n`;
- win rate and its **Wilson 95% one-sided lower bound**;
- **net expectancy per trade** (mean `net_return_pct`);
- the strategy equity curve's **maximum drawdown**;
- **sensitivity**: every metric at 1×, 2× and 3× the measured haircut;
- the `haircut_unknown` exclusion count;
- **added 2026-08-20, after the first run:** the **gross** expectancy and win
  rate of the same instances, and the measured round-trip friction. This does
  not change the verdict rule — it makes a negative verdict *readable*. "The
  setup has no edge" and "the setup has an edge that EVE's frictions eat" are
  different answers with different next steps, and without the gross figure the
  report cannot tell them apart. Reporting more is never a retrofit; the
  **rule** in §13.6 is untouched.

### 13.6 The verdict rule — FROZEN before measurement

At the **0.25B tier** (the smallest, because a setup that needs size to work
is not a setup the operator can start with):

> The setup class is **PLAUSIBLE** iff **all** of:
> 1. `n ≥ 100` at the horizon under test;
> 2. net expectancy per trade remains **> 0** at **2× the measured haircut**;
> 3. `wilson_lb(win rate) > breakeven_win_rate` on the full sample, where
>    `breakeven_win_rate = mean|loss| / (mean win + mean|loss|)`;
> 4. condition 3 holds **independently in both halves** of the sample period.
>
> Fail any of 2–4 → **NOT PLAUSIBLE**.
> `n < 100` → **UNKNOWN**, which is not a pass and never rounds up to one.

### 13.7 Limitations the report must state about itself

A backtest that hides its own weaknesses is worthless. Every generated report
carries these, verbatim, in its own body:

1. **No historical depth.** Fills are close-to-close with a haircut measured
   from *today's* book. A type whose liquidity has changed since 2025 is
   mispriced by exactly that change, and the direction is unknowable.
2. **Close-to-close fills.** ESI `average` is a whole-day mean, so the study
   can neither buy the low nor sell the high, and equally cannot be hurt by
   intraday adversity. This cuts both ways and is not conservative by default.
3. **~13.5-month window.** One year is one meta. A patch cycle, a war, or a
   single industry rebalance can dominate it.
4. **Survivorship.** The universe comes from types with *live orders today*.
   Types that died between 2025 and now are absent, and their absence is
   invisible to the win rate.
5. **No concurrency or capital constraint** unless configured: expectancy is
   per instance, not per ISK-day of a real portfolio.
6. **The haircut is measured, not the spread paid.** It assumes the operator
   crosses the spread exactly as the depth walk describes, at one moment.
7. **Instances overlap** (added 2026-08-20 after the first run exposed it). The
   setup fires on *every* qualifying bar, so one sustained dip contributes many
   instances sharing most of their forward window. That inflates `n` and makes
   the Wilson bound tighter than the independent-sample reading it resembles.
   It does **not** bias the expectancy, which is what §13.6's verdict actually
   turns on — but any conclusion resting on the interval rather than the mean
   should be read as weaker than its `n` suggests. A non-overlapping variant is
   a **separate study with its own pre-stated rule**, not a re-run of this one.

---

## 14. Destruction lead-lag study — hypothesis and pass rule

**FROZEN 2026-08-20, before the study was run.** This is the system's genuine
edge — item-level demand-destruction telemetry that equity systems do not have
(§7) — so it is measured properly or it does not influence a ranking.

### 14.1 The hypothesis

> **H2.** `destruction_z` leads `order_count`/`volume` upticks and price
> firming in doctrine-class hulls and their fitted modules by **1–5 days**.

`destruction_z(type, t)` = (units destroyed in the trailing 7 days − mean of
the trailing 90-day 7-day-window baseline) / (std of that baseline). Losses
are bucketed by region catchment: Forge-adjacent war-zone losses replace at
Jita.

### 14.2 Method

For each lag `k ∈ {1..5}`, pooled over the cohort, compute the **Spearman rank
correlation** between `destruction_z(t)` and each of:

* `participation(t + k)` = `order_count(t+k)` / its trailing-20 baseline;
* `forward_return(t, k)` = `close(t+k)/close(t) − 1`.

Significance uses the large-sample normal approximation
`z = ρ × sqrt(n − 1)`; `p < 0.01` two-sided ⇔ `|z| > 2.576`. (No scipy — the
dependency set is locked at four runtime packages, §11 D1 — so the
approximation is stated rather than hidden inside a library call.)

### 14.4 Method amendment — added 2026-08-20 by §21 R5

**§14.1–14.3 above are FROZEN and are not edited.** The pass rule, its
thresholds and the recorded result all stand exactly as written. What follows
is an amendment to the *method*, added beside them with its reason, because the
implementation did not test the hypothesis §14.1 declared.

**1. The cohort was wrong.** H2 names *doctrine-class hulls and their fitted
modules*, with losses *bucketed by region catchment*. The implementation pooled
global destruction against every type in the lake. That is a different
population: pooling unrelated catalogue types can dilute a real effect as
easily as manufacture one. The recorded rho=0.027 on 473,606 observations is
therefore **exploratory**, and is not evidence about H2 in either direction.
Every result now carries a `cohort_declaration` naming its population,
catchment and evidence class, so a pooled run can never later be read as
confirmatory.

**2. Lags were row positions, not calendar days.** `groupby.shift(-lag)` takes
the next *observed* row, so a type trading on 1 January and again on 10 January
had the 10th labelled "lag 1" — a nine-day move counted as a one-day effect.
`exact_lag_frame()` joins on the literal `day + k`, so a gap is **absent** and
therefore UNKNOWN rather than filled by whatever came next.

**3. Observations were treated as independent.** Daily rows on one type are
serially dependent, and rows across types on one day are cross-sectionally
dependent through the market itself. `independent_observations()` counts
**types** — the conservative floor, since the data is certainly not more
independent than that — and reports it beside the raw count.

**4. Ten tests, one alpha.** Five lags times two targets were each judged at
p < 0.01 with no family-wise policy. `LEAD_LAG_TESTS = 10` and
`FAMILY_ALPHA = 0.001` (Bonferroni) are declared, and every lag row now carries
both `p_value_frozen_rule` and `p_value_family_wise`. The frozen verdict is
reported unchanged; the corrected one sits beside it.

**Owed live gate (§21 R5).** The doctrine cohort has been *declared* but not
yet *run*: a confirmatory H2 measurement requires selecting the cohort and
catchment before looking at results, then regenerating. Until that run exists,
the only lead-lag evidence in this repository is exploratory, and the digest
annotation must continue to say the claim was tested and not supported.

### 14.3 The pass rule — FROZEN before measurement

> The effect **survives** iff **all** of:
> 1. at least one lag `k ∈ {1..5}` shows `ρ ≥ 0.10` with `p < 0.01`;
> 2. `n ≥ 500` (type, day) observations at that lag;
> 3. the sign of `ρ` at that lag is the **same in both halves** of the sample
>    period;
> 4. a **placebo** — `destruction_z` shuffled across types within the same
>    day, preserving the daily marginal distribution — yields `|ρ_placebo|`
>    less than **half** the measured `ρ`.
>
> Survives → destruction features **may** influence ranking, after a shadow
> period, never straight in (§8 Phase 5's ladder).
> Does not survive → destruction ships as **digest annotations only**, and the
> annotation says the lead-lag claim was tested and not supported.

---

## 15. Cross-region scan

Hub-to-hub net margin from the WARM sweeps (Domain, Sinq Laison, Heimatar,
Metropolis vs The Forge), with freight netted from a **real PushX quote** at
the SDE packaged volume and the position's collateral, plus a staleness
haircut on cached quotes.

**No freight quote → no cross-region row, ever.** A margin that has not paid
for its own hauling is not a margin, and estimating freight from a formula
would be exactly the kind of invented number this system exists to avoid.

This is a **swing-compatible arbitrage screen** — buy in hub A, haul, sell in
hub B over days — not station trading (§10.5). The holding period is the
freight time plus the sell queue, and the escrow cost of that time is charged.

---

## 16. The viability report

`data/reports/viability-<date>.md`, regenerated on demand, is the single
document that answers the operator's actual question from measurements:

1. **Census opportunity map** (§8 Phase 1) — how many types clear the derived
   floor, and what turnover they carry.
2. **Backtest verdict** (§13) with its sensitivities and its stated
   limitations.
3. **Destruction lead-lag result** (§14) with its pass/fail against the frozen
   rule.
4. **Cross-region margin distribution** after freight (§15).
5. **Running paper-trading tally** (§12) against the frozen verdict tracker.

Every number cites its source (lake query, sweep, study) and its date. A
section whose inputs do not exist yet renders as **UNKNOWN with the reason**,
never as an empty table implying zero opportunity, and never as an estimate.

Reading the report and deciding whether EVE trading is worth the operator's
time is **his** decision. The system's job is to make that decision an
informed one, and to be honest enough that a negative answer is a possible
output.

---

## 17. Operator directive 2026-08-20 — deviations recorded

### Measured facts that correct this document

The following were measured during the build and **supersede** the estimates
and expectations written above. Each is cited where it changes a decision.

| Fact | Measured value (2026-08-20, The Forge) |
|---|---|
| Types with a live order in the region | **19,152** |
| Of those, types with daily history | **14,013 have at least one bar** (3,116,848 bars total). **4,978 return HTTP 200 with an *empty* history array** — an order book with no trades at all in 13.5 months. Only **241 genuinely 404** (1.3%) |
| Full order-book sweep | **415 pages, 414,152 orders, 19,151 types**, ~83 seconds, 830 tokens of a 6,000 self-cap |
| Cross-page `order_id` duplicates in one sweep | **10** (0.0024%) |
| Crossed books (bid above ask) in one sweep | **201 of 16,706** two-sided types (1.2%) |
| Share of sell books that can absorb 0.25B / 1.0B / 2.5B ISK | **77.1% / 55.8% / 39.6%** |
| Sell books where one order holds >50% of resting volume | **25.8%** |
| Median spread across two-sided types | **98.8%** — p5 is 4.2%, p10 is 8.6%. Only ~932 types (5.6%) sit inside a 5% spread, i.e. anywhere near the 3.375% tax floor |
| Volume-weighted structure share, ask side / bid side | **0.0% / 22.0%** |
| Rate-limit incidents across 16,590+ requests | **zero 429, zero 420**; error-limit budget bottomed at 42 of 100 |
| Bars in the lake at the time of measurement | **1,854,651** across **7,264** types |
| Bars whose true range needs clamping (8× rolling median) | **7.9%**, touching **79%** of tracked types |
| Tracked types whose ATR would be >2× too large without winsorization | **20.5%** (p99 143× too large, worst 2,433×) |
| Backtest on the **full** lake (2,654 tracked types, **108,441** setup instances) | 10-day: gross **+2.80%** (win rate 55.7%) against **14.7% friction at 1× / 26.0% at 2×** → net **−20.0%**. 20-day: gross **+3.91%** (56.0%) → net **−19.2%**. **NOT PLAUSIBLE at every horizon**, on friction rather than direction. The 20-day gross edge *rises* with horizon while friction stays flat, and never catches it |
| Lead-lag on the **full** lake (**473,606** observations, 2,088 types) | best **ρ=0.027** at 1 day vs participation (p=1.2e-76) — **DOES NOT SURVIVE**. Note the effect *halved* versus the 347-type sample (ρ=0.052), while p fell to 1e-76 purely on sample size: exactly why §14.3 required an effect **size** and a placebo rather than significance alone |
| Round-trip taker friction at 0.25B, all fillable types (n=6,672) | min 0.00% · p1 **2.17%** · p5 5.34% · p50 **33.61%**. Types below 1% friction: **27**; below 2%: 55; below 5%: 285 |
| Round-trip friction among the **tracked** types (n=315) | min **0.062%** · p1 1.21% · p5 2.43% · p50 **9.54%**. Only **2 types (0.63%)** sit under the ~0.78% that the measured 20-day gross edge of 4.15% could absorb after the 3.375% sales tax: *100MN Afterburner II* (0.062%) and *Mexallon* (0.767%) |
| Cross-region at 0.25B after real PushX freight and tax | **14 routes** clear costs of **151,123** pairs considered; best **+14.44%** net (Electronic Parts, Dodixie→Hek) |
| `X-Compatibility-Date` rejection boundary | **A pin still in the future on CCP's UTC-11 clock is refused on every route** with `HTTP 400 {"error":"Compatibility date (2026-08-18) is in the future. Current date (UTC-11) is 2026-08-17."}`. Measured 2026-08-18 against live ESI on branch `claude/phase-0-gate-checklist-oucoil` (commit a7f5872), where the pin took every request down until corrected. Not a degraded run — a total outage from a one-line config value |
| Secondary hub sweeps | Domain 181,540 orders / 15,510 types; Sinq Laison 121,872 / 14,249; Metropolis 118,993 / 11,272; Heimatar 72,572 / 10,729 — all complete |

The spread distribution is the single most important number here for the
operator's question: **the long tail of EVE's item catalogue is not a market**,
it is a list of things with a bid and an ask that are nowhere near each other.
Any claim about "thousands of opportunities" has to survive the fact that only
~932 Forge types trade inside a 5% spread at all, before costs.

And the friction row above is the sharpest form of the answer. The setup's
measured 20-day gross edge is **4.15%**; after the 3.375% sales tax it can
absorb roughly **0.78%** of round-trip spread-and-depth cost. Among the 315
tracked types with a measurable book at 0.25B, **two** are that tight. The
open question §13's verdict leaves — "is there a subset where this works?" —
therefore has a measured scope, and the scope is two names. Two names is not a
strategy. Any follow-up study should start from that number rather than from
the hope that the subset is large.



The operator authorized a single-push build of the complete v1 system,
overriding the one-phase-per-session rule for this build. Every deviation from
this document's prior text is recorded here with its reason.

| # | Deviation | Reason |
|---|---|---|
| D-1 | **Phases 0–6 collapsed into one build.** The per-phase gates collapse into one consolidated live-validation checklist handed to the operator at the end. | Operator directive 2026-08-20. The gates are not waived — they are batched, and every one of them still owes its live evidence before the system is trusted. |
| D-2 | **Historical viability backtest promoted from deferred to required** (§13). | Operator directive 2026-08-20: the mission is to vet whether EVE swing trading makes money, not to ship a screener. |
| D-3 | **Destruction lead-lag study promoted from Phase 5's gate to a required deliverable** (§14). | Same. |
| D-4 | **Paper trading platform specified as the centrepiece** (§12), where the prior text had only an append-only decisions log. | Operator directive 2026-08-20: the backtest justifies the experiment; paper trading is the experiment. |
| D-5 | **Subcommand set extended** beyond §11 D1's list with `paper`, `backtest`, `killmails`, `cross-region`, `report`, `sde`, `screen`. | The added deliverables need entry points. Additive only; no listed command was removed or renamed. |
| D-6 | **SDE source changed** from per-file jsonl URLs to the per-build bundle zip (`latest.jsonl` → `eve-online-static-data-{build}-jsonl.zip`). | Measured 2026-08-20: the flat per-file URLs return HTTP 403; the bundle URL serves. The plan's §0 statement that the SDE "includes types and marketGroups" holds — the packaging differs. |
| D-7 | **Vendored files carry mechanical lint edits** and are marked `diverged` in `VENDORED.md`. | §11 D1 forbids per-file lint exemptions, so excluding `vendored/` was not available. No numerical behaviour changed. |
| D-8 | **Upstream branch for vendoring is `phase05-integration-blitz`**, not `phase05-r8-weekend-prep`. | Measured 2026-08-20: the r8 branch no longer exists on the remote. The integration-blitz branch carries the same tree. Module paths are under `scripts/`, not the repo root, correcting §0's file:line citations by that prefix. |
| D-9 | **The `≤15k LOC` budget (§1) is exceeded** by the added studies and the tests they need. | Operator directive 2026-08-20 authorized this explicitly, requiring the final count be stated. **Final: 17,134 LOC — 2,134 over the §1 budget.** Breakdown: **10,751 product**, **1,435 vendored**, **4,948 tests**. The overage is entirely tests and the two promoted studies; the product surface is 10.5k against a 15k ceiling. §1's budget stands for future work — this is a one-time, authorized exception, not a new ceiling. |
| D-10 | **§3.2's claim that 4xx "should not occur in the steady state" is withdrawn** — but by less than an earlier draft of this table claimed. | **Corrected 2026-08-20 after the full crawl.** A first, aborted run reported 16,789 "failures" and that number was written here as if it were the 404 rate. It was not: it was the circuit-breaker cascade described in D-12, i.e. the symptom of a bug in this repo, mistaken for a property of ESI. The completed crawl measures **241 real 404s out of 17,325 history requests (1.3%)**. 4xx does occur in the steady state and must not trip a breaker, so the fix stands; the magnitude claim did not, and is retracted. |
| D-12 | **A per-item 404 no longer trips the per-feed circuit breaker.** | The breaker treated each 404 as a feed failure and latched open permanently, turning a 1.3% catalogue gap into a total ingest outage after 2,363 of 19,152 types — and producing the false 16,789 figure above. Protection against the legacy 100-errors/minute limit moved to the error-limit guard, which yields at 25 remaining. Measured across the completed crawl: zero 429, zero 420. |
| D-11 | **The census floor grid gained looser corners** (a no-floor row, 1M ISK, 0 and 1 order_count). | Measured 2026-08-20: the original grid's loosest corner (10M ISK / 5 orders) captured only 88.2% of median daily turnover, so §8 Phase 1's derive rule could not resolve at its 95% target. The **rule is unchanged**; only its candidate set widened downward. |
| D-13 | **Operator workflow surfaces added**: `watch`, `brief`, `board` subcommands and the digest's watchlist section (§18). | Second operator directive 2026-08-20: "I want my TradingBotV3 moved to EVE with relevant changes" — the analytical core alone is not the daily product; the desk workflow is. Additive only, observation-only by the §18.1 rule. LOC after: **18,296** (11,575 product, 1,435 vendored, 5,286 tests) — the D-9 exception grows by 1,162 and §1's budget still stands for future work. |
| D-14 | **§10.6's "no GUI" non-goal is REVOKED.** A full PySide6 desk ships as §19 Part 2. | Third operator directive 2026-08-20: "Build the full desktop desk modelled on TradingBotV3." §2's lesson — that the 42k-LOC Qt desk was the thing that made the source repo unmaintainable — is answered structurally rather than by abstention: Qt is an **optional dependency tier**, the core must run headless, and `tests/test_headless.py` walks the import graph to prove that `daemon`, `digest` and every CLI subcommand import without PySide6. The desk is 2,972 LOC of product against the source repo's 42,000. |
| D-15 | **§6's "no momentum/breakout-continuation logic" row is NARROWED, not deleted.** The row still governs the **system's** recommendation engine, which stays the frozen dip-below-value class. **Operator-defined setups (§19 Part 3) may express anything, including trend and continuation.** | Third operator directive 2026-08-20: *"The machinery's job is not to argue with my setups — it is to measure them honestly and tell me which ones earn."* The elasticity argument that motivated §6 is a claim about EVE, and a claim is a thing to measure, not a thing to make unexpressible. One of the three shipped example setups is deliberately continuation-shaped so the machinery is seen measuring one. |
| D-16 | **The membership floor changes from derived turnover to operator unit volume** (§11 D3, rewritten with the old rule left visible as superseded text). | Fourth operator directive 2026-08-20 (Amendment 1). The measured cost is recorded beside the rule: the OK tier carries **33.1%** of the region's median daily ISK turnover and THIN another **9.9%**. That ISK is real and it is given up on purpose, buying exit-ability with coverage. |
| D-17 | **A price-pinned type is excluded from the index** — a type whose close did not move at all across the window (tolerance 1e-7, ≥5 bars). Measured: **163 pinned of 12,143**, of which **3 sit inside the OK tier**, leaving 999 index-eligible names. | Fourth directive: "NPC-price-seeded excluded". The zero-dispersion test is a **proxy** for NPC seeding, chosen because it is measurable from the bar contract alone and because it targets the property that actually matters to an index — a member that cannot move contributes no information while absorbing weight. Recorded as a proxy, not as ground truth about which items CCP seeds. |
| D-18 | **Opening a paper position now requires a setup tag and at least one like tag**; passing requires at least one dislike tag. Both refusals are recorded in the ledger. | Fourth directive (Amendment 3). This is a **behaviour change to an existing surface**: every prior `paper open` call site had to gain the new arguments, and a trade that could previously be recorded with only a thesis can no longer be recorded at all. That is the point — a trade whose reason is not recorded is a trade the learning loop can never attribute in either direction. |
| D-19 | **`near_level` conditions cannot be backtested and produce zero instances**, rather than being approximated or silently dropped from the setup. | The level store is built from the whole series, so evaluating "was price near a level on day 40" against a store that knows about day 300 is lookahead. Silently reducing the setup to its other conditions would score a **different setup** than the one written down, which is the specific way a backtest becomes worse than no backtest. |
| D-20 | **The `≤15k LOC` budget is exceeded further, as authorized.** **Final: 27,399 LOC — 18,049 product (of which 2,972 is the desk), 1,435 vendored, 7,880 tests, 35 launcher.** | Third operator directive granted the desk its own ~12,000 LOC budget including its tests, on top of the count at the time (18,296). The desk plus its tests plus the launcher came to **3,477** of that 12,000. The whole third-and-fourth-directive delta is 9,103 LOC, of which 2,594 is tests. §1's budget stands for future work; this remains an authorized exception rather than a new ceiling. |
| D-21 | **`selftest` gained a twelfth check: the `X-Compatibility-Date` pin must be at least one full day past on CCP's UTC-11 clock.** `timeutil.esi_compatibility_today` is the one place that clock is computed. | **Salvaged 2026-08-20 from branch `claude/phase-0-gate-checklist-oucoil` (commit a7f5872)** — a parallel Phase-0 build that ran against live ESI and measured the rejection above. That branch is preserved as tag `archive/phase-0-first-light`. This head pinned `2026-08-18` with **no guard**: the value happens to be sendable today, but nothing stopped the next bump from naming a future date and taking every route down at once. ESI would accept a pin equal to its own UTC-11 date; the check demands a full extra day so a pin cannot clear offline and then fail mid-run as the UTC-11 clock rolls. The §11 D2 *decision* (pinned, never floated) is untouched, and so is the pinned value. |
| D-22 | **Member daily returns are winsorized before they reach the index**, mirroring the ATR path's TR clamp: each member's return is clipped at `k ×` its own rolling median absolute return (`composite_return_clamp_k` = 8.0, `_window` = 60, `_floor` = 0.20), with clamped-day counts carried in every index's diagnostics. Returns are also computed **explicitly** rather than by `pct_change`, so a member needs a real bar on both `t-1` and `t` to contribute at all. | **Measured 2026-08-20 on the operator's own lake.** FORGE had run **1,000 → 69,243** with single-day prints of **+1,661%** (2026-08-02), +94% and +57%. Decomposition named the cause exactly: on 2026-08-02 a single member — *Vanguard Resonant Cypher*, type 95640 — printed `close 10.07 → 22,450.00`, a **+222,839.4%** return, at a 0.75% live weight, contributing **+1,661.59%** of the +1,661.37% the index moved. All 100 members were priced that day, so no gap or NaN path was involved. **The chain-link was never at fault** — §19.1's churn fixture is correct and stayed green throughout. The poison was upstream: `returns = closes.pct_change()` consumed **raw** closes, and §0 check #4 had already measured that CCP does not filter outlier prints. The ATR path had clamped for that exact reason since Phase 2; the index path never did. A spike does not cancel when it reverts, because an arithmetic weighted-return index can gain 222,839% and can only ever give back 100%. **Consequence for RRS:** `power_index = Δref/ATR_ref` measured **1,478**, which swamped every type's own term and left every printed RRS in a −1,479 band — and, because RRS is one of the four gates, the digest reported an honest zero that was really a broken gate. Post-fix: FORGE runs 1,000 → **981** over 415 bars, median |daily move| **0.34%**, max **2.08%**; `power_index` = **−3.28**; and the digest finds **25** candidates. Fixtures were regenerated first per §11 D5 — including a real-data fixture that reproduces the 2026-08-02 day and a test that disables the clamp to prove the clamp is what fixes it. The existing golden index fixture needed **no** regeneration: on clean data the clamp touches nothing, which is the strongest available evidence that it is surgical. |
| D-23 | **A composite's `high == low == close` is documented in `composite.py` as a deliberate close-to-close volatility proxy**, not an oversight. | An index level is one number per day and has no intraday range, so `atr.true_range` on a composite reduces to |Δclose|. That is the honest reading and it is what the RRS power index wants — but it is structurally smaller than a ranged instrument's ATR, so `power_index` runs larger against a composite than against SPY upstream. Post-fix it sits at −3.28; a test now fails if it ever reaches the hundreds again. |
| D-24 | **§19.2 gains a threading contract: the GUI thread never computes, it paints.** Lazy pages, a `compute`/`paint` split on a worker thread, last-good-on-failure with a visible stamp, and recomputation keyed on input fingerprints rather than on a timer. | **Measured on the operator's desk 2026-08-20.** The desk took **217 s** to open against 2,947 tracked types and 4,052,335 bars — `ScannerPage` 145.9 s and `BoardPage` 56.5 s, both synchronous inside `build()` — and a 60 s timer re-ran all of it, so it never became interactive. The earlier build validated the desk on 2,001 types and a 1.85M-bar lake, about half the work, which is why this only appeared now. After: **8.6 s to interactive**, a **15 ms** timer tick, and **0.000 s** to revisit a computed page. Nothing about §3.2 changes — the desk still cannot fetch, and a worker thread cannot make a local read unsafe. |
| D-25 | **`verdict_banner` renders an explicit UNKNOWN banner when no study is stored**, where it previously returned an empty string. | `data/` is gitignored, so a fresh clone has no `backtest-*.json` and MARKET and SCANNER carried **no warning at all** — a desk that has never measured anything looked exactly like one that measured and passed. UNKNOWN never gets to look like a pass (§4). |
| D-26 | **The patch-notes watcher dedupes candidates on the article URL as well as on (date, label).** | Measured 2026-08-20: `config/anchors.jsonl` held *Patch Notes - Version 24.01* on both 08-19 and 08-20 under an identical source URL, because CCP re-dated the article. The watcher runs daily, so the operator would have been asked to confirm one patch twice, and confirming both would anchor twice on one event. The duplicate row was removed. |
| D-27 | **`selftest`'s cost-model check derives the expected tax and fee from config** instead of hardcoding Accounting V's 3.375%. | The pinned constant asserted a *skill level*, not the arithmetic. It happened to hold for this operator (Accounting V, Broker Relations IV) and would have failed anyone who had not trained Accounting to V, on a correct install. |
| D-28 | **`universe.seed_watchlist` is deleted.** | It read `config.universe.watchlist` and resolved all 50 §11 D4 names, and **nothing in `src/` ever called it** — only the tests did. The roster was seeded through the documented `watch add` path on 2026-08-20 (50 resolved, 0 unresolved) and those entries are operator-owned. Wiring an automatic seeder was deliberately not done: a re-seed would resurrect a name the operator had removed, which is §11 D4's never-auto-removed rule failing in the other direction. |
| D-29 | **§13.2's `measurable` gate becomes RELATIVE.** A type is measurable only when `atr / close >= signals.min_atr_fraction`; below it the gate is UNKNOWN, and UNKNOWN fails (§4). One epsilon governs the ATR **and** the AVWAP sigma, applied at one definition site (`atr.measurable_fraction`, enforced inside `atr_last` so no scalar consumer can bypass it) plus the two per-bar paths in `setup.py` and `rrs_series`. The §13.2 text is amended with the old wording left visible. | **Measured, and the default is derived rather than chosen.** Across 2,914 Forge types with a positive ATR, `atr/close` is bimodal: a degenerate cluster from **1.7e-14** to about 1e-11, then three orders of magnitude of near-empty space — p1 is **1.6e-08**, p2 is **2.4e-05** — then the working distribution (p50 **5.8e-02**). **1e-6 sits at the top of that gap**: it marks **39 types (1.33%)** UNKNOWN and touches nothing in the working distribution. 1e-5 would take 1.82% and 1e-4 would take 2.68%, reaching into names that are quiet rather than broken; the conservative end of an empirical gap is defensible in a way that a round number inside the continuum is not. **Effect:** max abs RRS falls from **9.05e11 to 1.19e7**, abs RRS > 1,000 from 77 types to 51, p1 from −1,966 to −677 and p99 from +2,661 to +710, while the median is **unchanged at +3.18** — the body of the distribution is untouched, which is what a surgical gate looks like. Backtest instances **147,140 → 145,655** (−1.0%) and the verdict is **NOT PLAUSIBLE at every horizon**, unchanged. The digest produced **the same 25 candidates, none dropped** — the degenerate types were never clearing costs; they were polluting the board's sort and the RRS distribution. **What this does NOT fix, stated plainly:** 51 types still exceed abs RRS 1,000, and they are *not* degenerate. *Hemorphite II-Grade* has a healthy `atr/close` of 1.55e-04 and reads RRS −2,932 because it fell 45% in 20 bars — **2,936× its own ATR**. That is RRS working, not failing. The board's value sort therefore still shows large magnitudes at the top; they are honest measurements of violent moves, several of which trace to unfiltered ESI prints in `close`, which reporting deliberately does not clamp. |
| D-30 | **The chart draws range candles; a prev-close body is refused on measurement, not on principle.** The operator asked for conventional candlesticks, reasoning that EVE trades 24/7 so there is no session gap and yesterday's close is today's open. That is correct about the market and wrong about this data, and it was settled by measuring rather than arguing. Each candle is a filled body spanning the day's **low→high**, crossed by a notch at the **average**, coloured against the previous average. §4's no-synthesized-`open` invariant is **unchanged**. | **Measured on the full lake — 4,034,697 bars with a previous close.** `close` is the ESI daily *mean transaction price*, not a last trade, so yesterday's mean is not where today opened. Yesterday's close lands **outside** today's measured `[low, high]` on **55.70%** of all bars (27.81% above the high, 27.89% below the low). It is **worse on exactly the names the desk charts**: **68.97%** of tier-OK bars, **66.40%** of THIN, **58.07%** of watchlist bars — and still **46.10%** after excluding the 40.8% of bars where `high == low`. A conventional body would therefore hang off the end of its own wick on the *majority* of bars: not merely a fabrication, a visibly broken rendering. Clamping it into the range would make over half the chart's bodies artefacts of the clamp. **What the data does support:** the range is measured, the average is measured, and the day-over-day change in the average is a comparison between two measured numbers — so body, notch and colour are each real. **What no chart from this lake can ever show:** intraday direction. ESI records no sequence within a day, so whether price rose or fell inside the day is not recoverable at any price; the notch's height within the body is the honest substitute. **Readability, the actual complaint:** the previous HLC rendering was unreadable at the 400-bar default (3.5 px/bar on a desk pane). The chart now opens at **120 bars** with a 60/120/250/all selector, and degrades by measured slot width rather than smearing. **Level series are not candle series.** A composite index is built with `high == low == close` (signals/composite.py — an index level is one number per day), so candles drawn from one are zero-height bodies and MARKET rendered FORGE and FORGE-EW as a field of floating notches. `ChartSeries.ranged` detects the absence of any intraday range and the painter draws a level line instead. Separately, `ChartCanvas` had a non-expanding size policy, so in a `section()` block it split the available height with its own title label and sat squashed at the bottom of a mostly empty pane; it is now Expanding. |
| D-31 | **SPREADS reads the book from the maker's side, and refuses the dust bid.** §17's NOT PLAUSIBLE verdict was measured on a *taker* — cross in, cross out, 14.7% round-trip friction against a +2.80% gross edge. A maker posts both sides and **collects** the spread, so the 98.8% median Forge spread that killed the taker is the maker's revenue. `books.spread_view()` and `CostModel.buy_outlay/sell_proceeds(maker=True)` already existed; `spreads.py` composes them. Maker round trip at the operator's skills = broker 1.300% in + broker 1.300% out + sales tax 3.375% = **5.975%**. Nothing here contradicts §17: both readings are true at once, because they are prices paid by opposite participants. | **The dust bid is the failure mode, and it was measured before the page was designed.** Ranking the raw book by spread produces garbage: a 0.02 ISK bid against a 129,000 ISK ask reads as a **608,000,000%** edge and nothing will ever sell into that bid. Median raw `net_pct` across 16,709 two-sided Forge types is **+181%**, p90 **+37,492%** — numbers that are arithmetically correct and economically meaningless. Anchoring on the **traded average** (the ESI daily mean, the one price transactions are known to have happened at), of 16,381 types with both a two-sided book and an average: **39.7%** have a best bid under half the average (19.8% under a tenth, 9.3% under a hundredth) and **23.6%** have a best ask above twice it. With the guards — bid ≥ 0.5× average, ask ≤ 2× average, ≥100 units/day — **2,230** names survive and **1,590** carry a positive net maker edge, median **+13.0%**, p90 **+57.3%**, top name *Capital Ion Thruster* at bid 301,700 / avg 597,400 / ask 871,600. **CORRECTED by §21 R4 (2026-08-20):** the sentence above originally read that these guards were *derived* from measurement. That was an overclaim and the original wording is left visible here so the correction is auditable. The measurement counted how many observations fall beyond cutoffs that had **already been chosen** — which describes the cutoffs, it does not derive them. They are **operator heuristics**; deriving them would need an outcome-based, preregistered, preferably out-of-sample study of which quotes actually filled, and no such study exists. The counts (39.7% / 23.6%) and the thresholds (0.5x, 2.0x) are **unchanged** — only the claim about their provenance is. Guards are **page controls, not constants**, and 'show excluded' returns the rejects with their `DUST_BID`/`WIDE_ASK`/`NO_AVG` flags so the guard can be checked rather than trusted. **What is still unmeasured and is stated on the page rather than modelled:** whether a posted order ever fills. Undercut risk (0.01 ISK inside your order, defended only by relisting at a broker fee each time) and waiting time are **not** in the lake and no number here bounds them. Volume, top-of-book depth and the top order's share are reported as evidence, never as a probability. A book older than `costs.book_staleness_minutes` prices **nothing** — on the operator's 121-minute-old sweep the page correctly showed an honest zero. |
| D-32 | **The paper ledger records two fill models, and a `mid` fill is refused.** §12.2's taker rules are unchanged and remain the default; `maker` posts one tick in front of the executable quote, pays the broker fee on both legs, and is stamped `fill_assumed`. `paper report` and the desk's PAPER page score the two populations apart under the same frozen §12.4 rule. `config/config.toml` gains `paper.default_fill_model` and `paper.maker_tick_isk`. | **Operator directive 2026-08-21**, asked as "fill at the mid point to simulate a real fill". The mid was declined with the reason stated and the operator chose the maker model instead: no EVE order type fills at the midpoint, so mid is not a simulation of a real fill but an average of two prices the operator could get — and on the 2026-08-21 Forge sweep, Helium Fuel Block quoted a taker round trip of **−11.5%** against a maker round trip of **+2.7%** on the same book at the same second. A number sitting between those two describes no trade anyone can make. **This is the same reading as D-31**, moved from a page that *shows* maker edge to a ledger that *records* it, and it inherits D-31's unmeasured limit verbatim: whether a posted order ever fills is not in the lake, so the ledger flags the assumption on every row rather than pricing it. The §17 verdicts were all measured on takers and none of them moves. |
| D-33 | **HAULING phases H1–H4 built in one push**, overriding the one-phase-per-session rule and the H0-before-H1 ordering — the same override pattern as D-1. The per-phase gates are **batched, not waived**: they become one consolidated owed live-validation checklist in `CURRENT_CHECKPOINT.md`, and H0 converts from a build/don't-build gate into a **keep/park** gate decided after the two-week shadow. | **Operator directive 2026-08-25**, reason as stated: *"build first, evaluate against competitors and live gates afterwards"*. Three things this authorization explicitly does **not** touch, and did not: **H5 and H6 are out of scope entirely** (both would need authenticated ESI, and §10.8 makes that a plan-level decision rather than a convenience); no hard invariant is weakened; and nothing existing changed — the whole track is additive, with a regression test asserting `book_summary`'s frame is byte-identical through the modified sweep path. The competitor set the directive named was checked live the same day and corrected in §23.2: **ISK Scout** was missing from it, **EVE Flipper** is further along than assumed (v1.6.14, Jul 2026 — VWAP depth walking, multi-hop route trading, contract arbitrage, paper backtesting), **eve-meta is defunct** and **evetrade.space has lapsed**. |
| D-34 | **§23.15's ≤7,000-line track budget is exceeded. Final: 8,953 lines** — 4,024 new core modules and page, 3,021 new tests, 1,908 added to existing files. Executable lines (excluding blanks, comments and docstrings) are 5,387. | Stated rather than trimmed. The track carried five separable concerns — the map and router, the depth reduction, the ranking engine, the liquidity and packing layer, and the freight comparison — plus a desk page and eleven test modules, and the last of those is where a third of the overage is. Two things were deliberately **not** done to make the number: no test was dropped, and no explanation of *why* a rule exists was cut, because this repository's whole defence against re-litigating settled decisions is that the reasoning is written down next to the code. §1's repo-wide budget stands for future work and this remains an authorized exception, not a new ceiling. |
| D-35 | **§23 remediation, 2026-08-25.** An adversarial first-build audit (Fable) reproduced twelve defects by running the code with concrete inputs; all twelve are fixed, fixture-first, one commit each. The five with ISK at stake: a losing round trip ranked as a plan because the marginal rule ran only from the **second** breakpoint (one ask of 100 @ 100 against one bid of 100 @ 50 ranked at **−51.7%** with zero rejections — and on a 98.8%-median-spread market that is most books, so the page would have filled with the least-bad losses and the honest zero become unreachable); an unknown packaged volume **skipped the cargo cap entirely** (a million units against a 60,000 m³ hold); the basket **double-spent measured depth** (one 1,000-unit ask sold to two hubs packed 2,000 units); liquidity **measured a dead market from year-old bars** via a `tail(window_days)` fallback; and the ledger **laundered `expected_cost_isk` into `actual_cost_isk`**, computing a "realized" net and a "forecast error" from the forecast and counting it as resolved evidence. | Every fix ships with a test that failed on the audited head. Two changed stated behaviour and are corrected **in place with the old wording visible**: §23.10 (the first chunk's marginal is its net) and §23.3 (`along_route` refuses without a destination). Two are diagnostics rather than behaviour: the avoid list is no longer reported as a security block, and an order beyond the search bound is out of reach rather than unplaceable. One is a quarantine with its own self-test: the reliability grade's weights are invented, so a test now fails if a grade reaches a comparison, a branch, a sort key or a filter anywhere under `src/`. **And one was a measurement, not a guess:** the scan was profiled rather than assumed — 18.8 s for one pair over 5,000 types, of which `curves_from_depth` was **18.4 s** and the ranking loop **0.4 s**. The index now sorts once and walks rows instead of paying pandas' per-group constant five thousand times: **18.8 s → 1.4 s**. |

### §0 named checks — status after this build

| Check | Status |
|---|---|
| #1 ESI `average` semantics | **OPEN.** Not resolved by this build. The bar contract tolerates either answer (§4) and nothing computed here depends on it, but it belongs in the contract doc and the Fuzzwork cross-check is a Phase-2-gate item still owed. |
| #2 page-snapshot consistency | **PARTIALLY ANSWERED 2026-08-20.** A full Forge sweep (415 pages, 414,152 orders) reconciled by `order_id` found **10 duplicates** (0.0024%) and produced **201 crossed books** out of 16,706 two-sided types (1.2%), where the bid printed above the ask. So the pages of one sweep are *not* a perfectly atomic snapshot, but the incoherence is small and bounded. Both are carried as data-quality counters, never as arbitrage. The deliberate two-sweeps-in-one-window diff is still owed. |
| #3 structure blind spot | **MEASURED 2026-08-20 — and the plan's assumption was backwards.** On a full Forge sweep: **0.0% of visible *ask* volume rests in player structures, and 22.0% of visible *bid* volume does** (verified independently against raw ESI pages: 0 structure sell orders in a 7,000-order sample, ~70% of buy volume in structures on those pages). §9 R3 worried that invisible structure orders would make public books *understate* depth. The measured exposure runs the other way and lands entirely on the **exit**: what the operator can buy is fully visible in NPC stations, but a fifth of the bid depth he would sell into may sit behind docking rights he does not have, so a naive bid-walk exit price is **optimistic**. `screen.py` flags this on the bid row and `paper.py` records `bid_station_volume_share` at entry. What remains owed is the operator checking whether he actually has access to those structures. |
| #4 outlier prints in `highest`/`lowest` | **ANSWERED 2026-08-20: CCP does not filter them, and the effect is severe.** Measured over 1,854,651 real Forge bars: `high/close` reaches **1,940,777×** and `close/low` reaches **12.8 billion×**; 0.24% of bars carry a high more than 10× the day's average and 1.78% carry a low below a tenth of it. The low side is worse than the high side (6.7% of bars have `close/low > 2` versus 3.1% for `high/close > 2`), which fits EVE: fat-fingering a sale at 0.01 ISK is a common mistake, buying at a million times fair is not. Consequence, measured on the 347 tracked types: winsorization clamps **7.9% of bars** and touches **79% of types**, and **without it 20.5% of types would carry a risk unit more than twice too large**, p99 143× too large, worst case 2,433× too large. Position sizing off raw `highest`/`lowest` would be wrong by orders of magnitude for a fifth of the universe. The §6 winsorization decision was made on suspicion and is now measured. |
| #5 relist/modify fee formula | **OPEN.** Modelled as `relist_surcharge_multiple` in config, defaulting to 1.0× the broker fee. Only the maker-exit branch depends on it, and that branch is advisory. |
| #6 sales tax / broker base rates | **OPEN.** 7.5% / 3% are config defaults, not constants; the operator's one-real-fill reconciliation is the gate that closes this. |

---


## 18. Operator workflow port — the desk surfaces (operator directive 2026-08-20, second)

The operator's follow-up directive: *"I want my TradingBotV3 moved to EVE with
relevant changes for the game vs real life."* The analytical core (§2's port
surface) landed in the v1 build; what was still missing was the **daily
workflow the operator actually lives in** on the source system — the Focus
lists, the per-symbol chart, and the strength board. This section adds their
text-mode ports and records the rule that keeps them inside this repo's
invariants.

### 18.1 The rule: observation is not opportunity

The honest-zero invariant (§5) governs the digest's candidate panel, and it
alone. The surfaces below deliberately show types that do **not** clear costs
— with their measured friction printed beside them — because the operator is
learning the EVE market and must see what the screen rejects and why. Nothing
below ranks on net edge, calls itself a pick, or nets a cost away. UNKNOWN
renders as a blank that sorts to the bottom, never as a zero, never as a
silently-priced number.

### 18.2 The surfaces (`src/evescreener/brief.py`)

| Surface | Source-repo ancestor | What it is |
|---|---|---|
| `watch add / remove / list` | Focus lists + candidate registry | Operator-owned names in `state.db`'s watchlist table. `add` resolves against the SDE loudly (never a guess); `remove` is the **only** removal path and only the operator reaches it — §11 D4's never-auto-removed invariant, now with a CLI. The §11 D4 seed list remains the config-seeded starting roster. |
| `brief --name X` | the per-symbol desk chart | One type fully read: anchored VWAP + σ zone, the four tri-state gates (PASS/FAIL/UNKNOWN), RRS vs the Forge Composite, participation, ATR and risk unit, nearby levels with conviction, destruction annotation, and the priced tiers — breakeven **and round-trip friction** per notional — with book freshness and flags. It ends by saying what it is not. |
| `board [--sort value\|strength\|change]` | the strength board | The tracked universe **plus watchlist names, floor or no floor**, as one cross-section: close, day move, dip σ, RRS, participation, friction at the smallest tier, setup marker, watchlist marker. Blanks sort to the bottom whichever way the board is sorted; the footer counts what was measured, what was UNKNOWN, and how many setups exist today. |
| Digest watchlist section | the Focus feed | Every watchlist name renders in **every** digest — cleared costs or not, resolved or not, bars or no bars — one compact line each. An unresolvable or bar-less name says so and says what to run; it never disappears. |

### 18.3 Deliberately NOT ported

The Qt desk (42k LOC — §2's lesson, learned once), M5/bounce anything, session
VWAP, auto-adoption and pick staging (nothing here adopts anything: every
watchlist entry is operator-typed), alert sounds, phone price alerts, and the
review-learning loop. The board and the brief are pull surfaces the operator
runs when looking; the only push channel remains the daily webhook (§11 D6).

---

## 19. The desk — indices, setups, and the learning loop (operator directives 2026-08-20, third and fourth)

The third directive: build the desk the operator actually works at, and make
the machinery measure **his** setups rather than argue with them. The fourth
amended the membership rule and required that every decision — taken *and*
passed — carry its reasons.

The hard line does not move. **No order execution and no client automation,
ever** (§10.1–.2). EVE has no order-entry API and automating the client is
bannable. "Execute" in this build means: run the operator's setups, chart
them, surface them, record his decision, and learn from the outcome. The paper
ledger and real-fill recording are the execution surface, and they are the
whole of it.

### 19.1 The index layer (`src/evescreener/indices.py`)

One engine (`signals/composite.py`) serves every index, so there is no second
construction path to drift.

| Index | What it is |
|---|---|
| **FORGE** | The market read. Members are the OK-tier, non-price-pinned types; weights are **median ISK turnover** with the §11 D3 single-name cap, chain-linked across monthly rebalances, base 1000. |
| **FORGE-EW** | Same membership, exactly, equal weights, same chain-link schedule. |
| **FORGE-EW − FORGE** | The **breadth** read, rendered wherever FORGE is. Positive means the average member is outrunning the turnover-weighted market. |
| Sector indices | From the committed, operator-editable `config/sectors.jsonl`: nine seeded sectors defined by market-group subtree roots read from the live SDE. Each may set its own `min_unit_volume`. |

Three things the module refuses to do:

* **Weight by raw unit volume.** "Weighted by daily volume" would mean units,
  and units make the index ~100% Tritanium — 5 billion units a day at 4 ISK.
  Turnover is the only common denominator across twelve orders of magnitude of
  unit price. **The unit floor decides who is IN; turnover decides how much
  they COUNT.**
* **Merge a thin sector.** A sector below its minimum member count renders
  UNKNOWN with its reason and its candidate count. Folding it into a
  neighbour would produce a number with no honest label.
* **Substitute a scope.** `sector_for_type` returns `None` — UNKNOWN — for a
  type it cannot resolve. It never falls back to the market index. Every RRS
  is reported against FORGE *and* against the type's own sector, and an
  unresolvable one says so.

Golden fixtures landed **before** anything consumed the new math (§11 D5),
including an adversarial composition-churn case: a member joining at bar 60
priced 1,000× the rest, with dominant turnover, leaves the chain-linked level
at exactly 1000.0 across all four rebalances. Composition churn is not an
index move. Diagnostics — members, top weight, weight entropy, rebalances —
render beside every index. The monthly MER cross-check is unchanged.

### 19.2 The desk (`src/evescreener/gui/`, PySide6)

Eight pages in the operator's priority order: **MARKET · CHARTS · BOARD ·
FOCUS · SCANNER · PAPER · LEARNING · HEALTH.**

**Qt is optional and the core proves it.** PySide6 is a `gui` extra;
`tests/test_headless.py` walks the import graph of `src/evescreener/**` and
fails if anything outside `gui/` can reach it, and a subprocess check asserts
that importing the CLI never puts PySide6 into `sys.modules`. §2's lesson —
the source repo's 42k-LOC Qt desk is what made it unmaintainable — is answered
structurally, not by abstention. The desk is 2,972 LOC.

**The refresh timer is safe by construction.** `gui/data.py` reads the Parquet
lake, the state database and the book snapshot; it has no ESI client and no
way to acquire one, and a test proves no module under `gui/` imports `httpx`,
`urllib` or anything named `esi`. A UI timer may re-read local data freely;
nothing on the desk can cause a fetch before `Expires` (§3.2). The desk
therefore **shows** staleness rather than curing it.

**The GUI thread never computes; it paints** (amended 2026-08-20, §17 D-24).
The original design built every page in `DeskWindow.__init__` and refreshed
all eight on a 60-second timer. On the operator's first real universe — 2,947
tracked types over 4,052,335 bars — that measured **217 seconds to open**,
145.9 s of it inside `ScannerPage` and 56.5 s inside `BoardPage`, on the
thread that draws the window; the timer then asked for all of it again every
60 s. The desk opened in 3.6 minutes and never became interactive. Four rules
now hold:

* **Pages are lazy.** `build()` lays out widgets and computes nothing. The
  window opens on MARKET and is interactive in seconds; BOARD, SCANNER and
  LEARNING compute on first visit and on an explicit refresh.
* **Heavy work runs off-thread.** A page declaring `heavy = True` splits into
  `compute(data)` (pure, worker thread, no Qt) and `paint(result)` (GUI
  thread). While work is in flight the page shows its **last completed
  result** under a `computing… (showing the HH:MM result)` stamp, and a
  failure keeps that result and says why — last-good-on-failure, never a
  blanked panel, because a blank reads as "nothing here".
* **Recomputation is keyed on inputs**, not on the clock: lake and book file
  stats plus the mtimes of `setups.jsonl`, `reasons.jsonl`, `sectors.jsonl`
  and `anchors.jsonl`. **Daily bars change once a day** — a 60-second full
  rescan was modelling the timer rather than the data. The timer now stats
  those files (~15 ms) and only pays for a reload when the key moves.
* **Workers open their own database connection.** sqlite3 connections belong
  to the thread that opened them.

Measured after: **8.6 s to interactive**, a 15 ms timer tick, and a revisit to
an already-computed page at **0.000 s**.

**Range candles.** The bar contract has no `open` and none is synthesized
(§4). Each candle is a filled body spanning the day's **low→high**, crossed by
a notch at the **average**, coloured against the previous average — body,
notch and colour are each a measured number or a comparison between two of
them. A conventional open→close body is refused **on measurement**: `close` is
the ESI daily mean, and yesterday's mean falls outside today's `[low, high]` on
**55.7%** of the lake and **69.0%** of tier-OK bars, so such a body would hang
off its own wick on the majority of bars (§17 D-30). Intraday direction is not
in this data at any price; the notch's height inside the body is the honest
substitute. The chart opens at **120 bars** with a 60/120/250/all selector and
degrades by slot width (body+notch → bare range → shaded envelope with a close
line) rather than smearing. Drawn on top: the frozen anchored-VWAP σ ladder,
configurable SMA/EMA overlays, the EMA cloud as a shaded two-EMA ribbon, and
the **high-volume levels, pivots and round-ISK levels that `levels.py` has
computed since Phase 2 and that nothing had ever drawn**. Volume and
participation subpanes; setup markers; open paper positions with their entry,
stop and target.

**One chart window, re-pointed.** Every page emits `chart_requested`; the
window aims the single panel. No page can open a second one.

**Blanks at the bottom whichever way a column sorts** (§18.1). This required
the table to order its own rows: Qt reverses its comparator for a descending
sort, so any comparator that keeps blanks last ascending puts them first
descending. Sorting is a pure view operation and never refetches.

**Focus never auto-removes** (§11 D4). The only removal path is a button
behind a confirm; a refresh, a pass or a floor change cannot reach it.

The backtest NOT-PLAUSIBLE banner sits at the top of MARKET and SCANNER in the
digest's **exact wording** — one function, because a banner phrased
differently in two places reads as two different findings. No sounds and no
urgency styling in v1.

Launch: `python -m evescreener gui`, or `launch_gui.py` for a Windows
shortcut.

### 19.3 The operator setup engine (`src/evescreener/setups.py`, `config/setups.jsonl`)

Setups are **data**, long-only, and validated loudly on load. Nine typed
condition kinds, all from daily high/low/close/volume/order_count — nothing
needs an `open`:

`price_vs_ma` · `cloud` · `ma_cross` · `band_zone` · `dip_sigma` · `rrs` ·
`participation` · `near_level` · `change`

* **An unknown condition kind, a misspelled parameter, a bad enum or an
  out-of-range value stops the load and names the file and line.** The failure
  this guards against is the expensive one: a DSL that ignores what it does
  not understand produces a setup that *looks tested* and is not.
* **Evaluation is tri-state.** Any UNKNOWN condition sinks the setup; each
  result carries the reason it came out as it did, so a setup that never fires
  can be debugged rather than guessed at.
* **A setup is UNVALIDATED** until it has a backtest read or ≥20 tagged closed
  trades. The label is information, not a lock — it still scans, charts and
  tags.

Every setup is evaluated by the scanner, drawable on charts, taggable on paper
trades, and runnable as `backtest --setup NAME` with the same cost realism,
horizons and limitations statement as the built-in rule. That required a
per-bar evaluator, pinned to the last-bar evaluator by a parametrised test
over every condition kind. `near_level` is refused over history rather than
approximated (§17 D-19).

SMA/EMA/cloud were new indicator code and got golden fixtures first (§11 D5),
including the seeding decision: an EMA is seeded on the **SMA of its first
`length` bars**, not on bar 1 as `pandas.ewm(adjust=True)` does, so "price
above the rising 21 EMA" cannot fire on bar 2.

Three example setups ship, all marked `"example": true`. Nothing shipped is
passed off as something shown to earn.

### 19.4 Qualified reasons and the learning loop (`config/reasons.jsonl`, `src/evescreener/learning.py`)

**Both directions of a decision are recorded with the same rigour.** An
opening requires a thesis, a setup tag and at least one *like* tag. A pass
(`not_today` / `bad_signal`) requires at least one *dislike* tag. **No tags,
no record** — and the refusal itself is written to the ledger, because a
decision the operator started and did not qualify is information too. A typo'd
tag is a loud error, not a dropped one: a decision recorded with a misspelled
reason is a reason that can never be measured.

`not_today` clears a name from today's queue only. It **never** touches Focus.

Per setup and per tag, calibrated through the vendored `expected_r` engine:
sample count, win rate with a **Wilson lower bound**, average and median net R,
expected R by **shrinkage toward a zero prior**, and freshness decay. Ranking
is evidence-weighted, so 3-for-3 cannot outrank 40-for-70, and every UNKNOWN
sorts below every measured setup. Below **20** closed trades — the same
threshold §12.4 draws — everything reads UNKNOWN, which is a statement about
the sample and not about the setups.

**Regret tracking.** Every recorded pass is measured forward on the backtest's
horizons with the backtest's cost realism: a pass is "right" only when the
avoided trade would have lost money net of entry haircut, exit haircut **and**
sales tax. Judging passes on gross moves would flatter every pass in a market
whose median spread is 98.8% — and flatter it in the wrong direction, since
the names that look best gross are usually the widest. A pass whose window has
not elapsed is pending; a pass on a type with no measurable haircut is
UNKNOWN, never scored as a good call.

The digest may name a best and worst setup, but only once the ledger has 20
closed trades.

**What the learning loop never does:** silently edit a setup definition,
change a frozen formula, or promote, demote or disable anything. It correlates
and reports; the operator promotes. A system that quietly retunes itself on 14
samples of its own output has a backtest that means nothing, because the thing
measured is no longer the thing running.

### 19.5 What is owed before any of this is trusted

The §17 checklist still stands in full, and this build adds to it rather than
replacing it. Everything in §19 is **IMPLEMENTED and GREEN offline**; nothing
here is LIVE_VALIDATED. In particular:

* FORGE and FORGE-EW have never been eyeballed against Adam4EVE or the MER.
* The sector membership has never been skimmed by a human for obvious
  misfiling.
* No setup has a backtest read or a single tagged closed trade, so **every
  setup on the LEARNING page is UNVALIDATED and every number on it is
  UNKNOWN** — correctly.
* The regret-tracking arithmetic is tested against synthetic ledgers only.

## §20 — The daily desk (added 2026-08-20, operator request)

The operator's stated loop: *"open it, walk the lists, chart each name, paper
trade the ones I like, tab out."* Eight rail pages made that a tour. §20
consolidates the review into one page and adds the four things the loop is
missing. **Setups are explicitly out of scope for now** — the action on every
surface is a paper trade, not a setup tag.

**Phase order, one per session, each gated green before the next starts.**

### §20.1 — DESK, the consolidated review page — **IMPLEMENTED + GREEN**

One page: source tabs on the left, the chart on the right, paper-trade from
the row. It **composes the real page classes** (`FocusPage`, `BoardPage`,
`ScannerPage`) rather than forking them, so there is no second watchlist
implementation to drift. The rail keeps every existing page (operator's
choice); DESK is added as the first entry, not a replacement.

**One chart survives.** The window now owns the single `ChartPanel` and
*moves* it into whichever visible page declares a `chart_slot`
(`DeskPage.dock_chart`). DESK and CHARTS therefore share one panel, one anchor
set and one set of overlays — §19 Part 2 page 2 is preserved literally, not
merely in spirit. A test asserts `findChildren(ChartPanel) == 1`.

Charting from inside DESK does not navigate away; charting from a page with no
slot (MARKET, PAPER) still jumps to CHARTS as before.

### §20.2 — SPREADS: maker / station trading — **IMPLEMENTED + GREEN**

**This inverts the sign of the measured friction finding, and that is the
reason to build it.** §17's NOT PLAUSIBLE verdict was measured on a *taker*
strategy: cross the spread in, cross it out, and 14.7% round-trip friction
eats a +2.80% gross edge. A maker does the opposite — posts a bid, posts an
ask, and **collects** that spread. The 98.8% median Forge spread that killed
the taker is the maker's revenue line.

The primitives already exist: `books.spread_view()` computes
`best_bid`/`best_ask`/`spread_pct`, and `CostModel.sell_proceeds(maker=...)`
already distinguishes posting from crossing. Round-trip maker cost at the
operator's skills is broker 1.300% in + broker 1.300% out + sales tax 3.375%
≈ **5.98%**.

What must NOT be waved through: a wide spread is not an edge if nothing fills.
The tab owes a fill-plausibility column (volume, `order_count`, depth at top
of book) and must render a stale book as UNKNOWN, never as a priced row.
Undercut risk is real and is **not** modelled by any existing measurement —
say so on the page rather than implying an edge the lake has not measured.

Landed as `spreads.py` (core, no Qt) plus the SPREADS page and a DESK tab,
with a hub dropdown covering every configured hub and an all-hubs entry.
The measurement that shaped it is §17 D-31.

**SETTINGS also landed here**, ahead of §20.5, at the operator's request: an
ntfy server/topic/token/priority form. It writes to the `meta` table of
`state.db`, **not** `config.toml` — that file is the hand-edited, comment-rich
contract of §11 D1, and no TOML *writer* exists among the four locked runtime
dependencies. Nothing is delivered yet and the page says so: nothing under
`gui/` may import an HTTP client, so evaluation and delivery remain §20.5 and
belong to the daemon.

### §20.3 — TOP PERFORMERS (1w / 1m) — **IMPLEMENTED + GREEN**

Resumed after §21 R8 and landed as `performers.py` (core, no Qt) plus the TOP
page and a DESK tab. Ranks the tracked universe by trailing return, badges
THIN, and reports UNKNOWN wherever a return is not defensible.

**Three amendments to the scope above, each with its reason. The original text
is preserved here:**

> *Original:* "Rank the tracked universe by return over **5 and 20** completed
> bars. UNKNOWN when fewer than the required bars exist; THIN badged, never
> silently mixed."

**1. The windows are 7 and 30 days, not 5 and 20 bars.** Five trading days is
a week only because an equity exchange shuts at the weekend. **EVE's market
never closes**, so five days is five days. Ranking on 5/20 would have measured
five days and labelled it a week — a habit ported from the source system
rather than a decision about this one (§6).

**2. The windows are calendar days, not bar counts.** A thin type trading on
the 22nd, 27th, 28th and 31st has bars that are not consecutive days, so
counting seven *rows* back spans nearly a month. This is the same defect §21
R5 fixed in the lead-lag study, found again here, and fixed the same way: the
window is a literal `day − N`.

**3. The ranked return is print-resistant, and the raw one is shown beside
it.** CCP does not filter outlier prints and `close` is the day's *mean*
transaction price, so one fat-fingered trade drags a whole bar. Measured on
the real Forge lake:

| | raw close-to-close | median-of-3-days |
|---|---|---|
| worst 7-day reading | **+49,699,900%** | removed |
| median difference from raw | — | **0.88 pp** |
| types reading >1000% | 39 (1.32%) | 23 (0.78%) |

*Batch Compressed Plagioclase II-Grade* is the worst case and shows the
mechanism exactly: a single order set the 2026-08-02 daily average to **0.01
ISK**, and that bar was the window's start point.

A median needs something to be a median *of*: an endpoint window holding one
bar offers no defence at all, which is why that name still read +49,699,900%
after the first fix. Fewer than **two** observations at an endpoint is now
UNKNOWN — the name has no defensible return over that window, and saying so is
the answer.

**No further threshold was invented.** Genuinely repriced names still read in
the thousands of percent. The page shows raw beside robust so the operator can
see the two disagree, rather than trusting a cutoff nobody derived (§21 R4).

**This ranks; it does not recommend.** The system's own measurement is that
continuation does not pay in an elastic-supply market (§6, §17), and the page
says so.

**Owed live gate (§20.3).** Chart the top five names and confirm against the
in-game market that the move is real rather than an artefact of thin trading;
confirm a THIN name is badged and a stale name reads UNKNOWN.

### §20.4 — REGIONS: cross-region hauling

Surfacing work, not new analysis: `crossregion.py` already nets real PushX
freight and sales tax across hub pairs, and measured 10 of 151,113 pairs
clearing at 0.25B, best +13.63%. The tab must carry the caveat the CLI
carries — **those are simultaneous snapshots for a haul that takes days** —
visibly, not in a footnote.

### §20.5 — ALERTS + ntfy

Rule store, evaluation on refresh and in the daemon, dedupe state so one
condition cannot spam, and ntfy HTTP delivery **alongside** the Discord
contract of §11. Extending the locked delivery decision is a plan-level edit
and is recorded here as one. `httpx` already covers the transport; no new
runtime dependency. Re-arm only after a condition clears.

## §21 — Remediation track (operator-authorized 2026-08-20)

**Why this exists.** An adversarial review of the repository found defects in
how the order book was reduced and how snapshots were validated. The operator
authorized this track to take priority over the queued §20.3 work. That
authorization explicitly does **not** permit weakening a hard invariant,
rewriting a historical result, changing the frozen AVWAP sigma formula, or
combining phases.

**Nothing here retracts a measurement.** Every previous result, rule and number
stays visible where it was recorded. Where corrected methodology changes a
result, the old result and the old rule remain, the amended method is added
with its reason, the measurement is regenerated, and the new output is
provenance-stamped. §17's deviation record is append-only.

**The principal finding.** The existing negative taker conclusion may remain
directionally correct, but several measurements were not audit-grade. The most
important defect: the reduction discarded station and buy-order range, then
treated a region-wide best bid and best ask as an executable round trip.
Independently, partial snapshots and stale or incomplete bars could enter
pricing and signal paths. These are correctness issues, not requests to make
the strategy look better.

**One phase per session, in this order. A later phase is never started because
it is adjacent.**

| phase | scope | state |
|---|---|---|
| **R1** | Executable order-book identity and validated snapshots | **IMPLEMENTED + GREEN** |
| **R2** | Completed-bar enforcement and independent bar freshness | **IMPLEMENTED + GREEN** |
| **R3** | Backtest price bounds, statistics and friction labels | **IMPLEMENTED + GREEN** |
| **R4** | Maker analysis and location-specific cost semantics | **IMPLEMENTED + GREEN** |
| **R5** | Killmail lead-lag hypothesis fidelity | **IMPLEMENTED + GREEN** |
| **R6** | Learning freshness and eligible-sample handling | **IMPLEMENTED + GREEN** |
| **R7** | Desk threading, invalidation and worker lifecycle | **IMPLEMENTED + GREEN** |
| **R8** | GUI network isolation, chart parity, regional data, stale docs | **IMPLEMENTED + GREEN** |

The consolidated live-validation checklist in `CURRENT_CHECKPOINT.md` is
untouched by this track and remains owed in full.

### §21 R1 — Executable order-book identity and validated snapshots — **IMPLEMENTED + GREEN**

**The contract repaired.** A spread is only a spread if one character could
have traded both sides. The old reduction grouped by `(type_id, side)` and kept
price and volume only, so the region-wide lowest ask — typically Jita 4-4 —
and the region-wide highest bid, which may rest at another station or inside an
Upwell structure the operator cannot dock at, were joined and called an
executable round trip. Maker spreads, screen pricing, paper fills, backtest
haircuts and cross-region analysis all consumed that reduction.

**What the reduction now preserves.** `location_id` on every order and `range`
on every buy order, and from them:

* `best_location_id` / `best_range` — where the region-wide extremum rests. The
  region-wide numbers are kept and relabelled as **diagnostics**, so the
  correction stays auditable and the two readings can be compared.
* `exec_location_id` — the one venue a round trip could happen at, chosen by
  `executable_venue()`.
* `exec_price` / `exec_volume` / `exec_order_count` — that side's quote **at
  that venue**.
* `exec_is_structure` — whether the venue is a player structure, because
  docking rights are not in the lake.

**The venue is anchored on the asks, deliberately.** A sell order is executable
only where it rests, so to buy at all the operator must dock where the asks
are; a bid may reach across the region. §17 measured ~0% of visible ask volume
in player structures against 8.8-98.3% of bid volume, so anchoring on asks
lands on a station the operator can almost always dock at. Among ask locations
the **busiest** wins — deliberately not the widest-spread one, because choosing
the venue that flatters the number is how a screen talks itself into a trade.

**Range semantics fail closed.** A bid at the venue is reachable whatever its
range. A remote bid is reachable only when its range is `region`, which needs
no topology. `solarsystem` and the numeric jump ranges *may* reach, but
deciding that needs station-to-system-to-jump data the reduction does not have,
so they are UNKNOWN and **UNKNOWN fails** (§4).

**Partial sweeps are diagnostics, never prices.** `sweep_region` now routes an
incomplete sweep to `BookLake.write_partial`, under a filename `latest()` does
not glob. `latest()` additionally returns the newest snapshot that is
*complete*, scanning back past partial ones — so a failed or partial refresh
cannot displace the last verified snapshot, and that guarantee is structural
for every consumer at once rather than a rule each caller must remember.

**One central validated-book contract.** `books.load_validated_book()` returns a
`BookSnapshot` that decides completeness, executability and staleness once, and
`snapshot.priceable` is empty unless all three hold. Warning flags were not
enough: a caller that *could* check `partial_sweep` is a caller that can forget
to, and the failure mode of forgetting is a confidently priced row.

**Consequence for existing data, stated plainly.** A snapshot written before R1
genuinely does not know where its quotes rested. It is therefore UNKNOWN and
prices nothing until the region is swept again — the operator's stored
35,858-row Forge book included. That is the honest reading of missing data, not
a regression.

**Owed live gate (R1).** After the next `sweep-books`, confirm against the
in-game market that `exec_location_id` for a handful of liquid types is the
station the operator actually trades at, and that a type whose best bid rests
in a structure is flagged rather than priced. No row produced by this phase has
been checked against a live client.

### §21 R2 — Completed bars at ingestion, and freshness that is not the book's — **IMPLEMENTED + GREEN**

**Two contracts repaired.**

**1. Only completed EVE days enter the lake.** `timeutil.last_completed_bar_date`
existed but was never applied in production ingestion, so `frame_from_history`
accepted every date ESI returned. A partial current-day bar — whose high, low
and average are all still moving — could confirm a signal. The rule is now
enforced at the single ESI-to-bar mapping site, so no caller can forget it, and
drops are counted in `frame.attrs["incomplete_dropped"]` rather than being
silent. The boundary is the 11:05 history roll, not midnight.

**2. Bar freshness and book freshness are separate facts.** `brief.freshness`
was `"fresh" if sell_row is not None and not stale_reason` — derived entirely
from the order book. A history job failing for a week while sweeps kept running
rendered a week-old signal as fresh. `bars.bar_freshness()` now judges the bars
on their own evidence, and a test asserts its source never mentions the book.

It measures two independent failures, because they are not the same question:

* **`bar_age_days`** — completed EVE days between the newest bar and today's.
* **`refresh_age_hours`** — time since ingestion last wrote. A lake whose
  history job stopped still holds a bar dated the day it stopped, so bar age
  alone cannot see the outage.

Either exceeding its budget is stale, and **stale downgrades every analytical
gate to UNKNOWN** (`TypeBrief.apply_bar_freshness`) — not to FAIL, because the
gate is unestablished rather than false. `TypeBrief` now carries
`bar_freshness`, `bar_stale_reason` and `bar_age_days` alongside the book's own
`freshness`, so a read can say "book fresh, bars stale" and be believed.

Budgets are config: `[screen].max_bar_age_days` (3) and
`max_refresh_age_hours` (36).

**A config-loading change was required and is recorded here.** `build_section`
rejected any section missing a key. Adding an optional setting would therefore
have broken the operator's existing `config.toml`. It now honours a field's
**declared default** and requires only fields that have none — drift still
fails loudly, optional settings no longer do.

**Owed live gate (R2).** Confirm on the operator's machine that a deliberately
skipped `ingest-history` run makes the screen report bars stale while the book
still reads fresh, and that gates go UNKNOWN rather than silently passing.

### §21 R3 — Price bounds, honest statistics, and friction that says what it is — **IMPLEMENTED + GREEN**

**No frozen verdict moved.** The golden fixture's haircuts (entry 0.015, exit
0.020) never reach the new clamp, so every previously measured value is
identical and the verdict is still **NOT PLAUSIBLE** at every cell. What
changed is what is *claimed*, not what was measured.

**1. Stress prices are bounded.** `exit_close * (1 - haircut * multiple)` goes
negative for a wide book: with bid 1, ask 99 and mid 50 the exit haircut is
~0.98, so 2x stress gave a factor of -0.96 — a sale realising negative ISK and
an unlevered long returning worse than -100%. `stress_factors()` clamps the
stressed haircut to 1.0, representing zero liquidity explicitly: the exit
realises nothing, the position is a total loss, and -100% is the floor.

**2. The Wilson bound is labelled as what it is.** `z = 1.96` is the two-sided
95% critical value, so as a one-sided lower bound it is a **97.5%** bound; the
prose called it 95% one-sided. **The number is unchanged** — moving it would
move a frozen verdict — and the label is corrected via
`wilson_one_sided_confidence()`. The error ran conservative: a 97.5% bound is
stricter than a 95% one, so a NOT PLAUSIBLE verdict cannot have been flattered
by it.

**3. Overlapping instances are no longer counted as independent.**
`effective_samples()` counts non-overlapping `horizon`-day blocks **per type**,
and `wilson_lb_clustered` is reported **beside** the naive `wilson_lb` rather
than replacing it. The correction is deliberately the crudest defensible one:
one that can be checked by hand is worth more than a tighter one that cannot.

**4. Friction reports its parts, and they compound.** One number was described
two ways — the round-trip haircut already contained sales tax while the control
text called 14.7% friction "before tax". `book_haircut_pct`, `sales_tax_pct`
and `total_friction_pct` are separate now, with
`total = 1 - (1 - book)(1 - tax)`, slightly *less* than the sum because tax is
levied on what the book already left. Reporting the sum would overstate the
cost of a strategy already judged NOT PLAUSIBLE.

**5. `max_drawdown_pct` is withdrawn.** It compounded *overlapping* trades in
date order with no portfolio or capital-allocation model. It was not a
drawdown, and the -100% readings at 2x and 3x are the artefact that gives it
away. The measured values are preserved in
`tests/fixtures/golden_signals.json` under `backtest_withdrawn_pre_r3` with
their reason, so no historical number is erased.

**Owed live gate (R3).** Re-run the backtest on the real lake: confirm the
verdict is unchanged, that `n_eff` is materially below `samples`, and that no
cell reports a net return below -100%.

### §21 R4 — A quoted margin is not an expected edge — **IMPLEMENTED + GREEN**

**No threshold moved and no count was regenerated.** Every number in §17 D-31
is reproducible and unchanged. What changed is what the numbers are *claimed*
to be.

**1. `net_pct` is now `quoted_margin_pct`.** "Net" promises that costs have
been netted out. The largest ones have not been: nothing in this lake models
queue position, fill probability, waiting time, undercut risk or relist fees.
The value is the margin the book is **quoting** between two resting orders,
minus the fees that are known. Every row now also carries
`execution_model = "none"` and a literal `unmodelled_costs` list, so an
omission can never be read as a modelled zero. The page header says "QUOTED
MARGIN, BEFORE EXECUTION RISK".

**2. The 0.5x / 2.0x guards are labelled operator heuristics.** §17 D-31 said
they were derived from measurement; that sentence is **corrected in place with
the original wording left visible**. Counting how many observations fall
beyond an already-chosen cutoff describes the cutoff, it does not derive it.
`spreads.GUARD_PROVENANCE` carries the correction in code.

**3. Broker fee is per station.** Standings are held per corporation, so the
rate differs between hubs owned by different NPC corps; one scalar priced
Amarr as if it were Jita. `CostModel.broker_fee_at(location_id)` and
`with_broker_overrides()` take **operator-observed effective rates** —
transcribed from what the client charged, never computed from standings the
system cannot read. With none configured the behaviour is byte-identical to
before. The maker margin now uses the rate at `exec_location_id`, which R1
made available.

**4. A stale traded average cannot bless a row.** The anchor is what makes
DUST_BID decidable, so a stale one decides nothing: `average_is_stale` yields
`STALE_AVG` and prices nothing.

**5. `relist_cost` is withdrawn.** It charged a broker fee on the *whole order
value*; EVE charges on the change between old and new price, so it overstated
a one-tick undercut by orders of magnitude. It is now
`relist_cost_unverified(old_price, new_price, quantity)` — the right shape,
but the exact terms and skill discount have never been checked against a live
client and plan.md §0 open check #5 remains open. **A test asserts no module
under `src/` consumes it**, because a wrong cost model is worse than an absent
one: it looks answered.

**Owed live gate (R4).** Transcribe the actual broker fee the client charges
at Jita 4-4 and at one secondary hub, and confirm `broker_fee_at` reproduces
them. Separately, verify the order-modification fee against the client before
anything is allowed to consume `relist_cost_unverified`.

### §21 R5 — The study must test the hypothesis that was frozen — **IMPLEMENTED + GREEN**

The method amendment is recorded at **§14.4**, beside the frozen §14.1–14.3
rather than over it. In summary:

* **Cohort.** H2 named doctrine-class hulls and fitted modules with a regional
  catchment; the run pooled the whole catalogue globally. Every result now
  carries a `cohort_declaration` with its population, catchment and evidence
  class, and the existing rho=0.027 result is labelled **exploratory**.
* **Lags.** `exact_lag_frame()` joins `day + k` literally, so a gap is UNKNOWN
  instead of the next observed row masquerading as a one-day lead.
* **Dependence.** `independent_observations()` counts types — the conservative
  floor — and is reported beside the raw row count.
* **Multiplicity.** Ten tests are declared, Bonferroni gives
  `FAMILY_ALPHA = 0.001`, and each lag row carries both the frozen verdict and
  the family-wise one.

**Nothing was retrofitted.** The frozen pass rule text is asserted unchanged by
a test, and no recorded result was regenerated — the confirmatory H2 run does
not exist yet and is owed.

### §21 R6 — Freshness must change the number that is ranked — **IMPLEMENTED + GREEN**

**The defect.** `learning.py` computed `freshness_factor(days_since_last)`,
stored it on the record, and then ranked on `expected_r`, which never saw it.
A setup last measured a year ago sorted exactly level with one measured
yesterday. The existing tests established only that the field *changed value* —
which is how a decorative number survives a test suite.

**One expected-R contract.** `effective_expected_r(expected_r, freshness)` is
the single definition, and it is what `rank_setups()` orders on. The raw blend
stays visible beside it so the decay can be audited rather than trusted. It
**scales** rather than penalises, so a negative expected R decays toward zero
rather than deeper: a stale loss is a less certain claim, not a larger one.
Either input missing is UNKNOWN, and UNKNOWN never reads as 1.0.

**The eligible denominator.** Shrinkage used `closed = len(rows)` — every
closed trade — while the mean R was computed only over rows that carry a
realized R. A setup with twenty closes and two scored outcomes was shrunk as
though it held twenty facts, understating the prior's pull exactly where the
evidence is thinnest. `eligible_outcomes()` counts outcomes that have an R, and
records report `eligible` beside `closed` so a reader can see the gap.

**Nothing was bought with authority.** The pre-existing invariants survive: an
UNKNOWN setup still never outranks a MEASURED one, small samples are still
ranked on their lower bound, and a test asserts the module still never writes
`setups.jsonl`, promotes, or mutates a setup.

**Owed live gate (§21 R6).** No setup has a backtest read or a single tagged
closed trade, so every LEARNING row is still UNVALIDATED and every number on it
UNKNOWN — correctly. The ranking change cannot be observed until real closed
trades exist.

### §21 R7 — The threading contract, held structurally — **IMPLEMENTED + GREEN**

Three defects of the same shape: a rule that held by convention.

**1. Widget reads happened off the GUI thread.** `SpreadsPage.compute()`
called `QComboBox.currentData()` on a worker. Qt widgets are not thread-safe
and the value can change mid-read. `DeskPage.job_input()` now captures every
widget-derived value into an **immutable tuple on the GUI thread**, immediately
before dispatch, and `compute()` reads it back through `self._running_input`.
A test walks the AST of every `compute()` under `gui/` and fails on any widget
access — a rule this easy to forget has to be structural, not remembered.

**2. An input change during a job was declined, then painted stale.**
`ensure_current()` returned early while a job was in flight, so the newer input
never got its own computation and the older result was painted over it. The
running input and the queued input are now tracked separately: a change during
a job is **remembered**, a result whose input has been superseded is
**discarded** rather than painted, and a follow-up computation is guaranteed.

**3. A worker could emit into a deleted page.** Closing the window mid-compute
delivered `finished` to a destroyed `QObject` — the
`RuntimeError: Signal source has been deleted` seen during teardown.
`PageJob.cancel()` makes a job emit nothing, checked both before the work
starts and again before the emit, because the page can go away during the
computation itself. `DeskPage.shutdown()` cancels and disconnects, and
`DeskWindow.closeEvent` shuts every page down before the widgets go.

**SQLite is unchanged and still correct**: `thread_local_db()` opens a
connection in the worker and closes it there, and a test asserts every
`compute()` that uses it does so inside a `with` block.

**Test-fixture consolidation.** The `desk` fixture and its lake/book helpers
moved from `test_gui.py` into `conftest.py`, so two test modules cannot drift
apart on what a `DeskData` looks like. The book helper now carries R1's
executable-identity columns.

**Owed live gate (§21 R7).** Open the desk against the real lake, switch the
SPREADS hub while a computation is running, and confirm the list that appears
matches the hub finally selected. Then close the window mid-compute and confirm
no `RuntimeError` reaches the console.

### §21 R8 — Isolation proved, parity restored, a retracted number removed — **IMPLEMENTED + GREEN**

**1. GUI isolation is now proved by the import graph.** The old guard walked
the AST for *direct* imports, so it could not see
`gui.pages.spreads` → `spreads` → `books` → `esi.client` → `httpx`. The
invariant was real; the check was one hop short.

`tests/_import_probe.py` imports **every** module under `gui/` in one cold
subprocess and asks `sys.modules` what actually loaded — the only check an
extra hop cannot fool. Three module-scope ESI imports moved into the one
function in each file that fetches (`books.sweep_region`,
`bars.ingest_history`, `universe.active_type_ids`); everything else in those
modules is pure analysis over a frame, which is why the desk imports them at
all. The AST guard stays as a fast first line.

**2. Chart parity.** `build_series` tailed the frame to `gui.chart_bars` and
*then* computed AVWAP and the overlays, so an anchor just outside the display
window produced bands that disagreed with the screen's. Everything is computed
on the full analytical history now; the canvas tails a **view** at paint time
through `ChartSeries.tail()`, which R2 already made slice every overlay in step.

**3. Regional data is keyed by region.** The desk loaded home-region bars while
SPREADS iterated every configured hub, so a second hub would have been judged
against Jita's traded averages. `DeskData.bars_for_region()` and
`last_close_by_region()` answer per region, and a region with nothing in the
lake returns empty — UNKNOWN, never another region's numbers standing in.

**4. `Expires` fails closed.** A missing or malformed header was treated as *no
active expiry*, which permits an immediate refetch — the precise behaviour the
never-fetch-before-expiry invariant exists to prevent, and circumventing it is
a bannable offence (§3.2). Unknown now means **wait**: an unparseable header
falls back to a feed TTL, and a malformed `Expires` on a **304** keeps the
previously stored valid expiry rather than clearing it.

**5. The retracted 16,789 is gone.** `esi/client.py` and `store/db.py` both
quoted "16,789 of 19,152 types 404" as fact. §17 D-10 **withdrew** that figure:
it was the D-12 circuit-breaker cascade — a bug in this repository — mistaken
for a property of ESI. Both now state the measured **241 of 17,325 (1.3%)** and
name the withdrawal, so the correction cannot be lost again.

**Owed live gate (§21 R8).** Open the desk against a second region's book once
one exists, and confirm its rows are priced against that region's own traded
averages. Separately, confirm against live ESI that a response with no
`Expires` results in a wait rather than an immediate refetch.

## §22 — Second remediation track (operator-authorized 2026-08-20, post-Sol)

**Why this exists.** An independent adversarial review (Sol) of the §21
remediation found defects in the remediation itself, plus older ones §21 did
not reach. The operator authorized this track. It is **append-only**: it does
not change a locked decision, the frozen AVWAP formula, or a frozen verdict
rule, and every historical claim it corrects keeps its original wording.

**One phase per session.** Every §21 owed live gate and the consolidated
live-validation checklist remain owed in full and are untouched by this track.

### Disposition of the review's findings

Reproduced from their concrete inputs before anything was written. A finding
is listed as **CONFIRMED** only where the wrong output was observed directly.

| id | finding | disposition | state |
|---|---|---|---|
| **S1** | `Expires` does not fail closed on the 304 and 200 production paths | **CONFIRMED** — 2 transport requests where 1 was correct | **IMPLEMENTED + GREEN** |
| **S2a** | Executable best quote carries **regional** depth, p5 and concentration | claimed; reproduction owed at phase start | **NEXT** |
| **S2b** | Production pricing bypasses `load_validated_book()` | claimed; reproduction owed at phase start | queued with S2a |
| **S4** | Pooled exploratory lead-lag rendered as if H2 had been tested | claimed; reproduction owed at phase start | queued |
| **S5a** | `friction_breakdown` returns 100% where 66.667% is correct | **CONFIRMED** — reported 100.0 vs 66.666667 | **IMPLEMENTED + GREEN** |
| **S3** | Worker reads page state; a same-input key change schedules no follow-up | **CONFIRMED** | **IMPLEMENTED + GREEN** |
| **S5b** | `effective_samples` global-origin binning overstates independence | **CONFIRMED** — returned 3 where at most 2 is supported | **IMPLEMENTED + GREEN** |
| **S5c** | Aging adverse evidence improves its rank | **CONFIRMED** — `-1R x 0.01` outranks `-0.1R x 1.0` | **IMPLEMENTED + GREEN** |
| **S5d** | A two-observation median is a mean and is ranked as print-resistant | **CONFIRMED** — `week_pct +99.98%` / `raw 0%` / state OK | **IMPLEMENTED + GREEN** |
| **S6** | `broker_fee_overrides` is always empty in production | **CONFIRMED** — `from_config` yields `{}` | **IMPLEMENTED + GREEN** |
| **S7** | Validation failures raise before any refusal is recorded | **CONFIRMED** at the `paper.PaperLedger` boundary | **IMPLEMENTED + GREEN** |
| **S8** | Import guard too narrow; TOP's aggregate figures are unversioned | **CONFIRMED** | **IMPLEMENTED + GREEN** |

**Nothing in §21 that the review found correct is being churned.** That list
is in `SOL_REVIEW_PROMPT.md` §"Findings that appeared correct" and includes
R2's 11:05 boundary, R3's stressed-exit clamp, R4's overclaim correction, R5's
calendar lag join, R6's eligible denominator, R7's cancellation and
last-good-on-failure, GUI network isolation, and TOP's 7/30 calendar windows.

### §22 S1 — `Expires` fails closed on every production path — **IMPLEMENTED + GREEN**

**Reproduced first, through real `EsiClient.get()` calls against a counting
transport.** R8's tests exercised `fallback_expiry()` in isolation, which is
exactly why they passed while the branches around it did not:

| | before | after |
|---|---|---|
| stored expiry after a malformed 304 at 12:01 | **12:00 — already past** | 13:01 |
| second call skipped as still-fresh | **no** | yes |
| **transport requests** | **2** | **1** |
| history 200 with no `Expires` | 300 s (borrowed from orders) | 83,100 s — the next 11:05 roll |

**The 304 defect.** R8 restored `db.expires_at(url)` when the header was
unusable. That value has *necessarily lapsed* — its lapsing is why the request
happened at all — so restoring it left a past timestamp and made the very next
call legal. Fetching before `Expires` is the one rule CCP bans accounts for.

**The 200 defect.** A single 300-second fallback was applied to every feed.
History rolls **once a day at 11:05 UTC**; a five-minute TTL would re-ask 288
times a day for a resource that changes once. A number lifted from one feed is
not a safe statement about another.

**No TTL is invented.** `unknown_expiry_boundary()` waits until the next moment
this system was going to ask anyway:

* **history** — the next 11:05 UTC roll (`timeutil.next_history_roll`), a
  measured property of the data rather than a preference;
* **orders** — the operator's own `[cadence].book_cold_interval_minutes`;
* **types** — the operator's own `[cadence].universe_refresh_utc`;
* **anything unmapped** — the longest of the three. Not knowing which feed this
  is, is a reason to wait longer, never shorter.

Waiting until the next scheduled run costs nothing that was going to be fetched
sooner, which is what makes it safe rather than merely cautious.

`safe_expiry()` additionally guarantees the result is **never at or before
now** and **never shorter than an expiry already trusted**. `EsiResponse` gains
`expiry_unknown`, so telemetry can show how often the server gave us nothing
rather than that fact being silent.

ETags, `last-modified`, pagination, budgets, the breaker and the error-limit
guard are unchanged, and tests assert each still works.

**Owed live gate (§22 S1).** Against live ESI, confirm that a real response
carrying no `Expires` results in a wait rather than an immediate refetch, and
that `expiry_unknown` is rare in the telemetry ledger — if it is common, the
header parsing is wrong rather than the server being silent.

### §22 S2 — Executable identity covers depth, and pricing uses the validator — **IMPLEMENTED + GREEN**

**S2a reproduced, verbatim from the review.** Venue A: ask 100 / 100 units, bid
90 / 100 units. Venue B: an unreachable ask at 1 / 99 units and a
station-ranged bid at 1,000 / 100 units. Tier notional 1,000 ISK.

| field | before | after |
|---|---|---|
| executable ask / bid | 100 / 90 | 100 / 90 |
| ask `depth_fill_price_0` | **9.258402** | 100.00 |
| bid `depth_fill_price_0` | **1,000.00** | 90.00 |
| ask `p5_price` | **1.00** | 100.00 |
| bid `p5_price` | **1,000.00** | 90.00 |

R1 made `exec_price` respect `reachable_from()` and left everything else
walking **region-wide** levels. Screen, paper and backtest all consume those
fields, so a row carried an executable ask of 100 beside an ask *fill* of 9.26
taken from a venue the operator cannot reach — physically impossible, and
optimistic on **both** sides at once.

Every executable field now derives from orders reachable at
`exec_location_id`: `p5_price`, `depth_fill_price_*`, the quantities,
`order_count` and `top_order_volume_share`. A concentration flag about orders
you cannot trade against was not a flag.

**The region-wide readings are preserved, not deleted**, under explicit
diagnostic names — `region_p5_price`, `region_depth_fill_price_*`,
`region_depth_fill_qty_*`, `region_top_order_volume_share` — so the correction
stays auditable and the two readings can be compared.

**Accessibility is reachability, not NPC ownership.** `station_volume_share`
answered "is this depth in an NPC station", which is the wrong question: CCP
matches a buy order by its **range from its own location**, so a
station-ranged bid at another NPC station is unreachable however NPC-owned it
is, while a region-ranged bid inside an Upwell structure *is* reachable because
the seller never docks there. `exec_reachable_volume_share` measures the right
thing and `screen.py` and `brief.py` flag on it; `station_volume_share` remains
as a diagnostic. `reachable_from()` is **unchanged** — `solarsystem` and the
numeric jump ranges still fail closed, which is correct until the topology
exists.

**S2b reproduced.** `load_validated_book()` correctly rejects the operator's
stored pre-R1 snapshot, and `paper.book_quote` priced off it anyway:

| | before | after |
|---|---|---|
| `paper.book_quote` on a pre-R1 frame | `price=9.2584, stale=False` | `price=None, stale=True` |
| reason | *(none)* | "book predates the executable-quote contract … re-run sweep-books" |

The guard is at the pricing boundary rather than being another warning flag:
`paper.book_quote`, `books.spread_view` and `backtest.measure_haircuts` each
refuse a frame missing `EXECUTABLE_COLUMNS`, and a parametrised test asserts
all three refuse the current pre-R1 schema.

**Owed live gate (§22 S2).** After the next `sweep-books`, confirm on a liquid
type that `depth_fill_price_0` is consistent with `exec_price` (a buy fill at
or above the executable ask, a sell fill at or below the executable bid), and
that `exec_reachable_volume_share` is high at Jita 4-4 and visibly lower for a
type whose bids sit elsewhere.

### §22 S4 — H2 is UNKNOWN, and every renderer must say so — **IMPLEMENTED + GREEN**

**Reproduced.** R5 made the *payload* honest — it carries a
`cohort_declaration` stating the pooled catalogue-wide run is exploratory and
not evidence about H2 — and then every renderer discarded it and printed **"the
lead-lag claim was tested and not supported"**. That sentence asserts a test of
H2. No such test exists. `brief.py` was worse: it printed it whenever
`destruction_z` was merely present, with no lead-lag payload involved.

`h2_statement()` returns the honest pair — **`H2 UNKNOWN — confirmatory run
absent`**, plus the exploratory finding beside it, labelled with its cohort. An
H2 verdict is available **only** from a declared doctrine cohort, and a payload
carrying no declaration **fails closed**: it cannot be shown to be
confirmatory, so it is not treated as such.

| surface | before | after |
|---|---|---|
| digest | "DOES NOT SURVIVE" + "tested and not supported" | "H2 UNKNOWN — confirmatory run absent", the pooled run labelled exploratory, "annotation only" |
| brief | "the lead-lag claim was tested and not supported" | "a brief carries no lead-lag study, and H2 has no confirmatory run" |
| `evaluate_lead_lag` consequence | asserts a test of H2 | names the pooled cohort and points at §14.4 |

**The dependence correction is no longer decorative.** R5 measured
`independent_observations()` and nothing read it: `spearman()` still produced
`z = rho * sqrt(n - 1)`, which treats ~470,000 serially- and
cross-sectionally-dependent rows as independent, and Bonferroni was applied to
*that*. `rotation_permutation_p()` rotates each type's series by a random
offset — destroying the alignment between destruction and returns while
preserving each series' own autocorrelation **exactly** — and reports an
empirical p-value bounded below by `1/(permutations+1)`, because an empirical
test cannot prove more than its own resolution.

The **frozen §14.3 rule is still applied to the naive p-value**, unchanged and
not retrofitted. The **family-wise** verdict now uses the cluster-aware
p-value, because Bonferroni over p-values that already assume independence
corrects the wrong error. Every lag row carries both, plus
`p_value_assumes_independence`.

**No confirmatory run was created or claimed.** The doctrine cohort remains
declared and unmeasured.

**Owed live gate (§22 S4).** Re-run the lead-lag study on the real lake and
record how far the permutation p-value sits from the naive one. If they agree
closely, the dependence is weaker than assumed — itself a finding.

### §22 S5a — Friction is a ratio of the gross move, not a sum of two costs — **IMPLEMENTED + GREEN**

**Reproduced.** entry close 100 → effective 150, exit close 100 → effective 50,
tax 0:

| | before | after |
|---|---|---|
| `total_friction_pct` | **100.0%** | **66.666667%** |

R3 computed friction as `entry_cost + exit_cost`, two one-sided percentages
added together. That says "the whole move is friction" for a round trip that
actually kept a third of it, and the error grows with the size of the moves —
the sum can exceed 100% and imply a loss larger than the position.

Friction is what the round trip **keeps of the gross move**, so it is a ratio
of ratios:

```
book = 1 - (pre_tax_exit / exit_close) / (entry_effective / entry_close)
total = 1 - (1 - book) * (1 - tax)
```

Tax still compounds rather than adds, because it is levied on what the book
already left. Both the scalar helper and the aggregate statistic use the same
per-row form — applied *before* the mean rather than after.

**The R3 regression test asserted the wrong formula and is replaced.** New
cases pin the reproduction itself, that friction can never exceed the whole
position however wide the book, and that a frictionless round trip costs only
the tax.

**The frozen §13.6 verdict rule is untouched and the verdict does not move.**
The golden fixture was regenerated *after* the corrected case existed; every
cell is still **NOT PLAUSIBLE**. Its friction figures shifted slightly — at 2x,
book 7.00% → 6.80% and total 10.14% → 9.94% — because the additive error only
bites at large moves and the golden haircuts are small. The pre-correction
values remain in git history; no stored report was rewritten.

**Owed live gate (§22 S5a).** Re-run the backtest on the real lake and compare
the reported `total_friction_pct` against the headline 14.7% recorded in §17.
If the corrected figure differs materially, §17's number is a historical
snapshot of the old formula and must be labelled as one rather than replaced.

### §22 S3 — A generation, not a widget tuple — **IMPLEMENTED + GREEN**

**Two defects, both from R7 solving half the problem.**

**1. The worker still read the page.** R7 passed `job_input` to the job and
then had `SpreadsPage.compute()` read `self._running_input` back off the
**page**, on a worker thread. `Generation` is now frozen before the job leaves
the GUI thread — token, input key, data and widget input together — and
`compute(data, job_input)` receives everything as arguments. The worker never
reaches back.

**2. A data-only refresh was silently dropped.** R7 queued *only* the widget
tuple, so:

> a key-1 job is running with widget tuple A; the lake moves to key 2 while no
> control is touched; the queued tuple is still A; A == A, so nothing is
> queued, the key-1 result paints, and **no follow-up is scheduled**.

The desk kept showing key-1 data with no indication. The owed generation now
carries the key and the data too, so a data-only move is a different
generation, and `_run_owed()` runs it **unconditionally** — including after a
*failed* job, which previously stranded the owed work.

**The guard is stronger than the defect.** Beyond the existing widget-access
check, tests now fail on any `self._running*` / `self._owed` / `self.data` read
inside a `compute()`, and on any `compute()` that omits the `job_input`
parameter — a page that forgot it would silently fall back to page state.

Cancellation, off-thread execution and last-good-on-failure are unchanged and
still tested.

**Owed live gate (§22 S3).** With the desk open, switch the SPREADS hub while a
computation is running and confirm the list that settles matches the hub
finally selected; then let the refresh timer fire mid-computation and confirm
the page ends up showing the newer data rather than the older.

### §22 S5b — Effective samples select a real non-overlapping subset — **IMPLEMENTED + GREEN**

**Reproduced.** Type 2 on 1 January; type 1 on the 10th and 11th; horizon 10.
The two type-1 windows share **nine of their ten days**, yet R3 answered **3**.

| | before | after |
|---|---|---|
| `effective_samples` | **3** | **2** |

R3 binned dates against a **global origin**, which calls two windows
independent whenever they fall in different bins — a bin *edge* between the
10th and 11th manufactured independence that is not there.
`non_overlapping_subset()` selects rows instead: walk each type's own dates in
order and keep an instance only when it starts at least `horizon` days after
the last kept one. Greedy, deterministic, and what comes back is an actual set
of rows whose forward windows provably do not overlap.

**Wins are counted in that subset.** R3 reconstructed them as
`round(win_rate * n_eff)`, which re-imports the very dependence the correction
exists to remove.

**Cross-type dependence remains unmodelled and is now stated as such.** Two
types moving together on the same day still count as two observations;
correcting that needs a market-factor model this system does not have.

**Owed live gate (§22 S5b).** Re-run the backtest on the real lake and record
`n_eff` against `samples`. If the ratio is far from `1/horizon`, the instance
set is more clustered than the crude correction assumes.

### §22 S5c — Aging adverse evidence must not improve its rank — **IMPLEMENTED + GREEN**

**Reproduced.**

| setup | expected R | freshness | effective (before) | rank (before) |
|---|---|---|---|---|
| stale severe | −1.0R | 0.01 | **−0.01R** | **1st** |
| fresh mild | −0.1R | 1.00 | −0.10R | 2nd |

R6 multiplied, which is right for a gain and backwards for a loss: a severe
loss that had gone stale sorted **above** a mild one measured yesterday. Decay
moves an estimate toward the 0R prior, so it shrinks a gain and must not shrink
a loss — **a stale loss is not evidence of a smaller loss**. A positive expected
R still decays toward zero; a negative one is held at its measured value, and
`fresh-mild` now ranks first.

**No staleness cliff was invented.** `freshness_factor` is bounded to
[0.4, 1.0] by construction, so no point in its range means "this carries no
information"; a cutoff there would be exactly the unmeasured threshold §22 S4
removed elsewhere. An initial 0.5 floor was tried, marked every setup older
than about eight days UNKNOWN, and was withdrawn for that reason. Small-sample
scepticism stays in `MIN_SAMPLES_FOR_A_READ` and the Wilson lower bound, where
it belongs. Raw and adjusted values both remain on the record.

**Owed live gate (§22 S5c).** Unobservable until real closed trades exist —
every LEARNING row is still UNVALIDATED.

### §22 S5d — A median of two is a mean — **IMPLEMENTED + GREEN**

**Reproduced.** Aug 10 = 0.01, Aug 12/17/19 = 100. Both raw seven-day endpoints
are 100, so the raw return is 0%.

| | before | after |
|---|---|---|
| ranked `week_pct` | **+99.980002%** | **UNKNOWN** |
| `week_pct_raw` | 0% | 0% |
| `state` | **OK** | UNKNOWN |

§20.3 required **two** observations at each endpoint and called the result
print-resistant. The median of two values is their arithmetic **mean**, which
one 0.01 ISK print drags almost as far as it drags the raw number — so the
table sorted on a "robust" value that was not robust, and showing the raw
number beside it does not help when the ranking uses the false one.
`MIN_ENDPOINT_BARS` is **3**, the smallest window in which a single bad print
is outvoted.

**Measured cost on the real Forge lake** (2,947 tracked names, 100-unit floor,
2026-08-20):

| | 2 observations | 3 observations |
|---|---|---|
| OK | 2,740 | **2,583** |
| UNKNOWN | 109 | **266** |
| THIN and OK | 1,207 | **1,168** |
| worst week reading | 85,069% | 85,069% |

157 names (5.7% of OK, 39 of them THIN) become UNKNOWN. The worst reading is
unchanged, which confirms the remaining extremes have three real observations
at each end — they are sustained repricings, not prints.

Calendar-day 7/30 windows are unchanged.

**Owed live gate (§22 S5d).** Chart five of the newly-UNKNOWN names and confirm
they genuinely trade too sparsely to carry a weekly return, rather than the
minimum being too strict.

### §22 S6 — Broker overrides reach production — **IMPLEMENTED + GREEN**

**Reproduced.** `CostModel.from_config(...).broker_fee_overrides` was `{}` for
every config, always. `with_broker_overrides()` existed and worked, and nothing
in production called it — `maker_spreads()` used the untuned model — so R4's
per-station broker fee could not affect a single production number. The R4 test
constructed the model by hand, which proves the arithmetic and nothing about
whether the feature is reachable.

`[costs].broker_fee_overrides` is now an optional list of
`{ location_id, broker_fee_pct }`, loaded by `from_config()` and therefore
reaching `maker_spreads()`. It is **operator-observed**: transcribed from what
the client actually charged, never derived from standings this system cannot
read. With none configured the behaviour is byte-identical to before, and an
unlisted station still uses the skill-derived base. The new test drives two
stations at 0.10% and 5.00% **through `maker_spreads()`**.

**Owed live gate (§22 S6) — unchanged and still owed.** The operator must
transcribe the actual in-client broker fee at Jita 4-4 and one secondary hub;
until then the list is empty and every hub is priced at the base rate.

### §22 S7 — A refusal is a record — **IMPLEMENTED + GREEN**

**Reproduced.** §19.4 requires that the refusal itself go in the ledger.
`record_pass()` raised `Refusal` for an invalid action, and `_clean_tags`
raised `ReasonError` for an unknown tag, **before** `_refuse()` was reached —
so neither left any record. The one class of decision the ledger silently lost
was the one made wrongly, which is precisely the class worth keeping.

Both now route through `_refuse()`, so the append-only ledger records the
attempted action and the attempted tags with their reason. **The decision is
still refused and the unknown tag is still never accepted** — tests assert the
exception, the record, and that no `pass` event is written from a refused
decision.

**Owed live gate (§22 S7).** Attempt one bad pass on the real desk and confirm
the refusal appears in `paper.jsonl` with the tags that were attempted.

### §22 S8 — A wider guard, and numbers that can be re-derived — **IMPLEMENTED + GREEN**

**The import guard was two exact names.** R8's probe rejected `httpx` and
`evescreener.esi.client` only, so a GUI module could have reached the network
through `requests`, `urllib.request`, `urllib3`, `aiohttp`, or **any ESI module
other than `client`**, and the guard would have passed. It now rejects those by
name, and any module with an `esi` path component — so a future
`evescreener.esi.anything` cannot slip in under a new name. Detection is still
transitive, in a cold subprocess, across every GUI module.

`socket`, `ssl` and `http.client` are **deliberately allowed** and the probe
says so: Qt and the standard library load them on import regardless of what
this package does, so flagging them would fail always and prove nothing. The
list is the set of clients our own code would have to *choose*, which is what
the invariant is actually about.

**A number in prose is not a measurement.** §20.3 and `performers.py` quoted
"2,944 tracked types", "0.88 pp median difference", "39/23 readings above
1000%" and a worst raw reading of "49,699,900%" with no as-of date, no
membership definition, no denominators and no way to re-run them. An
independent reproduction disagreed with all of them; a third run disagreed
again. **None of the three can be shown right or wrong**, because not one
recorded what it measured. The differing numbers are the symptom; the missing
provenance is the defect.

`provenance.py` emits a `MeasurementReport` carrying the as-of timestamp, the
membership sentence, every filter, the identity of each input file, the
**denominator beside every count**, the command, and the git revision.
`performers.measure_top_performers()` produces the TOP figures through it.

Two details that keep it honest:

* **A magnitude gets no share.** "worst reading / population size" is not a
  number about anything, so `is_count` gates the share rather than dividing
  everything by the denominator.
* **The file identity says what it is not.** Hashing multi-gigabyte Parquet
  would take minutes, so the digest is over `(name, size, mtime_ns)` — enough
  to notice the inputs changed, and explicitly **not** a claim that equal
  digests mean equal bytes.

**The old figures are labelled a historical snapshot and left in place.** Their
inputs cannot be recovered, so replacing them with a newer set would repeat the
mistake with fresher numbers.

**Owed live gate (§22 S8).** Run the TOP measurement report on the operator's
lake and commit its output under `data/reports/`, so §20.3's prose can cite a
dated artefact instead of a floating number.

## §23 — The personalized HAULING tab (operator-authorized 2026-08-25)

**Why this exists.** Every surface this system has built so far answers "is
this item mispriced?". None of them answers the question the operator actually
asks when he undocks: *given where I am, what I fly, what ISK I have and how
long I have got, what should I put in the hold right now?* That question has a
different shape. It is personal (position, ship, capital, session length),
spatial (routes, jumps, security), and it is decided at a **quantity**, not at
a notional tier — the right size is a breakpoint in two order books, not a
number chosen in advance.

This track builds that tab. It is **additive**: no existing page, CLI output,
formula, verdict rule or data contract changes, and every invariant in
`CLAUDE.md` and §10 holds without exception.

### §23.1 What it is, and what it is not

| It is | It is not |
|---|---|
| A decision-support screen ranking (item, source, destination, quantity) plans against the operator's own constraints | An autopilot, a route-runner, or anything that touches the EVE client (§10.1–.2) |
| A reader of the **local** depth lake, the local SDE graph and the local bar lake | A second ESI consumer. The GUI page still cannot fetch, and nothing under `gui/` may import an ESI client, `httpx` or `urllib` |
| Honest about what it cannot measure — fill probability, competitors, the destination price days from now | A promise that the ranked row is money |

**The measured reality this page lives inside, stated up front.** The Forge's
median spread is **98.8%** and only ~932 types trade inside a 5% spread at all;
of 151,113 hub pairs measured at 0.25B in §17, **10–14** cleared real freight
and tax. A hauling scan whose normal output is a short list or an honest zero
is the machinery working, not failing.

### §23.2 The competitive landscape — AMENDED 2026-08-25 (adversarial review)

The original directive named EVE Flipper, EVE Profits, eve-meta and
evetrade.space as the comparison set. That set was checked against the live
sites on 2026-08-25 and is corrected here; the original naming is left visible
in this sentence rather than deleted.

| Tool | State (checked 2026-08-25) | What it already does that this tab must not pretend to invent |
|---|---|---|
| **EVE Flipper** | **live, v1.6.14 (Jul 2026)** — first tier | VWAP **depth walking**, **multi-hop route trading**, **contract arbitrage**, **paper backtesting**. This is the closest thing to the tab being built and it is further along than the directive assumed |
| **EVE Profits** | live — first tier | Station and region trading screens, margin/volume filters |
| **ISK Scout** (`iskscout.com`) | **live — first tier, added by this amendment** | Hauling-shaped route/profit screening. Absent from the original directive entirely |
| **Trading Matrix** | live | **Free tier is Jita-only**; multi-hub work is paid. Relevant to what a free alternative actually gives the operator |
| eve-meta | **defunct** | — |
| evetrade.space | **domain lapsed** | — |

**Consequence for H0.** The benchmark set is **EVE Flipper + EVE Profits +
ISK Scout**, and H0 **moves after the two-week shadow period** (§23.15). It
converts from a build/don't-build gate into a **keep/park** gate: the honest
comparison is between this tab's *measured* behaviour on the operator's own
data and what the competitors show, not between a specification and a
screenshot.

### §23.3 The operator's inputs — what "personalized" means

A profile, not a preference file:

| Input | Why it changes the answer |
|---|---|
| current system | Pickup jumps are a real cost and they are not the same for everyone |
| intended destination (**required in `along_route`**) | In `along_route` mode only the **incremental** detour is charged. *(Corrected 2026-08-25: the mode with no destination used to fall through to dedicated charging in silence. There is no increment without a trip to be incremental to, so the profile now refuses to construct; the desk page, whose control strip can always be half-filled, falls back to dedicated and says so on screen.)* |
| ship: usable cargo m³, EHP, hull value, seconds/jump, handling minutes | Cargo caps the quantity; the rest price the time and state the exposure |
| capital, max exposure | The breakpoint that maximizes profit is usually not the one he can afford |
| session minutes, max wait days | A plan that does not fit the evening is not a plan |
| security profile, max jumps, avoid list | A route through nullsec is a different trade, not a cheaper one |

### §23.4 What already exists and is reused unchanged

`books.reduce_orders` and `book_summary` (byte-identical — a regression test
proves it), `CostModel` including `broker_fee_at` (§21 R4) and
`broker_fee_overrides` (§22 S6), `load_validated_book`'s staleness semantics,
`BookLake`'s atomic-write and complete-only `latest()`, the reason vocabulary
and the §19.4 refusal pattern, `crossregion.quote_freight` (H4), the desk's
`DeskPage` compute/paint contract, `SortableTable`'s blanks-last idiom.

### §23.5 Costs — and one formula that is recorded but quarantined

Fees are the existing model and nothing here re-derives them:

* **broker fee** = 3% − 0.3%/Broker Relations level − 0.03%/faction standing −
  0.02%/corp standing, floored at 1%; **a taker order pays no broker fee**;
  Broker Relations does not apply to orders in Upwell structures;
* **sales tax** 7.5% base → 3.37% at Accounting V;
* **relist fee** = `max(0, BR·(P2−P1)) + (1−RD)·BR·P2`, where BR is the broker
  rate and RD the Advanced Broker Relations discount.

**The relist formula closes the *shape* of §0 open check #5 and nothing more.**
It is recorded here so the shape stops being guessed at; it has **not** been
verified in-client, so `costs.relist_cost_unverified` stays quarantined from
every analytical path exactly as §21 R4 left it, and a test still asserts no
module under `src/` consumes it. A verified-looking formula that has never met
a wallet is the failure mode §21 R4 exists to prevent.

**One book generation per region, and both are pinned on every row.** A hauling
row joins two regions' books. Those are two independent sweeps with two
independent ages, so every row carries **both** `generation_id`s and both ages,
and the **older** one decides staleness. A single "book age" on a two-region
row would be a number describing neither leg.

**One ISK-day metric, not two.** The original directive carried both a
`capital_turnover` figure and an ISK-per-capital-day figure, which are the same
statement twice. They are merged into **`isk_per_capital_day`** =
`net_profit / (capital_committed × liquidation_days)`, and in `immediate` exit
mode the denominator's days are the **travel time**, because that is the whole
period the ISK is committed for.

### §23.6 Depth — the reduction this track adds

`book_summary` answers "what is the executable quote for this type in this
region". A hauling plan needs "what does it cost to buy 1,200 of these at
*this station*, and what do 1,200 fetch at *that* one". That is a **price-level
curve per execution station**, which nothing in the lake carries. `reduce_depth`
adds it, reading the **same in-memory pages** as `reduce_orders` in one pass:
no extra ESI traffic, no cadence change, no new feed.

**`generation_id ≡ (region_id, sweep_ts)`** — identical to `book_summary`'s, so
a depth row and a book row from one sweep are provably the same generation and
a row can name the sweep it came from.

**The reachability doctrine (buy side).** CCP matches a buy order by its
**range from the order's own location**, and the seller transacts from the
station he is standing in. A bid is therefore executable from the operator's
station iff **any** of:

1. it rests at that station; or
2. its range is `region`; or
3. its range is `solarsystem` and its system **is** the station's system; or
4. its range is numeric and the **stargate-graph jump distance** between its
   system and the station's system is ≤ that number.

Anything unresolvable — unknown system, missing or unrecognised range, a jump
distance the graph cannot answer — is **excluded and counted**. UNKNOWN fails
(§4). Rule 4 is the one R1 could not evaluate and left failing closed; the SDE
graph built in H1a is what makes it decidable, and the `station` range at a
*different* station remains correctly unreachable.

**The `min_volume` rule (conservative v1, recorded as such).** A buy order with
`min_volume > 1` will not accept a smaller parcel. Rather than model the
packing problem — which interacts with every other level in the walk — such an
order is **excluded from executable levels** and its volume accumulated into a
per-level `min_volume_excluded_qty` diagnostic, so the depth it represents is
visible rather than silently gone. This under-states reachable exit depth by
construction, which is the safe direction, and it is a v1 simplification rather
than a claim about the game. Sell orders carry `min_volume = 1` by
construction and need no rule.

**Aggregation and truncation.** Identical prices aggregate **after** the
filters above, keeping `level_order_count`, `oldest_issued`, `newest_issued`
and the structure-resident share of that level's volume. Levels are persisted
best-first only until cumulative notional ≥ `max_scan_capital_isk ×
depth_safety_margin` **and** cumulative volume ≥ the largest configured cargo ×
the same margin; a curve cut short sets `depth_complete = False`, and a walk
that reaches that boundary is **UNKNOWN, never extrapolated**.

**Order age is what it is.** `oldest_issued`/`newest_issued` are the `issued`
timestamps ESI publishes. Whether `issued` updates when an order is repriced
is **unverified in either direction**, so the page labels the column "last
placed **or repriced** (unverified)" and never treats it as order age.

### §23.7 Liquidity, liquidation, and three labelled assumptions

Getting in is measured; getting out is **assumed**, and the page never blurs
the two. Per (type, destination region) over **completed bars only**:

`liquidation_days(q, scenario) = q / (quantile_units × destination_share_prior
× capture_share_{low,base,high})`

* **`quantile_units`** is measured from the bars (low/base/high quantiles of
  daily units, default 0.25/0.5/0.75).
* **`destination_share_prior`** (default 0.25) is a **labelled operator prior**
  — the share of a region's traded volume that happens at the destination hub.
  It is not measurable from regional history, which carries no station split.
  It stays a prior, displayed as an ASSUMPTION, **until the operator's own
  recorded fills can replace it**, at which point it becomes a measurement with
  a sample size.
* **`capture_share`** (0.05 / 0.15 / 0.35) is the share of that flow the
  operator's own order wins. Also an assumption, also displayed.

**A zero or unmeasurable quantile makes liquidation UNKNOWN**, and an UNKNOWN
liquidation **fails every maker-mode cap** rather than defaulting to a fast
one. Fewer than `min_liquidity_bars` completed bars does the same. A dead
market does not become tradeable by dividing by a small number. **Bars outside
the window are not evidence about the window** — *(corrected 2026-08-25: a
fallback to the last N rows measured a market that had not traded for a year
and reported 500 units a day with an empty reason, feeding the maker caps, the
drawer and the reliability grade)*.

**The paper-haul ledger is the path by which these priors become measurements,
so it never launders a forecast into an actual.** A close is *resolved* only
when proceeds **and** cost are both what the operator really paid; proceeds
alone yields a labelled `assumed_net_isk` while `realized_net_isk` and the
forecast error stay UNKNOWN. *(Corrected 2026-08-25: the close borrowed
`expected_cost_isk`, stored it as `actual_cost_isk`, and computed a "realized"
net and a "forecast error" from it — the forecast grading its own homework, and
counted as evidence.)*

### §23.8 Routes

A local graph over `mapStargates` edges, built once from SQLite. Profiles:
**shortest** (BFS), **safer** (Dijkstra with a configured penalty on edges
entering non-highsec), **highsec-only** (the graph restricted before the
search), plus an operator avoid-list applied by removing systems before the
search. `RouteFacts` carries the ordered system list, jumps, minimum displayed
security, the counts of nullsec / lowsec / exactly-0.5 systems, the profile
used and the `sde_build` it was computed from. **No route is UNKNOWN**, never a
guess and never a straight-line estimate. The same jump-distance function
resolves numeric order ranges (§23.6 rule 4), so reachability and routing
cannot disagree.

**Production routing is local, by CCP's own guidance.** `POST /route` exists,
is uncached and has its own 3,600/15-min token group; it is used for **spot
verification only**, never in a scan loop.

### §23.9 Security is what the client displays

High-sec is decided on **displayed** security, not on the raw float:

```
display = round(true_sec, 1)          # half-up
except: 0 < true_sec <= 0.05  ->  0.1
high_sec  ==  display >= 0.5          # i.e. true_sec >= 0.45
```

A system at 0.4499 is **not** high-sec and a system at 0.45 **is**, which is
exactly the boundary a hauler gets ganked on. The boundaries 0.449 / 0.45 /
0.5 / 0.049 / −0.1 are fixtured.

### §23.10 Ranking

Candidate quantities are **every source-ask and destination-bid cumulative
breakpoint**, capped by capital, exposure, cargo and (maker mode) the liquidity
caps. Each feasible breakpoint is priced end to end — source WAP, destination
WAP, sales tax through `CostModel.sell_proceeds(maker=False)`, net profit, net
ROI, profit/m³, cargo utilisation, the **marginal net of the final chunk**
(which must be > 0), route facts, active minutes, net ISK per active minute and
`isk_per_capital_day`.

**The mixed-cargo basket admits at most one plan per `(type, source)` and per
`(type, destination)`.** *(Corrected 2026-08-25 after the audit: the basket
packed every ranked plan, so one 1,000-unit Jita ask sold to two hubs became
2,000 units of cargo out of a 1,000-unit book, and the mirror case
double-counted one destination's bid depth. The scan is right to rank those
plans independently — they are alternatives — but a book can only be spent
once.)* Plans withheld for overlap are **counted and named on the basket**.
**The known refinement, deliberately not built:** a shared consumption ledger,
so part of a book could go to one hub and the rest to another. It needs the
marginal chunks re-priced against what a sibling plan already took, which is a
different computation rather than a filter, and it is worth doing only if real
baskets look starved.

**The first chunk's marginal IS its net** — it is the step from zero, so a
first size that loses money is refused as `MARGINAL_NET_NEGATIVE` like any
other. *(Corrected 2026-08-25 after an adversarial audit reproduced the
alternative: with the rule running only from the **second** breakpoint, a pair
whose smallest fillable size netted −51.7% ranked as a plan with no rejection
at all. On a market whose median spread is 98.8% that is most pairs, and the
page would have filled with the hundred least-bad losses while "Nothing clears
costs today" became unreachable.)*

**The search stops at the first size that stops paying.** Per-unit marginal net
is monotonically non-increasing in quantity — the ask WAP only rises and the
bid WAP only falls — so a chunk whose aggregate is ≤ 0 guarantees every later
one is too. Continuing cannot accept a larger size; it only floods the rejected
set and burns walks.

The default objective is **conservative net ISK per active minute**; the
max-profit, max-ROI and max-ISK/m³ quantities are recorded beside it whenever
they differ, because they usually do and the difference is the interesting
part.

`active_minutes = jumps × seconds_per_jump / 60 + 2 × handling_minutes`, and
the per-minute denominator is floored at the handling time — a zero-jump plan
still costs the time it takes to load and unload.

### §23.11 The page

Control strip: system autocomplete from `sde_solar_systems`, ship-profile
picker, capital / exposure / session minutes / max jumps / security controls,
mode, destination, saved filters in `state.db`'s `meta` table.

Default columns: **Item** (THIN/UNKNOWN badged) · **Route** · **Qty** ·
**Capital** · **Net profit** · **Net ROI** · **Cargo** · **Pickup** · **Trip** ·
**Liquidation** · **Route risk** · **Reliability** · **Rank**. Everything else
is optional. Blanks sort last in both directions. "Nearest first" is a preset
sorting on Pickup.

Detail drawer on click: both price ladders with the consumed levels
highlighted, the breakpoint table ("why this size"), the route's system list
with per-system displayed security, the fee and cost audit, the liquidity
scenarios, and the rejected alternatives with their reason codes. Charts go
through the existing `chart_requested` signal — one chart window, still.

The page must visibly carry, at all times: both generation ages, **"a snapshot
is not a tape — books move while you fly"**, the order-age caveat, the
`min_volume`-excluded depth, the route provenance (SDE build) and every
UNKNOWN's reason.

### §23.12 Who computes what

| Producer | Product |
|---|---|
| `daemon` / CLI | **Ingest products**: depth generations written by `sweep-books`, route cache, destination bars |
| GUI page worker | **Per-profile feasibility and ranking**, off the GUI thread, from local data only |
| `haul scan` (CLI) | The **audit artefact**: an immutable report under `data/reports/` |

The page and the CLI call the same `hauling.py`, so a disagreement between them
is a bug rather than two opinions. Every new file the page depends on joins
`desk_input_key`, or the desk would keep painting a stale generation.

### §23.13 Rejection vocabulary

Every rejected candidate carries exactly one, and the rejected set is
**queryable rather than discarded**: `STALE_BOOK`, `DEPTH_TRUNCATED`,
`DEST_DEPTH_SHORT`, `MIN_VOLUME_BLOCKED`, `ROUTE_BLOCKED_SECURITY`, `NO_ROUTE`,
`OVER_CAPITAL`, `OVER_EXPOSURE`, `OVER_CARGO`, `OVER_JUMPS`, `OVER_TIME`,
`LIQUIDATION_UNKNOWN`, `MARGINAL_NET_NEGATIVE`, **`VOLUME_UNKNOWN`**. "Nothing
cleared" with a denominator and a reason histogram is an answer; an empty table
is not.

**`VOLUME_UNKNOWN` was added 2026-08-25** after an adversarial audit reproduced
the alternative: a type absent from the SDE's packaged-volume map got
`cargo=None`, which **skipped the cargo cap entirely** — a million units ranked
against a 60,000 m³ hold with no rejection. A hold is a cap even when the m³ is
unknown; UNKNOWN is never permission (§4).

**Separately counted, and not a rejection:** `dropped_unrankable` records
priced plans the *chosen objective* cannot score — an ISK/m³ ranking of a type
with no measured volume. Another objective would rank them, so they are not
refused; but they were previously filtered out of the ranking in silence, which
made the scan report a denominator it had not measured.

### §23.14 What this track never does

No SSO and no acting scopes (**H5 and H6 are out of scope for this build
entirely**). No fetch before `Expires`; ETags always; existing self-caps
unchanged. No `open` column and none synthesized. No change to the frozen AVWAP
σ formula or to any frozen verdict rule. No page under `gui/` may import an ESI
client. Tri-state everywhere, UNKNOWN always fails, and an honest zero is a
valid scanner result.

### §23.15 Phases, and the single-push deviation

| phase | scope | state |
|---|---|---|
| **H1a** | SDE extension (security, stargates, NPC stations) + the route engine | **IMPLEMENTED + GREEN** |
| **H1b** | Station depth reduction, the executable walk, the depth lake | **IMPLEMENTED + GREEN** |
| **H2** | The hauling engine, the report, the `haul` CLI, the GUI page | **IMPLEMENTED + GREEN** |
| **H3** | Liquidity scenarios, the maker display, **mixed-cargo packing** (the greedy basket) | **IMPLEMENTED + GREEN** |
| **H4** | Opportunistic detour mode, self-haul vs PushX | **IMPLEMENTED + GREEN** |
| ~~H5, H6~~ | **OUT OF SCOPE.** They would need authenticated ESI | not built |
| **H0** | The competitor comparison — **moved after the shadow period** (§23.2, §23.20) | owed, after the shadow |

**Nothing in this track is `LIVE_VALIDATED`.** The gate stamp after the
**2026-08-25 remediation** (§17 D-35) is `uv run pytest -q` → **1,068 passed,
7 deselected**, ruff check + format clean, `selftest` **12/12**. *(The build
session's own stamp was 1,030; the twelve remediation fixes and their fixtures
account for the difference.)* The consolidated owed checklist is in
`CURRENT_CHECKPOINT.md` and is **unchanged** by the remediation — none of it
was live evidence, and none of it has been earned.

**H1–H4 were built in one push under operator authorization 2026-08-25**,
recorded as §17 **D-33** with the operator's stated reason. The per-phase gates
are **not waived**: they are batched into one consolidated owed live-validation
checklist in `CURRENT_CHECKPOINT.md`.

The CLI subcommand is **`haul`**. Mixed-cargo packing belongs to **H3**, not
H2 — it is a heuristic over the single-item plans and cannot exist before they
do.

**LOC budget for this track: ≤ 7,000 lines including tests**, and the final
count is reported in `CHANGELOG.md`. §1's repo-wide budget remains exceeded by
authorization (§17 D-9, D-20); this is a per-track ceiling, not a new licence.

**Measured: 8,953 lines at the build, 10,180 after the 2026-08-25 remediation
— 3,180 over, recorded as §17 D-34 and D-35.** 4,024 in seven
new core modules plus the page, 3,021 in eleven new test modules, and 1,908
added to existing files (658 of them the depth reduction in `books.py`, 462 the
`haul` command surface). Excluding blank lines, comments and docstrings the
executable total is **5,387**, which is inside the target — but the target said
"lines including tests" and the honest reading of it is the first number.

### §23.16 Tests and fixtures

Golden fixtures land **before** the code they pin (§11 D5): the synthetic
10-system graph (shortest / safer / highsec-only / avoid / disconnected) and
the displayed-security boundaries; the real-SDE graph fixture carrying its
build number; `min_volume` exclusion; `solarsystem` and numeric-range
reachability; a structure-resting region-ranged bid included; truncation →
UNKNOWN; the §23.17 worked example end to end. Plus: `book_summary`
byte-stability through the modified sweep path, the headless import walk and
the GUI network-isolation probe extended to every new module, `desk_input_key`
moving on a depth or report change, and an additive-migration test against a
fixture database built with the **old** schema.

### §23.17 The worked example (fixtured end to end)

Source asks: 800 @ 100,000 and 400 @ 107,250. Destination bids: 500 @ 120,000
and 700 @ 115,500. Quantity **1,200** — a cumulative breakpoint on both sides.

| | |
|---|---|
| source WAP | **102,416.67** (122,900,000 ISK committed) |
| destination WAP | **117,375** (140,850,000 ISK gross) |
| sales tax @ 3.375% | **4,753,687.50** |
| **net profit** | **13,196,312.50** |
| **net ROI** | **10.74%** |

### §23.18 Config

`[hauling]` — `enabled`, `hub_station_ids`, `extra_destination_station_ids`,
`max_scan_capital_isk`, `depth_safety_margin`, `default_objective`,
`liquidity_quantiles`, `destination_share_prior`, `capture_share`,
`min_liquidity_bars`, `default_session_minutes`, `default_max_wait_days`,
`default_seconds_per_jump`, `default_handling_minutes`,
`max_exposure_pct_per_trade`, `max_exposure_pct_per_destination`.
`[routes]` — `security_profile`, `safer_penalty`, `avoid_systems`, `cache`.

Every value is an operator-editable default and is commented as one. **Hub
station ids are resolved from the SDE, never from memory**; the resolved ids
and the systems they sit in are recorded in `CHANGELOG.md` with the build
number they were read from.

### §23.19 What is owed before any of this is trusted

Nothing in this track is `LIVE_VALIDATED`. The consolidated checklist lives in
`CURRENT_CHECKPOINT.md` and covers: ten in-game route spot checks including a
0.45–0.49 boundary system; ten in-game quote/depth spot checks against the
ladders; one unit sold into a ranged bid (one of them structure-resting) with
where the goods and ISK landed recorded; one liquid `order_id` tracked across
sweeps to settle whether `issued` moves on reprice; the measured depth rows and
bytes per five-hub generation, from which retention is then set; broker-fee
overrides transcribed for two hubs (§22 S6); a two-week shadow of the tab; and
then §23.20.

### §23.20 H0 — the deferred comparison, as a keep/park gate

After the shadow period, the tab is compared against **EVE Flipper**, **EVE
Profits** and **ISK Scout** on the same day, on the operator's own hubs, on
these questions:

1. Does any of them price a **quantity** against **executable** depth at a
   named station, or do they price a margin at top of book?
2. Do they charge the **operator's own** route, ship and session, or a generic one?
3. Do they say **why not** — a rejected-candidate view with reasons?
4. Do they distinguish **measured** from **assumed** anywhere at all?

**Keep** if the answers show this tab is doing something they do not, on
evidence rather than on taste. **Park** if a live third-party site does the
same job better — parking is a real, expected outcome, and it is cheaper than
maintaining a worse copy of EVE Flipper.
