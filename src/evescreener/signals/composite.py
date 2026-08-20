"""The Forge Composite — this system's benchmark (plan.md §6, §9 R8).

There is no SPY in EVE and no market-cap to weight by, so the benchmark is
built: an **ISK-turnover-weighted index** over the top tracked types, with

* **chain-linked monthly reweighting**, so composition churn cannot fake an
  index move (the index level is carried forward across a reweight; only the
  *returns* of the new basket apply after it);
* a **single-type weight cap** (10%), so PLEX cannot become the market;
* members drawn only from the tracked (floored) universe, so the benchmark is
  never made of the thin names the screen already refuses to trade;
* published **diagnostics** — member count, weight entropy, top weight — that
  ride in the digest footer, because a benchmark nobody can audit is a benchmark
  nobody should trust.

The composite is returned as a bar frame under the same contract as any type,
so RRS treats it as just another series.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = ["Composite", "build_composite"]


@dataclass(slots=True)
class Composite:
    frame: pd.DataFrame
    diagnostics: dict = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return not self.frame.empty and len(self.frame) > 1


def _entropy(weights: np.ndarray) -> float:
    """Normalized Shannon entropy: 1.0 = perfectly even, 0.0 = one member."""
    weights = weights[weights > 0]
    if weights.size <= 1:
        return 0.0
    raw = -float((weights * np.log(weights)).sum())
    return raw / float(np.log(weights.size))


def _capped_weights(turnover: pd.Series, cap: float) -> pd.Series:
    """Turnover weights with an iterative single-name cap."""
    weights = turnover.clip(lower=0.0)
    total = float(weights.sum())
    if total <= 0:
        return pd.Series(dtype="float64")
    weights = weights / total
    for _ in range(50):
        excess = weights[weights > cap]
        if excess.empty:
            break
        spill = float((excess - cap).sum())
        weights[weights > cap] = cap
        free = weights[weights < cap]
        if free.empty or free.sum() <= 0:
            break
        weights[free.index] = free + spill * (free / free.sum())
    return weights / float(weights.sum())


def build_composite(
    bars: pd.DataFrame,
    *,
    members: int = 100,
    single_cap: float = 0.10,
    rebalance_days: int = 30,
    min_members: int = 5,
) -> Composite:
    """Chain-linked, turnover-weighted composite over the supplied bar lake.

    `bars` is the long-format lake frame (`type_id, datetime, close, volume,
    isk_value, ...`). The output frame carries `datetime, high, low, close,
    volume, order_count` so it is a drop-in reference series for RRS.
    """
    if bars is None or bars.empty:
        return Composite(pd.DataFrame(), {"reason": "no bars"})
    frame = bars.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    closes = frame.pivot_table(index="datetime", columns="type_id", values="close")
    turnover = frame.pivot_table(index="datetime", columns="type_id", values="isk_value")
    if closes.shape[0] < 2 or closes.shape[1] < min_members:
        return Composite(
            pd.DataFrame(),
            {"reason": f"needs >= {min_members} members and 2 dates", "members": closes.shape[1]},
        )
    returns = closes.pct_change()

    dates = closes.index
    level = 1000.0
    levels: list[float] = []
    weights: pd.Series | None = None
    basket_history: list[dict] = []
    last_rebalance: pd.Timestamp | None = None
    daily_turnover: list[float] = []

    for position, stamp in enumerate(dates):
        rebalance = weights is None or (
            last_rebalance is not None and (stamp - last_rebalance).days >= rebalance_days
        )
        if rebalance:
            lookback = turnover.iloc[max(0, position - rebalance_days) : position + 1]
            median_turnover = lookback.median(skipna=True).dropna()
            median_turnover = median_turnover[median_turnover > 0]
            if len(median_turnover) >= min_members:
                selected = median_turnover.nlargest(members)
                weights = _capped_weights(selected, single_cap)
                last_rebalance = stamp
                basket_history.append(
                    {
                        "date": stamp.isoformat(),
                        "members": int(len(weights)),
                        "top_weight": float(weights.max()) if len(weights) else 0.0,
                        "entropy": _entropy(weights.to_numpy()),
                    }
                )
        if weights is None or weights.empty:
            levels.append(level)
            daily_turnover.append(0.0)
            continue
        if position == 0:
            levels.append(level)
            daily_turnover.append(float(turnover.iloc[position].reindex(weights.index).sum()))
            continue
        # Chain link: only the basket's own returns move the level, so a
        # reweight never prints as a market move.
        day_returns = returns.iloc[position].reindex(weights.index)
        available = day_returns.notna()
        if not available.any():
            levels.append(level)
            daily_turnover.append(0.0)
            continue
        live_weights = weights[available]
        live_weights = live_weights / float(live_weights.sum())
        level = level * (1.0 + float((day_returns[available] * live_weights).sum()))
        levels.append(level)
        daily_turnover.append(
            float(turnover.iloc[position].reindex(weights.index).sum(skipna=True))
        )

    series = pd.Series(levels, index=dates, name="close")
    composite = pd.DataFrame(
        {
            "datetime": dates,
            "high": series.to_numpy(),
            "low": series.to_numpy(),
            "close": series.to_numpy(),
            "volume": daily_turnover,
            "order_count": np.ones(len(dates), dtype=int),
        }
    ).reset_index(drop=True)
    # The composite has no true high/low; using close for both is the honest
    # choice — an index level is a single number per day, and ATR on it reads
    # close-to-close, which is exactly what the RRS power index wants.
    final_weights = weights if weights is not None else pd.Series(dtype="float64")
    diagnostics = {
        "members": int(len(final_weights)),
        "top_weight": float(final_weights.max()) if len(final_weights) else None,
        "weight_entropy": _entropy(final_weights.to_numpy()) if len(final_weights) else None,
        "single_cap": single_cap,
        "rebalance_days": rebalance_days,
        "rebalances": len(basket_history),
        "first_date": dates[0].isoformat(),
        "last_date": dates[-1].isoformat(),
        "level_last": float(levels[-1]),
        "basket_history": basket_history[-6:],
    }
    return Composite(composite, diagnostics)
