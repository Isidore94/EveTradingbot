"""The hauling engine: what to put in the hold, right now (plan.md §23).

Every other surface in this system answers "is this item mispriced?". This one
answers the question the operator actually asks when he undocks — *given where
I am, what I fly, what ISK I have and how long I have got, what should I carry
and how much of it?* — and that question has a different shape:

* it is decided at a **quantity**, not at a notional tier, because the right
  size is a breakpoint in two order books rather than a number chosen in
  advance;
* it is **personal**: pickup jumps, cargo, capital and session length are the
  binding constraints, and they are different for every pilot;
* and it is **spatial**: the route is a cost, and a route through low-sec is a
  different trade rather than a cheaper one.

Three rules keep it honest.

**Getting in is measured; getting out is assumed.** The source and destination
walks are arithmetic over depth that was actually swept. Everything about
*when* the goods sell is a labelled assumption (§23.7), and an assumption is
never quietly folded into a profit number.

**A rejected candidate keeps its reason.** The rejected set is queryable, not
discarded: "nothing cleared" with a denominator and a histogram of reasons is
an answer, and an empty table is not.

**Either region stale prices nothing.** A hauling row joins two independent
sweeps with two independent ages. Both generations are pinned on the row, the
older one decides staleness, and a stale pair produces an UNKNOWN row with its
reason rather than a row priced off whichever leg happened to be fresh.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

import pandas as pd

from .books import DepthCurve, DepthLevel, DepthSnapshot, q_walk
from .config import Config
from .costs import CostModel
from .routes import HIGHSEC, PROFILES, RouteFacts, RouteGraph
from .timeutil import ensure_utc, iso, utcnow

__all__ = [
    "MODES",
    "OBJECTIVES",
    "REJECTIONS",
    "HaulPlan",
    "HaulProfile",
    "HaulScan",
    "Rejection",
    "ShipProfile",
    "Station",
    "curves_from_depth",
    "scan_hauls",
    "stations_from_db",
]

# -- the rejection vocabulary (plan.md §23.13) ------------------------------
STALE_BOOK = "STALE_BOOK"
DEPTH_TRUNCATED = "DEPTH_TRUNCATED"
DEST_DEPTH_SHORT = "DEST_DEPTH_SHORT"
MIN_VOLUME_BLOCKED = "MIN_VOLUME_BLOCKED"
ROUTE_BLOCKED_SECURITY = "ROUTE_BLOCKED_SECURITY"
NO_ROUTE = "NO_ROUTE"
OVER_CAPITAL = "OVER_CAPITAL"
OVER_EXPOSURE = "OVER_EXPOSURE"
OVER_CARGO = "OVER_CARGO"
OVER_JUMPS = "OVER_JUMPS"
OVER_TIME = "OVER_TIME"
LIQUIDATION_UNKNOWN = "LIQUIDATION_UNKNOWN"
MARGINAL_NET_NEGATIVE = "MARGINAL_NET_NEGATIVE"

REJECTIONS = (
    STALE_BOOK,
    DEPTH_TRUNCATED,
    DEST_DEPTH_SHORT,
    MIN_VOLUME_BLOCKED,
    ROUTE_BLOCKED_SECURITY,
    NO_ROUTE,
    OVER_CAPITAL,
    OVER_EXPOSURE,
    OVER_CARGO,
    OVER_JUMPS,
    OVER_TIME,
    LIQUIDATION_UNKNOWN,
    MARGINAL_NET_NEGATIVE,
)

#: Ranking objectives. The default is deliberately the conservative one: a
#: plan that earns more per hour than a bigger, slower one is the better plan
#: for an operator whose real constraint is the evening he has free.
ISK_PER_ACTIVE_MINUTE = "isk_per_active_minute"
NET_PROFIT = "net_profit"
NET_ROI = "net_roi"
ISK_PER_M3 = "isk_per_m3"
OBJECTIVES = (ISK_PER_ACTIVE_MINUTE, NET_PROFIT, NET_ROI, ISK_PER_M3)

DEDICATED = "dedicated"
ALONG_ROUTE = "along_route"
MODES = (DEDICATED, ALONG_ROUTE)

IMMEDIATE = "immediate"

#: Said on every surface this engine feeds. It is the difference between what
#: the page shows and what the operator will meet when he arrives.
SNAPSHOT_CAVEAT = (
    "A snapshot is not a tape. Both ladders are one moment of two order books; "
    "the books move while you fly, and the destination bid you are pricing "
    "against may be gone when you dock. Nothing here models the competitor who "
    "read the same spread."
)

ORDER_AGE_CAVEAT = (
    "Order age is ESI's `issued` — when the order was last placed OR repriced. "
    "Whether repricing updates it is UNVERIFIED in either direction, so this is "
    "evidence about the book, never the age of the order."
)


# -- profiles ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShipProfile:
    """What the operator flies. Cargo binds the size; the rest prices the time."""

    name: str
    usable_cargo_m3: float
    ehp: float | None = None
    ship_value_isk: float | None = None
    seconds_per_jump: float = 55.0
    handling_minutes: float = 4.0

    @classmethod
    def from_config(cls, config: Config, *, name: str = "unnamed", cargo_m3: float = 0.0):
        return cls(
            name=name,
            usable_cargo_m3=float(cargo_m3),
            seconds_per_jump=float(config.hauling.default_seconds_per_jump),
            handling_minutes=float(config.hauling.default_handling_minutes),
        )

    @classmethod
    def from_row(cls, row) -> ShipProfile:
        return cls(
            name=str(row["name"]),
            usable_cargo_m3=float(row["usable_cargo_m3"] or 0.0),
            ehp=float(row["ehp"]) if row["ehp"] is not None else None,
            ship_value_isk=(
                float(row["ship_value_isk"]) if row["ship_value_isk"] is not None else None
            ),
            seconds_per_jump=float(row["seconds_per_jump"] or 55.0),
            handling_minutes=float(row["handling_minutes"] or 0.0),
        )

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "usable_cargo_m3": self.usable_cargo_m3,
            "ehp": self.ehp,
            "ship_value_isk": self.ship_value_isk,
            "seconds_per_jump": self.seconds_per_jump,
            "handling_minutes": self.handling_minutes,
        }


@dataclass(frozen=True, slots=True)
class HaulProfile:
    """The operator's own constraints. Everything here changes the answer."""

    current_system: int | None
    ship: ShipProfile
    capital_isk: float
    intended_destination: int | None = None
    mode: str = DEDICATED
    max_exposure_isk: float | None = None
    session_minutes: float = 30.0
    max_wait_days: float = 3.0
    security_profile: str = HIGHSEC
    max_jumps: int | None = None
    exit_model: str = IMMEDIATE
    objective: str = ISK_PER_ACTIVE_MINUTE
    avoid_systems: tuple[int, ...] = ()
    safer_penalty: float = 50.0

    def __post_init__(self) -> None:
        if self.objective not in OBJECTIVES:
            raise ValueError(f"unknown objective {self.objective!r}; known: {OBJECTIVES}")
        if self.mode not in MODES:
            raise ValueError(f"unknown mode {self.mode!r}; known: {MODES}")
        if self.security_profile not in PROFILES:
            raise ValueError(
                f"unknown security profile {self.security_profile!r}; known: {PROFILES}"
            )

    @classmethod
    def from_config(cls, config: Config, *, ship: ShipProfile, **overrides) -> HaulProfile:
        hauling = config.hauling
        capital = float(overrides.pop("capital_isk", hauling.max_scan_capital_isk))
        exposure = overrides.pop("max_exposure_isk", None)
        if exposure is None:
            exposure = capital * float(hauling.max_exposure_pct_per_trade) / 100.0
        return cls(
            current_system=overrides.pop("current_system", None),
            ship=ship,
            capital_isk=capital,
            max_exposure_isk=float(exposure),
            session_minutes=float(
                overrides.pop("session_minutes", hauling.default_session_minutes)
            ),
            max_wait_days=float(overrides.pop("max_wait_days", hauling.default_max_wait_days)),
            security_profile=str(overrides.pop("security_profile", config.routes.security_profile)),
            objective=str(overrides.pop("objective", hauling.default_objective)),
            avoid_systems=tuple(
                int(value) for value in overrides.pop("avoid_systems", config.routes.avoid_systems)
            ),
            safer_penalty=float(overrides.pop("safer_penalty", config.routes.safer_penalty)),
            **overrides,
        )

    def as_dict(self) -> dict:
        return {
            "current_system": self.current_system,
            "intended_destination": self.intended_destination,
            "mode": self.mode,
            "ship": self.ship.as_dict(),
            "capital_isk": self.capital_isk,
            "max_exposure_isk": self.max_exposure_isk,
            "session_minutes": self.session_minutes,
            "max_wait_days": self.max_wait_days,
            "security_profile": self.security_profile,
            "max_jumps": self.max_jumps,
            "exit_model": self.exit_model,
            "objective": self.objective,
            "avoid_systems": list(self.avoid_systems),
        }


