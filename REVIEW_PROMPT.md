# Repository review prompt

Paste everything below the line into Codex (or any reviewing agent) with the
repo checked out. It is deliberately self-contained: it tells the reviewer
where the contract lives, what has been built, what is deliberately *not*
built, and the specific places the work is most likely to be wrong.

---

You are reviewing **EveTradingbot**, a decision-support market screener for
EVE Online's in-game market. Read the repository and produce a critical review.
Assume the code compiles and the tests pass — that is already verified. Your
job is to find what is **wrong, unsound, unjustified, or missing**, not to
summarise what exists.

## 1. Read these first, in this order

The project keeps its whole control surface in four Markdown files. Read them
completely before looking at code; they are the contract the code answers to.

1. **`CLAUDE.md`** — the AI-session contract: mandatory workflow, hard
   invariants, locked tech stack, and the measured facts that override the
   plan's own estimates.
2. **`plan.md`** — the authoritative design. Pay particular attention to:
   - **§4 / §10** — hard invariants and non-goals
   - **§11** — locked implementation decisions (D1–D4)
   - **§17** — the **deviation record** (D-1 … D-31) and the measured-facts
     table. This is where every design decision that changed under measurement
     is recorded, with the number that forced it.
   - **§12.4, §13.6, §14.3** — verdict rules that were **frozen before** the
     studies ran and are never retrofitted.
   - **§19** — the desk (GUI)
   - **§20** — the current work track (DESK, SPREADS, TOP PERFORMERS, REGIONS,
     ALERTS+ntfy)
3. **`CHANGELOG.md`** — what has actually landed, newest first.
4. **`CURRENT_CHECKPOINT.md`** — the single active item, the verification
   baseline, and the **consolidated live-validation checklist** (every item on
   it is an operator action the build cannot self-certify).

Then read the code under `src/evescreener/`, the tests under `tests/`, and
`README.md`.

## 2. What this system is, and is not

It ports the analytical core of a private US-equity system (anchored-VWAP
bands, relative strength, levels, expected-R) onto EVE's public ESI API, and
delivers a daily Discord digest plus a local PySide6 desk.

**It is decision support only.** It never places orders, never automates the
EVE client, and uses read-only public ESI with no character-acting SSO scopes.
Treat any code path that would change that as a critical finding.

**Its headline result is negative, and that is not a bug.** Measured on
3,116,848 bars across 2,654 tracked types and 108,441 setup instances, the
setup class is **NOT PLAUSIBLE at every horizon** — and it fails on *friction*,
not direction (10-day gross +2.80% at a 55.7% win rate against 14.7%
round-trip friction before tax). The destruction lead-lag effect does not
survive either (ρ=0.027 on 473,606 observations against a 0.10 threshold). A
review that treats "the strategy doesn't work" as a defect has misread the
project; the correct question is whether the *measurement* is sound.

## 3. The invariants — check the code actually holds them

These are non-negotiable. For each, verify it structurally, not by reading
comments that claim it:

- **Never fetch before `Expires`.** The ESI `Expires` header is a correctness
  invariant and circumventing it is a bannable offence. ETags on every request;
  self-caps of 6,000 tokens/window on orders and 150 req/min on history.
- **No `open` column exists and none is ever synthesized.** The bar contract is
  `["datetime","high","low","close","volume","order_count"]` with
  `close ← ESI average`. See §17 D-30 for why candlesticks were refused and
  what was drawn instead.
- **The AVWAP σ formula is frozen** from Phase 2 forward.
- **Completed bars only. Missing or stale data is uncertainty, never
  confirmation.** Gates are tri-state and UNKNOWN always fails.
- **Honest zero beats a filled panel.**
- **No decision is recorded without its reasons**, and a refusal to record is
  itself logged.
- **The learning loop never edits a setup, changes a frozen formula, or
  promotes anything.**
- **Nothing under `src/evescreener/gui/` may import an ESI client** — or
  `httpx`, `urllib`, or `requests`. `tests/test_gui.py` enforces this by
  walking the AST of every file under `gui/`.
- **The core must run headless.** `tests/test_headless.py` walks the import
  graph to prove no core module pulls in Qt.
- **A failed publish never destroys the last verified output**; operator-entered
  watchlist names are never auto-removed.

## 4. Where the work is most likely to be wrong — review these hardest

### 4a. `src/evescreener/spreads.py` and `gui/pages/spreads.py` (newest, §17 D-31)

This is the newest and least-validated analysis. It reads the order book from
the **maker** side: post a bid, post an ask, collect the spread — the inverse
of the taker strategy §17 rejected. The maker round trip is broker 1.300% in +
broker 1.300% out + sales tax 3.375% = 5.975%.

Challenge specifically:

- **Is the "traded average" anchor sound?** Raw spread ranking is garbage (a
  0.02 ISK bid against a 129,000 ISK ask reads as a 608,000,000% edge), so rows
  are anchored to the ESI daily mean and flagged `DUST_BID` when the bid is
  under 0.5× it, `WIDE_ASK` when the ask is over 2×. **Are those thresholds
  defensible, or are they round numbers dressed as measurements?** The
  measurement behind them is in §17 D-31 — check that the numbers in the
  docstring actually match what the code computes.
- **Is `net_pct` computed against the right denominator?** It uses the bid plus
  the broker fee paid to post it. Argue for or against.
- **Is the fill-probability caveat honest enough?** Undercut risk and waiting
  time are *not modelled at all*. Does the page overstate anything?
- **Is the per-region keying correct?** Volumes and averages are keyed by
  region so that Amarr's book is never judged against Jita's traded average.
  Verify no path mixes them.
- **Does a stale book truly price nothing?**

### 4b. The chart (§17 D-30) — `src/evescreener/gui/chart.py`

Candlesticks were requested and refused **on measurement**: `close` is the
daily *mean*, not a last trade, and yesterday's mean falls outside today's
`[low, high]` on 55.7% of 4,034,697 bars (69.0% of tier-OK bars). So the body
is the day's measured low→high with a notch at the average. Check the density
degradation, the `ranged` level-series detection, and whether `tail()` slices
every overlay array in step.

### 4c. The desk's threading contract (§19.2)

`DeskPage` splits into `compute()` (pure, off-thread) and `paint()` (GUI
thread). Verify no page touches a Qt object inside `compute()`, that sqlite
connections never cross threads, and that a stale worker result cannot
overwrite a newer one. **Known loose end to assess:** a `PageJob` can emit into
a deleted page if the window is torn down mid-compute (`RuntimeError: Signal
source has been deleted`). Judge whether this is a real defect in the shipped
app or only reachable in abnormal shutdown.

### 4d. The single-chart rule (§19 Part 2, §20.1)

The window owns exactly one `ChartPanel` and *moves* it into whichever visible
page declares a `chart_slot`. Two panels would mean two anchor sets. Verify the
reparenting is sound and that no path constructs a second panel.

### 4e. Costs, and whether the negative verdict is trustworthy

`costs.py` is the model everything hinges on. If it is wrong, the NOT PLAUSIBLE
verdict is wrong. Check the maker/taker distinction, the skill-derived tax and
broker rates, the relist surcharge, and whether any consumer bypasses it.

### 4f. Statistical soundness

Check `backtest.py`, `learning.py`, `signals/` and the killmail lead-lag study
for: look-ahead bias, survivorship bias, multiple-comparisons problems,
in-sample threshold fitting, and whether "frozen before measurement" verdict
rules were genuinely frozen (git history will tell you).

## 5. What is deliberately not built — do not report these as gaps

- **Setups are out of scope for the §20 track** by operator decision; the
  action on every surface is a paper trade.
- **Alert evaluation and ntfy delivery are §20.5 and not built.** The SETTINGS
  page stores ntfy server/topic/token/priority in the `meta` table of
  `state.db` (deliberately **not** `config.toml` — that file is hand-edited and
  comment-rich, and no TOML *writer* exists among the four locked runtime
  dependencies: `httpx`, `pandas`, `pyarrow`, `numpy`). The page states plainly
  that nothing is delivered yet.
- **TOP PERFORMERS (§20.3) and REGIONS (§20.4) are queued**, not missing.
- **No momentum/breakout-continuation logic in the system's own recommendation
  engine** — EVE supply is elastic. Operator-defined setups in
  `config/setups.jsonl` may express anything, including continuation; the
  machinery measures them rather than arguing with them.
- **Nothing is `LIVE_VALIDATED`.** The whole live-validation checklist in
  `CURRENT_CHECKPOINT.md` is outstanding by design — the build cannot
  self-certify, because a machine's confidence in itself is not evidence.

## 6. What to produce

A written review, ordered by severity, with file and line references. For each
finding give: the defect, a concrete failure scenario (inputs → wrong output),
and a suggested fix. Separate clearly:

1. **Correctness bugs** — code that produces a wrong number or violates an
   invariant.
2. **Unsound analysis** — measurements or thresholds that do not support the
   claims made about them, especially anything fitted in-sample or any round
   number presented as a measured one.
3. **Invariant risk** — anywhere a hard invariant is held only by convention
   and could be broken by an ordinary future edit, rather than structurally.
4. **Simplification and reuse** — genuine duplication or unnecessary
   complexity. Do not propose stylistic churn.
5. **Missing tests** — specifically for the invariants above, and for any
   behaviour currently guaranteed only by a docstring.

Be adversarial about the analysis, not just the code. The single most valuable
thing you can find is **a number that is presented as measured but was actually
chosen**, or **a claim in a docstring that the code does not implement**. This
project's whole discipline is that measurements outrank intentions, so hold
its own documentation to that standard.
