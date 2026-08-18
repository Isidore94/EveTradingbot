# 0002 — A standalone repository with vendored copies, not a monorepo or a fork

Date: 2026-08-18

## Context
Roughly 2,600 LOC of TradingBotV3 is directly reusable here: the pure indicator
modules, `expected_r.py`, `levels.py`, the RRS formula. The options were a
monorepo, a long-lived branch of TradingBotV3, a git submodule, a published
shared package, or a new repo with copies.

## Decision
A new standalone repository (`plan.md` §1). The small shared surface is
**vendored** — copied in under `src/evescreener/vendored/`, each file stamped
with its source branch, commit SHA, and date, and listed in a root `VENDORED.md`
manifest. No imports from TradingBotV3, no submodule, no shared state, no
upstreaming obligation. Vendored files may diverge freely.

## Rationale
The binding constraint is not code reuse, it is **the operator's attention**
(`plan.md` §9 R6). TradingBotV3 is a production system this project must not
siphon maintenance from. Every coupling mechanism creates a path by which a
change here forces a change there:

- a monorepo or branch shares CI, lint config, and merge conflicts;
- a submodule pins a SHA someone must bump, and a bump can break the production
  side at the worst moment;
- a published package creates a versioning and release obligation for a
  single-operator project.

Vendoring makes cross-contamination structurally impossible at the cost of
manual re-sync, which is the right trade when the shared surface is ~2,600 lines
that are already stable and that this repo intends to *modify* anyway (the bar
contract differs — ADR 0005).

Abandoning this project at any phase gate must leave zero debt on the production
side. Only vendoring guarantees that.
