# EveTradingbot implemented history

Authoritative for what exists and the sequence of revisions. Remaining work:
`plan.md`. Labels keep the source-repo meanings: `IMPLEMENTED` = code exists,
`GREEN` = deterministic tests pass, `LIVE_VALIDATED` = real-market evidence
recorded, `PROMOTED` = explicit operator decision.

## 2026-08-25 — §23 H1b: what 1,200 units really cost, at one station

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **929 passed, 7
deselected**, ruff check + format clean. **Not LIVE_VALIDATED**: no ladder this
produces has been compared to a market window.

- **`reduce_depth` builds a price-level curve per execution station**, from the
  **same in-memory pages** `reduce_orders` already reads. One fetch, two
  products: no extra ESI request, no cadence change, no new feed. The depth
  generation is `(region_id, sweep_ts)` — **identical** to `book_summary`'s —
  so a depth row and a book row can be proved to come from one sweep.
- **`book_summary` did not move, and a test proves it.** The frame produced
  through the modified sweep path is compared column for column, dtype for
  dtype and value for value against the frame produced without it. The whole
  track is additive; this is where that stops being a claim.
- **The reachability doctrine is now decidable.** §21 R1 had to fail closed on
  `solarsystem` and numeric ranges because the reduction had no topology. It
  has one now: a bid is executable from a station if it rests there, or its
  range is `region`, or its range is `solarsystem` and the system matches, or
  its numeric range covers the **stargate-graph jump distance**. Everything
  else — unknown system, unrecognised range, a distance the graph cannot
  answer — is **excluded and counted**. A structure-resting region-ranged bid
  is **included**, because the seller never docks there and range is what
  decides (§22 S2a).
- **`min_volume` is a conservative v1 rule, recorded as one.** A buy order
  demanding a parcel bigger than one unit is excluded from executable levels
  and its volume accumulated into `min_volume_excluded_qty`, so depth that
  exists but cannot be used is **visible rather than missing**. This
  under-states reachable exit depth on purpose; the packing problem it would
  otherwise create interacts with every other level in the walk.
- **`q_walk` prices a quantity, not a notional.** The existing `depth_walk`
  ("what does 0.25B buy") is untouched and still used by everything that used
  it. A quantity past the stored curve is **UNKNOWN**, and when the curve was
  truncated by the storage bound the reason says so — the levels that would
  have answered were never written, which is a different fact from a shallow
  book. Nothing is extrapolated from the last known price.
- **§23.17's worked example is fixtured and passes at the walk level**: 1,200
  units at a 102,416.67 source WAP and a 117,375 destination WAP, 4,753,687.50
  of sales tax, **13,196,312.50** net.
- **`DepthLake` mirrors `BookLake` exactly**: atomic writes, partial sweeps
  quarantined under a filename `latest()` does not glob, complete-only reads.
  **`load_validated_depth`** is the single staleness contract, on the same
  budget as the book because it is the same sweep — reimplementing staleness
  per call site is how two surfaces end up disagreeing about one generation.
- **The bound is a storage heuristic, and truncation is safe.** Levels are kept
  until they cover `max_scan_capital_isk × depth_safety_margin` **and** the
  largest recorded hold × the same margin. With no ship profile recorded the
  cargo target is zero rather than a guess at what the operator flies.

## 2026-08-25 — §23 H1a: the map, and a router that says no

**Status: IMPLEMENTED + GREEN.** `uv run pytest -q` → **894 passed, 7
deselected**, ruff check + format clean, `selftest` **12/12**. **Not
LIVE_VALIDATED**: no route this engine produces has been flown.

- **The SDE now carries the map.** `mapSolarSystems` contributes
  `securityStatus`, `mapStargates` becomes a system-to-system edge table, and
  `npcStations` becomes `sde_npc_stations`. Verified against build **3478781**:
  `destination` is an object carrying `solarSystemID`, and **`npcStations`
  has no name field at all** — so a station with no operator-supplied name
  renders as "<system> — station <id>" rather than as a guess that cannot be
  checked in the client. A gate whose destination will not resolve is counted,
  not invented, and a bundle where *none* resolve fails loudly rather than
  producing a router that answers "no route" to everything.
- **`routes.py`: shortest, safer, high-sec-only, and UNKNOWN.** BFS, Dijkstra
  with a configured penalty for entering non-high-sec, and a graph restricted
  before the search. A disconnected pair, an unmapped system or a filter that
  empties the graph returns UNKNOWN **with its reason and no jump count** —
  there is no straight-line fallback anywhere in this module.
- **High-sec is what the client displays.** `display_security` rounds half-up
  to one decimal — deliberately not Python's `round`, whose banker's rounding
  would send 0.45 to 0.4 and move the boundary by a whole system class — with
  the one irregular case `0 < true_sec ≤ 0.05 → 0.1`. So 0.4499 is **not**
  high-sec and 0.45 **is**, which is the band a hauler is actually ganked in.
  An unknown security is never high-sec.
- **A route carrying an unmeasured system reports an UNKNOWN minimum**, not a
  minimum over the systems we happen to know — that would report the safest
  possible reading of missing data.
- **Measured on the real map, and fixtured.** Jita → Amarr is **11 jumps**
  through **Ahbazon (0.4)**, and **34** on the high-sec-only profile. The
  fixture is the whole gated k-space graph from build 3478781 (5,268 systems,
  6,989 edges) so the assertion is about EVE rather than about a stub.
- **The same BFS resolves buy-order ranges.** `jump_distance` is memoised per
  (origin, bound) because a sweep asks it once per resting order, and it
  ignores security entirely — an order's range reaches as far as it reaches.
  Beyond the bound is **None**, which fails closed at every call site.
- **The route cache is keyed, never edited.** SDE build, origin, destination,
  profile, avoid list **and penalty** are all in the key, so a new build cannot
  read the old build's routes and two `safer` runs at different penalties
  cannot be confused. An UNKNOWN route is cached *as* UNKNOWN: a failed search
  is expensive too.
- **The migration reaches a database that already exists.** `state.db` holds
  the paper ledger and the watchlist and is not regenerable, so
  `security_status` is added by `ALTER TABLE` and the next `sde` run fills it;
  a test drives the whole thing against a database built with the **old**
  three-column schema and asserts no row is lost and NULL stays NULL.
- **A whole config section can now be optional.** `[hauling]` and `[routes]`
  declare defaults for every field, so an operator's existing `config.toml`
  loads unchanged and `selftest` parity still passes — the same rule §21 R2
  applied per field, applied per section. **Hub station ids were resolved from
  the SDE**, each checked to sit in its hub system: Jita 60003760, Amarr
  60008494, Dodixie 60011866, Rens 60004588, Hek 60005686.

## 2026-08-25 — plan.md §23 opened: the personalized HAULING tab (§17 D-33)

**Status: PLAN ONLY in this commit — no code.** `plan.md` gains §23, the
contract for a hauling tab that answers "given where I am, what I fly, what ISK
I have and how long I have got, what should I put in the hold?" — a question no
existing surface asks, because it is decided at a **quantity** rather than at a
notional tier.

- **§17 D-33 records the single-push authorization** (operator, 2026-08-25:
  *"build first, evaluate against competitors and live gates afterwards"*),
  covering H1–H4 only. **H5 and H6 are out of scope entirely** — both need
  authenticated ESI. The per-phase gates are **batched, not waived**.
- **The competitor set was checked live the same day and corrected.**
  **ISK Scout** (`iskscout.com`) was missing from the directive and is now a
  first-tier benchmark; **EVE Flipper** is at v1.6.14 (Jul 2026) and already
  does VWAP depth walking, multi-hop route trading, contract arbitrage and
  paper backtesting; **eve-meta is defunct**; **evetrade.space has lapsed**;
  Trading Matrix's free tier is Jita-only. H0 therefore **moves after** the
  shadow period and becomes a **keep/park** gate rather than a build gate —
  parking is a real expected outcome, and cheaper than maintaining a worse copy
  of a live tool.
- **Recorded in the contract before any code:** `generation_id ≡ (region_id,
  sweep_ts)` with **both** regions' generations pinned on every row and the
  older one deciding staleness; the buy-side **reachability doctrine** (station
  / region / solarsystem / numeric jump range, everything else excluded and
  counted); the conservative **`min_volume` rule** (an order demanding a
  minimum parcel is excluded from executable levels and its volume carried as a
  diagnostic); **displayed-security** rounding (`round(true_sec, 1)` half-up,
  except `0 < true_sec ≤ 0.05 → 0.1`; high-sec is display ≥ 0.5); a
  zero or unmeasurable volume quantile making liquidation **UNKNOWN** and
  failing every maker cap; `destination_share_prior` and `capture_share` as
  **labelled assumptions** rather than estimates; and the merge of the
  duplicated capital-turnover metric into one `isk_per_capital_day`.
