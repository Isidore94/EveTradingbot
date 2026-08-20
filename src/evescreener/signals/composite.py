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

**One engine, three uses** (plan.md §19 Part 1). `build_composite` also builds
FORGE-EW (equal weight over the *same* membership) and every sector index
(turnover weight over a market-group subtree). They differ only in
`weighting` and `member_ids`; the chain-link, the cap and the diagnostics are
shared, because three index implementations would drift into three answers.

**Member daily returns are winsorized before aggregation** (plan.md §17
D-22). ESI publishes unfiltered prints — §0 check #4 measured `close/low`
reaching 12.8 billion× — and the ATR path has clamped for exactly that reason
since Phase 2 while this path did not. One polluted member-day is enough to
destroy the level: on 2026-08-02 a single member at a 0.75% weight printed
`close 10.07 → 22,450.00`, a +222,839% "return", and moved FORGE **+1,661%**
in a day. The spike does not cancel when it reverts, because an arithmetic
weighted-return index can gain 222,839% and can only ever give back 100%.

**"Weighted by daily volume" means ISK TURNOVER — units × price — never raw
unit volume.** Raw units would make the index essentially 100% Tritanium: it
trades ~5 billion units a day at ~4 ISK. Turnover is the only common
denominator across items whose unit prices span twelve orders of magnitude
(§6). Membership is decided separately, by a unit-volume floor (§11 D3), so
4-ISK dust that clears the unit gate still cannot distort the level.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "Composite",
    "TURNOVER",
    "EQUAL",
    "build_composite",
    "winsorized_member_returns",
    "clamp_settings",
]

# Weighting schemes. FORGE is TURNOVER; FORGE-EW is EQUAL over the same names.
TURNOVER = "turnover"
EQUAL = "equal"


@dataclass(slots=True)
class Composite:
    frame: pd.DataFrame
    diagnostics: dict = field(default_factory=dict)
    member_ids: tuple[int, ...] = ()

    @property
    def known(self) -> bool:
        return not self.frame.empty and len(self.frame) > 1

    @property
    def series(self) -> pd.Series:
        """The index level, indexed by date. Empty when the index is UNKNOWN."""
        if self.frame.empty:
            return pd.Series(dtype="float64")
        return pd.Series(
            self.frame["close"].to_numpy(),
            index=pd.to_datetime(self.frame["datetime"], utc=True),
            name="level",
        )


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


def clamp_settings(signals) -> dict:
    """The member-return winsorization knobs, read from config in ONE place.

    Five call sites build indices, and §19.1's whole point is that they share
    one engine. Passing the clamp three keyword arguments at a time, five
    times over, is precisely how they would stop sharing it.
    """
    return {
        "return_clamp_k": signals.composite_return_clamp_k,
        "return_clamp_window": signals.composite_return_clamp_window,
        "return_clamp_floor": signals.composite_return_clamp_floor,
    }


