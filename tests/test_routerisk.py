"""Route risk from the killmail lake — a column, never a multiplier (§23.21).

The destruction lead-lag hypothesis failed (§14). The killmail data has one use
the plan never assigned it: hauler losses per system on the route. Everything
here is counted from ingested days; a window nothing was ingested for is
UNKNOWN, never zero.
"""

from __future__ import annotations

from datetime import date

import pytest

from evescreener.routerisk import (
    hauler_type_ids,
    persist_system_losses,
    reduce_system_losses,
    route_risk,
)

JITA, PERIMETER, AMARR = 30000142, 30000144, 30002187
BADGER, BADGER_BP, RIFTER = 648, 983, 587


def _sde(db):
    db.replace_market_groups(
        [
            (204, None, "Ships"),
            (208, 204, "Haulers"),
            (84, 208, "Caldari"),
            (25, 204, "Frigates"),
            (2, None, "Blueprints & Reactions"),
            (3, 2, "Ships"),
            (9, 3, "Haulers"),
        ]
    )
    db.replace_types(
        [
            (BADGER, "Badger", 84, 250000.0, 20000.0, 1),
            (BADGER_BP, "Badger Blueprint", 9, 0.01, 0.01, 1),
            (RIFTER, "Rifter", 25, 27289.0, 2500.0, 1),
        ]
    )


def _killmail(*, time, system, hull):
    return {
        "killmail_id": 1,
        "killmail_time": time,
        "solar_system_id": system,
        "victim": {"ship_type_id": hull, "items": []},
        "attackers": [],
    }


def test_hauler_hulls_are_resolved_by_market_group_ancestry_not_blueprints(db):
    _sde(db)
    hulls = hauler_type_ids(db, ("Haulers",))
    assert hulls == {BADGER}, "the blueprint sits under a Haulers group too, and is not a hull"


def test_losses_are_counted_per_system_and_per_hauler(db):
    _sde(db)
    kills = [
        _killmail(time="2026-08-20T10:00:00Z", system=PERIMETER, hull=BADGER),
        _killmail(time="2026-08-20T11:00:00Z", system=PERIMETER, hull=RIFTER),
        _killmail(time="2026-08-21T10:00:00Z", system=AMARR, hull=BADGER),
    ]
    counts = reduce_system_losses(kills, {BADGER})
    assert counts[(PERIMETER, "2026-08-20")] == [2, 1]
    assert counts[(AMARR, "2026-08-21")] == [1, 1]
    assert persist_system_losses(db, counts) == 2
    # Re-persisting the same day replaces, never doubles.
    assert persist_system_losses(db, counts) == 2
    row = db.conn.execute(
        "SELECT hull_losses, hauler_losses FROM system_losses WHERE solar_system_id=? AND day=?",
        (PERIMETER, "2026-08-20"),
    ).fetchone()
    assert (row["hull_losses"], row["hauler_losses"]) == (2, 1)


def test_a_window_nothing_was_ingested_for_is_unknown_not_zero(db):
    _sde(db)
    risk = route_risk(db, [JITA, PERIMETER, AMARR], days=90, end=date(2026, 8, 28))
    assert risk["known"] is False
    assert risk["hauler_losses"] is None
    assert "ingested" in risk["reason"]


def test_route_risk_sums_the_route_inside_the_window_and_names_coverage(db):
    _sde(db)
    kills = [
        _killmail(time="2026-08-20T10:00:00Z", system=PERIMETER, hull=BADGER),
        _killmail(time="2026-08-20T11:00:00Z", system=PERIMETER, hull=RIFTER),
        _killmail(time="2026-08-21T10:00:00Z", system=AMARR, hull=BADGER),
        _killmail(time="2026-01-01T10:00:00Z", system=AMARR, hull=BADGER),  # outside
    ]
    persist_system_losses(db, reduce_system_losses(kills, {BADGER}))
    for day in ("2026-08-20", "2026-08-21", "2026-01-01"):
        db.conn.execute(
            "INSERT INTO killmail_ingest(source, ingested_at, killmail_count) VALUES(?,?,?)",
            (day, "2026-08-28T00:00:00+00:00", 1),
        )
    risk = route_risk(db, [JITA, PERIMETER, AMARR], days=90, end=date(2026, 8, 28))
    assert risk["known"] is True
    assert risk["days_covered"] == 2
    assert risk["window_days"] == 90
    assert risk["hauler_losses"] == 2
    assert risk["hull_losses"] == 3
    assert risk["per_system"][PERIMETER] == {"hull_losses": 2, "hauler_losses": 1}
    assert risk["per_system"][JITA] == {"hull_losses": 0, "hauler_losses": 0}
    assert "multiplier" in risk["note"]


def test_the_attachment_puts_the_column_on_the_plan_without_ranking_it(config, db):
    from evescreener.routerisk import route_risk_attachment
    from test_loops import AMARR as AMARR_STATION
    from test_loops import JITA as JITA_STATION
    from test_loops import _plan

    _sde(db)
    plan = _plan(1, "A", JITA_STATION, AMARR_STATION, cost=1.0, net=1.0, haul_jumps=2)
    attach = route_risk_attachment(config, db, end=date(2026, 8, 28))
    attached = attach(plan)
    assert attached.route_risk["known"] is False
    assert attached.rank_score == plan.rank_score
    assert attached.isk_per_active_minute == plan.isk_per_active_minute


@pytest.mark.parametrize("names", [(), ("No Such Group",)])
def test_no_hauler_groups_means_no_hauler_hulls(db, names):
    _sde(db)
    assert hauler_type_ids(db, names) == set()
