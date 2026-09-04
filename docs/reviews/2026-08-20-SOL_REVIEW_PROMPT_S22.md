# Sol review prompt — EveTradingbot §22 remediation

Paste everything below the line into Sol with the repository checked out at
`4f726ad` or later.

---

You are reviewing the **§22 remediation track** of EveTradingbot. You wrote the
findings it answers (S1–S8). This is not a fresh audit of the repository and it
is not a re-run of your own review.

**The question is narrow and adversarial:** for each finding you raised, is the
fix *real*, is the claim made about it *true*, and did fixing it break or
quietly redefine something else?

The diff to review is `dc43300..4f726ad` — nine commits, one per phase plus a
self-correction:

```
77e8603  S1  Expires fails closed on every production path
3df5f85  S2  executable identity covers depth, and pricing uses the validator
940f6a3  S4  H2 is UNKNOWN, and every renderer says so
63022e1  S5a friction is a ratio of the gross move
09b9323  S3  a generation, not a widget tuple
1c1383f  S5b/c/d three statistical and ranking corrections
9a7c829  S6/S7 broker overrides reach production; a refusal is a record
d9d6865  S8  a wider import guard, and numbers that can be re-derived
4f726ad  two defects found while writing this prompt
```

## 1. Read these first, in this order

1. **`CLAUDE.md`** — invariants, locked stack, mandatory workflow.
2. **`plan.md`** — especially **§22** (this track, with each finding's
   disposition), **§21** (the track you reviewed), **§17** (deviation record,
   append-only), **§14.3 / §14.4** (frozen rule, and the amendment beside it),
   **§13.6**, **§20.3**.
3. **`CHANGELOG.md`** — newest first.
4. **`CURRENT_CHECKPOINT.md`** — per-phase owed live gates and the consolidated
   live-validation checklist.
5. **`FABLE_REVIEW_BRIEF.md`** and **`SOL_REVIEW_PROMPT.md`** — the two earlier
   briefs, for context on what was already claimed.

Then the source and tests.

## 2. Every finding was reproduced before anything was changed

Each phase records a before/after. Re-derive them; do not take them on trust.

| id | reproduced defect | claimed fix |
|---|---|---|
| S1 | 304 at 12:01 restored a **12:00** expiry; **2** transport requests | 1 request; boundary derived per feed |
| S2a | ask fill **9.258402** beside an executable ask of 100; bid fill **1,000** beside a bid of 90 | both walk the executable book |
| S2b | `book_quote` returned **9.2584, stale=False** on a pre-R1 frame | `None, stale=True`, with a reason |
| S4 | pooled run rendered as "the lead-lag claim was tested and not supported" | `H2 UNKNOWN — confirmatory run absent` |
| S5a | friction **100.0%** where 66.667% is correct | ratio of the gross move |
| S3 | a data-only refresh scheduled no follow-up | generation carries token/key/data/input |
| S5b | `effective_samples` **3** where at most 2 holds | a real non-overlapping subset |
| S5c | `-1R × 0.01` outranked `-0.1R × 1.0` | losses are held, not shrunk |
| S5d | `week_pct +99.98%`, state OK, raw 0% | UNKNOWN; three endpoint observations |
| S6 | `from_config` overrides always `{}` | loaded from `[costs]`, reaching `maker_spreads()` |
| S7 | validation raised before `_refuse()` | both paths route through the ledger |
| S8 | guard was two exact names; TOP figures unversioned | wider guard; `provenance.py` |

**S7 was narrowed, not confirmed as written.** `_refuse` is not in `reasons.py`;
the raises there are vocabulary-parse errors. The real boundary is
`paper.PaperLedger`, and that is where the fix went. Check the narrowing was
right and that no *other* ledger boundary has the same hole.

## 3. Attack these decisions first — they are judgment calls, not mechanics

Each of these declined or reinterpreted part of your brief. They are the most
likely places this track is wrong.

**S5c — a threshold was added and then withdrawn.** Your brief said "prefer
treating sufficiently stale evidence as increasingly UNKNOWN **or** using a
conservative freshness-aware bound/penalty". A 0.5 staleness floor was
implemented, found to mark everything older than ~8 days UNKNOWN (because
`freshness_factor` is bounded to **[0.4, 1.0]** by construction), and removed.
What shipped is only the directional fix: a positive expected R still decays,
a negative one is **held at its measured value**. Is "held" defensible, or does
a stale severe loss deserve to decay *toward* zero in magnitude while still
ranking below a fresh mild one? Is there a bound that would have been better
than either?

