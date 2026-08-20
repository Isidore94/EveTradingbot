"""The destruction demand layer — plan.md §7 and §14.

Every ship lost in New Eden, with its fitting, is public. Destroyed hulls and
their fitted modules are demand that **must be re-bought** — item-level demand
telemetry equity systems simply do not have. That is this system's genuine
edge, which is exactly why it gets measured against a pass rule frozen before
the measurement (§14.3) rather than assumed.

Two sources, per the plan's verified corrections:

* **History** comes from EVE Ref's daily archives
  (`data.everef.net/killmails/YYYY/killmails-YYYY-MM-DD.tar.bz2`), not the
  zKillboard API — its `startTime`/`endTime` are deprecated and `pastSeconds`
  caps at 7 days.
* **Live** comes from **R2Z2** (`sequence.json` + per-sequence fetch).
  RedisQ sunset on 2026-05-31 and must not be built against.

Archives are reduced on ingest to `(type_id, region_id, date)` hull and
fitted-module loss counts. Raw killmails are not persisted; a day's archive is
tens of MB of JSON and nothing downstream needs it.
"""

from __future__ import annotations

import bz2
import io
import json
import math
import tarfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from .config import Config
from .paths import atomic_write_bytes
from .store.db import Database
from .timeutil import iso, utcnow

__all__ = [
    "DestructionIngest",
    "LeadLagResult",
    "backfill_archives",
    "destruction_z",
    "poll_r2z2",
    "reduce_killmails",
    "render_lead_lag",
    "run_lead_lag_study",
    "spearman",
]


@dataclass(slots=True)
class DestructionIngest:
    days: int = 0
    killmails: int = 0
    hull_rows: int = 0
    module_rows: int = 0
    skipped_days: list[str] = field(default_factory=list)
    unmapped_systems: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "days": self.days,
            "killmails": self.killmails,
            "hull_rows": self.hull_rows,
            "module_rows": self.module_rows,
            "skipped_days": self.skipped_days[:20],
            "unmapped_systems": self.unmapped_systems,
            "errors": self.errors[:20],
        }


def reduce_killmails(
    killmails, system_regions: dict[int, int]
) -> tuple[dict[tuple[int, int, str], list[int]], int]:
    """Reduce raw killmails to `(type_id, region_id, day) -> [hulls, modules]`.

    A destroyed hull counts once; each destroyed fitted module counts by its
    quantity. **Dropped** items are deliberately excluded: they survived and do
    not need re-buying, so counting them would inflate demand by roughly half.
    """
    counts: dict[tuple[int, int, str], list[int]] = {}
    unmapped = 0
    for killmail in killmails:
        time = str(killmail.get("killmail_time") or "")
        if not time:
            continue
        day = time[:10]
        system_id = killmail.get("solar_system_id")
        region_id = system_regions.get(int(system_id)) if system_id is not None else None
        if region_id is None:
            unmapped += 1
            continue
        victim = killmail.get("victim") or {}
        hull = victim.get("ship_type_id")
        if hull is not None:
            key = (int(hull), int(region_id), day)
            counts.setdefault(key, [0, 0])[0] += 1
        for item in victim.get("items") or []:
            destroyed = item.get("quantity_destroyed")
            type_id = item.get("item_type_id")
            if not destroyed or type_id is None:
                continue
            key = (int(type_id), int(region_id), day)
            counts.setdefault(key, [0, 0])[1] += int(destroyed)
    return counts, unmapped


def _persist(db: Database, counts: dict[tuple[int, int, str], list[int]]) -> tuple[int, int]:
    """Batch-write the reduction. A day is ~41k rows; per-row execute is too slow."""
    rows = [
        (type_id, region_id, day, hulls, modules)
        for (type_id, region_id, day), (hulls, modules) in counts.items()
    ]
    if not rows:
        return 0, 0
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO destruction(type_id, region_id, day, hull_losses, module_losses)"
            " VALUES(?,?,?,?,?) ON CONFLICT(type_id, region_id, day) DO UPDATE SET"
            " hull_losses=excluded.hull_losses, module_losses=excluded.module_losses",
            rows,
        )
    return sum(1 for row in rows if row[3]), sum(1 for row in rows if row[4])


