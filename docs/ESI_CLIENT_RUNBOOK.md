# ESI client runbook

Operating `src/evescreener/esi.py`. Decision 0004 explains *why*; this is *how*,
and what to do when something trips.

**The one rule that is not a performance concern:** never fetch before expiry.
CCP treats cache circumvention as a bannable offence and attaches developer-app
termination language to the history endpoint's limit. Every other number in this
file is a dial. That one is a correctness invariant.

---

## What the client sends

| Header | Value | Set by |
|---|---|---|
| `User-Agent` | `EveTradingbot/{version} (aaron.siano@gmail.com; +https://github.com/Isidore94/EveTradingbot)` | `config.toml [esi] user_agent` |
| `X-Compatibility-Date` | `2026-08-17` | `config.toml [esi] compatibility_date` — must be a fully-past day (decision 0015) |
| `If-None-Match` | The stored ETag, whenever one exists | automatic |

The User-Agent is never a library default; CCP has publicly named default agents.

---

## Endpoints, limits, and what a sweep costs

| Feed | Endpoint | Regime | Measured cost |
|---|---|---|---|
| Forge order book | `/markets/10000002/orders` | `market-order` tokens: 12,000 / 15-min floating window | **413 pages × 2 = 826 tokens**, ~38 s at concurrency 4 |
| Daily history | `/markets/10000002/history?type_id=` | *Outside* the token regime. CCP states 300 req/min/IP | 1 request per type; 50 types ≈ 20 s at our 150/min pacing |
| Global prices | `/markets/prices` | token-free, 1-hour cache | 2 tokens |

Token costs: 2 per 2xx, **1 per 304**, 5 per 4xx, 0 per 5xx, 429 exempt.

Self-caps (`config.toml [budget]`): orders hard-stop at **6,000** tokens/window
(50% of the published limit), history paced at **150** req/min (50% of CCP's
stated ceiling). The planned worst case is ~21% of budget — the cap exists to
stop a bug, not the design.

Cadences (`config.toml [schedule]`, `plan.md` §11 D3): history at 11:20 UTC,
book HOT window 15:00–17:00 UTC (every cache window) and hourly outside it,
digest at 16:00 UTC. **None of these are scheduled yet** — the `daemon` that
owns them is Phase 1. Phase 0 runs the jobs by hand.

---

## The telemetry ledger

`state.db → sweep_ledger`, one row per request. Columns that matter:

| Column | Meaning |
|---|---|
| `outcome` | `fetched` · `not_modified` · `skipped_fresh` · `error` · `transport_error` |
| `honored_expiry` | **0 means we asked while a stored `Expires` was still in the future.** This must always be 1. |
| `tokens_charged` | What the request cost under the regime above |
| `ratelimit_used` / `ratelimit_remaining` / `ratelimit_limit` | The `X-Ratelimit-*` headers exactly as returned |
| `error_limit_remain` | `X-Esi-Error-Limit-Remain`; the legacy 100/min counter |
| `expires_at` / `prior_expires_at` | This response's expiry, and the one we held before asking |
| `sent_if_none_match` | Whether an ETag went out |

`skipped_fresh` is distinct from `not_modified` on purpose: the first means we
never asked, the second means we asked and it had not changed. Only the second
costs a token.

### Reading it

The digest footer summarises the last 24 hours. Directly:

```bash
sqlite3 data/state.db "
  SELECT COUNT(*) requests,
         SUM(1 - honored_expiry) early_fetches,
         SUM(tokens_charged) tokens,
         MAX(ratelimit_used) peak_used,
         SUM(status >= 400 AND status < 500) client_errors,
         SUM(status >= 500) server_errors
  FROM sweep_ledger;"
```

Healthy looks like: `early_fetches = 0`, `client_errors = 0`, `peak_used` well
under 6,000.

Recent activity, newest first:

```bash
sqlite3 -header -column data/state.db "
  SELECT requested_at, status, outcome, tokens_charged, ratelimit_used, url
  FROM sweep_ledger ORDER BY id DESC LIMIT 20;"
```

Token spend in the current rolling window:

```bash
sqlite3 data/state.db "
  SELECT SUM(tokens_charged) FROM sweep_ledger
  WHERE ratelimit_group = 'market-order'
    AND requested_at >= datetime('now', '-15 minutes');"
```

---

## When something trips

| Symptom | What the client does | What you do |
|---|---|---|
| `BudgetExhausted` | Refuses the request **before** sending it | Nothing is wrong with ESI. Something asked for too many sweeps — check `sweep_ledger` for a loop. Wait out the 15-minute window. |
| HTTP 429 | Sleeps `Retry-After`, retries within the bounded attempts | Expected under load; investigate only if it repeats with the self-cap in place. |
| HTTP 420 (`ErrorLimited`) | Sleeps 60 s, then **stops the job** | The legacy error limit tripped: 100 non-2xx/3xx in a minute, on any route. Find the 4xx source in the ledger before running anything again. |
| HTTP 4xx (`EsiHttpError`) | Raises immediately, **never retries** | A bug to fix, not a transient. Costs 5 tokens each, so a retry loop here is how you reach 420. |
| HTTP 5xx | Retries 3× with 2/4/8 s backoff plus jitter, then raises | Costs 0 tokens. If it persists, ESI is down; run again later. |
| Transport error | Same bounded retry | Usually local networking. |
| `early_fetches > 0` | — | **Stop.** This is the invariant failing. Do not run another sweep until you know why. |

### Compatibility-date failure

```
HTTP 400 {"error":"Compatibility date (YYYY-MM-DD) is in the future. Current date (UTC-11) is ..."}
```

The pin names a day that has not fully passed on CCP's UTC-11 clock. Set
`[esi] compatibility_date` to an earlier day; `selftest` catches this before a
run does.

---

## Body cache

Response bodies are gzipped under `data/cache/<sha256(url)>.json.gz` so a 304
resolves to real data for one token. Deleting the cache is safe — the client
then waits out the current window rather than fetching early, which is slower
but never incorrect. Expect ~11 MB after one full Forge sweep.
