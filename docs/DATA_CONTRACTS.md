# Data contracts

Every schema this system writes, in one place. Source of truth for each is the
module named in its section; this file exists because no single module holds the
whole picture and the operator needs one.

Cross-cutting rules:

- **All timestamps are tz-aware UTC.** EVE time *is* UTC; the digest displays
  UTC only. Naive datetimes are rejected, not coerced (`clock.ensure_utc`).
- **Every write is atomic.** A partial write can never replace a verified file
  (`paths.atomic_write_path`).
- **Freshness is a value, not an assumption.** Rows carry when they were
  fetched; consumers surface staleness rather than computing through it
  (decision 0007).

---

## 1. Daily bars — the bar contract

Source: `src/evescreener/bars.py`. Decision: 0005.

```python
EVE_DAILY_BAR_COLUMNS = ["datetime", "high", "low", "close", "volume", "order_count"]
```

**There is no `open` column and none is ever synthesized.** Anything that
requires an open is dead code here.

Mapping from the ESI `/markets/{region_id}/history` response, at the single site
`bars._FIELD_MAP`:

| ESI field | Bar column | Note |
|---|---|---|
| `average` | `close` | The whole-day trade-derived mean. Also the AVWAP typical price (decision 0006). |
| `highest` | `high` | May contain off-market prints; winsorization is Phase 2 (`plan.md` §0 check #4). |
| `lowest` | `low` | Same. |
| `volume` | `volume` | Units. |
| `order_count` | `order_count` | Referenced nowhere upstream; a free column here. |
| `date` | `datetime` | Stamped at 11:00 UTC, the downtime boundary. |

Full lake row (`bars.LAKE_COLUMNS`):

| Column | Type | Meaning |
|---|---|---|
| `type_id` | int64 | SDE type. |
| `region_id` | int64 | 10000002 (The Forge) in v1. |
| `date` | date | Identity key, with `type_id` and `region_id`. |
| `datetime` | datetime64[ns, UTC] | `date` at 11:00 UTC. |
| `high`, `low`, `close` | float64 | ISK per unit. |
| `volume` | int64 | Units traded that day. |
| `order_count` | int64 | Distinct orders filled that day. |
| `isk_value` | float64 | `volume × close`, derived on write. |
| `fetched_at` | datetime64[ns, UTC] | When we asked. |
| `last_modified` | str | The response's `Last-Modified`, verbatim. |

**Completed bars only.** A row dated on or after
`clock.last_completed_bar_date()` is dropped, not carried. That boundary is
"yesterday if the 11:05 UTC roll has happened today, otherwise the day before".

**Data-quality note.** `order_count == 0` rows are recorded as data-quality
events and are to be excluded from ATR and σ warm-ups from Phase 2 — such a bar
is not a real price.

Storage: `data/bars/region={region_id}/year={year}.parquet`, deduped on
`(type_id, region_id, date)` with the newest row winning, so a corrected bar
propagates.

---

## 2. `book_summary` — the order-book reduction

Source: `src/evescreener/books.py`. Decisions: 0008, 0010.

A full Forge sweep is ~412,000 orders; at 288 sweeps/day that would be ~10 GB/day
raw. **Raw orders are never persisted.** Each sweep is reduced in memory to one
row per `(type_id, region_id, side)`:

| Column | Type | Meaning |
|---|---|---|
| `type_id`, `region_id` | int64 | Identity. |
| `side` | str | `"buy"` or `"sell"`. |
| `sweep_ts` | datetime, UTC | When the sweep started. Identity, with the three above. |
| `expires_ts` | datetime, UTC | When this book generation expires. |
| `best_price` | float | Top of book. **Displayed, never ranked on.** |
| `total_volume` | int64 | Units resting on this side, region-wide. |
| `order_count` | int64 | Orders resting on this side. |
| `p5_price` | float | Volume-weighted mean price of the best 5% of resting volume (the Fuzzwork statistic, replicated locally). |
| `top_order_volume_share` | float | Largest single order ÷ total volume. Flag above 0.5 — the book may be one order. |
| `station_volume_share` | float | Share of volume resting in NPC stations (`location_id < 10¹²`). The structure-blind-spot metric. |
| `depth_fill_price_{1,2,3}` | float | Effective unit price walking the book for 0.25B / 1.0B / 2.5B ISK. **NaN means the book cannot absorb that notional** — a distinct answer from a price. |
| `depth_fill_qty_{1,2,3}` | int64 | Units obtainable. When the price is NaN, this is the whole book. |

Sides fill from opposite ends: a sell-side walk starts at the cheapest ask
(buying), a buy-side walk at the highest bid (selling).

Sweeps are reconciled by `order_id`; cross-page duplicates are counted as a
data-quality signal, not treated as an error (`plan.md` §0 check #2).

Storage: `data/books/region={region_id}/date={YYYY-MM-DD}.parquet`, appended per
sweep, deduped on `(type_id, region_id, side, sweep_ts)`.

---

## 3. The screen frame

Source: `src/evescreener/screen.py`, `src/evescreener/costs.py`. Decision: 0008.

Built by joining the newest `book_summary` to the bar lake's 30-day turnover.
Not persisted as Parquet in Phase 0 — it is rendered to the digest and archived
as JSONL.

| Column | Meaning |
|---|---|
| `type_id`, `name` | Identity. |
| `status` | `"priced"` or `"unknown"`. **UNKNOWN always fails** (decision 0007). |
| `reason` | Why a row is UNKNOWN, in words. `None` when priced. |
| `best_bid`, `best_ask` | Top of book, for in-game cross-checking. |
| `spread_pct` | `(ask − bid) / mid × 100`. Can be negative — see `crossed_book`. |
| `crossed_book` | True when the best bid sits above the best ask: different stations, or a lone cheap order. Read the netted number, not the spread. |
| `p5_bid`, `p5_ask` | The 5%-percentile prices. |
| `entry_price`, `entry_units` | Ask-walk VWAP at the smallest tier, and the units it buys. |
| `net_margin_pct` | `(bid_walk × (1 − tax) − entry) / entry × 100`. **The ranking quantity.** |
| `breakeven_move_taker_pct` | How far the bid walk must rise for a taker exit to break even. |
| `breakeven_move_maker_pct` | How far above the current best ask a resting sell must clear tax **and** broker fee. Advisory: queue risk is flagged, never netted away. |
| `median_isk_value_30d`, `median_order_count_30d`, `bars` | Trailing-30-day turnover. Median, not mean — one wash-trade day must not decide liquidity. |
| `passes_liquidity_floor` | Against the configured floor (pre-census: ≥100M ISK/day median **and** ≥30 orders/day median). |
| `top_order_volume_share`, `station_volume_share` | Carried through from the book. |
| `book_age_minutes`, `sweep_ts` | Freshness. Older than `book_staleness_minutes` (60) forces UNKNOWN. |

A row is UNKNOWN when: the book is stale, one side has no resting orders, or the
book cannot absorb the smallest notional tier.

### Cost arithmetic

```
sales tax    = 7.5% × (1 − 0.11 × Accounting)          → 3.375% at V
broker fee   = 1.0% at Broker Relations V + standings  → posting/modifying only, never on a taker fill
entry(S)     = ask-walk VWAP for notional S
exit_taker(S)= bid-walk VWAP for S × (1 − tax)
exit_maker   = target × (1 − tax − broker)
```

Tax and broker rates are configuration, not constants. `plan.md` §0 checks #5
and #6 may correct them; that is a `config.toml` edit, not a code change.

---

## 4. `state.db` (SQLite, WAL)

Source: `src/evescreener/state.py`. Path: `data/state.db`.

| Table | Holds |
|---|---|
| `http_cache` | Per-URL `etag`, `expires_at`, `last_modified`, `fetched_at`, `status`. The freshness gate reads this before every request. |
| `sweep_ledger` | One row per request. The telemetry ledger — see `ESI_CLIENT_RUNBOOK.md`. |
| `sde_types` | `type_id`, English `name`, `published`, `group_id`, `market_group_id`, `volume`, `packaged_volume`, `portion_size`. |
| `sde_market_groups` | `market_group_id`, `name`, `parent_group_id`, `has_types`. The tree RRS cohorts will walk in Phase 3. |
| `sde_meta` | `sde_build`, `sde_release_date`. |
| `watchlist` | `type_id`, `name`, `source`, `added_at`. **Rows are never auto-removed.** |

---

## 5. On-disk layout

```
data/                          # EVESCREENER_DATA_DIR overrides; ./data by default
├── bars/region=10000002/year=2026.parquet
├── books/region=10000002/date=2026-08-18.parquet
├── cache/<sha256(url)>.json.gz    # response bodies, so a 304 resolves to data
├── streams/
│   ├── digests.jsonl              # every digest, archived before delivery
│   └── decisions.jsonl            # operator decisions (Phase 3)
└── state.db
```

The whole tree is one directory and is rsync-able. No DAS, no cloud, no writer
leases — one machine, one process, atomic writes.
