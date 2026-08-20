"""The index layer — FORGE, FORGE-EW and the sectors (plan.md §19 Part 1).

Three things this module is careful about, because each is a way a self-built
index quietly lies:

1. **Weighting is ISK turnover, membership is unit volume.** "Weighted by
   daily volume" would mean raw units, and raw units make the index ~100%
   Tritanium — it trades ~5 billion units a day at ~4 ISK. Turnover (units ×
   price) is the only common denominator across items whose unit prices span
   twelve orders of magnitude. The *unit* floor decides who is in; turnover
   decides how much they count (§11 D3, amended).
2. **FORGE-EW inherits FORGE's membership exactly.** If the two indices could
   drift apart in *who* they hold, `FORGE-EW − FORGE` would measure
   composition rather than breadth, and the breadth read is the entire reason
   the equal-weight twin exists.
3. **A thin sector is UNKNOWN, never merged.** Folding a 3-member sector into
   its neighbour would produce a number with no honest label; the sector says
   how many members it found and why that was not enough.

Everything is chain-linked through the one engine in `signals/composite.py`,
so composition churn can never print as an index move.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import Config
from .signals.composite import EQUAL, TURNOVER, Composite, build_composite
from .store.db import Database

__all__ = [
    "FORGE",
    "FORGE_EW",
    "IndexSet",
    "Sector",
    "build_index_set",
    "load_sectors",
    "rotation_table",
    "sector_for_type",
    "sector_members",
]

FORGE = "FORGE"
FORGE_EW = "FORGE-EW"
SECTORS_FILE = "sectors.jsonl"


class SectorConfigError(RuntimeError):
    """A malformed sectors.jsonl fails loudly; it is never partially loaded."""


@dataclass(frozen=True, slots=True)
class Sector:
    ticker: str
    name: str
    roots: tuple[int, ...]
    min_members: int = 8
    min_unit_volume: float | None = None

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "roots": list(self.roots),
            "min_members": self.min_members,
            "min_unit_volume": self.min_unit_volume,
        }


def load_sectors(path: Path | None = None) -> list[Sector]:
    """Read the committed sector map. A malformed row names itself and stops."""
    path = path or (Path.cwd() / "config" / SECTORS_FILE)
    if not path.exists():
        return []
    sectors: list[Sector] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SectorConfigError(f"{path}:{number}: not valid JSON — {exc}") from exc
        missing = [key for key in ("ticker", "name", "roots") if key not in record]
        if missing:
            raise SectorConfigError(f"{path}:{number}: missing {', '.join(missing)}")
        ticker = str(record["ticker"]).strip().upper()
        if not ticker:
            raise SectorConfigError(f"{path}:{number}: empty ticker")
        if ticker in {FORGE, FORGE_EW}:
            raise SectorConfigError(
                f"{path}:{number}: ticker {ticker!r} is reserved for the market index"
            )
        if ticker in seen:
            raise SectorConfigError(f"{path}:{number}: duplicate ticker {ticker!r}")
        roots = record["roots"]
        if not isinstance(roots, list) or not roots:
            raise SectorConfigError(f"{path}:{number}: 'roots' must be a non-empty list")
        try:
            root_ids = tuple(int(value) for value in roots)
        except (TypeError, ValueError) as exc:
            raise SectorConfigError(
                f"{path}:{number}: 'roots' must be marketGroup ids — {exc}"
            ) from exc
        seen.add(ticker)
        sectors.append(
            Sector(
                ticker=ticker,
                name=str(record["name"]),
                roots=root_ids,
                min_members=int(record.get("min_members", 8)),
                min_unit_volume=(
                    float(record["min_unit_volume"])
                    if record.get("min_unit_volume") is not None
                    else None
                ),
            )
        )
    return sectors


def sector_members(db: Database, sector: Sector, type_ids) -> list[int]:
    """Which of `type_ids` sit under any of the sector's subtree roots."""
    roots = set(sector.roots)
    members: list[int] = []
    for type_id in type_ids:
        row = db.type_by_id(int(type_id))
        if row is None or row["market_group_id"] is None:
            continue
        chain = db.market_group_chain(int(row["market_group_id"]))
        if roots.intersection(chain):
            members.append(int(type_id))
    return members


