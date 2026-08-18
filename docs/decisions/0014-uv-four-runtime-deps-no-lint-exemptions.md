# 0014 — uv with a committed lockfile, four runtime dependencies, no lint exemptions

Date: 2026-08-18

## Context
TradingBotV3 uses layered requirements files (`-core` ⊂ `-gui` ⊂ `-dev`) pinned
by a shared `constraints.txt`, and a ruff configuration with a deliberately
narrow rule selection. It also carries two `legacy.py` monoliths of 31,137 and
11,670 lines.

## Decision
- **uv**, with `pyproject.toml` and a committed `uv.lock`. No requirements-file
  layering — there is no GUI tier to layer.
- **Runtime dependencies are exactly four**: `httpx[http2]`, `pandas`,
  `pyarrow`, `numpy`. Nothing else in v1. Configuration is stdlib `tomllib` into
  frozen dataclasses — no pydantic, no ORM, no settings framework.
- **Dev dependencies are two**: `pytest`, `ruff` (lint *and* format).
- **No per-file lint exemptions, ever.**
- Python ≥ 3.12; all internal timestamps tz-aware UTC.

## Rationale
The dependency floor is set by what the analytics actually need and nothing else.
Every added dependency is a maintenance surface for a single operator whose
attention belongs to a production system (`plan.md` §9 R6), and a config library
in particular buys nothing over `tomllib` plus a dataclass when the config is
forty keys read once at startup.

The lint rule is about a specific failure this repo has watched happen. A
per-file exemption is how a monolith starts: the file gets too awkward to fix,
gets exempted, and the exemption then removes the pressure that would have kept
it small. With a ≤15k-LOC budget and 39,229 lines of cautionary tale in the
source repo, the exemption mechanism is closed rather than rationed.

uv over pip-tools is a straightforward speed and reproducibility choice; the
lockfile is committed so the always-on mini-PC and any future machine resolve
identically.
