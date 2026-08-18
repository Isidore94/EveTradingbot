# 0001 — Decision-support only: no order automation, no client automation

Date: 2026-08-18

## Context
EVE has a public market API and a large community of automation tools. Two lines
exist and they are not the same line: reading the market, and acting on it. The
second splits again into ESI-side action (order endpoints, SSO scopes that act on
a character) and client-side action (input injection, screen scraping, reading
the client's cache files).

## Decision
This system reads. It never places, modifies, or cancels an order; it never holds
an SSO scope that acts on a character; it never touches the EVE client. `plan.md`
§10.1 and §10.2 make both permanent non-goals, not deferred features.

## Rationale
Three reasons, in descending order of force.

1. **Client automation is botting and is bannable.** There is no version of it
   that is safe, and nothing in this design needs it.
2. **There is no order-entry advantage to capture.** The order cache is five
   minutes deep for everyone (ADR 0004). A screener that hands the operator a
   ranked, cost-netted list at 16:00 UTC loses nothing by having a human press
   the buttons — the edge is in *which* item, not in *how fast* the order lands.
3. **It is the source system's boundary, and it held.** TradingBotV3 has run for
   years as decision-support with order routing explicitly out of roadmap. The
   discipline is imported along with the analytics.

The cost is real and accepted: the operator does the clicking, and the system
can never measure its own fills without him recording them (hence the decisions
log, `plan.md` §2).
