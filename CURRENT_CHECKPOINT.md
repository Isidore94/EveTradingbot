# Current checkpoint

This file names the one active item, its working state, and the last
verification stamp. `plan.md` owns the roadmap; `CHANGELOG.md` owns history.

## Active item

**plan.md §23 — the personalized HAULING tab, phases H1–H4** (operator
authorization 2026-08-25, recorded as §17 D-33: *"build first, evaluate against
competitors and live gates afterwards"*). H5 and H6 are **out of scope**; H0 is
deferred to a keep/park decision after the shadow period.

**State: H1–H4 IMPLEMENTED + GREEN, REMEDIATED, and CLOSED OUT. THE TRACK'S
CODE IS DONE. NOTHING IS `LIVE_VALIDATED`.**
**Gate stamp:** `uv run pytest -q` → **1,084 passed, 7 deselected**, ruff check
+ format clean, `python -m evescreener selftest` → **12/12**.

**The next step is the checklist below, not more code.** It is unchanged and
owed in full.

**Three adversarial review passes happened.** The first, 2026-08-25 (§17 D-35),
reproduced twelve defects with concrete inputs; all twelve are fixed
fixture-first. The one worth knowing before you open the tab: a round trip that
loses money used to rank as a plan, because the marginal-net rule ran only from
the second breakpoint — so the page could not have produced an honest zero even
when an honest zero was the truth. The second, 2026-08-26 (§17 D-35a),
verified that work — eleven of twelve fixes real, including property-verified
equivalence of both rewrites (`curves_from_depth` over 60 randomized frames,
the `q_walk` shortcut over ~2,800 walks, zero differences in either) — and
found **one fix cosmetic in production**: `reduce_depth`'s station-first
closure did not forward `.knows`, so FIX 11b's behaviour never reached a real
sweep, and all three of its tests called the primitive directly. Six Low
residues came with it. All seven are closed, fixture-first.

The third, operator-directed pass on 2026-08-26 (§17 D-35b), reproduced three
more seams before changing them. A blank or off-graph current system had
labelled pickup UNKNOWN while ranking it as zero jumps; maker max-wait was not
selectable on either user surface and its rejected sizes could reappear in the
mixed basket; PushX replaced an extra destination with its region's configured
hub. All three are closed across engine, basket, CLI, desk and freight paths,
with seven regression tests. None changes a formula or detector.

**The checklist below is unchanged by any pass**: none of it was live
evidence, and no code session earns any of it. One line was added to section B
during the remediation, for the scan wall-clock on the real lake.

The remediation added **+1,323 lines and removed 96**; the closeout added
**+360 and removed 74** across 14 code and test files; the operator audit added
**+283 and removed 20** across four product and five test files before this
control-file reconciliation. The track's total is now roughly **10,743 lines**
against §23.15's ≤7,000-line target — the overage recorded as §17 D-34 grows
with each pass and remains stated rather than trimmed.

Everything below this item — the paper desk, §21, §22 and the consolidated
checklist — is unchanged and still owed in full. This track **adds** to that
list; it replaces nothing on it.

### Operator report 2026-08-26 — the HAULING tab returned nothing

Two causes, both data and neither a formula.

1. **The stargate map was never loaded on this machine.** `sde_stargates` and
   `sde_npc_stations` held 0 rows while `meta.sde_build` read 3475087, because
   those tables landed after the operator's first `sde` run and the same-build
   no-op declined to fill them. `RouteGraph` was therefore empty and every scan
   ended `NO_ROUTE` on "current system 30000142 is not in the stargate graph".
   Reloaded from the local bundle: **13,978 gates, 5,210 NPC stations, 0
   unresolved**. The no-op now also demands non-empty tables, with two
   regression tests (`tests/test_sde_map.py`).
2. **No depth generation exists on disk.** `data/depth/` is empty and
   `data/books/` holds only Forge, dated 2026-08-20/21. With routing fixed the
   scan reaches 20 station pairs and rejects all 20 as `STALE_BOOK`. A
   `sweep-books --secondary` is what fills it; the desk shows staleness rather
   than curing it (§19.2), so this is an operator run, not a GUI action.

3. **With both fixed, the first scan against a real five-hub lake crashed.**
   `TypeError` in `_oldest_issued`: parquet's nullable `issued` columns come
   back from pandas as float NaN, NaN is truthy, and 556 of the Forge's 314,793
   depth rows carried one. Normalized at the loader (`books.issued_stamp`) with
   two regression tests. **This is the first defect any of the three review
   passes missed, and it was found by running the thing on real data** — every
   fixture had written a stamp or an explicit None.

The scan now runs: `haul scan --from Jita --to Amarr` over 20 station pairs
prices **29,211 candidates**, rejects **46,966** (MARGINAL_NET_NEGATIVE 23,390,
DEST_DEPTH_SHORT 12,820, OVER_CAPITAL 5,186, DEPTH_TRUNCATED 5,115), and ranks
15 plans led by ~697k ISK/active-minute at 250M capital.

Gate stamp after these fixes: `uv run pytest -q` → **1,088 passed, 7
deselected**, ruff clean, `selftest` **12/12**.

None of this is live validation of anything: the numbers above are arithmetic
over one swept moment, and not one of them has met the client. The checklist
below is untouched.

### Read this before you open the tab

**Nothing in this build has been checked against the game.** Not one route has
been flown, not one ladder has been compared to a market window, not one
liquidation estimate has met a real sell order. The whole track is arithmetic
over swept data and a graph read out of the SDE, and arithmetic is exactly the
part a machine cannot validate about itself.

**The page's normal state may be a short list or an honest zero, and that is
the system working.** The measured Forge reality has not changed because a new
tab reads it: the median spread across two-sided types is **98.8%**, only ~932
Forge types trade inside a 5% spread at all, and of **151,113** hub pairs
measured at 0.25B, **10–14** cleared real freight and tax (§17). A hauling
scanner that returned a full screen of plans against that book would be
telling you something false. Judge the tab on whether its refusals are
*correct*, not on how many rows it prints — the rejected view with its reason
histogram is the more informative half of the page.

**What the tab measures, and what it assumes.** Getting in is measured: the
source and destination walks are arithmetic over depth that was actually
swept. Getting out is assumed: `destination_share_prior` (0.25) and
`capture_share` (0.05/0.15/0.35) are **operator priors**, because ESI's
regional history carries no station split and no computation turns that into a
measurement. They are labelled ASSUMED on every surface, and they become
measurements only when your own recorded fills can replace them.

**This checklist — A through E — is now the ONLY thing between this tab and
use.** Every item on it is an **operator action**; none of it can be
self-certified, which is the entire point of the ladder. Nothing below has been
altered by the remediation or the closeout, and nothing below has been earned.
Work it in order: the route spot checks, the ladder spot checks, the ranged-bid
sale, the `issued`-tracking observation, the depth-size and scan wall-clock
measurements, the broker overrides, the two-week shadow, and then — and only
then — the H0 keep/park comparison.

### A. The map, and the route it draws (H1a)

- [ ] **Ten in-game route spot checks.** Set destination in the client for ten
      of the tab's routes and compare the client's jump count against the
      page's. Include **at least one route through a 0.45–0.49 system** — the
      band where displayed security (0.5, high-sec) and raw security (0.449,
      not) disagree, and the band a hauler is actually ganked in. The engine
      rounds half-up on purpose; the client is the authority on whether that
      is right.
- [ ] **Confirm the Jita → Amarr readings against the client**: this build
      measures **11 jumps** through **Ahbazon (0.4)** on the shortest profile
      and **34** on high-sec-only, from SDE build **3478781**. If the client
      disagrees, the map is stale or the profile is not what you think it is.
- [ ] **Confirm one avoid-list system is genuinely avoided**, and that removing
      the only high-sec path produces **UNKNOWN with a reason** rather than a
      longer route through low-sec.

### B. The ladders (H1b)

- [ ] **Ten in-game quote/depth spot checks.** Open the market window for ten
      of the tab's items at the named station and compare the first few price
      levels and their volumes against the detail drawer's ladders. What is
      being checked is the **station** reduction: the page claims these are the
      orders you could hit standing *there*.
- [ ] **Sell one unit into a ranged bid** — one of them **resting in a player
      structure** — and record **where the goods left from and where the ISK
      landed**. This is the single most load-bearing unverified claim in the
      track: the reachability doctrine says a ranged bid reaches out to you and
      the seller never docks at the buyer's station. If that is wrong, every
      exit number on the page is wrong in the optimistic direction.
- [ ] **Track one liquid `order_id` across sweeps** and watch whether `issued`
      moves when the order is repriced. The page currently labels the column
      "last placed **or repriced** (unverified)" because nobody knows. One
      observation settles it, and the answer belongs in §23.6.
- [ ] **Record the measured scan wall-clock on the real five-hub lake**, beside
      the depth-size measurement below. The remediation measured a synthetic
      100,000-row generation: the depth index fell from **9.0 s to 0.5 s** per
      region and a one-pair scan from **18.8 s to 1.4 s**, which extrapolates
      to roughly ten seconds for five hubs — but a synthetic book has one level
      shape and the real one does not.
- [ ] **Record the measured depth size per five-hub generation** — rows and
      bytes — into `plan.md` §17, then set retention from that number rather
      than from a guess. `data/depth/region=*/date=*.parquet` after a
      `sweep-books --secondary`. The killmail table (§7) is the standing
      warning about what "trivial next to the market lake" is worth as an
      estimate.

### C. The costs (H2, and §22 S6 restated)

- [ ] **Transcribe the actual in-client broker fee at two hubs** into
      `[costs].broker_fee_overrides` — Jita 4-4 and one other, which are owned
      by **different NPC corporations** and therefore charge you different
      rates. Until then every hub is priced at the skill-derived base, and the
      maker column is wrong by exactly that difference. (This is §22 S6's owed
      gate; the hauling maker scenario now consumes the same override, so it is
      owed twice over.)
- [ ] **Reproduce one hauling row's arithmetic against a real round trip**:
      what you paid, what the wallet showed after tax, and what the report's
      audit block predicted. Tolerance is §12.3's ±0.5% of notional, stated
      before any fill and not to be adjusted after seeing the result.

### D. The tab itself — a two-week shadow (H2–H4)

- [ ] **Two weeks of shadow use, with the decision recorded before you
      undock.** For each haul you take: `haul record open` with a thesis and a
      like tag *before departure*, and `haul record close` with what you really
      got *after arrival*. The forecast error is the number that matters, and
      it only exists if the open is recorded first.
- [ ] **Log the stale-miss rate**: how often the destination bid you priced
      against was gone when you docked. Nothing in this system bounds that —
      it is the "a snapshot is not a tape" caveat, and the shadow period is
      how it acquires a number.
- [ ] **Log the minutes.** The default objective ranks on net ISK per active
      minute using `seconds_per_jump` and `handling_minutes` from your ship
      profile. Time one real haul end to end and correct the profile; a
      ranking built on a wrong minute is a ranking of the wrong thing.
- [ ] **Pass on something deliberately**, with a dislike tag, and confirm the
      refusal lands in `data/streams/paper_hauls.jsonl`. Then attempt one
      **bad** pass (a misspelled tag) and confirm the refusal is recorded with
      the attempted tags — §22 S7's rule, now enforced here too.
- [ ] **Use `along_route` mode on a trip you were making anyway** and confirm
      the detour it charges matches what the trip actually cost you in extra
      jumps.

### E. Then, and only then: H0 — keep or park

- [ ] **Compare the tab against EVE Flipper, EVE Profits and ISK Scout**, on
      the same day, on your own hubs, on the four questions in §23.20: does any
      of them price a **quantity** against **executable** depth at a named
      station; does any charge **your** route, ship and session; does any say
      **why not**; does any distinguish **measured** from **assumed**.
- [ ] **Decide keep or park, and write the decision down.** **Park is a real
      and expected outcome.** EVE Flipper is at v1.6.14 and already walks VWAP
      depth, trades multi-hop routes, arbitrages contracts and backtests on
      paper. If a live third-party site does this job better, parking is
      cheaper than maintaining a worse copy of it — and the two weeks of shadow
      evidence is what makes that judgement something other than taste.

## Previous item — the paper desk's fill models (plan.md §12.2 amended 2026-08-21, §17 D-32)

**The paper desk's fill models** (plan.md §12.2 amended 2026-08-21, §17 D-32)
— see the ACTIVE section below — sitting on top of **the consolidated
live-validation gate** (plan.md §17 D-1), which covers the desk and the
operator setup engine as well (plan.md §19, checklist section I).
Everything is **IMPLEMENTED + GREEN**. Nothing is `LIVE_VALIDATED`, and
nothing may be promoted to real ISK until the checklist below is worked
through.

**The system already has an answer to the question it was built for, and it is
a negative one.** Measured on the full lake — 3,116,848 bars, 2,654 tracked
types, 108,441 setup instances:

- the setup class is **NOT PLAUSIBLE at every horizon**, on **friction rather
  than direction** (10-day gross **+2.80%** at a 55.7% win rate against
  **14.7%** round-trip friction before tax);
- the destruction lead-lag effect **does not survive** (ρ=0.027 on 473,606
  observations against a 0.10 threshold);
- of 6,672 fillable Forge types, only **27** have round-trip friction under
  1%, and only **2 of 315** measured tracked types are tight enough for the
  measured edge to survive costs;
- the one measurably positive finding is **cross-region**: 10 of 151,113 hub
  pairs clear real PushX freight and sales tax at 0.25B, best **+13.63%** —
  and that is a simultaneous-snapshot number for a haul that takes days.

All of it is in `plan.md` §17 and `data/reports/viability-*.md`.

That answer is *provisional on the cost model being right*, which is exactly
what gate E measures. Do that one first.

Every item on it is an **operator action**. The build cannot self-certify: the
whole point of the ladder is that a machine's confidence in itself is not
evidence.

## ACTIVE — the paper desk, fill models (plan.md §12.2 amended, §17 D-32)

**Operator directive 2026-08-21**, from an attempt to take a paper trade on
the desk: *"when I go to paper trade it's just a mess and it doesn't work"*,
plus a request to fill at the midpoint.

**State: IMPLEMENTED + GREEN. Nothing here is LIVE_VALIDATED.**
**Gate stamp:** `uv run pytest -q` → **850 passed, 7 deselected**, ruff check
+ format clean.

What it was, in order of what actually blocked the trade:

1. The only book on disk was **25 hours old and pre-R1** (18 columns, no
   `exec_*`), so `book_quote()` refused every fill twice over. A fresh sweep
   (411,876 orders, 412/412 pages, 19,148 types, complete, 2026-08-21T19:42Z)
   replaced it.
2. The form **prefilled a price the ledger would refuse**, reading the lake
   directly instead of going through `book_quote()`. It now prices through the
   ledger's own function, shows `UNKNOWN` and the reason up front, and greys
   the button rather than accepting a full form and refusing the submit.
3. The **notional was free-entry** where only the three configured tiers are
   ever accepted.
4. **Two entries in the same second collapsed into one position** — the second
   `open` replayed over the first, so a position on disk was uncloseable and
   invisible to the verdict tracker. Ids now carry a sequence suffix, and a
   legacy collision is recovered on read as `…#2`.
5. PAPER read `verdict['reason']` and `exit_source`, neither of which exists;
   every verdict rendered as "no reason recorded" and every close as "book".

The **mid fill was declined with the reason stated**, and the operator chose
the maker model instead. `fill_model` is now recorded on every open, mark and
close; taker is unchanged and remains the default; maker posts one tick in
front of the executable quote, pays the per-station broker fee on both legs,
and is stamped `fill_assumed`. The two populations are scored apart under the
same frozen §12.4 rule.

### Owed live gate — the maker assumption is the whole risk

Nothing below can be self-certified: the ledger cannot tell whether a posted
order would ever have filled, and no number in this system bounds it.

- [ ] Post one real buy order at the price a maker paper entry recorded, and
      record **whether it filled at all, and how long it took**. A maker paper
      record with no fill-rate evidence behind it is exactly the self-flattery
      §12.2 exists to prevent.
- [ ] Record how many times it was undercut before filling, and the broker fee
      each relist cost — undercut risk and waiting time are unmodelled (the
      same limit §17 D-31 states for SPREADS).
- [ ] `paper real-fill` a taker entry and a maker entry against prices really
      paid, and check both against the ±0.5%-of-notional tolerance (§12.3).
- [ ] Confirm on the in-game market that `exec_price` for a tracked type
      matches the best quote reachable at Jita 4-4 — the maker price is
      derived from it directly.

## ACTIVE — plan.md §22 remediation track (operator-authorized, post-Sol)

An independent adversarial review found defects in the §21 remediation itself
and older ones §21 did not reach. **All §22 phases are now IMPLEMENTED + GREEN,
and none is LIVE_VALIDATED.** Every §21 owed live gate, every §22 owed live
gate, and the consolidated checklist below remain owed in full.

**Gate stamp:** `uv run pytest -q` → **825 passed, 7 deselected**, ruff check +
format clean, `selftest` **12/12**.

| id | finding | disposition | state |
|---|---|---|---|
| **S1** | `Expires` did not fail closed on the 304/200 paths | **CONFIRMED** (2 requests where 1 was correct) | **IMPLEMENTED + GREEN** |
| **S2** | Regional depth on an executable quote; pricing bypasses the validator | **CONFIRMED** | **IMPLEMENTED + GREEN** |
| **S4** | Pooled exploratory lead-lag rendered as if H2 were tested | claimed | **NEXT — reproduce first** |
| **S5a** | `friction_breakdown` 100% where 66.667% is correct | **CONFIRMED** | **IMPLEMENTED + GREEN** |
| **S3** | Worker reads page state; same-input key change schedules no follow-up | **CONFIRMED** | **IMPLEMENTED + GREEN** |
| **S5b** | `effective_samples` returned 3 where at most 2 holds | **CONFIRMED** | **IMPLEMENTED + GREEN** |
| **S5c** | Aging adverse evidence improves its rank | **CONFIRMED** | **IMPLEMENTED + GREEN** |
| **S5d** | Two-observation median ranked as print-resistant | **CONFIRMED** | **IMPLEMENTED + GREEN** |
| **S6** | `broker_fee_overrides` always empty in production | **CONFIRMED** | **IMPLEMENTED + GREEN** |
| **S7** | Validation failures raise before any refusal is recorded | **CONFIRMED** (paper ledger) | **IMPLEMENTED + GREEN** |
| **S8** | Import guard too narrow; TOP figures unversioned | **CONFIRMED** | **IMPLEMENTED + GREEN** |

### S2 owed live gate

- [ ] After the next `sweep-books`, confirm on a liquid type that
      `depth_fill_price_0` is consistent with `exec_price` — a buy fill at or
      above the executable ask, a sell fill at or below the executable bid.
- [ ] Confirm `exec_reachable_volume_share` is high at Jita 4-4 and visibly
      lower for a type whose bids rest elsewhere.

### S8 owed live gate

- [ ] Run the TOP measurement report on the real lake and commit its output
      under `data/reports/`, so §20.3's prose can cite a dated artefact rather
      than a floating number.

### S6/S7 owed live gates

- [ ] **S6** — transcribe the actual in-client broker fee at Jita 4-4 and one
      secondary hub into `[costs].broker_fee_overrides`. Until then the list is
      empty and every hub is priced at the base rate.
- [ ] **S7** — attempt one bad pass on the real desk and confirm the refusal
      appears in `paper.jsonl` with the attempted tags.

### S5b/c/d owed live gates

- [ ] **S5b** — re-run the backtest and record `n_eff` against `samples`. If the
      ratio is far from `1/horizon`, the instance set is more clustered than the
      crude correction assumes.
- [ ] **S5c** — unobservable until real closed trades exist.
- [ ] **S5d** — chart five of the 157 newly-UNKNOWN names and confirm they
      genuinely trade too sparsely to carry a weekly return.

### S3 owed live gate

- [ ] Switch the SPREADS hub while a computation is running; confirm the list
      that settles matches the hub finally selected.
- [ ] Let the refresh timer fire mid-computation; confirm the page ends up
      showing the newer data rather than the older.

### S5a owed live gate

- [ ] Re-run the backtest on the real lake and compare `total_friction_pct`
      against the headline **14.7%** in §17. If it differs materially, §17's
      figure is a snapshot of the old additive formula and must be **labelled**
      as historical rather than replaced.

### S4 owed live gate

- [ ] Re-run the lead-lag study on the real lake and record how far the
      permutation p-value sits from the naive one. If they agree closely, the
      dependence is weaker than assumed — itself a finding.

### S1 owed live gate

- [ ] Against live ESI, confirm a response with no `Expires` produces a wait
      rather than an immediate refetch.
- [ ] Confirm `expiry_unknown` is **rare** in the telemetry ledger. If it is
      common, our header parsing is wrong rather than the server being silent.

## §21 remediation track — ALL PHASES IMPLEMENTED + GREEN, NONE LIVE_VALIDATED

R1 through R8 are code-complete and the offline gate is green:
**717 passed, 7 deselected**, ruff check + format clean, `selftest` **12/12**.

**That is not the same as correct.** Every phase carries an owed live gate, and
none has been run. The build cannot self-certify — a machine's confidence in
itself is not evidence — so nothing here is `LIVE_VALIDATED` and nothing may be
promoted to real ISK. The consolidated live-validation checklist further down
is untouched and still owed in full. **§20.3 resumes only when the operator
says so.**

### Owed live gates, by phase

| phase | owed |
|---|---|
| R1 | After a sweep, confirm `exec_location_id` for liquid types is the station actually traded at, and that a structure-resting best bid is flagged rather than priced. |
| R2 | Skip an `ingest-history` run and confirm the screen reports bars stale while the book reads fresh, with gates UNKNOWN. |
| R3 | Re-run the backtest on the real lake: verdict unchanged, `n_eff` materially below `samples`, no cell below −100%. |
| R4 | Transcribe the client's actual broker fee at two hubs and confirm `broker_fee_at` reproduces them; verify the order-modification fee before anything consumes `relist_cost_unverified`. |
| R5 | **The confirmatory H2 run does not exist.** The doctrine cohort is declared but not measured; all lead-lag evidence remains exploratory. |
| R6 | Unobservable until real closed trades exist — every LEARNING row is still UNVALIDATED. |
| R7 | Switch the SPREADS hub mid-computation and confirm the list matches the hub finally selected; close the window mid-compute and confirm no `RuntimeError`. |
| R8 | Confirm a second region prices against its own averages; confirm a missing `Expires` produces a wait against live ESI. |

## Superseded heading — plan.md §21 remediation track (operator-authorized 2026-08-20)

An adversarial repository review found defects in how the order book was
reduced and how snapshots were validated. The operator authorized this track
to take priority over the queued §20.3 work. **§20.3 is paused, not
cancelled**, and the consolidated live-validation checklist below is untouched
and still owed in full. Nothing in this track retracts a measurement.

**One phase per session.** A later phase is never started because it is
adjacent.

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

### R1 owed live gate

Nothing R1 produces has been checked against a live client. After the next
`sweep-books`:

- [ ] Confirm `exec_location_id` for a handful of liquid Forge types is the
      station the operator actually trades at (expected: Jita 4-4, 60003760).
- [ ] Confirm a type whose region-wide best bid rests in a structure is
      flagged `exec_is_structure` or excluded, rather than priced.
- [ ] Confirm the SPREADS page prices rows again once a complete sweep exists.

### R1 operational consequence — read this before the next run

**The stored Forge book (35,858 rows) now prices nothing.** It predates the
executable-quote contract and genuinely cannot say where its quotes rested, so
`load_validated_book` reports it UNKNOWN. This is missing data reported
honestly, not a regression. `sweep-books` restores pricing.

## Superseded sub-track: plan.md §20 — the daily desk

The operator asked (2026-08-20) for a consolidated daily-review page, alerts
with ntfy, top performers, region trading and a spreads tab. That is recorded
as **plan.md §20**, phased, one per session, each gated green.

| phase | state |
|---|---|
| §20.1 DESK — consolidated review page | **IMPLEMENTED + GREEN** |
| §20.2 SPREADS — maker / station trading | **IMPLEMENTED + GREEN** (§17 D-31) |
| §20.3 TOP PERFORMERS 1w / 1m | **IMPLEMENTED + GREEN** |
| §20.4 REGIONS — cross-region hauling | **NEXT** |
| §20.5 ALERTS + ntfy | not started — SETTINGS form landed early, delivery not built |

**Setups are out of scope for this track** by operator decision; the action on
every surface is a paper trade.

**§20.2 is the one with an analytical claim behind it.** §17's NOT PLAUSIBLE
verdict was measured on a *taker* strategy — cross in, cross out, 14.7%
round-trip friction against a +2.80% gross edge. A maker posts both sides and
**collects** the spread, so the 98.8% median Forge spread that killed the
taker is the maker's revenue. `books.spread_view()` and
`CostModel.sell_proceeds(maker=...)` already exist. The open question the tab
must not paper over is **fill plausibility and undercut risk**, neither of
which any existing measurement covers.

## Verification baseline (2026-08-20)

- **Latest — §21 R1, executable book identity and validated snapshots:**
  `uv run pytest -q` → **602 passed, 7 deselected**, ruff check + format clean.
  The reduction preserves `location_id` and buy-order `range`; a spread is now
  the pair executable at one named venue, with the region-wide extrema kept
  visible as diagnostics. Partial sweeps are quarantined and `latest()` returns
  the newest *complete* snapshot. `load_validated_book()` is the single
  contract deciding completeness, executability and staleness.
- **SPREADS and SETTINGS (plan.md §20.2, §17 D-31):**
  `uv run pytest -q` → **583 passed, 7 deselected**, ruff check + format clean.
  The maker read of the book, anchored to the traded average because 39.7% of
  two-sided Forge books have a bid under half of it. 1,590 Forge names carry a
  positive net maker edge on a simulated-fresh book (median +13.0%); on the
  real 121-minute-old sweep the page correctly showed an honest zero.
  **Owed live gate:** run `sweep-books`, then confirm the SPREADS list against
  the in-game market for a handful of names — no row on this page has ever
  been checked against a real order book, and fill probability is unmodelled.
- **DESK, the consolidated review page (plan.md §20.1):**
  `uv run pytest -q` → **566 passed, 7 deselected**, ruff check + format clean.
  FOCUS/BOARD/SCANNER as tabs on the left, the one chart on the right, nothing
  removed from the rail. The window now owns the single `ChartPanel` and docks
  it into the visible host, so §19's one-chart rule holds literally across two
  pages. The chart opens on the whole series. Presentation and composition
  only — no detector, formula or verdict rule moved.
- **The chart draws range candles (plan.md §19.2, §17 D-30):**
  `uv run pytest -q` → **560 passed, 7 deselected**, ruff check + format clean.
  Body = the day's measured low→high, notch = the average, colour = the move
  against the previous average; no `open` is synthesized. A conventional
  open→close body was refused **on measurement**: yesterday's close falls
  outside today's range on **55.7%** of 4,034,697 bars and **69.0%** of
  tier-OK bars, so it would hang off its own wick on most bars. The chart now
  opens at 120 bars with a 60/120/250/all selector. A composite index carries
  no intraday range (`high == low == close`), so MARKET draws FORGE and
  FORGE-EW as level lines rather than zero-height candles, and the canvas now
  expands instead of sharing its height with its own title. Presentation only — no
  detector, formula or verdict rule moved, so nothing on the live-validation
  checklist below changes state.
- **The relative measurable gate (plan.md §13.2 amended, §17 D-29):**
  `uv run pytest -q` → **556 passed, 7 deselected**, ruff check + format clean.
  39 types (1.33%) now read UNKNOWN rather than carrying a float-noise risk
  unit; max abs RRS falls 9.05e11 → 1.19e7 with the median unchanged at +3.18.
  Backtest 145,655 instances, still NOT PLAUSIBLE at every horizon. The digest
  produced the same 25 candidates.
- **Latest — FORGE's outlier clamp and the desk's threading contract (plan.md
  §17 D-22…D-28, §19.2 amended):** `uv run pytest -q` → **544 passed, 7
  deselected**, ruff check + format clean, `selftest` → **12/12**. FORGE now
  runs 1,000 → 981 over 415 bars with a 0.34% median daily move and a
  `power_index` of −3.28; the desk opens in **8.6 s** against 2,947 tracked
  types and 4,052,335 bars, with a 15 ms timer tick. The digest finds 25
  candidates where the broken RRS gate had reported an "honest zero".
- **Latest — deployed to the operator's Windows desk; compatibility-date guard
  salvaged (plan.md §17 D-21):** `uv run pytest -q` → **509 passed, 7
  deselected** on Windows (Python 3.12.13, uv 0.12.3), ruff check + format
  clean, `python -m evescreener selftest` → **12/12** against the real
  `config.toml`. Two changes only: the new `compatibility date` check, and a
  Windows path-escaping fix in the parity test that had been failing the
  offline gate on this machine and nowhere else. Config is Accounting **V** /
  Broker Relations **IV** → sales tax 3.375%, broker fee 1.300%; the Discord
  webhook is deliberately empty, so delivery reports `unconfigured`.
- **Latest — the desk, indices, setups and the learning loop (plan.md §19,
  §17 D-14…D-20):** `uv run pytest -q` → **509 passed, 7 deselected**, ruff
  check + format clean, `python -m evescreener selftest` → **11/11**. The desk
  was opened against the real data directory: all eight pages rendered on
  2,001 tracked types, with a 223-minute-old book correctly shown as STALE and
  every friction column correctly UNKNOWN because of it. LOC now **27,399**
  (18,049 product / 1,435 vendored / 7,880 tests / 35 launcher); the desk plus
  its tests plus the launcher is **3,477** of the 12,000 the third directive
  authorized for it.
- **Earlier the same day — operator workflow port (plan.md §18, §17 D-13):**
  `watch`/`brief`/`board` and the digest watchlist section landed;
  `uv run pytest -q` → **358 passed, 7 deselected**, ruff check + format
  clean, selftest 7/7. LOC now 18,296 (11,575 product / 1,435 vendored /
  5,286 tests). The baseline below is the v1 build it extends.
- `uv run pytest -q` → **337 passed, 7 deselected** (network).
- `uv run pytest -m network -q` → **7 passed in 139 s** against real endpoints:
  real history against the frozen bar contract, a second call skipped as
  still-fresh, a full 415-page Forge sweep inside the token self-cap, the
  telemetry ledger, a real digest, a real paper open priced off a live book,
  one real day of EVE Ref killmails, and a live PushX quote.
- `uv run ruff check .` → clean. `uv run ruff format --check .` → clean.
- `python -m evescreener selftest` → 7/7 checks passed.
- **17,134 LOC** at the v1 build (10,751 product + 1,435 vendored + 4,948
  tests) — superseded by the count above; the budget exception is recorded in
  §17 D-9 and D-20.
- **Environment note for a Linux box only:** PySide6 needs system libraries
  that a bare container lacks (`libegl1 libgl1 libxkbcommon0 libdbus-1-3
  libfontconfig1`, after `apt-get update`). The operator's Windows machine
  needs none of this — `uv sync --extra gui` is the whole install there.

## Deployment on the operator's desk (2026-08-20)

The system now lives at `C:\Users\Aaron\EveTradingbot` on the always-on
mini-PC, in its own directory and its own `.venv`, fully isolated from
TradingBotV3 as §11 D1 requires. Nothing in this deployment reads, writes or
schedules anything under `C:\Users\Aaron\TradingBotV3` or `C:\TradingBotData`.

**Standing, and what is still owed:**

| Step | State |
|---|---|
| `uv sync --extra dev --extra gui` | Done. Python 3.12.13, uv 0.12.3, PySide6 6.11.2. |
| `config.toml` | Written, gitignored. Accounting **V**, Broker Relations **IV** → sales tax 3.375%, broker fee 1.300%. Discord webhook deliberately **empty**, so delivery reports `unconfigured` — correct behaviour, and gate D still owes the real webhook. |
| `pytest -q` / `ruff` / `selftest` | **515 passed, 7 deselected**; ruff check + format clean; selftest **12/12**. |
| `sde` | Build **3475087** — 52,863 types, 2,106 market groups, 8,490 systems. |
| `census` | **RUNNING** at the 150 req/min self-cap, ~2h10m expected. Notably it is already past **2,363** history requests — the exact point where the pre-D-12 circuit breaker latched open — with `history_missing` still empty, so the D-12 fix holds against live ESI rather than only against fixtures. |
| `anchors` | Run. 8 posts in the feed, 1 new candidate. **See the duplicate below.** |
| `census` result | **COMPLETE, 2h07m.** 19,150 active types · 18,946 fetched · **201** no-history 404s · 3 failed · **4,052,335 bars** across 17,638 types. Zero 429, zero 420, no breaker trip. Membership: OK **1,633** (9 price-pinned), THIN **1,314**, below floor 16,203 → tradeable universe **2,947**. |
| `sweep-books` | **COMPLETE.** 413/413 pages, 412,972 orders, 19,149 types, **0 duplicate `order_id`s**, structure volume share 14.0%. |
| `ingest-history` | **2,947 requested, 2,947 skipped-fresh, 0 fetched.** The never-fetch-before-expiry rule doing exactly its job on a lake an hour old. |
| `backtest` | **COMPLETE. NOT PLAUSIBLE at 5, 10 and 20 days**, reproduced independently on this machine's own lake: 2,947 types, **125,254** instances, friction 61.9–62.2% against gross edges of the same order. The banner is restored on MARKET and SCANNER. |
| `digest --dry-run` | **COMPLETE.** Banner present, honest zero ("Nothing clears costs today"), and all 50 watchlist names render — PLEX correctly saying it has no bars, which is §17's known Forge-cannot-price-PLEX finding showing up on its own. |
| `backtest` | **NOT RUN on this machine, and it must be.** `data/` is gitignored, so the previous build's `reports/backtest-*.json` did not come with the clone, and `verdict_banner` returns an **empty string** when no stored verdict exists. Until `backtest` runs here, MARKET and SCANNER show **no NOT-PLAUSIBLE banner at all** — the system's own headline finding is invisible on the desk. Run it after `ingest-history`; it reads the lake and costs no ESI traffic. |
| Killmail backfill | **SKIPPED** by operator decision (1.3 GB, and §14's lead-lag already returned negative). |
| The desk | Constructed offscreen against the **real** data directory: all eight pages built, `window.refresh()` fed them one local read, all eight selected without error. `book_age_minutes` is `None` and the book renders **STALE**, which is correct with no sweep yet. This is a smoke test of the Qt stack on this machine, **not** checklist I — that is still the operator's to walk. |
| Desktop shortcut | `EVE Screener Desk.lnk` → `.venv\Scripts\pythonw.exe launch_gui.py`, working dir the repo. |
| Daemon task | Registered as **`\EveScreener daemon`**, currently **Disabled**. Logon trigger for this operator, `PT2M` delay, action `uv.exe run python -m evescreener daemon` in the repo directory, `MultipleInstancesPolicy=IgnoreNew`, no execution time limit. **Enable it once the bootstrap finishes** — while the census runs, a logon would start a second independent ESI consumer against one IP. Distinct from all three TradingBotV3 tasks in name, executable and working directory. |

### Defects found during deployment

#### 1. The §11 D4 seed watchlist never reaches a fresh install

`config/anchors.jsonl` now holds *Patch Notes - Version 24.01* **twice**, on
2026-08-19 and 2026-08-20, with the **same** `source` URL. `patchnotes.py`
dedupes on `(date, label)` and on date-occupancy, but never on the article
URL, so an article CCP re-dates is appended again as a second candidate for
what is one real event. The daemon runs this watcher **daily**, so it will
keep happening.

It is not urgent and it is not silently harmful: candidates are inert until
`confirmed: true`, and growth is bounded to one row per date. But it lands
directly in gate C, where the operator confirms anchors by hand — and if both
rows were confirmed, the signal layer would anchor twice on one patch.

**Left for a decision rather than patched here**, because "what counts as the
same anchor event" is a plan-level question about a signal-layer input, not a
janitorial fix. The obvious answer is to add `source` to the dedup key and
prefer the newest date for a given URL.

`universe.seed_watchlist` exists, reads `config.universe.watchlist`, and
resolves each of the 50 D4 names against the SDE — and **nothing in `src/`
calls it.** The only callers are in `tests/test_universe_census.py`. So on any
fresh install `watch list` is empty, and with it:

* §18.2's "every watchlist name renders in **every** digest" renders nothing;
* the desk's FOCUS page starts empty;
* gate I's "check which of my watchlist hulls landed in the THIN tier" has
  nothing to check.

The roster has been **seeded operationally**, not by a code change: the 50
names were added through the documented `watch add` path, one call each, all
50 resolving against the SDE with zero unresolved. They are therefore
operator-owned entries, reachable by `watch remove` like any other, and
`config.toml` is unchanged.

**Wiring the seeder into a production path is left as a decision**, because it
has an invariant edge: if the universe refresh re-seeds, a name the operator
deliberately `watch remove`d would come back, which is the never-auto-removed
rule failing in the other direction. A one-shot seed on an empty watchlist is
probably the right shape, but that is a call to make deliberately.

#### 2. A legacy console codepage could kill a finished command — FIXED

`backtest` computed 125,254 instances, wrote **both** report files, and then
died on `print(render_backtest(result))` with `UnicodeEncodeError`: this
console is **cp1252** and the report contains `→`. Every renderer in the
package emits UTF-8, so `cli.main` now calls `_force_utf8_console()` before
anything else. The same crash was waiting in `digest`, `board`, `brief` and
`learning`, which all emit `→`, `σ` or `≥`.

Note what saved the run: `write_backtest` happens **before** the `print`, so
the reports survived the crash. That ordering is the failed-publish invariant
earning its keep by accident.

#### 3. The anchor watcher can double-count one event

### 3. FORGE printed composition artifacts, and RRS with it — RESOLVED 2026-08-20

Fixed under plan.md §17 **D-22**. Full write-up in `CHANGELOG.md`.

**Verified diagnosis, not the assumed one.** The chain-link was sound and
§19.1's churn fixture stayed green throughout. Decomposition against the real
lake named a single member-day: on 2026-08-02 *Vanguard Resonant Cypher*
(type 95640) printed `close 10.07 → 22,450.00`, a **+222,839.4%** return, at a
**0.75%** live weight — contributing **+1,661.59%** of the +1,661.37% the
index moved. All 100 members were priced that day, so no gap or NaN path was
involved. The same shape explains 2026-05-17 (*HyperCore*, +2,298%, 4.11%
weight → +94.38% of a +94.07% day) and 2026-08-18 (*HyperCore* again,
+1,385% → +58.91% of +57.13%).

**One assumed mechanism was ruled out.** The gap-reappearance path does not
occur here: pandas 3.0.5's `pct_change` no longer pads (`fill_method=None`),
so a member returning after a gap already yielded NaN. The returns are now
computed explicitly anyway, so the answer does not depend on which pandas is
installed.

**Fix:** member daily returns are winsorized at `k ×` each member's own
rolling median absolute return before aggregation, mirroring the ATR path's
TR clamp, with clamped-day counts in every index's diagnostics.

**Acceptance, measured against the criteria stated before the fix:**

| criterion | before | after |
|---|---|---|
| FORGE level, 415 bars | 1,000 → 69,243 | 1,000 → **981.10** |
| median abs daily move | 0.029%, punctuated by +1,661% days | **0.3396%** |
| p95 / max abs daily move | — | **1.03% / 2.08%** |
| `power_index` | **1,478.27** | **−3.280** |
| RRS, middle 84% of universe | every name ≈ −1,479 | p5 **−2.20** · p50 **+3.12** · p95 **+6.73** |
| digest | "Nothing clears costs today" | **25 candidates** |

That last row is the one that mattered: the honest zero was not honest. RRS
is one of the four gates, so a −1,479 offset was failing every name in the
universe and the digest was reporting a broken gate as an absence of
opportunity.

**Nothing persisted needed rebuilding** — indices are computed live from the
lake on every read, so there was no cached series to invalidate. The stored
backtest *was* regenerated, since its gate counts were computed against the
broken RRS: instances rose 125,254 → **147,140** and the verdict is still
**NOT PLAUSIBLE at every horizon**, which is the expected result because that
verdict rests on measured friction and never reads RRS.

### 3b. A degenerate per-type ATR — RESOLVED 2026-08-20, with a caveat

Fixed under plan.md §13.2 (amended) and §17 **D-29**. §13.2's `measurable`
gate is now **relative**: `atr / close >= signals.min_atr_fraction`, below
which the gate reads UNKNOWN and UNKNOWN fails. One epsilon governs the ATR
and the AVWAP sigma, enforced inside `atr_last` so no scalar consumer can
bypass it.

**The default is derived from the lake, not invented.** `atr/close` is
bimodal — a degenerate cluster at 1e-14…1e-11, then p1 = **1.6e-08**, p2 =
**2.4e-05**, p50 = **5.8e-02**. `1e-6` sits at the top of that empty band.

| | before | after |
|---|---|---|
| types blocked | — | **39 (1.33%)** |
| max abs RRS | 9.05e11 | **1.19e7** |
| abs RRS > 1,000 | 77 | **51** |
| RRS p1 / p99 | −1,966 / +2,661 | **−677 / +710** |
| RRS median | +3.18 | **+3.18** |
| backtest instances | 147,140 | **145,655** |
| backtest verdict | NOT PLAUSIBLE | **NOT PLAUSIBLE** |
| digest candidates | 25 | **25, none dropped** |

No golden fixture needed regenerating: on clean data the gate changes nothing.

**The caveat, because the acceptance was written expecting more.** The board's
value sort still shows large magnitudes at the top, and this fix was never
going to change that. Those rows are **not** ghost names: *Hemorphite
II-Grade* has `atr/close` of 1.55e-04 — ordinary — and reads RRS −2,932
because it fell 45% in twenty bars, i.e. **2,936× its own ATR**. That is a
correct measurement of an extreme move. The one visible board row this fix did
change is *Second-hand Parts* (`atr/close` 1.22e-10, a genuine ghost), whose
RRS is now UNKNOWN.

What is left at the top of that sort is mostly **unfiltered ESI prints in the
per-type `close` series** — the §0 check #4 phenomenon — and reporting stays
unclamped by design. If the operator wants the board's *ordering* to stop
selecting for those, that is a separate decision about the sort key, not about
the measurable gate.


### 4. The desk blocked its own GUI thread — RESOLVED 2026-08-20

Fixed under plan.md §19.2 (amended) and §17 **D-24**. Full write-up in
`CHANGELOG.md`.

**Contract now recorded in §19.2: the GUI thread never computes; it paints.**
Lazy pages, a `compute`/`paint` split on a `QThreadPool` worker,
last-good-on-failure under a visible stamp, and recomputation keyed on input
fingerprints rather than on the clock.

**Measured on the real lake, before and after:**

| | before | after |
|---|---|---|
| open to interactive | **217 s** | **8.6 s** |
| timer tick, unchanged inputs | full 217 s rescan every 60 s | **15 ms** |
| revisit an already-computed page | full recompute | **0.000 s** |
| first visit to SCANNER | blocking the window | 162.8 s off-thread, stamped |
| first visit to BOARD | blocking the window | 63.5 s off-thread, stamped |

BOARD and SCANNER still cost real time on their first visit — that work is
genuinely expensive — but it happens on a worker while the window stays
responsive and the page says what it is doing. That is what the amendment
permits; what it forbids is paying for it before the operator has asked, and
paying for it again every sixty seconds.

Four offscreen tests pin it: `build()` runs no computation (asserted against
the scan entry point itself), an unchanged input key does not recompute, a
completed background result lands on the GUI thread and repaints, and the
stamp renders while work is in flight — plus one that a failed recompute keeps
the last good result and names the error.

**Still owed, and honest about it:** the 8.6 s open is mostly `load_desk()`
(5.6 s) plus Qt startup, still on the GUI thread. It is inside the
"interactive in seconds" the amendment asks for, so it was left alone rather
than moved to a worker for its own sake.

### 5. Four small items — CLOSED 2026-08-20

- **`verdict_banner` renders UNKNOWN instead of nothing** (§17 D-25). A fresh
  clone has no stored study, and MARKET and SCANNER were carrying **no banner
  at all** — indistinguishable from a desk that measured and passed.
- **The patch-notes watcher dedupes on the article URL** (§17 D-26); the
  duplicate *Version 24.01* row is gone and seven distinct candidates remain.
- **`selftest`'s cost-model check derives tax and fee from config** (§17
  D-27) rather than hardcoding Accounting V.
- **`universe.seed_watchlist` deleted** (§17 D-28), its test with it. The
  roster is seeded and operator-owned; an automatic seeder was deliberately
  not wired in, because a re-seed would resurrect a name the operator removed.


## The consolidated live-validation checklist

### A. Data honesty (was Phase 0's gate)

- [ ] **Spot-check five types in-game.** Open the market window for five
      tracked types and compare price and volume against the lake and the last
      sweep, within cache-window tolerance. *Falsified if any disagrees beyond
      that tolerance.*
- [ ] **Reproduce the fee arithmetic against one real fill.** Make one real
      trade, then check `costs.py`'s numbers against the wallet to ±0.1%.
      *This is also what closes §0 checks #5 and #6* (relist surcharge, and
      whether the 7.5%/3% base rates are still current).
