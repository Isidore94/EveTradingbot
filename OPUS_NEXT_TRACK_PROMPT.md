# Opus prompt — EveTradingbot §23: from testbed to measurement

Paste everything below the line into a fresh Opus session with the repository
checked out at `73a058a` or later.

---

You are continuing EveTradingbot. Read `CLAUDE.md`, `plan.md`, `CHANGELOG.md`,
`CURRENT_CHECKPOINT.md`, then `FABLE_REVIEW_BRIEF.md`, `SOL_REVIEW_PROMPT.md`
and `SOL_REVIEW_PROMPT_S22.md`, in that order, before proposing anything.

This prompt is operator authorization to open **plan.md §23**, an append-only
track. It does not authorize changing a locked decision, the frozen AVWAP σ
formula, or a frozen verdict rule. Every historical figure you correct keeps
its original wording visible. **One phase per session**; future sessions
resume through `CURRENT_CHECKPOINT.md`.

## Why this track exists — the state the review found

A full review of the local repository on 2026-08-20 found the *machinery*
complete and heavily tested (828 tests, 23.5k LOC product) and the *testbed*
never actually exercised:

| fact | consequence |
|---|---|
| `data/streams/paper.jsonl` **does not exist** | zero paper trades have ever been recorded; the learning loop has never had an input |
| one book sweep on disk, from before §21 R1 | every pricing surface correctly shows an honest zero until `sweep-books` runs again |
| bars exist for **Jita only** (`region=10000002`) | goal 3, regional trading, cannot be measured at all |
| `destruction` table: **0 rows**; `anchors`: **0 rows** | H2 cannot be confirmed for a second reason (no data), and every AVWAP band runs from a synthetic anchor grid |
| the on-disk backtest reports **+2236.27% gross** at 10 days | the instance returns are **not winsorized**, so the same 0.01 ISK prints §20.3 fought contaminate the backtest's "directional" claim. The verdict (NOT PLAUSIBLE, expectancy −151% after costs) does not turn on that number, but the sentence "the setup is not directionless" rests on it and is unsupported |
| `CLAUDE.md` still says **+2.80% gross on 108,441 instances** | that is an earlier run; the current artefact says +2236% on 145,655. The prose was never updated — the exact §22 S8 pattern, a floating number |

The three operator goals and what blocks each:

1. **Jita station trading via technical analysis** — machinery complete;
   verdict measured on a *taker* strategy, one setup class, print-contaminated
   gross, pre-R1 book. A real measurement of the wrong inputs, since repaired
   and never re-run.
2. **Spread / maker trading** — SPREADS quotes a margin honestly labelled
   "before execution risk". **There is no way to measure fills.** The paper
   ledger is a taker ledger (buy at the ask-walk); a maker trade needs
   post-and-watch semantics that do not exist.
3. **Regional trading** — `crossregion.py` exists and nets real PushX freight,
   but no secondary-hub bars or books are on disk. §20.4 is queued.

## Phases, in order. Do not start a later one because it is adjacent.

### P1 — Run the testbed clean, once, end to end

Nothing analytical changes in this phase. It produces the first **clean**
measurement so every later phase has something real to compare against.

- `sweep-books` for **all five configured hubs**; confirm each yields a
  complete, post-R1 snapshot that `load_validated_book()` accepts.
- `ingest-history` for the four secondary hubs. Record the ESI request cost
  against the 150 req/min self-cap and the token budget **before** starting;
  if five regions of history cannot fit inside the self-caps in one night,
  say so and stage it — never approach the cap.
- Re-run `backtest` on the post-R1 book. **Before** re-running, add a
  winsorized gross-return column beside the raw one (same k and window as the
  ATR winsor, §17 D-22) so the "directional" sentence can be supported or
  withdrawn. Do **not** edit the frozen §13.6 rule; the verdict reads the same
  net expectancy it always did.
- Emit every figure through `provenance.MeasurementReport` and commit the
  artefacts under `data/reports/`.
- Correct `CLAUDE.md`'s headline: label +2.80%/108,441 as a historical
  snapshot, cite the dated artefact for the current figure.
- Work through every §21 and §22 owed live gate that this run makes
  answerable, and tick it in `CURRENT_CHECKPOINT.md` with the evidence.

Gate: reports committed, live-gate checklist updated, 828+ tests green.

### P2 — A maker paper ledger

Goal 2 is unmeasurable without this. Design it under §19.4's rules (no
decision without reasons; refusals recorded).

- A paper **maker** position is a *resting order*: side, price, quantity,
  venue (`exec_location_id`), posted-at sweep.
- A fill is **inferred from subsequent sweeps**, never assumed: a posted bid
  is filled when a later complete sweep shows the executable best ask at that
  venue at or below the bid price (someone traded through), and symmetrically
  for an ask. Partial fills are not inferred — a resting order is filled or it
  is not, and UNKNOWN between sweeps.
- Record **time-to-fill in sweeps**, and **undercut events**: any sweep where
  a new order rests inside yours at the same venue. These are the two costs
  §20.2 explicitly could not model; this is where the data to model them
  starts accumulating.
- Relist is out of scope until §0 check #5 is verified in-client.
- Add a `MAKER` surface to the desk that shows resting paper orders, their
  age in sweeps, and their undercut count. Paper-post from SPREADS.
- **Do not** compute an "expected edge" from this ledger until it holds at
  least `MIN_SAMPLES_FOR_A_READ` closed maker round trips. Until then the
  page says so.

Gate: post → observe → fill-or-cancel round trip proven on fixture sweeps;
golden fixture for fill inference before any ranking reads it.

