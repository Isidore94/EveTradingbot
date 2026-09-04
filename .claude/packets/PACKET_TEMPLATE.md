# Packet {{ID}} — {{TITLE}}

Authorized by the operator on {{DATE}} ("{{VERBATIM_QUOTE}}"). Base: `main` at
`{{BASE_SHA}}`. Branch: `claude/{{SLUG}}`. Governing: {{GOVERNING_DOCS}} (name the
`plan.md` sections and any `docs/decisions/` record). Line numbers below were read by the
lead on {{DATE}} against `{{BASE_SHA}}`; verify each before editing — **if the code
disagrees with this packet, the code is the fact**: report the difference, do not force
the change.

The ask-first rule **does / does not** apply: {{WHY}}. (The list is in
`docs/AGENT_TEAM.md`.)

{{STANDING_PROHIBITION_FOR_THIS_RUN}} — e.g. never restart the desk or the daemon; no
subcommand that fetches from ESI; `./data/` is read-only and probes use a copy under
`EVESCREENER_DATA_DIR`.

## What the lead measured

The evidence this packet is built on, with the instrument and the window, not a summary
of it. A claim with no measurement beside it is a draft.

- {{WHAT_WAS_MEASURED}} — {{HOW}}, {{WHEN}}: {{THE_NUMBERS}}.
- {{WHAT_EXISTS_TODAY}} — `{{path}}:{{line}}`.
- {{WHAT_DOES_NOT_EXIST}} — not found; searched {{WHERE}} and `CHANGELOG.md`'s inventory.

## Items

### 1. {{ITEM_TITLE}} (`{{path}}`)

Today `{{symbol}}` at `{{path}}:{{line}}` {{WHAT_IT_DOES_NOW}}. Change it to
{{WHAT_IT_MUST_DO}}, because {{WHY_IN_ONE_CLAUSE}}. {{WHAT_NOT_TO_TOUCH}} is out of
scope — a wider change is a separate packet.

Binding invariants: {{WHICH_INVARIANTS}} (from `CLAUDE.md` "Hard invariants").

Tests (new `tests/test_{{slug}}_*.py`): (a) {{ASSERTION}}; (b) {{ASSERTION}}; (c)
{{ASSERTION}}. **(b) is the fail-before-fix proof** — on the un-fixed code it fails with
{{THE_EXPECTED_FAILURE}}, because {{WHY_THE_OLD_CODE_CANNOT_PASS_IT}}. The existing
{{WHICH}} tests must stay green untouched. Golden fixtures, if a detector or score moves:
{{WHICH_FIXTURES_AND_THE_COMMIT_THEY_ARE_PINNED_FROM}}.

### 2. {{ITEM_TITLE}}

...

## Parts (delete if this packet is one branch)

- **PART A** — on `claude/{{SLUG}}` off `main`: items 1–{{N}}.
- **PART B** — on `{{OTHER_BRANCH}}`: merge `main` (with Part A) in first, then items
  {{N}}–{{M}}.
- **PART C** — integrate and prove: merge order, the full gate list, the checkpoint
  refresh, and the one line the operator is told at the end.

## Docs to reconcile (same branch)

- `CURRENT_CHECKPOINT.md` — a dated entry carrying the measurements above (short); the
  gate below in the open-gates list; refresh the "Active state at a glance" block.
- `CHANGELOG.md` — inventory line(s) for {{WHAT_LANDED}}; one `Recent changes` entry.
- `plan.md` — advance or narrow {{ITEM}}; keep any gate still owed; a §17 deviation row
  if a stated behaviour changed, with the old wording left visible.
- `docs/INTERNALS.md` — the entry behind any new rule, with the numbers.
- `docs/README.md` — only if a Markdown file was added or reclassified.
- `CLAUDE.md` + `AGENTS.md` (byte-identical) — **exactly this rule line, no more**:
  > {{THE_RULE_TEXT_TO_ADD_VERBATIM}}

## Gates before handoff

{{PRECONDITION_TO_PROBE}}; `uv run pytest -q` exit 0 with exactly 7 deselected;
`uv run ruff check . && uv run ruff format --check .` clean;
`uv run python -m evescreener selftest` 12/12. Report the **process** exit codes, not a
piped tail's. Say whether the desk or the daemon must be restarted to see the change;
the restart is the operator's call.

## The real-world gate

**{{WHAT_MUST_BE_OBSERVED_IN_THE_GAME_OR_THE_REAL_LAKE}}** — not a test. Who observes
it, on what run, and what exactly they must see. It goes in the checkpoint's
live-validation checklist and stays open until someone has seen it. `IMPLEMENTED + GREEN`
is the most this packet can claim; `LIVE_VALIDATED` is earned only here.

## Still owed after {{ID}}, as its own packet {{NEXT_ID}}

{{WHAT_WAS_CONSIDERED_AND_DELIBERATELY_LEFT_OUT}} — named here so it is queued, not
rediscovered later as a gap.
