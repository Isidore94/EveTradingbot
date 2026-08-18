# `docs/` — reference material

This directory is **subordinate to the control set**. Where anything here and
`plan.md` §11 disagree, §11 wins and the file here is the thing that is stale
(decision 0013).

- `plan.md` owns the roadmap, the architecture contracts, and the locked
  decisions.
- `CHANGELOG.md` owns what exists and why it was changed.
- `CURRENT_CHECKPOINT.md` owns the single active item and its verification
  stamp.
- `CLAUDE.md` owns the agent operating rules and the mandatory reading order.

Nothing in `docs/` is a roadmap, a status board, or a handoff note. If a file
here starts to become one, it belongs in the control set instead — or nowhere.

## Reading order

For a human coming to the repo cold: `README.md` → this file →
`DATA_CONTRACTS.md` → `decisions/` as needed.

For an agent: the order in `CLAUDE.md`, unchanged. These documents are
background, not a substitute for `plan.md`.

## Contents

| File | What it is |
|---|---|
| `DATA_CONTRACTS.md` | Every schema the system writes: the bar contract, `book_summary`, the screen frame, the SQLite tables, and the on-disk layout. The reference no single source file holds. |
| `ESI_CLIENT_RUNBOOK.md` | Operating the ESI client: cadences, budgets, what the telemetry ledger records, how to read it, and what to do when a limit trips. |
| `FIRST_SESSION_CHECKLIST.md` | Clone to first digest, with the artifact that proves each step. |
| `decisions/` | Numbered decision records (ADRs). Why each locked choice was made, in the source repo's format. |

## Decision records

`plan.md` §11 is the binding table of locked decisions; it is deliberately terse
because it is a contract. `decisions/` expands the load-bearing ones into
Context / Decision / Rationale so the *reasoning* survives when the people who
had it do not.

| # | Decision |
|---|---|
| [0001](decisions/0001-decision-support-only-no-order-or-client-automation.md) | Decision-support only: no order automation, no client automation |
| [0002](decisions/0002-standalone-repo-with-vendored-copies.md) | A standalone repository with vendored copies |
| [0003](decisions/0003-public-read-only-esi-as-the-only-market-source.md) | Public, unauthenticated ESI is the only market source in v1 |
| [0004](decisions/0004-never-fetch-before-expiry.md) | Never fetch before expiry; ETags always; self-caps at 50% |
| [0005](decisions/0005-new-bar-contract-no-open.md) | A new bar contract: no `open`, `close ← ESI average` |
| [0006](decisions/0006-avwap-sigma-formula-frozen-tp-is-close.md) | The AVWAP σ formula is frozen; typical price is `close` |
| [0007](decisions/0007-completed-bars-only-tri-state-gates.md) | Completed bars only; UNKNOWN always fails |
| [0008](decisions/0008-costs-netted-inside-the-screen-at-notional-tiers.md) | Costs netted inside the screen, at notional tiers |
| [0009](decisions/0009-no-momentum-or-breakout-continuation-logic.md) | No momentum or breakout-continuation logic |
| [0010](decisions/0010-rank-on-depth-walk-and-p5-never-on-best-price.md) | Rank on the depth walk, never on best price |
| [0011](decisions/0011-golden-fixtures-before-detector-changes.md) | Golden fixtures regenerated before a detector change |
| [0012](decisions/0012-discord-digest-honest-zero-nothing-dropped-silently.md) | Discord webhook; honest zero; nothing dropped silently |
| [0013](decisions/0013-four-file-control-set-plan-md-authority.md) | A four-file control set; `plan.md` §11 is authority |
| [0014](decisions/0014-uv-four-runtime-deps-no-lint-exemptions.md) | uv, four runtime deps, no lint exemptions |
| [0015](decisions/0015-compatibility-date-pinned-to-a-fully-past-day.md) | `X-Compatibility-Date` pinned to a fully-past day |

Records 0001–0014 are backfilled from `plan.md` as written on 2026-08-18; 0015
records a correction forced by live evidence during Phase 0. Adding a record
does not change a decision — amending `plan.md` §11 does, and the record then
follows.
