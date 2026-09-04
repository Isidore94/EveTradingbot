# EveTradingbot internals — the incident behind every rule

Document role: **active reference.** The evidence behind the rules in
[`CLAUDE.md`](../CLAUDE.md), recovered on 2026-09-04 from `plan.md` §17, `CHANGELOG.md`
and `CURRENT_CHECKPOINT.md` when the JumpStarter control set was retrofitted.

**`CLAUDE.md` keeps the rule; this file keeps the reason.** The rules are binding from
`CLAUDE.md` alone — nothing here is optional context that weakens them. What is here is
why each rule exists: the incident, the measurements and the operator's own words where
they were recorded.

**Read the matching entry here before you change the behaviour a rule governs.**

**A rule with no entry here is a draft.** Where an incident could not be recovered the
entry says **"Evidence not recovered"** and where it was looked for — never an invented
one. Several rules below predate the code: they were design decisions in `plan.md`, and
their entry says so rather than dressing a decision up as an incident.

If you change a rule, change it in both places, in the same commit. The fuller record of
each incident is in the dated `CHANGELOG.md` entry named beside it (archived entries are
under `docs/CHANGELOG_ARCHIVE_2026-08.md`).

---

## Expires fails closed (2026-08-20, §22 S1 — an adversarial review)

**The rule, as it appears in `CLAUDE.md`:** *Never fetch before expiry. The `Expires`
header is a correctness invariant; circumvention is a bannable offence.*

**What happened.** §21 R8 made a missing or malformed `Expires` mean "wait", but its
tests exercised `fallback_expiry()` in isolation. In production a malformed `Expires` on
a **304** restored the *stored* expiry — a value that had necessarily lapsed, because its
lapsing is why the request happened — so the next call was legal a second later. A
history 200 with no `Expires` borrowed the orders feed's 300-second fallback and would
have re-asked 288 times a day for a resource that rolls once at 11:05 UTC. Reproduced
through real `EsiClient.get()` calls against a counting transport.

**What was measured.** Stored expiry after a malformed 304 at 12:01: **12:00, already
past** → 13:01 after. Transport requests for the pair: **2 → 1**. History fallback:
300 s → 83,100 s (the next 11:05 roll). Across the build: **zero 429s and zero 420s** on
~26,000 requests; the orders sweep used 830 of a 6,000-token self-cap.

**Operator's words.** None recorded; the rule is CCP's, quoted in `plan.md` §3.

**What is deliberately NOT done.** No TTL is invented. `unknown_expiry_boundary()` waits
until the next moment the system was going to ask anyway. `safe_expiry()` never returns a
time at or before now and never shortens an expiry already trusted.

**Reopen trigger.** CCP changes the cache contract. Nothing else.

---

## No open column exists (2026-08-20, §17 D-30 — the operator asked for candlesticks)

**The rule, as it appears in `CLAUDE.md`:** *No `open` column exists and none is ever
synthesized. `close ← ESI average`.*

**What happened.** The operator, a day trader, could not read the HLC bars and asked for
conventional candlesticks, reasoning that EVE trades 24/7 so yesterday's close is today's
open. Right about the market, wrong about this data: `close` is the ESI daily **mean
transaction price**, not a last trade. It was settled by measuring rather than arguing.

**What was measured.** Across **4,034,697 bars**, yesterday's close falls **outside**
today's `[low, high]` on **55.70%** of all bars and **68.97%** of the OK tier the desk
charts. A conventional body would hang off its own wick on the majority of bars. The
chart draws range candles instead: body = low→high, notch = average.

**Operator's words.** The request is paraphrased in `CHANGELOG.md` (2026-08-20, "The body
is the range"); no verbatim quote was recorded.

**What is deliberately NOT done.** No clamp of a synthetic open into the range — over half
the bodies would be artefacts of the clamp. Intraday direction is not in this lake and no
chart drawn from it says whether price rose or fell inside the day.

**Reopen trigger.** ESI publishes an open, or a last-trade series. Not before.