@dataclass(frozen=True, slots=True)
class Station:
    """An execution station: where a trade can actually happen."""

    station_id: int
    system_id: int | None = None
    region_id: int | None = None
    system_name: str | None = None
    name: str | None = None

    @property
    def label(self) -> str:
        """Never a guessed name. `npcStations.jsonl` carries none (§23, H1a)."""
        if self.name:
            return self.name
        if self.system_name:
            return f"{self.system_name} — station {self.station_id}"
        return f"station {self.station_id}"

    def as_dict(self) -> dict:
        return {
            "station_id": self.station_id,
            "system_id": self.system_id,
            "region_id": self.region_id,
            "label": self.label,
        }


def stations_from_db(config: Config, db, *, include_extra: bool = True) -> list[Station]:
    """The configured execution stations, resolved against the SDE.

    A station the SDE has never heard of is returned with `system_id=None`
    rather than dropped: it renders as UNKNOWN with a reason, which is how the
    operator finds out his config names a station that does not exist.
    """
    systems = db.station_systems()
    regions = db.system_region_map()
    names = db.system_names()
    ids = list(config.hauling.hub_station_ids)
    if include_extra:
        ids.extend(config.hauling.extra_destination_station_ids)
    stations: list[Station] = []
    seen: set[int] = set()
    for station_id in ids:
        station_id = int(station_id)
        if station_id in seen:
            continue
        seen.add(station_id)
        system = systems.get(station_id)
        row = db.station_row(station_id)
        stations.append(
            Station(
                station_id=station_id,
                system_id=int(system) if system is not None else None,
                region_id=regions.get(int(system)) if system is not None else None,
                system_name=names.get(int(system)) if system is not None else None,
                name=(row["name"] if row is not None else None),
            )
        )
    return stations


