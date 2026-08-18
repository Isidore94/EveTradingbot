# Current checkpoint

This file names the one active item, its working state, and the last
verification stamp. `plan.md` owns the roadmap; `CHANGELOG.md` owns history.

## Active item

**Phase 0 — First light** (`plan.md` §8). **IMPLEMENTED → GREEN. Holding at the
live validation gate.**

Everything in Phase 0's scope is built and exercised against live ESI: repo
scaffold (§11 D1–D2), the ESI client with its expiry/ETag/budget invariants
(§3.1–§3.3), the SDE loader and the 50-name D4 watchlist, history ingest into
the Parquet bar store under the §4 contract, one Forge orders sweep reduced to
`book_summary` (§3.4), the net-cost screen (§5) and the Discord digest (D6).

**Phase 1 is not started and must not be started until the gate below passes.**
`daemon` and `census` exist as subcommands and deliberately refuse to run,
naming Phase 1 — the scheduler is Phase 1 work, so Phase 0's jobs are invoked
by hand.

## Verification baseline

Stamped 2026-08-18:

- `uv run pytest -q` → **112 passed, 3 deselected** (the deselected three are
  the `network`-marked live smoke path).
- `uv run pytest -q -m network` → **3 passed** against live ESI.
- `uv run ruff check .` and `uv run ruff format --check .` → clean.
- `uv run python -m evescreener selftest` → ok (SDE build 3470007, 52,863
  types, all 50 watchlist names resolved).
- Live runs: `ingest-history` → 50/50 types, 20,192 bars, 0 partial bars
  carried, 0 zero-`order_count` bars. `sweep-books` → 413 pages, 412,380
  orders, 0 duplicate `order_id`s, 19,151 types reduced, **826 tokens** (the
  planned figure exactly). Ledger: **0 early fetches**, 0 4xx, 0 5xx.
- `digest --dry-run` → honest zero. Nothing in the 50-name roster cleared costs
  at 0.25B; the deepest names sat at −3.5% to −7% net on the round trip, which
  is what a ~3.4% tax plus a two-sided depth walk should produce.

## Gate owed before Phase 1 — operator action

Run these in one sitting. The order matters: the book is only valid for one
five-minute cache window, so refresh it immediately before looking in game.

### Preparation (one terminal command)

```
uv run python -m evescreener sweep-books && uv run python -m evescreener digest --dry-run
```

The digest prints bid, ask, spread, net margin, breakeven and 30-day turnover
for the roster, and stamps the sweep time in its header.

### 1. Five in-game spot-checks (prices and volumes)

For each of the five types below, in the EVE client:

1. Open **Market** (Alt+R), search the item, select it.
2. Set the range dropdown to **The Forge** (region-wide, not "this station") —
   the screener's `book_summary` is a region-wide reduction, so a station-only
   view will not match.
3. **Sell orders** tab, sort **Price ascending**: the top row's price is the
   digest's `ask`.
4. **Buy orders** tab, sort **Price descending**: the top row's price is the
   digest's `bid`.
5. **Price History** tab, region The Forge: read the most recent *completed*
   day's **volume** and **average**. That day is two days back before 11:05 UTC
   and one day back after it.

The five, chosen because each tests something different:

| # | Type | What it tests | What to expect |
|---|---|---|---|
| 1 | **Tritanium** | the deepest, cheapest book | bid/ask ≈ 3.8–3.9 ISK, within one tick |
| 2 | **Large Skill Injector** | a unit price near the 0.25B tier | bid/ask ≈ 0.70–0.73B; the depth walk buys only ~0.3 of a unit's worth of book |
| 3 | **Gila** | a hull, mid-size book | bid/ask ≈ 0.19B |
| 4 | **Zydrine** | the bait/crossed-book case | the region's **lowest sell is far below** the highest buy — the digest flags this row with `*` and its netted entry price (~1,198) should match the Jita depth, **not** the ~1,000 outlier |
| 5 | **Scourge Light Missile** | a book too thin to fill the tier | the digest must show it under **Unpriced — UNKNOWN**, "sell side cannot fill this notional". Confirm in game that the whole regional sell book is worth under 0.25B ISK |

