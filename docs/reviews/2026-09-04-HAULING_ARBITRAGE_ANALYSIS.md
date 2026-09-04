# Deep analysis — trading and hauling/arbitrage capability, and where the leverage is

Date: **2026-09-04**. Branch `docs/hauling-scanner-plan` at `90a3906`. Written by a
Claude session at the operator's request ("analyse this bot's ability to trade the EVE
market and find excellent hauling/arbitrage opportunities, and find ways to optimise for
that goal"). **Evidence, not authority**: nothing here changes a plan item, a locked
decision or a frozen rule. The ideas at the end are recorded in `WISHLIST.md` as
`CANDIDATE`; the operator promotes or parks them.

Method: the bounded read (`CURRENT_CHECKPOINT.md` active block, `plan.md` §0, §3.2, §5,
§10, §15, §17, §20.2–§20.4, §23; the `CHANGELOG.md` inventory for hauling), the source
of `hauling.py`, `positioning.py`, `liquidity.py`, `crossregion.py`, `spreads.py`,
`books.reduce_depth`, `routes.py`, the CLI, and the GUI page; a read-only inspection of
`data/` and `state.db`; and **two reproduction runs of the hauling engine against
copies of the lake** (procedure in the appendix). **No ESI-fetching subcommand was run.
Nothing was written under `./data/`.**

---

## 1. What the system can do today, by instrument

The repository contains three distinct instruments that answer "can I make ISK in the
market", and they are in very different states.

| Instrument | Where | What it measures | Verdict on record |
|---|---|---|---|
| **Swing screener** (AVWAP bands, RRS, levels, expected-R) | `screen.py`, `scanner.py`, `setups.py`, the DESK/BOARD/SCANNER pages | Dip-below-anchored-value setups on Forge daily bars, priced as a **taker** round trip | **NOT PLAUSIBLE at every horizon** on friction, not direction (§17: 10-day gross +2.80% vs 14.7% round-trip friction; only 2 of 315 tracked types are tight enough). Frozen verdict, never retrofitted |
| **Maker station trading** | `spreads.py`, SPREADS page (§20.2, §17 D-31) | The spread the book is *quoting* to a maker at one hub, guarded against dust bids by the traded average | **Advisory only.** 1,590 Forge names carried a positive quoted maker margin (median +13.0%) on 2026-08-20, but fill probability, undercutting and wait are unmodelled, and §10.5 forbids a market-maker strategy layer. The paper ledger's `maker` fill model is stamped `fill_assumed` |
| **Cross-region / hauling** | `crossregion.py` (§15, CLI `cross-region`), and the §23 HAULING engine (`hauling.py` + six modules, CLI `haul`, HAULING page) | Buy at hub A's executable ask ladder, sell into hub B's reachable bid ladder, at a quantity chosen at the books' breakpoints, netted for tax, route, time and the operator's own ship and capital | **The one measurably positive edge** (§17: 10–14 of 151,113 hub pairs cleared real PushX freight at 0.25B, best +14.44%). The §23 engine is IMPLEMENTED + GREEN after three adversarial passes; **nothing is LIVE_VALIDATED** |

The honest one-line summary the repository already carries is correct: *swing trading
the Forge as a taker does not pay at this operator's size; the residual edge is
spatial (hauling) and, advisorily, maker-side.* Everything below is about making the
hauling instrument answer its question better, because that is the instrument with a
measured positive sign.

### 1.1 What the hauling engine gets right (verified in source)

* **Prices a quantity, not a margin**: every source-ask and destination-bid cumulative
  breakpoint is walked, and the search stops at the first chunk whose marginal net is
  ≤ 0 (`hauling.py` `_best_plan`, §23.10). The first chunk's marginal is its net, so a
  losing pair is refused rather than ranked.
* **Executable identity on the exit**: a bid counts only if it rests at the station,
  is region-ranged, is system-ranged in that system, or is numeric-ranged within
  stargate jumps (§23.6). `min_volume > 1` bids are excluded and counted.
* **Two generations pinned per row, older decides staleness**; a stale leg prices
  nothing (`_scan_pair`).