# -- the scan ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rejection:
    """One thing that could have been a plan, and why it is not."""

    reason: str
    type_id: int | None = None
    type_name: str | None = None
    source_station: int | None = None
    dest_station: int | None = None
    quantity: float | None = None
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "reason": self.reason,
            "type_id": self.type_id,
            "type_name": self.type_name,
            "source_station": self.source_station,
            "dest_station": self.dest_station,
            "quantity": self.quantity,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class HaulPlan:
    """One (item, source, destination, quantity) plan, priced end to end."""

    type_id: int
    type_name: str | None
    badge: str | None
    source: Station
    destination: Station
    quantity: float
    # -- the two walks --
    source_wap: float
    source_cost: float
    source_levels: int
    source_marginal_next_price: float | None
    dest_wap: float
    gross_sale: float
    dest_levels: int
    dest_marginal_next_price: float | None
    # -- costs and returns --
    sales_tax_isk: float
    net_profit: float
    net_roi_pct: float
    marginal_net_isk: float | None
    packaged_volume_m3: float | None
    cargo_m3: float | None
    cargo_utilisation_pct: float | None
    profit_per_m3: float | None
    # -- the trip --
    pickup: RouteFacts
    haul: RouteFacts
    total_jumps: int | None
    detour_jumps: int | None
    active_minutes: float | None
    isk_per_active_minute: float | None
    # -- capital --
    liquidation_days: float | None = None
    isk_per_capital_day: float | None = None
    liquidation_reason: str = ""
    liquidity: dict | None = None
    maker: dict | None = None
    reliability: dict | None = None
    freight: dict | None = None
    # -- provenance --
    source_generation: tuple[int, str] | None = None
    dest_generation: tuple[int, str] | None = None
    source_age_minutes: float | None = None
    dest_age_minutes: float | None = None
    source_depth_complete: bool = True
    dest_depth_complete: bool = True
    min_volume_excluded_qty: float = 0.0
    dest_structure_share: float | None = None
    oldest_issued: str | None = None
    #: The quantities the other objectives would have chosen, when they differ.
    alternatives: dict = field(default_factory=dict)
    breakpoints: tuple = ()
    rank_score: float | None = None

    @property
    def generation_age_minutes(self) -> float | None:
        """The OLDER of the two legs. A row is as fresh as its stalest half."""
        ages = [age for age in (self.source_age_minutes, self.dest_age_minutes) if age is not None]
        return max(ages) if ages else None

    def objective_value(self, objective: str) -> float | None:
        return {
            ISK_PER_ACTIVE_MINUTE: self.isk_per_active_minute,
            NET_PROFIT: self.net_profit,
            NET_ROI: self.net_roi_pct,
            ISK_PER_M3: self.profit_per_m3,
        }[objective]

    def as_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "type_name": self.type_name,
            "badge": self.badge,
            "source": self.source.as_dict(),
            "destination": self.destination.as_dict(),
            "quantity": self.quantity,
            "source_wap": self.source_wap,
            "source_cost": self.source_cost,
            "source_levels": self.source_levels,
            "source_marginal_next_price": self.source_marginal_next_price,
            "dest_wap": self.dest_wap,
            "gross_sale": self.gross_sale,
            "dest_levels": self.dest_levels,
            "dest_marginal_next_price": self.dest_marginal_next_price,
            "sales_tax_isk": self.sales_tax_isk,
            "net_profit": self.net_profit,
            "net_roi_pct": self.net_roi_pct,
            "marginal_net_isk": self.marginal_net_isk,
            "packaged_volume_m3": self.packaged_volume_m3,
            "cargo_m3": self.cargo_m3,
            "cargo_utilisation_pct": self.cargo_utilisation_pct,
            "profit_per_m3": self.profit_per_m3,
            "pickup": self.pickup.as_dict(),
            "haul": self.haul.as_dict(),
            "total_jumps": self.total_jumps,
            "detour_jumps": self.detour_jumps,
            "active_minutes": self.active_minutes,
            "isk_per_active_minute": self.isk_per_active_minute,
            "liquidation_days": self.liquidation_days,
            "isk_per_capital_day": self.isk_per_capital_day,
            "liquidation_reason": self.liquidation_reason,
            "liquidity": self.liquidity,
            "maker": self.maker,
            "reliability": self.reliability,
            "freight": self.freight,
            "source_generation": list(self.source_generation) if self.source_generation else None,
            "dest_generation": list(self.dest_generation) if self.dest_generation else None,
            "source_age_minutes": self.source_age_minutes,
            "dest_age_minutes": self.dest_age_minutes,
            "generation_age_minutes": self.generation_age_minutes,
            "source_depth_complete": self.source_depth_complete,
            "dest_depth_complete": self.dest_depth_complete,
            "min_volume_excluded_qty": self.min_volume_excluded_qty,
            "dest_structure_share": self.dest_structure_share,
            "oldest_issued": self.oldest_issued,
            "alternatives": self.alternatives,
            "breakpoints": list(self.breakpoints),
            "rank_score": self.rank_score,
        }