def read_archive(payload: bytes) -> list[dict]:
    """Parse one EVE Ref daily archive (`.tar.bz2` of one JSON per killmail)."""
    killmails: list[dict] = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:bz2") as archive:
        for member in archive:
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                killmails.append(json.loads(handle.read()))
            except json.JSONDecodeError:
                continue
    return killmails


def backfill_archives(
    config: Config,
    db: Database,
    *,
    days: int | None = None,
    end: date | None = None,
    client: httpx.Client | None = None,
    cache_dir: Path | None = None,
    progress=None,
) -> DestructionIngest:
    """Backfill EVE Ref daily archives and reduce them on ingest.

    Days already ingested are skipped by the `killmail_ingest` ledger; a
    missing archive is recorded and stepped over, never retried into a wall.
    """
    result = DestructionIngest()
    span = days if days is not None else config.killmails.backfill_days
    last = end or (utcnow().date() - timedelta(days=1))
    system_regions = db.system_region_map()
    if not system_regions:
        result.errors.append("no solar-system map: run `sde` before backfilling killmails")
        return result
    owns = client is None
    client = client or httpx.Client(headers=config.headers, timeout=300.0, follow_redirects=True)
    cache = cache_dir or (config.paths.ensure().killmails / "archives")
    cache.mkdir(parents=True, exist_ok=True)
    try:
        for offset in range(span):
            day = last - timedelta(days=offset)
            key = day.isoformat()
            already = db.conn.execute(
                "SELECT killmail_count FROM killmail_ingest WHERE source=?", (key,)
            ).fetchone()
            if already is not None:
                continue
            url = f"{config.killmails.everef_base_url}/{day.year}/killmails-{key}.tar.bz2"
            cached = cache / f"killmails-{key}.tar.bz2"
            try:
                if cached.exists():
                    payload = cached.read_bytes()
                else:
                    response = client.get(url)
                    if response.status_code == 404:
                        result.skipped_days.append(key)
                        continue
                    response.raise_for_status()
                    payload = response.content
                    atomic_write_bytes(cached, payload)
                killmails = read_archive(payload)
            except (httpx.HTTPError, tarfile.TarError, OSError, EOFError) as exc:
                result.errors.append(f"{key}: {type(exc).__name__}: {exc}")
                continue
            counts, unmapped = reduce_killmails(killmails, system_regions)
            hulls, modules = _persist(db, counts)
            db.conn.execute(
                "INSERT INTO killmail_ingest(source, ingested_at, killmail_count)"
                " VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET"
                " ingested_at=excluded.ingested_at, killmail_count=excluded.killmail_count",
                (key, iso(utcnow()), len(killmails)),
            )
            # The raw archive is not kept: nothing downstream needs it.
            cached.unlink(missing_ok=True)
            result.days += 1
            result.killmails += len(killmails)
            result.hull_rows += hulls
            result.module_rows += modules
            result.unmapped_systems += unmapped
            if progress is not None:
                progress(offset + 1, span, result)
    finally:
        if owns:
            client.close()
        # A year of archives leaves a WAL bigger than most of the lake.
        db.checkpoint()
    return result


