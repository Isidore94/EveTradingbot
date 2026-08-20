# EveTradingbot implemented history

Authoritative for what exists and the sequence of revisions. Remaining work:
`plan.md`. Labels keep the source-repo meanings: `IMPLEMENTED` = code exists,
`GREEN` = deterministic tests pass, `LIVE_VALIDATED` = real-market evidence
recorded, `PROMOTED` = explicit operator decision.

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