---

## Completed bars only (2026-08-20, §21 R2 — a week-old bar rendered as fresh)

**The rule, as it appears in `CLAUDE.md`:** *Completed bars only; missing or stale data
is uncertainty, never confirmation. Gates are tri-state and UNKNOWN always fails.*

**What happened.** `last_completed_bar_date` existed in `timeutil` and was never applied
in production, so `frame_from_history` accepted today's partial bar, whose high, low and
average were all still moving. Separately, `brief.freshness` was derived entirely from
the order book, so a history job failing for a week while sweeps kept running rendered a
week-old signal as fresh — a lake whose history job stopped still holds a bar dated the
day it stopped, so bar age alone cannot see the outage.

**What was measured.** Drops are counted in `frame.attrs["incomplete_dropped"]`. Budgets:
`[screen].max_bar_age_days` 3, `max_refresh_age_hours` 36. Stale bars downgrade every
gate to UNKNOWN, not FAIL: the gate is unestablished, not false.

**Operator's words.** None recorded.

**What is deliberately NOT done.** Stale inputs never render as silently-priced rows.
The boundary is the 11:05 UTC roll, not midnight.

**Reopen trigger.** None — this holds as long as ESI publishes a moving partial day.

---

## Honest zero beats a filled panel (2026-08-20, §17 D-22/D-23/D-25 — FORGE printed a typo)

**The rule, as it appears in `CLAUDE.md`:** *Honest zero beats a filled panel. "Nothing
clears costs today" is a valid, expected digest.*

**What happened, twice over.** First, the digest printed an "honest zero" that was not
one: FORGE had run 1,000 → 69,243 because one member printed `close 10.07 → 22,450.00`
(+222,839%) at a 0.75% weight, `power_index` measured **1,478**, and every RRS sat in a
−1,479 band — so a gate was broken, not empty. After the member-return clamp the same
digest carried **25** candidates. Second, `verdict_banner` returned an empty string when
no study was stored, so on a fresh clone a desk that had never measured anything looked
exactly like one that had measured and passed.

**What was measured.** FORGE over 415 bars: 1,000 → **981** after the clamp; max abs
daily move +1,661% → **2.08%**; RRS middle 84% at p5 −2.20 / p50 +3.12 / p95 +6.73;
clamped member-days 101,616 of 590,522 (17.2%). The existing golden index fixture needed
no regeneration — on clean data the clamp touches nothing.

**Operator's words.** None recorded.

**What is deliberately NOT done.** Reporting stays unclamped: the board prints what ESI
printed. Only the index path and the risk unit are winsorized. A zero is published with
the counts that explain it, so an outage never reads as an absence of opportunity.

