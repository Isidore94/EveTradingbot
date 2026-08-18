# 0013 — A four-file control set; `plan.md` §11 is the binding authority

Date: 2026-08-18

## Context
TradingBotV3 accumulated parallel status documents — `plan.md`, a GUI plan, a
GUI learning plan, two progress stamps, and an `AGENTS.md` that is a byte-for-byte
copy of `CLAUDE.md`. Its own decision 0013 exists to referee the resulting
authority conflicts, and a 2026-07 commit had to reconcile drifted status claims
against repository evidence.

## Decision
Four files, and no more (`plan.md` §11 D8):

| File | Owns |
|---|---|
| `CLAUDE.md` | agent operating rules and the mandatory reading order |
| `plan.md` | roadmap, architecture contracts, phase gates, **locked decisions §11** |
| `CHANGELOG.md` | what exists, with rationale |
| `CURRENT_CHECKPOINT.md` | the single active item and its verification stamp |

Status vocabulary is `IMPLEMENTED → GREEN → LIVE_VALIDATED → PROMOTED`, and only
the operator promotes. One phase is active at a time.

**No `AGENTS.md` copy — one file, one truth.**

`docs/` (added 2026-08-18) holds reference material and these decision records.
It is explicitly **subordinate**: where a record here and `plan.md` §11 disagree,
§11 wins and the record is the thing that is stale. Records expand *why*; they
never redefine *what*.

## Rationale
The failure mode is documented upstream: duplication drifts, and drifted status
is worse than no status because it is trusted. Status therefore lives in exactly
one place per program.

The `AGENTS.md` exclusion is the sharpest case. Upstream it is a literal copy of
`CLAUDE.md` maintained by hand, which means it is one forgotten edit away from
being wrong, and an agent reading the wrong one gets stale invariants. If a
non-Claude agent needs an entry point here, the answer is a pointer file, not a
copy — and that is a plan-level decision to make deliberately, not a convenience.

`docs/` does not violate this. Decision records are append-only history of *why*;
they are not roadmap, status, or handoff. The subordination clause above is what
keeps it that way, and it is stated in `docs/README.md` too so nobody has to find
this file to learn it.
