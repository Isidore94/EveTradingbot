"""The route engine and the security boundary (plan.md §23.8, §23.9).

Two fixtures pin this, and both were written before `routes.py` existed
(§11 D5): a hand-built ten-system graph where the shortest route and the safe
route deliberately disagree, and the **real** gated k-space graph from SDE
build 3478781.

The real-SDE case is the one worth reading. Jita → Amarr is eleven jumps
through **Ahbazon**, a 0.4 system — the exact route every hauler in the game
argues about — and the high-sec-only answer is thirty-four. A router that
cannot tell those two apart is not a router a freighter pilot can use.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evescreener.routes import (
    HIGHSEC,
    SAFER,
    SHORTEST,
    RouteCache,
    RouteGraph,
    display_security,
    is_highsec,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def synthetic() -> dict:
    return _load("route_graph_synthetic.json")


@pytest.fixture(scope="module")
def graph(synthetic) -> RouteGraph:
    return RouteGraph(
        [(left, right) for left, right in synthetic["edges"]],
        {system: security for system, security, _name in synthetic["systems"]},
        sde_build=1,
    )


# -- 1. displayed security, at the boundaries -------------------------------


def test_display_security_matches_the_client_at_every_fixtured_boundary(synthetic):
    for raw, expected in synthetic["display_security"]:
        assert display_security(raw) == pytest.approx(expected), raw


def test_the_high_sec_boundary_sits_at_a_true_security_of_0_45():
    """0.4499 shows 0.4 and is not high-sec; 0.45 shows 0.5 and is."""
    assert is_highsec(0.45) is True
    assert is_highsec(0.449) is False
    assert is_highsec(0.4499999) is False
    assert is_highsec(0.5) is True


def test_the_sliver_above_zero_displays_as_0_1_and_is_still_not_high_sec():
    assert display_security(0.01) == pytest.approx(0.1)
    assert display_security(0.05) == pytest.approx(0.1)
    assert is_highsec(0.05) is False


def test_unknown_security_is_never_high_sec():
    """A system we cannot measure is not a system we may assume is safe."""
    assert display_security(None) is None
    assert is_highsec(None) is False


# -- 2. the profiles disagree, on purpose -----------------------------------


def test_the_shortest_route_takes_the_dangerous_way(graph, synthetic):
    expected = synthetic["expected"]["shortest_1_to_7"]
    facts = graph.route(1, 7, profile=SHORTEST)
    assert facts.known
    assert list(facts.systems) == expected["systems"]
    assert facts.jumps == expected["jumps"]
    assert facts.min_display_security == pytest.approx(expected["min_display_security"])
    assert facts.lowsec_systems == expected["lowsec_systems"]
    assert facts.nullsec_systems == expected["nullsec_systems"]
    assert facts.all_highsec is False


def test_the_high_sec_route_is_longer_and_stays_in_high_sec(graph, synthetic):
    expected = synthetic["expected"]["highsec_1_to_7"]
    facts = graph.route(1, 7, profile=HIGHSEC)
    assert list(facts.systems) == expected["systems"]
    assert facts.jumps == expected["jumps"]
    assert facts.min_display_security == pytest.approx(0.5)
    # System 3 is true-sec 0.45: it displays as exactly 0.5 and counts.
    assert facts.borderline_systems == expected["borderline_systems"]
    assert facts.all_highsec is True


def test_the_safer_route_pays_four_jumps_to_avoid_two_low_sec_systems(graph, synthetic):
    facts = graph.route(1, 7, profile=SAFER, safer_penalty=50.0)
    assert list(facts.systems) == synthetic["expected"]["safer_1_to_7"]["systems"]
    assert facts.jumps == 4


def test_a_penalty_of_zero_makes_safer_identical_to_shortest(graph):
    """The penalty is the whole difference, and it is a config value."""
    assert graph.route(1, 7, profile=SAFER, safer_penalty=0.0).jumps == 3


def test_null_sec_systems_are_counted_on_the_route(graph, synthetic):
    facts = graph.route(1, 6, profile=SHORTEST)
    assert facts.nullsec_systems == synthetic["expected"]["shortest_1_to_6"]["nullsec_systems"]


# -- 3. UNKNOWN, never a guess ----------------------------------------------


def test_avoiding_one_system_can_remove_the_only_high_sec_route(graph):
    facts = graph.route(1, 7, profile=HIGHSEC, avoid=[3])
    assert facts.known is False
    assert "no high-sec route" in facts.reason
    assert facts.jumps is None, "an UNKNOWN route reports no jump count at all"


def test_the_same_avoid_leaves_the_shortest_route_untouched(graph):
    assert graph.route(1, 7, profile=SHORTEST, avoid=[3]).jumps == 3


def test_a_disconnected_system_is_unknown_rather_than_far_away(graph):
    facts = graph.route(1, 10, profile=SHORTEST)
    assert facts.known is False
    assert "not connected" in facts.reason


def test_a_system_the_graph_never_heard_of_says_so(graph):
    facts = graph.route(1, 9999, profile=SHORTEST)
    assert facts.known is False
    assert "not in the stargate graph" in facts.reason


def test_a_high_sec_profile_refuses_when_the_origin_itself_is_low_sec(graph):
    facts = graph.route(5, 7, profile=HIGHSEC)
    assert facts.known is False
    assert "not high-sec" in facts.reason


def test_a_route_to_itself_is_zero_jumps_not_an_error(graph):
    facts = graph.route(7, 7, profile=SHORTEST)
    assert facts.known and facts.jumps == 0 and facts.systems == (7,)


def test_a_route_carrying_an_unmeasured_system_reports_an_unknown_minimum():
    """The minimum is over the whole route or it is UNKNOWN — never over the
    systems we happen to know, which would report the safest reading of
    missing data."""
    graph = RouteGraph([(1, 2), (2, 3)], {1: 0.9, 3: 0.8}, sde_build=1)
    facts = graph.route(1, 3, profile=SHORTEST)
    assert facts.known and facts.jumps == 2
    assert facts.min_display_security is None
    assert facts.unknown_security_systems == 1
    assert facts.all_highsec is False


def test_an_unmeasured_system_is_excluded_from_a_high_sec_route():
    graph = RouteGraph([(1, 2), (2, 3)], {1: 0.9, 3: 0.8}, sde_build=1)
    assert graph.route(1, 3, profile=HIGHSEC).known is False


def test_an_unknown_profile_name_is_a_loud_error(graph):
    with pytest.raises(ValueError, match="unknown route profile"):
        graph.route(1, 7, profile="scenic")


# -- 4. jump distance, which is what resolves order ranges ------------------


def test_jump_distance_ignores_security_because_an_order_range_does(graph, synthetic):
    assert graph.jump_distance(9, 1) == synthetic["expected"]["jump_distance_9_to_1"]
    assert graph.jump_distance(1, 1) == 0


def test_a_distance_beyond_the_bound_is_unknown_rather_than_large(graph):
    assert graph.jump_distance(9, 1, max_jumps=2) is None
    assert graph.jump_distance(1, 10) is None
    assert graph.jump_distance(1, None) is None


def test_distances_are_memoised_per_origin_and_bound(graph):
    first = graph.distances_from(1, max_jumps=5)
    assert graph.distances_from(1, max_jumps=5) is first, "one BFS per (origin, bound)"


# -- 5. the cache is keyed, never edited ------------------------------------


def test_the_cache_returns_the_same_route_and_survives_a_reopen(db, graph):
    cache = RouteCache(db)
    first = cache.route(graph, 1, 7, profile=HIGHSEC)
    stored = cache.get(graph, 1, 7, HIGHSEC, (), 50.0)
    assert stored is not None
    assert stored.systems == first.systems and stored.jumps == first.jumps


def test_an_unknown_route_is_cached_as_unknown_not_as_absent(db, graph):
    """A NO_ROUTE is an expensive search too, and it must not come back known."""
    cache = RouteCache(db)
    cache.route(graph, 1, 10, profile=SHORTEST)
    stored = cache.get(graph, 1, 10, SHORTEST, (), 50.0)
    assert stored is not None and stored.known is False


def test_a_different_avoid_list_or_penalty_is_a_different_key(db, graph):
    cache = RouteCache(db)
    cache.route(graph, 1, 7, profile=SHORTEST)
    assert cache.get(graph, 1, 7, SHORTEST, (3,), 50.0) is None
    assert cache.get(graph, 1, 7, SAFER, (), 50.0) is None
    cache.route(graph, 1, 7, profile=SAFER, safer_penalty=50.0)
    assert cache.get(graph, 1, 7, SAFER, (), 0.0) is None, "the penalty is part of the answer"


def test_a_new_sde_build_cannot_read_the_old_builds_routes(db, graph, synthetic):
    cache = RouteCache(db)
    cache.route(graph, 1, 7, profile=SHORTEST)
    rebuilt = RouteGraph(
        [(left, right) for left, right in synthetic["edges"]],
        {system: security for system, security, _name in synthetic["systems"]},
        sde_build=2,
    )
    assert cache.get(rebuilt, 1, 7, SHORTEST, (), 50.0) is None


def test_a_disabled_cache_stores_nothing(db, graph):
    cache = RouteCache(db, enabled=False)
    assert cache.route(graph, 1, 7, profile=SHORTEST).jumps == 3
    assert db.conn.execute("SELECT COUNT(*) AS n FROM route_cache").fetchone()["n"] == 0


# -- 6. the real map --------------------------------------------------------


@pytest.fixture(scope="module")
def real_graph() -> RouteGraph:
    payload = _load("sde_graph_3478781.json")
    return RouteGraph(
        [(left, right) for left, right in payload["edges"]],
        {system: security for system, security, _region in payload["systems"]},
        sde_build=payload["provenance"]["build"],
    )


def test_the_real_graph_reproduces_the_jita_amarr_route(real_graph):
    """Eleven jumps through Ahbazon — the route the game actually has.

    Fixture provenance: CCP SDE jsonl bundle, build **3478781**, members
    `mapSolarSystems.jsonl` and `mapStargates.jsonl`, acquired 2026-08-25.
    """
    jita, amarr, ahbazon = 30000142, 30002187, 30005196
    facts = real_graph.route(jita, amarr, profile=SHORTEST)
    assert facts.known
    assert facts.jumps == 11
    assert facts.sde_build == 3478781
    assert ahbazon in facts.systems
    # Ahbazon is 0.4 displayed: the shortest route is NOT a high-sec route, and
    # the engine must not round it into one.
    assert facts.min_display_security == pytest.approx(0.4)
    assert facts.lowsec_systems == 1
    assert facts.all_highsec is False


def test_the_high_sec_only_route_to_amarr_is_the_long_way_round(real_graph):
    facts = real_graph.route(30000142, 30002187, profile=HIGHSEC)
    assert facts.known and facts.jumps == 34
    assert facts.all_highsec is True
    assert facts.min_display_security >= 0.5


def test_avoiding_ahbazon_forces_the_long_route_even_on_shortest(real_graph):
    facts = real_graph.route(30000142, 30002187, profile=SHORTEST, avoid=[30005196])
    assert facts.known and facts.jumps > 11


def test_the_real_graph_is_the_whole_gated_map(real_graph):
    """Guard against a fixture that shrank to the tested path."""
    assert real_graph.systems > 5000