* **UNKNOWN never ranks**: a missing packaged volume refuses the cargo cap
  (`VOLUME_UNKNOWN`); a missing current system ends the scan `NO_ROUTE`.
* **Rejections are queryable** with fourteen reasons and a denominator.
* **Routing is local** and security is the *displayed* value (0.45 rounds to 0.5), the
  band haulers are ganked in.

---

## 2. The state of the data on this machine (measured 2026-09-04, read-only)

This is the part that decides whether the tab can show anything at all today.

| Store | State | Consequence |
|---|---|---|
| `data/depth/region=*` (five hubs) | Newest complete generation **2026-08-28 21:18–21:21 UTC**, ~7 days old. Rows per generation: Forge 314,473 · Domain 126,580 · Sinq Laison 80,270 · Metropolis 46,714 · Heimatar 38,024 (**606,061 rows, ~4 MB per region-date on disk**) | Every scan run now rejects all 20 pairs `STALE_BOOK` (`costs.book_staleness_minutes = 60`). The tab is empty by design until a sweep runs |
| `data/books/region=*` | Same dates | SPREADS prices nothing (`STALE`) |
| `data/bars/` | **The Forge only**, 4,052,335 bars, 17,305 types, **last bar 2026-08-19** (16 days old vs `screen.max_bar_age_days = 3`) | Every bar-fed gate is UNKNOWN; the SPREADS traded-average anchor is `STALE_AVG`. **No destination bars exist for Amarr, Dodixie, Rens or Hek**, so every maker-exit liquidation is `LIQUIDATION_UNKNOWN` except into Jita. `ingest-history --scope hauling` exists for exactly this and has never been run |
| `state.db.haul_profiles` | **0 rows** | The desk's ship picker is empty; `depth_bound()` therefore stored curves with a **zero cargo target** (capital-only bound), and the operator's saved page filter carries `cargo: 0.0` — "cargo is unbounded; capital is the only size cap" |
| `data/streams/paper_hauls.jsonl` | does not exist | No haul has been recorded, so neither prior in §23.7 has any path to becoming a measurement |
| `state.db.freight_quotes` | 0 rows | No PushX quote cached; the self-haul vs freight column has never been populated |
| `state.db.route_cache` | present | Fine |
| Daemon | No process found today (also noted in the active block) | Nothing refreshes. The system is a snapshot machine with nobody taking snapshots |

**Finding 0 — the biggest single optimisation is operational, not analytical.** The
engine cannot be better than the freshness of its inputs, and today its inputs are a
week to two weeks old. Running `daemon` (hourly secondary sweeps, daily history at
11:20 UTC) and `ingest-history --scope hauling` once are operator actions with zero code
and they unblock every other item in this document.

---

## 3. What the engine produces on real books (reproduced on lake copies)

Two complete five-hub generations exist on disk (2026-08-26 22:44 UTC and 2026-08-28
21:18 UTC, 46.5 h apart). Each was scanned with "now" pinned five minutes after its own
sweep so that staleness did not fire, with a **synthetic profile**: current system
Jita, 60,000 m³ usable cargo (a DST-class hold; no real profile exists), 250 M ISK
capital and exposure, 120-minute session, `highsec` routing, `immediate` exit, default
objective net ISK per active minute. Sources and destinations: the five configured hub
stations (20 pairs).

### 3.1 The denominator

| | 2026-08-26 | 2026-08-28 |
|---|---|---|
| pairs / (station, type) combinations / breakpoints priced | 20 / 141,108 / 142,606 | 20 / 140,464 / 142,301 |
| `MARGINAL_NET_NEGATIVE` | 118,367 | 117,684 |
| `DEST_DEPTH_SHORT` | 34,152 | 33,707 |
| `OVER_CAPITAL` | 21,488 | 21,552 |
| `DEPTH_TRUNCATED` | 10,168 | 10,193 |
| `OVER_CARGO` / `MIN_VOLUME_BLOCKED` / `VOLUME_UNKNOWN` | 1,059 / 799 / 40 | 1,029 / 835 / 40 |
| **plans ranked (net > 0 at some size)** | **831** | **961** |
| of which badge OK / THIN / BELOW | 423 / 252 / 156 | 513 / 270 / 178 |
| plans with quantity ≤ 5 units | 292 (35%) | — |
| wall-clock, five hubs, one profile | — | **11.4 s** (checklist B asked for this number) |