- **The relist fee formula is recorded and stays quarantined.**
  `max(0, BR·(P2−P1)) + (1−RD)·BR·P2` closes the *shape* of §0 check #5 and
  nothing else: it has never met a wallet, so `relist_cost_unverified` remains
  unconsumed by any analytical path and the test that enforces that is
  unchanged.
- **Compute ownership is stated** so it cannot drift: the daemon and CLI
  produce ingest products (depth generations, routes, destination bars), the
  GUI page computes per-profile feasibility in its own worker, and `haul scan`
  writes the immutable audit artefact.

## 2026-08-21 — Two fill models, and a paper form that refuses before it asks (§12.2, §17 D-32)

**Status: IMPLEMENTED + GREEN.** `plan.md` §12.2 as amended, §17 D-32.
`uv run pytest -q` → **850 passed, 7 deselected**, ruff check + format clean.
**Not LIVE_VALIDATED**: no maker fill in this ledger has been checked against
a fill the operator really got.

Operator report: *"when I go to paper trade it's just a mess and it doesn't
work"*, plus a request to fill at the midpoint.

- **It did not work because the only book on disk could not price anything.**
  `data/books/region=10000002/date=2026-08-20.parquet` was swept 25 hours
  before the attempt and predated the executable-quote contract (18 columns,
  no `exec_location_id` / `exec_price` / `exec_is_structure`), so
  `book_quote()` refused it twice over. A fresh sweep — 411,876 orders,
  412/412 pages, 19,148 types, complete — replaced it.
- **The form showed a price the ledger was always going to refuse.**
  `prefill_for()` read `depth_fill_price_0` straight off the lake, bypassing
  the staleness budget and the R1 contract that `PaperLedger` enforces. It now
  prices through `paper.book_quote` — the ledger's own function — so an
  unpriceable book reads `UNKNOWN`, names its reason in the header, and greys
  `Record paper buy` instead of accepting a full form and then refusing it.
  The ledger still validates on submit and remains the authority.
- **The notional was a free-entry spin box**, but `open_position` refuses any
  value that is not one of `costs.notional_tiers_isk` — the only sizes the
  depth walk measures. It is now a picker of those tiers.
- **Two entries in the same second silently became one position.** Ids were
  `{type_id}-{HHMMSS}`, so `positions()` replayed the second `open` over the
  first: a position that existed on disk, could never be closed, and was
  missing from every number the verdict tracker reads. New ids carry a
  sequence suffix; a legacy collision is now recovered on read as `…#2` with
  `duplicate_id` set, rather than dropped.
- **Two page bugs that made the ledger look empty.** PAPER read
  `verdict['reason']` (the tracker writes `detail`), so every verdict rendered
  as "no reason recorded"; and the closed table read `exit_source`, a key that
  has never existed, so the `priced` column always said "book" even for an
  operator-supplied fill.
- **`fill_model` — `taker` or `maker` — is recorded on every open, mark and
  close.** Taker is unchanged and remains the default. Maker posts one tick in
  front of the executable quote, pays the per-station broker fee (§21 R4) on
  both legs, records the volume queued **ahead** of it, and is stamped
  `fill_assumed` with its assumption in the record: the book proves the price
  was postable, never that anyone traded into it. `paper report`, the PAPER
  page and the desk score the two populations apart under the same frozen
  §12.4 rule, and the whole-sample verdict says when it is mixing them.
- **There is no mid fill, and asking for one is a recorded refusal.** No EVE
  order type executes at the midpoint. On the 2026-08-21 sweep, Helium Fuel
  Block quoted a taker round trip of **−11.5%** against a maker round trip of
  **+2.7%** on the same book at the same second; a number between them
  describes no trade anyone can make.
- **A maker position marks on its own model**, with the taker liquidation mark
  recorded beside it — what the plan says it is worth, and what walking out
  today would actually pay.
- `paper open|close --fill-model {taker,maker}`; `config.toml` gains
  `paper.default_fill_model` and `paper.maker_tick_isk`, both optional with
  the previous behaviour as their defaults.

## 2026-08-20 — A wider guard, and numbers that can be re-derived (§22 S8)

**Status: IMPLEMENTED + GREEN.** `plan.md` §22 S8. `uv run pytest -q` →
**825 passed, 7 deselected**, ruff check + format clean, `selftest` 12/12.
This completes §22 S1–S8; **none is LIVE_VALIDATED**.

- **The import guard was two exact names.** R8 rejected `httpx` and
  `evescreener.esi.client` only, so a GUI module could have reached the network
  through `requests`, `urllib.request`, `urllib3`, `aiohttp`, or any ESI module
  other than `client`. The probe now rejects those and any module with an `esi`
  path component. `socket`, `ssl` and `http.client` are deliberately allowed
  and the probe says why: Qt and the stdlib load them regardless, so flagging
  them would fail always and prove nothing.
- **A number in prose is not a measurement.** §20.3 quoted figures with no
  as-of date, membership, denominator or command. An independent reproduction
  disagreed with all of them and a third run disagreed again — and none can be
  shown right or wrong, because none recorded what it measured.
  `provenance.py` emits a `MeasurementReport` with as-of, membership, filters,
  input identity, denominators, command and git revision;
  `measure_top_performers()` produces the TOP figures through it. A magnitude
  gets no share, and the file digest states that it is over
  `(name, size, mtime_ns)` and **not** over the contents.
- **The old figures are labelled a historical snapshot and left in place** —
  their inputs cannot be recovered, and replacing them would repeat the mistake
  with fresher numbers.

## 2026-08-20 — Broker overrides reach production; a refusal is a record (§22 S6, S7)

**Status: IMPLEMENTED + GREEN.** `plan.md` §22 S6/S7. `uv run pytest -q` →
**802 passed, 7 deselected**, ruff check + format clean, `selftest` 12/12.

- **S6.** `CostModel.from_config(...).broker_fee_overrides` was `{}` for every
  config, always, and `maker_spreads()` used that untuned model — so R4's
  per-station broker fee could not move a single production number. The R4 test
  built the model by hand, proving the arithmetic and nothing about reach.
  `[costs].broker_fee_overrides` is loaded by `from_config()` now; the new test
  drives two stations at 0.10% and 5.00% **through `maker_spreads()`**. Rates
  are operator-**observed**, never derived from standings. With none
  configured, behaviour is byte-identical to before.
- **S7.** §19.4 requires the refusal itself to go in the ledger.
  `record_pass()` raised for an invalid action, and `_clean_tags` raised for an
  unknown tag, **before** `_refuse()` was reached — so the one class of
  decision the ledger lost was the one made wrongly. Both route through
  `_refuse()` now, recording the attempted action and tags with the reason. The
  decision is still refused and the unknown tag still never accepted; tests
  assert the exception, the record, and that no `pass` event is written.

## 2026-08-20 — Three statistical and ranking corrections (§22 S5b, S5c, S5d)

**Status: IMPLEMENTED + GREEN.** `plan.md` §22 S5b/c/d. `uv run pytest -q` →
**795 passed, 7 deselected**, ruff check + format clean, `selftest` 12/12.

- **S5b — effective samples.** R3 binned dates against a *global* origin, so a
  bin edge between 10 and 11 January made two windows sharing nine of ten days
  look independent: **3** where at most **2** holds.
  `non_overlapping_subset()` now selects an actual set of rows per type, and
  wins are counted **in** that subset rather than rescaled from the overlapping
  win rate — rescaling re-imports the dependence being corrected. Cross-type
  dependence remains unmodelled and is stated as such.
- **S5c — negative expected R.** R6 multiplied, so `-1R x 0.01 = -0.01R`
  outranked `-0.1R x 1.0 = -0.1R`: a severe loss gone stale sorted above a mild
  one measured yesterday. Decay moves an estimate toward the 0R prior, so it
  shrinks a gain and must not shrink a loss. **No staleness cliff was
  invented** — `freshness_factor` is bounded to [0.4, 1.0], so no point in its
  range means "no information"; a 0.5 floor was tried, marked everything older
  than ~8 days UNKNOWN, and was withdrawn.
- **S5d — a median of two is a mean.** Aug 10 = 0.01 with Aug 12/17/19 = 100
  gave a ranked **+99.98%** week beside a **raw 0%**, state OK, because the far
  endpoint held two bars. `MIN_ENDPOINT_BARS` is **3**. Measured cost on the
  real lake: OK 2,740 → 2,583, UNKNOWN 109 → 266 (157 names, 39 of them THIN).
  The worst reading is unchanged at 85,069%, confirming the remaining extremes
  are sustained repricings rather than prints.

## 2026-08-20 — A generation, not a widget tuple (§22 S3)

**Status: IMPLEMENTED + GREEN.** `plan.md` §22 S3. `uv run pytest -q` →
**789 passed, 7 deselected**, ruff check + format clean, `selftest` 12/12.

R7 solved half of this and the half it left was the dangerous one.

- **The worker still read the page.** It passed `job_input` to the job and then
  had `compute()` read `self._running_input` back off the page, on a worker
  thread. `Generation` freezes token, key, data and input before the job leaves
  the GUI thread, and `compute(data, job_input)` gets everything as arguments.
