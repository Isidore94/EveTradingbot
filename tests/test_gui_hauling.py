"""The HAULING page, offscreen (plan.md §23.11).

The page is a shell: it captures the operator's constraints on the GUI thread,
hands them to a worker as an immutable tuple, and paints what comes back. So
what is tested here is the shell's contract rather than the arithmetic — which
lives in `test_hauling.py` and is checked against §23.17's frozen numbers.

The properties that matter: `build()` computes nothing, an UNKNOWN pair renders
**with its reason** instead of vanishing, blanks sort to the bottom in both
directions, the rejected view names its reason codes, and a stale generation is
visible on the page rather than implied by an empty table.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pytestqt")
pytestmark = pytest.mark.gui

from evescreener.gui.data import DeskData  # noqa: E402
from evescreener.gui.pages.hauling import HaulingPage  # noqa: E402
from evescreener.store.lake import DEPTH_COLUMNS, DepthLake  # noqa: E402

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
FORGE, DOMAIN = 10000002, 10000043
JITA_44, AMARR_8 = 60003760, 60008494
JITA, AMARR = 30000142, 30002187


def _depth_rows(*, region, station, side, levels, sweep):
    rows = []
    cumulative_qty = 0.0
    cumulative_notional = 0.0
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
    """A desk whose lake holds two hub generations and a two-system map."""
    from evescreener import books, hauling

    config.paths.ensure()
    db.replace_types([(34, "Tritanium", 18, 0.01, 0.01, 1)])
    db.replace_solar_systems([(JITA, FORGE, "Jita", 0.9459), (AMARR, DOMAIN, "Amarr", 0.949)])
    db.replace_stargates([(1, JITA, AMARR), (2, AMARR, JITA)])
    db.replace_npc_stations(
        [(JITA_44, JITA, 1000035, 14, None), (AMARR_8, AMARR, 1000086, 32, None)]
    )
    db.set_meta("sde_build", "3478781")

    sweep = "2026-08-25T11:50:00+00:00"
    lake = DepthLake(config.paths)
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
    # The engine reads "now" from the clock when a snapshot is validated, so
    # the fixture's generation has to be young in real time rather than in
    # 2026 — hence a patched clock rather than a hand-built snapshot.
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


# -- 1. the shell's contract -----------------------------------------------


def test_building_the_page_runs_no_scan(qtbot, haul_desk, monkeypatch):
    """`build()` lays out widgets. The window builds every page at startup."""
    import evescreener.gui.pages.hauling as module

    called = []
    monkeypatch.setattr(module, "scan_hauls", lambda *a, **k: called.append(1))
    page = HaulingPage(haul_desk)
    qtbot.addWidget(page)
    assert called == []
    assert page.heavy is True


def test_the_job_input_is_an_immutable_snapshot_of_the_controls(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    captured = page.job_input()
    assert isinstance(captured, tuple)
    before = page.job_input()
    page.capital.setValue(999.0)
    assert page.job_input() != before, "a control change is a different generation"


def test_the_control_strip_exposes_maker_exit_and_its_wait_cap(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    page.exit_model.setCurrentText("maker")
    page.max_wait_days.setValue(1.5)
    controls = dict(page.job_input())
    assert controls["exit_model"] == "maker"
    assert controls["max_wait_days"] == 1.5


def test_the_control_state_is_remembered_in_state_db_not_config_toml(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    page.origin.setText("Jita")
    page._save_filters()
    reopened = HaulingPage(haul_desk)
    qtbot.addWidget(reopened)
    assert reopened.origin.text() == "Jita"
    assert haul_desk.db.get_meta("hauling.filters")


# -- 2. what it paints -----------------------------------------------------


def test_a_priced_plan_reaches_the_table_and_the_drawer(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    result = _run(qtbot, page)
    scan = result["scan"]
    assert scan.plans, f"expected a plan; rejections: {scan.rejection_counts}"
    assert page.table.rowCount() >= 1

    page.table.selectRow(0)
    ladders = page.panes["ladders"].toPlainText()
    assert "SOURCE" in ladders and "DESTINATION" in ladders
    assert "min_volume-blocked depth" in ladders
    assert "UNVERIFIED" in ladders, "the order-age caveat rides on the row"
    assert "chosen" in page.panes["why this size"].toPlainText()
    assert "sales tax" in page.panes["costs"].toPlainText()
    assert "Jita" in page.panes["route"].toPlainText()


def test_the_page_shows_both_generation_ages_and_the_sde_build(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    _run(qtbot, page)
    stamp = page.stamp.text()
    assert "region 10000002" in stamp and "region 10000043" in stamp
    assert "SDE build 3478781" in stamp


def test_a_stale_generation_renders_unknown_rows_with_their_reason(qtbot, haul_desk, monkeypatch):
    from evescreener import books

    monkeypatch.setattr(books, "utcnow", lambda: datetime(2026, 8, 26, 12, 0, tzinfo=UTC))
    page = _page(qtbot, haul_desk)
    _run(qtbot, page)
    assert page._result["scan"].plans == []
    texts = [
        page.table.item(row, column).text()
        for row in range(page.table.rowCount())
        for column in range(page.table.columnCount())
    ]
    assert any("UNKNOWN" in text for text in texts)
    assert any("STALE" in text for text in texts), "the reason is on the row, not in a log"
    assert "STALE_BOOK" in page.panes["rejected"].toPlainText()


def test_the_rejected_pane_names_its_reason_codes(qtbot, haul_desk):
    page = _page(qtbot, haul_desk, cargo=1.0)
    _run(qtbot, page)
    rejected = page.panes["rejected"].toPlainText()
    assert "OVER_CARGO" in rejected
    assert "m³ of hold" in rejected
    assert "denominator" in rejected


def test_blanks_sort_to_the_bottom_both_ways_on_this_page(qtbot, haul_desk, monkeypatch):
    """An UNKNOWN pair must not float to the top of an ascending sort."""
    from evescreener import books

    monkeypatch.setattr(books, "utcnow", lambda: datetime(2026, 8, 26, 12, 0, tzinfo=UTC))
    page = _page(qtbot, haul_desk)
    _run(qtbot, page)
    column = 4  # net profit
    page.table.sort_by(column, descending=False)
    assert page.table.blank_rows_are_last()
    page.table.sort_by(column, descending=True)
    assert page.table.blank_rows_are_last()


def test_the_nearest_first_preset_sorts_on_pickup(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    _run(qtbot, page)
    page.nearest.click()
    assert page.table._sort_column == 7


def test_an_unresolvable_system_name_is_reported_rather_than_ignored(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    page.origin.setText("Jitta")
    _run(qtbot, page)
    assert any("no solar system named" in note for note in page._result["scan"].notes)
    assert "no solar system named" in page.summary.text()
    assert page._result["scan"].plans == []


def test_a_blank_current_system_refuses_to_price_pickup_as_zero(qtbot, haul_desk):
    page = _page(qtbot, haul_desk, origin="")
    result = _run(qtbot, page)
    assert result["scan"].plans == []
    assert result["scan"].rejected_for("NO_ROUTE")
    assert "current system" in page.summary.text()


# -- 3. the desk cannot paint a generation the lake has replaced ------------


def test_the_input_key_moves_when_a_depth_partition_changes(config, tmp_path):
    """A page that reads a file the key does not watch keeps painting a
    generation that no longer exists (§22 S3)."""
    from evescreener.gui.data import desk_input_key

    config.paths.ensure()
    first = desk_input_key(config, FORGE, root=tmp_path)
    partition = config.paths.depth_partition(FORGE, "2026-08-25")
    partition.parent.mkdir(parents=True, exist_ok=True)
    partition.write_bytes(b"a new depth generation")
    assert desk_input_key(config, FORGE, root=tmp_path) != first


def test_the_input_key_moves_when_a_hauling_report_is_written(config, tmp_path):
    from evescreener.gui.data import desk_input_key

    config.paths.ensure()
    first = desk_input_key(config, FORGE, root=tmp_path)
    (config.paths.reports / "hauling-20260825T120000Z.json").write_text("{}", encoding="utf-8")
    assert desk_input_key(config, FORGE, root=tmp_path) != first


# -- 4. the boundary the page must never cross -----------------------------


def test_no_gui_module_imports_the_freight_comparison():
    """`haulfreight` reaches PushX through `crossregion`, which imports `httpx`
    at module scope. The page is allowed to show a freight column from a
    stored report; it is not allowed to be able to fetch one (§19.2)."""
    import ast
    import pathlib

    gui = pathlib.Path(__file__).resolve().parents[1] / "src" / "evescreener" / "gui"
    offenders = []
    for path in gui.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if "haulfreight" in name or "crossregion" in name:
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert offenders == [], "the desk must not be able to reach a third-party API: " + "; ".join(
        offenders
    )


# -- 5. a control strip is dragged, not clicked once ----------------------


def test_rapid_control_changes_launch_one_job_not_five(qtbot, haul_desk):
    """A spin box emits on every step. Without a debounce, dragging capital
    from 250 to 254 dispatched five scans: the token guard discarded four
    results, but four jobs had already burned the four-thread pool."""
    page = _page(qtbot, haul_desk)
    launched = []
    page.ensure_current = lambda **kwargs: launched.append(kwargs)

    for value in (251.0, 252.0, 253.0, 254.0, 255.0):
        page.capital.setValue(value)
    assert launched == [], "nothing may start while the operator is still typing"

    qtbot.wait(page.DEBOUNCE_MS + 250)
    assert len(launched) == 1, f"one scan for one settled edit, got {len(launched)}"


def test_the_debounce_still_lets_a_single_change_through(qtbot, haul_desk):
    page = _page(qtbot, haul_desk)
    launched = []
    page.ensure_current = lambda **kwargs: launched.append(kwargs)
    page.security.setCurrentText("safer")
    qtbot.wait(page.DEBOUNCE_MS + 250)
    assert len(launched) == 1


def test_the_drawer_marks_the_refused_size_as_refused(qtbot, haul_desk):
    """The audit keeps the size the ranker stopped at; the drawer must say so."""
    from dataclasses import replace

    from evescreener.gui.pages.hauling import _why_text
    from test_positioning import _plan

    plan = _plan(34, "Tritanium", [(100.0, 1000.0, 500.0), (200.0, 3000.0, 400.0)])
    text = _why_text(replace(plan, quantity=100.0, rank_score=50.0))
    assert "<- chosen" in text
    assert "<- refused (marginal <= 0)" in text


def test_a_fresh_install_is_told_its_cargo_is_unbounded(qtbot, haul_desk):
    """No saved profile, spin at its 0 default: the scan has no hold at all."""
    page = _page(qtbot, haul_desk, cargo=0.0)
    result = _run(qtbot, page)
    assert result["scan"].profile.ship.usable_cargo_m3 == 0.0
    assert any("cargo is unbounded" in note for note in result["scan"].notes)
    assert "cargo is unbounded" in page.summary.text()


def test_the_cargo_box_overrides_the_ship_profile_only_when_it_is_set(qtbot, haul_desk):
    """The spin box defaulted to 60,000 m³, which silently overrode whatever
    hold the selected ship actually has — the picker looked live and was not."""
    from evescreener.timeutil import iso, utcnow

    haul_desk.db.put_haul_profile(
        {
            "name": "Bestower",
            "usable_cargo_m3": 6_000.0,
            "ehp": None,
            "ship_value_isk": None,
            "seconds_per_jump": 55.0,
            "handling_minutes": 4.0,
            "created_at": iso(utcnow()),
        }
    )
    page = HaulingPage(haul_desk)
    qtbot.addWidget(page)
    assert page.cargo.value() == 0.0, "the default must defer to the ship"
    assert "ship profile" in page.cargo.specialValueText()

    page.capital.setValue(250.0)
    page.exposure.setValue(250.0)
    page.security.setCurrentText("shortest")
    page.minutes.setValue(600)
    result = _run(qtbot, page)
    assert result["scan"].profile.ship.usable_cargo_m3 == 6_000.0
    assert result["ship"] == "Bestower"

    page.cargo.setValue(60_000.0)
    overridden = _run(qtbot, page)
    assert overridden["scan"].profile.ship.usable_cargo_m3 == 60_000.0