About 83% of every priced breakpoint loses money — the 98.8%-median-spread reality —
and yet **the page's normal state is not an honest zero: it is ~900 positive plans**,
because the engine ranks every pair with any positive size. The question is not
whether plans exist but which of them are real.

### 3.2 The top of the ranking (2026-08-28, net ISK per active minute)

| Item | Badge | Route | Qty | Capital | Net | ROI | Min | ISK/min |
|---|---|---|---|---|---|---|---|---|
| Sylramic Fibers | OK | Jita → Amarr (34 j) | 1,195,141 | 249.1 M | 23.6 M | 9.5% | 39 | 603 k |
| Capital Corporate Hangar Bay | BELOW | Jita → Dodixie (15 j) | 14 | 150.8 M | 12.2 M | 8.1% | 22 | 562 k |
| Pioneer Consortium Issue | BELOW | Jita → Dodixie | 2 | 137.0 M | 11.8 M | 8.6% | 22 | 542 k |
| Corax Navy Issue | BELOW | Jita → Amarr | 6 | 113.9 M | 18.8 M | 16.5% | 39 | 479 k |
| Phenolic Composites | OK | Jita → Dodixie | 257,975 | 209.1 M | 10.2 M | 4.9% | 22 | 470 k |
| Svipul | BELOW | Jita → Dodixie | 3 | 197.7 M | 9.7 M | 4.9% | 22 | 446 k |
| Terahertz Metamaterials | OK | Jita → Amarr | 18,430 | 237.9 M | 15.4 M | 6.5% | 39 | 394 k |
| Compressed Bitumens | OK | Dodixie → Jita (30 j) | 208,723 | 208.9 M | 11.7 M | 5.6% | 36 | 330 k |
| Compressed Kernite | OK | Amarr → Hek (55 j) | 550,000 | 104.6 M | 17.6 M | 16.9% | 58 | 302 k |

Two populations share the top: **bulk industrial materials** (Sylramic Fibers,
Phenolic Composites, Tungsten Carbide, metamaterials, compressed ore) walking several
levels on both sides, and **one-to-six-unit faction hulls and modules** where a single
high bid at the destination sits above a single ask at Jita. On 2026-08-26 the leader
was Fullerite-C70 Dodixie → Jita at 30.7 M net (15.0% ROI); it is absent from the
2026-08-28 top 25.

### 3.3 Persistence — a snapshot measured against the next snapshot

The tab's standing caveat is "a snapshot is not a tape". This is the first time the
size of that caveat has been measured on this lake. Same profile, plan identity =
(type, source station, destination station):

| Measure (08-26 → 08-28, 46.5 h) | Value |
|---|---|
| plans on 08-26 still a plan on 08-28 | **370 of 831 (44.5%)** |
| by badge: OK / THIN / BELOW | 42.8% / 43.7% / 50.6% |
| by size: quantity ≤ 5 / quantity > 5 | **33%** / **51%** |
| top 10 on 08-26: still a plan / still top 10 | 4 / 3 |
| top 25: still a plan / still top 25 | 12 / 5 |
| top 100: still a plan / still top 100 | 60 / 36 |
| survivors' net(08-28) ÷ net(08-26): p10 / p25 / median / p75 / p90 | 0.16 / 0.69 / **1.06** / 2.85 / 11.38 |
| the 08-26 top-25's net, re-priced on the 08-28 books (0 where gone) | **260.6 M → 94.7 M (36%)** |

Read carefully: this is a two-day gap, and a haul takes 20–60 minutes. The number the
operator needs is the **one-hour** decay, and it can only be measured with hourly
generations, which the daemon produces and the lake currently does not keep beyond a
per-day partition. But the shape is already informative: *half the list turns over in
two days, the top of the list turns over faster than the body, and one-to-five-unit
plans are the least durable.* A ranking that ignores this is ranking on the noisiest
component of the signal.

