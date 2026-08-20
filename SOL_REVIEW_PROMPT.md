# Sol review prompt — EveTradingbot

Paste everything below the line into Sol with the repository checked out at
`4c72da1` or later.

---

You are performing an independent adversarial review of **EveTradingbot**, a
decision-support market screener for EVE Online. Fable has already reviewed an
earlier state of this repository and its brief drove a remediation track; your
job is **not** to re-run that review. Your job is to find what is *still*
wrong — including anything the remediation itself introduced or claimed
falsely.

Assume the tests pass and lint is clean. That is verified and it is not
evidence of correctness. **Reproduce claims from their inputs.**

## 1. Read these first, in this order

The project keeps its entire control surface in Markdown. Read it before code.

1. **`CLAUDE.md`** — the session contract: mandatory workflow, hard invariants,
   locked stack, and the measured facts that override the plan's estimates.
2. **`plan.md`** — the authoritative design. In particular:
   - **§4 / §10** — hard invariants and non-goals
   - **§11** — locked decisions D1–D4
   - **§13.6, §14.3** — verdict rules **frozen before** their studies ran
   - **§14.4** — the lead-lag method amendment (added beside §14.3, not over it)
   - **§17** — the deviation record, D-1 … D-31, append-only
   - **§19** — the desk
   - **§20** — the daily-desk track (DESK, SPREADS, TOP, REGIONS, ALERTS)
   - **§21** — the remediation track, R1–R8
3. **`CHANGELOG.md`** — what landed, newest first.
4. **`CURRENT_CHECKPOINT.md`** — active state, the verification stamp, the
   per-phase owed live gates, and the consolidated live-validation checklist.
5. **`FABLE_REVIEW_BRIEF.md`** — the audit brief that drove §21. Treat it as
   *input*, not as a verdict: check whether each expected property was actually
   achieved, and whether achieving it broke something else.

Then read `src/evescreener/`, `tests/`, and `README.md`.

## 2. What this is, and what it already concluded

It ports the analytical core of a private US-equity system onto EVE's public
ESI API. **Decision support only**: no orders, no client automation, no
character-acting SSO. Any code path that changes that is a critical finding.

**Its headline result is negative and that is not a bug.** The setup class is
NOT PLAUSIBLE at every horizon, failing on *friction* not direction, and the
destruction lead-lag effect does not survive. A review that treats "the
strategy doesn't work" as a defect has misread the project. The question is
always whether the **measurement** is sound.

## 3. Review the remediation itself — hardest, and first

Eight phases landed in eight commits (`4d37893` … `a4bec28`), then §20.3
(`4c72da1`). For each, the question is not "did something change" but **"is the
new claim true, and is it held structurally or only by convention?"**

**R1 — executable book identity.** `reduce_orders` now preserves `location_id`
and buy-order `range`, and derives an `exec_location_id` chosen as the **busiest
ask location**. Challenge: is anchoring on asks sound, or does it fail for types
whose asks are thin but bids deep? Is `reachable_from`'s fail-closed treatment
of `solarsystem`/jump ranges too strict to be useful? Does `BookLake.latest`
scanning back for a complete snapshot ever return something *arbitrarily* old
without saying how old?

**R2 — completed bars and freshness.** Verify the `last_completed_bar_date`
boundary is right around 11:05 UTC and across month/year ends. `build_section`
now honours dataclass defaults — check that a genuinely malformed config still
fails loudly.

**R3 — bounds and statistics.** `stress_factors` clamps the stressed haircut to
1.0. Does the clamp silently *improve* any previously-recorded result? (The
claim is that the golden haircuts never reach it — verify.) Is
`effective_samples`' non-overlapping-block count defensible, or does it
overstate independence for types sharing market-wide moves?

**R4 — maker semantics.** The 0.5x/2x guards are now labelled operator
heuristics. Verify §17 D-31's original wording is genuinely still visible.
`broker_fee_at` takes operator-observed overrides — check nothing derives them.