- **A data-only refresh was silently dropped.** R7 queued only the widget
  tuple, so a lake moving from key 1 to key 2 without a control being touched
  compared equal, queued nothing, painted the key-1 result and scheduled no
  follow-up. The owed generation carries key and data too, and `_run_owed()`
  runs it unconditionally — including after a *failed* job, which previously
  stranded the owed work.

The AST guard now also fails on any `self._running*` / `self._owed` /
`self.data` read inside a `compute()`, and on any `compute()` missing the
`job_input` parameter. Cancellation, off-thread execution and
last-good-on-failure are unchanged.

## 2026-08-20 — Friction is a ratio of the gross move (§22 S5a)

**Status: IMPLEMENTED + GREEN.** `plan.md` §22 S5a. `uv run pytest -q` →
**785 passed, 7 deselected**, ruff check + format clean, `selftest` 12/12.

Reproduced: entry 100→150, exit 100→5 0, tax 0 reported **100.0%** friction
where the true cost is `1 - 50/150` = **66.667%**.

R3 added two one-sided percentages. That says "the whole move is friction" for
a round trip that kept a third of it, and the error grows with the moves — the
sum can exceed 100% and imply a loss larger than the position. Friction is what
the round trip keeps of the **gross move**, so it is a ratio of ratios:
`1 - (pre_tax_exit / exit_close) / (entry_effective / entry_close)`. Tax still
compounds. Both the scalar helper and the aggregate use the same per-row form.

The R3 regression test asserted the wrong formula and is replaced.

**The frozen §13.6 rule is untouched and the verdict does not move** — every
cell is still NOT PLAUSIBLE. The golden fixture was regenerated after the
corrected case existed; its friction figures shifted slightly (2x: book
7.00%→6.80%, total 10.14%→9.94%) because the additive error only bites at
large moves. Pre-correction values remain in git history; no stored report was
rewritten.

## 2026-08-20 — H2 is UNKNOWN, and every renderer says so (§22 S4)

**Status: IMPLEMENTED + GREEN.** `plan.md` §22 S4. `uv run pytest -q` →
**782 passed, 7 deselected**, ruff check + format clean, `selftest` 12/12.

R5 made the payload honest and every renderer discarded it, printing **"the
lead-lag claim was tested and not supported"** — a claim about H2, whose
confirmatory run does not exist. `brief.py` printed it whenever `destruction_z`
was merely present, with no study involved.

`h2_statement()` returns **`H2 UNKNOWN — confirmatory run absent`** plus the
exploratory pooled finding beside it, labelled. An H2 verdict is available only
from a declared doctrine cohort; a payload with no declaration **fails closed**.

**The dependence correction is no longer decorative.**
`independent_observations()` was measured and never read — `spearman()` still
assumed ~470,000 dependent rows were independent, and Bonferroni corrected
that. `rotation_permutation_p()` rotates each type's series by a random offset,
preserving its autocorrelation exactly while destroying the alignment, and
gives an empirical p-value bounded below by `1/(permutations+1)`. The frozen
§14.3 rule still uses the naive p-value, unchanged; the family-wise verdict now
uses the cluster-aware one.

No confirmatory run was created or claimed.

## 2026-08-20 — Executable identity covers depth, and pricing uses the validator (§22 S2)

**Status: IMPLEMENTED + GREEN.** `plan.md` §22 S2. `uv run pytest -q` →
**767 passed, 7 deselected**, ruff check + format clean, `selftest` 12/12.

**S2a.** R1 made `exec_price` respect `reachable_from()` and left `p5_price`,
`depth_fill_price_*`, the quantities and `top_order_volume_share` walking
region-wide levels. Reproduced verbatim: an executable ask of 100 beside an ask
fill of **9.258402**, and an executable bid of 90 beside a bid fill of
**1,000** — a physically impossible round trip, optimistic on both sides.

| field | before | after |
|---|---|---|
| ask `depth_fill_price_0` | 9.258402 | 100.00 |
| bid `depth_fill_price_0` | 1,000.00 | 90.00 |
| ask / bid `p5_price` | 1.00 / 1,000.00 | 100.00 / 90.00 |

Region-wide readings are preserved under `region_*` diagnostic names rather
than deleted, so the correction stays auditable.

**Accessibility is reachability, not NPC ownership.** CCP matches a buy order
by its range from its own location, so a station-ranged bid at another NPC
station is unreachable however NPC-owned it is, while a region-ranged bid in a
structure is reachable. `exec_reachable_volume_share` replaces
`station_volume_share` in the screen and brief flags.

**S2b.** `paper.book_quote` priced a pre-R1 snapshot that
`load_validated_book()` rejects — `price=9.2584, stale=False`. It now returns
`price=None, stale=True` with an explicit reason, and `books.spread_view` and
`backtest.measure_haircuts` refuse the same schema. A parametrised test asserts
all three.

## 2026-08-20 — Expires fails closed on every production path (§22 S1)

**Status: IMPLEMENTED + GREEN.** `plan.md` §22 S1. `uv run pytest -q` →
**753 passed, 7 deselected**, ruff check + format clean, `selftest` 12/12.

First phase of the **§22 track**, opened after an independent adversarial
review found defects in the §21 remediation itself.

**Reproduced through real `EsiClient.get()` calls against a counting
transport.** R8's tests exercised `fallback_expiry()` in isolation — which is
precisely why they passed while the production branches did not:

| | before | after |
|---|---|---|
| stored expiry after a malformed 304 at 12:01 | **12:00, already past** | 13:01 |
| second call skipped as still-fresh | **no** | yes |
| **transport requests** | **2** | **1** |
| history 200 with no `Expires` | 300 s, borrowed from orders | 83,100 s — next 11:05 roll |

- **The 304 defect.** R8 restored the stored expiry when the header was
  unusable. That value has necessarily lapsed — its lapsing is *why* the
  request happened — so it left a past timestamp and made the next call legal.
  Fetching before `Expires` is the one rule CCP bans accounts for.
- **The 200 defect.** One 300-second fallback was applied to every feed.
  History rolls once a day at 11:05 UTC; five minutes would re-ask 288 times a
  day for a resource that changes once.
- **No TTL is invented.** `unknown_expiry_boundary()` waits until the next
  moment this system was going to ask anyway — the next 11:05 roll for history,
  the operator's own cadence for orders and types, and the longest of the three
  for anything unmapped. Waiting until the next scheduled run costs nothing
  that was going to be fetched sooner.
- `safe_expiry()` never returns a time at or before now, and never shortens an
  expiry already trusted. `EsiResponse.expiry_unknown` makes a silent server
  visible to telemetry.

ETags, `last-modified`, pagination, budgets, the breaker and the error-limit
guard are unchanged and still tested.

## 2026-08-20 — A week is seven days, and a print is not a return (§20.3)

**Status: IMPLEMENTED + GREEN.** `plan.md` §20.3. `uv run pytest -q` ->
**740 passed, 7 deselected**, ruff check + format clean.

TOP PERFORMERS resumes after the §21 remediation track and lands as
`performers.py` plus the TOP page and a DESK tab. Three things the original
scope got wrong, each corrected with its reason and the old text preserved in
the plan:

- **The windows are 7 and 30 days, not 5 and 20 bars.** Five trading days is a
  week only because an equity exchange shuts at the weekend. EVE never closes.
- **The windows are calendar days, not bar counts.** A thin type trading on the
  22nd, 27th, 28th and 31st has bars that are not consecutive days, so counting
  seven rows back spans nearly a month — the same defect §21 R5 fixed in the
  lead-lag study, found again and fixed the same way.
- **The ranked return is print-resistant.** CCP does not filter outlier prints
  and `close` is a daily mean, so one trade drags a whole bar. The worst raw
  7-day reading on the real lake was **+49,699,900%**: *Batch Compressed
  Plagioclase II-Grade* had a single order set its 2026-08-02 average to 0.01
  ISK. A three-day median at each end costs 0.88 pp against the raw number
  where the data is sound, and an endpoint window with fewer than two
  observations is UNKNOWN — a median over one observation is that observation,
  which is why that name still read +49,699,900% after the first fix.

No further threshold was invented: genuinely repriced names still read in the
thousands of percent, and the raw number sits beside the robust one so the
operator can see them disagree (§21 R4).

## 2026-08-20 — Isolation proved, parity restored, a retracted number removed (§21 R8)

**Status: IMPLEMENTED + GREEN.** `plan.md` §21 R8. `uv run pytest -q` ->
**717 passed, 7 deselected**, ruff check + format clean, `selftest` **12/12**.
This completes R1-R8;
**none of them is LIVE_VALIDATED** and every phase still owes its live gate.

