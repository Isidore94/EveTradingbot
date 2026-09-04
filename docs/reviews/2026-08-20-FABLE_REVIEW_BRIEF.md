# EveTradingbot remediation review brief for Fable

## Purpose

This is an audit brief for reviewing the remediation work requested after the
2026-08-20 repository review. It is evidence and review rationale, not a new
roadmap or an assertion that any remediation has passed. `plan.md`,
`CHANGELOG.md`, and `CURRENT_CHECKPOINT.md` remain authoritative.

This document does not contain private model chain-of-thought. It records the
reproducible evidence, failure scenarios, conclusions, uncertainties, and
acceptance criteria that a reviewer needs to challenge the implementation.

## Required review posture

Read `CLAUDE.md`, then follow its mandatory control-file order before examining
the remediation. Review the complete diff from the pre-remediation commit to
HEAD. Do not assume green tests prove analytical correctness. Reproduce every
important price or statistical claim from its inputs.

The operator has asked that these defects be fixed before the queued plan
section 20 work continues. The repository still requires one phase per
session. Confirm that
the implementation recorded the reprioritization and did not quietly combine
multiple phases or erase old measured results.

## Principal conclusion behind the remediation

The existing negative taker conclusion may remain directionally correct, but
several measurements were not audit-grade. The most important defect was that
order reduction discarded station and buy-order range, then treated a
region-wide best bid and best ask as an executable round trip. Independently,
partial snapshots and stale or incomplete bars could enter pricing and signal
paths. These are correctness issues, not requests to make the strategy look
better.

## Evidence and expected remediation

### R1 — Executable book identity and snapshot validity (critical)

Evidence:

- `src/evescreener/books.py::_sorted_levels` retained only price and volume.
- `reduce_orders` grouped by type and side, discarding `location_id` and the
  range of buy orders.
- `spread_view` joined region-wide extrema as one spread.
- Maker spreads, screen pricing, paper fills, backtest haircuts, and
  cross-region analysis consumed this reduction.
- `sweep_region` wrote partial pagination into the normal lake, and
  `BookLake.latest` could promote it ahead of the last complete snapshot.

Concrete failure:

The lowest ask can be at Jita 4-4 while the highest bid is at another station
or an inaccessible structure. The old code could call that station trading or
an immediate paper exit. A missing later page could also become the newest
book and be priced as if complete.

Expected properties:

- Executable quotes preserve region, location, and buy-order range.
- A station-trading spread is executable at one named location. Any transport
  path is explicit and separately costed.
- Partial sweeps are retained only for diagnostics and never become a
  priceable latest snapshot.
- Every consumer uses one central validated-book contract; warning flags are
  not a substitute for UNKNOWN-fail behavior.
- Existing last verified output survives a failed or partial refresh.

Review attacks:

- Put best ask and best bid at incompatible locations.
- Use station-, system-, region-, and jump-range buy orders.
- Put the true best level on a missing pagination page.
- Attempt paper open/close, screen, maker, backtest, and cross-region pricing
  from the partial snapshot.
- Check structure-access uncertainty is not silently considered executable.

### R2 — Completed and fresh bars (high)

Evidence:

- `bars.frame_from_history` accepted every returned date.
- `timeutil.last_completed_bar_date` was not used in production ingestion.
- Screen and other consumers evaluated the last stored bar without a shared
  bar-age gate; screen freshness was derived largely from book status.

Concrete failure:

If history refresh fails for a week while books stay current, an old signal can
be labelled fresh. If a provider or fixture supplies today's incomplete daily
bar, it can confirm a signal.

Expected properties:

- Ingestion structurally rejects bars newer than the last completed EVE day.
- Bar and book freshness are independent, explicit inputs.
- Missing, partial, or stale analytical inputs yield UNKNOWN and cannot confirm
  or price a recommendation.
- Boundary behavior is deterministic around daily downtime and UTC dates.

### R3 — Backtest price bounds and statistical claims (high)

Evidence:

- Exit stress used `exit_close * (1 - haircut * multiple)`, which becomes
  negative for wide books at 2x or 3x stress.