- [ ] **Read the telemetry ledger.** `SELECT feed, COUNT(*), MIN(expires_at)
      FROM sweep_ledger GROUP BY feed` — confirm every request honoured its
      `Expires` and stayed inside the self-caps. Any row with `from_cache=0`
      whose `requested_at` precedes the previous response's `expires_at` for
      the same URL is a bug and must be reported, not tuned around.

### B. Universe (was Phase 1's gate)

- [x] **Budget discipline held during the build.** Zero 429s and zero 420s
      across ~26,000 requests; the orders token floor observed was 10,176 of
      12,000, i.e. a peak of **15.2%** against the gate's 25% target. Recorded
      in the `sweep_ledger`.
- [x] **Census run and the derived floor written into `plan.md` §11 D3**
      (≥500M ISK median daily turnover, 2,654 types, 95.1% of turnover). This
      gate is the licence for every later "the universe is N" claim, and it is
      now earned.
- [ ] **48 hours unattended** is still owed — the build ran the crawl once
      under supervision, not the daemon across two days.
- [ ] **Decide the `order_count` floor question** raised in §11 D3: the derived
      floor's `order_count` component came out **zero**, so the anti-wash-trade
      guard §3.6 wanted is not active in it. Either constrain the rule or keep
      `config.toml`'s own `min_median_order_count` above it. This is a
      plan-level decision, not a re-derivation.

