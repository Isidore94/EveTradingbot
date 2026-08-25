# Fable review prompt — EveTradingbot §23, the HAULING track

Paste everything below the line into a fresh Fable session with the repository
checked out at the head of `claude/hauling-h1-h4-build-4pwsso`.

---

You are reviewing **plan.md §23**, a new track: a personalized HAULING tab
built in one authorized push (phases H1a, H1b, H2, H3, H4 — recorded as §17
D-33). You have reviewed this repository before; `FABLE_REVIEW_BRIEF.md` is
yours, and §21 and §22 are the two remediation tracks that answered it and
Sol's follow-up.

**This is not that kind of review.** There are no prior findings to check the
fixes of. This is a **first-build audit of new code that has never met the
game**, and the useful question is not "is it tidy" but:

> Where does this build **claim more than it measured**, and where would an
> operator acting on one of its rows lose ISK he was told he would keep?

The diff is `aa46fc4..HEAD` — eleven commits:

```
5b628ea  plan.md §23: the hauling contract, written before the code
9a62014  H1a  the map, and a router that says no
37d33d4  H1b  what 1,200 units really cost, at one station
cf878c3  H2   the engine, the report, the haul CLI, and the page
fe31239  H3   getting out is assumed, and the row says which parts
b301e09  H4   charge the detour, and price your own flying time
ed1fccc  handoff: one sweep to one painted row, and what it owes
b6bd2da  name the right side when depth runs out
2ccfb9e  restore three CHANGELOG entries lost to no-op replaces
c61ff84  root the reachability search at the station, not at every order
(head)   two defects found while writing this brief
```

## 1. Read these first, in this order

1. **`CLAUDE.md`** — invariants, locked stack, mandatory workflow.
2. **`plan.md`** — **§23 in full** (the contract this track is judged against),
   then §17 (deviation record, append-only — D-33 and D-34 are this track),
   §3–§5, §11, §15, §19.2, §21 R1, §22 S2 and S6.
3. **`CHANGELOG.md`** — the six §23 entries, newest first.
4. **`CURRENT_CHECKPOINT.md`** — the consolidated owed live-validation
   checklist for this track.

Then: `routes.py`, `books.py` (the H1b half), `hauling.py`, `liquidity.py`,
`positioning.py`, `haulreport.py`, `haulledger.py`, `haulfreight.py`,
`gui/pages/hauling.py`, and the eleven new test modules.

## 2. Facts already verified — do not re-research, do not contradict

Checked against CCP primary sources and the live SDE on 2026-08-25. Treat as
given; a finding that rests on disputing one of these is a finding about the
wrong thing.

- Buy orders match by **range from the order's own location**; the seller
  transacts from the station he is docked in. Ranges are
  `station | solarsystem | region | 1..40`.
- High-sec is decided on **displayed** security: `round(true_sec, 1)` half-up,
  except `0 < true_sec <= 0.05 → 0.1`; high-sec is `display >= 0.5`.
- `npcStations.jsonl` carries **no name field**. `mapStargates.jsonl`'s
  `destination` is an object with `solarSystemID`. `mapSolarSystems.jsonl`
  carries `securityStatus`. SDE build **3478781**.
- Hub stations, resolved from that build and checked against their systems:
  Jita 60003760, Amarr 60008494, Dodixie 60011866, Rens 60004588, Hek 60005686.
- A taker pays **no broker fee**; sales tax is 7.5% base → 3.375% at
  Accounting V. Broker Relations does not apply in Upwell structures.
- Whether ESI's `issued` updates on a reprice is **unverified in either
  direction**, and the build says so wherever it shows one.
- Jita → Amarr measures **11 jumps** through Ahbazon (0.4) and **34**
  high-sec-only on build 3478781.

## 3. Re-derive these before reading any prose about them