@dataclass(slots=True)
class HaulScan:
    """One scan: the plans, the refusals, and what could not be priced."""

    generated_at: str
    profile: HaulProfile
    plans: list[HaulPlan] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    unknown_pairs: list[dict] = field(default_factory=list)
    generations: dict = field(default_factory=dict)
    pairs_considered: int = 0
    types_considered: int = 0
    candidates_considered: int = 0
    sde_build: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def rejection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rejection in self.rejected:
            counts[rejection.reason] = counts.get(rejection.reason, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def rejected_for(self, reason: str) -> list[Rejection]:
        return [rejection for rejection in self.rejected if rejection.reason == reason]

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "profile": self.profile.as_dict(),
            "plans": [plan.as_dict() for plan in self.plans],
            "rejected": [rejection.as_dict() for rejection in self.rejected],
            "rejection_counts": self.rejection_counts,
            "unknown_pairs": self.unknown_pairs,
            "generations": {str(key): value for key, value in self.generations.items()},
            "pairs_considered": self.pairs_considered,
            "types_considered": self.types_considered,
            "candidates_considered": self.candidates_considered,
            "sde_build": self.sde_build,
            "notes": self.notes,
            "caveats": [SNAPSHOT_CAVEAT, ORDER_AGE_CAVEAT],
        }


def curves_from_depth(frame: pd.DataFrame) -> dict[tuple[int, int, str], DepthCurve]:
    """Index a depth generation as `(station, type, side) -> DepthCurve`.

    Built once per scan rather than per candidate: a real generation is tens of
    thousands of rows and a scan asks about most of them.
    """
    curves: dict[tuple[int, int, str], DepthCurve] = {}
    if frame is None or frame.empty:
        return curves
    frame = frame.sort_values(["execution_location_id", "type_id", "side", "cumulative_qty"])
    for (station, type_id, side), group in frame.groupby(
        ["execution_location_id", "type_id", "side"], sort=False
    ):
        levels = tuple(
            DepthLevel(
                price=float(row.price),
                qty=float(row.level_qty),
                cumulative_qty=float(row.cumulative_qty),
                cumulative_notional=float(row.cumulative_notional),
                order_count=int(row.level_order_count or 0),
                min_volume_excluded_qty=float(row.min_volume_excluded_qty or 0.0),
                structure_share=(
                    float(row.structure_share)
                    if row.structure_share is not None
                    and row.structure_share == row.structure_share
                    else None
                ),
                oldest_issued=row.oldest_issued,
                newest_issued=row.newest_issued,
            )
            for row in group.itertuples()
        )
        first = group.iloc[0]
        curves[(int(station), int(type_id), str(side))] = DepthCurve(
            levels=levels,
            complete=bool(group["depth_complete"].fillna(False).astype(bool).all()),
            side=str(side),
            type_id=int(type_id),
            execution_location_id=int(station),
            generation=(int(first["region_id"]), str(first["sweep_ts"])),
        )
    return curves


def _minutes(profile: HaulProfile, jumps: int | None) -> float | None:
    """Active minutes for a trip: flying plus loading and unloading.

    The denominator is floored at the handling time, so a zero-jump plan still
    costs what it takes to load and unload rather than dividing by nothing.
    """
    if jumps is None:
        return None
    ship = profile.ship
    minutes = jumps * float(ship.seconds_per_jump) / 60.0 + 2.0 * float(ship.handling_minutes)
    return max(minutes, float(ship.handling_minutes))


@dataclass(frozen=True, slots=True)
class _Trip:
    """The routes a pair of stations implies, computed once per pair."""

    pickup: RouteFacts
    haul: RouteFacts
    total_jumps: int | None
    detour_jumps: int | None
    active_minutes: float | None
    reason: str | None = None
    detail: str = ""


def _route(graph, cache, origin, destination, profile: HaulProfile) -> RouteFacts:
    if cache is not None:
        return cache.route(
            graph,
            origin,
            destination,
            profile=profile.security_profile,
            avoid=profile.avoid_systems,
            safer_penalty=profile.safer_penalty,
        )
    return graph.route(
        origin,
        destination,
        profile=profile.security_profile,
        avoid=profile.avoid_systems,
        safer_penalty=profile.safer_penalty,
    )


