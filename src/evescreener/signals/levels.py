"""Support/resistance levels — ported from TradingBotV3 `levels.py`.

Source: `scripts/master_avwap_lib/levels.py` (884 LOC) on branch
`phase05-integration-blitz`, commit d60cbaf. Every computation there uses only
high/low/close/volume; the single `open` reference was its required-column
gate, which this port drops (plan.md §2, §4).

Kept intact: relative pivots with ATR prominence, high-volume horizontal
levels bucketed green/red by relvol, ATR-fraction clustering, touch/respect/
break statistics with post-break returns, and the conviction score that
combines structural weight with multi-year respect history.

Changed for EVE:
* required columns lose `open`;
* relvol thresholds are config, re-tuned against EVE volume distributions
  rather than equity ones;
* **a new level family: psychological ISK round numbers** (1M / 100M / 1B).
  These are strong player anchors in EVE — order prices cluster on them the
  way equity prices cluster on whole dollars — and they earn their weight
  through the same touch statistics as any other level, not by assertion.
* earnings-origin tagging becomes patch-anchor-origin tagging.

Dropped: the level *store* file layer (Windows reserved-filename handling, per
symbol JSON files). Levels live in memory and in the digest; the lake is the
only persistence this system has.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date

import pandas as pd

__all__ = [
    "build_level_store",
    "cluster_levels",
    "compute_relvol",
    "extract_hv_levels",
    "find_relative_pivots",
    "level_conviction",
    "levels_near",
    "recompute_touch_stats",
    "round_number_levels",
]

# Ported constants. The relvol thresholds are the two the port re-tunes.
HV_RELVOL_GREEN = 3.0
HV_RELVOL_RED = 2.0
HV_VOL_SMA = 50
LEVEL_TOL_ATR_FRACTION = 0.05
LEVEL_BREAK_ATR = 0.25
LEVEL_FORWARD_BARS = 5
LEVEL_TOUCH_WEIGHT = 0.08
LEVEL_TOUCH_CAP = 0.40
LEVEL_BUCKET_WEIGHTS = {"green": 1.0, "red": 0.35, "round_isk": 0.5}
LEVEL_RESPECT_FULL_COUNT = 8
LEVEL_RESPECT_BONUS_CAP = 1.0
LEVEL_BREAK_DISCOUNT_FLOOR = 0.5
RELATIVE_PIVOT_LOOKBACK = 10
RELATIVE_PIVOT_PROMINENCE_ATR = 0.5
PIVOT_LEVEL_WEIGHT = 0.6

REQUIRED_COLUMNS = ("datetime", "high", "low", "close", "volume")


def _coerce_float(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Required columns without `open`. A frame missing one is empty, loudly."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"levels: frame is missing required columns {missing}")
    work = frame.loc[:, list(REQUIRED_COLUMNS)].copy()
    for column in ("high", "low", "close", "volume"):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    return work.dropna(subset=["high", "low", "close"]).reset_index(drop=True)


def _date_text(value) -> str:
    if isinstance(value, str):
        return value[:10]
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError):
        return str(value or "")


def _date_key(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError):
        return None


def _level_tolerance(atr20: float | None, price: float | None = None, *, tol_frac: float) -> float:
    atr = _coerce_float(atr20)
    if atr and atr > 0:
        return float(atr) * float(tol_frac)
    reference = _coerce_float(price) or 0.0
    # No ATR: fall back to a fraction of price, never to zero (a zero tolerance
    # makes every level untouchable, which reads as "never tested").
    return abs(reference) * float(tol_frac) * 0.5


def find_relative_pivots(
    frame: pd.DataFrame | None,
    *,
    lookback: int = RELATIVE_PIVOT_LOOKBACK,
    prominence_atr: float = RELATIVE_PIVOT_PROMINENCE_ATR,
    atr20: float | None = None,
    max_lookback_bars: int | None = None,
) -> list[dict]:
    """Confirmed swing highs/lows. The most recent `lookback` bars cannot be
    confirmed yet and are excluded — completed structure only."""
    work = _normalize_frame(frame)
    bar_count = len(work)
    k = max(1, int(lookback))
    if bar_count < 2 * k + 1:
        return []
    highs = work["high"].tolist()
    lows = work["low"].tolist()
    stamps = [_date_text(value) for value in work["datetime"]]
    atr = _coerce_float(atr20)
    prominence = float(atr) * float(prominence_atr) if atr and atr > 0 else 0.0
    start = k
    if max_lookback_bars and int(max_lookback_bars) > 0:
        start = max(k, bar_count - int(max_lookback_bars))
    pivots: list[dict] = []
    for index in range(start, bar_count - k):
        window_high = highs[index - k : index + k + 1]
        window_low = lows[index - k : index + k + 1]
        if prominence > 0 and (max(window_high) - min(window_low)) < prominence:
            continue
        left_high = max(highs[index - k : index])
        right_high = max(highs[index + 1 : index + k + 1])
        # Strict local extreme on BOTH sides, so a flat plateau is not flagged.
        if highs[index] > left_high and highs[index] > right_high:
            pivots.append(
                {
                    "kind": "pivot",
                    "origin_side": "high",
                    "price": float(highs[index]),
                    "bar_index": index,
                    "first_seen": stamps[index],
                    "last_seen": stamps[index],
                    "bucket": "red",
                    "relvol": None,
                }
            )
        left_low = min(lows[index - k : index])
        right_low = min(lows[index + 1 : index + k + 1])
        if lows[index] < left_low and lows[index] < right_low:
            pivots.append(
                {
                    "kind": "pivot",
                    "origin_side": "low",
                    "price": float(lows[index]),
                    "bar_index": index,
                    "first_seen": stamps[index],
                    "last_seen": stamps[index],
                    "bucket": "red",
                    "relvol": None,
                }
            )
    return pivots


def compute_relvol(frame: pd.DataFrame | None, vol_sma: int = HV_VOL_SMA) -> pd.Series:
    work = _normalize_frame(frame)
    if work.empty:
        return pd.Series(dtype="float64")
    lookback = max(1, int(vol_sma))
    volume = pd.to_numeric(work["volume"], errors="coerce")
    baseline = volume.rolling(lookback, min_periods=lookback).mean()
    return volume / baseline


def extract_hv_levels(
    frame: pd.DataFrame | None,
    atr20: float | None,
    *,
    green: float = HV_RELVOL_GREEN,
    red: float = HV_RELVOL_RED,
    vol_sma: int = HV_VOL_SMA,
    anchor_dates: Iterable[str] = (),
) -> list[dict]:
    """High-volume horizontals: the highs and lows of participation days.

    `anchor_dates` are patch/expansion dates (the EVE analogue of earnings);
    a level born on one is tagged so the digest can say *why* it exists.
    """
    work = _normalize_frame(frame)
    if work.empty:
        return []
    relvol = compute_relvol(work, vol_sma=vol_sma).tolist()
    stamps = [_date_text(value) for value in work["datetime"]]
    anchors = {str(value)[:10] for value in anchor_dates}
    highs = work["high"].tolist()
    lows = work["low"].tolist()
    atr = _coerce_float(atr20)
    candidates: list[dict] = []
    for index in range(len(work)):
        value = _coerce_float(relvol[index])
        if value is None or value < float(red):
            continue
        bucket = "green" if value >= float(green) else "red"
        stamp = stamps[index]
        for side, prices in (("high", highs), ("low", lows)):
            price = _coerce_float(prices[index])
            if price is None:
                continue
            candidates.append(
                {
                    "kind": "hv_horizontal",
                    "price": float(price),
                    "origin_side": side,
                    "bucket": bucket,
                    "relvol": float(value),
                    "first_seen": stamp,
                    "last_seen": stamp,
                    "patch_origin": stamp in anchors,
                    "atr20_at_origin": atr,
                    "bar_index": index,
                }
            )
    return candidates


def round_number_levels(
    frame: pd.DataFrame | None, steps: Sequence[float], *, span_multiple: float = 3.0
) -> list[dict]:
    """Psychological ISK levels inside the frame's traded range.

    1M / 100M / 1B ISK are real player anchors: order prices pile onto them.
    They enter as ordinary levels and earn their conviction from the same
    touch statistics as everything else (plan.md §6).
    """
    work = _normalize_frame(frame)
    if work.empty or not steps:
        return []
    # The range comes from `close` (= ESI average), not high/low: a single
    # scam print in `highest` must not sprout forty spurious round levels.
    low = float(work["close"].min())
    high = float(work["close"].max())
    if not math.isfinite(low) or not math.isfinite(high) or high <= 0:
        return []
    stamp = _date_text(work["datetime"].iloc[0])
    levels: list[dict] = []
    seen: set[float] = set()
    for step in steps:
        step = float(step)
        if step <= 0:
            continue
        # Only generate levels of a magnitude the price actually lives at, so a
        # 4-ISK mineral does not sprout a thousand 1M-ISK levels.
        if high < step or low > step * span_multiple * 1000:
            continue
        start = math.floor(low / step) * step
        stop = math.ceil(high / step) * step
        count = int(round((stop - start) / step)) + 1
        if count > 200:
            continue
        for index in range(count):
            price = start + index * step
            if price <= 0 or price < low or price > high or price in seen:
                continue
            seen.add(price)
            levels.append(
                {
                    "kind": "round_isk",
                    "price": float(price),
                    "origin_side": "round",
                    "bucket": "round_isk",
                    "relvol": None,
                    "step": step,
                    "first_seen": stamp,
                    "last_seen": stamp,
                    "patch_origin": False,
                }
            )
    return levels


def _level_strength(level: dict) -> float:
    bucket = str(level.get("bucket") or "red").lower()
    base = LEVEL_BUCKET_WEIGHTS.get(bucket, LEVEL_BUCKET_WEIGHTS["red"])
    if str(level.get("kind")) == "pivot":
        base = PIVOT_LEVEL_WEIGHT
    touches = int(level.get("touch_count", 0) or 0)
    return round(base + min(LEVEL_TOUCH_CAP, touches * LEVEL_TOUCH_WEIGHT), 4)


def level_conviction(level: dict | None) -> float:
    """How real this level is, from cumulative respect/break history (0..~2).

    Structural weight plus a respect bonus, discounted toward half when the
    level breaks as often as it holds. An untested fresh level trusts its
    structure rather than being scored as failed.
    """
    if not isinstance(level, dict):
        return 0.0
    bucket = str(level.get("bucket") or "red").lower()
    base = LEVEL_BUCKET_WEIGHTS.get(bucket, LEVEL_BUCKET_WEIGHTS["red"])
    if str(level.get("kind")) == "pivot":
        base = PIVOT_LEVEL_WEIGHT
    respect = int(level.get("respect_count", 0) or 0)
    breaks = int(level.get("break_count", 0) or 0)
    bonus = min(
        LEVEL_RESPECT_BONUS_CAP,
        LEVEL_RESPECT_BONUS_CAP * respect / float(LEVEL_RESPECT_FULL_COUNT),
    )
    tested = respect + breaks
    ratio = (respect / tested) if tested > 0 else 1.0
    reliability = LEVEL_BREAK_DISCOUNT_FLOOR + (1.0 - LEVEL_BREAK_DISCOUNT_FLOOR) * ratio
    return round((base + bonus) * reliability, 4)


def _cluster_from_members(members: list[dict], atr20: float | None) -> dict:
    weights = [max(_coerce_float(member.get("relvol")) or 0.5, 0.01) for member in members]
    prices = [float(member["price"]) for member in members]
    total = sum(weights) or 1.0
    price = sum(p * w for p, w in zip(prices, weights, strict=True)) / total
    buckets = {str(member.get("bucket")) for member in members}
    bucket = "green" if "green" in buckets else ("round_isk" if "round_isk" in buckets else "red")
    kinds = {str(member.get("kind")) for member in members}
    kind = "hv_horizontal" if "hv_horizontal" in kinds else sorted(kinds)[0]
    first = [str(member.get("first_seen") or "") for member in members if member.get("first_seen")]
    last = [str(member.get("last_seen") or "") for member in members if member.get("last_seen")]
    relvols = [_coerce_float(member.get("relvol")) for member in members]
    relvols = [value for value in relvols if value is not None]
    level = {
        "kind": kind,
        "price": float(price),
        "band": [min(prices), max(prices)],
        "origin_sides": sorted(
            {
                str(member.get("origin_side") or "")
                for member in members
                if member.get("origin_side")
            }
        ),
        "bucket": bucket,
        "relvol": max(relvols) if relvols else None,
        "first_seen": min(first) if first else "",
        "last_seen": max(last) if last else "",
        "patch_origin": any(bool(member.get("patch_origin")) for member in members),
        "member_count": len(members),
        "atr20_at_update": _coerce_float(atr20),
        "touch_count": 0,
        "respect_count": 0,
        "break_count": 0,
    }
    level["strength"] = _level_strength(level)
    return level


def cluster_levels(
    candidates: list[dict] | None, atr20: float | None, *, tol_frac: float = LEVEL_TOL_ATR_FRACTION
) -> list[dict]:
    """Merge candidates that sit inside one ATR-fraction tolerance of each other."""
    valid = [dict(item) for item in (candidates or []) if _coerce_float(item.get("price"))]
    if not valid:
        return []
    valid.sort(key=lambda item: float(item["price"]))
    clusters: list[list[dict]] = []
    current: list[dict] = []
    current_max: float | None = None
    for candidate in valid:
        price = float(candidate["price"])
        tolerance = _level_tolerance(atr20, price, tol_frac=tol_frac)
        if current and current_max is not None and price > current_max + tolerance:
            clusters.append(current)
            current = []
            current_max = None
        current.append(candidate)
        current_max = price if current_max is None else max(current_max, price)
    if current:
        clusters.append(current)
    return [_cluster_from_members(members, atr20) for members in clusters]


def recompute_touch_stats(
    levels: list[dict] | None,
    frame: pd.DataFrame | None,
    atr20: float | None,
    *,
    tol_frac: float = LEVEL_TOL_ATR_FRACTION,
    break_atr: float = LEVEL_BREAK_ATR,
    forward_bars: int = LEVEL_FORWARD_BARS,
) -> list[dict]:
    """Touches, respects, breaks and post-break returns, from the level's birth on.

    A bar counts as a touch when its range intersects the tolerance band; it is
    a *break* when the close finishes beyond the break distance, otherwise a
    *respect*. Bars at or before `first_seen` never count — a level cannot be
    tested by the day that created it.
    """
    work = _normalize_frame(frame)
    if not levels:
        return []
    if work.empty:
        return [dict(level) for level in levels]
    break_tolerance = _level_tolerance(atr20, tol_frac=float(break_atr))
    stamps = [_date_text(value) for value in work["datetime"]]
    keys = [_date_key(value) for value in stamps]
    highs = work["high"].tolist()
    lows = work["low"].tolist()
    closes = work["close"].tolist()
    bar_count = len(work)
    step = max(1, int(forward_bars))
    output: list[dict] = []
    for raw in levels:
        level = dict(raw)
        price = _coerce_float(level.get("price"))
        if price is None:
            continue
        first_seen = _date_key(level.get("first_seen"))
        tolerance = _level_tolerance(atr20, price, tol_frac=tol_frac)
        upper = price + tolerance
        lower = price - tolerance
        break_up = price + (break_tolerance or tolerance)
        break_down = price - (break_tolerance or tolerance)
        touches = respects = breaks = 0
        post_break: list[float] = []
        last_touch = last_break = ""
        for index in range(bar_count):
            if first_seen is not None and keys[index] is not None and keys[index] <= first_seen:
                continue
            if not (lows[index] <= upper and highs[index] >= lower):
                continue
            touches += 1
            last_touch = stamps[index]
            close = closes[index]
            if close > break_up or close < break_down:
                breaks += 1
                last_break = stamps[index]
                future = closes[min(bar_count - 1, index + step)]
                if close:
                    post_break.append((future - close) / close * 100.0)
            else:
                respects += 1
        level.update(
            {
                "touch_count": touches,
                "respect_count": respects,
                "break_count": breaks,
                "last_touch": last_touch,
                "last_break": last_break,
                "avg_post_break_return_pct": (
                    sum(post_break) / len(post_break) if post_break else None
                ),
                "atr20_at_update": _coerce_float(atr20),
            }
        )
        level["strength"] = _level_strength(level)
        level["conviction"] = level_conviction(level)
        output.append(level)
    output.sort(
        key=lambda item: (float(item.get("price") or 0.0), str(item.get("first_seen") or ""))
    )
    return output


def build_level_store(
    frame: pd.DataFrame | None,
    *,
    atr20: float | None,
    round_steps: Sequence[float] = (),
    anchor_dates: Iterable[str] = (),
    green: float = HV_RELVOL_GREEN,
    red: float = HV_RELVOL_RED,
    vol_sma: int = HV_VOL_SMA,
) -> dict:
    """The whole level pipeline: extract -> cluster -> measure -> score."""
    candidates: list[dict] = []
    candidates.extend(
        extract_hv_levels(
            frame, atr20, green=green, red=red, vol_sma=vol_sma, anchor_dates=anchor_dates
        )
    )
    candidates.extend(find_relative_pivots(frame, atr20=atr20))
    candidates.extend(round_number_levels(frame, round_steps))
    clustered = cluster_levels(candidates, atr20)
    measured = recompute_touch_stats(clustered, frame, atr20)
    return {
        "levels": measured,
        "atr20": atr20,
        "candidate_count": len(candidates),
    }


def levels_near(
    store: dict | None,
    price: float | None,
    atr20: float | None,
    *,
    tol_frac: float = LEVEL_TOL_ATR_FRACTION,
    min_strength: float = 0.0,
    kinds: Sequence[str] | None = None,
) -> list[dict]:
    """Levels within tolerance of `price`, nearest first."""
    entry = _coerce_float(price)
    if entry is None or not isinstance(store, dict):
        return []
    wanted = {str(kind) for kind in kinds} if kinds else set()
    matches: list[dict] = []
    for level in store.get("levels", []) or []:
        if wanted and str(level.get("kind")) not in wanted:
            continue
        level_price = _coerce_float(level.get("price"))
        if level_price is None:
            continue
        if (_coerce_float(level.get("strength")) or 0.0) < float(min_strength):
            continue
        tolerance = _level_tolerance(atr20, level_price, tol_frac=tol_frac)
        distance = level_price - entry
        if abs(distance) <= tolerance:
            item = dict(level)
            item["distance"] = distance
            item["distance_atr"] = (distance / float(atr20)) if atr20 else None
            item["position"] = "above" if distance > 0 else "below" if distance < 0 else "at"
            matches.append(item)
    matches.sort(key=lambda item: (abs(item["distance"]), -float(item.get("strength") or 0.0)))
    return matches
