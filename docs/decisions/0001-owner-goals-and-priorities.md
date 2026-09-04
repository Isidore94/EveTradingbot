# 0001 — The operator's goals and priorities, in their own words

Date: 2026-09-04 (created at the JumpStarter retrofit; questionnaire **not yet asked**)

Status: `OPEN` — every answer below is either a statement the operator already made on
the record, quoted with its source, or marked **OPEN**. Nothing here was asked directly.
When the questionnaire is asked, amend with a new dated record; do not overwrite these.

**This record is the tie-breaker for every prioritisation call**, until the operator says
otherwise. `CLAUDE.md`'s mandatory read points here.

It exists because a build grows faster than the statement of what it is for. This repo
went from planning to a 27,000-line desk in one week (2026-08-18 → 2026-08-25) and
carries roughly forty owed live-validation items that only the operator can perform. The
questions below are how that list gets ordered.

---

## How to fill this in

**Ask one question at a time. Record the answer verbatim.** An answer you paraphrase is
an answer you have already begun to overwrite. If an answer raises a new question, ask it
and record that too. If the operator does not have an answer yet, write "**OPEN** — the
operator has not decided" rather than a guess.

Do not summarise the answers into requirements in this file. That translation happens in
`plan.md`, and it cites this record.

---

## Context

The repo was retrofitted with the JumpStarter control set on 2026-09-04. At that moment
the §23 HAULING track's code was done (`1,090 passed, 7 deselected`), nothing anywhere
was `LIVE_VALIDATED`, and the consolidated checklist in `CURRENT_CHECKPOINT.md` (sections
A–I plus the §23 items) was the only thing between the build and use. The operator's
priorities among those items had never been recorded in their own words.

## The goals, in priority order

**OPEN** — not yet stated by the operator. The one goal on the record is the repo's own
framing question, from `README.md`:

> is EVE market swing trading a money-making activity, for this operator, at his size?

## The questionnaire

### 1. What must this get right FIRST?

> **OPEN.** Recovered, not asked: the checklist's own ordering (`CURRENT_CHECKPOINT.md`
> section H) says "use the paper platform to validate the **cost model**, not the setup"
> — ten real fills against predicted effective prices — because if the cost model is
> wrong, every other number is wrong too. This was the builder's reasoning, not the
> operator's words.

### 2. How is success scored?

> **OPEN.** Recovered: `plan.md` §12.4 freezes the verdict tracker's rule (20 closed
> trades for a first read, 40 for a verdict; `FALSIFIED` is an expected outcome), and
> §13.6 / §14.3 freeze the study rules. Whether that is how *the operator* scores success
> has not been asked.

### 3. What does "right" mean for the main output?

> **OPEN.**

### 4. Which screens, files or reports do you ACTUALLY use?

> **OPEN.** Recovered: the DESK page was built because the operator's loop was described
> as *"open it, walk the lists, chart each name, paper trade the ones I like, tab out"*
> (`CHANGELOG.md` 2026-08-20, DESK). Which of the twelve pages are opened today is not
> recorded.

### 5. Where should the answer appear?

> **OPEN.**

### 6. What is the slow part of your work right now?

> **OPEN.**

### 7. What is never automated?

> Recorded as the product boundary, `plan.md` §10, and never as a questionnaire answer:
> no order placement, no order modification, no client automation, no SSO scope that acts
> on a character. Read-only public ESI and a Discord webhook. **What the system may do on
> its own** beyond fetching, scanning and publishing a digest is **OPEN**.

### 8. What would make you stop trusting it?

> **OPEN.** Recovered: 2026-08-21, on the paper form — *"when I go to paper trade it's
> just a mess and it doesn't work"* (`CHANGELOG.md`, "Two fill models"). One recorded
> instance of lost trust, and the cause was a stale book the form did not refuse.

### 9. What does it never do?

> See 7. The boundary is written; the operator's own phrasing of it is **OPEN**.

### 10. How do you want to be told things?

> **OPEN.** `CLAUDE.md` now asks for ten short lines in chat with detail in the docs
> (inherited from JumpStarter); the operator has not confirmed that preference.

### 11. What do you already do by hand that the system should match?

> **OPEN.** The system is a port of the operator's own US-equity process (TradingBotV3:
> anchored-VWAP bands, relative strength, levels, expected-R). Whether the EVE version
> should match that process or has already diverged from how the operator actually
> trades EVE is not recorded.

### 12. What is the one thing you would fix today?

> **OPEN.**

## Decision

No prioritisation decision is taken by this record. Until the questionnaire is asked:

- The order in `CURRENT_CHECKPOINT.md`'s consolidated checklist stands (A → I, then the
  §23 items), because it is the only ordering on the record.
- The one recorded authorization shape — 2026-08-25, *"build first, evaluate against
  competitors and live gates afterwards"* (§17 D-33) — is not read as a standing
  preference; it was scoped to §23 H1–H4.
- Unused surfaces are **not** candidates for removal until the operator names them.

## Consequences

- The questionnaire is owed, and is recorded as owed in `CURRENT_CHECKPOINT.md`.
- Until it is answered, no agent reorders the checklist or retires a finished feature on
  its own reading of what the operator wants.

## Reopen trigger

Ask the questionnaire at the next session the operator has twenty minutes for it; then
amend with `0002`. Re-ask when the operator's use of the desk changes materially.