### C. Signals (was Phase 2's gate)

- [ ] **Bands and levels spot-checked** against Adam4EVE or the in-game chart
      for 10 types across 3 market groups.
- [ ] **Confirm the anchor calendar.** `config/anchors.jsonl` now carries seven
      **real** dates pulled from the official patch-notes feed (Revenant,
      Legion, Catalyst, Cradle of War and three version patches). They ship as
      `confirmed: false` because which of them counts as a live anchor for
      *your* items is your judgement, not the feed's — a version patch that
      rebalances one hull is not the same event as an expansion. Flip the ones
      you want to `true`. **Until then the system anchors on a synthetic
      90-day grid**, which works but is not the patch-anchored read the design
      is built around. `python -m evescreener anchors` refreshes candidates
      (it can never confirm one); `--list` shows the calendar.
- [ ] **Resolve §0 check #1** (is ESI `average` volume-weighted or a plain
      mean?) by comparing ~20 types' `average` against Fuzzwork's same-day
      `weightedAverage`. The bar contract tolerates either answer; the answer
      belongs in `plan.md` §4.
- [x] **§0 check #4 is ANSWERED** — CCP does *not* filter outlier prints.
      Measured over 1,854,651 real bars: `high/close` reaches 1,940,777× and
      `close/low` reaches 12.8 billion×. Winsorization clamps 7.9% of bars and
      touches 79% of tracked types; without it **20.5% of types would carry a
      risk unit more than twice too large** (worst case 2,433×). Nothing is
      owed here — it is recorded in `plan.md` §17.

