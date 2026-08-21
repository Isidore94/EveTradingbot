"""Top performers — the strongest names over a week and a month (plan.md §20.3).

**A week in EVE is seven days.** §20.3 originally specified 5- and 20-bar
windows. That is the *equity* convention, and it is only a week because the
exchange shuts at the weekend: five trading days spans seven calendar ones.
EVE's market never closes, so a week is **7 completed bars** and a month is
**30**. Ranking on 5 and 20 would have measured five days and labelled it a
week — a habit ported from the source system rather than a decision about this
one (§6). The plan is amended with that reason rather than silently followed.

**This ranks; it does not recommend.** A large trailing return is a fact about
the past, and this system's own measured verdict is that continuation does not
pay in an elastic-supply market (§6, §17). The page exists so the operator can
*find* names quickly, not because strength predicts anything here.

Three guards, all of them the same ones the rest of the desk uses:

* **Completed bars only.** R2 keeps partial days out of the lake, so a window
  is always whole days that finished happening.
* **Stale is UNKNOWN.** A type whose bars stopped a month ago has no current
  return, and neither does a lake whose ingestion stopped — those are two
  different failures and `bars.bar_freshness` measures both (§21 R2).
* **A volume floor.** A dead item with one lucky print reads as +9,900%. The
  floor is a page control rather than a hidden constant, because a hidden
  filter is a hidden opinion.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .bars import bar_freshness

__all__ = [
    "COLUMNS",
    "DEFAULT_MIN_UNITS",
    "ENDPOINT_BARS",
    "ENDPOINT_DAYS",
    "MIN_ENDPOINT_BARS",
    "MONTH_BARS",
    "MONTH_DAYS",
    "WEEK_BARS",
    "WEEK_DAYS",
    "top_performers",
]

#: EVE trades every day. See the module docstring for why this is not 5 and 20.
#: These are CALENDAR days, not bar counts: a thin type's bars are not
#: consecutive, so counting rows back overshoots the window (§21 R5's lesson).
WEEK_DAYS = 7
MONTH_DAYS = 30
#: Kept as aliases so callers reading "a week" get the same number either way.
WEEK_BARS = WEEK_DAYS
MONTH_BARS = MONTH_DAYS

#: Same floor the maker read uses, and for the same reason.
DEFAULT_MIN_UNITS = 100.0

#: Calendar days median-ed at each end of a window. Three is the smallest span
#: that survives one bad print, which is exactly the failure being defended
#: against.
ENDPOINT_DAYS = 3
#: Kept as an alias: the guard is a span of days, not a count of rows.
ENDPOINT_BARS = ENDPOINT_DAYS
#: A median over ONE observation is that observation. An endpoint window
#: holding a single bar therefore offers no defence against a print at all, so
#: it is UNKNOWN rather than falsely robust.
MIN_ENDPOINT_BARS = 3

COLUMNS = [
    "type_id",
    "name",
    "tier",
    "close",
    "week_pct",
    "week_pct_raw",
    "month_pct",
    "month_pct_raw",
    "median_units",
    "last_bar",
    "state",
]


def _raw_return(days: np.ndarray, closes: np.ndarray, span: int) -> float:
    """Close on the last bar against the close exactly `span` days earlier.

    Reported but **never ranked**. On the real Forge lake this reads as high as
    **+49,699,900%** over a week, which is not a return, it is a print: *Batch
    Compressed Plagioclase II-Grade* had one 0.01 ISK daily average on
    2026-08-02, from a single order, and that bar was the window's start point.
    """
    if closes.size == 0:
        return float("nan")
    target = days[-1] - np.timedelta64(span, "D")
    match = np.nonzero(days == target)[0]
    if match.size == 0:
        return float("nan")
    start, end = closes[match[-1]], closes[-1]
    if not np.isfinite(start) or not np.isfinite(end) or start <= 0:
        return float("nan")
    return float((end / start - 1.0) * 100.0)


def _window_median(days: np.ndarray, closes: np.ndarray, anchor, span: int) -> float:
    """Median close over the `span` calendar days ending at `anchor`, or NaN.

    **A median over one observation is that observation.** On a thin type an
    endpoint window can hold a single bar, and if that bar is a 0.01 ISK print
    the "robust" return is exactly as wrong as the raw one — which is how
    *Batch Compressed Plagioclase II-Grade* still read +49,699,900% over a
    month after the first fix. Over **two** it is their arithmetic MEAN, which
    one 0.01 ISK print drags almost as far as it drags the raw number: Aug 10 =
    0.01 with Aug 12/17/19 = 100 produced a ranked **+99.98%** week beside a
    **raw 0%**, and state OK (§22 S5d). Three is the smallest window in which a
    single bad print is outvoted, and calling the result "print-resistant"
    below that was a claim the arithmetic did not support.

    Fewer than `MIN_ENDPOINT_BARS` observations is
    UNKNOWN: the name has no defensible return over that window, and saying so
    is the honest answer (§4).
    """
    first = anchor - np.timedelta64(span - 1, "D")
    mask = (days >= first) & (days <= anchor)
    values = closes[mask]
    values = values[np.isfinite(values)]
    if values.size < MIN_ENDPOINT_BARS:
        return float("nan")
    return float(np.median(values))


def _robust_return(
    days: np.ndarray, closes: np.ndarray, span: int, endpoint_days: int = ENDPOINT_DAYS
) -> float:
    """Median close over the last few days against the median `span` days back.

    **Two defects, one fix.**

    *Prints.* CCP does not filter outlier prints (§17; §17 D-22 winsorizes
    FORGE for exactly this reason), and `close` is the day's *mean* transaction
    price — so one fat-fingered trade drags a whole daily bar. A close-to-close
    return rests both endpoints on a single unfiltered number. A three-day
    median at each end costs almost nothing where the data is sound (measured
    on 2,944 tracked Forge types, the median difference from the raw return is
    **0.88 percentage points**) and removes the worst readings.

    *Gaps.* The window is **calendar days, not rows**. A thin type trading on
    the 22nd, 27th, 28th and 31st has bars that are not consecutive days, so
    counting seven rows back spans nearly a month — the same defect §21 R5
    fixed in the lead-lag study, and it is fixed the same way here. A type with
    nothing trading near the far endpoint gets **UNKNOWN**, which is the true
    answer: it has no measurable week.

    It is not a cure. Genuinely repriced names still read in the thousands of
    percent, and the page shows the raw number beside this one so the operator
    can see when the two disagree rather than trust a threshold nobody derived
    (§21 R4).
    """
    if closes.size == 0:
        return float("nan")
    end = _window_median(days, closes, days[-1], endpoint_days)
    start = _window_median(days, closes, days[-1] - np.timedelta64(span, "D"), endpoint_days)
    if not np.isfinite(start) or not np.isfinite(end) or start <= 0:
        return float("nan")
    return float((end / start - 1.0) * 100.0)


def top_performers(
    bars: pd.DataFrame,
    *,
    now=None,
    names: dict[int, str] | None = None,
    tiers: dict[int, str] | None = None,
    volumes: dict[int, float] | None = None,
    min_units: float = DEFAULT_MIN_UNITS,
    rank_by: str = "week_pct",
    max_bar_age_days: int = 3,
    max_refresh_age_hours: float = 36.0,
) -> pd.DataFrame:
    """Rank one region's tracked names by trailing return.

    `bars` must hold **one region**. Two regions are two markets, and a table
    that ranks them together compares prices that were never quoted to the same
    character (§21 R8) — so it is refused rather than pooled.
    """
    if bars is None or bars.empty:
        return pd.DataFrame(columns=COLUMNS)
    if "region_id" in bars.columns and bars["region_id"].nunique() > 1:
        raise ValueError(
            "top_performers ranks one region at a time; this frame holds "
            f"{bars['region_id'].nunique()} — two markets are not one table"
        )

    names = names or {}
    tiers = tiers or {}
    volumes = volumes or {}

    rows = []
    for type_id, group in bars.groupby("type_id", sort=False):
        ordered = group.sort_values("datetime")
        closes = pd.to_numeric(ordered["close"], errors="coerce").to_numpy(dtype="float64")
        days = (
            pd.to_datetime(ordered["datetime"], utc=True)
            .dt.normalize()
            .to_numpy(dtype="datetime64[D]")
        )
        freshness = bar_freshness(
            ordered,
            now=now,
            max_bar_age_days=max_bar_age_days,
            max_refresh_age_hours=max_refresh_age_hours,
        )
        week = _robust_return(days, closes, WEEK_DAYS)
        month = _robust_return(days, closes, MONTH_DAYS)
        week_raw = _raw_return(days, closes, WEEK_DAYS)
        month_raw = _raw_return(days, closes, MONTH_DAYS)

        if freshness.stale:
            # A stale series has no *current* return, only an old one (§21 R2).
            state = "STALE"
            week = month = week_raw = month_raw = float("nan")
        elif not np.isfinite(week) and not np.isfinite(month):
            state = "UNKNOWN"
        else:
            state = "OK"

        rows.append(
            {
                "type_id": int(type_id),
                "name": names.get(int(type_id), f"type {int(type_id)}"),
                "tier": tiers.get(int(type_id)),
                "close": float(closes[-1]) if closes.size and np.isfinite(closes[-1]) else np.nan,
                "week_pct": week,
                "week_pct_raw": week_raw,
                "month_pct": month,
                "month_pct_raw": month_raw,
                "median_units": volumes.get(int(type_id), np.nan),
                "last_bar": freshness.last_bar_date,
                "state": state,
            }
        )

    frame = pd.DataFrame(rows, columns=COLUMNS)
    if frame.empty:
        return frame

    if min_units > 0:
        units = pd.to_numeric(frame["median_units"], errors="coerce")
        # A name with no measured volume is UNKNOWN, and UNKNOWN fails a floor.
        frame = frame[units.notna() & (units >= min_units)]

    key = rank_by if rank_by in frame.columns else "week_pct"
    return frame.sort_values(key, ascending=False, na_position="last").reset_index(drop=True)