def poll_r2z2(
    config: Config,
    db: Database,
    *,
    client: httpx.Client | None = None,
    max_batches: int = 20,
) -> DestructionIngest:
    """Poll the R2Z2 ephemeral bucket for live killmails.

    RedisQ sunset 2026-05-31 and is not used. This is a *supplement* to the
    archives, never the source of truth: the archives are complete and can be
    backfilled at leisure, so a missed poll costs nothing.
    """
    result = DestructionIngest()
    system_regions = db.system_region_map()
    if not system_regions:
        result.errors.append("no solar-system map: run `sde` first")
        return result
    owns = client is None
    client = client or httpx.Client(headers=config.headers, timeout=60.0, follow_redirects=True)
    base = config.killmails.r2z2_base_url.rstrip("/")
    try:
        response = client.get(f"{base}/sequence.json")
        response.raise_for_status()
        payload = response.json()
        newest = int(payload.get("sequence") or payload.get("latest") or 0)
        if newest <= 0:
            result.errors.append(f"R2Z2 sequence.json carried no sequence: {payload!r}")
            return result
        stored = db.get_meta("r2z2_sequence")
        cursor = int(stored) + 1 if stored else max(1, newest - max_batches + 1)
        collected: list[dict] = []
        for sequence in range(cursor, newest + 1):
            batch = client.get(f"{base}/{sequence}.json")
            if batch.status_code == 404:
                continue
            batch.raise_for_status()
            body = batch.json()
            collected.extend(body if isinstance(body, list) else [body])
        counts, unmapped = reduce_killmails(collected, system_regions)
        hulls, modules = _persist(db, counts)
        db.set_meta("r2z2_sequence", str(newest))
        result.killmails = len(collected)
        result.hull_rows = hulls
        result.module_rows = modules
        result.unmapped_systems = unmapped
    except (httpx.HTTPError, ValueError) as exc:
        result.errors.append(f"R2Z2: {type(exc).__name__}: {exc}")
    finally:
        if owns:
            client.close()
    return result


def destruction_frame(
    db: Database,
    *,
    region_ids: list[int] | None = None,
    type_ids: list[int] | None = None,
) -> pd.DataFrame:
    """Losses per `(type_id, day)`, aggregated across regions in SQL.

    Regions are pooled by default because replacement demand lands at the trade
    hub, not at the wreck. Filtering by `type_ids` matters at scale: a year of
    archives is ~15M rows, and only the types in the bar lake can ever join.
    """
    clauses: list[str] = []
    params: list[int] = []
    if region_ids:
        clauses.append(f"region_id IN ({','.join('?' * len(region_ids))})")
        params.extend(int(value) for value in region_ids)
    if type_ids:
        clauses.append(f"type_id IN ({','.join('?' * len(type_ids))})")
        params.extend(int(value) for value in type_ids)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    query = (
        "SELECT type_id, day, SUM(hull_losses) AS hull_losses,"
        " SUM(module_losses) AS module_losses FROM destruction"
        f"{where} GROUP BY type_id, day"
    )
    frame = pd.DataFrame(
        [dict(row) for row in db.conn.execute(query, params)],
        columns=["type_id", "day", "hull_losses", "module_losses"],
    )
    if frame.empty:
        return frame
    frame["day"] = pd.to_datetime(frame["day"], utc=True)
    return frame