### D. Ranking and delivery (was Phase 3's gate)

- [ ] **Two-week shadow period.** Every digest archived, every decision logged
      with its planned net-R, outcomes tracked.
- [ ] **Survive one ESI outage day** with honest staleness — UNKNOWN rows and
      an explained honest zero, never stale numbers presented as fresh.
- [ ] **Set the Discord webhook** in `config.toml`. Until then delivery
      reports `unconfigured` and the digest is archived but not posted, which
      is correct behaviour, not a failure.
- [ ] **Read the new desk surfaces once against the in-game market** (plan.md
      §18): `board --top 20` beside the in-game browser — do the movers and
      dips look like the market you see? — and `brief --name <a type you
      know>` — are the bands, levels and friction numbers believable? These
      are the ported TradingBotV3 chart/strength-board reads; §2.7's "does the
      board resemble your scan" judgement is yours to make, not the build's.

### E. Cost netting (was Phase 4's gate)

- [ ] **Predicted vs actual effective fill on ≥ 10 real trades**, within
      **±0.5% of notional**. Record each with
      `python -m evescreener paper real-fill`. The tolerance was stated in
      `plan.md` §12.3 before any fill was recorded and is not to be adjusted
      after seeing the results.

### F. Destruction (was Phase 5's gate)

- [ ] **Read the lead-lag study's outcome** against the §14.3 rule frozen
      before measurement. If it did not survive, destruction stays an
      annotation — do not let it into the ranking by feel.

