# 0005 — A new daily bar contract: no `open`, `close ← ESI average`

Date: 2026-08-18

## Context
TradingBotV3's `DAILY_BAR_COLUMNS` is `["datetime","open","high","low","close","volume"]`
and every daily frame passes through a normalizer that silently returns an empty
frame if `open` or `close` is missing. ESI's market history publishes
`date, average, highest, lowest, volume, order_count` — **there is no open**, and
there never will be: EVE has no session boundary that produces one.

The tempting option was to preserve the source contract with
`open ← prior day's average`.

## Decision
A new contract (`plan.md` §4):

```
EVE_DAILY_BAR_COLUMNS = ["datetime", "high", "low", "close", "volume", "order_count"]
# close ← ESI average, mapped at exactly one site in the adapter
# datetime = the ESI date stamped at 11:00 UTC (the downtime boundary)
# plus derived isk_value = volume × close
```

No `open` column exists and none is ever synthesized. Anything upstream that
requires an open is dead code here.

## Rationale
A synthetic open does not fail loudly — it makes every open-consumer *run* while
computing fiction:

- the earnings gap-index inference measures `|open − prior close|`, which becomes
  identically zero;
- `gap_atr_multiple` and its minimum filter go dark silently;
- `close > open` candle confirmations degrade to day-over-day sign tests without
  saying so;
- `laguerre_rsi`'s OHLC validation *raises* when the open falls outside the bar's
  high–low range, and yesterday's average routinely does.

That is precisely the source system's own invariant inverted: missing data is
uncertainty, never confirmation. A synthetic open launders uncertainty into
confirmation. The frame seam is kept as a concept — one contract drives the
stack — but the lie is not.

The cost was priced before it was paid: everything the port actually keeps (all
ATR variants, the SMA/EMA stack, every `levels.py` computation, band
classification, `expected_r`, RRS) reads only high/low/close/volume and survives
untouched. `order_count`, which is referenced nowhere in the source repo, is a
free column and earns its place three times over — liquidity floor input,
`avg_trade_size = volume / order_count` as a spoof discriminator, and
participation RVOL.
