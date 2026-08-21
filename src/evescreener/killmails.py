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
    #: Which population this run measured. Declared on the result so a pooled
    #: exploratory run can never be read later as evidence about H2 (§21 R5).
    cohort: str = "pooled_all_types"
    #: Clusters rather than rows — see `independent_observations`.
    independent_observations: int = 0
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
            "independent_observations": self.independent_observations,
            "types": self.types,
            "cohort": self.cohort,
            "cohort_declaration": cohort_declaration(self.cohort),
            "h2": h2_statement(self),
            "multiple_comparisons": {
                "tests": LEAD_LAG_TESTS,
                "frozen_alpha": MAX_P,
                "family_wise_alpha": FAMILY_ALPHA,
                "correction": "bonferroni",
            },
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


# -- R5: hypothesis fidelity (plan.md §21 R5) ------------------------------

COHORT_POOLED = "pooled_all_types"
COHORT_DOCTRINE = "doctrine_cohort"

#: Five lags x two targets. Declared before the run so it cannot be chosen
#: after seeing which cell looked best.
LEAD_LAG_TESTS = 10
#: Bonferroni over that family, against the frozen §14.3 alpha of 0.01.
FAMILY_ALPHA = 0.01 / LEAD_LAG_TESTS


def cohort_declaration(cohort: str) -> dict:
    """What population a run measured, and what class of evidence it is.

    H2 (§14.1) named **doctrine-class hulls and their fitted modules**, with
    losses **bucketed by region catchment**. The original run pooled global
    destruction against every type in the lake. That is a different population,
    and pooling unrelated catalogue types can dilute a real effect as easily as
    manufacture one — so the pooled number is **exploratory** and is not
    evidence about H2 either way.

    Declaring this on the result, rather than in prose beside it, is what stops
    the two runs being read as the same measurement later.
    """
    if cohort == COHORT_DOCTRINE:
        return {
            "cohort": COHORT_DOCTRINE,
            "evidence_class": "confirmatory",
            "definition": (
                "doctrine-class hulls and their fitted modules, per H2: types whose "
                "SDE market-group ancestry is Ships or Ship Equipment, restricted to "
                "the tracked universe"
            ),
            "catchment": "forge_adjacent",
            "caveat": (
                "the cohort and catchment are declared BEFORE remeasurement; a run "
                "whose membership was chosen after seeing results is not confirmatory"
            ),
        }
    return {
        "cohort": COHORT_POOLED,
        "evidence_class": "exploratory",
        "definition": "every type in the lake with both bars and destruction rows",
        "catchment": "global",
        "caveat": (
            "this is NOT the H2 cohort: H2 named doctrine-class hulls and their "
            "fitted modules with a regional catchment. Pooling the whole catalogue "
            "answers a different question and is exploratory only"
        ),
    }


def exact_lag_frame(frame: pd.DataFrame, lag: int) -> pd.DataFrame:
    """Attach `day + lag` values by an exact calendar join (§21 R5).

    `groupby.shift(-lag)` takes the next *observed row*. On a lake where a
    thin type trades on the 1st and again on the 10th, that labels the 10th a
    one-day lead — a nine-day move counted as a one-day effect. Joining on the
    literal date makes the gap what it is: **absent**, and therefore UNKNOWN,
    rather than quietly filled by whatever came next.
    """
    if frame is None or frame.empty:
        return frame
    lead = frame.copy()
    lead["day"] = lead["day"] - pd.Timedelta(days=int(lag))
    keep = [column for column in ("close", "participation") if column in lead.columns]
    lead = lead[["type_id", "day", *keep]].rename(
        columns={column: f"{column}_lead" for column in keep}
    )
    return frame.merge(lead, on=["type_id", "day"], how="left")


def independent_observations(frame: pd.DataFrame) -> int:
    """Clusters, not rows (§21 R5).

    Daily observations on one type are serially dependent, and observations
    across types on one day are cross-sectionally dependent through the market
    itself. Neither is modelled. Counting **types** is the conservative floor:
    it is certainly not more independent than that, and a p-value computed on
    row count treats one type's year as 365 facts.
    """
    if frame is None or frame.empty or "type_id" not in frame.columns:
        return 0
    return int(frame["type_id"].nunique())


def adjusted_verdict(row: dict) -> dict:
    """Both verdicts, side by side: the frozen rule, and the family-wise one.

    §14.3 is frozen and judged each test at p < 0.01. Ten tests were run, so
    at least one crossing 0.01 by chance is likely. The frozen verdict is
    reported unchanged — it is not retrofitted — and the Bonferroni verdict is
    reported beside it so a reader can see which claims survive both.
    """
    p_value = row.get("p_value")
    permutation = row.get("p_value_permutation")
    if p_value is None:
        return {
            "p_value_frozen_rule": None,
            "p_value_family_wise": None,
            "p_value_assumes_independence": None,
        }
    # The frozen §14.3 rule is applied to the naive p-value exactly as it always
    # was — it is frozen, and is not retrofitted. The family-wise verdict uses
    # the CLUSTER-AWARE p-value when one exists, because Bonferroni over
    # p-values that already assume independence corrects the wrong error.
    family_source = permutation if permutation is not None else p_value
    return {
        "p_value_frozen_rule": bool(p_value < MAX_P),
        "p_value_family_wise": bool(family_source < FAMILY_ALPHA),
        "p_value_assumes_independence": True,
    }


H2_UNKNOWN = "H2 UNKNOWN — confirmatory run absent"

#: Permutations for the cluster-aware p-value. Modest by default because the
#: study runs over ~470k rows and ten tests; raise it when a claim depends on
#: the tail of the distribution rather than on its shape.
DEFAULT_PERMUTATIONS = 199


