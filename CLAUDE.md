# EveTradingbot — AI context index

EveTradingbot is a decision-support market screener for EVE Online's in-game
market, built on CCP's public ESI API and delivered as a daily Discord digest.
It ports the analytical core (anchored-VWAP bands, relative strength, levels,
expected-R) of a private US-equity system; it never places, automates, or
assists in placing orders, and it never automates the EVE client.

## How to talk to the operator

**Short.** One idea per sentence. Say what you did, what is broken, and what
they need to do — nothing else. If a message runs past about ten short lines,
cut it. Detail belongs in the docs and the commit message, not in the chat.
This rule is for chat output only; docs, code comments and commit messages
keep their normal depth.

## Mandatory workflow for every AI session

**Read narrow, not everything.** An agent that cannot read its brief skims it
and then appends to it, which is what grows these files past the point of
being followable. The bounded read below is the instruction — widen it only
when the narrow read leaves a real question open.

Before proposing or changing anything, in this order:

1. `CURRENT_CHECKPOINT.md` — the **"Active state at a glance"** block at the
   top: branch, active item, last measured baseline, open gates, restart owed.
   That block is the brief. Read the dated entries below it only for the item
   you are touching; if a dated entry contradicts the block, the dated entry
   wins and the block is stale.
2. `plan.md` — §4 (bar contract), §10 (non-goals), **§11 (locked decisions —
   never re-litigated; changing one is a plan-level edit with a stated reason,
   made only when the operator agrees)**, §17 (measured facts and the deviation
   record), then the body of the section your active item lives in. The frozen
   verdict rules are §12.4, §13.6 and §14.3. Read nothing else unless the item
   sends you there — the file is 2,961 lines and is not context to load.
3. `CHANGELOG.md` — **search** `Current implemented inventory` for the feature
   you are about to touch, so you never rebuild landed work. Search it; do not
   read it end to end. `Recent changes` holds the last build days; older
   entries are archived under `docs/` and are never loaded as context.
4. `docs/README.md` — open only the runbook, reference or decision record the
   item needs. Historical documents are evidence, not authority.
5. Inspect the source, tests and git state to verify the docs still match
   reality. **When the docs and the code disagree, the code is the fact and the
   doc is the defect** — fix the doc and say so. **This file is one of those
   docs.** A line here that the code contradicts is not authority; correct it,
   or leave a dated note saying which line is wrong and what the code does
   instead, and tell the operator. Never fix it silently.
   *(INTERNALS: "The control file itself goes stale")*

`WISHLIST.md` contains ideas, not authorized work. Never implement from it; an
item enters the build only when the operator moves it into `plan.md`.

Before editing, state the plan/checkpoint item, what already exists (from the
inventory search), what remains, the governing sections, the files, the tests,
and whether the ask-first rule applies.

**One phase at a time.** Implement exactly the active phase's scope from
`plan.md`, stop at its validation gate, hand the operator the gate checklist,
and update `CURRENT_CHECKPOINT.md`. Never start the next phase in the same
session; never reach into a later phase because it is adjacent. Green tests
earn `IMPLEMENTED + GREEN`; only the operator's observation in the game or
against the real lake earns `LIVE_VALIDATED`, and only the operator promotes.

After every repository change, before handoff:

- refresh the **"Active state at a glance"** block with numbers measured on
  this branch (test count, **process** exit code, lint, selftest, commit) — a
  stale block is worse than none, because it is the one thing the next agent
  trusts;
- add a short dated entry to `CURRENT_CHECKPOINT.md` and record any live gate
  the change owes in the consolidated checklist;
- add completed behaviour/contract changes to `CHANGELOG.md`: an inventory line
  and a `Recent changes` entry;
- advance or narrow `plan.md`, keeping any owed live gate; a stated behaviour
  that changed gets a §17 row with the old wording left visible;
- add a `docs/INTERNALS.md` entry for any new rule, with the incident behind it;
- update `docs/README.md` whenever a Markdown file is added, moved or
  reclassified;
- keep `CLAUDE.md` and `AGENTS.md` byte-identical (see the last line of this
  file);
- **keep the active files small.** `CURRENT_CHECKPOINT.md` ≤ 1,500 lines and
  `CHANGELOG.md`'s `Recent changes` ≤ 800: move older entries into a dated
  archive under `docs/` and leave a pointer. Archiving is maintenance, not a
  new document.