def _trip(
    graph: RouteGraph,
    cache,
    profile: HaulProfile,
    source: Station,
    destination: Station,
) -> _Trip:
    """Price the movement. A route that does not exist rejects the whole pair."""
    haul = _route(graph, cache, source.system_id, destination.system_id, profile)
    pickup = (
        _route(graph, cache, profile.current_system, source.system_id, profile)
        if profile.current_system is not None
        else RouteFacts.unknown(
            None, source.system_id, profile.security_profile, "no current system given"
        )
    )
    if not haul.known:
        # A security profile that blocks an otherwise-connected pair is a
        # different fact from a pair that is not connected at all, and the
        # operator can act on the first one by changing the profile.
        blocked = (
            profile.security_profile == HIGHSEC
            and graph.route(source.system_id, destination.system_id).known
        )
        return _Trip(
            pickup=pickup,
            haul=haul,
            total_jumps=None,
            detour_jumps=None,
            active_minutes=None,
            reason=ROUTE_BLOCKED_SECURITY if blocked else NO_ROUTE,
            detail=haul.reason,
        )

    detour_jumps = None
    if profile.mode == ALONG_ROUTE and profile.intended_destination is not None:
        baseline = _route(
            graph, cache, profile.current_system, profile.intended_destination, profile
        )
        onward = _route(graph, cache, destination.system_id, profile.intended_destination, profile)
        if not (pickup.known and onward.known and baseline.known):
            return _Trip(
                pickup=pickup,
                haul=haul,
                total_jumps=None,
                detour_jumps=None,
                active_minutes=None,
                reason=NO_ROUTE,
                detail="the detour cannot be measured against the trip you were making anyway",
            )
        detour_jumps = max(0, pickup.jumps + haul.jumps + onward.jumps - baseline.jumps)
        charged_jumps = detour_jumps
    else:
        charged_jumps = (pickup.jumps or 0) + haul.jumps if pickup.known else haul.jumps

    minutes = _minutes(profile, charged_jumps)
    if profile.max_jumps is not None and charged_jumps > profile.max_jumps:
        return _Trip(
            pickup=pickup,
            haul=haul,
            total_jumps=charged_jumps,
            detour_jumps=detour_jumps,
            active_minutes=minutes,
            reason=OVER_JUMPS,
            detail=f"{charged_jumps} jumps against a {profile.max_jumps}-jump limit",
        )
    if minutes is not None and profile.session_minutes and minutes > profile.session_minutes:
        return _Trip(
            pickup=pickup,
            haul=haul,
            total_jumps=charged_jumps,
            detour_jumps=detour_jumps,
            active_minutes=minutes,
            reason=OVER_TIME,
            detail=(
                f"{minutes:.0f} active minutes against a "
                f"{profile.session_minutes:.0f}-minute session"
            ),
        )
    return _Trip(
        pickup=pickup,
        haul=haul,
        total_jumps=charged_jumps,
        detour_jumps=detour_jumps,
        active_minutes=minutes,
    )


def _candidate_quantities(source: DepthCurve, destination: DepthCurve) -> tuple[float, ...]:
    """Every breakpoint either book offers, capped by what both can fill.

    Between two breakpoints the marginal price does not move, so the best plan
    inside an interval is always at one of its ends — which is why the whole
    quantity space reduces to this handful of numbers.
    """
    ceiling = min(source.available_qty, destination.available_qty)
    if ceiling <= 0:
        return ()
    quantities = {
        quantity
        for quantity in (*source.breakpoints, *destination.breakpoints)
        if 0 < quantity <= ceiling + 1e-9
    }
    return tuple(sorted(quantities))


def _volume(packaged: Mapping[int, float] | None, type_id: int) -> float | None:
    if not packaged:
        return None
    value = packaged.get(int(type_id))
    return float(value) if value else None