@dataclass(slots=True)
class IndexSet:
    """FORGE, its equal-weight twin, and every sector that had enough members."""

    forge: Composite
    forge_ew: Composite
    sectors: dict[str, Composite] = field(default_factory=dict)
    sector_meta: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.forge.known

    def breadth(self) -> pd.Series:
        """`FORGE-EW − FORGE` in percent — the breadth read.

        Positive means the average member is outrunning the turnover-weighted
        market: broad participation. Negative means a few large names are
        carrying it. Empty when either index is UNKNOWN — never a zero.
        """
        if not self.forge.known or not self.forge_ew.known:
            return pd.Series(dtype="float64")
        wide = self.forge.series
        even = self.forge_ew.series
        common = wide.index.intersection(even.index)
        if common.empty:
            return pd.Series(dtype="float64")
        return (even.loc[common] / even.loc[common].iloc[0]) * 100.0 - (
            wide.loc[common] / wide.loc[common].iloc[0]
        ) * 100.0

    def as_dict(self) -> dict:
        return {
            "forge": self.forge.diagnostics,
            "forge_ew": self.forge_ew.diagnostics,
            "sectors": {
                ticker: composite.diagnostics for ticker, composite in self.sectors.items()
            },
            "sector_meta": self.sector_meta,
            "notes": self.notes,
        }


def sector_for_type(db: Database, sectors: list[Sector], type_id: int) -> Sector | None:
    """The sector a type belongs to, or None.

    None is UNKNOWN and the caller must treat it as such: an unresolvable
    scope never silently falls back to the market index (§6 — the upstream
    fallback-to-"SPY" bug is not ported here either).
    """
    row = db.type_by_id(int(type_id))
    if row is None or row["market_group_id"] is None:
        return None
    chain = set(db.market_group_chain(int(row["market_group_id"])))
    for sector in sectors:
        if chain.intersection(sector.roots):
            return sector
    return None


def _change_pct(series: pd.Series, bars: int) -> float | None:
    if series.empty or len(series) <= bars:
        return None
    latest = float(series.iloc[-1])
    earlier = float(series.iloc[-1 - bars])
    if earlier <= 0:
        return None
    return (latest / earlier - 1.0) * 100.0


def rotation_table(
    index_set: IndexSet,
    *,
    breadth_by_sector: dict[str, float] | None = None,
    rrs_length: int = 20,
) -> list[dict]:
    """Each sector's RRS vs FORGE, its 1/5/20-day change, and its breadth.

    A sector that could not be built appears with `status: UNKNOWN` and its
    reason rather than being dropped — the operator needs to know a sector
    exists and could not be measured, which is different from it not existing.
    """
    from .signals.rrs import real_relative_strength

    rows: list[dict] = []
    for ticker, meta in index_set.sector_meta.items():
        composite = index_set.sectors.get(ticker)
        if composite is None or not composite.known:
            rows.append(
                {
                    "ticker": ticker,
                    "name": meta.get("name", ticker),
                    "status": "UNKNOWN",
                    "reason": meta.get("reason", "not built"),
                    "members": meta.get("candidate_members"),
                    "rrs": None,
                    "change_1d": None,
                    "change_5d": None,
                    "change_20d": None,
                    "breadth": None,
                }
            )
            continue
        series = composite.series
        strength = real_relative_strength(
            composite.frame, index_set.forge.frame, length=rrs_length, scope=FORGE
        )
        rows.append(
            {
                "ticker": ticker,
                "name": meta.get("name", ticker),
                "status": "OK",
                "members": composite.diagnostics.get("members"),
                "min_unit_volume": meta.get("min_unit_volume"),
                "rrs": strength.rrs,
                "rrs_unknown_reason": strength.unknown_reason,
                "change_1d": _change_pct(series, 1),
                "change_5d": _change_pct(series, 5),
                "change_20d": _change_pct(series, 20),
                "breadth": (breadth_by_sector or {}).get(ticker),
                "top_weight": composite.diagnostics.get("top_weight"),
                "weight_entropy": composite.diagnostics.get("weight_entropy"),
            }
        )
    # Strongest first; a sector that could not be built sorts below one that
    # was built but could not be scored, and both sit under every measured
    # row — blanks at the bottom whichever way the table is read (§18.1).
    rows.sort(
        key=lambda row: (
            row["status"] == "UNKNOWN",
            row["rrs"] is None,
            -(row["rrs"] or 0.0),
        )
    )
    return rows


