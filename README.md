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

## Running it

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.12.

```
uv sync
cp config.example.toml config.toml     # then fill in the Discord webhook
uv run python -m evescreener selftest

uv run python -m evescreener ingest-history   # loads the SDE on first run
uv run python -m evescreener sweep-books
uv run python -m evescreener digest
```

`digest --dry-run` renders and archives without posting. `daemon` and `census`
are declared but belong to Phase 1 and refuse to run until then.

Gate before every commit:

```
uv run pytest -q                                  # offline; live smoke: -m network
uv run ruff check . && uv run ruff format --check .
```

Nothing in this repository automates orders or the EVE client, and nothing may
be added that does. See `plan.md` §10.
