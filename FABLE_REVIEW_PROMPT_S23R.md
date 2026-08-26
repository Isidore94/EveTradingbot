# Fable review prompt — EveTradingbot §23 remediation

Paste everything below the line into a fresh Fable session with the repository
checked out at the head of `claude/hauling-h1-h4-build-4pwsso`.

---

You are reviewing the **§23 remediation** of EveTradingbot. **You wrote the
findings it answers** — the twelve defects your first-build audit reproduced on
2026-08-25. This is not a fresh audit of the hauling track and it is not a
re-run of your own review.

**The question is narrow and adversarial:** for each finding you raised, is the
fix *real*, is the claim made about it *true*, and did fixing it break or
quietly redefine something else?

The diff is `6987da9..HEAD` — twelve commits, one per fix, plus the doc pass:

```
d06ac16  FIX 1   a losing trade is not a plan
109deea  FIX 2   an unknown packaged volume is UNKNOWN, not a free pass
eda272c  FIX 3   one book, spent once
40caa99  FIX 4   bars outside the window are not evidence about the window
188da92  FIX 5   a NULL timing column must not delete a row
0edd59c  FIX 6   along_route with nowhere to go is refused, not re-interpreted
ce70d4c  FIX 7   name the constraint that actually severed the pair
35690e3  FIX 8   a forecast cannot be evidence about itself
77e2f50  FIX 9   quarantine the reliability grade, and prove the quarantine
009b7d5  FIX 10  the scan was spending its time indexing, not ranking
b7f2307  FIX 11  three residues
186f38d  FIX 12  refresh the stamps, and record the remediation
```

17 files, +1,483 / −107. Gate at head: `uv run pytest -q` → **1,068 passed, 7
deselected**, ruff check + format clean, `selftest` **12/12**.

## 1. Read these first, in this order

1. **`CLAUDE.md`** — invariants, locked stack, mandatory workflow.
2. **`plan.md`** — **§23** (the contract, now carrying five in-place
   corrections), **§17 D-33/D-34/D-35** (the deviation record; D-35 is this
   remediation), and §21 R1, §22 S2, §22 S4, §22 S6, §22 S7 — the earlier
   findings this track keeps colliding with.
3. **`CHANGELOG.md`** — the remediation entry is newest.
4. **`CURRENT_CHECKPOINT.md`** — the owed live-validation checklist, which is
   **unchanged**.
5. **`FABLE_REVIEW_PROMPT_S23.md`** — the brief you were given for the audit,
   for what was claimed before you found otherwise.

## 2. Every fix was reproduced before anything was changed

Each shipped with a test written first and watched to fail on the audited head.
Re-derive them; do not take them on trust.

| fix | reproduced defect | claimed fix |
|---|---|---|
| 1 | ask 100@100 vs bid 100@50 → **plan at −51.7%, zero rejections** | first chunk's marginal is its net; search **breaks** at the first failing chunk |
| 2 | 1,000,000 units vs a 60,000 m³ hold → plan, no rejection | `VOLUME_UNKNOWN`; unrankable plans counted in `dropped_unrankable` |
| 3 | one 1,000-unit ask sold to two hubs → **2,000 units packed** | one plan per `(type, source)` and `(type, dest)`, withheld count named |
| 4 | 15 bars from a year ago → `known=True`, 500 units/day, reason empty | the `tail(window_days)` fallback is deleted |
| 5 | NULL `handling_minutes` → 0.0 → plan deleted from the ranking | NULL inherits the dataclass default; residue counted |
| 6 | `along_route` with no destination charged the whole trip | profile refuses; the page degrades to dedicated **with a note** |
| 7 | a pair severed by the avoid list reported `ROUTE_BLOCKED_SECURITY` | two separate probes, both cached |
| 8 | proceeds-only close → "realized" net from `expected_cost_isk`, resolved | resolved needs both sides actual; `assumed_net_isk` labelled |
| 9 | the grade's invented weights could gate | AST quarantine + a self-test of the detector |
| 10 | 14.3 s / 80,000 rejections per pair, uncapped report, no debounce | see §4 — **the hot spot was somewhere else** |
| 11 | missing `bound` → one-level curves; far order → `unresolvable`; cargo box overrode the ship | `ValueError`; `range_out_of_reach`; "use ship profile" |