### 3.4 The mixed-cargo basket is dominated by the best single plan

`greedy_basket` fills the hold by **profit per m³** across all ranked plans:

| | 08-26 | 08-28 |
|---|---|---|
| basket: capital / net / **volume** / items | 250.0 M / **13.0 M** / **8.5 m³** / 20 | 249.9 M / **15.6 M** / **1.8 m³** / 20 |
| best single plan on the same capital | Fullerite-C70, **30.7 M** | Sylramic Fibers, **23.6 M** |
| plans withheld for overlap | 292 | 363 |

With a capital-bound profile the greedy fills 250 M ISK with twenty near-zero-volume
blueprints, formulas and insignia (0.01–1 m³ each) because they have the highest
ISK/m³, ends with a hold 0.003% full, and earns **42–66% of what the best single plan
earns**. It also spreads those twenty items over **four destinations** (Jita, Dodixie,
Amarr, Hek) without charging the four trips. The plan text says the basket is "shown
beside the best single-item plan, never instead of it", which is true, but as built it
adds no information on either real generation.

### 3.5 Loops exist in the data and nothing composes them

The ranking is one-way. A hauler's unit of work is a loop. Composing the best plan each
way from the 08-28 scan, with no new pricing:

| Loop | Net | Active min | ISK/min | Legs |
|---|---|---|---|---|
| Jita → Dodixie → Jita | 23.9 M | 57 | **418 k** | Capital Corporate Hangar Bay out, Compressed Bitumens back |
| Jita → Amarr → Jita | 32.7 M | 110 | 298 k | Sylramic Fibers out, Medium Compact Pb-Acid Cap Battery back |
| Hek → Amarr → Hek | 19.7 M | 103 | 191 k | Stabber Fleet Issue out, Compressed Kernite back |

Every one-way ISK/minute on the page silently assumes the return leg is free, or that
the operator ends the session at the destination.

---

## 4. Findings, ranked by expected effect on "find excellent hauling opportunities"

Each finding states what exists, what is wrong or missing, the evidence, and the
smallest change that addresses it. **Code changes are candidates**, not authorised
work; they touch §23 (an operator-facing surface and the ranking) and therefore need a
`plan.md` entry, fixtures first, and the ask-first rule for `hauling.py` and
`positioning.py`.

### F1 — Keep the lake fresh (operator action; no code)

*Evidence:* §2. *Change:* run `daemon` on this machine (Task Scheduler or a service),
or at minimum `sweep-books --secondary` before opening the tab and `ingest-history`
daily. Run `ingest-history --scope hauling` once (bounded at 400 types per hub by bid
notional) so liquidation stops being UNKNOWN at four of five destinations. Add one ship
profile (`haul profile add`) **before** the next sweep so `depth_bound()` stores the
cargo-conditioned depth rather than capital-only. Then set retention from the measured
~4 MB per region-generation; 24 generations a day for five hubs is roughly 0.5 GB a
day, which is the number §23.19 asked for.

### F2 — Score persistence, not a single snapshot (candidate; the highest-value analytical change)

*Exists:* generations are pinned per row; the lake keeps one partition per region-day
and `latest()` reads the newest complete sweep only. *Missing:* any use of the
**previous** generations. *Evidence:* §3.3 — 44.5% survival over two days, top-25 net
realised at 36%, small-quantity plans least durable. *Change:* keep every hourly
generation for N days; for each (type, source, destination) compute over the last K
generations the survival rate, the median realised net at the *current* quantity
re-priced on each older book, and the age of the bid levels consumed (the
`oldest_issued` column already exists and is displayed but unused in rank). Rank on
**persistence-weighted net per active minute** and show the unweighted figure beside
it. This is measurement from data the system already stores, needs no ESI change,
respects "completed data only" (every generation used is a complete sweep), and it is
the one thing EVE Flipper-class tools do not do at a station-executable quantity.
It also makes the §23.19 "stale-miss rate" a computed column instead of a two-week
diary.

### F3 — Fix the basket's objective, or scope it per route (candidate)

