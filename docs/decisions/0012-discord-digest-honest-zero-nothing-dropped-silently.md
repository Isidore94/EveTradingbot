# 0012 — Discord webhook delivery; honest zero; nothing is dropped silently

Date: 2026-08-18

## Context
The source system pushes to ntfy from a 155-line stdlib-only transport with a
`unconfigured / delivered / rejected / ambiguous` result contract. The operator
wants Discord, and explicitly does not want a desktop GUI — the 42,417-LOC
PySide6 lesson is learned once (`plan.md` §10.6).

## Decision
- **Webhook, not a bot**, one channel, in v1 (`plan.md` §11 D6).
- The result contract carries over unchanged, plus a Discord-specific
  `rate_limited` kind for 429 + `retry_after`. The opener is injectable so every
  branch is testable without a network.
- **Honest zero.** "Nothing clears costs today" is a valid, expected digest, and
  the digest says so in those words rather than dredging up the least-bad
  negative row.
- **Nothing is truncated silently.** Content splits into numbered ≤2,000-char
  messages with code fences closed and reopened across splits; a line that still
  cannot fit is replaced by a visible marker and counted.
- No `@here`/`@everyone` in v1 — nothing in a D1 screener is urgent.
- Every digest is archived to `streams/digests.jsonl` **before** delivery is
  attempted.

## Rationale
Honest zero is the load-bearing part and it is the same decision as ADR 0007 and
ADR 0005 applied to presentation. A daily digest creates pressure to be
non-empty; a screener that yields to that pressure teaches the operator that its
top row means something when it does not. Given ADR 0008's netting, an empty
candidate list is the *expected* daily output for a 50-name roster, and Phase 0
confirmed it on the first live run.

An `ambiguous` send is never reported as delivered, for the same reason UNKNOWN
never passes a gate: the transport genuinely does not know, and pretending
otherwise is the one failure mode that silently loses a day's output.

Archiving before delivery means a failed publish never destroys the last
verified output — the source repo's invariant, kept.

The digest also carries a **measurement table** below the candidate section,
explicitly labelled "measurements, not recommendations". That is Phase 0 gate
machinery: the operator needs prices and volumes in front of him to spot-check
five types against the in-game market window. It is not a workaround for honest
zero, and it must not become one.
