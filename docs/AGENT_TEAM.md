# The agent team: one lead, its builders, its reviewers

Document role: **active runbook.** How a session in this repo plans, builds, reviews and
integrates work using project-defined sub-agents instead of pasting prompts between
windows. Claude Code definitions live in `.claude/agents/`; Codex definitions live in
`.codex/agents/` (both tracked). This file is the contract they share with the lead
session and with the operator. It was installed on 2026-09-04 from JumpStarter
(`C:\Users\Aaron\JumpStarter`, not vendored); the generic loop and the exact prompts are
in that repo's `playbooks/build-review-loop.md`.

Before this file existed the repo ran the same loop by hand: seven dated review prompts
under `docs/reviews/` were pasted into Fable, Sol, Opus and Codex sessions, and three
adversarial passes over §23 found nineteen defects between them. The roles below make
that loop a definition instead of a paste.

## The roles

The two native role sets implement the same four roles, packet shape and handoff
interface. The harness chooses its own definition; both receive the same packet path
under `.claude/packets/`.

| Agent | Model | Where it runs | What it may do | What it must never do |
|---|---|---|---|---|
| **lead** (the session the operator talks to) | session model | the main checkout | ask the operator, write packets, spawn the others, merge, run the full suite, reconcile the ledgers | build a packet itself when a builder could; restart the desk or daemon without the operator's word; run an ESI-fetching subcommand as a "check" |
| **tester** (`tester.md` / `tester.toml`) | strong, high effort | its own worktree, on the packet's branch | write the packet's tests, prove each FAILS on the current code, commit them red | write the fix; weaken or skip a test |
| **builder** (`builder.md` / `builder.toml`) | strong, high effort | its own worktree, branch `claude/<slug>` | edit, test, commit, push its branch, reconcile docs on its branch | touch the main checkout, merge, delete branches, edit an ask-first file without a recorded yes |
| **reviewer** (`reviewer.md` / `reviewer.toml`) | strong, high effort | its own worktree, on the branch under review | run tests, revert-and-rerun to prove fail-before-fix, reproduce claims on COPIES of the lake | write, edit, commit, push, touch live stores, fetch from ESI |
| **recon** (`recon.md` / `recon.toml`) | cheap, medium effort | the main checkout, read-only | map code with `file:line`, count real rows, find gaps | write anything, propose designs unasked |

Built-in exploration agents remain available for one-off lookups; `recon` is the same
job with this repo's rules baked in.

## The loop

1. **Recon first.** Before the lead writes a packet it spawns `recon` on the premises
   ("does X exist, where, what does the real lake show"). A packet is written only from
   verified premises. *(JumpStarter principle 5: two claims in one packet were refuted at
   code level by the builder.)*
2. **The packet.** The lead writes it as a numbered list: the operator's decision quoted,
   the facts with `file:line`, the exact change per item, the tests that must fail first,
   the invariants that bind, the docs to reconcile, the gate.
   `.claude/packets/PACKET_TEMPLATE.md` is the shape. Packets are tracked in git.
3. **Tests first, for anything that matters.** For a packet with more than one item, or
   any item touching a frozen surface, a verdict rule, the ESI client, or an
   operator-facing surface (a page, the CLI output, the digest, the report), the lead
   spawns `tester` FIRST. It writes one test per item that drives the real path, proves
   each fails on the current code, and commits them red. The builder then makes them
   pass and may only ADD tests. *(§17 D-35a: three tests agreed on behaviour production
   never had, because they called the primitive rather than the path.)*
4. **Build.** The lead spawns `builder` with the packet path and the branch slug. One
   builder per packet. **Two packets that touch the same files run one after the other,
   not in parallel.** **The lead checks the handoff against the diff before believing
   it.** `git diff --stat <base>..<branch>` and the item list must agree: an item marked
   "done" with no file behind it, or a file changed that no item names, is a question
   for the builder before any reviewer is spawned.