*Evidence:* §3.4. *Change, smallest:* choose the greedy key by the binding constraint —
profit per ISK when `capital / best_isk_per_m3 < cargo` (i.e. capital binds), profit per
m³ otherwise — and restrict a basket to **one destination** (one trip) unless the page
asks for a multi-stop route. *Change, right:* the chunks are already piecewise-linear,
so the two-constraint fractional knapsack over ≤ a few thousand chunks is a small LP;
numpy-only Lagrangian greedy (maximise `net − λ·capital − μ·volume`, bisect λ, μ) gets
within the last fractional chunk of optimal without a new dependency. The basket must
never out-earn its own parts' single-plan marginals, and a test should assert the
basket's net ≥ the best single plan's net on the same caps, or say why not.

### F4 — Compose loops and circuits from the plans already priced (candidate)

*Evidence:* §3.5. *Change:* after ranking, for every ordered station pair with a plan
each way, emit a "loop" row: net = out + back, minutes = out + back (the pickup leg is
charged once), capital = max(out, back) if the outbound proceeds fund the return
(they do, in `immediate` exit), and rank loops on the same objective. Extend to
3–5-stop circuits over the five hubs by exhaustive search (5! orderings, trivial). This
is composition over `HaulPlan` objects; the engine's arithmetic is unchanged, so no
golden fixture changes.

### F5 — Treat one-bid, one-unit exits as their own class (candidate; page control first)

*Evidence:* §3.2, §3.3 — ten of the top 25 are BELOW-badged 1–6-unit hulls or faction
modules; quantity ≤ 5 plans survive at 33% vs 51%. A plan whose exit is one bid at one
level (`dest_levels = 1`) disappears when one other player sells into it. *Change:*
a page/CLI filter "minimum quantity" and "hide BELOW", both default-off, and a visible
`single_bid_exit` flag on the row; when F2 lands, the persistence weight does this
honestly instead of by badge.

### F6 — The 30-minute default session hides every Jita ↔ Amarr plan (documentation, then a page count)

`default_session_minutes = 30`; Jita → Amarr high-sec is 34 jumps = 39.2 active
minutes at 55 s/jump + 8 min handling; Dodixie → Jita is 35.5. With the operator's saved
filter (30 min) the engine returns `OVER_TIME` for the two busiest pairs before
pricing a single type. This is correct behaviour with a silent effect. *Change:* show
the `OVER_TIME`/`OVER_JUMPS` pair count in the control strip and make the default
match the operator's real session once one is timed (checklist D).

### F7 — Make the destination priors per-hub, and label them as proxies (candidate)

`destination_share_prior = 0.25` is flat across hubs. The book already measures
`station_volume_share` per (type, region) on every sweep (`book_summary`), i.e. the
share of the region's resting bid volume at the hub station. That is a book-share
proxy for the flow share, not a flow measurement, and it must be displayed as one, but
it is per hub and per type, and Amarr (98.3% structure-resident bids) is not Jita.
Recorded fills still replace it, exactly as §23.7 says.

### F8 — Coverage: sources are five stations; 108 regions are cold (plan-level; §11 D3)

Token arithmetic (§3.2): a five-hub sweep is 912 requests ≈ 1,824 tokens per hour
against a 6,000-token self-cap per 15 minutes, so the budget is not the constraint.
Hub-to-hub is the most-competed arbitrage in the game (every listed competitor scans
it); hub-to-**non-hub** is where regional buy orders sit unfilled for days. The engine
already lets ranged bids anywhere in a swept region reach the hub station, so **exits**
beyond the hub are partly covered; **entries** are not (sources are hubs only). *Change:*
allow `extra_source_station_ids` (mirror of the destination list, same SDE resolution),
and consider a WARM sweep of the high-sec regions adjacent to the hubs (Lonetrek, The
Citadel, Essence, Tash-Murkon, Everyshore, Genesis) as a §11 D3 amendment with the
token cost stated.

### F9 — Route risk from the killmail lake, which is otherwise unused (candidate)