### G. Cross-region (was Phase 6's gate)

- [ ] **One full cross-region cycle validated on real freight**: quoted vs
      invoiced cost within tolerance, and the token budget still under 25% peak.
- [ ] **Resolve §0 check #3** (structure blind spot) by comparing
      `station_volume_share` against the in-game regional view. The number is
      already carried per row; what is owed is reading it.

### H. The experiment itself (plan.md §12)

**Read this section against what the backtest already found.** The setup class
was tested and came back NOT PLAUSIBLE at every horizon — on friction, not
direction. Running the paper experiment on the general setup would mostly
reproduce that, expensively and slowly. So the sequencing that actually earns
something:

- [ ] **First, use the paper platform to validate the cost model, not the
      setup.** That is gate E: ten real fills recorded against predicted
      effective prices. It works regardless of whether any setup pays, and it
      is what makes every other number in the system trustworthy. If the cost
      model is wrong, the backtest's verdict is wrong too — in either
      direction.
- [ ] **Only then decide whether to test a setup at all**, and if so, scope it
      to the two tracked types whose round-trip friction is under the ~0.78%
      the measured gross edge can absorb (*100MN Afterburner II*, *Mexallon*).
      That is a **new study needing its own pre-stated rule** (plan.md §13
      discipline), not a re-run of the old one with the losers removed.