- **GUI isolation is proved by the import graph.** The old AST guard saw only
  *direct* imports, so it missed
  `gui.pages.spreads` -> `spreads` -> `books` -> `esi.client` -> `httpx`.
  `tests/_import_probe.py` imports every GUI module in one cold subprocess and
  asks `sys.modules` what loaded. Three module-scope ESI imports moved into the
  one function that fetches (`books.sweep_region`, `bars.ingest_history`,
  `universe.active_type_ids`).
- **Chart parity.** `build_series` tailed the frame *before* computing AVWAP
  and the overlays, so an anchor outside the display window produced bands that
  disagreed with the screen. Everything computes on full history; the canvas
  tails a view at paint time.
- **Regional data is keyed by region.** `bars_for_region()` /
  `last_close_by_region()`; a region with no bars returns empty rather than
  another region's numbers.
- **`Expires` fails closed.** Missing or malformed now means *wait*, not "no
  expiry"; a malformed `Expires` on a 304 keeps the stored valid expiry.
- **The retracted 16,789 is gone.** Both `esi/client.py` and `store/db.py`
  quoted it as fact; §17 D-10 withdrew it as a circuit-breaker cascade mistaken
  for data. They state the measured 241 of 17,325 (1.3%) and name the
  withdrawal.
- **`selftest` config parity learned about optional keys.** R2's defaulted
  settings made a perfectly valid operator `config.toml` fail parity, because
  the check required every example key while the loader did not.
  `optional_config_keys()` mirrors `build_section`, so drift still fails loudly
  and optional settings no longer do.

## 2026-08-20 — The threading contract, held structurally (§21 R7)

**Status: IMPLEMENTED + GREEN.** `plan.md` §21 R7. `uv run pytest -q` ->
**702 passed, 7 deselected**, ruff check + format clean.

Three defects of the same shape: a rule that held by convention.

- **Widget reads happened off the GUI thread.** `SpreadsPage.compute()` called
  `QComboBox.currentData()` on a worker; Qt widgets are not thread-safe and the
  value can change mid-read. `DeskPage.job_input()` captures widget state into
  an immutable tuple on the GUI thread before dispatch, and a test walks the
  AST of every `compute()` under `gui/` to fail on any widget access.
- **An input change during a job was declined, then painted stale.** Running
  input and queued input are tracked separately now: a change during a job is
  remembered, a result whose input has been superseded is discarded rather than
  painted, and a follow-up computation is guaranteed.
- **A worker could emit into a deleted page.** `PageJob.cancel()` makes a job
  emit nothing, checked before the work starts and again before the emit;
  `DeskPage.shutdown()` cancels and disconnects; `DeskWindow.closeEvent` shuts
  every page down before the widgets go.

The `desk` fixture and its helpers moved from `test_gui.py` into
`conftest.py`, so two test modules cannot drift on what a `DeskData` is; the
book helper now carries R1's executable-identity columns.

## 2026-08-20 — Freshness must change the number that is ranked (§21 R6)

**Status: IMPLEMENTED + GREEN.** `plan.md` §21 R6. `uv run pytest -q` ->
**689 passed, 7 deselected**, ruff check + format clean.

- **Decay reached nothing.** `freshness_factor` was computed, stored on the
  record, and ignored by the ranking, so a setup last measured a year ago
  sorted level with one measured yesterday. The old tests only asserted the
  field changed value — which is how a decorative number survives.
  `effective_expected_r()` is now the single expected-R contract and is what
  `rank_setups()` orders on; the raw blend stays visible beside it.
- **Decay scales rather than penalises**, so a negative expected R moves toward
  zero rather than deeper — a stale loss is a less certain claim, not a larger
  one. A missing input is UNKNOWN and never reads as 1.0.
- **Shrinkage now uses the eligible denominator.** It weighted by every closed
  trade while the mean R came only from rows carrying a realized R, so twenty
  closes with two scored outcomes were shrunk as twenty facts.
  `eligible_outcomes()` counts real outcomes and records report `eligible`
  beside `closed`.
- **No authority was added.** UNKNOWN still never outranks MEASURED, small
  samples are still ranked on their lower bound, and a test asserts the loop
  still never writes `setups.jsonl`, promotes, or mutates a setup.

## 2026-08-20 — The study must test the hypothesis that was frozen (§21 R5)

**Status: IMPLEMENTED + GREEN.** `plan.md` §21 R5, method amendment at §14.4.
`uv run pytest -q` -> **677 passed, 7 deselected**, ruff check + format clean.

§14.1–14.3 are frozen and are not edited; the amendment sits beside them.

- **The cohort was wrong.** H2 names doctrine-class hulls and their fitted
  modules with a regional catchment; the run pooled global destruction against
  every type in the lake. The recorded rho=0.027 on 473,606 observations is
  therefore **exploratory** and is not evidence about H2 either way. Results
  now carry a `cohort_declaration` naming population, catchment and evidence
  class, so a pooled run cannot later be read as confirmatory.
- **Lags were row positions, not calendar days.** `groupby.shift(-lag)` takes
  the next *observed* row, so a type trading on 1 and 10 January had the 10th
  labelled "lag 1". `exact_lag_frame()` joins `day + k` literally; a gap is
  UNKNOWN rather than filled by whatever came next.
- **Observations were treated as independent.** `independent_observations()`
  counts types — the conservative floor — beside the raw row count.
- **Ten tests, one alpha.** `LEAD_LAG_TESTS = 10`, `FAMILY_ALPHA = 0.001`
  (Bonferroni), and every lag row carries both `p_value_frozen_rule` and
  `p_value_family_wise`.

**Owed:** the confirmatory H2 run does not exist yet. The cohort is *declared*
but not *measured*, so the only lead-lag evidence here remains exploratory.

## 2026-08-20 — A quoted margin is not an expected edge (§21 R4)

**Status: IMPLEMENTED + GREEN.** `plan.md` §21 R4. `uv run pytest -q` ->
**663 passed, 7 deselected**, ruff check + format clean.

No threshold moved and no count was regenerated. What changed is what the
numbers are claimed to be.

- **`net_pct` -> `quoted_margin_pct`.** "Net" promises costs have been netted
  out; queue position, fill probability, waiting time, undercut risk and
  relist fees are not modelled at all. Every row carries
  `execution_model = "none"` and a literal `unmodelled_costs` list. The page
  says "QUOTED MARGIN, BEFORE EXECUTION RISK".
- **The 0.5x / 2.0x guards are operator heuristics.** §17 D-31 called them
  derived; that sentence is corrected in place with the original wording left
  visible. Counting observations beyond an already-chosen cutoff describes the
  cutoff, it does not derive it.
- **Broker fee is per station.** Standings are per corporation, so one scalar
  priced Amarr as Jita. `broker_fee_at()` / `with_broker_overrides()` take
  operator-**observed** effective rates; with none configured, behaviour is
  identical to before.
- **A stale traded average yields `STALE_AVG` and prices nothing.**
- **`relist_cost` is withdrawn.** It charged the broker fee on the whole order
  value; EVE charges on the old-to-new price change. It is now
  `relist_cost_unverified()`, and a test asserts nothing under `src/` consumes
  it — a wrong cost model is worse than an absent one, because it looks
  answered.

## 2026-08-20 — A sale cannot realise negative ISK (§21 R3)

**Status: IMPLEMENTED + GREEN.** `plan.md` §21 R3. `uv run pytest -q` ->
**651 passed, 7 deselected**, ruff check + format clean.

**No frozen verdict moved.** The golden haircuts never reach the new clamp, so
every previously measured value is identical and every cell is still NOT
PLAUSIBLE. What changed is what is *claimed*.

- **Stress prices are bounded.** `exit_close * (1 - haircut * multiple)` went
  negative for a wide book — bid 1 / ask 99 / mid 50 gives a ~0.98 exit
  haircut, so 2x stress produced -0.96 and a return worse than -100%.
  `stress_factors()` clamps the stressed haircut to 1.0, so zero liquidity is
  a total loss rather than an impossible price.
- **The Wilson bound is labelled correctly.** `z = 1.96` is a one-sided
  **97.5%** bound, not 95%. The number is unchanged — moving it would move a
  frozen verdict — and the error ran conservative.
- **Overlapping instances are no longer independent observations.**
  `effective_samples()` counts non-overlapping `horizon`-day blocks per type;
  `wilson_lb_clustered` sits beside `wilson_lb` rather than replacing it.
- **Friction reports its parts, and they compound**:
  `total = 1 - (1 - book)(1 - tax)`, less than the sum, because tax is levied
  on what the book already left.
- **`max_drawdown_pct` is withdrawn** — compounding overlapping trades in date
  order is not an equity curve, and the -100% readings at 2x/3x give it away.
  The values are preserved in the golden fixture under
  `backtest_withdrawn_pre_r3`.

## 2026-08-20 — A week-old bar is not a fresh signal (§21 R2)

**Status: IMPLEMENTED + GREEN.** `plan.md` §21 R2. `uv run pytest -q` →
**618 passed, 7 deselected**, ruff check + format clean.

