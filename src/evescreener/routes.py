"""The stargate graph, and what a route costs (plan.md §23.8, §23.9).

Every route in this system is computed **locally**, from the SDE's own gate
graph. CCP's `POST /route` exists, is uncached, and carries its own 3,600-token
group; their published guidance is to build the graph yourself and keep the
endpoint for spot checks. A scan that priced a thousand candidate hauls would
otherwise be a thousand uncached requests for a map that changes on a patch
cycle.

Two rules do the load-bearing work here.

**No route is UNKNOWN, and UNKNOWN never becomes an estimate.** A disconnected
pair, a system the graph has never heard of, or a security filter that empties
the graph all return `RouteFacts.unknown(...)` with the reason. There is no
straight-line fallback, no "about N jumps", and no silently-dropped avoid list.

**High-sec is what the client displays, not what the float says.** CCP rounds
the raw security to one decimal for display, and every gate camp, every
CONCORD response and every hauler's judgement is keyed to that displayed
number. A system at 0.4499 shows 0.4 and is **not** high-sec; a system at
0.45 shows 0.5 and **is**. The one irregular case is the sliver just above
zero: `0 < true_sec <= 0.05` displays as 0.1 rather than 0.0, because CCP
refuses to show a positive-security system as null.
"""

from __future__ import annotations

import hashlib
import heapq
import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .timeutil import iso, utcnow

__all__ = [
    "HIGHSEC_DISPLAY",
    "PROFILES",
    "HIGHSEC",
    "SAFER",
    "SHORTEST",
    "RouteCache",
    "RouteFacts",
    "RouteGraph",
    "display_security",
    "is_highsec",
]

#: Displayed security at or above which a system is high-sec.
HIGHSEC_DISPLAY = 0.5

SHORTEST = "shortest"
SAFER = "safer"
HIGHSEC = "highsec"
PROFILES = (SHORTEST, SAFER, HIGHSEC)

#: EVE's numeric buy-order ranges top out at 40 jumps, so no reachability
#: search ever needs to go further than this.
MAX_ORDER_RANGE_JUMPS = 40


