# EveTradingbot

A decision-support market screener for EVE Online's in-game market, built on
CCP's public ESI API and delivered as a daily Discord digest. It is a port of
the analytical core of a private US-equity swing-trading system (TradingBotV3):
daily-bar anchored-VWAP setups, relative strength, level/pivot statistics and
expected-R ranking — re-grounded in EVE's market microstructure.

**Nothing here places, automates, or assists in placing an order, and nothing
automates the EVE client.** Read-only public ESI and a Discord webhook. See
`plan.md` §10.

## The question it exists to answer

Not "does a screener exist" but: **is EVE market swing trading a money-making
activity, for this operator, at his size?** Every claim the system makes traces
to a measurement, and every number it cannot measure renders as `UNKNOWN`
rather than a guess. A negative answer is a possible — and acceptable — output.

`python -m evescreener report` writes the document that answers it.

## Quick start

```bash
uv sync --extra dev
cp config.example.toml config.toml     # then edit: contact, webhook, skills
uv run python -m evescreener selftest

uv run python -m evescreener sde       # static data: types, market groups, systems
uv run python -m evescreener census    # the opportunity map (~2h once, then diff-append)
uv run python -m evescreener anchors   # pull patch dates as anchor CANDIDATES
uv run python -m evescreener sweep-books
uv run python -m evescreener ingest-history   # daily bars for the tracked universe
uv run python -m evescreener digest --dry-run
```

Then the studies and the experiment:

```bash
uv run python -m evescreener backtest              # plan.md §13
uv run python -m evescreener killmails --backfill 365 --study   # §14
uv run python -m evescreener cross-region          # §15
uv run python -m evescreener paper open --type-id 34 --thesis "..." \
    --setup "Dip into value, strength intact" --like clean_dip_below_value
uv run python -m evescreener paper report          # §12.4 verdict tracker
uv run python -m evescreener report                # §16 viability report
```

And the daily desk workflow (plan.md §18 — the TradingBotV3 surfaces, in text):

```bash
uv run python -m evescreener watch add --name "Ishtar" --note "doctrine hull"
uv run python -m evescreener watch list
uv run python -m evescreener brief --name "Ishtar"     # the chart, in text
uv run python -m evescreener board --sort value        # the D1 strength board
```

The board and the brief are observation, not opportunity: they show friction
beside every row and never hide a type for failing to clear costs. Watchlist
names render in every digest, and only `watch remove` — you — removes one.

Your own setups, the scanner that runs them, and what they have been worth
(plan.md §19):

```bash
uv run python -m evescreener setups                    # validated on load
uv run python -m evescreener scan                      # every enabled setup
uv run python -m evescreener backtest --setup "Cloud reclaim"
uv run python -m evescreener paper pass --name "Ishtar" --dislike spread_too_wide
uv run python -m evescreener learning                  # what earns, what bleeds
```

Setups live in `config/setups.jsonl` and are data, not code: a typed condition
vocabulary validated loudly on load, long-only, all of it from daily
high/low/close/volume. A setup stays **UNVALIDATED** until it has a backtest
read or 20 tagged closed trades — that label is information, not a lock.

Reasons live in `config/reasons.jsonl` and are required in both directions. An
opening needs a thesis, a setup tag and a "why I like it" tag; a pass needs a
"why I don't like it" tag. No tags, no record. Passes are then measured
forward on the backtest's cost terms, so `learning` can tell you which of your
*reasons* are predictive — in both directions.

And the desk itself (plan.md §19.2):

```bash
uv sync --extra gui                    # Qt is optional; the core never needs it
uv run python -m evescreener gui       # or double-click launch_gui.py
```

Eight pages — MARKET, CHARTS, BOARD, FOCUS, SCANNER, PAPER, LEARNING, HEALTH —
over the local lake only. The refresh timer re-reads what is on disk and
cannot cause a fetch before `Expires`; the desk *shows* staleness rather than
curing it. Paper Buy is on every surface a name appears, through one prefilled
form that calls the same ledger the CLI does, with the same refusals. Price
is drawn as range candles — the body is the day's measured low–high, the notch
is the average, the colour is the move against the previous average. There is
no `open` in the data, and yesterday's close sits outside today's range on 56%
of bars, so a conventional body is not available honestly or even legibly.

```bash
uv run python -m evescreener daemon    # every cadence in one process
```

`daemon` owns the locked cadences (history at 11:20 UTC, digest at 16:00, the
Forge book every cache window inside 15:00–17:00 and hourly otherwise); each
other subcommand runs the same job once, for manual and backfill use.

Before the anchor calendar is confirmed, `anchors --list` shows what is pending
— the signal layer ignores unconfirmed candidates and falls back to a synthetic
90-day anchor grid until you flip the ones you consider live.

## The rules that shape the code

- **Never fetch before `Expires`.** CCP treats polling before expiry as cache
  circumvention and bans for it. This is a correctness invariant, not courtesy.
- **No `open` column exists and none is ever synthesized.** ESI publishes daily
  aggregates; `close ← average`. A synthetic open would launder uncertainty
  into confirmation.
- **The AVWAP σ formula is frozen** and proven against the upstream row loop to
  1e-9 by `tests/generate_golden.py`.
- **Gates are tri-state and UNKNOWN always fails.** "Could not measure" is
  never "measured and passed".
- **Honest zero beats a filled panel.** "Nothing clears costs today" is a
  valid, expected digest — published with the counts that explain it, so an
  outage never reads as an absence of opportunity.
- **Every study's hypothesis and pass rule was frozen in `plan.md` before the
  study ran** (§12.4, §13.6, §14.3), so a disappointing result cannot be argued
  away afterwards.
- **No momentum or breakout-continuation logic in the system's own
  recommendation engine** — though your own setups may express it, and the
  machinery will measure them honestly. EVE supply is player-produced
  and elastic; spikes get arbitraged flat by industrialists. The tradeable read
  is dips below anchored value with intact demand.

## Documents

- **[`plan.md`](plan.md)** — the contract. Architecture, data-layer spec, bar
  contract, cost model, signal translation, the phased build order, the risk
  register, the non-goals, the locked decisions (§11), and the frozen study
  hypotheses (§12–§14).
- **[`CHANGELOG.md`](CHANGELOG.md)** — what exists.
- **[`CURRENT_CHECKPOINT.md`](CURRENT_CHECKPOINT.md)** — the one active item
  and the consolidated live-validation checklist.
- **[`VENDORED.md`](VENDORED.md)** — provenance of code copied from
  TradingBotV3.
- **[`CLAUDE.md`](CLAUDE.md)** — agent operating rules.

## Testing

```bash
uv run pytest -q              # offline, the default gate
uv run pytest -m network -q   # the live smoke path, run deliberately
uv run ruff check . && uv run ruff format --check .
```