### P3 — §20.4 REGIONS (resumes the queued phase)

Surface `crossregion.py` as a DESK tab. The caveat §20.4 already names must be
on the page, not in a footnote: **those are simultaneous-snapshot numbers for a
haul that takes days.** Add the one thing the CLI lacks: for each pair, the
**destination book's depth at the notional**, so a 13% edge on a book that
cannot absorb the cargo is UNKNOWN rather than 13%. Region-key everything
(§21 R8).

### P4 — "Big mover anatomy": a study, not a feature

The operator wants to know what technical or material traits precede big
moves. This is a hypothesis to **freeze and test**, not a screen to build —
built as a screen it is p-hacking with a UI.

- Write the hypothesis into `plan.md` **before** any code: the mover cohort
  definition (e.g. top-decile 30-day robust return, ≥3 endpoint observations,
  OK tier), the candidate antecedents (participation, `order_count` trend,
  market-group ancestor, THIN status, spread state, destruction_z, recent
  anchor), the comparison cohort, the lag, and the **pass rule**. Declare the
  multiple-comparison policy (§14.4 style).
- Measure with `provenance.MeasurementReport`. Use the rotation permutation
  from §22 S4 for any p-value; report effect sizes regardless.
- The output is a **report**, labelled exploratory unless it was pre-
  registered, and a list of antecedents that survived. Nothing in the
  recommendation engine reads it until a separate, later phase promotes it
  under §11's governance. EVE supply is elastic (§6): expect the honest result
  to be that most big moves are prints, patches, or wars, not patterns.

### P5 — Catalysts, and an LLM that is allowed to do exactly one thing

The operator wants news, patch notes and rumours correlated to price. The
repository already has the right skeleton and it is empty: `patchnotes.py`
parses the RSS feed into **anchor candidates**, `anchors` holds 0 rows, and
AVWAP is *anchored* VWAP — the whole formula is built to measure price
relative to an event. So the honest version of this feature is an **event
study**, and the LLM's job is narrow:

- **What the LLM may do:** read a patch note / dev blog / announcement and
  emit a structured anchor candidate — date, affected market groups or type
  ids, a direction-agnostic category (balance change, new item, removal,
  event), and a one-line quote of the source text. That is a *classifier*
  producing *data*. It runs in the daemon, never in the GUI, through `httpx`
  only, and every candidate is written to `config/anchors.jsonl` as a
  **candidate** for the operator to confirm (§11 D4 governance — operator
  promotes).
- **What the LLM may not do:** rank, score, recommend, predict, or write
  anything that a detector reads. No "sentiment". No rumours from unnamed
  sources — the source URL is a required field and an unparseable source is
  a refused candidate, recorded.
- **What gets measured:** for confirmed anchors, the AVWAP band position and
  forward return at 1/5/20 days, by category, as a `MeasurementReport`.
  Frozen pass rule first. That is the "correlation" the operator asked for,
  done in a way that can be wrong in public.
- Provider: the Anthropic API via `httpx` (already a runtime dep; do **not**
  add the SDK). Key in `config.toml`, never committed. Calls are budgeted and
  logged like ESI calls. Load the `claude-api` skill before writing any of it.

Gate: zero LLM output reaches a signal path — enforce with a test that walks
the import graph from `signals/` and `screen.py` and fails on any import of
the catalyst module.

### P6 — Desk performance, adopting what TradingBotV3 proved

TradingBotV3 commit `d0aebd5` ("Keep GUI health and audits off the event
loop") shipped two things this desk lacks:

1. **Idle-aware GC of Qt wrappers** (`install_gui_thread_gc` + an activity
   monitor that measures `idle_ms` via an event filter): wrapper collection
   stays on the GUI thread for Qt-ownership correctness but is deferred while
   the user is interacting, so a GC pause never lands on a click. Port the
   pattern, not the code (`VENDORED.md` rules; no import from V3).
2. **Every periodic audit off the event loop.** This desk already has the
   compute/paint split (§19.2); audit that nothing periodic slipped back onto
   the GUI thread.

Then the three things the review measured here:

- `load_desk()` reads ~4M Parquet rows **synchronously** and is most of the
  8.6 s open (§17 D-24 says so). Load on a worker and paint a "loading"
  state; every page already tolerates absent data as UNKNOWN.
- `performers.top_performers`, `scanner`, `brief` each run a **per-type
  Python loop** calling `bar_freshness()` 2,947 times. Fine today; at five
  hubs it is ~15k iterations per refresh. Vectorize freshness and the
  calendar-window medians over the whole frame.
- `iterrows()` in `paint()` on SPREADS and TOP. Build rows from arrays.

Measure before and after with the same `MeasurementReport`. Do not claim a
speed-up you did not time.

## Hard rules for every phase

- No new runtime dependencies (`httpx`, `pandas`, `pyarrow`, `numpy`; `pyside6`
  GUI extra). The Anthropic API is reached through `httpx`.
- Never fetch before `Expires`. Stay under the self-caps with margin.
- No order automation, no client automation, no character-acting SSO.
- No `open` column. Frozen AVWAP. Frozen verdict rules.
- Nothing under `gui/` imports a network client, directly or transitively —
  the LLM integration lives in the daemon.
- Detector, scoring or study changes get golden fixtures **first**.
- Missing, stale, partial or pre-R1 data is UNKNOWN and UNKNOWN fails.
- Reproduce before you fix; measure before you claim; label a historical
  figure rather than replace it.
- Gate every phase: `uv run pytest -q`, `uv run ruff check .`,
  `uv run ruff format --check .`, `selftest`. Update the four control files.
  Commit small and green, push, **stop**.