- [ ] **If you do run it: 20 closed trades for the first read, 40 for the
      verdict.** Below 20 the tracker reports `TOO_EARLY` and offers no read —
      take none.
- [ ] **Accept that `FALSIFIED` is a real possible outcome.** The rule was
      frozen before the first trade precisely so that a negative answer cannot
      be argued away afterwards. Given the backtest, it is the likely one.

### I. The desk and the operator setups (plan.md §19) — NEW

Everything in §19 is IMPLEMENTED and GREEN offline. Nothing in it is
LIVE_VALIDATED, and the LEARNING page is correctly showing UNKNOWN for
everything because there is not one tagged closed trade yet.

- [ ] **Open the desk on the real data directory.** `uv sync --extra gui`,
      then `python -m evescreener gui` (or double-click `launch_gui.py`). Walk
      all eight pages. The thing to look for is not "does it render" — it did
      here — but whether any number on it disagrees with the same number from
      the CLI (`board`, `brief`, `scan`, `paper report`, `learning`). They read
      the same code, so a disagreement is a real bug.
- [ ] **Eyeball FORGE against Adam4EVE or the MER.** The index has never been
      compared to an outside source. It does not need to match — different
      membership, different weighting — but it must not disagree in *shape*.
      If FORGE rises through a month the MER shows falling, something is wrong
      with membership or with the chain-link, and the diagnostics beside the
      chart (members, top weight, entropy) are where to start.
