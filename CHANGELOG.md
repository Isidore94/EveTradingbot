# EveTradingbot implemented history

Authoritative for what exists and the sequence of revisions. Remaining work:
`plan.md`. Labels keep the source-repo meanings: `IMPLEMENTED` = code exists,
`GREEN` = deterministic tests pass, `LIVE_VALIDATED` = real-market evidence
recorded, `PROMOTED` = explicit operator decision.

Each entry states **what changed and why**. Where a "why" is load-bearing enough
to outlive the change, it is expanded into a numbered decision record under
`docs/decisions/` and linked from here — this file keeps the one-line reason so
it reads on its own.

## 2026-08-18 — Reference documentation and decision records

No behaviour change. `docs/` added, mirroring the source repo's structure
(`docs/decisions/NNNN-slug.md` in its Context/Decision/Rationale format, plus
runbook-style reference files).

| Change | Rationale |
|---|---|
| `docs/decisions/0001`–`0015` — fifteen decision records backfilled from `plan.md` §11 and its invariants, plus one recording a Phase 0 correction | `plan.md` §11 is a *contract*: deliberately terse, because a binding table should be. That terseness loses the reasoning, and reasoning is what a future session needs in order to know whether a decision still applies. The source repo hit this exactly — several of its own backfilled records read "RATIONALE UNKNOWN - confirm with Aaron". Writing ours while the reasoning is still fresh is the only cheap moment. |
| `docs/DATA_CONTRACTS.md` — every schema in one place: the bar contract, `book_summary`, the screen frame, the SQLite tables, the on-disk layout | The schemas are spread across five modules and no single file holds the whole picture. The operator needs one, and so does anyone reading a Parquet file six months from now. |
| `docs/ESI_CLIENT_RUNBOOK.md` — cadences, budgets, the ledger's columns, the queries that read it, and a symptom→action table for every limit that can trip | The client enforces its own invariants (record 0004), so the operator never has to *remember* a rule — but when something trips he has to *diagnose* it, and 420 versus 429 versus `BudgetExhausted` demand different responses. Guessing there is how a bannable mistake happens. |
| `docs/FIRST_SESSION_CHECKLIST.md` — clone to first digest, each step naming the artifact that proves it | The source repo's equivalent, adapted. It also pre-empts the two things that look like failures and are not: an empty candidate list, and a sweep that reports "still fresh" instead of refetching. |
| `docs/README.md` — index, plus an explicit subordination clause | The control set is four files (record 0013). `docs/` must not become a fifth authority, so the clause says in the file itself: where `docs/` and `plan.md` §11 disagree, §11 wins and `docs/` is what is stale. |
| `CLAUDE.md`, `plan.md` §11 D8, `README.md` — updated to name `docs/` and its subordinate status | A directory nothing points at is a directory nobody reads, and one that no rule constrains is one that drifts into a second roadmap. |

**Deliberately not created:** `AGENTS.md`. `plan.md` §11 D8 forbids it, and the
source repo shows why — its `AGENTS.md` is a byte-for-byte copy of `CLAUDE.md`,
maintained by hand, one forgotten edit from telling an agent something false.
If a non-Claude agent needs an entry point here, the answer is a three-line
pointer file rather than a copy, and that is a plan-level decision to make
deliberately.

**Also not created:** `VENDORED.md`. Nothing is vendored yet — that begins in
Phase 2, and an empty manifest would be a claim rather than a record.

## 2026-08-18 — Phase 0: first light (IMPLEMENTED → GREEN)

Everything in `plan.md` §8 Phase 0, built to §11's locked decisions. Live gate
still owed (`CURRENT_CHECKPOINT.md`), so nothing here is `LIVE_VALIDATED` yet.

- **Scaffold (D1–D2).** uv project, Python ≥3.12, runtime deps exactly
  `httpx[http2]`/`pandas`/`pyarrow`/`numpy`; `pytest` + `ruff` for dev, no
  lint exemptions. `src/evescreener/` package, `python -m evescreener
  <daemon|ingest-history|sweep-books|census|digest|selftest>`. `config.toml`
  gitignored, `config.example.toml` committed with every key; `selftest` fails
  on divergence. `EVESCREENER_DATA_DIR` is the only env override. All
  timestamps tz-aware UTC.