5. **Review by reproduction.** The lead spawns `reviewer` with the branch, the packet and
   the builder's handoff. GO / NO-GO, blockers separated from advisories. Never skipped
   for a packet that touches a number, a gate or a surface the operator sees: the
   `issued` `NaN` crash (2026-08-26) passed three reading reviews and was found only by
   running a scan on the real lake.
6. **Fix round.** Blockers go back to a builder as a small fix packet on the same branch.
   Sending them to the SAME builder keeps its context; a fresh builder gets the
   reviewer's blockers verbatim. Advisories are batched into a later packet. A third fix
   round means the packet's premises were wrong: new recon, not a third fix.
7. **Integrate.** The lead merges in a **scratch worktree**, never in the main checkout
   while the desk or daemon runs from it, then runs the full suite, lint and `selftest`,
   and refreshes the "Active state at a glance" block. Merge order is packet order.
8. **The handover.** A merged commit reaches the operator only at the next restart of the
   desk or the daemon, and the restart is their call. The lead says in one line that it
   is owed and why. Then the **live gate** is owed — a checklist item in
   `CURRENT_CHECKPOINT.md`, observed in the game or against the real lake. Green tests
   earn `IMPLEMENTED + GREEN`; only the operator's observation earns `LIVE_VALIDATED`.

## Rules that exist because something broke

The general ones are in JumpStarter's `PRINCIPLES.md`; the ones specific to this code
are in [`INTERNALS.md`](INTERNALS.md). The ones every agent needs before its first
command:

- **One checkout, many agents.** The desk (`python -m evescreener gui`) and the daemon
  run from the main checkout. Testers, builders and reviewers work in worktrees under
  `.claude/worktrees/`, which `.gitignore` already covers. Nobody switches the main
  checkout's branch while either is running: a mid-merge working tree under a running
  application takes it down.
- **Assume another session is in the repository.** Verify the branch immediately before
  staging and immediately before pushing; stage explicitly by path, never `git add -A`;
  never `git stash`, which takes the other session's in-flight work with it; after
  committing, confirm your work landed.
- **The suite is NOT a baseline when** the count does not end in `7 deselected` (the
  network tests were not deselected, or the run was filtered), or when the worktree's
  environment lacks the `gui` extra and the offscreen desk tests were skipped or
  errored. Probe with `uv run pytest --co -q | tail -1` before quoting a number.
- **A probe that RUNS the system writes wherever the system is configured to write.**
  `haul scan`, `sde`, `sweep-books`, the desk — all write under the data dir. Point
  `EVESCREENER_DATA_DIR` at a copy and say which. And **never run an ESI-fetching
  subcommand as a check**: `Expires` is a bannable invariant and the orders budget is
  shared with the daemon.
- **Fail-before-fix is proven, not claimed.** The builder restores the pre-change file
  and watches the new test fail; the reviewer does it again independently.
- **Old rows have the key PRESENT and EMPTY, not absent.** Here that is a float `NaN`
  in a nullable Parquet column (truthy!) or a NULL in `state.db`. 556 of 314,793 Forge
  depth rows carried one and that aborted every scan.
- **A fixture generated by the code it is meant to pin is a self-portrait.** Pin from
  the old code and record the commit — the golden fixtures under `tests/fixtures/` are
  the frozen-formula contract (§11 D5).
- **Live stores are read-only to every agent** except a builder whose packet names the
  write — and that takes a backup first. `state.db` holds the paper ledger and the
  watchlist and is not regenerable.