- The project called `z=1.96` a 95% one-sided Wilson bound.
- Overlapping instances were treated as independent observations.
- Sequential compounding of overlapping trades was labelled max drawdown
  despite no portfolio or capital-allocation model.
- The reported round-trip haircut already included sales tax, while control
  text called 14.7% friction "before tax."

Concrete failure:

With bid 1, ask 99, and midpoint 50, the exit haircut is near 0.98. The old 2x
stress calculation created negative sale proceeds and an impossible unlevered
long return below -100%.

Expected properties:

- Stress prices are bounded to economically possible values, with zero
  liquidity represented explicitly.
- Any change to a frozen verdict study is a visible plan-level amendment. The
  old rule and old result remain visible; revised results are regenerated and
  provenance-stamped.
- Confidence intervals match their label and account for clustering or block
  dependence where claims depend on them.
- "Max drawdown" is removed unless backed by an explicit, reproducible
  portfolio model.
- Book haircut, tax, and total modeled friction are reported separately and
  labelled accurately.

### R4 — Maker analysis and cost semantics (high)

Evidence:

- DUST_BID=0.5x average and WIDE_ASK=2x average were described as derived from
  measurement, but the cited measurement only counted observations beyond
  cutoffs that had already been selected.
- `net_pct` omitted queue position, fill probability, waiting time, undercut
  risk, and relists.
- One scalar broker rate was applied across hubs.
- `CostModel.relist_cost` was not the game's old-price/new-price order-change
  formula.

Conclusions to preserve:

- Bid plus the entry broker fee is a defensible denominator for return on ISK
  committed.
- A positive value without an execution model is a quoted maker margin, not an
  expected edge.
- The 0.5x and 2x cutoffs may be retained only as clearly labelled operator
  heuristics unless an outcome-based, preregistered, preferably out-of-sample
  derivation exists.

Expected properties:

- UI and reports say "quoted margin before execution risk," or equivalent.
- Fill probability and relist omissions are impossible to mistake for modeled
  zero costs.
- Broker inputs are location/hub-specific or explicitly operator-observed
  effective rates.
- Relist cost uses the actual old/new price terms and skill discount, or the
  feature is removed from analytical claims until it can.
- Stale or partial books price nothing. A stale traded-average anchor also
  cannot classify a row as OK.

### R5 — Lead-lag hypothesis fidelity (high)

Evidence:

- The frozen hypothesis concerned doctrine-class hulls/modules and regional
  catchments.
- The implementation pooled global destruction and all lake types.
- `groupby.shift(-lag)` meant the next observed row, not an exact calendar-day
  lag when dates were missing.
- Naive p-values assumed independent observations despite serial and
  cross-sectional dependence, and several lag/target comparisons were tested.

Concrete failure:

A type observed on January 1 and January 10 can have January 10 labelled
"lag 1." Pooling unrelated catalogue types can dilute or manufacture the
effect the stated H2 intended to test.

Expected properties:

- The doctrine cohort and catchment mapping are declared before remeasurement.
- Lags join exact `day + k` calendar dates or use a complete daily index.
- The old pooled result remains labelled exploratory rather than silently
  becoming proof about the narrower H2.
- Dependence-aware inference and a declared multiple-comparison policy are
  used if p-values or confidence claims remain.

### R6 — Learning freshness (medium)

Evidence:

- `learning.py` calculated `freshness_factor`, but expected-R and ranking did
  not use it.
- Existing tests established only that the side field changed.

Expected properties:

- Freshness changes expected-R or ordering according to one shared expected-R
  contract.
- The learning loop still never edits, promotes, or mutates a setup.
- Mixed rows without eligible realized-R use an explicit eligible denominator.

### R7 — Desk threading and lifecycle (high)

Evidence:

- `SpreadsPage.compute()` read `QComboBox.currentData()` off-thread.
- If inputs changed during an active job, `ensure_current()` could decline the
  new job and later paint the obsolete result.
- A page-bound `PageJob` could emit after the page was deleted during ordinary
  window close.

Expected properties:

- Every widget-derived value is captured on the GUI thread into immutable job
  input before dispatch.
- An input change invalidates the running result and guarantees a follow-up
  computation.
- Shutdown cancels or lifecycle-invalidates workers; no signal targets deleted
  pages.
- SQLite connections are opened and closed in their owning thread.

### R8 — Structural isolation, chart parity, and regional data (medium)

Evidence:

- Importing `gui.pages.spreads` transitively loaded `books`, `esi.client`, and
  `httpx`; the AST test checked only direct imports.
- The chart truncated its frame before calculating AVWAP and signal overlays,
  so an anchor just outside the display window could disagree with the screen.
- GUI data loaded home-region bars while SPREADS iterated configured regions.
- `EsiClient` retained the retracted claim that 16,789 types genuinely 404;
  the corrected measurement is 241.
- Missing or malformed `Expires` was treated as no active expiry, permitting
  an immediate future fetch.

Expected properties:

- Importing any GUI module does not load `httpx` or `evescreener.esi.client`.
- No file under `gui/` can acquire a network client through a pure-analysis
  module.
- `Expires` handling fails closed for cache-controlled market endpoints;
  malformed 304 responses retain the prior valid expiry.
- Chart overlays compute on the full analytical history and are tailed only
  for rendering.
- Region bars and traded averages are keyed explicitly by region.
- The false 16,789 measurement is removed or corrected everywhere.

## Cross-cutting acceptance criteria

- Preserve decision-support-only behavior: no client automation, orders, or
  character-acting SSO scopes.
- Preserve the six-column bar contract; never synthesize `open`.
- Do not change the frozen AVWAP sigma formula.
- No detector, score, or study change without fixtures first.
- UNKNOWN always fails; honest zero is valid.
- No failed refresh destroys the last verified output.
- Operator watchlist entries are never auto-removed.
- GUI remains headless-safe and network-incapable.
- The single `ChartPanel` ownership rule remains literal.
- Run `uv run pytest -q`, `uv run ruff check .`, and
  `uv run ruff format --check .` at every phase gate.
- Update `CURRENT_CHECKPOINT.md`, `CHANGELOG.md`, and `plan.md` exactly as the
  repository contract requires. Commit and push each small green phase.

## Required adversarial tests

At minimum, verify tests exist for:

1. Incompatible bid/ask locations and every buy-order range class.
2. Partial pagination never becoming priceable latest data.
3. Stale books, stale bars, incomplete bars, and stale traded averages all
   producing UNKNOWN/no price.
4. Missing and malformed `Expires` failing closed.
5. Wide-book 2x/3x stress never producing negative proceeds.
6. Exact calendar-day lead/lag behavior across gaps.
7. Learning freshness changing the value that is ranked.
8. Widget input changes during a job, worker failure, and window teardown.
9. No `QObject` access in any page `compute()`.
10. A subprocess import-graph assertion that every GUI module leaves `httpx`
    and `evescreener.esi.client` unloaded.
11. Full-history chart calculations agreeing with screen values after display
    tailing.
12. Multiple regions using only their own bars, averages, and books.

## Things that were checked and should not be rewritten gratuitously

- No order or EVE-client automation was found.
- No synthesized `open` column was found.
- The AVWAP formula itself was not identified as defective.
- Git history supported that the verdict rules preceded the recorded study
  results; method fidelity, not commit timing, was the problem.
- `ChartSeries.tail()`, ranged-series detection, and density degradation were
  internally consistent.
- Exactly one `ChartPanel` was constructed and moved between page slots.
- The maker `net_pct` denominator was reasonable; execution assumptions and
  naming were the defects.

## Reviewer output format

Fable should report findings only, ordered by severity. For each finding:

1. State the violated contract or incorrect claim.
2. Cite exact file and line.
3. Give concrete inputs and the wrong output or unsafe state.
4. Explain whether the new tests would actually catch the regression.
5. Give the smallest contract-preserving fix.

Separate correctness bugs, unsound analysis, invariant risks, unnecessary
complexity, and missing tests. Explicitly state when a remediation is correct;
do not invent churn merely to produce a finding.