## 3. The one place this remediation went beyond what you asked for

**FIX 10 is not the fix you specified, and that is the first thing to check.**

You asked for four things: the `break` (a), a capped rejected list (b), a
control debounce (c), and a `q_walk` shortcut (d). All four landed. But the
measurement behind your estimate did not reproduce: profiling one pair over
5,000 types × 20 levels gave **18.8 s, of which `curves_from_depth` was 18.4 s
and the ranking loop 0.4 s** — pandas' per-group constant paid five thousand
times, not the walks and not the rejections.

So **`curves_from_depth` was rewritten**: sort once, pull each column to a list
once, walk the rows detecting key changes. 18.8 s → 1.4 s. That is a rewrite of
the index **every plan in the system is built from**, done under a performance
heading, and it is the highest-risk change in the diff. A field-by-field test
was written first (`test_curves_from_depth_rebuilds_exactly_what_the_reduction
_wrote`) — decide whether it is sufficient. Specifically:

* NaN `structure_share` must stay **UNKNOWN**, not become 0.0.
* `depth_complete` is AND-ed across the group; `generation` is taken from the
  **first row** of each group. Can those disagree within a group, and if so
  which is right?
* Ordering, dtype coercion (`int()`/`float()` on pandas scalars), and empty or
  single-row groups.
* Does the old `groupby` path and the new one produce **identical** curves on a
  frame with mixed sides, stations and truncation? A property-style comparison
  against the previous implementation is the check I did not write.

## 4. Attack these decisions — they are judgment calls, not mechanics

**FIX 1's `break` rests on a monotonicity argument.** Per-unit marginal net is
claimed non-increasing in quantity because the ask WAP only rises, the bid WAP
only falls, and tax scales proceeds by a positive constant. Chunk *sizes*
vary — candidate quantities are the union of both curves' breakpoints — so the
argument runs on per-unit marginals and infers the aggregate. Is that sound at
every step, including across a zero-quantity level, a `min_volume`-excluded
level, and the first chunk (where the "previous" point is a synthetic zero)?
A counterexample here means the ranker stops early on a real book.

**FIX 1 still appends the refused breakpoint to `priced`.** It therefore
appears in the report's `why_this_size` table and the drawer. Is showing the
size that failed useful, or does it read as a size that was considered viable?

**FIX 2 fires `VOLUME_UNKNOWN` only when a hold is declared.** With
`usable_cargo_m3 = 0` (no ship, no `--cargo`) an unknown-volume type still
ranks — on the grounds that there is no cap to bypass. Is that the right
reading, or should an unmeasurable volume be UNKNOWN regardless of whether
anything would have caught it?

**FIX 3 withholds rather than shares.** At most one plan per `(type, source)`
and per `(type, destination)`, best by the run's objective. Two questions: does
the tie-break (objective, falling back to `net_profit`) make the choice
deterministic in every case; and is withholding the right conservatism, given
the shared-consumption ledger is recorded in §23.10 as the known refinement?

**FIX 4 leaves `window_days = 30` as a function default with no config key.**
The quantiles, the minimum bar count and both liquidation priors are all config;
the window that decides which bars are even eligible is not. Is that a hidden
constant of the kind §22 S4 removed elsewhere?

**FIX 7 gives the avoid list precedence in the explanation.** When both the
avoid list *and* the security profile would sever a pair, the operator is told
about the avoid list. Is that the more actionable answer, and does the extra
probe per blocked pair (cached, but still a search) cost anything at scale?

