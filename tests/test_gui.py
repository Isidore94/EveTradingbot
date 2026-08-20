"""The desk (plan.md §19 Part 2). Offscreen, offline, on fixture data.

These tests are about **wiring and rules**, not arithmetic — the arithmetic is
tested where it lives. What is asserted here is what could only break in the
Qt layer: every page opens, the board's blanks stay at the bottom, the chart
is one window that re-points, Focus never auto-removes, the banner reaches
both pages that must carry it, and a Paper Buy from the desk lands in the same
ledger with the same refusals as the CLI.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

# QtWidgets, not the bare package: a PySide6 whose shared libraries cannot
# load (a headless box without GL) must SKIP here, never crash collection.
pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pytestqt")
pytestmark = pytest.mark.gui

from evescreener.gui.data import DeskData  # noqa: E402
from evescreener.gui.widgets import SortableTable  # noqa: E402

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def lake(type_ids, *, bars=200, seed=3):
    rng = np.random.default_rng(seed)
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    rows = []
    for offset, type_id in enumerate(type_ids):
        close = 100.0 * (offset + 1) * np.exp(np.cumsum(rng.normal(0.0, 0.02, bars)))
        for position, stamp in enumerate(stamps):
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": close[position] * 1.02,
                    "low": close[position] * 0.98,
                    "close": close[position],
                    "volume": 50_000.0,
                    "order_count": 40,
                    "isk_value": close[position] * 50_000.0,
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    return pd.DataFrame(rows)


def book(*, sweep="2026-08-20T11:58:00+00:00", type_ids=(600,)):
    rows = []
    for type_id in type_ids:
        for side, best, fill in (("sell", 105.0, 106.0), ("buy", 95.0, 94.0)):
            row = {
                "type_id": type_id,
                "region_id": 10000002,
                "side": side,
                "sweep_ts": sweep,
                "expires_ts": None,
                "best_price": best,
                "total_volume": 1e9,
                "order_count": 20,
                "p5_price": best,
                "top_order_volume_share": 0.05,
                "station_volume_share": 1.0,
                "partial_sweep": False,
            }
            for index in range(3):
                row[f"depth_fill_price_{index}"] = fill if index == 0 else None
                row[f"depth_fill_qty_{index}"] = 10_000_000.0 if index == 0 else None
            rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def desk(config, db):
    """A DeskData built by hand — no disk crawl, no network, no ESI client."""
    db.replace_market_groups([(4, None, "Ships"), (100, 4, "Cruisers")])
    db.replace_types([(600 + n, f"Thing {n}", 100, 1.0, 1.0, 1) for n in range(6)])
    frame = lake(range(600, 606))
    db.conn.execute(
        "INSERT INTO universe(type_id, region_id, first_seen, last_seen, tier, tracked,"
        " median_unit_volume, source) VALUES(600, 10000002, 'x', 'y', 'THIN', 1, 400, 't')"
    )
    for type_id in range(601, 606):
        db.conn.execute(
            "INSERT INTO universe(type_id, region_id, first_seen, last_seen, tier, tracked,"
            " median_unit_volume, source) VALUES(?, 10000002, 'x', 'y', 'OK', 1, 50000, 't')",
            (type_id,),
        )
    return DeskData(
        config=config,
        db=db,
        region_id=10000002,
        loaded_at=NOW,
        bars=frame,
        all_bars=frame,
        book=book(type_ids=range(600, 606)),
        tiers={600: "THIN", **{n: "OK" for n in range(601, 606)}},
    )


# -- every page opens -------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    ["MARKET", "CHARTS", "BOARD", "FOCUS", "SCANNER", "PAPER", "LEARNING", "HEALTH"],
)
def test_every_page_opens_on_fixture_data(qtbot, desk, title):
    from evescreener.gui.pages import PAGES

    factory = dict(PAGES)[title]
    page = factory(desk)
    qtbot.addWidget(page)
    assert page.title == title


def test_the_window_registers_all_eight_pages_in_priority_order(qtbot, desk, config):
    from evescreener.gui.app import DeskWindow

    window = DeskWindow(config, data=desk)
    qtbot.addWidget(window)
    assert list(window.pages) == [
        "MARKET",
        "CHARTS",
        "BOARD",
        "FOCUS",
        "SCANNER",
        "PAPER",
        "LEARNING",
        "HEALTH",
    ]
    window.timer.stop()


# -- the board idiom --------------------------------------------------------


def test_blanks_sort_to_the_bottom_whichever_way_the_column_is_sorted(qtbot):
    """A blank is a value we could not measure, not a zero and not a minimum."""
    table = SortableTable(["name", "value"])
    qtbot.addWidget(table)
    table.set_rows(
        [
            [("A", "A"), ("1.00", 1.0)],
            [("B", "B"), ("—", None)],
            [("C", "C"), ("3.00", 3.0)],
            [("D", "D"), ("—", None)],
        ]
    )
    table.sort_by(1, descending=False)
    assert table.blank_rows_are_last()
    assert table.item(0, 0).text() == "A", "smallest measured value first, ascending"

    table.sort_by(1, descending=True)
    assert table.blank_rows_are_last(), "descending must not float blanks to the top"
    assert table.item(0, 0).text() == "C", "largest measured value first, descending"
    assert {table.item(2, 0).text(), table.item(3, 0).text()} == {"B", "D"}


def test_sorting_never_refetches(qtbot, desk, monkeypatch):
    from evescreener.gui.pages.board import BoardPage

    page = BoardPage(desk)
    qtbot.addWidget(page)
    calls = []
    monkeypatch.setattr(page, "repopulate", lambda: calls.append(1))
    page.table.horizontalHeader().sectionClicked.emit(2)
    assert calls == [], "a header click is a view operation, never a reload"


def settled(qtbot, page, timeout=120_000):
    """Bring a page current and wait for the worker, if it has one.

    Heavy pages no longer compute in `build()` (§19.2 amended), so a test that
    constructs one and reads its table is asserting against an empty widget.
    This is how a test says "now do the work".
    """
    page.ensure_current(force=True)
    if page.heavy:
        qtbot.waitUntil(lambda: page._running_token is None, timeout=timeout)
    return page


def test_the_board_prints_friction_and_never_hides_a_row(qtbot, desk):
    from evescreener.gui.pages.board import BoardPage

    page = BoardPage(desk)
    qtbot.addWidget(page)
    settled(qtbot, page)
    assert page.table.rowCount() > 0
    assert "friction %" in [
        page.table.horizontalHeaderItem(index).text() for index in range(page.table.columnCount())
    ]
    assert "never" in page.caption.text()


def test_the_thin_badge_reaches_the_board(qtbot, desk):
    from evescreener.gui.pages.board import BoardPage

    page = BoardPage(desk)
    qtbot.addWidget(page)
    settled(qtbot, page)
    badges = set()
    for index in range(page.table.rowCount()):
        payload = page.table.payload(index)
        if payload:
            badges.add(payload.get("tier"))
    assert "THIN" in badges


# -- one chart window, re-pointed -------------------------------------------


def test_the_chart_is_one_window_that_repoints(qtbot, desk, config):
    from evescreener.gui.app import DeskWindow

    window = DeskWindow(config, data=desk)
    qtbot.addWidget(window)
    window.timer.stop()
    charts = window.pages["CHARTS"]
    before = id(charts.panel)
    window.chart_type(601)
    assert charts.current == 601
    window.chart_type(602)
    assert charts.current == 602
    assert id(charts.panel) is not None and id(charts.panel) == before, (
        "re-pointing must reuse the panel, never build a second one"
    )


def test_charting_from_the_board_reaches_the_chart_page(qtbot, desk, config):
    from evescreener.gui.app import DeskWindow

    window = DeskWindow(config, data=desk)
    qtbot.addWidget(window)
    window.timer.stop()
    board = window.pages["BOARD"]
    board.chart_requested.emit(603)
    assert window.pages["CHARTS"].current == 603
    assert window.rail.currentItem().text() == "CHARTS"


def test_the_chart_draws_no_synthesized_open(qtbot, desk):
    """The body is the measured range; the bar contract has no open (§4)."""
    from evescreener.gui.chart import build_series

    series = build_series(desk, 601)
    assert series.known
    assert not hasattr(series, "open")
    assert series.high is not None and series.low is not None
    # Body = low..high, notch = close (the ESI average). All three are measured.
    assert len(series.high) == len(series.low) == len(series.close)


def test_candles_colour_against_the_previous_average_not_an_open():
    """Up/down compares two averages, both of which are measured numbers."""
    import numpy as np

    from evescreener.gui.chart import DOWN_COLOUR, FLAT_COLOUR, UP_COLOUR, bar_colours

    close = np.array([100.0, 110.0, 110.0, 90.0, np.nan, 95.0], dtype="float64")
    assert bar_colours(close) == [
        FLAT_COLOUR,  # nothing behind the first bar: no direction to report
        UP_COLOUR,
        FLAT_COLOUR,  # unchanged average is not a direction either
        DOWN_COLOUR,
        FLAT_COLOUR,  # a missing average is uncertainty, never a colour
        UP_COLOUR,  # ... and the next bar compares against the last real one
    ]
    assert bar_colours(np.array([], dtype="float64")) == []


def test_zooming_slices_every_overlay_with_price(qtbot, desk):
    """A window is a view. Any array left unsliced would drift out of step."""
    from evescreener.gui.chart import build_series

    series = build_series(desk, 601)
    window = series.tail(5)
    assert len(window.close) == 5
    for array in (window.high, window.low, window.volume):
        assert array is not None and len(array) == 5
    for values in window.moving.values():
        assert len(values) == 5
    assert len(window.stamps) == 5
    if window.cloud is not None:
        assert len(window.cloud[0]) == len(window.cloud[1]) == 5
    # The last bar of a window is still the last bar of the series.
    assert window.close[-1] == series.close[-1]
    # Asking for more than exists, or for nothing, returns the series itself.
    assert series.tail(10_000) is series and series.tail(0) is series


def test_dense_windows_fall_back_rather_than_smear_into_a_block(qtbot, desk):
    """Below a resolvable slot width the candle degrades; it never renders as a mass."""
    from PySide6.QtCore import QSize

    from evescreener.gui.chart import (
        CANDLE_BODY_SLOT,
        CANDLE_MIN_SLOT,
        CANDLE_NOTCH_SLOT,
        ChartCanvas,
        build_series,
    )

    assert 0 < CANDLE_MIN_SLOT < CANDLE_BODY_SLOT < CANDLE_NOTCH_SLOT
    canvas = ChartCanvas()
    qtbot.addWidget(canvas)
    canvas.set_series(build_series(desk, 601))
    for visible in (0, 60, 250):
        canvas.visible = visible
        for size in (QSize(240, 400), QSize(1600, 900)):
            canvas.resize(size)
            canvas.grab()  # paints through every density regime without raising


def test_the_chart_opens_on_the_whole_series(qtbot, desk):
    """The operator reads this on a 4K pane, where the full history resolves."""
    from evescreener.gui.chart import DEFAULT_VISIBLE_BARS, ChartPanel, build_series

    assert DEFAULT_VISIBLE_BARS == 0, "0 means every bar"
    panel = ChartPanel()
    qtbot.addWidget(panel)
    assert panel.canvas.visible == 0
    assert panel.zoom.currentData() == 0, "the selector must agree with the canvas"
    panel.show_series(build_series(desk, 601))
    panel.zoom.setCurrentIndex(0)  # "60"
    assert panel.canvas.visible == 60


def test_an_index_is_drawn_as_a_line_because_it_has_no_range(qtbot, desk):
    """`composite.py` builds high == low == close; candles there are dashes."""
    import numpy as np

    from evescreener.gui.chart import ChartSeries

    level = np.array([100.0, 101.0, 99.0, 102.0], dtype="float64")
    index = ChartSeries(type_id=0, type_name="FORGE", close=level, high=level, low=level)
    assert index.known
    assert not index.ranged, "an index level is one number a day, not a range"

    real = ChartSeries(
        type_id=34,
        type_name="Tritanium",
        close=level,
        high=level + 1.0,
        low=level - 1.0,
    )
    assert real.ranged

    # A series that is flat for a while but not always still gets its candles.
    mixed = ChartSeries(
        type_id=34,
        type_name="Tritanium",
        close=level,
        high=np.array([100.0, 101.0, 99.0, 104.0]),
        low=level,
    )
    assert mixed.ranged


def test_the_market_page_charts_its_indices_without_candles(qtbot, desk, config):
    """The regression: FORGE rendered as a field of floating notches."""
    from evescreener.gui.app import DeskWindow

    window = DeskWindow(config, data=desk)
    qtbot.addWidget(window)
    window.timer.stop()
    market = window.pages["MARKET"]
    for canvas in (market.forge_canvas, market.breadth_canvas):
        series = canvas.series
        if series is not None and series.known:
            assert not series.ranged
        canvas.resize(700, 500)
        canvas.grab()


def test_the_chart_draws_the_levels_that_were_already_computed(qtbot, desk):
    from evescreener.gui.chart import build_series

    series = build_series(desk, 601)
    assert series.atr is not None
    assert series.levels, "levels.py computed these; the chart's job is to draw them"


def test_a_type_with_no_bars_says_so_rather_than_drawing_nothing(qtbot, desk):
    from evescreener.gui.chart import ChartPanel, build_series

    series = build_series(desk, 999999)
    assert not series.known
    assert "no bars in the lake" in series.note
    panel = ChartPanel()
    qtbot.addWidget(panel)
    panel.show_series(series)
    assert "999999" in panel.title.text()


# -- Focus never auto-removes -----------------------------------------------


def test_focus_never_auto_removes_a_name(qtbot, desk):
    """The only removal path is the deliberate button. Nothing automatic reaches it."""
    from evescreener.gui.pages.focus import FocusPage
    from evescreener.universe import add_watch, watchlist_entries

    add_watch(desk.db, name="Thing 1", type_id=601)
    desk.watch_ids.add(601)
    page = FocusPage(desk)
    qtbot.addWidget(page)
    assert page.table.rowCount() == 1

    # A refresh, a pass, a floor change — none of them may drop the name.
    page.refresh(desk)
    assert page.table.rowCount() == 1
    assert [row["name"] for row in watchlist_entries(desk.db)] == ["Thing 1"]
    assert "NEVER auto-removed" in page.footer.text()


def test_an_unresolvable_focus_name_is_a_loud_error(qtbot, desk):
    from evescreener.gui.pages.focus import FocusPage

    page = FocusPage(desk)
    qtbot.addWidget(page)
    page.entry.setText("Rifter Mk III")
    page._add()
    assert "no type named" in page.message.text()
    assert page.table.rowCount() == 0


# -- the banner -------------------------------------------------------------


def test_the_backtest_banner_reaches_market_and_scanner(qtbot, desk, config, monkeypatch):
    from evescreener.backtest import verdict_banner
    from evescreener.gui.app import DeskWindow

    verdicts = {"5": {"verdict": "NOT PLAUSIBLE"}, "10": {"verdict": "NOT PLAUSIBLE"}}
    monkeypatch.setattr(DeskWindow, "_backtest_verdict", lambda self: verdicts)
    window = DeskWindow(config, data=desk)
    qtbot.addWidget(window)
    window.timer.stop()
    expected = verdict_banner(verdicts)
    assert expected
    for title in ("MARKET", "SCANNER"):
        banner = window.pages[title].banner
        assert banner.text() == expected, f"{title} must carry the digest's exact wording"
        assert banner.isVisibleTo(window.pages[title])


def test_no_banner_when_the_backtest_has_not_failed(qtbot, desk, config):
    from evescreener.gui.pages.market import MarketPage

    page = MarketPage(desk)
    qtbot.addWidget(page)
    assert page.banner.text() == ""


# -- scanner honest zero ----------------------------------------------------


def test_the_scanner_prints_an_honest_zero_per_setup(qtbot, desk):
    from evescreener.gui.pages.scanner import ScannerPage
    from evescreener.setups import Condition, Setup

    desk.setups = [
        Setup(
            name="Never",
            conditions=(Condition("change", {"bars": 5, "op": "at_least", "value": 1e6}),),
        )
    ]
    page = ScannerPage(desk)
    qtbot.addWidget(page)
    settled(qtbot, page)
    texts = [
        child.text()
        for child in page.blocks.findChildren(type(page.summary))
        if hasattr(child, "text")
    ]
    assert any("Nothing cleared this setup today" in text for text in texts)
    assert any("examined" in text for text in texts)


# -- paper: one ledger, two doors -------------------------------------------


def test_a_desk_paper_buy_lands_in_the_same_ledger_as_the_cli(qtbot, desk):
    from pathlib import Path

    from evescreener.gui.paperform import PaperOpenDialog, prefill_for
    from evescreener.paper import PaperLedger
    from evescreener.reasons import load_reasons

    desk.vocabulary = load_reasons(Path(__file__).resolve().parents[1] / "config" / "reasons.jsonl")
    dialog = PaperOpenDialog(desk, prefill_for(desk, 601))
    qtbot.addWidget(dialog)
    dialog.thesis.setText("dip below anchored value")
    dialog.setup.setCurrentText("discretionary")
    dialog.tags.check("clean_dip_below_value")
    dialog.submit()

    ledger = PaperLedger(desk.config.paths.paper_ledger, desk.config)
    opens = [record for record in ledger.records() if record.get("event") == "open"]
    assert len(opens) == 1
    assert opens[0]["setup_tag"] == "discretionary"
    assert opens[0]["like_tags"] == ["clean_dip_below_value"]


def test_a_desk_paper_buy_without_reasons_is_refused_inline_and_recorded(qtbot, desk):
    from evescreener.gui.paperform import PaperOpenDialog, prefill_for
    from evescreener.paper import PaperLedger

    dialog = PaperOpenDialog(desk, prefill_for(desk, 601))
    qtbot.addWidget(dialog)
    dialog.thesis.setText("dip")
    dialog.submit()
    assert dialog.record is None
    assert dialog.error.isVisibleTo(dialog)
    assert "Refused" in dialog.error.text()
    ledger = PaperLedger(desk.config.paths.paper_ledger, desk.config)
    assert ledger.refusals(), "a GUI refusal is recorded exactly like a CLI one"


def test_a_stale_book_refusal_renders_inline_rather_than_repricing(qtbot, desk):
    from pathlib import Path

    from evescreener.gui.paperform import PaperOpenDialog, prefill_for
    from evescreener.reasons import load_reasons

    desk.book = book(sweep="2026-08-20T06:00:00+00:00", type_ids=range(600, 606))
    desk.vocabulary = load_reasons(Path(__file__).resolve().parents[1] / "config" / "reasons.jsonl")
    prefill = prefill_for(desk, 601)
    assert prefill["book_stale"]
    dialog = PaperOpenDialog(desk, prefill)
    qtbot.addWidget(dialog)
    dialog.thesis.setText("dip")
    dialog.tags.check("clean_dip_below_value")
    dialog.submit()
    assert dialog.record is None
    assert "Refused" in dialog.error.text()
    assert "STALE" in dialog.stamp.text()


def test_the_prefill_leaves_an_unmeasurable_field_empty_with_a_reason(qtbot, desk):
    from evescreener.gui.paperform import prefill_for

    desk.book = pd.DataFrame()
    prefill = prefill_for(desk, 601)
    assert prefill["entry_price"] is None
    assert any("ask-walk" in reason for reason in prefill["reasons"])
    assert prefill["target_price"] is not None, "anchored value is still measurable"


def test_a_pass_from_the_desk_needs_a_dislike_tag(qtbot, desk):
    from evescreener.gui.paperform import PassDialog

    dialog = PassDialog(desk, 601, action="not_today")
    qtbot.addWidget(dialog)
    dialog.submit()
    assert dialog.record is None
    assert "Refused" in dialog.error.text()


# -- the refresh timer is safe ----------------------------------------------


def test_the_desk_data_layer_cannot_reach_esi():
    """The guarantee is structural: no ESI import anywhere under gui/."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "evescreener" / "gui"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [alias.name for alias in node.names]
            for name in names:
                if "esi" in name.lower() or name in {"httpx", "urllib", "requests"}:
                    offenders.append(f"{path.name}: {name}")
    assert offenders == [], (
        "the desk refreshes on a timer; nothing under gui/ may be able to fetch "
        f"before Expires (§3.2). Offenders: {offenders}"
    )


def test_the_status_bar_says_how_old_everything_is(qtbot, desk, config):
    from evescreener.gui.app import DeskWindow

    window = DeskWindow(config, data=desk)
    qtbot.addWidget(window)
    window.timer.stop()
    text = window.stamp.text()
    assert "tracked types" in text
    assert "book" in text
    assert "local files only" in text


def test_a_missing_book_reads_as_unknown_not_as_fresh(qtbot, config, db):
    data = DeskData(config=config, db=db, region_id=10000002, loaded_at=NOW, book=pd.DataFrame())
    assert data.book_age_minutes is None
    assert data.book_is_stale, "an unmeasurable book is not a fresh one"
