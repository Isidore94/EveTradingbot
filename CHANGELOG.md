# EveTradingbot implemented history

Authoritative for what exists and the sequence of revisions. Remaining work:
`plan.md`. Labels keep the source-repo meanings: `IMPLEMENTED` = code exists,
`GREEN` = deterministic tests pass, `LIVE_VALIDATED` = real-market evidence
recorded, `PROMOTED` = explicit operator decision.

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