**Completed days only, enforced at ingestion.** `last_completed_bar_date`
existed in `timeutil` but was never applied in production, so
`frame_from_history` accepted every date ESI returned — including today's
partial bar, whose high, low and average are all still moving. It is enforced
at the one ESI-to-bar mapping site now, and drops are counted in
`frame.attrs["incomplete_dropped"]` rather than being silent. The boundary is
the 11:05 roll, not midnight.

**Bar freshness is no longer the book's freshness.** `brief.freshness` was
derived entirely from the order book, so a history job failing for a week while
sweeps kept running rendered a week-old signal as fresh. `bars.bar_freshness()`
judges the bars on their own evidence — a test asserts its source never
mentions the book — and measures two independent failures: how many completed
days the newest bar is behind, and how long since ingestion last wrote. A lake
whose history job stopped still holds a bar dated the day it stopped, so bar
age alone cannot see the outage.

Stale bars now **downgrade every analytical gate to UNKNOWN**, not to FAIL: the
gate is unestablished, not false. `TypeBrief` carries `bar_freshness`,
`bar_stale_reason` and `bar_age_days` beside the book's own `freshness`.

Budgets: `[screen].max_bar_age_days` (3), `max_refresh_age_hours` (36).

**Config loading changed to allow this without breaking existing files.**
`build_section` rejected any section missing a key, so adding an optional
setting would have broken the operator's `config.toml`. It now honours a
field's declared default and requires only fields that have none — drift still
fails loudly, optional settings no longer do.

## 2026-08-20 — A spread nobody could trade is not a spread (§21 R1)

**Status: IMPLEMENTED + GREEN.** `plan.md` §21 R1. `uv run pytest -q` →
**602 passed, 7 deselected**, ruff check + format clean.

First phase of the operator-authorized **§21 remediation track**, which takes
priority over the queued §20.3 TOP PERFORMERS work (paused, not cancelled).
Nothing in this track retracts a measurement; §17 stays append-only.

### The defect

The book reduction grouped by `(type_id, side)` and kept price and volume
only. It discarded `location_id` and the `range` of buy orders. So the
region-wide lowest ask — typically Jita 4-4 — and the region-wide highest
bid, which may rest at another station or inside an Upwell structure the
operator cannot dock at, were joined and called an executable round trip.

Nobody can buy at one and sell at the other without hauling. Maker spreads,
screen pricing, paper fills, backtest haircuts and cross-region analysis all
consumed that reduction.

Separately, `sweep_region` wrote partial pagination into the normal lake and
`BookLake.latest` could promote it ahead of the last complete snapshot — and a
missing page can hold the true best level.

### What changed

`reduce_orders` now preserves location and buy-order range, and derives:

| column | meaning |
| --- | --- |
| `best_location_id`, `best_range` | where the region-wide extremum rests — **diagnostics** |
| `exec_location_id` | the one venue a round trip could happen at |
| `exec_price`, `exec_volume`, `exec_order_count` | that side's quote **at that venue** |
| `exec_is_structure` | venue is a player structure; docking rights are not in the lake |

The region-wide numbers are kept rather than deleted, so the correction stays
auditable and `spread_view` reports both (`region_best_bid` / `region_best_ask`
alongside the executable pair).

**The venue is anchored on the asks.** A sell order is executable only where it
rests, so to buy at all you must dock where the asks are; a bid may reach
across the region. §17 measured ~0% of ask volume in structures against
8.8–98.3% of bid volume, so anchoring on asks lands on a station the operator
can dock at. Among ask locations the busiest wins — deliberately not the
widest-spread one.

**Range fails closed.** A bid at the venue is reachable whatever its range. A
remote bid is reachable only when its range is `region`. `solarsystem` and the
numeric jump ranges need topology the reduction does not have, so they are
UNKNOWN and UNKNOWN fails.

**Partial sweeps are diagnostics.** `BookLake.write_partial` writes them under
a filename `latest()` does not glob, and `latest()` returns the newest
*complete* snapshot, scanning back past partial ones.

**One contract.** `books.load_validated_book()` returns a `BookSnapshot` that
decides completeness, executability and staleness once; `priceable` is empty
unless all three hold. `spreads.py` and `backtest.py` now read through it and
price from `exec_price` rather than the region-wide `best_price`.

### Consequence for existing data

A snapshot written before R1 does not know where its quotes rested, so it is
UNKNOWN and prices nothing until the region is swept again — **including the
stored 35,858-row Forge book**. That is the honest reading of missing data, not
a regression. Re-run `sweep-books`.

### Still owed

No row produced by this phase has been checked against a live client. After
the next sweep, confirm `exec_location_id` for a handful of liquid types is the
station actually traded at, and that a structure-resting best bid is flagged
rather than priced.

## 2026-08-20 — The spread is revenue when you are the one posting it

**Status: IMPLEMENTED + GREEN.** `plan.md` §20.2 and §17 D-31.
`uv run pytest -q` → **583 passed, 7 deselected**, ruff check + format clean.

### The claim, and why it does not contradict §17

§17's NOT PLAUSIBLE verdict was measured on a **taker** — cross the spread in,
cross it out, 14.7% round-trip friction against a +2.80% gross edge. A
**maker** posts both sides and *collects* that spread. The 98.8% median Forge
spread that made taking hopeless is what a maker is paid. Both readings are
true at once, because they are prices paid by opposite participants.

Maker round trip at the operator's skills: broker 1.300% in + broker 1.300%
out + sales tax 3.375% = **5.975%**.

### The dust bid, measured before the page was designed

Ranking a swept book by raw spread produces garbage. A 0.02 ISK bid against a
129,000 ISK ask reads as a **608,000,000%** edge, and nothing will ever sell
into that bid. Median raw net edge across 16,709 two-sided Forge types is
**+181%**; p90 is **+37,492%**. Arithmetically correct, economically
meaningless.

Every row is therefore anchored to the **traded average** — the ESI daily
mean, the one price transactions are known to have happened at. Of 16,381
types with both a two-sided book and an average:

| | |
| --- | --- |
| bid under **half** the traded average | **39.7%** |
| bid under a tenth | 19.8% |
| bid under a hundredth | 9.3% |
| ask above **twice** the average | 23.6% |

With the guards — bid ≥ 0.5× average, ask ≤ 2× average, ≥100 units/day —
**2,230** names survive and **1,590** carry a positive net maker edge, median
**+13.0%**, p90 **+57.3%**. Top name *Capital Ion Thruster*: bid 301,700, avg
597,400, ask 871,600.

The guards are **page controls, not constants**, and "show excluded" puts the
rejects back with their `DUST_BID` / `WIDE_ASK` / `NO_AVG` flags, so the guard
can be checked rather than trusted.

### What it refuses to pretend to know

Whether a posted order ever **fills**. Undercut risk — another trader posting
0.01 ISK inside you for a fraction of the capital, defended only by relisting
at a broker fee each time — and waiting time are **not** in the lake, and no
number on the page bounds them. Volume, top-of-book depth and the top order's
share of volume are reported as evidence, never as a probability, and the page
prints the caveat rather than implying an edge nothing has measured.

A book older than `costs.book_staleness_minutes` prices **nothing**. On the
operator's 121-minute-old sweep the page showed an honest zero, which is the
correct answer and not an empty one.

### Also landed

**SETTINGS** — an ntfy server/topic/token/priority form, at the operator's
request and ahead of §20.5. It writes to the `meta` table of `state.db`, not
`config.toml`: that file is the hand-edited, comment-rich contract of §11 D1,
and no TOML *writer* exists among the four locked runtime dependencies. Saving
a server with no topic is refused rather than half-stored. **Nothing is
delivered yet and the page says so** — no file under `gui/` may import an HTTP
client, so evaluation and delivery stay in §20.5, in the daemon.

A hub dropdown covers every configured hub plus an all-hubs entry, and SPREADS
is also a DESK tab.

## 2026-08-20 — DESK: pick on the left, decide on the right

**Status: IMPLEMENTED + GREEN.** `plan.md` §20.1. `uv run pytest -q` →
**566 passed, 7 deselected**, ruff check + format clean.

The operator's loop is "open it, walk the lists, chart each name, paper trade
the ones I like, tab out", and eight rail pages made that a tour. **DESK** is
one page: FOCUS / BOARD / SCANNER as tabs on the left, the chart on the right,
Paper Buy on every row. It is the first entry in the rail and **replaces
nothing** — every existing page still works.

**It composes rather than forks.** The left tabs are the real `FocusPage`,
`BoardPage` and `ScannerPage` classes over the same `DeskData`, so there is no
second watchlist to drift out of step with the first.

**There is still exactly one chart.** The window now owns the single
`ChartPanel` and *moves* it into whichever visible page declares a
`chart_slot` (`DeskPage.dock_chart`). DESK and CHARTS share one panel, one
anchor set and one set of overlays, so §19's "one window, re-pointed, never a
stack" holds literally — a test asserts `findChildren(ChartPanel) == 1`.
Charting from inside DESK no longer navigates away; charting from a page with
nowhere to put a chart still jumps to CHARTS.