def build_index_set(
    config: Config,
    db: Database,
    bars: pd.DataFrame,
    *,
    member_ids=None,
    sectors: list[Sector] | None = None,
    sectors_path: Path | None = None,
) -> IndexSet:
    """Build FORGE, FORGE-EW and every sector index from one lake read.

    `member_ids` is the eligible universe — the types that cleared the unit
    floor (§11 D3). Passing it explicitly keeps the index honest about the
    difference between "tradeable" and "index member".
    """
    signals = config.signals
    notes: list[str] = []
    eligible = None if member_ids is None else [int(value) for value in member_ids]

    forge = build_composite(
        bars,
        members=signals.composite_members,
        single_cap=signals.composite_single_weight_cap,
        rebalance_days=signals.composite_rebalance_days,
        weighting=TURNOVER,
        member_ids=eligible,
        ticker=FORGE,
        name="Forge Composite",
    )
    # FORGE-EW must hold exactly what FORGE holds; anything else measures
    # composition instead of breadth.
    forge_ew = build_composite(
        bars,
        members=signals.composite_members,
        single_cap=1.0,
        rebalance_days=signals.composite_rebalance_days,
        weighting=EQUAL,
        member_ids=forge.member_ids or eligible,
        ticker=FORGE_EW,
        name="Forge Composite, equal weight",
    )
    if forge.known and not forge_ew.known:
        notes.append("FORGE-EW could not be built from FORGE's membership; breadth is UNKNOWN")

    definitions = sectors if sectors is not None else load_sectors(sectors_path)
    built: dict[str, Composite] = {}
    meta: dict[str, dict] = {}
    pool = eligible if eligible is not None else sorted(bars["type_id"].unique().tolist())
    for sector in definitions:
        members = sector_members(db, sector, pool)
        meta[sector.ticker] = {
            **sector.as_dict(),
            "candidate_members": len(members),
        }
        if len(members) < sector.min_members:
            meta[sector.ticker]["status"] = "UNKNOWN"
            meta[sector.ticker]["reason"] = (
                f"{len(members)} member(s) cleared the floor, below the sector's "
                f"minimum of {sector.min_members}; a thin sector renders UNKNOWN "
                "rather than being merged into a neighbour"
            )
            continue
        composite = build_composite(
            bars,
            members=signals.composite_members,
            single_cap=signals.composite_single_weight_cap,
            rebalance_days=signals.composite_rebalance_days,
            min_members=sector.min_members,
            weighting=TURNOVER,
            member_ids=members,
            ticker=sector.ticker,
            name=sector.name,
        )
        meta[sector.ticker]["status"] = "OK" if composite.known else "UNKNOWN"
        if not composite.known:
            meta[sector.ticker]["reason"] = composite.diagnostics.get("reason", "no series")
            continue
        built[sector.ticker] = composite
    return IndexSet(forge=forge, forge_ew=forge_ew, sectors=built, sector_meta=meta, notes=notes)