def scan_hauls(
    config: Config,
    profile: HaulProfile,
    *,
    stations: Sequence[Station],
    depths: Mapping[int, DepthSnapshot],
    graph: RouteGraph,
    route_cache=None,
    names: Mapping[int, str] | None = None,
    badges: Mapping[int, str] | None = None,
    packaged_volume: Mapping[int, float] | None = None,
    destinations: Sequence[Station] | None = None,
    liquidity=None,
    costs: CostModel | None = None,
    now=None,
    max_plans: int = 100,
) -> HaulScan:
    """Rank hauling plans for one profile against local data only.

    `depths` maps region id to that region's validated depth snapshot. Both
    regions' generations are pinned on every row, and the older one decides
    staleness: a row that joins a fresh book to a three-hour-old one is not a
    fresh row.
    """
    now = ensure_utc(now or utcnow())
    costs = costs or CostModel.from_config(config)
    names = names or {}
    badges = badges or {}
    scan = HaulScan(
        generated_at=iso(now),
        profile=profile,
        sde_build=graph.sde_build,
        generations={
            int(region): {
                "generation": list(snapshot.generation) if snapshot.generation else None,
                "age_minutes": snapshot.age_minutes,
                "stale": snapshot.stale,
                "reason": snapshot.reason,
            }
            for region, snapshot in depths.items()
        },
    )
    if not config.hauling.enabled:
        scan.notes.append("hauling is disabled in config; no plans are produced")
        return scan

    curves: dict[int, dict[tuple[int, int, str], DepthCurve]] = {
        int(region): curves_from_depth(snapshot.priceable) for region, snapshot in depths.items()
    }
    destinations = destinations if destinations is not None else stations

    for source in stations:
        for destination in destinations:
            if source.station_id == destination.station_id:
                continue
            scan.pairs_considered += 1
            _scan_pair(
                scan,
                config,
                profile,
                source=source,
                destination=destination,
                depths=depths,
                curves=curves,
                graph=graph,
                route_cache=route_cache,
                names=names,
                badges=badges,
                packaged_volume=packaged_volume,
                liquidity=liquidity,
                costs=costs,
            )

    objective = profile.objective
    scan.plans = [plan for plan in scan.plans if plan.objective_value(objective) is not None]
    scan.plans.sort(key=lambda plan: -float(plan.objective_value(objective)))
    scan.plans = [
        replace(plan, rank_score=plan.objective_value(objective)) for plan in scan.plans[:max_plans]
    ]
    return scan


def _stale_reason(snapshot: DepthSnapshot | None, station: Station) -> str | None:
    if station.region_id is None:
        # Not a stale book — an unbuilt map. Say which, because the fix is a
        # different command.
        return (
            f"station {station.station_id} is not in the SDE (no system, no region) — "
            "run `sde` to build the map"
        )
    if snapshot is None:
        return f"no depth generation for region {station.region_id} — run `sweep-books`"
    if not snapshot.known:
        return snapshot.reason or "depth is UNKNOWN"
    return None


def _scan_pair(
    scan: HaulScan,
    config: Config,
    profile: HaulProfile,
    *,
    source: Station,
    destination: Station,
    depths: Mapping[int, DepthSnapshot],
    curves: Mapping[int, dict],
    graph: RouteGraph,
    route_cache,
    names: Mapping[int, str],
    badges: Mapping[int, str],
    packaged_volume: Mapping[int, float] | None,
    liquidity,
    costs: CostModel,
) -> None:
    source_snapshot = depths.get(int(source.region_id)) if source.region_id is not None else None
    dest_snapshot = (
        depths.get(int(destination.region_id)) if destination.region_id is not None else None
    )
    stale = _stale_reason(source_snapshot, source) or _stale_reason(dest_snapshot, destination)
    if stale:
        # The pair prices NOTHING. Not the fresh leg, not a partial row.
        scan.unknown_pairs.append(
            {
                "source": source.as_dict(),
                "destination": destination.as_dict(),
                "reason": stale,
                "state": STALE_BOOK,
            }
        )
        scan.rejected.append(
            Rejection(
                reason=STALE_BOOK,
                source_station=source.station_id,
                dest_station=destination.station_id,
                detail=stale,
            )
        )
        return

    trip = _trip(graph, route_cache, profile, source, destination)
    if trip.reason is not None:
        scan.rejected.append(
            Rejection(
                reason=trip.reason,
                source_station=source.station_id,
                dest_station=destination.station_id,
                detail=trip.detail,
            )
        )
        return

    source_curves = curves.get(int(source.region_id), {})
    dest_curves = curves.get(int(destination.region_id), {})
    source_types = {
        type_id
        for (station, type_id, side) in source_curves
        if station == source.station_id and side == "sell"
    }
    dest_types = {
        type_id
        for (station, type_id, side) in dest_curves
        if station == destination.station_id and side == "buy"
    }
    shared = sorted(source_types & dest_types)
    scan.types_considered += len(shared)

    exposure_cap = profile.max_exposure_isk
    for type_id in shared:
        ask = source_curves[(source.station_id, type_id, "sell")]
        bid = dest_curves[(destination.station_id, type_id, "buy")]
        plan = _best_plan(
            scan,
            config,
            profile,
            type_id=type_id,
            ask=ask,
            bid=bid,
            source=source,
            destination=destination,
            trip=trip,
            source_snapshot=source_snapshot,
            dest_snapshot=dest_snapshot,
            names=names,
            badges=badges,
            packaged_volume=packaged_volume,
            liquidity=liquidity,
            costs=costs,
            exposure_cap=exposure_cap,
        )
        if plan is not None:
            scan.plans.append(plan)