Do not create another roadmap, progress ledger, handoff, review-prompt or
status file at the root. The root control set is `CLAUDE.md`/`AGENTS.md`,
`plan.md`, `CHANGELOG.md`, `CURRENT_CHECKPOINT.md`, `WISHLIST.md` and
`docs/README.md` (widened from four files on 2026-09-04, §17 D-36). Review
prompts and briefs go under `docs/reviews/`, dated.

## Hard invariants (plan.md §4, §10, §11 — never violate)

- **Decision-support only.** No order automation, no client automation, no SSO
  scopes that act on a character. Read-only public ESI, nothing else.
- **Never fetch before expiry.** The `Expires` header is a correctness
  invariant; circumvention is a bannable offence. ETags on every request;
  descriptive User-Agent from config; self-caps: 6,000 tokens/window on
  orders, 150 req/min on history. **No agent runs an ESI-fetching subcommand
  as a check** (`sweep-books`, `ingest-history`, `census`, `sde`, `killmails`,
  `daemon`). *(INTERNALS: "Expires fails closed")*
- **No `open` column exists and none is ever synthesized.** The bar contract
  is `["datetime","high","low","close","volume","order_count"]` with
  `close ← ESI average`. Anything that wants an open is dead code here.
  *(INTERNALS: "No open column exists")*
- **The AVWAP σ formula is frozen** (running-AVWAP, volume-weighted deviation,
  `tp = close`) from Phase 2 forward. Changing it requires regenerating every
  golden fixture and operator sign-off, together.
- **Completed bars only; missing or stale data is uncertainty, never
  confirmation.** Gates are tri-state and UNKNOWN always fails. Stale inputs
  render as UNKNOWN/stale, never as silently-priced rows.
  *(INTERNALS: "Completed bars only")*
- **Honest zero beats a filled panel.** "Nothing clears costs today" is a
  valid, expected digest. *(INTERNALS: "Honest zero beats a filled panel")*
- **No detector/scoring change without golden fixtures first** (from Phase 2).
  *(INTERNALS: "Golden fixtures first")*
- **A failed publish never destroys the last verified output.** Atomic writes;
  operator-entered watchlist names are never auto-removed.
- **No momentum/breakout-continuation logic *in the system's own recommendation
  engine*.** EVE supply is elastic; spikes arbitrage flat. Do not re-introduce
  it by habit (plan.md §6). **Narrowed 2026-08-20 (§17 D-15): operator-defined
  setups in `config/setups.jsonl` may express anything, including trend and
  continuation.** The machinery's job is to measure them honestly, not to
  argue with them.
- **Membership is decided on median UNIT volume; weighting on ISK turnover**
  (§11 D3, amended). THIN names (100–999 units/day) are carried, charted,
  scanned and badged everywhere — and excluded from FORGE.
  *(INTERNALS: "Membership is decided on unit volume")*
- **No decision is recorded without its reasons.** An opening needs a thesis, a
  setup tag and a like tag; a pass needs a dislike tag. No tags, no record —
  and the refusal itself goes in the ledger (§19.4).
  *(INTERNALS: "A refusal is a record")*
- **The learning loop never edits a setup, changes a frozen formula, or
  promotes anything.** It correlates and reports; the operator promotes.
- **Nothing under `src/evescreener/gui/` may import an ESI client.** The desk
  refreshes on a timer; it shows staleness rather than curing it (§19.2).
  *(INTERNALS: "The desk has no ESI client")*
- **No coupling to TradingBotV3.** No imports, no submodules; vendored files
  under `src/evescreener/vendored/` may diverge and are tracked in
  `VENDORED.md`.

## Core rules learned by breaking something

Each rule is binding as written. The incident and the measurements behind it
are in [`docs/INTERNALS.md`](docs/INTERNALS.md) — **read the matching entry
before changing the behaviour a rule governs.** A rule with no entry is a draft.

- **The GUI thread never computes; it paints.** `heavy` pages compute on a
  worker from an immutable generation captured on the GUI thread; the AST
  guard fails any widget read inside `compute()`. Last good result stays on
  screen on failure. *(INTERNALS: "The GUI thread never computes")*
- **A number in prose is not a measurement.** Every figure quoted in a doc or
  a handoff carries its as-of date, membership, denominator and command;
  `provenance.py` is how. A retraction is a new §17 row, never an edit.
  *(INTERNALS: "A number in prose is not a measurement")*