def rotation_permutation_p(
    x,
    y,
    groups,
    *,
    observed_rho: float | None,
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = 20260820,
) -> float | None:
    """Cluster-aware p-value by circular rotation within each type (§22 S4).

    `spearman()`'s p-value comes from `z = rho * sqrt(n - 1)`, which assumes
    every one of the ~470,000 rows is an independent observation. They are not:
    a type's own days are serially dependent, and every type moves with the
    market on the same day. R5 measured that dependence into
    `independent_observations()` and then nothing read it — a decorative field
    described as a correction.

    Rotating each type's `x` series by a random offset destroys the *alignment*
    between destruction and returns while preserving each series' own
    autocorrelation **exactly**, which is what makes the resulting null
    distribution the right one to compare against. The empirical p-value is
    `(1 + #{|rho_perm| >= |rho_obs|}) / (1 + permutations)`, so it can never be
    zero — an empirical test cannot prove more than its own resolution.
    """
    if observed_rho is None:
        return None
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")
    groups = np.asarray(groups)
    usable = np.isfinite(x) & np.isfinite(y)
    if usable.sum() < 3:
        return None
    x, y, groups = x[usable], y[usable], groups[usable]

    order = np.argsort(groups, kind="stable")
    x, y, groups = x[order], y[order], groups[order]
    starts = np.flatnonzero(np.r_[True, groups[1:] != groups[:-1]])
    ends = np.r_[starts[1:], len(groups)]

    rng = np.random.default_rng(seed)
    hits = 0
    target = abs(float(observed_rho))
    for _ in range(int(permutations)):
        rotated = x.copy()
        for start, end in zip(starts, ends, strict=False):
            span = end - start
            if span > 1:
                rotated[start:end] = np.roll(x[start:end], int(rng.integers(1, span)))
        rho, _p, _n = spearman(rotated, y)
        if rho is not None and abs(rho) >= target:
            hits += 1
    return float((1 + hits) / (1 + int(permutations)))


def h2_statement(result) -> dict:
    """What may be said about **H2**, as opposed to about a pooled run.

    H2 (§14.1) is a claim about doctrine-class hulls and their fitted modules
    within a regional catchment. The only lead-lag run this repository has ever
    performed pooled the whole catalogue globally, so whatever it found is
    evidence about *that* population and not about H2 — in either direction.

    Every renderer previously printed "the lead-lag claim was tested and not
    supported", which asserts a test of H2 that has not happened. This returns
    the honest pair: **H2 UNKNOWN**, plus the exploratory finding beside it,
    labelled.
    """
    if result is None:
        return {
            "h2": H2_UNKNOWN,
            "h2_reason": "no lead-lag study has been run",
            "evidence_class": "none",
            "cohort": None,
            "exploratory_outcome": None,
        }
    cohort = getattr(result, "cohort", COHORT_POOLED)
    declaration = cohort_declaration(cohort)
    outcome = (getattr(result, "outcome", None) or {}).get("outcome")
    if declaration["evidence_class"] == "confirmatory":
        return {
            "h2": outcome or "UNKNOWN",
            "h2_reason": (getattr(result, "outcome", None) or {}).get("reason", ""),
            "evidence_class": "confirmatory",
            "cohort": cohort,
            "exploratory_outcome": None,
        }
    return {
        "h2": H2_UNKNOWN,
        "h2_reason": (
            "the only run performed pooled every catalogue type globally; H2 names "
            "doctrine-class hulls and their fitted modules within a regional "
            "catchment, and that confirmatory run absent"
        ),
        "evidence_class": "exploratory",
        "cohort": cohort,
        "exploratory_outcome": outcome,
    }


def run_lead_lag_study(
    config: Config,
    bars: pd.DataFrame,
    destruction: pd.DataFrame,
    *,
    max_lag: int | None = None,
    seed: int = 20260820,
    permutations: int = DEFAULT_PERMUTATIONS,
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
    # Exact calendar joins, not row shifts (§21 R5): a type trading on the 1st
    # and again on the 10th had the 10th labelled a one-day lead.
    for lag in range(1, lag_cap + 1):
        lagged = exact_lag_frame(frame[["type_id", "day", "close", "participation"]], lag)
        frame[f"participation_lead_{lag}"] = lagged["participation_lead"].to_numpy()
        frame[f"return_lead_{lag}"] = (
            lagged["close_lead"].to_numpy() / frame["close"].to_numpy() - 1.0
        )

    joined = frame.merge(destruction, on=["type_id", "day"], how="inner")
    joined = joined[np.isfinite(joined["destruction_z"])]
    result.observations = int(len(joined))
    result.independent_observations = independent_observations(joined)
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
            permutation_p = rotation_permutation_p(
                ordered["destruction_z"].to_numpy(dtype="float64"),
                ordered[target].to_numpy(dtype="float64"),
                ordered["type_id"].to_numpy(),
                observed_rho=rho,
                permutations=permutations,
                seed=seed,
            )
            result.lags.append(
                {
                    "lag_days": lag,
                    "target": label,
                    "rho": rho,
                    "p_value": p,
                    "p_value_permutation": permutation_p,
                    "observations": n,
                    "independent_observations": independent_observations(ordered),
                    **adjusted_verdict(
                        {"p_value": p, "p_value_permutation": permutation_p, "rho": rho}
                    ),
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
            "destruction ships as digest ANNOTATIONS ONLY. The annotation says only "
            "that this POOLED, EXPLORATORY run did not support the effect; H2 itself "
            "is UNKNOWN because its confirmatory cohort has never been measured "
            "(plan.md §14.4, §22 S4)"
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
