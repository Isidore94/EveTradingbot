# EveTradingbot — AI context index

EveTradingbot is a decision-support market screener for EVE Online's in-game
market, built on CCP's public ESI API and delivered as a daily Discord digest.
It ports the analytical core (anchored-VWAP bands, relative strength, levels,
expected-R) of a private US-equity system; it never places, automates, or
assists in placing orders, and it never automates the EVE client.

## Mandatory workflow for every AI session

Read, in this order, before proposing or changing anything:

1. `plan.md` — the authoritative contract: architecture (§1–§7), phase order
   and gates (§8), risks (§9), non-goals (§10), and **locked implementation
   decisions (§11)**. Do not re-litigate §11; changing a locked decision is a
   plan-level edit with a stated reason, made only when the operator agrees.
2. `CHANGELOG.md` — what already exists. Never rebuild landed work.
3. `CURRENT_CHECKPOINT.md` — the single active phase/item, working state, and
   last verification stamp. Resume it; do not pick new work while it is open.

**One phase at a time.** Implement exactly the active phase's scope from
`plan.md` §8, stop at its validation gate, hand the operator the gate
checklist, and update `CURRENT_CHECKPOINT.md`. Never start the next phase in
the same session; never reach into a later phase because it is adjacent.

After every repository change, before handoff: update `CURRENT_CHECKPOINT.md`
(active item, state, verification result), add completed behavior/contract
changes to `CHANGELOG.md`, and advance/narrow `plan.md` while keeping any owed
live gate. These four files are the whole control set — do not create extra
roadmap, status, or handoff files.

## Hard invariants (plan.md §4, §10, §11 — never violate)

- **Decision-support only.** No order automation, no client automation, no SSO
  scopes that act on a character. Read-only public ESI, nothing else.
- **Never fetch before expiry.** The `Expires` header is a correctness
  invariant; circumvention is a bannable offence. ETags on every request;
  descriptive User-Agent from config; self-caps: 6,000 tokens/window on
  orders, 150 req/min on history.
- **No `open` column exists and none is ever synthesized.** The bar contract
  is `["datetime","high","low","close","volume","order_count"]` with
  `close ← ESI average`. Anything that wants an open is dead code here.
- **The AVWAP σ formula is frozen** (running-AVWAP, volume-weighted deviation,
  `tp = close`) from Phase 2 forward. Changing it requires regenerating every
  golden fixture and operator sign-off, together.
- **Completed bars only; missing or stale data is uncertainty, never
  confirmation.** Gates are tri-state and UNKNOWN always fails. Stale inputs
  render as UNKNOWN/stale, never as silently-priced rows.
- **Honest zero beats a filled panel.** "Nothing clears costs today" is a
  valid, expected digest.
- **No detector/scoring change without golden fixtures first** (from Phase 2).
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
- **No decision is recorded without its reasons.** An opening needs a thesis, a
  setup tag and a like tag; a pass needs a dislike tag. No tags, no record —
  and the refusal itself goes in the ledger (§19.4).
- **The learning loop never edits a setup, changes a frozen formula, or
  promotes anything.** It correlates and reports; the operator promotes.
- **Nothing under `src/evescreener/gui/` may import an ESI client.** The desk
  refreshes on a timer; it shows staleness rather than curing it (§19.2).
- **No coupling to TradingBotV3.** No imports, no submodules; vendored files
  under `src/evescreener/vendored/` may diverge and are tracked in
  `VENDORED.md`.

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
setups|reasons|learning|gui|report|daemon>`, or `launch_gui.py` for a Windows
shortcut. All timestamps tz-aware UTC.

## Commands (gate before every commit)

- `uv run pytest -q` — must be fully green; offline by default, live calls
  only under `@pytest.mark.network`. GUI tests run offscreen and are part of
  the default gate.
- `uv run ruff check . && uv run ruff format --check .` — clean.
- Commit small and green; push after each commit.

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

- `plan.md` §17 — the measured facts, the deviation record, and the status of
  the six named checks (#3 and #4 are now ANSWERED).
- `plan.md` §12.4, §13.6, §14.3 — the frozen verdict rules. They were written
  before the studies ran and are **never** retrofitted; a change is a
  plan-level edit with the old rule left visible.
- `plan.md` §0 — verified ESI facts and the named checks.
- `plan.md` §11 — every locked default (cadences, tiers, floors, watchlist,
  Discord contract, anchor calendar, governance).
- `VENDORED.md` — provenance of code vendored from TradingBotV3 (once created).
