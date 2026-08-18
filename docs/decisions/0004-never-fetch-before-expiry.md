# 0004 — Never fetch before expiry; ETags always; self-caps at 50% of every limit

Date: 2026-08-18

## Context
ESI publishes an `Expires` header on market routes and, since 2026-02-24, a
token budget on the orders endpoint (`market-order` group, 12,000 tokens per
15-minute floating window; 2 per 2xx, 1 per 304, 5 per 4xx, 0 per 5xx). The
history endpoint sits outside that regime with a separate CCP-stated limit of
300 requests/minute/IP. A legacy error limit (100 non-2xx/3xx per minute → HTTP
420) still applies to all routes.

## Decision
Four rules, enforced in `src/evescreener/esi.py` rather than left to callers
(`plan.md` §3.1–§3.3):

1. **A request whose cached `Expires` has not passed is skipped, not queued.**
   If no cached body exists for a still-fresh URL, the client *waits out the
   window* rather than asking early.
2. **`If-None-Match` on every request**, with response bodies cached on disk so
   a 304 resolves to real data for one token instead of two.
3. **Self-caps at half of every published limit**: orders hard-stop at 6,000
   tokens/window, history paced at 150 req/min.
4. **A descriptive User-Agent** with the operator's contact, from config, never
   a library default; a pinned `X-Compatibility-Date` (ADR 0015).

Every request writes a `sweep_ledger` row recording the observed `X-Ratelimit-*`
headers and whether the previously-stored expiry had actually passed.

## Rationale
CCP treats polling before expiry as cache circumvention and has stated that it
is a bannable offence; the history endpoint's limit carries developer-app
termination language. This is therefore a **correctness invariant, not a
courtesy** — the one class of bug in this repo that can end the account rather
than produce a wrong number. Code that can only be correct if every caller
remembers a rule will eventually be incorrect, so the rule lives in the client.

The 50% self-cap exists because the *planned* worst case is ~21% of budget
(`plan.md` §3.2) — a bug, not the design, is what would spend to the cap, and a
bug does not respect a comment. Phase 0 measured one full Forge sweep at
exactly the planned 826 tokens.

The ledger is not logging. It is the evidence the Phase 0 gate reads, and it is
what detects a token-regime change on the day it happens rather than on the day
of a 429 storm (`plan.md` §9 R4).