def winsorized_member_returns(
    closes: pd.DataFrame,
    *,
    k: float | None = 8.0,
    window: int = 60,
    floor: float = 0.20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Member daily returns, clamped at `k ×` each member's own rolling median
    absolute return. Returns `(returns, clamped_mask)`.

    This mirrors `atr.winsorized_true_range` deliberately — same `k`, same
    window, same "clamp and flag, never hide" shape — because it answers the
    same measured fact (§0 check #4: CCP does not filter outlier prints).

    Two decisions worth stating, because both are silent failure modes:

    * **A member needs a real bar on BOTH days or it contributes nothing.**
      The returns are computed explicitly rather than with `pct_change`,
      whose `fill_method` default padded in pandas 2.x — which would hand a
      member reappearing after a 45-day gap its entire re-rating as one
      "daily" return. pandas 3.0 no longer pads, and this code does not
      depend on which of the two is installed.
    * **An unmeasurable ceiling clamps at the floor rather than passing the
      return through.** Where a member has fewer than five observations, the
      ceiling is UNKNOWN — and in this repo uncertainty never becomes
      permission (plan.md §4). The floor is only ever that fallback; it is
      **not** a lower bound on a measured ceiling, because clipping upward
      would hand a normally-stable member permission to print exactly the
      outlier this exists to catch.

    `k=None` (or a non-finite `k`) disables clamping entirely. That exists so
    a test can reproduce the pre-fix behaviour against the real 2026-08-02
    fixture and prove the clamp is what fixes it.
    """
    previous = closes.shift(1)
    usable = closes.notna() & previous.notna() & (previous > 0)
    returns = (closes / previous - 1.0).where(usable)
    empty_mask = pd.DataFrame(False, index=returns.index, columns=returns.columns)
    if k is None or not np.isfinite(float(k)) or returns.empty:
        return returns, empty_mask
    magnitude = returns.abs()
    median = magnitude.rolling(window, min_periods=5).median()
    # The floor is a FALLBACK for an unmeasurable ceiling, never a lower bound
    # on a measured one: clipping upward would hand a normally-stable member
    # permission to print the very outlier this exists to catch.
    ceiling = (median * float(k)).fillna(float(floor))
    clamped = returns.notna() & (magnitude > ceiling)
    cleaned = returns.where(~clamped, ceiling * np.sign(returns))
    return cleaned, clamped.fillna(False)


def build_composite(
    bars: pd.DataFrame,
    *,
    members: int = 100,
    single_cap: float = 0.10,
    rebalance_days: int = 30,
    min_members: int = 5,
    weighting: str = TURNOVER,
    member_ids=None,
    ticker: str = "FORGE",
    name: str | None = None,
    return_clamp_k: float | None = 8.0,
    return_clamp_window: int = 60,
    return_clamp_floor: float = 0.20,
) -> Composite:
    """Chain-linked index over the supplied bar lake. One engine, three uses.

    `bars` is the long-format lake frame (`type_id, datetime, close, volume,
    isk_value, ...`). The output frame carries `datetime, high, low, close,
    volume, order_count` so it is a drop-in reference series for RRS.

    * `weighting=TURNOVER` — FORGE and the sector indices.
    * `weighting=EQUAL` — FORGE-EW, which must be handed the **same**
      `member_ids` FORGE selected so the pair differ only in weighting; that is
      the whole point of the breadth read.
    * `member_ids` restricts the candidate pool (a sector's subtree, or
      FORGE's chosen basket). None means "everything in `bars`".
    """
    if weighting not in (TURNOVER, EQUAL):
        raise ValueError(f"unknown weighting {weighting!r}; expected {TURNOVER!r} or {EQUAL!r}")
    label = {"ticker": ticker, "name": name or ticker, "weighting": weighting}
    if bars is None or bars.empty:
        return Composite(pd.DataFrame(), {**label, "reason": "no bars"})
    frame = bars.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    if member_ids is not None:
        wanted = {int(value) for value in member_ids}
        frame = frame[frame["type_id"].isin(wanted)]
        if frame.empty:
            return Composite(
                pd.DataFrame(),
                {**label, "reason": "no bars for the requested members", "members": 0},
            )
    closes = frame.pivot_table(index="datetime", columns="type_id", values="close")
    turnover = frame.pivot_table(index="datetime", columns="type_id", values="isk_value")
    if closes.shape[0] < 2 or closes.shape[1] < min_members:
        return Composite(
            pd.DataFrame(),
            {
                **label,
                "reason": f"needs >= {min_members} members and 2 dates",
                "members": int(closes.shape[1]),
            },
        )
    returns, clamped_mask = winsorized_member_returns(
        closes,
        k=return_clamp_k,
        window=return_clamp_window,
        floor=return_clamp_floor,
    )

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
                if weighting == EQUAL:
                    # Equal weight, same names: FORGE-EW minus FORGE is breadth.
                    weights = pd.Series(1.0 / len(selected), index=selected.index, dtype="float64")
                else:
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
    # The composite carries `high == low == close` by construction: an index
    # level is one number per day and has no intraday range to report.
    # Downstream this means `atr.true_range` on a composite reduces to
    # |Δclose|, so any ATR taken on it is a **close-to-close volatility
    # proxy**, not a range measure. That is the honest reading and it is what
    # the RRS power index wants — but it is deliberately smaller than a real
    # instrument's ATR, so `power_index = Δref/ATR_ref` runs larger against a
    # composite than against a ranged series. Post-fix that term sits in the
    # low single digits; if it ever reaches the hundreds again, the index is
    # broken, not volatile (plan.md §17 D-22).
    final_weights = weights if weights is not None else pd.Series(dtype="float64")
    diagnostics = {
        **label,
        "members": int(len(final_weights)),
        "top_weight": float(final_weights.max()) if len(final_weights) else None,
        "weight_entropy": _entropy(final_weights.to_numpy()) if len(final_weights) else None,
        "single_cap": single_cap,
        "rebalance_days": rebalance_days,
        "rebalances": len(basket_history),
        "return_clamp_k": return_clamp_k,
        "return_clamp_window": return_clamp_window,
        "return_clamp_floor": return_clamp_floor,
        "clamped_member_days": int(clamped_mask.to_numpy().sum()),
        "measured_member_days": int(returns.notna().to_numpy().sum()),
        "clamped_share": (
            float(clamped_mask.to_numpy().sum()) / float(returns.notna().to_numpy().sum())
            if int(returns.notna().to_numpy().sum())
            else None
        ),
        "first_date": dates[0].isoformat(),
        "last_date": dates[-1].isoformat(),
        "level_last": float(levels[-1]),
        "basket_history": basket_history[-6:],
    }
    return Composite(
        composite,
        diagnostics,
        member_ids=tuple(int(value) for value in final_weights.index),
    )