The destruction lead-lag hypothesis failed (§14, ρ = 0.027). The killmail data has one
use the plan never assigned it: **per-system hauler losses on the route**. `RouteFacts`
already carries the ordered system list; `ShipProfile` carries EHP and hull value that
nothing prices. A column "industrial/DST/freighter kills in these systems, last 90
days" (from `killmails.py`'s ingest, currently 0 bytes on disk) turns "route risk" from
a security count into an observed frequency. It stays a column, never a probability
multiplied into net, until the shadow period says how to weight it.

### F10 — Two instruments answer one question (housekeeping)

`cross-region` (§15) prices a fixed notional at the **regional** `book_summary` walk and
always quotes PushX; `haul scan` prices a breakpoint at the **station** ladders and
quotes PushX only with `--freight`. §20.4 already says §23 superseded the REGIONS tab.
Parking `cross-region` after the shadow period removes one number the operator could
quote that the other instrument would not reproduce.

### F11 — Pricing gaps that flatter every row equally (state on the page)

`annual_capital_cost_pct = 0.0`, so ISK-days cost nothing; no relist or broker fee on
the `immediate` exit is correct; hull risk is unpriced (F9). None of these reorders the
current list, but they are why net ROI reads higher than a wallet will.

---

## 5. Doc defects found on the way (fixed in this commit, per the CLAUDE.md rule)

* `CHANGELOG.md` inventory listed **`haul_ledger`** among `state.db` tables. No such
  table exists; the paper-haul ledger is the JSONL stream at
  `data/streams/paper_hauls.jsonl` (`paths.py` `paper_hauls`). Corrected.
* Checklist B asked for the five-hub scan wall-clock: **11.4 s** for 20 pairs,
  142,301 breakpoints, one profile, on a copy of the 2026-08-28 lake (§3.1). Recorded
  in `CURRENT_CHECKPOINT.md`; the gate row is the operator's to strike.

---

## 6. Recommended order

1. **Operator, no code:** F1 — daemon running, one ship profile, `sweep-books
   --secondary`, `ingest-history --scope hauling`. Then work checklist A–C as written.
2. **Operator decision:** promote F2 + F3 + F4 into `plan.md` as one §23 H-phase with
   fixtures first (they are composition and ranking over existing `HaulPlan`s and do
   not touch the walk, the fee model or any frozen rule), or park them. F5 and F6 are
   page controls and can ride along.
3. **During the shadow period (checklist D):** the hourly generations F1 produces are
   the dataset F2 needs; the two-week diary and the computed persistence column should
   agree, and where they do not, the diary wins.
4. **After H0 (keep/park):** F7–F9 only if the tab is kept.

What this document does **not** recommend: any change to the walk, the marginal-net
rule, the reachability doctrine, the fee model, the staleness budget, or any §10/§11
lock. Every candidate above is additive and measurable against the two generations
already on disk.

---

## Appendix — reproduction procedure

* Copy `data/{sde,depth,books,bars,streams,reports}` and `state.db` to a scratch
  directory; make a second copy with the `date=2026-08-28.parquet` depth and book
  partitions removed so `latest()` returns the 2026-08-26 generation.
* `EVESCREENER_DATA_DIR=<copy>`; `load_config(None)`; `scan_inputs(config, db)`;
  reload each region with `load_validated_depth(config, region, now=T)` where T is the
  newest sweep timestamp in that copy + 5 minutes.
* Profile: `ShipProfile.from_config(config, name="probe", cargo_m3=60000)`;
  `HaulProfile.from_config(config, ship=ship, current_system=30000142,
  capital_isk=2.5e8, max_exposure_isk=2.5e8, session_minutes=120,
  security_profile="highsec")`; `liquidity_attachment(config, db, depths, profile,
  now=T)`; `scan_hauls(..., route_cache=RouteCache(db, enabled=False), now=T,
  max_plans=100000)`; `greedy_basket(scan.plans, capital_isk=2.5e8, cargo_m3=60000,
  exposure_per_trade_isk=2.5e8, objective=profile.objective)`.
* Persistence: plan identity `(type_id, source label, destination label)`; survivors'
  net ratio over plans present in both; loops from the best plan per ordered pair.
* Wall-clock: `time` around the whole script on the 2026-08-28 copy — 11.36 s.