def _best_plan(
    scan: HaulScan,
    config: Config,
    profile: HaulProfile,
    *,
    type_id: int,
    ask: DepthCurve,
    bid: DepthCurve,
    source: Station,
    destination: Station,
    trip: _Trip,
    source_snapshot: DepthSnapshot,
    dest_snapshot: DepthSnapshot,
    names: Mapping[int, str],
    badges: Mapping[int, str],
    packaged_volume: Mapping[int, float] | None,
    liquidity,
    costs: CostModel,
    exposure_cap: float | None,
) -> HaulPlan | None:
    """Price every feasible breakpoint for one type, and keep the best."""
    name = names.get(int(type_id))
    volume = _volume(packaged_volume, type_id)
    quantities = _candidate_quantities(ask, bid)
    if not quantities:
        return None

    # The cap bit: say which side ran out, and whether it ran out because the
    # curve was truncated rather than because the book is shallow.
    ceiling = min(ask.available_qty, bid.available_qty)
    if bid.available_qty < ask.available_qty:
        binding, curve = DEST_DEPTH_SHORT, bid
    else:
        binding, curve = DEPTH_TRUNCATED if not ask.complete else DEST_DEPTH_SHORT, ask
    if (
        max(ask.breakpoints or (0,)) > ceiling + 1e-9
        or max(bid.breakpoints or (0,)) > ceiling + 1e-9
    ):
        scan.rejected.append(
            Rejection(
                reason=DEPTH_TRUNCATED if not curve.complete else binding,
                type_id=int(type_id),
                type_name=name,
                source_station=source.station_id,
                dest_station=destination.station_id,
                quantity=float(ceiling),
                detail=(
                    "quantities beyond this are capped by the shallower side"
                    if curve.complete
                    else "the curve was cut short by the depth bound, so deeper sizes are UNKNOWN"
                ),
            )
        )

    best: HaulPlan | None = None
    priced: list[tuple[float, float]] = []  # (quantity, net profit)
    alternatives: dict[str, dict] = {}
    for quantity in quantities:
        scan.candidates_considered += 1
        cargo = quantity * volume if volume else None
        if (
            cargo is not None
            and profile.ship.usable_cargo_m3
            and cargo > profile.ship.usable_cargo_m3
        ):
            scan.rejected.append(
                Rejection(
                    reason=OVER_CARGO,
                    type_id=int(type_id),
                    type_name=name,
                    source_station=source.station_id,
                    dest_station=destination.station_id,
                    quantity=quantity,
                    detail=(
                        f"{cargo:,.0f} m³ against {profile.ship.usable_cargo_m3:,.0f} m³ of hold"
                    ),
                )
            )
            break  # every larger breakpoint fails for the same reason

        buy = q_walk(ask, quantity)
        sell = q_walk(bid, quantity)
        if not buy.known or not sell.known:
            walk = buy if not buy.known else sell
            scan.rejected.append(
                Rejection(
                    reason=DEPTH_TRUNCATED if "truncated" in walk.reason else DEST_DEPTH_SHORT,
                    type_id=int(type_id),
                    type_name=name,
                    source_station=source.station_id,
                    dest_station=destination.station_id,
                    quantity=quantity,
                    detail=walk.reason,
                )
            )
            continue

        cost = buy.value
        if cost > profile.capital_isk:
            scan.rejected.append(
                Rejection(
                    reason=OVER_CAPITAL,
                    type_id=int(type_id),
                    type_name=name,
                    source_station=source.station_id,
                    dest_station=destination.station_id,
                    quantity=quantity,
                    detail=f"{cost:,.0f} ISK against {profile.capital_isk:,.0f} of capital",
                )
            )
            break
        if exposure_cap is not None and cost > exposure_cap:
            scan.rejected.append(
                Rejection(
                    reason=OVER_EXPOSURE,
                    type_id=int(type_id),
                    type_name=name,
                    source_station=source.station_id,
                    dest_station=destination.station_id,
                    quantity=quantity,
                    detail=f"{cost:,.0f} ISK against a {exposure_cap:,.0f} exposure cap",
                )
            )
            break

        proceeds = costs.sell_proceeds(sell.value, maker=False)
        tax = sell.value - proceeds
        net = proceeds - cost
        marginal = None
        if priced:
            previous_quantity, previous_net = priced[-1]
            marginal = net - previous_net
            if marginal <= 0:
                # The last chunk did not pay for itself. Bigger is not better;
                # this is the size the book stops rewarding.
                scan.rejected.append(
                    Rejection(
                        reason=MARGINAL_NET_NEGATIVE,
                        type_id=int(type_id),
                        type_name=name,
                        source_station=source.station_id,
                        dest_station=destination.station_id,
                        quantity=quantity,
                        detail=(
                            f"the chunk from {previous_quantity:,.0f} to {quantity:,.0f} units "
                            f"nets {marginal:,.0f} ISK"
                        ),
                    )
                )
                priced.append((quantity, net))
                continue
        priced.append((quantity, net))

        minutes = trip.active_minutes
        per_minute = (net / minutes) if minutes else None
        days = (minutes / (60.0 * 24.0)) if minutes else None
        plan = HaulPlan(
            type_id=int(type_id),
            type_name=name,
            badge=badges.get(int(type_id)),
            source=source,
            destination=destination,
            quantity=float(quantity),
            source_wap=float(buy.wap),
            source_cost=float(cost),
            source_levels=buy.levels_consumed,
            source_marginal_next_price=buy.marginal_next_price,
            dest_wap=float(sell.wap),
            gross_sale=float(sell.value),
            dest_levels=sell.levels_consumed,
            dest_marginal_next_price=sell.marginal_next_price,
            sales_tax_isk=float(tax),
            net_profit=float(net),
            net_roi_pct=float(net / cost * 100.0) if cost > 0 else 0.0,
            marginal_net_isk=marginal,
            packaged_volume_m3=volume,
            cargo_m3=cargo,
            cargo_utilisation_pct=(
                cargo / profile.ship.usable_cargo_m3 * 100.0
                if cargo is not None and profile.ship.usable_cargo_m3
                else None
            ),
            profit_per_m3=(net / cargo if cargo else None),
            pickup=trip.pickup,
            haul=trip.haul,
            total_jumps=trip.total_jumps,
            detour_jumps=trip.detour_jumps,
            active_minutes=minutes,
            isk_per_active_minute=per_minute,
            liquidation_days=days,
            isk_per_capital_day=(net / (cost * days) if days and cost > 0 else None),
            liquidation_reason=(
                "immediate exit: the capital is committed for the trip, so ISK-days are "
                "charged over travel time"
                if profile.exit_model == IMMEDIATE
                else ""
            ),
            source_generation=ask.generation,
            dest_generation=bid.generation,
            source_age_minutes=source_snapshot.age_minutes,
            dest_age_minutes=dest_snapshot.age_minutes,
            source_depth_complete=ask.complete,
            dest_depth_complete=bid.complete,
            min_volume_excluded_qty=bid.min_volume_excluded_qty,
            dest_structure_share=_structure_share(bid, quantity),
            oldest_issued=_oldest_issued(bid),
            breakpoints=tuple(priced),
        )
        if liquidity is not None:
            plan = liquidity(plan)
            if plan is None:
                continue
        for objective in OBJECTIVES:
            value = plan.objective_value(objective)
            if value is None:
                continue
            current = alternatives.get(objective)
            if current is None or value > current["value"]:
                alternatives[objective] = {"value": float(value), "quantity": float(quantity)}
        if best is None or _better(plan, best, profile.objective):
            best = plan

    if best is None:
        return None
    chosen = float(best.quantity)
    return replace(
        best,
        alternatives={
            objective: entry
            for objective, entry in alternatives.items()
            if abs(entry["quantity"] - chosen) > 1e-9
        },
        breakpoints=tuple(priced),
    )