- [ ] **Skim the sector membership.** Open each sector on MARKET and check the
      member count and top weight look like the sector's name. A sector is a
      subtree of market groups read from the SDE; a plausible failure is a
      root that pulls in far more than intended. `config/sectors.jsonl` is
      yours to edit — that is what it is for.
- [ ] **Check the THIN band by hand.** Pick three THIN names off the board and
      look at them in-game. The claim is "100–999 units a day — you may not
      get out of this at size". If that reads wrong for EVE, the floor in
      `config.toml` is one number and §11 D3 records what the old one was.
- [ ] **Define one setup end to end.** Write it in `config/setups.jsonl`, run
      `setups` to see it validate, `scan --setup "<name>"` to see it fire (or
      honestly not), chart a hit, `paper` buy it with a setup tag and a like
      tag, close it, and confirm it appears on LEARNING. **That single loop is
      the acceptance test for the whole of §19** — everything else in this
      section is inspection.
- [ ] **Pass on something, deliberately.** Use "not today" with a dislike tag
      on a name you would genuinely skip. In 5, 10 and 20 days the LEARNING
      page will tell you whether that reason was a good one. This half of the
      record is the half nobody keeps, and it is the half that is cheap.
- [ ] **Run `backtest --setup NAME`** for the setup you defined. Note that a
      setup containing a `near_level` condition will correctly produce **zero**
      instances and say why — that is not a bug, it is the refusal to
      backtest a condition that would need lookahead.

## Notes for the next session

- One phase at a time resumes after this gate. The single-push override was
  scoped to this build only (plan.md §17 D-1).
- `plan.md` §0 checks **#1, #5 and #6 remain OPEN** — all three need the
  operator (a Fuzzwork cross-check for #1, one real fill for #5 and #6).
  **#3 and #4 are ANSWERED** by measurement and #2 is partially answered; §17
  records all of it.
- The unconfirmed anchor calendar is the single largest gap between what the
  system does today and what it was designed to do. It is now a *confirmation*
  task — the real dates are already in the file — not a data-entry task.
