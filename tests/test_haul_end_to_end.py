"""One sweep to one painted row (plan.md §23.17).

This is the test that would catch a seam. Synthetic ESI pages for two regions
go in at the top; a governed sweep reduces them; the depth lake writes and the
validator reads them back; the engine ranks; the report renders; and the desk
page paints — and **the same 13,196,312.50 ISK** has to survive all six steps.

Every number here was written into `plan.md` §23.17 before any of the code
existed. If a refactor ever moves one of them, this fails in whichever step
moved it rather than in the operator's evening.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.gui

from evescreener import books as books_module  # noqa: E402
from evescreener import hauling as hauling_module  # noqa: E402
from evescreener.books import (  # noqa: E402
    DepthBound,
    depth_stations,
    load_validated_depth,
    sweep_region,
)
from evescreener.hauling import HaulProfile, ShipProfile, scan_hauls, scan_inputs  # noqa: E402
from evescreener.haulreport import build_haul_report, render_haul_report  # noqa: E402
from evescreener.routes import RouteGraph  # noqa: E402
from evescreener.store.lake import BookLake, DepthLake  # noqa: E402

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
FORGE, DOMAIN = 10000002, 10000043
JITA_44, AMARR_8 = 60003760, 60008494
JITA, AMARR = 30000142, 30002187


@pytest.fixture(scope="module")
def worked() -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / "haul_worked_example.json").read_text(
            encoding="utf-8"
        )
    )


def _pages(levels, *, buy, station, system):
    return [
        {
            "order_id": 5000 + index,
            "type_id": 34,
            "price": float(price),
            "volume_remain": float(qty),
            "volume_total": float(qty),
            "min_volume": 1,
            "is_buy_order": buy,
            "location_id": station,
            "system_id": system,
            "range": "station" if buy else None,
            "duration": 90,
            "issued": "2026-08-21T09:00:00Z",
        }
        for index, (price, qty) in enumerate(levels)
    ]


def _sweep(config, db, region, rows, stations):
    from evescreener.esi.client import EsiClient

    def handler(_request):
        return httpx.Response(
            200,
            json=rows,
            headers={"expires": "Thu, 27 Aug 2026 03:00:00 GMT", "x-pages": "1"},
        )

    client = EsiClient(
        config,
        db,
        client=httpx.AsyncClient(
            base_url=config.esi.base_url, transport=httpx.MockTransport(handler)
        ),
    )
    try:
        return asyncio.run(
            sweep_region(
                config,
                client,
                BookLake(config.paths.ensure()),
                region,
                depth_lake=DepthLake(config.paths),
                stations=stations,
                bound=DepthBound(max_capital_isk=1e10, max_cargo_m3=1e6, safety_margin=1.5),
                jump_distance=RouteGraph.from_db(db).jump_distance,
            )
        )
    finally:
        asyncio.run(client.aclose())


def test_a_sweep_becomes_a_ranked_plan_a_report_and_a_painted_row(
    qtbot, config, db, worked, monkeypatch
):
    expected = worked["expected"]
    config.paths.ensure()
    db.replace_types([(34, "Tritanium", 18, 0.01, 0.01, 1)])
    db.replace_solar_systems([(JITA, FORGE, "Jita", 0.9459), (AMARR, DOMAIN, "Amarr", 0.949)])
    db.replace_stargates([(1, JITA, AMARR), (2, AMARR, JITA)])
    db.replace_npc_stations(
        [(JITA_44, JITA, 1000035, 14, None), (AMARR_8, AMARR, 1000086, 32, None)]
    )
    db.set_meta("sde_build", "3478781")

    # 1. two governed sweeps, each reduced twice from the same pages.
    forge = _sweep(
        config,
        db,
        FORGE,
        _pages(worked["source"]["asks"], buy=False, station=JITA_44, system=JITA),
        depth_stations(config, db, FORGE),
    )
    domain = _sweep(
        config,
        db,
        DOMAIN,
        _pages(worked["destination"]["bids"], buy=True, station=AMARR_8, system=AMARR),
        depth_stations(config, db, DOMAIN),
    )
    assert forge.complete and domain.complete
    assert forge.depth is not None and domain.depth is not None

    # The clock has to be inside the staleness budget for the generations we
    # just wrote, which are stamped with the real time of this test run.
    monkeypatch.setattr(books_module, "utcnow", lambda: datetime.now(UTC))
    monkeypatch.setattr(hauling_module, "utcnow", lambda: datetime.now(UTC))
    for region in (FORGE, DOMAIN):
        assert load_validated_depth(config, region).known, f"region {region} must price"

    # 2. the scan.
    sources, destinations, depths, graph, names, badges, packaged = scan_inputs(config, db)
    profile = HaulProfile.from_config(
        config,
        ship=ShipProfile(name="e2e", usable_cargo_m3=60_000.0, handling_minutes=5.0),
        current_system=JITA,
        capital_isk=5e9,
        max_exposure_isk=5e9,
        session_minutes=600.0,
        security_profile="shortest",
    )
    scan = scan_hauls(
        config,
        profile,
        stations=sources,
        destinations=destinations,
        depths=depths,
        graph=graph,
        names=names,
        badges=badges,
        packaged_volume=packaged,
    )
    assert scan.plans, f"expected a plan; rejections were {scan.rejection_counts}"
    plan = scan.plans[0]
    assert plan.quantity == pytest.approx(worked["quantity"])
    assert plan.source_wap == pytest.approx(expected["source_wap"])
    assert plan.dest_wap == pytest.approx(expected["destination_wap"])
    assert plan.sales_tax_isk == pytest.approx(expected["sales_tax_isk"])
    assert plan.net_profit == pytest.approx(expected["net_profit"])
    assert plan.net_roi_pct == pytest.approx(expected["net_roi_pct"])
    assert plan.type_name == "Tritanium"
    assert plan.source.label.startswith("Jita")
    assert plan.haul.jumps == 1 and plan.haul.sde_build == 3478781

    # 3. the report.
    report = build_haul_report(scan, config=config)
    row = report["rows"][0]
    assert row["net_profit"] == pytest.approx(expected["net_profit"])
    assert row["audit"]["fees"]["sales_tax_isk"] == pytest.approx(expected["sales_tax_isk"])
    assert row["audit"]["walks"]["source_levels_consumed"] == 2
    assert row["audit"]["generations"]["source"][0] == FORGE
    assert "13,196,312" in render_haul_report(report).replace("13,196,313", "13,196,312")

    # 4. the page.
    import pandas as pd

    from evescreener.gui.data import DeskData
    from evescreener.gui.pages.hauling import HaulingPage

    desk = DeskData(
        config=config,
        db=db,
        region_id=FORGE,
        loaded_at=NOW,
        bars=pd.DataFrame(),
        all_bars=pd.DataFrame(),
        input_key=("e2e", 1),
    )
    page = HaulingPage(desk)
    qtbot.addWidget(page)
    page.cargo.setValue(60_000.0)
    page.capital.setValue(5000.0)
    page.exposure.setValue(5000.0)
    page.minutes.setValue(600)
    page.security.setCurrentText("shortest")
    page.origin.setText("Jita")
    page.ensure_current(force=True)
    qtbot.waitUntil(lambda: page._running_token is None, timeout=60_000)

    painted = page._result["scan"].plans
    assert painted, "the page must paint the same plan the CLI ranked"
    assert painted[0].net_profit == pytest.approx(expected["net_profit"])
    texts = [page.table.item(0, column).text() for column in range(page.table.columnCount())]
    assert any("Tritanium" in text for text in texts)
    assert any("1,200" in text for text in texts)
    page.table.selectRow(0)
    assert "102,416.67" in page.panes["ladders"].toPlainText()
    assert "117,375.00" in page.panes["ladders"].toPlainText()