def destruction_z(
    frame: pd.DataFrame, *, recent_days: int = 7, baseline_days: int = 90
) -> pd.DataFrame:
    """`destruction_z` = trailing-7d destroyed units vs their 90-day baseline.

    Returned per `(type_id, day)`, pooled across regions: replacement demand
    lands at the trade hub, not at the wreck.
    """
    if frame.empty:
        return pd.DataFrame(columns=["type_id", "day", "destroyed", "destruction_z"])
    daily = (
        frame.assign(destroyed=frame["hull_losses"] + frame["module_losses"])
        .groupby(["type_id", "day"], as_index=False)["destroyed"]
        .sum()
    )
    # Regions are already pooled by `destruction_frame`; this groupby only
    # collapses a caller-supplied frame that still carries them.
    pivot = daily.pivot(index="day", columns="type_id", values="destroyed")
    calendar = pd.date_range(pivot.index.min(), pivot.index.max(), freq="D", tz="UTC")
    pivot = pivot.reindex(calendar).fillna(0.0)
    recent = pivot.rolling(recent_days, min_periods=recent_days).sum()
    baseline_mean = recent.rolling(baseline_days, min_periods=baseline_days // 2).mean()
    baseline_std = recent.rolling(baseline_days, min_periods=baseline_days // 2).std()
    with np.errstate(invalid="ignore", divide="ignore"):
        scores = (recent - baseline_mean) / baseline_std
    scores = scores.replace([np.inf, -np.inf], np.nan)
    out = (
        scores.stack(future_stack=True)
        .rename("destruction_z")
        .reset_index()
        .rename(columns={"level_0": "day", "level_1": "type_id"})
    )
    destroyed = (
        recent.stack(future_stack=True)
        .rename("destroyed")
        .reset_index()
        .rename(columns={"level_0": "day", "level_1": "type_id"})
    )
    merged = out.merge(destroyed, on=["day", "type_id"], how="left")
    return merged.dropna(subset=["destruction_z"]).reset_index(drop=True)


def spearman(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None, int]:
    """Spearman rho with the large-sample normal approximation for p.

    `z = rho * sqrt(n - 1)`; `p` two-sided from the normal CDF. Stated openly
    rather than hidden inside a library call — the dependency set is locked at
    four runtime packages (plan.md §11 D1), so there is no scipy here and the
    approximation is part of the method (§14.2).
    """
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    n = int(x.size)
    if n < 10:
        return None, None, n
    rank_x = pd.Series(x).rank().to_numpy()
    rank_y = pd.Series(y).rank().to_numpy()
    if rank_x.std() == 0 or rank_y.std() == 0:
        return None, None, n
    rho = float(np.corrcoef(rank_x, rank_y)[0, 1])
    if not np.isfinite(rho):
        return None, None, n
    z = rho * math.sqrt(n - 1)
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return rho, p, n


@dataclass(slots=True)
class LeadLagResult:
    generated_at: str
    observations: int = 0
    types: int = 0
    sample_start: str | None = None
    sample_end: str | None = None
    lags: list[dict] = field(default_factory=list)
    placebo: list[dict] = field(default_factory=list)
    outcome: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "observations": self.observations,
            "types": self.types,
            "sample_start": self.sample_start,
            "sample_end": self.sample_end,
            "lags": self.lags,
            "placebo": self.placebo,
            "outcome": self.outcome,
            "notes": self.notes,
        }


PASS_RULE = (
    "SURVIVES iff some lag k in 1..5 shows Spearman rho >= 0.10 with p < 0.01 on "
    "n >= 500 observations, the sign of rho at that lag is the same in both halves "
    "of the sample period, and a within-day shuffled placebo yields |rho| less than "
    "half the measured rho (plan.md §14.3, frozen 2026-08-20 before measurement)"
)
MIN_RHO = 0.10
MAX_P = 0.01
MIN_OBSERVATIONS = 500
PLACEBO_RATIO = 0.5


def run_lead_lag_study(
    config: Config,
    bars: pd.DataFrame,
    destruction: pd.DataFrame,
    *,
    max_lag: int | None = None,
    seed: int = 20260820,
) -> LeadLagResult:
    """Test H2 against the frozen §14.3 rule. No interpretation, no retrofit."""
    result = LeadLagResult(generated_at=iso(utcnow()))
    lag_cap = max_lag or config.killmails.lead_lag_max_lag_days
    if bars.empty or destruction.empty:
        result.notes.append(
            "no bars or no destruction data; the study reports UNKNOWN rather than "
            "a null result — an unmeasured effect is not a measured absence"
        )
        result.outcome = {"rule": PASS_RULE, "outcome": "UNKNOWN", "reason": "no data"}
        return result

    frame = bars.copy()
    frame["day"] = pd.to_datetime(frame["datetime"], utc=True).dt.normalize()
    frame = frame.sort_values(["type_id", "day"])
    window = config.screen.participation_window
    grouped = frame.groupby("type_id")
    frame["participation"] = grouped["order_count"].transform(
        lambda series: series / series.shift(1).rolling(window, min_periods=window // 2).mean()
    )
    for lag in range(1, lag_cap + 1):
        frame[f"participation_lead_{lag}"] = grouped["participation"].shift(-lag)
        frame[f"return_lead_{lag}"] = grouped["close"].shift(-lag) / frame["close"] - 1.0

    joined = frame.merge(destruction, on=["type_id", "day"], how="inner")
    joined = joined[np.isfinite(joined["destruction_z"])]
    result.observations = int(len(joined))
    result.types = int(joined["type_id"].nunique()) if not joined.empty else 0
    if joined.empty:
        result.notes.append("no overlapping (type, day) rows between the lake and destruction data")
        result.outcome = {"rule": PASS_RULE, "outcome": "UNKNOWN", "reason": "no overlap"}
        return result
    result.sample_start = str(joined["day"].min().date())
    result.sample_end = str(joined["day"].max().date())

    ordered = joined.sort_values("day")
    midpoint = ordered["day"].quantile(0.5)
    first_half = ordered[ordered["day"] <= midpoint]
    second_half = ordered[ordered["day"] > midpoint]
    rng = np.random.default_rng(seed)

    for lag in range(1, lag_cap + 1):
        for target, label in (
            (f"participation_lead_{lag}", "participation"),
            (f"return_lead_{lag}", "forward_return"),
        ):
            rho, p, n = spearman(
                ordered["destruction_z"].to_numpy(dtype="float64"),
                ordered[target].to_numpy(dtype="float64"),
            )
            first_rho, _, first_n = spearman(
                first_half["destruction_z"].to_numpy(dtype="float64"),
                first_half[target].to_numpy(dtype="float64"),
            )
            second_rho, _, second_n = spearman(
                second_half["destruction_z"].to_numpy(dtype="float64"),
                second_half[target].to_numpy(dtype="float64"),
            )
            result.lags.append(
                {
                    "lag_days": lag,
                    "target": label,
                    "rho": rho,
                    "p_value": p,
                    "observations": n,
                    "first_half_rho": first_rho,
                    "first_half_n": first_n,
                    "second_half_rho": second_rho,
                    "second_half_n": second_n,
                }
            )
            # Placebo: shuffle destruction_z ACROSS TYPES WITHIN EACH DAY, so
            # the daily marginal distribution is preserved and only the
            # type-level pairing is destroyed.
            shuffled = ordered.copy()
            shuffled["destruction_z"] = shuffled.groupby("day")["destruction_z"].transform(
                lambda series: rng.permutation(series.to_numpy())
            )
            placebo_rho, placebo_p, placebo_n = spearman(
                shuffled["destruction_z"].to_numpy(dtype="float64"),
                shuffled[target].to_numpy(dtype="float64"),
            )
            result.placebo.append(
                {
                    "lag_days": lag,
                    "target": label,
                    "rho": placebo_rho,
                    "p_value": placebo_p,
                    "observations": placebo_n,
                }
            )
    result.outcome = evaluate_lead_lag(result)
    return result


def evaluate_lead_lag(result: LeadLagResult) -> dict:
    """Apply the FROZEN §14.3 pass rule literally."""
    placebos = {(row["lag_days"], row["target"]): row for row in result.placebo}
    survivors: list[dict] = []
    for row in result.lags:
        rho = row.get("rho")
        p = row.get("p_value")
        if rho is None or p is None:
            continue
        if rho < MIN_RHO or p >= MAX_P or row["observations"] < MIN_OBSERVATIONS:
            continue
        first = row.get("first_half_rho")
        second = row.get("second_half_rho")
        if first is None or second is None or (first > 0) != (second > 0):
            continue
        placebo = placebos.get((row["lag_days"], row["target"]))
        placebo_rho = abs(placebo["rho"]) if placebo and placebo.get("rho") is not None else None
        if placebo_rho is None or placebo_rho >= abs(rho) * PLACEBO_RATIO:
            continue
        survivors.append(row)
    if survivors:
        best = max(survivors, key=lambda row: row["rho"])
        return {
            "rule": PASS_RULE,
            "outcome": "SURVIVES",
            "reason": (
                f"lag {best['lag_days']}d vs {best['target']}: rho={best['rho']:.4f}, "
                f"p={best['p_value']:.2e}, n={best['observations']}, sign consistent "
                "across halves, placebo below half the measured rho"
            ),
            "consequence": (
                "destruction features MAY influence ranking after a shadow period — "
                "never straight in (plan.md §8 Phase 5)"
            ),
            "best_lag": best,
        }
    measurable = [row for row in result.lags if row.get("rho") is not None]
    if not measurable:
        return {
            "rule": PASS_RULE,
            "outcome": "UNKNOWN",
            "reason": "no lag could be measured at all",
        }
    strongest = max(measurable, key=lambda row: row["rho"] if row["rho"] is not None else -9)
    return {
        "rule": PASS_RULE,
        "outcome": "DOES NOT SURVIVE",
        "reason": (
            f"strongest lag was {strongest['lag_days']}d vs {strongest['target']} at "
            f"rho={strongest['rho']:.4f} (p={strongest['p_value']:.2e}, "
            f"n={strongest['observations']}); the frozen rule requires rho >= {MIN_RHO} "
            f"with p < {MAX_P} on n >= {MIN_OBSERVATIONS}, sign-consistent across halves, "
            "with a placebo below half the measured rho"
        ),
        "consequence": (
            "destruction ships as digest ANNOTATIONS ONLY, and the annotation says the "
            "lead-lag claim was tested and not supported"
        ),
        "strongest_lag": strongest,
    }


def render_lead_lag(result: LeadLagResult) -> str:
    lines = [
        "# Destruction lead-lag study",
        "",
        f"Generated {result.generated_at}.",
        "",
        "**Hypothesis (frozen in plan.md §14.1 before this study ran):** "
        "`destruction_z` leads `order_count`/`volume` upticks and price firming in "
        "doctrine-class hulls and their fitted modules by 1–5 days.",
        "",
        f"- Observations: **{result.observations:,}** across **{result.types:,}** types",
        f"- Sample period: {result.sample_start} → {result.sample_end}",
        "",
        "## Outcome",
        "",
        f"**{result.outcome.get('outcome', 'UNKNOWN')}** — {result.outcome.get('reason', '')}",
        "",
        f"{result.outcome.get('consequence', '')}",
        "",
        f"> Rule: {result.outcome.get('rule', PASS_RULE)}",
    ]
    if result.lags:
        lines.extend(
            [
                "",
                "## Measured correlations",
                "",
                "| lag | target | rho | p | n | 1st half rho | 2nd half rho | placebo rho |",
                "|---:|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        placebos = {(row["lag_days"], row["target"]): row for row in result.placebo}
        for row in result.lags:
            placebo = placebos.get((row["lag_days"], row["target"])) or {}

            def fmt(value, digits=4):
                return "UNKNOWN" if value is None else f"{value:.{digits}f}"

            p_text = "UNKNOWN" if row["p_value"] is None else f"{row['p_value']:.2e}"
            lines.append(
                f"| {row['lag_days']}d | {row['target']} | {fmt(row['rho'])} | {p_text} "
                f"| {row['observations']:,} | {fmt(row['first_half_rho'])} "
                f"| {fmt(row['second_half_rho'])} | {fmt(placebo.get('rho'))} |"
            )
    if result.notes:
        lines.extend(["", "## Notes"])
        for note in result.notes:
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def decompress_archive(path: Path) -> bytes:
    """Read a `.bz2` payload from disk. Used by fixture tooling only."""
    return bz2.decompress(path.read_bytes())