**Pass** if every price agrees to within what one five-minute cache window of
new orders could explain, and every history volume/average matches exactly.
**Falsified** if any disagreement is systematic — a consistent offset, a wrong
side, or a price from a different station tier.

### 2. Fee arithmetic against one real fill (±0.1%)

Do one small round trip on a liquid item (Tritanium is fine; keep it under a
few million ISK):

1. **Buy as a taker**: right-click a resting *sell* order → Buy. Do **not**
   place a buy order — a taker fill pays no broker fee, and confirming that no
   broker fee appears is half of this test.
2. **Sell as a taker**: right-click a resting *buy* order → Sell.
3. Open **Wallet → Transactions** and **Wallet → Journal** and record, for the
   sell: the gross ISK, the `Transaction Tax` entry, and any `Brokers Fee`.

Then check, to ±0.1%:

```
transaction tax  ==  gross_sell_isk * 0.03375      # 7.5% * (1 - 0.11 * 5), Accounting V
brokers fee      ==  0                             # taker fills pay none
```

If the tax rate is not 3.375%, `plan.md` §0 check #6 is answered and
`config.toml`'s `[costs]` block needs the real number — that is a config
change, not a code change. If a broker fee *did* appear on a taker fill, stop:
§5's whole entry model is wrong and Phase 4 depends on it.

Optionally also place (and immediately cancel) one sell order to read the real
broker fee against `broker_fee_effective_pct = 1.0`, which answers check #5.

### 3. Telemetry — every request honored expiry and stayed inside the caps

The digest's own **Telemetry (last 24h)** block reports this. It must read
`all requests honoured Expires`, `4xx 0`, and a peak `X-Ratelimit-Used` far
below 6,000. To read the ledger directly:

```
sqlite3 data/state.db "
  SELECT COUNT(*) AS requests,
         SUM(1 - honored_expiry) AS early_fetches,
         SUM(tokens_charged) AS tokens,
         MAX(ratelimit_used) AS peak_used,
         SUM(status >= 400) AS errors
  FROM sweep_ledger;"
```

**Pass** if `early_fetches` is 0 and `errors` is 0. Any early fetch is a
correctness failure, not a warning — it is the one thing in this repo that can
get the account banned.

## Two decisions the gate should also settle

1. **PLEX.** Type 44992 has no resting orders in the Forge book and its Forge
   history stopped on 2025-07-07 — it trades on the dedicated PLEX market. The
   D4 roster carries a name this region cannot price. Drop it, or add the PLEX
   region (a §3.2 cadence change, so a plan-level edit).
2. **`X-Compatibility-Date`.** D2's `2026-08-18` pin was rejected by ESI on
   every route: CCP evaluates the header on a UTC-11 clock, so the pinned day
   must have passed everywhere. Corrected to `2026-08-17` and recorded in
   §0/§11 D2. Confirm or choose a different past date.

## Notes for the next session

- Do **not** start Phase 1 until the gate above is signed off in `CHANGELOG.md`
  as `LIVE_VALIDATED`.
- `docs/` landed 2026-08-18 (no behaviour change, gate unaffected): fifteen
  decision records under `docs/decisions/`, plus `DATA_CONTRACTS.md`,
  `ESI_CLIENT_RUNBOOK.md`, and `FIRST_SESSION_CHECKLIST.md`. It is subordinate
  to the control set — `plan.md` §11 still binds, and `docs/` must never grow a
  roadmap, a status board, or handoff notes.
- `AGENTS.md` was **not** created: §11 D8 forbids it. If a non-Claude agent
  needs an entry point, the open option is a three-line pointer file (never a
  copy of `CLAUDE.md`) and it is an operator decision.
- `plan.md` §0 now carries a "Phase 0 live measurements" block: check #3 is
  answered on the data side (6 structures in The Forge, all buy-side, zero
  structure sell orders; Jita 4-4 holds 86.4% of Forge sell orders) and check
  #2 is half-answered (zero duplicate `order_id`s in one sweep; the
  two-sweeps-in-one-window diff is still owed and belongs to Phase 1).
- Checks #1 and #4 are untouched and belong to Phase 2, as planned.
- The golden `book_summary` fixture is frozen and reviewed. Changing the
  reduction means regenerating it deliberately, not editing the test.
