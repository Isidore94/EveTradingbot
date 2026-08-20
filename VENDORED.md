# Vendored code — provenance manifest

Files copied from `Isidore94/TradingBotV3` into `src/evescreener/vendored/`.
This is a **copy relationship, not a dependency** (plan.md §1): no submodule,
no package, no shared state, no upstreaming obligation. Divergence is expected
and allowed; this file is the record that makes a future diff one command.

## Upstream snapshot

| Field | Value |
|---|---|
| Repository | `https://github.com/Isidore94/TradingBotV3` |
| Branch | `phase05-integration-blitz` |
| Commit | `d60cbaf91fa3505411c0382cf05aed34205c0af9` (2026-08-17) |
| Vendored on | 2026-08-20 |

**Branch note.** `plan.md` was written against `phase05-r8-weekend-prep`. That
branch no longer exists on the remote as of 2026-08-20; `phase05-integration-blitz`
carries the same tree (it is the branch `testing-week-2026-08-17` also points
at) and is the successor used here. The module layout differs from the plan's
citations in one way: the packages live under `scripts/` (`scripts/master_avwap_lib/`,
`scripts/bounce_bot_lib/`, `scripts/indicators/`), not at the repo root.

## Files

| Vendored path | Upstream path | Upstream sha256 (first 16) | Status |
|---|---|---|---|
| `vendored/expected_r.py` | `scripts/master_avwap_lib/expected_r.py` | `293e71c38133eaab` | **diverged** (lint-only) |
| `vendored/indicators/__init__.py` | `scripts/indicators/__init__.py` | `e37f7079c7653495` | **diverged** (lint-only) |
| `vendored/indicators/smi.py` | `scripts/indicators/smi.py` | `512380700ddb5709` | **diverged** (lint-only) |
| `vendored/indicators/efficiency_lrsi.py` | `scripts/indicators/efficiency_lrsi.py` | `bf781705c19cf18d` | **diverged** (lint-only) |
| `vendored/indicators/heikin_ashi.py` | `scripts/indicators/heikin_ashi.py` | `2f9cb250d6f50e04` | **diverged** (lint-only) |
| `vendored/indicators/laguerre_rsi.py` | `scripts/indicators/laguerre_rsi.py` | `1eed508272c9f02e` | **diverged** (lint-only) |

### What "diverged (lint-only)" means here

This repo's lint policy admits **no per-file exemptions** (plan.md §11 D1), so
excluding `vendored/` from ruff was not an option. Every vendored file
therefore carries mechanical, behaviour-preserving edits made by
`ruff check --fix --unsafe-fixes` plus `ruff format`:

- `typing.Sequence`/`Iterable` imports moved to `collections.abc`;
- `zip(...)` calls given an explicit `strict=` argument;
- `%`-formatting and `.format()` left alone; line lengths wrapped to 100;
- import ordering normalized (isort).

**No numerical behaviour was changed.** `zip(..., strict=False)` is the
pre-existing semantic in every case; nothing was tightened to `strict=True`.

Each file also carries a provenance header comment naming its upstream path,
branch, commit and vendoring date.

## Recovering a clean diff against upstream

```bash
git clone https://github.com/Isidore94/TradingBotV3 /tmp/tbv3
git -C /tmp/tbv3 checkout d60cbaf91fa3505411c0382cf05aed34205c0af9
diff -u /tmp/tbv3/scripts/master_avwap_lib/expected_r.py \
        src/evescreener/vendored/expected_r.py
```

The provenance headers are the only non-lint delta.

## What is *not* vendored, and why

`levels.py` (884 LOC) and `real_relative_strength` / `_wilder_atr_last` are
**ports**, not copies: they live in `src/evescreener/signals/levels.py`,
`signals/rrs.py` and `signals/atr.py` because the bar contract changed (no
`open`) and the EVE versions add behaviour the upstream has no referent for
(ISK round-number levels, TR winsorization, cohort scopes that return UNKNOWN
instead of falling back to a benchmark). Each of those modules names its
upstream source, branch and commit in its docstring.

The AVWAP band formula in `signals/avwap.py` is a **reimplementation** of
`calc_anchored_vwap_bands` with two documented changes (`tp = close`;
vectorized). `tests/generate_golden.py` carries the upstream row loop verbatim
and asserts the vectorized result matches it to 1e-9, so the port is proven
rather than asserted.

## Unimported by design

`indicators/heikin_ashi.py` and `indicators/laguerre_rsi.py` validate OHLC and
need an `open`. There is no `open` in this system and none is ever synthesized
(plan.md §4), so both stay vendored and unimported — exactly as they are
upstream, where `indicators/` has no importer either.