Also: the chart opens on the **whole series** rather than 120 bars, since the
operator reads it on a 4K pane where 400+ bars resolve. The 60/120/250
selector stays for narrowing.

Lazy compute is unchanged and matters more here — a DESK tab computes when
first looked at, so opening the page costs its first tab rather than the sum
of all of them.

## 2026-08-20 — The body is the range, because the range is what was measured

**Status: IMPLEMENTED + GREEN.** `plan.md` §19.2 and §17 D-30.
`uv run pytest -q` → **560 passed, 7 deselected**, ruff check + format clean.

### The ask, and the measurement that answered it

The operator — a day trader — could not read the HLC bars and asked for
conventional candlesticks, reasoning that EVE trades 24/7, so there is no
session gap and yesterday's close is today's open.

Right about the market, wrong about this data, and settled by measuring it
rather than arguing it. `close` is the ESI daily **mean transaction price**,
not a last trade, so yesterday's mean is not where today opened. Across
**4,034,697 bars**, yesterday's close falls **outside** today's measured
`[low, high]`:

| population | open outside the day's range |
| --- | --- |
| all bars | **55.70%** |
| excluding `high == low` days | 46.10% |
| **tier OK** — what the desk charts | **68.97%** |
| tier THIN | 66.40% |
| watchlist | 58.07% |

A conventional body would hang off the end of its own wick on the *majority*
of bars. Not merely a fabrication — a visibly broken one. Clamping it into the
range would make over half the chart's bodies artefacts of the clamp.

### What landed

Range candles: a filled body spanning the day's **low→high**, crossed by a
notch at the **average**, coloured against the previous average. Body, notch
and colour are each a measured number or a comparison between two of them, and
§4's no-synthesized-`open` invariant is untouched.

Intraday direction is simply not in this lake — ESI records no sequence within
a day — so no chart drawn from it can say whether price rose or fell inside
the day. The notch's height inside the body is the honest substitute: high in
the range means the trading happened high in the range.

### Two regressions the operator's screenshots caught

**An index is not a candle series.** `signals/composite.py` builds a composite
with `high == low == close` by construction — an index level is one number a
day and has no intraday range — so every FORGE candle was a zero-height body
with a notch floating in it, and MARKET rendered as a field of dashes.
`ChartSeries.ranged` now reports whether the bars carry any range at all, and
a series without one is drawn as a level line.

**The canvas was not claiming its space.** `ChartCanvas` had the default
non-expanding size policy, so inside a `section()` block it split the leftover
height with its own title label and sat squashed at the bottom of a mostly
empty pane. It is now `Expanding` in both directions.

### Readability, which was the actual complaint

The 400-bar default gave ~3.5 px per bar on a desk pane. The chart now opens
at **120 bars** with a 60/120/250/all selector, and `ChartSeries.tail()`
slices every overlay array in one place so nothing drifts a bar out of step
with price. The form still degrades by measured slot width — body and notch,
then bare range, then the shaded envelope and close line.

## 2026-08-20 — Bars, because the eye reads a body as a fact

**Status: IMPLEMENTED + GREEN.** `plan.md` §19.2. `uv run pytest -q` →
**558 passed, 7 deselected**, ruff check + format clean.

### The change

The chart drew price as a line over a shaded high/low envelope. It now draws
**HLC bars**: a vertical high–low range per bar with a close tick on its
right, coloured against the **previous close**.

The `open` invariant (§4) is untouched, and the reason it is untouched is the
whole point of the form. A candlestick's body is the mark a reader trusts
first and questions last; drawing one here would require inventing the open it
measures, and the fabrication would arrive wearing the most persuasive shape
on the chart. An HLC bar has no body. Every mark on it — the range, the tick,
the colour — is a measured number or a comparison between two of them.

`bar_colours()` is a pure function over the close series, so the rule is
tested directly rather than through a paint event. A bar with nothing behind
it (the first, or one following a gap) is FLAT: no direction to report is not
the same as no change, and neither is guessed.

### Density

A 400-bar window on a narrow pane cannot resolve a tick, so the form degrades
by measured slot width rather than smearing into a block that would read as
more data than it is:

| pixels per bar | drawn |
| --- | --- |
| ≥ 4.0 | range + close tick |
| 1.5 – 4.0 | bare coloured range |
| < 1.5 | shaded envelope + close line (the previous rendering) |

## 2026-08-20 — A risk unit made of float noise is not a risk unit

**Status: IMPLEMENTED + GREEN.** `plan.md` §13.2 (amended) and §17 D-29.
`uv run pytest -q` → **556 passed, 7 deselected**, ruff check + format clean.

### The defect

§13.2's `measurable` gate asked only `atr > 0`. On the real lake that admitted
**1.33% of tracked types whose ATR is float noise** — near-flat series where
the twenty-day "range" is the last bits of a double, measured as low as
**1.7e-14 of price**. Everything that divides by a risk unit then exploded:
*Power Couplings* read RRS **−905 billion**. Both surfaces that rank by depth
— the board's value sort and the screen — select for exactly those names.

### The floor, derived rather than chosen

`atr/close` across 2,914 Forge types is **bimodal**:

| percentile | atr/close |
|---|---|
| p0.1 | 1.8e-14 |
| p1 | **1.6e-08** |
| p2 | **2.4e-05** |
| p5 | 5.0e-04 |
| p50 | 5.8e-02 |

Three orders of magnitude of near-empty space separate the degenerate cluster
from the working distribution. **`min_atr_fraction = 1e-6` sits at the top of
that gap**, marking 39 types (1.33%) UNKNOWN and touching nothing that trades.
1e-5 would take 1.82% and 1e-4 would take 2.68%, reaching into names that are
quiet rather than broken.

### One epsilon, one definition site

`atr.measurable_fraction` is the only place the question is answered, and the
floor is enforced **inside `atr_last`** — so RRS, the screen, the brief, the
scanner, the chart, the paper prefill and `risk_unit` all inherit it and none
can bypass it. The two per-bar paths (`setup.py`, `rrs_series`) call the same
function. It governs the **AVWAP sigma** as well as the ATR, because dip-σ
divides by sigma and a flat series makes both degenerate.

The composite reference ATR is deliberately **not** floored: an index carries
`high == low == close` (§17 D-23), so a price-relative test does not describe
it. Only the per-type denominator is guarded.

### Measured

| | before | after |
|---|---|---|
| types blocked | — | **39 (1.33%)** |
| max abs RRS | **9.05e11** | **1.19e7** |
| abs RRS > 1,000 | 77 types | **51** |
| RRS p1 / p99 | −1,966 / +2,661 | **−677 / +710** |
| RRS median | +3.18 | **+3.18 (unchanged)** |
| backtest instances | 147,140 | **145,655** (−1.0%) |
| backtest verdict | NOT PLAUSIBLE | **NOT PLAUSIBLE** |
| digest candidates | 25 | **25, none dropped** |

The unchanged median is the point: the body of the distribution never moved.
**No golden fixture needed regenerating** — on clean data the gate changes
nothing, the same evidence of surgicality the return clamp produced.

That the digest is untouched is worth stating plainly: the degenerate types
were never clearing costs. They were polluting the board's ordering and the
RRS distribution, not the candidate list.

### What this does NOT fix

**51 types still exceed abs RRS 1,000, and they are not degenerate.**
*Hemorphite II-Grade* has a perfectly healthy `atr/close` of 1.55e-04 and
reads RRS **−2,932** because it fell 45% in twenty bars — **2,936× its own
ATR**. That is RRS working, not failing: a quiet type that collapses really is
that weak relative to how it normally moves. The board's value sort therefore
still shows large magnitudes at the top. Several of those moves trace to
unfiltered ESI prints in `close`, and **reporting stays unclamped by design**
— the board prints what ESI printed.

### Also

- MARKET renders top weight as **10.0%** rather than `0.10000000000000003`,
  and entropy to three places.

## 2026-08-20 — The desk stops computing on the thread that draws it

**Status: IMPLEMENTED + GREEN.** `plan.md` §19.2 (amended) and §17 D-24…D-28.
`uv run pytest -q` → **544 passed, 7 deselected**, ruff check + format clean,
selftest **12/12**.

### The measurement that forced it

Against the operator's real universe — 2,947 tracked types, 4,052,335 bars:

| stage | before |
|---|---:|
| `load_desk()` | 5.6 s |
| BOARD `build()` | **56.5 s** |
| SCANNER `build()` | **145.9 s** |
| LEARNING `build()` | 9.1 s |
| **open to interactive** | **217 s** |

Every page was constructed in `DeskWindow.__init__`, and `ScannerPage`
called the whole scan engine from inside `build()`. `DeskPage.refresh()` then
called the same code for all eight pages on a **60-second** timer, on the GUI
thread. The desk opened in 3.6 minutes and was permanently behind its own
timer afterwards.