- **§23.17's worked example**, end to end: 1,200 units, source WAP
  **102,416.67**, destination WAP **117,375**, tax **4,753,687.50**, net
  **13,196,312.50**, ROI **10.74%**. `tests/test_haul_end_to_end.py` claims it
  survives sweep → depth lake → validator → engine → report → painted row.
  Check the arithmetic yourself and check that each stage really is a stage —
  a test that reuses one computed object six times proves nothing.
- **`book_summary` is byte-identical** through the modified sweep path
  (`tests/test_depth_lake.py`). The whole track claims to be additive; this is
  the load-bearing test for that claim. Is the comparison actually strict, and
  does it cover the columns that matter?
- **The gate:** `uv run pytest -q` → 1,030 passed, 7 deselected; ruff check +
  format clean; `selftest` 12/12.

## 4. Attack these first — they are judgment calls, not mechanics

Every one of these is a place I chose, and any of them could be the wrong
choice. They are ordered by how much ISK a wrong answer costs.

**The reachability doctrine is the single most load-bearing unverified claim
in the track** (`books.reachable_from_station`). A bid is counted as executable
from a station if it rests there, or its range is `region`, or it is
`solarsystem` in the same system, or its numeric range covers the stargate-graph
jump distance. **Structure-resting bids are included** on the grounds that the
seller never docks there. If that is wrong, every exit number on the page is
optimistic — which is the exact direction §21 R1 and §22 S2a existed to remove.
Attack the rule, the fail-closed cases, and whether "excluded and counted" is
really counted everywhere it is claimed to be.

**`min_volume` exclusion is called conservative. Prove it is.**
(`books.reduce_depth`.) A bid demanding a parcel larger than one unit is
dropped from executable levels and its volume carried as a diagnostic. Is there
*any* path where that exclusion makes a number look **better** — a WAP, a
structure share, a level order count, a cumulative that skips a level, the
`MIN_VOLUME_BLOCKED` rejection that now depends on it?

**Truncation is claimed to be safe in every direction** (`DepthBound`,
`q_walk`). A curve cut short by the storage bound marks `depth_complete=False`
for the whole `(station, type, side)` group, and a walk past stored depth is
UNKNOWN. Is the bound evaluated at the right moment? Can a truncated curve
still price a quantity *inside* it wrongly — cumulative fields, breakpoints,
the `_candidate_quantities` ceiling, the `deepest > ceiling` cap rejection?

**The marginal-net rule refuses a size and then keeps going.**
(`hauling._best_plan`.) A chunk that nets ≤ 0 is rejected as
`MARGINAL_NET_NEGATIVE` — and the loop continues, so a *larger* breakpoint can
still win. Defensible for a non-monotonic book, or does it let the ranker climb
back over a size the same run just refused? Note the rejected size is still
appended to `priced` and becomes the baseline for the next chunk's marginal.

**Staleness is decided per region and reported as the older age.** A pair with
one stale leg prices **nothing**. Check that "nothing" is literal on every
surface — report row, page row, basket, freight column, ledger prefill — and
that the UNKNOWN pair rows cannot be sorted or filtered into looking like
priced ones.

**`isk_per_active_minute` is the default objective and it divides by a number
the operator supplies.** `seconds_per_jump` and `handling_minutes` come from a
ship profile he types in. The denominator is floored at handling time. What
does the ranking do at the edges — zero jumps, zero handling, an along_route
detour of zero — and can a plan win the whole page by being fast rather than
good?

**`along_route` silently degrades.** With `mode="along_route"` and no
`intended_destination`, `_trip` falls through to dedicated charging. Should it
refuse instead? And the `ROUTE_BLOCKED_SECURITY` label is decided by re-running
the route *without* the avoid list — so a pair blocked purely by the avoid list
is reported as blocked by security. Is that a mislabel worth fixing?

**The reliability grade is an opinion with a letter on it.** Weights (2/2/1/1),
the `weak` half-credit, the ratio cut-points and the "any UNKNOWN caps at D"
rule are all chosen, not derived. §22 S4 removed an unmeasured threshold
elsewhere for exactly this reason. Is a graded letter defensible here, or is it
the same mistake in a new place?