**FIX 8 changed a test rather than deleting it.** The build session's
`test_a_close_records_what_really_happened_and_the_forecast_error` asserted the
defect; it now asserts the contract, with a note saying which half was wrong.
Check the amendment is honest and that no *other* consumer reads
`actual_cost_isk` expecting the old borrowing behaviour.

**FIX 9's detector skips `liquidity.py` entirely.** That is where the grade is
computed and returned — so a gate written *inside* that module would be
invisible to the quarantine. Is the exemption too wide? And can you defeat the
detector with a gate it does not model (a dict lookup, a `getattr`, a
comparison built at runtime)?

**FIX 11b puts a `.knows` attribute on a callable.** `bounded_jump_distance`
returns a closure carrying the graph's membership test, and
`reachable_from_station` reads it with `getattr`. It keeps the exclusion
identical and only changes the diagnostic — but it is duck-typing across a
module boundary. Is there a cleaner contract that does not widen the surface?

**FIX 10's `q_walk` shortcut uses `bisect_left` with an epsilon.** Levels can
share a `cumulative_qty` when one carries only `min_volume`-excluded volume
(`qty == 0`). Check the boundary behaviour there, and that the shortcut's
`levels_consumed` and `marginal_next_price` match the walk's in every case —
the fixture pins equality, but it was written by the same hand as the shortcut.

## 5. Two things the tests cannot have caught, and I want them checked

* **The `q_walk` shortcut is behaviour-preserving, so its test passed before
  the change.** It could not fail first. Treat it as unverified by the usual
  discipline and check it directly.
* **The `curves_from_depth` rewrite's test was written first but by the same
  session that wrote the rewrite.** It pins what I believed the old code did.
  If that belief was wrong anywhere, the test encodes the error.

## 6. Invariants — verify structurally

- `book_summary` byte-stability through the modified sweep path is still green
  (`tests/test_depth_lake.py`). The track is additive or it is nothing.
- Read-only public ESI; never fetch before `Expires`; no cadence change. The
  depth reduction still rides the same pages.
- Nothing under `gui/` reaches the network, directly or transitively —
  `haulfreight` → `crossregion` → `httpx` is the way in, and a test forbids the
  import. The debounce timer must not have changed the worker contract:
  `compute()` still reads only its arguments, never page state (§22 S3).
- Tri-state everywhere; UNKNOWN always fails and renders with its reason.
- No frozen formula or verdict rule moved. §23's corrections are **in place
  with the superseded wording visible** — confirm none was overwritten.
- A failed publish never destroys the last verified output; the report is still
  written atomically, and the cap changed what is *in* it, not how it lands.

## 7. Deliberately absent — do not report as gaps

- **Nothing is `LIVE_VALIDATED` and the owed checklist is unchanged.** A
  remediation earns no live evidence. One line was added to checklist B (scan
  wall-clock on the real lake).
- **The shared consumption ledger for baskets is not built**, and is recorded
  in §23.10 as the known refinement.
- **`window_days`, the reliability weights and the two liquidation priors are
  all still chosen numbers.** Report a *new* unmeasured threshold; the existing
  ones are labelled and known.
- **The track is 10,180 lines against a 7,000 target** (§17 D-34/D-35), stated
  rather than trimmed. Volume is not the review.
- **H5/H6 remain out of scope**, and H0 is still deferred to the shadow.

## 8. Output

Findings only, ordered by severity. For each: the violated contract or false
claim; exact file and line; concrete inputs → wrong output; whether the new
tests would catch it and why not if not; the smallest contract-preserving fix.

Separate **correctness bugs**, **unsound analysis**, **invariant risk**,
**unnecessary complexity**, **missing tests**. Call out any fix that is
**cosmetic** — where the reported symptom is gone but the mechanism that
produced it is not.

Say plainly when a remediation is correct. An empty category is a valid result —
this project prefers an honest zero to a filled panel, and a manufactured
finding costs more than a missing one.