def display_security(true_sec: float | None) -> float | None:
    """The security the client shows. UNKNOWN in, UNKNOWN out.

    `round(true_sec, 1)` with **half-up** rounding — deliberately not Python's
    `round()`, whose banker's rounding would send 0.45 to 0.4 and quietly move
    the high-sec boundary by one system class. The single exception is the
    sliver above zero: `0 < true_sec <= 0.05` displays as 0.1.
    """
    if true_sec is None:
        return None
    try:
        value = float(true_sec)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    if 0.0 < value <= 0.05:
        return 0.1
    return float(Decimal(repr(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def is_highsec(true_sec: float | None) -> bool:
    """High-sec is `display >= 0.5`, i.e. `true_sec >= 0.45`.

    An unknown security is **not** high-sec. A system we cannot measure is not
    a system we may route a freighter through on the assumption it is safe.
    """
    display = display_security(true_sec)
    return display is not None and display >= HIGHSEC_DISPLAY


@dataclass(frozen=True, slots=True)
class RouteFacts:
    """One route, or the stated reason there is not one.

    `known` is about the route's **existence**. `min_display_security` is a
    separate question and can be UNKNOWN on a known route, when the graph
    carries a system whose security the SDE did not give us — that is reported
    as UNKNOWN rather than as a comfortable number.
    """

    origin: int | None
    destination: int | None
    profile: str
    systems: tuple[int, ...] = ()
    jumps: int | None = None
    min_display_security: float | None = None
    nullsec_systems: int | None = None
    lowsec_systems: int | None = None
    borderline_systems: int | None = None
    unknown_security_systems: int | None = None
    sde_build: int | None = None
    known: bool = False
    reason: str = ""

    @classmethod
    def unknown(
        cls,
        origin: int | None,
        destination: int | None,
        profile: str,
        reason: str,
        *,
        sde_build: int | None = None,
    ) -> RouteFacts:
        return cls(
            origin=origin,
            destination=destination,
            profile=profile,
            sde_build=sde_build,
            known=False,
            reason=reason,
        )

    @property
    def all_highsec(self) -> bool:
        """True only when every system on the route is measurably high-sec."""
        return bool(
            self.known
            and not self.unknown_security_systems
            and not self.nullsec_systems
            and not self.lowsec_systems
        )

    def as_dict(self) -> dict:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "profile": self.profile,
            "systems": list(self.systems),
            "jumps": self.jumps,
            "min_display_security": self.min_display_security,
            "nullsec_systems": self.nullsec_systems,
            "lowsec_systems": self.lowsec_systems,
            "borderline_systems": self.borderline_systems,
            "unknown_security_systems": self.unknown_security_systems,
            "sde_build": self.sde_build,
            "known": self.known,
            "reason": self.reason,
        }


def _avoid_key(avoid: Iterable[int], penalty: float) -> str:
    """One stable key for the two inputs that reshape a search.

    The penalty belongs in the key: two `safer` routes computed at different
    penalties are different answers, and a cache that conflated them would
    hand back a route nobody asked for.
    """
    payload = json.dumps(
        {"avoid": sorted(int(value) for value in avoid), "penalty": round(float(penalty), 6)},
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


class RouteGraph:
    """The gate graph, with security attached. Pure; no I/O, no Qt."""

    def __init__(
        self,
        edges: Iterable[tuple[int, int]],
        security: Mapping[int, float | None],
        *,
        sde_build: int | None = None,
    ) -> None:
        self.sde_build = int(sde_build) if sde_build is not None else None
        self.security = {int(key): value for key, value in security.items()}
        self.adjacency: dict[int, set[int]] = {}
        for left, right in edges:
            left, right = int(left), int(right)
            if left == right:
                continue
            # Gates are stored per direction, but a stargate pair is walkable
            # both ways; recording both makes a one-sided SDE row harmless.
            self.adjacency.setdefault(left, set()).add(right)
            self.adjacency.setdefault(right, set()).add(left)
        #: Every system the map knows about, gated or not. A system with no
        #: stargates (J-space, an isolated pocket) is genuinely **unreachable**,
        #: which is a different answer from one the map has never heard of —
        #: and the reason text has to say which.
        self.known_systems = set(self.adjacency) | {int(key) for key in self.security}
        self._distance_cache: dict[tuple[int, int], dict[int, int]] = {}

    # -- construction ------------------------------------------------------
    @classmethod
    def from_db(cls, db) -> RouteGraph:
        build = db.get_meta("sde_build")
        return cls(
            db.stargate_edges(),
            db.system_security(),
            sde_build=int(build) if build else None,
        )

    def __bool__(self) -> bool:
        return bool(self.adjacency)

    @property
    def systems(self) -> int:
        return len(self.adjacency)

    def knows(self, system_id: int | None) -> bool:
        return system_id is not None and int(system_id) in self.known_systems

    def display_security(self, system_id: int) -> float | None:
        return display_security(self.security.get(int(system_id)))

    def is_highsec(self, system_id: int) -> bool:
        return is_highsec(self.security.get(int(system_id)))

    # -- searches ----------------------------------------------------------
    def _allowed(self, profile: str, avoid: frozenset[int]):
        def allowed(system_id: int) -> bool:
            if system_id in avoid:
                return False
            if profile == HIGHSEC:
                return self.is_highsec(system_id)
            return True

        return allowed

    def route(
        self,
        origin: int | None,
        destination: int | None,
        *,
        profile: str = SHORTEST,
        avoid: Iterable[int] = (),
        safer_penalty: float = 50.0,
    ) -> RouteFacts:
        """One route under one profile, or UNKNOWN with the reason why."""
        if profile not in PROFILES:
            raise ValueError(f"unknown route profile {profile!r}; known: {PROFILES}")
        if origin is None or destination is None:
            return RouteFacts.unknown(
                origin,
                destination,
                profile,
                "no origin or destination given",
                sde_build=self.sde_build,
            )
        origin, destination = int(origin), int(destination)
        avoid_set = frozenset(int(value) for value in avoid)
        for label, system in (("origin", origin), ("destination", destination)):
            if not self.knows(system):
                return RouteFacts.unknown(
                    origin,
                    destination,
                    profile,
                    f"{label} system {system} is not in the stargate graph — "
                    "run `sde` to rebuild the map",
                    sde_build=self.sde_build,
                )
        allowed = self._allowed(profile, avoid_set)
        for label, system in (("origin", origin), ("destination", destination)):
            if not allowed(system):
                detail = (
                    "is not high-sec"
                    if profile == HIGHSEC and system not in avoid_set
                    else "is on the avoid list"
                )
                return RouteFacts.unknown(
                    origin,
                    destination,
                    profile,
                    f"{label} system {system} {detail}, so this profile has no route",
                    sde_build=self.sde_build,
                )

        if origin == destination:
            return self._facts(origin, destination, profile, [origin])

        path = (
            self._dijkstra(origin, destination, allowed, safer_penalty)
            if profile == SAFER
            else self._bfs(origin, destination, allowed)
        )
        if path is None:
            reason = (
                "no high-sec route exists between these systems"
                if profile == HIGHSEC
                else "the systems are not connected under this profile"
            )
            if avoid_set:
                reason += f" (avoiding {len(avoid_set)} system(s))"
            return RouteFacts.unknown(
                origin, destination, profile, reason, sde_build=self.sde_build
            )
        return self._facts(origin, destination, profile, path)

    def _bfs(self, origin: int, destination: int, allowed) -> list[int] | None:
        previous: dict[int, int | None] = {origin: None}
        queue = deque([origin])
        while queue:
            current = queue.popleft()
            if current == destination:
                return self._unwind(previous, current)
            for neighbour in sorted(self.adjacency.get(current, ())):
                if neighbour in previous or not allowed(neighbour):
                    continue
                previous[neighbour] = current
                queue.append(neighbour)
        return None

    def _dijkstra(self, origin: int, destination: int, allowed, penalty: float) -> list[int] | None:
        """Least-danger route: each hop costs 1, plus `penalty` off high-sec.

        The penalty is charged on **entering** a system, so a route is measured
        by where it takes you rather than by where it started.
        """
        previous: dict[int, int | None] = {origin: None}
        best: dict[int, float] = {origin: 0.0}
        heap: list[tuple[float, int]] = [(0.0, origin)]
        while heap:
            cost, current = heapq.heappop(heap)
            if current == destination:
                return self._unwind(previous, current)
            if cost > best.get(current, float("inf")):
                continue
            for neighbour in sorted(self.adjacency.get(current, ())):
                if not allowed(neighbour):
                    continue
                step = 1.0 + (0.0 if self.is_highsec(neighbour) else float(penalty))
                candidate = cost + step
                if candidate < best.get(neighbour, float("inf")):
                    best[neighbour] = candidate
                    previous[neighbour] = current
                    heapq.heappush(heap, (candidate, neighbour))
        return None

    @staticmethod
    def _unwind(previous: dict[int, int | None], node: int) -> list[int]:
        path = []
        cursor: int | None = node
        while cursor is not None:
            path.append(cursor)
            cursor = previous[cursor]
        return path[::-1]

    def _facts(
        self, origin: int, destination: int, profile: str, path: Sequence[int]
    ) -> RouteFacts:
        displays = [self.display_security(system) for system in path]
        known_displays = [value for value in displays if value is not None]
        return RouteFacts(
            origin=origin,
            destination=destination,
            profile=profile,
            systems=tuple(int(system) for system in path),
            jumps=len(path) - 1,
            # A route carrying a system of unknown security has an UNKNOWN
            # minimum, not a minimum computed over the systems we happen to
            # know: that would report the safest reading of missing data.
            min_display_security=(
                min(known_displays)
                if known_displays and len(known_displays) == len(displays)
                else None
            ),
            nullsec_systems=sum(1 for value in known_displays if value <= 0.0),
            lowsec_systems=sum(1 for value in known_displays if 0.0 < value < HIGHSEC_DISPLAY),
            borderline_systems=sum(1 for value in known_displays if value == HIGHSEC_DISPLAY),
            unknown_security_systems=len(displays) - len(known_displays),
            sde_build=self.sde_build,
            known=True,
            reason="",
        )

    # -- jump distance, for order ranges -----------------------------------
    def distances_from(
        self, origin: int, *, max_jumps: int = MAX_ORDER_RANGE_JUMPS
    ) -> dict[int, int]:
        """Jump distance from one system to everything within `max_jumps`.

        Computed once per (system, bound) and memoised, because resolving buy
        order ranges asks the same question thousands of times per sweep — once
        per resting order — and the answer cannot change inside a generation.
        Security is deliberately ignored: an order's range reaches as far as it
        reaches, whatever the space between is like.
        """
        key = (int(origin), int(max_jumps))
        cached = self._distance_cache.get(key)
        if cached is not None:
            return cached
        distances: dict[int, int] = {}
        if not self.knows(origin):
            self._distance_cache[key] = distances
            return distances
        distances[int(origin)] = 0
        frontier = deque([int(origin)])
        while frontier:
            current = frontier.popleft()
            distance = distances[current]
            if distance >= max_jumps:
                continue
            for neighbour in self.adjacency.get(current, ()):
                if neighbour not in distances:
                    distances[neighbour] = distance + 1
                    frontier.append(neighbour)
        self._distance_cache[key] = distances
        return distances

    def jump_distance(
        self, origin: int | None, destination: int | None, *, max_jumps: int = MAX_ORDER_RANGE_JUMPS
    ) -> int | None:
        """Jumps between two systems, or **None** for UNKNOWN.

        None means "further than the bound, disconnected, or off the map" — all
        three of which fail closed wherever this is consumed (§23.6).
        """
        if origin is None or destination is None:
            return None
        origin, destination = int(origin), int(destination)
        if origin == destination:
            return 0
        # The graph is undirected, so either endpoint answers — and answering
        # from one we have already searched from costs nothing at all.
        if (origin, int(max_jumps)) not in self._distance_cache and (
            destination,
            int(max_jumps),
        ) in self._distance_cache:
            origin, destination = destination, origin
        return self.distances_from(origin, max_jumps=max_jumps).get(destination)


class RouteCache:
    """Computed routes, keyed by everything that could change the answer.

    A cached route is invalidated **by key**, never edited in place: a new SDE
    build, a different profile, a different avoid list or a different penalty
    all produce a different key, so a stale answer is unreachable rather than
    overwritten. Storing a route that does not exist matters as much as storing
    one that does — a `NO_ROUTE` is an expensive search too.
    """

    def __init__(self, db, *, enabled: bool = True) -> None:
        self.db = db
        self.enabled = bool(enabled)

    def get(
        self, graph: RouteGraph, origin: int, destination: int, profile: str, avoid, penalty: float
    ) -> RouteFacts | None:
        if not self.enabled or graph.sde_build is None:
            return None
        row = self.db.conn.execute(
            "SELECT * FROM route_cache WHERE sde_build=? AND origin=? AND destination=?"
            " AND profile=? AND avoid_hash=?",
            (
                int(graph.sde_build),
                int(origin),
                int(destination),
                str(profile),
                _avoid_key(avoid, penalty),
            ),
        ).fetchone()
        if row is None:
            return None
        systems = tuple(json.loads(row["systems"] or "[]"))
        if not row["known"]:
            return RouteFacts.unknown(
                int(origin),
                int(destination),
                str(profile),
                row["reason"] or "",
                sde_build=graph.sde_build,
            )
        return graph._facts(int(origin), int(destination), str(profile), systems)

    def put(self, graph: RouteGraph, facts: RouteFacts, avoid, penalty: float) -> None:
        if not self.enabled or graph.sde_build is None or facts.origin is None:
            return
        self.db.conn.execute(
            "INSERT INTO route_cache(sde_build, origin, destination, profile, avoid_hash,"
            " systems, jumps, known, reason, computed_at) VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(sde_build, origin, destination, profile, avoid_hash) DO UPDATE SET"
            " systems=excluded.systems, jumps=excluded.jumps, known=excluded.known,"
            " reason=excluded.reason, computed_at=excluded.computed_at",
            (
                int(graph.sde_build),
                int(facts.origin),
                int(facts.destination),
                str(facts.profile),
                _avoid_key(avoid, penalty),
                json.dumps(list(facts.systems)),
                facts.jumps,
                1 if facts.known else 0,
                facts.reason,
                iso(utcnow()),
            ),
        )

    def route(
        self,
        graph: RouteGraph,
        origin: int | None,
        destination: int | None,
        *,
        profile: str = SHORTEST,
        avoid: Iterable[int] = (),
        safer_penalty: float = 50.0,
    ) -> RouteFacts:
        """Cached `RouteGraph.route`, with identical semantics."""
        avoid = tuple(int(value) for value in avoid)
        if origin is not None and destination is not None:
            hit = self.get(graph, int(origin), int(destination), profile, avoid, safer_penalty)
            if hit is not None:
                return hit
        facts = graph.route(
            origin, destination, profile=profile, avoid=avoid, safer_penalty=safer_penalty
        )
        self.put(graph, facts, avoid, safer_penalty)
        return facts
