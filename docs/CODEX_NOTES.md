# Working on EveTradingbot with Codex

Document role: **active runbook.** What a Codex session reads in this repo, what it
cannot do, and how the same work packets reach it.

## What Codex reads

- **`AGENTS.md` at the repo root.** It is a byte-identical generated copy of
  `CLAUDE.md`, so the operating rules — the bounded read, the hard invariants, the core
  rules, the commands, the working agreement, the ask-first rule, the short-chat rule —
  are the same for both tools. There is no Codex-specific variant, on purpose: two files
  that say almost the same thing drift, and then agents disagree. (Before 2026-09-04
  `plan.md` §11 D8 said "No AGENTS.md copy — one file, one truth"; the repo had already
  been carrying an identical `AGENTS.md` since 2026-08-25, and D8 is amended to say so.)
- Everything `AGENTS.md` points at: `CURRENT_CHECKPOINT.md`'s "Active state at a glance"
  block, `plan.md` §4/§10/§11/§17 and the active item's section, `CHANGELOG.md`'s
  inventory (searched, not read), `docs/README.md`, `docs/INTERNALS.md`,
  `docs/decisions/0001-owner-goals-and-priorities.md`.

**Never hand-edit `AGENTS.md`.** Edit `CLAUDE.md` and run
`python ../JumpStarter/tools/jumpstart.py sync-agents .` (or `cp CLAUDE.md AGENTS.md`
when JumpStarter is not checked out beside this repo). `jumpstart.py check .` fails on
the sha256 mismatch, which is how a hand-edit gets caught.

## Native roles and model routing

Codex loads `tester`, `builder`, `reviewer` and `recon` from `.codex/agents/`. Tester,
builder and reviewer use `gpt-5.6-terra` at high reasoning effort; recon uses
`gpt-5.6-luna` at medium effort. The lead keeps the session model. This preserves the
same strong/cheap cost split as Claude without changing `.claude/agents/` or its model
routing. The TOML files were adapted from the Claude roles by hand, not by substitution:
the scope, the `claude/<slug>` branch family, the safety clauses and the handoff format
are identical on purpose.

Codex does not read `.claude/settings.json`; its own sandbox and approval settings
apply. Anything the Claude allow-list treats as destructive is still destructive, and
the ESI rules bind Codex exactly as they bind Claude: no subcommand that fetches
(`sweep-books`, `ingest-history`, `census`, `sde`, `killmails`, `daemon`) is ever a
"check".

## Handing a packet to Codex

The packet format is tool-neutral (`.claude/packets/PACKET_TEMPLATE.md`). To run a
native role in a Codex session:

1. Start at the repo root, on the packet's branch (`claude/<slug>`), in its own worktree
   if another session is running — the desk or the daemon may be running from the main
   checkout. `uv sync --extra dev --extra gui` once in the worktree.
2. Spawn the matching native role from `.codex/agents/` and give it the packet path
   under `.claude/packets/`, plus the branch name. Claude and Codex consume the same
   packet; never maintain a second Codex packet copy.
3. Require the same handoff or verdict format the role definition specifies. **The
   formats are the interface between tools**: a Codex builder's handoff must be readable
   by a Claude Code lead, and the reverse. The crossing is unproven in this repo until
   the first packet that does it is recorded in the checkpoint.

One packet, one session, one role. A session that builds and then reviews its own work is
not a review.

## What stays the same in both tools

- The bounded read comes before the first edit.
- Fail-before-fix is proven, not claimed.
- Review is by reproduction, not by reading.
- Live stores (`./data/`, `config.toml`) are read-only unless the packet names the
  write; probes run against a copy under `EVESCREENER_DATA_DIR`.
- One checkout, many agents: worktrees for builders and reviewers, and nobody switches
  the main checkout's branch while the desk or daemon runs from it.
- Chat is short; detail lives in commits, docs and handoffs.
- The operator decides restarts, promotions, priorities and every `LIVE_VALIDATED`.