The earlier build validated the desk on 2,001 types and a 1.85M-bar lake —
about half the work — which is why this only appeared on first contact with a
full census.

### The contract, now recorded in §19.2

**The GUI thread never computes; it paints.**

- **Lazy pages.** `build()` lays out widgets and computes nothing. The window
  opens on MARKET; BOARD, SCANNER and LEARNING compute on first visit and on
  explicit refresh. A test asserts the scan entry point is not called during
  construction.
- **`compute` / `paint` split.** `heavy = True` pages compute on a
  `QThreadPool` worker and paint on the GUI thread. A test asserts the two
  actually run on different threads.
- **Last-good-on-failure.** A worker exception is *delivered*, not raised: the
  last completed result stays on screen under `could not compute: … — showing
  the HH:MM result`. A blanked panel would be worse than a stale one, because
  a blank reads as "nothing here" (§5).
- **Cache keyed on inputs, not on the clock.** `desk_input_key` stats the lake
  and book partitions and the four operator-editable config files. Daily bars
  change once a day; a 60-second full rescan was modelling the timer rather
  than the data.
- **Workers open their own SQLite connection** (`DeskData.thread_local_db`),
  because sqlite3 connections belong to the thread that opened them.
- `desk_input_key` lives in `gui/data.py`, which still imports no Qt; only the
  worker needs Qt and only the worker is in `gui/work.py`.

### Measured after

| | before | after |
|---|---|---|
| open to interactive | 217 s | **8.6 s** |
| timer tick, unchanged inputs | full 217 s rescan | **15 ms** |
| revisit an already-computed page | full recompute | **0.000 s** |
| first visit to SCANNER | blocking | 162.8 s, off-thread, stamped |
| first visit to BOARD | blocking | 63.5 s, off-thread, stamped |

Nothing about §3.2 changes. The desk still has no ESI client and no way to
acquire one; a background thread cannot make a local read unsafe.

### Four small items closed in the same pass

- **`verdict_banner` no longer returns an empty string when no study is
  stored** (§17 D-25). `data/` is gitignored, so a fresh clone had **no
  banner at all** on MARKET and SCANNER — a desk that had never measured
  anything looked exactly like one that measured and passed. It now says
  `Backtest verdict UNKNOWN — no study has run on this machine`.
- **The patch-notes watcher dedupes on the article URL** as well as on
  (date, label) (§17 D-26). CCP re-dated *Patch Notes - Version 24.01* and it
  landed twice under one source URL; the watcher runs daily, so the operator
  would have been asked to confirm one patch twice. The duplicate row is gone
  and seven distinct candidates remain.
- **`selftest`'s cost-model check derives tax and fee from config** (§17
  D-27) instead of hardcoding Accounting V's 3.375%, which asserted a skill
  level rather than the arithmetic.
- **`universe.seed_watchlist` is deleted** (§17 D-28). Nothing in `src/` ever
  called it; the §11 D4 roster was seeded through the documented `watch add`
  path on 2026-08-20 — 50 names, 50 resolved, 0 unresolved — and those entries
  are operator-owned and removable only by `watch remove`. An automatic
  seeder was deliberately not wired in: a re-seed would resurrect a name the
  operator had deliberately removed.

## 2026-08-20 — FORGE stops printing other people's typos

**Status: IMPLEMENTED + GREEN.** `plan.md` §17 D-22, D-23. `uv run pytest -q`
→ **527 passed, 7 deselected**, ruff check + format clean.

### What was wrong

FORGE had run **1,000 → 69,243** with single-day prints of **+1,661%**
(2026-08-02), +94% and +57%, against a median daily move of 0.029%.

Decomposition against the real lake named it exactly. On 2026-08-02 one
member — *Vanguard Resonant Cypher*, type 95640 — printed `close 10.07 →
22,450.00`, a **+222,839.4%** return, at a **0.75%** live weight. That single
member-day contributed **+1,661.59%** of the +1,661.37% the index moved. All
100 members were priced; no gap, no NaN, no missing bar.

**The chain-link was never at fault.** §19.1's composition-churn fixture is
correct and stayed green the whole way through. The poison was one line
upstream of it: `returns = closes.pct_change()` consumed **raw** closes, and
this repo's own §0 check #4 had already measured that CCP does not filter
outlier prints (`close/low` reaching 12.8 billion×). The ATR path has
winsorized for precisely that reason since Phase 2. The index path never did.

And "it reverts tomorrow" is not a defence: an arithmetic weighted-return
index can gain 222,839% and can only ever give back 100%. The level ratchets.

### What it cost

`power_index = Δref/ATR_ref` measured **1,478**, so every printed RRS sat in a
−1,479 band — digest, `board`, `brief`, the desk columns. RRS is one of the
four gates in the built-in setup, so the digest's "Nothing clears costs today"
was not an honest zero. It was a broken gate.

### The fix

- **`winsorized_member_returns`** clamps each member's daily return at `k ×`
  its own rolling median absolute return — same `k`, same window, same
  clamp-and-flag shape as `atr.winsorized_true_range`, because it answers the
  same measured fact. Config: `composite_return_clamp_k` (8.0), `_window`
  (60), `_floor` (0.20).
- **The floor is a fallback, never a lower bound.** Where a member has fewer
  than five observations the ceiling is UNKNOWN, and UNKNOWN clamps rather
  than passing through (§4). Clipping the ceiling *upward* to the floor would
  have handed a normally-stable member permission to print exactly the outlier
  this exists to catch.
- **Returns are computed explicitly**, not by `pct_change`, so a member needs
  a real bar on both `t-1` and `t`. pandas 3.0 no longer pads; pandas 2.x did,
  and would have booked a member's whole post-gap re-rating as one day. The
  code no longer depends on which is installed.
- **Clamped-day counts ride in every index's diagnostics** — clamping is
  visible, never silent.
- **`clamp_settings()`** reads the knobs in one place, because five call sites
  build indices and §19.1's whole point is that they share one engine.

### Fixtures first (§11 D5)

- `forge_outlier_2026-08-02.csv` — the **real** 100 members around the
  incident, with a provenance JSON naming the culprit type, its two closes and
  the measured returns.
- A test that builds the same fixture with the clamp **disabled** and asserts
  the >1,000% day reappears, so the clamp is demonstrably the fix.
- Synthetic spike-and-revert and 45-day-gap fixtures, each paired with an
  unclamped control.
- **The existing golden index fixture needed no regeneration.** On clean data
  the clamp touches nothing — the strongest evidence available that it is
  surgical rather than a general smoothing.

### Measured after

| | before | after |
|---|---|---|
| FORGE level over 415 bars | 1,000 → 69,243 | 1,000 → **981** |
| median abs daily move | 0.029% (with +1,661% days) | **0.340%** |
| max abs daily move | +1,661% | **2.08%** |
| `power_index` | 1,478.27 | **−3.28** |
| RRS, middle 84% of the universe | all ≈ −1,479 | p5 **−2.20**, p50 **+3.12**, p95 **+6.73** |
| digest candidates | 0 ("honest zero") | **25** |
| clamped member-days | — | 101,616 of 590,522 (17.2%) |

Nothing here touches a frozen formula, and the NOT PLAUSIBLE verdict is
unaffected: it rests on measured round-trip friction from the live book and
never reads RRS.

### Found while verifying, NOT fixed — the RRS tail is a per-type ATR problem

With the index fixed, **84.3%** of the tracked universe lands at p5 −2.20 /
p50 +3.12 / p95 +6.73. The remaining **15.7%** carries abs(RRS) > 10 and
**2.6%** exceeds 1,000 — because those types' own ATR is effectively zero
(measured: *Power Couplings* ATR **4.16e-11**, i.e. 1.6e-10% of close, giving
RRS −905 billion). `atr_last` rejects `atr <= 0` but a tiny positive ATR
passes, and `rrs = (Δsym − power_index × ATR_sym) / ATR_sym` divides by it.
This is independent of the index and was entirely masked by the −1,479 offset.
It lives on the frozen RRS/ATR surface, so it is recorded rather than patched.

## 2026-08-20 — The compatibility-date guard, salvaged from the parallel Phase-0 build

**Status: IMPLEMENTED + GREEN.** `plan.md` §17 D-21. `uv run pytest -q` →
**509 passed, 7 deselected** on Windows, ruff check + format clean, selftest
**12/12**.

Branch `claude/phase-0-gate-checklist-oucoil` was a parallel Phase-0 build
from another session, not an ancestor of this line. Before it was retired it
measured one thing this line did not know, and that measurement is now here.

### The guard

- **A pinned `X-Compatibility-Date` that is still in the future on CCP's
  UTC-11 clock is rejected on every ESI route** with a plain HTTP 400. The
  parallel branch hit it on 2026-08-18 (commit a7f5872) and lost every request
  until the pin was corrected. It is not a degraded run; it is a total outage
  caused by one config value, and it is invisible offline.
