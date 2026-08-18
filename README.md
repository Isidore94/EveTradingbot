# EveTradingbot

A market screener for EVE Online's in-game market, built on CCP's public ESI
API. It is a decision-support port of the analytical core of a private US-equity
swing-trading system (TradingBotV3): daily-bar anchored-VWAP setups, relative
strength, level/pivot statistics, and expected-R ranking — re-grounded in EVE's
market microstructure and delivered as a Discord digest.

**Start with [`plan.md`](plan.md).** It is the authoritative planning document:
the repo architecture decision, the module inventory against the source repo,
the ESI data-layer and bar-contract specifications, the cost model, the signal
translation table, the zKillboard assessment, the phased build order with
validation gates, the risk register, and the explicit non-goals.

Nothing in this repository automates orders or the EVE client, and nothing may
be added that does. See `plan.md` §10.