**Scale.** A real generation is five hubs and ~19k types per region.
`curves_from_depth` materialises every curve for every region up front, and
`scan_hauls` is O(pairs × shared types × breakpoints). Estimate the memory and
the wall clock on the operator's real lake, and say whether the desk page's
worker makes that acceptable or merely invisible.

## 5. Two defects were found while writing this brief — check the pattern

Both are the class you are hunting: **prose that says one thing while the code
does another**. Both are fixed in the head commit.

* **ISK-days were charged on the wrong clock.** §23.5 says an `immediate` exit
  charges capital-days over **travel time**. `liquidity.liquidity_attachment`
  overwrote `liquidation_days` and `isk_per_capital_day` with the *sell-out
  scenario* whenever it was known — in immediate mode too — while
  `liquidation_reason` went on saying "charged over travel time" beside a
  number that was not. The exit model now decides the clock.
* **`max_exposure_pct_per_destination` was configured and unreachable.** The
  engine caps per trade; the per-destination cap is a cap on a *basket*, and
  nothing passed it. That is §22 S6 wearing a different name.

**Assume this pattern recurs.** Every docstring, plan sentence and CHANGELOG
claim written during this track is a claim about behaviour. Grep them and check
the code does it — `plan.md` §23 especially, because it was written *before*
the code and nothing forced the code to match it afterwards.

## 6. Invariants — verify structurally, not by reading the docstrings

- **Read-only public ESI.** No SSO, no acting scopes. H5/H6 out of scope.
- **Never fetch before `Expires`.** The depth reduction rides the *same* pages
  as `reduce_orders` — confirm it adds no request and changes no cadence.
- **Nothing under `gui/` reaches the network**, directly or transitively.
  `haulfreight` → `crossregion` → `httpx` is the new way in; a test forbids the
  import, and the cold-subprocess probe should catch it anyway. Try to defeat
  both.
- **No `open` column**; the AVWAP σ formula is untouched; no frozen verdict
  rule moved.
- **Tri-state everywhere. UNKNOWN always fails**, and renders with its reason.
- **A failed publish never destroys the last verified output** — atomic writes
  for both report files; partial sweeps quarantined where `latest()` cannot
  glob them, for depth as for books.
- **The additive migration** must not drop or rewrite a row of the operator's
  deployed `state.db`.
- **Config parity**: an existing `config.toml` with no `[hauling]` and no
  `[routes]` must load unchanged and pass `selftest`.

## 7. Deliberately absent — do not report as gaps

- **Nothing here is `LIVE_VALIDATED`**, and the build cannot self-certify. The
  owed checklist is in `CURRENT_CHECKPOINT.md`.
- **`destination_share_prior` and `capture_share` are priors, not
  measurements** — regional history carries no station split. They are labelled
  ASSUMED. Report it if a surface *forgets* the label, not that they exist.
- **The relist formula is recorded and quarantined**; §0 check #5 stays open.
- **H0 is deferred** to a keep/park decision after the two-week shadow, and
  park is a real expected outcome.
- **Depth retention is unset on purpose** — the checklist owes a measurement
  first.
- **The tab's normal state may be a short list or an honest zero.** The Forge's
  median spread is 98.8% and §17 measured 10–14 of 151,113 hub pairs clearing.
  A quiet page is the system working; say so if the code makes a quiet page
  look like a broken one, not the reverse.
- **The track is 1,953 lines over its own budget**, stated as §17 D-34. Style
  and volume are not the review; a defect is.

## 8. Output

Findings only, ordered by severity. For each: the violated contract or false
claim; exact file and line; concrete inputs → wrong output; whether the new
tests would catch it and why not if not; the smallest contract-preserving fix.

Separate **correctness bugs**, **unsound analysis**, **invariant risk**,
**unnecessary complexity**, **missing tests**.

Say plainly when something is right. An empty category is a valid result — this
project prefers an honest zero to a filled panel, and a manufactured finding
costs more than a missing one.