- **Review by reproduction, not by reading.** A reviewer runs the branch
  against a **copy** of the real lake, reverts the fix to prove the new tests
  fail, and re-derives every number the builder quoted.
  *(INTERNALS: "Review by reproduction")*
- **Fail-before-fix is proven, not claimed.** Restore the pre-change file, run
  the new test, watch it fail, restore; say so in the commit message. A test
  that calls the primitive is not a test of the path.
  *(INTERNALS: "Fail-before-fix is proven")*
- **A probe that runs the system writes to it.** `haul scan`, `sde`, the desk
  — everything writes under the data dir. Point `EVESCREENER_DATA_DIR` at a
  copy and say which. *(INTERNALS: "A probe that runs the system writes to it")*
- **Assume another session is in this repository.** Verify the branch
  immediately before staging and immediately before pushing; stage explicitly
  by path, never `git add -A`; **never `git stash`** — restore one file with
  `git checkout <base> -- <path>` instead; after committing, confirm your work
  landed. *(INTERNALS: "Another session is in this repository")*
- **The suite is not a baseline** unless it ends in `7 deselected` (the
  `network` tests) and the environment carries the `gui` extra so the
  offscreen desk tests ran. Probe before quoting a number.
  *(INTERNALS: "A suite run under a known condition is not a baseline")*
- **A heuristic never under-earns its own best part.** A basket, packing or
  composition shown beside a single plan is floored at that plan under the
  same caps, and says so when the floor bound.
  *(INTERNALS: "A heuristic never under-earns its own best part")*
- **A snapshot is not a tape, and the size of that caveat is measured, not
  asserted.** A ranked hauling row carries its survival across the stored
  prior generations, UNKNOWN until enough exist.
  *(INTERNALS: "A snapshot is measured against the snapshots before it")*

## Tech stack (locked, plan.md §11 D1–D2)

Python ≥3.12, uv-managed (`pyproject.toml` + `uv.lock`). Runtime deps:
`httpx[http2]`, `pandas`, `pyarrow`, `numpy` — nothing else in v1. **Optional
`gui` extra: `pyside6`** — the desk (plan.md §19.2). The core must run
headless: `daemon`, `digest` and every CLI subcommand import without Qt, and
`tests/test_headless.py` enforces it by walking the import graph. Dev:
`pytest`, `ruff` (lint + format, no per-file exemptions ever), `pytest-qt`
(GUI tests run offscreen). Config: `config.toml` (gitignored) mirrored by a
committed `config.example.toml`, plus three committed operator-editable data
files — `config/sectors.jsonl`, `config/setups.jsonl`, `config/reasons.jsonl`;
`EVESCREENER_DATA_DIR` is the only env override. Storage: Parquet lake +
SQLite `state.db` + JSONL streams under `./data/`. Entry:
`python -m evescreener <selftest|sde|census|ingest-history|sweep-books|anchors|
screen|digest|backtest|killmails|cross-region|paper|watch|brief|board|scan|
setups|reasons|learning|haul|gui|report|daemon>`, or `launch_gui.py` for a Windows
shortcut. All timestamps tz-aware UTC.

## Commands (gate before every commit)

- `uv run pytest -q` — must be fully green; offline by default, live calls
  only under `@pytest.mark.network`. GUI tests run offscreen and are part of
  the default gate. The baseline shape is `N passed, 7 deselected`; the current
  count lives in `CURRENT_CHECKPOINT.md`. **Check the process exit code, not a
  piped tail's.**
- `uv run ruff check . && uv run ruff format --check .` — clean. Fix the code,
  not the config; a suppression needs its reason beside it.
- `uv run python -m evescreener selftest` — 12/12.
- Self-check of the control set: `python ../JumpStarter/tools/jumpstart.py
  check .` from a checkout of JumpStarter beside this repo (it is not
  vendored). It verifies `CLAUDE.md == AGENTS.md`, the size bounds and that no
  template token is left unfilled. Known red line: `plan.md` is over its
  1,200-line bound; splitting it is the operator's decision (see
  `docs/README.md`).
- Commit small and green; push after each commit.

## Working agreement for agents

