"""The HAULING page's H7 controls and columns, offscreen (§23.21).

New on the control strip: a minimum quantity and a hide-BELOW switch, both
default off and both part of the job input. New on the table: a persistence
column and a route-loss column, blank when unmeasured. New in the drawer: the
loops pane. New on the stamp: the pair-level refusals, so a 30-minute session
that drops every Jita ↔ Amarr pair before pricing says so on screen.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pytestqt")
pytestmark = pytest.mark.gui

from evescreener.gui.data import DeskData  # noqa: E402
from evescreener.gui.pages.hauling import HEADERS, HaulingPage  # noqa: E402
from evescreener.store.lake import DEPTH_COLUMNS, DepthLake  # noqa: E402

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
FORGE, DOMAIN = 10000002, 10000043
JITA_44, AMARR_8 = 60003760, 60008494
JITA, AMARR = 30000142, 30002187


def _depth_rows(*, region, station, side, levels, sweep):
    rows = []
    cumulative_qty = cumulative_notional = 0.0
    for price, qty in levels:
        cumulative_qty += qty
        cumulative_notional += price * qty
        rows.append(
            {
                "region_id": region,
                "sweep_ts": sweep,
                "fetched_at": sweep,
                "expires_ts": None,
                "execution_location_id": station,
                "type_id": 34,
                "side": side,
                "price": price,
                "level_qty": qty,
                "cumulative_qty": cumulative_qty,
                "cumulative_notional": cumulative_notional,
                "level_order_count": 1,
                "min_volume_excluded_qty": 0.0,
                "oldest_issued": "2026-08-20T00:00:00Z",
                "newest_issued": "2026-08-24T00:00:00Z",
                "structure_share": 0.0,
                "depth_complete": True,
            }
        )
    return pd.DataFrame(rows, columns=DEPTH_COLUMNS)


@pytest.fixture
def haul_desk(config, db, monkeypatch):
    from evescreener import books, hauling

    config.paths.ensure()
    db.replace_types([(34, "Tritanium", 18, 0.01, 0.01, 1)])
    db.replace_solar_systems([(JITA, FORGE, "Jita", 0.9459), (AMARR, DOMAIN, "Amarr", 0.949)])
    db.replace_stargates([(1, JITA, AMARR), (2, AMARR, JITA)])
    db.replace_npc_stations(
        [(JITA_44, JITA, 1000035, 14, None), (AMARR_8, AMARR, 1000086, 32, None)]
    )
    db.set_meta("sde_build", "3478781")
    lake = DepthLake(config.paths)
    for sweep in ("2026-08-25T10:50:00+00:00", "2026-08-25T11:50:00+00:00"):
        lake.write(
            _depth_rows(
                region=FORGE,
                station=JITA_44,
                side="sell",
                levels=[(100_000.0, 800.0), (107_250.0, 400.0)],
                sweep=sweep,
            )
        )
        lake.write(
            _depth_rows(
                region=DOMAIN,
                station=AMARR_8,
                side="buy",
                levels=[(120_000.0, 500.0), (115_500.0, 700.0)],
                sweep=sweep,
            )
        )
    monkeypatch.setattr(books, "utcnow", lambda: NOW)
    monkeypatch.setattr(hauling, "utcnow", lambda: NOW)
    return DeskData(
        config=config,
        db=db,
        region_id=FORGE,
        loaded_at=NOW,
        bars=pd.DataFrame(),
        all_bars=pd.DataFrame(),
        input_key=("hauling", 1),
    )


def _page(qtbot, desk, **controls):
    page = HaulingPage(desk)
    qtbot.addWidget(page)
    page.cargo.setValue(controls.get("cargo", 60_000.0))
    page.capital.setValue(controls.get("capital", 250.0))
    page.exposure.setValue(controls.get("exposure", 250.0))
    page.security.setCurrentText(controls.get("security", "shortest"))
    page.minutes.setValue(controls.get("minutes", 600))
    page.origin.setText(controls.get("origin", "Jita"))
    return page


def _run(qtbot, page):
    page.ensure_current(force=True)
    qtbot.waitUntil(lambda: page._running_token is None, timeout=60_000)
    return page._result


def test_the_new_controls_are_in_the_job_input_and_default_off(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    controls = dict(page.job_input())
    assert controls["min_quantity"] == 0.0
    assert controls["hide_below"] is False
    page.min_quantity.setValue(5.0)
    page.hide_below.setChecked(True)
    controls = dict(page.job_input())
    assert controls["min_quantity"] == 5.0
    assert controls["hide_below"] is True


def test_the_table_has_persistence_and_loss_columns(qtbot, haul_desk):
    assert "persist" in HEADERS and "losses" in HEADERS
    page = _page(qtbot, haul_desk)
    result = _run(qtbot, page)
    assert result["scan"].plans
    persist = HEADERS.index("persist")
    losses = HEADERS.index("losses")
    # One prior generation against a minimum of three: UNKNOWN, rendered blank.
    assert page.table.item(0, persist).text() in ("—", "1/3?", "UNKNOWN") or "?" in (
        page.table.item(0, persist).text()
    )
    assert page.table.item(0, losses).text() != ""


def test_the_loops_pane_exists_and_the_stamp_counts_pairs(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    assert "loops" in page.panes
    _run(qtbot, page)
    assert "no loop" in page.panes["loops"].toPlainText().lower()
    assert "pair" in page.stamp.text()


def test_hide_below_withholds_and_the_summary_says_so(qtbot, haul_desk):
    haul_desk.db.conn.execute(
        "INSERT OR REPLACE INTO universe(type_id, region_id, first_seen, last_seen, tier)"
        " VALUES(?, ?, ?, ?, ?)",
        (34, FORGE, "2026-08-01", "2026-08-25", "BELOW"),
    )
    page = _page(qtbot, haul_desk)
    page.hide_below.setChecked(True)
    result = _run(qtbot, page)
    assert result["scan"].plans == []
    assert result["scan"].withheld_by_filter == {"BADGE_BELOW": 1}
    assert "withheld" in page.summary.text()


def test_the_persistent_objective_is_offered(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    offered = [page.objective.itemText(i) for i in range(page.objective.count())]
    assert "persistent_isk_per_active_minute" in offered