**R5 — lead-lag fidelity.** `exact_lag_frame` joins `day + k`.
`independent_observations` counts **types**. Is that conservative floor
defensible, or is it so crude it makes every result unfalsifiable? **The
confirmatory H2 run does not exist** — verify nothing anywhere reads the pooled
exploratory result as evidence about H2.

**R6 — learning freshness.** `effective_expected_r` scales rather than
penalises. Does that ordering behave sensibly when expected R is negative?

**R7 — threading.** `job_input()` captures widget state on the GUI thread.
Verify the AST test cannot be trivially evaded, and that the queued-input path
cannot loop or drop a final computation.

**R8 — isolation and parity.** `tests/_import_probe.py` proves no GUI module
loads `httpx` or `evescreener.esi.client`. Three ESI imports moved inside
functions — check none of those functions is now called from the GUI. Verify
`Expires` genuinely fails closed on every path, including the 304 branch.

**§20.3 TOP** — the newest and least reviewed. Its guards were chosen after
measuring: 7/30 **calendar** days, three-day median endpoints, and a minimum of
**two observations** per endpoint. Challenge all three. Is the median-of-days
construction a return anyone would recognise? Does requiring two observations
silently exclude a whole liquidity class, and is that stated? Is
`week_pct_raw` beside `week_pct` genuinely enough, or is it a "we showed you
both" that a ranked table makes moot?

## 4. Invariants — verify structurally, not from comments

- Never fetch before `Expires`; ETags everywhere; self-caps honoured.
- **No `open` column exists and none is ever synthesized.**
- The AVWAP σ formula is frozen.
- Completed bars only; missing/stale/partial data is UNKNOWN, and **UNKNOWN
  always fails**.
- Honest zero beats a filled panel.
- No decision recorded without its reasons; refusals are logged too.
- The learning loop never edits, promotes, or mutates a setup.
- **Nothing under `gui/` may import `httpx`, `urllib`, `requests`, or anything
  named `esi`** — directly or transitively.
- The core runs headless; exactly one movable `ChartPanel` exists.
- A failed publish never destroys the last verified output; operator watchlist
  entries are never auto-removed.

## 5. The specific thing to hunt

This project's discipline is that **measurements outrank intentions**. The most
valuable findings, in order:

1. **A number presented as measured that was actually chosen.** R4 found one
   (§17 D-31's "derived" guards). There may be more — §20.3's three-day
   endpoint and two-observation minimum are prime suspects, and so is R5's
   type-count floor.
2. **A docstring or plan claim the code does not implement.** These files
   assert a great deal about their own behaviour.
3. **A remediation that fixed the test rather than the defect** — check whether
   each new test would actually fail against the *pre-remediation* code.
4. **An invariant held only by convention** where an ordinary future edit
   breaks it silently.
5. **A statistical claim that survived because nobody recomputed it.**

## 6. What is deliberately absent — do not report as gaps

- **Nothing is `LIVE_VALIDATED`.** Every §21 phase and §20.3 owes a live gate
  and none has been run. This is by design: the build cannot self-certify.
- **The confirmatory H2 lead-lag run does not exist.**
- **Alert evaluation and ntfy delivery are §20.5, not built.** SETTINGS stores
  the config in `state.db` `meta` — deliberately not `config.toml`, because no
  TOML *writer* exists among the four locked runtime deps.
- **§20.4 REGIONS is queued.**
- **`relist_cost_unverified` is intentionally unconsumed**; §0 open check #5 is
  open.
- **Setups are out of scope for the §20 track** by operator decision.
- **No momentum/continuation logic in the recommendation engine** — EVE supply
  is elastic. Operator setups in `config/setups.jsonl` may express anything.

## 7. Output

Findings only, ordered by severity. For each:

1. The violated contract or the incorrect claim.
2. Exact file and line.
3. Concrete inputs → the wrong output or unsafe state.
4. Whether the existing tests would catch it, and why not if not.
5. The smallest contract-preserving fix.

Separate: **correctness bugs**, **unsound analysis**, **invariant risk**,
**unnecessary complexity**, **missing tests**.

State plainly when a remediation is correct. Do not manufacture findings — an
empty category is a valid result, and this project prefers an honest zero to a
filled panel.