- **Ask-first files** — derived on 2026-09-04 from the locks in `plan.md` §11 and the
  hard invariants; **the operator has not yet confirmed this list**:
  `src/evescreener/signals/avwap.py`, `signals/atr.py`, `signals/rrs.py`,
  `signals/setup.py`, `esi/client.py`, `esi/budget.py`, `bars.py`, the verdict-rule
  functions in `paper.py` (§12.4), `backtest.py` (§13.6) and `killmails.py` (§14.3),
  `tests/generate_golden.py` and everything under `tests/fixtures/`, the three operator
  data files under `config/`, `plan.md` §11/§12.4/§13.6/§14.3/§17, and `CLAUDE.md`.
  They need the operator's decision quoted in the packet for the exact functions.
  Otherwise the builder stops and the question goes in the handoff.
- **Chat is short.** Detail lives in commits, docs and handoffs. A handoff states what
  was NOT built as plainly as what was.

## Delegation policy for the lead

The lead's job is routing, not typing. The cheapest correct agent does each job.

- **Do it yourself:** reading; a lookup under a minute; `git status/log/diff`; committing
  and pushing work that already exists on a branch; merging in a scratch worktree;
  doc-only edits under about 40 lines; answering the operator.
- **Spawn `recon` (the cheap model):** any question needing more than three files read,
  or a count from the real lake or `state.db`. Never the expensive model for a lookup.
- **Spawn `tester` then `builder`:** any packet with more than one item, or any item
  touching a frozen surface, a verdict rule, the ESI client or an operator-facing
  surface.
- **Spawn `builder` alone:** a one-item packet the lead can verify by running one test.
  For a small packet — one file, under about 80 lines — the cheap model is enough.
- **Spawn `reviewer`:** every builder branch touching a number the operator sees, a
  gate, a frozen surface or a store. Skip it for docs-only branches and for one-line
  fixes the lead verified by running the test.
- **Packets live in `.claude/packets/<name>.md`. The lead hands an agent the file path,
  never the pasted text**, so the lead's own context stays small.
- **Between jobs, the operator clears the session.** The checkpoint block is the memory,
  not the chat.

## How the operator uses it

- "Recon: <question>" — the lead spawns `recon` and reports the answer.
- "Build packet <name>" — the lead writes or reuses the packet, spawns `tester` then
  `builder` per the delegation policy, checks the handoff against the diff, and reports
  it when it lands.
- "Review <branch>" — the lead spawns `reviewer` and reports GO / NO-GO.
- "Integrate" — the lead merges in order, runs the gates, and says whether a restart is
  owed.

Costs: recon is cheap; testers, builders and reviewers are not, and each packet-sized
run is a real spend. The lead does not spawn a reviewer for a docs-only branch, and never
two builders on the same files.

## Setup on a machine

1. The agent files are tracked under `.claude/agents/` and `.codex/agents/`, and the
   packets under `.claude/packets/`; the `.gitignore` rules keep the rest of `.claude/`
   machine-local.
2. `.claude/settings.json` (machine-local, not tracked) allow-lists the commands the
   agents run without a prompt: `uv run pytest`, `uv run ruff`, `selftest`, `uv sync`,
   the JumpStarter check, `git worktree`, `git checkout -b claude/*`, `git commit`,
   `git push` to `claude/*`. It denies force-push, hard reset, `git stash`, `git add -A`
   and branch deletion. No ESI-fetching subcommand is allowed: those prompt on purpose.
   Because the file is machine-local it is never in a fresh checkout: its absence from
   a clone is not evidence the project has no allow-list.
3. Worktrees need their own environment: `uv sync --extra dev --extra gui` once per
   worktree, or the desk tests are not in the count.
4. No flag or restart is needed: each harness picks up changes in its native role
   directory.

## For Codex

Codex reads `AGENTS.md` and loads its native roles from `.codex/agents/`. Tester,
builder and reviewer route to `gpt-5.6-terra` at high effort; recon routes to
`gpt-5.6-luna` at medium effort. Both harnesses receive the same packet path under
`.claude/packets/`; see [`CODEX_NOTES.md`](CODEX_NOTES.md). The Claude-to-Codex handoff
crossing has not yet been exercised in this repo; the first packet that does so records
it in the checkpoint.