def _better(candidate: HaulPlan, incumbent: HaulPlan, objective: str) -> bool:
    left = candidate.objective_value(objective)
    right = incumbent.objective_value(objective)
    if left is None:
        return False
    if right is None:
        return True
    return left > right


def _structure_share(curve: DepthCurve, quantity: float) -> float | None:
    """Structure-resident share of the depth this plan actually sells into.

    §17 measured up to 98.3% of bid volume resting in player structures. It is
    reachable — range decides, not ownership (§22 S2a) — but it is worth seeing
    how much of the exit depends on someone else's structure staying up.
    """
    taken = 0.0
    weighted = 0.0
    measured = 0.0
    for level in curve.levels:
        if level.qty <= 0:
            continue
        chunk = min(level.qty, quantity - taken)
        if chunk <= 0:
            break
        if level.structure_share is not None:
            weighted += chunk * level.structure_share
            measured += chunk
        taken += chunk
    return (weighted / measured) if measured > 0 else None


def _oldest_issued(curve: DepthCurve) -> str | None:
    stamps = [level.oldest_issued for level in curve.levels if level.oldest_issued]
    return min(stamps) if stamps else None


def liquidity_hook(fn) -> callable:
    """Adapter so H3's scenarios can attach to a plan without this module
    importing them — the engine stays pure arithmetic over depth and routes."""
    return fn


def scan_inputs(config: Config, db, *, region_ids: Iterable[int] | None = None):
    """Everything a scan needs from disk, gathered once (never from ESI).

    Returns `(stations, depths, graph, names, badges, packaged_volume)`.
    """
    from .books import load_validated_depth

    stations = stations_from_db(config, db)
    regions = sorted(
        {int(station.region_id) for station in stations if station.region_id is not None}
        if region_ids is None
        else {int(region) for region in region_ids}
    )
    depths = {region: load_validated_depth(config, region) for region in regions}
    graph = RouteGraph.from_db(db)
    type_ids: set[int] = set()
    for snapshot in depths.values():
        if not snapshot.frame.empty:
            type_ids.update(int(value) for value in snapshot.frame["type_id"].unique())
    names = db.type_names(type_ids) if type_ids else {}
    packaged = {
        int(row["type_id"]): float(row["packaged_volume"])
        for row in db.conn.execute(
            "SELECT type_id, packaged_volume FROM sde_types WHERE packaged_volume IS NOT NULL"
        )
    }
    badges = {
        int(row["type_id"]): str(row["tier"])
        for row in db.conn.execute("SELECT type_id, tier FROM universe WHERE tier IS NOT NULL")
    }
    return stations, depths, graph, names, badges, packaged