**Reopen trigger.** CCP filters outlier prints (§0 check #4 says they do not).

---

## Golden fixtures first (2026-08-20, §22 S5a — a fixture regenerated after the fact)

**The rule, as it appears in `CLAUDE.md`:** *No detector/scoring change without golden
fixtures first.*

**What happened.** Friction had been computed as the sum of two one-sided percentages,
which reported **100.0%** where the true cost was **66.667%**. The corrected formula
shifted the golden backtest fixture (2× friction 7.00% → 6.80%, total 10.14% → 9.94%),
and the fixture was regenerated *after* the corrected case existed — the only reason
that was safe is that the frozen §13.6 verdict did not move and the pre-correction values
stay in git history. The opposite case is the evidence for the rule: the FORGE clamp
(D-22) and the ATR floor (D-29) both left the existing goldens byte-identical, which is
what proved them surgical.

**What was measured.** ATR floor: 39 types (1.33%) marked UNKNOWN, RRS median +3.18
unchanged, backtest verdict unchanged, digest 25 → 25. `tests/generate_golden.py` carries
the upstream AVWAP row loop verbatim and asserts a 1e-9 match.

**Operator's words.** None recorded; the rule is `plan.md` §11 D5.

**What is deliberately NOT done.** A fixture is never generated by the code it is meant
to pin. The AVWAP σ formula cannot change without regenerating every golden and operator
sign-off together.

**Reopen trigger.** None.

---

## The desk has no ESI client (2026-08-20, §21 R8 and §22 S8 — the guard saw only direct imports)

**The rule, as it appears in `CLAUDE.md`:** *Nothing under `src/evescreener/gui/` may
import an ESI client. The desk refreshes on a timer; it shows staleness rather than
curing it.*

**What happened.** The first guard walked the AST for *direct* imports and missed the
chain `gui.pages.spreads → spreads → books → esi.client → httpx`. `tests/_import_probe.py`
now imports every GUI module in one cold subprocess and asks `sys.modules` what loaded;
three module-scope ESI imports moved into the one function that fetches. Then §22 S8
found the probe rejected only `httpx` and `evescreener.esi.client` by name — a page could
have reached the network through `requests`, `urllib`, `aiohttp` or any other ESI module.

**What was measured.** Three module-scope imports relocated (`books.sweep_region`,
`bars.ingest_history`, `universe.active_type_ids`). `socket`, `ssl`, `http.client` are
allowed and the probe says why: Qt and the stdlib load them regardless.

**Operator's words.** None recorded. The reason is the `Expires` invariant: a refresh
timer that could fetch is a timer that could fetch before expiry.

**What is deliberately NOT done.** The desk does not cure staleness. A `haulfreight`
column may be *shown*; the page may not be able to *fetch* one.

**Reopen trigger.** None.

---

## Membership is decided on unit volume (2026-08-20, §11 D3 amended — the floor was ISK)

**The rule, as it appears in `CLAUDE.md`:** *Membership is decided on median UNIT
volume; weighting on ISK turnover. THIN names (100–999 units/day) are carried, charted,
scanned and badged everywhere — and excluded from FORGE.*

**What happened.** The census derived the tradeable universe from median 30-day ISK
turnover. Rebuilt on units, the roster changed by more than half: **added 1,418, dropped
2,071**. Raw units as a *weighting* input would have made the index ~100% Tritanium, so
turnover stays the weight.

**What was measured.** OK **1,002** · THIN **999** · below 17,151; tradeable universe
2,001; index-eligible 999 after 3 price-pinned names came out. The OK tier carries
**33.1%** of the region's median daily ISK turnover, THIN another 9.9% — ISK given up on
purpose, buying exit-ability with coverage. The old derived floor is left visible in
`plan.md` §11 D3 as superseded text.

**Operator's words.** None recorded.

**What is deliberately NOT done.** Price-pinned types (a close that did not move across
the window is held by an NPC vendor) are excluded from the index but stay tradeable and
chartable. A sector under its minimum member count renders UNKNOWN, never merged.

**Reopen trigger.** The operator changes the floor in `config.toml`; §11 D3 records what
the old one was.

---

## A refusal is a record (2026-08-20, §22 S7 — the one decision the ledger lost)

**The rule, as it appears in `CLAUDE.md`:** *No decision is recorded without its
reasons. No tags, no record — and the refusal itself goes in the ledger.*

**What happened.** `record_pass()` raised for an invalid action, and `_clean_tags`
raised for an unknown tag, **before** `_refuse()` was reached — so the one class of
decision the ledger lost was the one made wrongly. Both now route through `_refuse()`,
recording the attempted action and tags with the reason. The same defect was avoided in
`haul record` by design (§23 H2) because this entry existed.

**What was measured.** Tests assert the exception, the record, and that no `pass` event
is written.

**Operator's words.** None recorded; the requirement is `plan.md` §19.4.

**What is deliberately NOT done.** The decision is still refused and the unknown tag is
never accepted. `not_today` clears today's queue only and never touches Focus.

**Reopen trigger.** None.

---

## The GUI thread never computes (2026-08-20, §17 D-24…D-28 and §21 R7, §22 S3 — the desk opened in 217 s)

**The rule, as it appears in `CLAUDE.md`:** *The GUI thread never computes; it paints.
Pages compute on a worker from an immutable generation captured on the GUI thread.*

**What happened.** Every page was constructed in `DeskWindow.__init__`, `ScannerPage`
called the whole scan engine from `build()`, and a 60-second timer re-ran all eight pages
on the GUI thread. The earlier build had validated on half the universe, which is why it
only appeared on first contact with a full census. Then R7 found widget reads off the GUI
thread (`QComboBox.currentData()` on a worker), and S3 found the worker still read
`self._running_input` off the page and that a data-only refresh was silently dropped.

**What was measured.** Against 2,947 tracked types and 4,052,335 bars: open to
interactive **217 s → 8.6 s**; a timer tick on unchanged inputs **217 s → 15 ms**;
BOARD `build()` 56.5 s and SCANNER 145.9 s, now off-thread and stamped.

**Operator's words.** None recorded beyond the screenshots that prompted it.

**What is deliberately NOT done.** The worker never acquires an ESI client; a background
thread cannot make a local read unsafe. An AST guard fails on any widget access or any
`self._running*` read inside a `compute()`.

**Reopen trigger.** None.

---

## A number in prose is not a measurement (2026-08-20, §22 S8 and §17 D-10 — three runs disagreed)

**The rule, as it appears in `CLAUDE.md`:** *A number in prose is not a measurement.
Every quoted figure carries its as-of date, membership, denominator and command.*

**What happened.** §20.3 quoted TOP PERFORMERS figures with no as-of date, membership,
denominator or command. An independent reproduction disagreed with all of them and a
third run disagreed again — and none could be shown right or wrong, because none had
recorded what it measured. Earlier, "16,789 of 19,152 types 404 on history" had been
written into `plan.md`, `CHANGELOG.md` **and `CLAUDE.md`** as a measured fact; it was a
circuit-breaker cascade mistaken for data. The completed crawl measures **241 real 404s
of 17,325 requests (1.3%)**.

**What was measured.** `provenance.py` emits a `MeasurementReport` with as-of,
membership, filters, input identity, denominators, command and git revision. The old
§20.3 figures are labelled a historical snapshot and left in place.

**Operator's words.** None recorded.

**What is deliberately NOT done.** The old figures are not replaced with fresher ones —
that would repeat the mistake. §17 stays append-only; a retraction is a new row.

**Reopen trigger.** None.

---

## Review by reproduction (2026-08-25/26, §17 D-35, D-35a, D-35b — and the one all three missed)

**The rule, as it appears in `CLAUDE.md`:** *Review by reproduction, not by reading. A
reviewer runs the branch against a copy of the real lake.*

**What happened.** Three adversarial passes over §23 ran the code with concrete inputs
and found **nineteen** defects between them, including a losing round trip that ranked as
a plan (one ask of 100 @ 100 against one bid of 100 @ 50 ranked at **−51.7%** with zero
rejections) and a fix that was cosmetic in production because all three of its tests
called the primitive. The one all three missed — parquet's nullable `issued` column
coming back as a truthy float `NaN` and aborting every scan — was found only by running
`haul scan` against a real five-hub lake.

**What was measured.** 556 of 314,793 Forge depth rows carried a `NaN` stamp. The scan
then ran: 29,211 candidates priced, 46,966 rejected, 15 plans. Profiling during the same
pass: 18.8 s per pair, 18.4 s of it in `curves_from_depth` → **1.4 s**.

**Operator's words.** 2026-08-25: *"build first, evaluate against competitors and live
gates afterwards"* (§17 D-33) — the authorization the reviews were checking against.

**What is deliberately NOT done.** A reviewer never runs an ESI-fetching subcommand and
never points a probe at the live data dir.

**Reopen trigger.** None.

---

## Fail-before-fix is proven (2026-08-26, §17 D-35a — three tests that agreed with each other)

**The rule, as it appears in `CLAUDE.md`:** *Every fix ships with a test proven to fail
on the un-fixed code.*

**What happened.** FIX 11b's three tests all passed. The fix never reached a real sweep:
`reduce_depth` wraps the jump-distance function in a closure that did not forward
`.knows`, so production read `None` off it. The tests called the primitive directly and
one of them blessed the stripped path. A 45-system corridor through `reduce_depth`
counted (0 range, 2 unresolvable); it now counts (1, 1).

**What was measured.** Eleven of twelve fixes real; one cosmetic; six Low residues.

**Operator's words.** None recorded.

**What is deliberately NOT done.** A `tester` that did not write the fix writes the
tests, and the builder may not weaken them. The reviewer reverts and re-runs
independently.

**Reopen trigger.** None.

---

## A probe that runs the system writes to it (inherited from JumpStarter, 2026-09-04)

**The rule, as it appears in `CLAUDE.md`:** *A probe that runs the system writes to it.
Point `haul scan`, `sde`, the desk and every other subcommand at a copy under
`EVESCREENER_DATA_DIR` and say which.*

**What happened.** In the project JumpStarter was distilled from, a reviewer reproducing
a claim ran a build command against the live store and put thirteen unprovenanced rows
into it. **Evidence not recovered in this repo**: no such incident is recorded here. The
rule is adopted because `state.db` holds the paper ledger and the watchlist and is not
regenerable, and because every `evescreener` subcommand writes under the data dir.

**Reopen trigger.** None.

---

## Another session is in this repository (inherited from JumpStarter, 2026-09-04)

**The rule, as it appears in `CLAUDE.md`:** *Assume another session is in this
repository. Verify the branch immediately before staging and immediately before pushing;
stage explicitly by path, never `git add -A`; never `git stash`; after committing,
confirm your work landed.*

**What happened.** In the source project, in one afternoon: one commit carried two
unrelated packets; a second session's commit swallowed a third's uncommitted code; a
pushed branch was deleted underneath the session that created it. **In this repo:** the
2026-08-20 entry "The compatibility-date guard, salvaged from the parallel Phase-0 build"
records a parallel branch from another session (`claude/phase-0-gate-checklist-oucoil`)
that was not an ancestor of this line and had measured something this line did not know;
and `CHANGELOG.md` 2026-08-25 records three entries "lost to silent no-op replaces"
(commit 2ccfb9e). Two sessions in one tree is the normal state here, not the exception.

**What is deliberately NOT done.** `git stash` is not used to get a clean test count.
Restore the single file with `git checkout <base> -- <path>` instead.

**Reopen trigger.** One session per checkout, enforced rather than assumed.

---

## The control file itself goes stale (2026-08-20, §17 D-10 — CLAUDE.md carried the wrong number)

**The rule, as it appears in `CLAUDE.md`:** *This file is one of those docs. A line in
`CLAUDE.md` that the code contradicts is not authority; correct it or leave a dated note.*

**What happened.** `CLAUDE.md` stated as a measured fact that 16,789 of 19,152 types 404
on history. It was a circuit-breaker cascade mistaken for data. The line was corrected
with the retraction left visible beside it — which is the form this rule requires — and
`plan.md` §17 D-10 carries the withdrawal. The same file said until 2026-09-04 that "these
four files are the whole control set" while the repo had carried seven root-level review
prompts for two weeks.

**What was measured.** 241 real 404s of 17,325 requests (1.3%). Seven stray root files,
1,505 lines, found by the JumpStarter audit on 2026-09-04.

**What is deliberately NOT done.** A stale line is not silently deleted. It is corrected
or tombstoned with a date, and the operator is told.

**Reopen trigger.** None.

---

## A suite run under a known condition is not a baseline (2026-09-04, the retrofit — recorded from the gate shape)

**The rule, as it appears in `CLAUDE.md`:** *The suite is not a baseline unless it ends
in `7 deselected` and the environment carries the `gui` extra.*

**What happened.** Every gate stamp in this repo reads `N passed, 7 deselected`: the
seven are the `@pytest.mark.network` live tests, excluded by `addopts`. A worktree synced
without `--extra gui` has no PySide6, so the offscreen desk tests do not run and a
smaller number prints as a pass. **Evidence not recovered as an incident**: no wrong
baseline has been recorded here yet. The rule is written from the shape of the gate so
that the first one is caught.

**What was measured.** 2026-09-04 baseline: `uv run pytest -q` → **1,090 passed, 7
deselected in 49.12 s, process exit 0** on `dd6f4d6`; ruff check and format clean;
`selftest` 12/12.

**Reopen trigger.** The network tests move to their own suite, or the GUI extra becomes
mandatory.

---

## A heuristic never under-earns its own best part (2026-09-04, §23.21 and §17 D-37 — the basket packed formulas)

**The rule, as it appears in `CLAUDE.md`:** *A heuristic never under-earns its own best
part. A basket, a packing or a composition shown beside a single plan is floored at that
plan under the same caps, and says so when the floor bound.*

**What happened.** The mixed-cargo basket filled a hold greedily by ISK per m³. On the
2026-08-28 five-hub generation with a 60,000 m³ / 250 M ISK profile it packed twenty
near-zero-volume blueprints, formulas and insignia (1.8 m³ in total), spread them over
four destinations without charging four trips, and netted **15.6 M** where the best single
plan on the same capital netted **23.6 M**; on 2026-08-26 it was 13.0 M against 30.7 M.
The heuristic was labelled HEURISTIC and shown beside the single plan, exactly as the plan
said — and it still put a worse number in front of the operator with nothing on the page
to say it was worse.

**What was measured.** `docs/reviews/2026-09-04-HAULING_ARBITRAGE_ANALYSIS.md` §3.4: basket
net / best single net = 42% (08-26) and 66% (08-28); basket volume 8.5 m³ and 1.8 m³ of a
60,000 m³ hold; 292 and 363 plans withheld for overlap.

**Operator's words.** "go ahead and implement fixes for all of these."

**What is deliberately NOT done.** The greedy is not replaced by a solver: the floor
guarantees the operator never sees a basket worse than a plan he could take instead, which
is the property that matters; optimality is not claimed and the label still says so.
Persistence weighting is an **objective the operator chooses**, not a change to the
default rank, because two generations are not a tape either.

**Reopen trigger.** A real basket that beats its floor by more than the floor beats the
old greedy, over a shadow week — that is the signal a solver would be worth its lines.

---

## A snapshot is measured against the snapshots before it (2026-09-04, §23.21 — 44.5% survived)

**The rule, as it appears in `CLAUDE.md`:** *A snapshot is not a tape, and the size of
that caveat is measured, not asserted: a ranked row carries its survival across the stored
prior generations, UNKNOWN until enough exist.*

**What happened.** Every hauling surface carried the caveat "a snapshot is not a tape" as
text. Nothing measured it, although the lake kept every sweep of a day in that day's
partition. Two complete five-hub generations existed on disk, 46.5 h apart.

**What was measured.** Same synthetic profile on both: 831 and 961 ranked plans; **370 of
831 (44.5%)** still plans in the next generation; top 10 → 4 still plans, 3 still top 10;
the 08-26 top-25's net re-priced on the 08-28 books to **94.7 M of 260.6 M (36%)**;
quantity ≤ 5 plans survived at **33%**, bulk at **51%**; survivors' net ratio p10 0.16 /
median 1.06 / p90 11.38.

**Operator's words.** "go ahead and implement fixes for all of these."

**What is deliberately NOT done.** The one-hour decay — the horizon a haul actually
spans — is not measured, because hourly generations do not exist on this machine yet; the
daemon produces them and the checklist owes them. `persistence_min_generations` keeps the
column UNKNOWN until they do.

**Reopen trigger.** Hourly generations on disk, and the shadow-week diary disagreeing
with the computed column.