- **ESI client (§3.1–§3.3).** Descriptive UA with operator contact, pinned
  `X-Compatibility-Date`, ETag store and gzipped body cache so a 304 resolves
  to real data for one token, and a hard **never-fetch-before-expiry** rule: a
  still-fresh URL is skipped and recorded as `skipped_fresh`, and if no cached
  body exists the client waits out the window rather than asking early. Token
  accountant for the `market-order` group hard-stops at 6,000/15-min; history
  is paced at 150 req/min. Bounded retries on 5xx and transport errors only;
  4xx is never retried; 429 sleeps `Retry-After`; 420 stops for 60 s.
- **Telemetry ledger (§3.5).** Every request writes a `sweep_ledger` row: URL,
  status, tokens charged, the `X-Ratelimit-*` headers as observed, `Expires`,
  the previously-stored expiry, and whether that expiry had passed. This is the
  evidence the Phase 0 gate reads, and the digest footer summarises it.
- **SDE loader (§3.6).** Official jsonl static data, build-pinned, loading
  `types` and `marketGroups` into `state.db`. Build 3470007: 52,863 types.
- **Watchlist (D4).** The 50 seed names, resolved against the SDE. An
  unresolvable name raises with the full list — never a silent skip. Nothing
  removes a watchlist row automatically.
- **Bar contract (§4).** `EVE_DAILY_BAR_COLUMNS = ["datetime","high","low",
  "close","volume","order_count"]`, `close ← average` mapped in exactly one
  place, no `open` and no way to synthesize one. `datetime` stamped at the
  11:00 UTC downtime boundary; `isk_value = volume × close` derived on write.
  **Completed bars only:** a bar dated on or after the current roll boundary is
  dropped, not carried. Parquet lake partitioned `region/year`, written
  atomically, deduped on `(type_id, region_id, date)`.
- **Book sweep and reduction (§3.4).** One paged Forge sweep, reconciled by
  `order_id`, reduced in memory to one `book_summary` row per
  `(type_id, region_id, side)` with `best_price`, `p5_price`,
  `depth_fill_price/qty` at 0.25B/1.0B/2.5B, `top_order_volume_share` and
  `station_volume_share`. Raw orders are never persisted.
- **Cost model and screen (§5).** Taker entry on the ask walk, taker and maker
  exits both reported, sales tax from `Accounting` level and broker fee from
  config, breakeven quoted against the price each exit must actually beat.
  Tri-state throughout: a stale book (>60 min), a missing side, or a tier the
  book cannot absorb renders UNKNOWN with a reason, never a priced row.
  Crossed region-wide books are flagged rather than shown as a spread.
- **Digest and delivery (D6).** Discord webhook, mentions suppressed, content
  split into numbered ≤2,000-char messages with fences closed and reopened
  across splits; a line that cannot fit is reported, never truncated silently.
  Result contract `unconfigured/delivered/rejected/rate_limited/ambiguous`,
  injectable opener. Honest zero is the default outcome and says so. Every
  digest is archived to `streams/digests.jsonl` before delivery is attempted.
- **Tests (D5).** 112 offline tests plus 3 `network`-marked live smoke tests;
  `pytest -q` is offline by default. Recorded ESI fixtures and a frozen golden
  `book_summary` reduction, each carrying source URL, `acquired_at` and the
  `X-Compatibility-Date` it was recorded under.
- **Plan corrections from live evidence**, recorded in `plan.md` §0's new
  "Phase 0 live measurements" block: the `X-Compatibility-Date` pin moved to
  `2026-08-17` (ESI rejects a future date on its UTC-11 clock — §11 D2 amended
  with the reason); check #3 measured (6 Upwell structures in The Forge, all
  buy-side, **zero** structure sell orders; Jita 4-4 holds 86.4% of Forge sell
  orders); check #2 half-answered (0 duplicate `order_id`s across 413 pages);
  PLEX found to be unpriced by The Forge since 2025-07-07.

### Why it is built this way

The choices above are not neutral, and several of them cost something. In brief,
with the full argument in the linked record:

| Choice | Why, in one line | Record |
|---|---|---|
| Cache and budget rules live *in* the client, not in calling conventions | Code that is only correct when every caller remembers a rule will eventually be incorrect — and this is the one rule whose failure costs the account, not a number | [0004](docs/decisions/0004-never-fetch-before-expiry.md) |
| Self-caps at 50% of every published limit | The planned worst case is ~21% of budget; the cap exists to stop a *bug*, and a bug does not respect a comment | [0004](docs/decisions/0004-never-fetch-before-expiry.md) |
| The telemetry ledger records `honored_expiry` per request | Compliance has to be *provable* after the fact, not asserted; it is also what detects a token-regime change on the day it happens rather than during a 429 storm | [0004](docs/decisions/0004-never-fetch-before-expiry.md) |
| No `open` column, ever | A synthetic open does not fail loudly — it makes open-consumers *run* while computing fiction (gap logic silently zeroes, candle tests degrade, one indicator raises). Uncertainty must not be laundered into confirmation | [0005](docs/decisions/0005-new-bar-contract-no-open.md) |
| `close ← ESI average`, and that is also the AVWAP typical price | `average` is a whole-day trade-derived mean — it *is* the day's typical price, strictly better than a four-point proxy of it | [0005](docs/decisions/0005-new-bar-contract-no-open.md), [0006](docs/decisions/0006-avwap-sigma-formula-frozen-tp-is-close.md) |
| Completed bars only; UNKNOWN always fails | A screener that prices off a stale book presents yesterday's depth as today's and the operator cannot tell by looking. One UNKNOWN row buys trust in every priced row | [0007](docs/decisions/0007-completed-bars-only-tri-state-gates.md) |
| Rank on the depth walk, never on best price | The walk *prices bait in and dilutes it* — arithmetic that cannot be fooled, not a filter that might miss. Phase 0's Zydrine case: best ask 1,000 ISK against a 0.25B walk of 1,198 | [0010](docs/decisions/0010-rank-on-depth-walk-and-p5-never-on-best-price.md) |
| Costs netted inside the screen at real notionals | The screener's namesake failure is a gorgeous margin that cannot absorb 0.25B — because the wide margin *is* the illiquidity premium. Netting makes it rank at zero structurally, not advisorily | [0008](docs/decisions/0008-costs-netted-inside-the-screen-at-notional-tiers.md) |
| Honest zero as the default digest | Given the netting, an empty candidate list is the *expected* daily output. A digest that yields to the pressure to be non-empty teaches the operator that its top row means something when it does not | [0012](docs/decisions/0012-discord-digest-honest-zero-nothing-dropped-silently.md) |
| Response bodies cached on disk | Makes "ETags always" honest: a 304 resolves to real data for one token instead of leaving a hole where a body should be | [0004](docs/decisions/0004-never-fetch-before-expiry.md) |
| `daemon` and `census` declared but refusing to run | The scheduler is Phase 1 scope. A subcommand that silently no-ops is worse than one that names the phase it is waiting for | [0013](docs/decisions/0013-four-file-control-set-plan-md-authority.md) |
| An unresolvable watchlist name raises with the full list | Names drift across patches; a silently dropped name is a hole in the screen nobody notices | [0011](docs/decisions/0011-golden-fixtures-before-detector-changes.md) |
| Four runtime dependencies, no lint exemptions | The binding constraint is the operator's attention, not capability. A per-file exemption is how a 31,000-line monolith starts | [0014](docs/decisions/0014-uv-four-runtime-deps-no-lint-exemptions.md) |
| The compatibility-date pin moved off the locked value | It could not be transmitted: CCP evaluates the header on a UTC-11 clock, so "today" fails for part of every day it is set — worse than failing always, because it looks intermittent | [0015](docs/decisions/0015-compatibility-date-pinned-to-a-fully-past-day.md) |

## 2026-08-18 — Planning complete, decisions locked

- `plan.md` landed: port review of TradingBotV3 (`phase05-r8-weekend-prep`,
  `phase05-r2-focus-gating-strength-board`), repo architecture decision
  (standalone repo + vendoring), module inventory, ESI data-layer spec with
  verified token arithmetic, bar contract (no `open`, `close ← average`),
  depth-aware cost model, signal translation table, zKillboard assessment
  (Phase 5, EVE Ref archives + R2Z2), six phases with gates, risk register,
  non-goals.
- `plan.md` §11 added: locked implementation decisions D1–D8 (uv/httpx/pandas
  stack, config shape, cadence defaults, notional tiers, liquidity floor,
  50-name seed watchlist, test/fixture policy, Discord webhook contract,
  anchor calendar, governance/control set).
- `CLAUDE.md`, `CURRENT_CHECKPOINT.md`, this file: governance control set
  established. No product code exists yet.