- **The agent team.** Claude Code loads `.claude/agents/`; Codex loads
  `.codex/agents/`. Both expose `tester`, `builder`, `reviewer` and `recon`,
  use the same packets under `.claude/packets/`, and follow the contract in
  [`docs/AGENT_TEAM.md`](docs/AGENT_TEAM.md). Read it before spawning one.
  Builders and reviewers work in their own worktrees (`uv sync --extra dev
  --extra gui` once each) and never touch the main checkout; the lead merges
  in a scratch worktree.
- `main` is the trunk; branch per packet as `claude/<slug>`, merge back after
  the packet's gates pass. Recon before a packet; tests first for anything
  touching a frozen surface, a verdict rule, the ESI client or an
  operator-facing surface; review by reproduction before merge.
- **File-scoped ask-first rule.** Any edit to a frozen surface or an operator
  data file is asked about BEFORE it is made — even a change that only adds.
  The list, derived from the §11 locks and **not yet confirmed by the
  operator**, is in `docs/AGENT_TEAM.md`; the packet must quote the operator's
  decision for the exact functions, or the builder stops.
- Live stores (`./data/`, `config.toml`) are read-only to every agent except a
  builder whose packet names the write. `state.db` holds the paper ledger and
  the watchlist and is not regenerable.
- Never switch the main checkout's branch while the desk or the daemon runs
  from it, and never restart either without the operator's word. Say in one
  line when a restart is owed.

## Measured facts that override the plan's estimates

`plan.md` §17 carries a measured-facts table from the 2026-08-20 build. Read it
before trusting any estimate in §0–§11. The ones that changed a decision:

- Of **19,152** Forge-active types, **14,013 have daily bars**, **4,978 return
  an *empty* history array** (a book with no trades in 13.5 months) and **241
  genuinely 404** (1.3%). §3.2's "4xx should not occur" is withdrawn; gaps are
  recorded in `history_missing` and a 404 never trips a breaker. *(An earlier
  draft of this file claimed 16,789 404s. That was a circuit-breaker cascade
  mistaken for data — see §17 D-10.)*
- **Structure exposure is on the EXIT, not the entry.** Across all five hubs,
  0.0% of visible ask volume is in player structures and 8.8–98.3% of bid
  volume is. §9 R3 assumed the opposite direction.
- **CCP does not filter outlier prints.** Without TR winsorization, 20.5% of
  tracked types would carry a risk unit more than twice too large.
- The **median spread** across two-sided Forge types is **98.8%**, and only
  **2 of 315** tracked types have friction low enough for the measured gross
  edge to survive costs.
- A year of killmails is **15.7M rows / 1.3 GB** — §7's "trivial next to the
  market lake" was wrong by ~30×.

## Where to read more

- `CURRENT_CHECKPOINT.md` — **read the `Active state at a glance` block**, then
  the consolidated live-validation checklist, which is the list of open gates.
- `CHANGELOG.md` — **`Current implemented inventory` is the contract: search
  it before building.** Older entries: `docs/CHANGELOG_ARCHIVE_2026-08.md`.
- `docs/INTERNALS.md` — the incident behind every rule above.
- `plan.md` §17 — the measured facts, the deviation record (D-1…D-36), and the
  status of the six named checks (#3 and #4 are ANSWERED).
- `plan.md` §12.4, §13.6, §14.3 — the frozen verdict rules. They were written
  before the studies ran and are **never** retrofitted; a change is a
  plan-level edit with the old rule left visible.
- `plan.md` §0 — verified ESI facts and the named checks. §11 — every locked
  default (cadences, tiers, floors, watchlist, Discord contract, anchors,
  governance).
- `docs/README.md` — classifies every Markdown file as control, runbook,
  reference, decision record or historical evidence.
- `docs/decisions/0001-owner-goals-and-priorities.md` — the operator's goals
  in their own words, the tie-breaker for every prioritisation call. **Status
  OPEN: the questionnaire has not been asked.** Do not reorder work on your own
  reading of what the operator wants.
- `docs/AGENT_TEAM.md`, `docs/CODEX_NOTES.md` — the agent team and Codex.
- `WISHLIST.md` — candidate ideas; never an implementation queue.
- `VENDORED.md` — provenance of code vendored from TradingBotV3.

`AGENTS.md` is a generated copy of this file — **edit CLAUDE.md, then
re-copy**: `python ../JumpStarter/tools/jumpstart.py sync-agents .` (or
`cp CLAUDE.md AGENTS.md`). Never hand-edit `AGENTS.md`.
