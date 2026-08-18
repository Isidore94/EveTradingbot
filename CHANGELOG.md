# EveTradingbot implemented history

Authoritative for what exists and the sequence of revisions. Remaining work:
`plan.md`. Labels keep the source-repo meanings: `IMPLEMENTED` = code exists,
`GREEN` = deterministic tests pass, `LIVE_VALIDATED` = real-market evidence
recorded, `PROMOTED` = explicit operator decision.

## 2026-08-18 — Planning complete, decisions locked

- `plan.md` landed: port review of TradingBotV3 (`phase05-r8-weekend-prep`,
  `phase05-r2-focus-gating-strength-board`), repo architecture decision
  (standalone repo + vendoring), module inventory, ESI data-layer spec with
  verified token arithmetic, bar contract (no `open`, `close ← average`),
  depth-aware cost model, signal translation table, zKillboard assessment
  (Phase 5, EVE Ref archives + R2Z2), six phases with gates, risk register,
  non-goals.
- `plan.md` §11 added: locked implementation decisions D1–D8 (uv/httpx/pandas
  stack, config shape, cadence defaults, notional tiers, liquidity floor,
  50-name seed watchlist, test/fixture policy, Discord webhook contract,
  anchor calendar, governance/control set).
- `CLAUDE.md`, `CURRENT_CHECKPOINT.md`, this file: governance control set
  established. No product code exists yet.