- **`timeutil.esi_compatibility_today`** is now the single place that clock is
  computed — UTC minus eleven hours, as a date.
- **`selftest` check 12, `compatibility date`**, fails any pin that is not at
  least **one full day** past on that clock. ESI itself would accept a pin
  equal to its own UTC-11 date; the extra day exists so a pin cannot clear
  offline and then start failing mid-run as the clock rolls over. A malformed
  pin is a named failure, never a crash. `selftest` is 11 checks → **12**.
- **The §11 D2 decision is untouched** — pinned, never floated — and so is the
  pinned value `2026-08-18`, which the guard measures as two days past and
  therefore sendable.
- The retired branch is preserved as tag **`archive/phase-0-first-light`**.

### Test-gate portability (Windows)

- `test_selftest_parity_check_passes_on_a_matching_file` interpolated a
  `tmp_path` straight into a TOML basic string. On Windows that lands
  backslashes where TOML reads escape sequences, and a `\U` inside
  `C:\Users\...` aborts the parse — the offline gate failed on the operator's
  own machine and nowhere else. The path is now written with `as_posix()`.
  Same class as the preceding GL-less-machine fix: the gate has to be green
  where it is actually run.

## 2026-08-20 — The desk: indices, operator setups, and the learning loop (third + fourth directives)

**Status: IMPLEMENTED + GREEN.** `plan.md` §19 and its §17 D-14…D-20 rows.
**509 offline tests green** (151 new, including 31 GUI tests run offscreen),
ruff clean, selftest **11/11**. LOC: **27,399** — 18,049 product (2,972 of it
the desk), 1,435 vendored, 7,880 tests, 35 launcher.

The hard line did not move: **no order execution, no client automation**. The
paper ledger and real-fill recording remain the whole execution surface.

### The index layer — `indices.py`, `config/sectors.jsonl`

- **One index engine.** `signals/composite.py` gained turnover/equal weighting,
  explicit membership and a ticker, and now serves FORGE, FORGE-EW and every
  sector index — no second construction path to drift.
- **FORGE** is turnover-weighted, chain-linked, base 1000, OK-tier members
  only. **Weighting is ISK turnover (units × price), never raw units** — raw
  units would make the index ~100% Tritanium. **FORGE-EW** inherits FORGE's
  membership exactly; `FORGE-EW − FORGE` is the breadth read and renders
  wherever FORGE does.
- **Nine seeded sectors** with real market-group subtree roots read from the
  live SDE, each able to set its own unit floor. A sector under its minimum
  member count renders **UNKNOWN with its reason**, never merged into a
  neighbour. `sector_for_type` returns None rather than falling back to the
  market index, so an unresolvable RRS scope is UNKNOWN.
- **Golden fixtures first** (§11 D5), including an adversarial churn case: a
  member joining at bar 60 priced 1,000× the rest, with dominant turnover,
  leaves the level at exactly 1000.0 across all four rebalances.

### Membership and trading floors (Amendment 1)

- The gate is **median 30-day UNIT volume**; turnover stays the weighting
  input. Three tiers: **OK** (≥1,000/day), **THIN** (100–999/day — carried,
  charted, scanned, badged on the board and the brief, excluded from FORGE),
  **below** (lookup only).
- **Price-pinned types are excluded from the index**: a close that did not move
  at all across the window is held by an NPC vendor, and a flat line absorbs
  index weight while reporting nothing. Still tradeable, still chartable.
- Rebuilt against the full lake and recorded in `plan.md` §11 D3 with the old
  derived floor left visible as superseded text: **OK 1,002 · THIN 999 ·
  below 17,151**, tradeable universe **2,001**, index-eligible **999** after
  3 pinned names came out; added 1,418, dropped 2,071.
- The cost is recorded beside the rule: the OK tier carries **33.1%** of the
  region's median daily ISK turnover, THIN another 9.9%. That ISK is given up
  on purpose, buying exit-ability with coverage.
- `state.db` gained an additive column migration (schema v2) — it holds the
  paper ledger and the watchlist, so it is migrated, never rebuilt.

### The operator setup engine — `setups.py`, `config/setups.jsonl`

- Nine typed condition kinds, all from daily H/L/C/V/order_count. Long-only.
  Validated loudly on load: an unknown kind, a misspelled parameter, a bad
  enum or an out-of-range value **stops the load and names the file and line**.
- Evaluation is tri-state; any UNKNOWN sinks the setup, and every result
  carries the reason it came out as it did.
- `backtest --setup NAME` measures an operator setup on the built-in rule's
  cost realism, horizons and limitations statement. The per-bar evaluator this
  needed is pinned to the last-bar evaluator by a parametrised test over every
  condition kind.
- `near_level` is **refused over history** rather than approximated: the level
  store is built from the whole series, so evaluating it per-bar is lookahead.
  The setup produces zero instances and the study says why.
- SMA/EMA/`ema_cloud`/`cross_within` are new indicator code and got golden
  fixtures first. An EMA is seeded on the SMA of its first `length` bars, not
  on bar 1, so "above the rising 21 EMA" cannot fire on bar 2.
- Three example setups ship, all marked `"example": true`.

### The scanner — `scanner.py`, `scan` / `setups` CLI

Built-in rule plus every enabled operator setup, grouped, with **honest zero
per setup next to its examined count**, UNKNOWN counted separately from
rejection, friction and book age on every hit, and the THIN badge. The
backtest banner is now one function in `backtest.py`, used verbatim by the
digest, MARKET and SCANNER.

### Qualified reasons — `reasons.py`, `config/reasons.jsonl`

- An opening requires a thesis, a setup tag and **at least one like tag**; a
  pass (`not_today` / `bad_signal`) requires **at least one dislike tag**. No
  tags, no record — and the refusal itself lands in the ledger.
- A typo'd tag is a loud error, not a dropped one.
- `not_today` clears today's queue only and **never** touches Focus.
- New CLI: `paper pass`, `reasons`. **Breaking:** every `paper open` call site
  now requires `--setup` and `--like`.

### The learning loop — `learning.py`, `learning` CLI

- Per setup and per tag: sample count, win rate with **Wilson lower bound**,
  average and median net R, expected R by shrinkage toward a **zero prior**,
  freshness decay — through the vendored `expected_r` engine.
- Ranking is evidence-weighted: 3-for-3 cannot outrank 40-for-70, and every
  UNKNOWN sorts below every measured setup. Below 20 closed trades everything
  reads UNKNOWN.
- **Regret tracking**: every recorded pass is measured forward on the
  backtest's horizons and cost realism. A pass is "right" only when the
  avoided trade would have lost money net of both haircuts and sales tax.
  Pending windows are pending; unpriceable passes are UNKNOWN.
- The digest may name a best and worst setup, gated at 20 closed trades.
- It never edits a setup, changes a frozen formula, or promotes anything.

### The desk — `src/evescreener/gui/`, `gui` CLI, `launch_gui.py`

- Eight pages: **MARKET · CHARTS · BOARD · FOCUS · SCANNER · PAPER ·
  LEARNING · HEALTH**.
- **Qt is optional and proven so**: `tests/test_headless.py` walks the import
  graph and a subprocess check asserts the CLI never puts PySide6 in
  `sys.modules`. §10.6's no-GUI non-goal is revoked (§17 D-14); §2's 42k-LOC
  lesson is now enforced structurally. The desk is 2,972 LOC.
- **The refresh timer is safe by construction**: `gui/data.py` has no ESI
  client, and a test proves nothing under `gui/` imports `httpx`, `urllib` or
  anything named `esi`. The desk shows staleness rather than curing it.
- **No candlesticks** — the bar contract has no `open`. Price is a line with
  the measured high/low envelope, over the frozen AVWAP σ ladder, SMA/EMA
  overlays, a shaded EMA-cloud ribbon, and the **HV levels, pivots and
  round-ISK levels `levels.py` has computed since Phase 2 and nothing had ever
  drawn**. Volume and participation subpanes, setup markers, open positions.
- **One chart window that re-points**, never a stack.
- **Blanks at the bottom whichever way a column sorts** — the table orders its
  own rows, because Qt's comparator reverses under a descending sort. Sorting
  never refetches.
- **Focus never auto-removes**; the only path is a button behind a confirm.
- **Paper Buy on every surface** through one prefilled form (live ask walk with
  book age, ATR stop, anchored-value target, setup tag from whichever setup
  fired) calling the same `PaperLedger` methods the CLI calls — a stale-book
  refusal renders inline *and* is recorded. A prefill that could not be
  computed is left empty with its reason.
- Verified on the live data directory: all eight pages open against 2,001
  tracked types with a 223-minute-old book correctly rendered as STALE.

### Config

New `[gui]` section (refresh, chart bars, SMA/EMA lengths, cloud lengths,
overlay toggles) and two new `[universe]` keys. `selftest` grew from 7 checks
to **11**: membership floors, sector map, setups, reason vocabulary.

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