**S8 — `socket`, `ssl` and `http.client` are deliberately allowed.** You asked
for "network-capable `urllib` modules". Including the low-level three made the
guard fail on every GUI module, because Qt and the stdlib load them regardless.
The list is now the clients our code would have to *choose*. Is that hole real?
Could a GUI module open a socket without importing anything on the list?

**S4 — a rotation permutation was implemented rather than withdrawing
p-values.** Each type's series is rotated by a random offset. That preserves
within-type autocorrelation exactly and destroys the destruction/return
alignment. **But it also destroys same-day cross-sectional alignment across
types**, which may over-destroy and make the test *conservative in an
unquantified direction*. Is rotation the right null for this hypothesis? Should
the naive p-value still be what the frozen §14.3 rule reads?

**S5b — the non-overlapping subset is greedy and first-come.** It is
deterministic and its windows provably do not overlap, but it is one of many
valid subsets and it is not the largest. Does the choice bias which instances
survive? Cross-type dependence is explicitly **not** modelled and is stated as
such — is stating it enough?

**S1 — the "unknown expiry" boundary for orders is the operator's cold sweep
interval (60 min).** That is longer than ESI's 300 s orders cache, so it cannot
cause an early fetch — but a single malformed header now suppresses that URL
for an hour, including inside the 15:00–17:00 hot window. Is the operational
cost acceptable, and is "the next time we were going to ask anyway" genuinely
non-arbitrary for every feed, including the unmapped fallback?

**S2a — three field meanings changed.** `p5_price`, `depth_fill_price_*`,
`depth_fill_qty_*`, `order_count` and `top_order_volume_share` now describe the
**executable** book; the region-wide readings moved to `region_*`. Trace
**every** consumer. One was missed on the first pass and caught late
(`census.spoof_flagged_share`, fixed in `4f726ad`) — assume there are more.
`brief.py`, `paper.py` and `crossregion.py` still read `station_volume_share`:
is that right where they use it?

## 4. Two defects were found while writing this prompt — check the pattern

`4f726ad` fixes both, and both are the class you are hunting:

* the S1 docstring claimed `expiry_unknown` was "recorded so telemetry can show
  how often that happens". **It was not written anywhere.** The row is marked
  now and the wording matches the code.
* S2a's redefinition of `top_order_volume_share` silently changed what
  `census.spoof_flagged_share` measures — a statistic §17 already records
  region-wide. It reads `region_top_order_volume_share` now.

**Assume this pattern recurs.** Grep every docstring and plan sentence written
during §21 and §22 for a claim about behaviour, and check the code does it.

## 5. Invariants — verify structurally

- Never fetch before `Expires`; ETags, budgets, breaker, error-limit intact.
- **No `open` column, ever.** The AVWAP σ formula is frozen.
- Completed bars only; missing/stale/partial/pre-R1 data is UNKNOWN, and
  **UNKNOWN always fails**.
- Nothing under `gui/` reaches the network, directly or transitively.
- The core runs headless; exactly one movable `ChartPanel`.
- The learning loop never edits, promotes or mutates a setup.
- A failed publish never destroys the last verified output; watchlist entries
  are never auto-removed.
- **No frozen verdict rule was edited.** §13.6's verdict is still NOT PLAUSIBLE
  at every cell after S5a; §14.1–14.3 are untouched with the amendment at
  §14.4. Confirm both.
- **No historical figure was replaced.** `max_drawdown_pct` lives in
  `golden_signals.json` under `backtest_withdrawn_pre_r3`; §17 D-31's "derived"
  wording is corrected in place with the original visible; §20.3's original
  scope text is preserved above its amendments. Confirm none was overwritten.

## 6. Deliberately absent — do not report as gaps

- **Nothing is `LIVE_VALIDATED`.** Every §21 phase, every §22 phase and §20.3
  owe live gates; none has been run. The build cannot self-certify.
- **The confirmatory H2 run does not exist.** It was not created or claimed.
- **Cross-type dependence in the backtest is unmodelled**, and said so.
- **`relist_cost_unverified` is intentionally unconsumed**; §0 check #5 open.
- **`[costs].broker_fee_overrides` is empty** until the operator transcribes
  real in-client fees — so every hub is currently priced at the base rate.
- **§20.4 REGIONS and §20.5 ALERTS+ntfy are queued**, not missing.
- Setups are out of scope for the §20 track by operator decision.

## 7. Output

Findings only, ordered by severity. For each: the violated contract or false
claim; exact file and line; concrete inputs → wrong output; whether the new
tests would catch it and why not if not; the smallest contract-preserving fix.

Separate **correctness bugs**, **unsound analysis**, **invariant risk**,
**unnecessary complexity**, **missing tests**.

Say plainly when a remediation is correct. An empty category is a valid result —
this project prefers an honest zero to a filled panel, and a manufactured
finding costs more than a missing one.
