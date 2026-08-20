"""The GUI thread never computes; it paints (plan.md §19.2, amended).

The desk's first contact with a real universe — 2,947 tracked types over
4,052,335 bars — took 217 seconds to open, 145.9 s of it inside
`ScannerPage.build()` and 56.5 s inside `BoardPage.build()`, on the thread
that draws the window. A 60-second timer then asked for all of it again. It
never became interactive.

These tests pin the four properties that stop that happening again.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pytestqt")
pytestmark = pytest.mark.gui

from evescreener.gui.data import DeskData, desk_input_key  # noqa: E402
from evescreener.gui.pages.base import DeskPage  # noqa: E402

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _lake(type_ids, *, bars=120, seed=11):
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
                    "high": close[position] * 1.01,
                    "low": close[position] * 0.99,
                    "close": close[position],
                    "volume": 5_000.0,
                    "order_count": 40,
                    "isk_value": close[position] * 5_000.0,
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture
def desk(config):
    from evescreener.store.db import Database

    config.paths.ensure()
    db = Database(config.paths.db)
    return DeskData(
        config=config,
        db=db,
        region_id=10000002,
        loaded_at=NOW,
        bars=_lake(range(1001, 1009)),
        all_bars=_lake(range(1001, 1009)),
        input_key=("key", 1),
    )


# -- 1. build() must not compute --------------------------------------------


@pytest.mark.parametrize("page_name", ["BoardPage", "ScannerPage", "LearningPage"])
def test_constructing_a_heavy_page_runs_no_computation(qtbot, desk, page_name, monkeypatch):
    """The specific regression: `build()` used to call the scan engine.

    Constructing all eight pages is what the window does at startup, so if any
    of them computes there, opening the desk pays for all of it before the
    operator can click anything.
    """
    import evescreener.gui.pages.board as board_module
    import evescreener.gui.pages.learning as learning_module
    import evescreener.gui.pages.scanner as scanner_module

    called: list[str] = []
    monkeypatch.setattr(
        scanner_module, "run_scan", lambda *a, **k: called.append("run_scan") or None
    )
    monkeypatch.setattr(
        board_module, "build_board", lambda *a, **k: called.append("build_board") or None
    )
    monkeypatch.setattr(
        learning_module,
        "build_learning_report",
        lambda *a, **k: called.append("build_learning_report") or None,
    )

    module = {
        "BoardPage": board_module,
        "ScannerPage": scanner_module,
        "LearningPage": learning_module,
    }[page_name]
    page = getattr(module, page_name)(desk)
    qtbot.addWidget(page)

    assert called == [], f"{page_name}.build() computed: {called}"
    assert page.heavy is True


def test_every_page_declares_which_kind_it_is():
    """A page that computes must say so, or it will do it on the GUI thread."""
    from evescreener.gui.pages import PAGES

    heavy = {title for title, factory in PAGES if factory.heavy}
    assert heavy == {"BOARD", "SCANNER", "SPREADS", "LEARNING"}
    for _title, factory in PAGES:
        assert issubclass(factory, DeskPage)


# -- 2. an unchanged input key does not recompute ---------------------------


class _CountingPage(DeskPage):
    title = "COUNTING"
    heavy = True

    def build(self) -> None:
        self.computed = 0
        self.painted = 0

    def compute(self, data):
        self.computed += 1
        return {"n": self.computed}

    def paint(self, result) -> None:
        self.painted += 1


def test_an_unchanged_input_key_does_not_recompute(qtbot, desk):
    """Daily bars change once a day. A timer is not a reason to rescan."""
    page = _CountingPage(desk)
    qtbot.addWidget(page)

    assert page.ensure_current() is True
    qtbot.waitUntil(lambda: page._running_token is None, timeout=30_000)
    assert page.computed == 1
    assert page.painted == 1

    for _ in range(5):
        assert page.ensure_current() is False, "same key must not start work"
    assert page.computed == 1

    desk.input_key = ("key", 2)
    page.refresh(desk)
    assert page.ensure_current() is True
    qtbot.waitUntil(lambda: page._running_token is None, timeout=30_000)
    assert page.computed == 2
    assert page.painted == 2


def test_an_explicit_refresh_recomputes_even_on_an_unchanged_key(qtbot, desk):
    """F5 means "I want it now", not "if you feel like it"."""
    page = _CountingPage(desk)
    qtbot.addWidget(page)
    page.ensure_current()
    qtbot.waitUntil(lambda: page._running_token is None, timeout=30_000)
    assert page.computed == 1

    page.ensure_current(force=True)
    qtbot.waitUntil(lambda: page._running_token is None, timeout=30_000)
    assert page.computed == 2


# -- 3. a completed background result lands and repaints --------------------


def test_a_background_result_lands_on_the_gui_thread_and_repaints(qtbot, desk):
    import threading

    seen = {}

    class _ThreadPage(DeskPage):
        title = "THREADED"
        heavy = True

        def build(self) -> None:
            self.result_seen = None

        def compute(self, data):
            seen["worker"] = threading.current_thread().ident
            return "computed"

        def paint(self, result) -> None:
            seen["painter"] = threading.current_thread().ident
            self.result_seen = result

    page = _ThreadPage(desk)
    qtbot.addWidget(page)
    gui_thread = threading.current_thread().ident

    page.ensure_current()
    qtbot.waitUntil(lambda: page.result_seen is not None, timeout=30_000)

    assert page.result_seen == "computed"
    assert seen["worker"] != gui_thread, "compute() must not run on the GUI thread"
    assert seen["painter"] == gui_thread, "paint() must run on the GUI thread"


# -- 4. the stamp, and last-good-on-failure ---------------------------------


def test_the_stamp_renders_while_a_recompute_is_in_flight(qtbot, desk):
    page = _CountingPage(desk)
    qtbot.addWidget(page)
    page.ensure_current()
    qtbot.waitUntil(lambda: page._running_token is None, timeout=30_000)

    desk.input_key = ("key", 3)
    page.refresh(desk)
    page.ensure_current()
    # The stamp is set synchronously when work starts, before any waiting.
    assert not page.work_stamp.isHidden()
    assert "computing" in page.work_stamp.text()
    assert "showing the" in page.work_stamp.text(), "it must name the result still on screen"

    qtbot.waitUntil(lambda: page._running_token is None, timeout=30_000)
    assert page.work_stamp.isHidden()


def test_a_failed_recompute_keeps_the_last_good_result_and_says_so(qtbot, desk):
    """A blanked panel reads as "nothing here". That is the one thing it must
    never say when the truth is "I could not measure"."""

    class _BreakingPage(DeskPage):
        title = "BREAKING"
        heavy = True

        def build(self) -> None:
            self.painted = None
            self.explode = False

        def compute(self, data):
            if self.explode:
                raise RuntimeError("the lake moved under me")
            return "good result"

        def paint(self, result) -> None:
            self.painted = result

    page = _BreakingPage(desk)
    qtbot.addWidget(page)
    page.ensure_current()
    qtbot.waitUntil(lambda: page._running_token is None, timeout=30_000)
    assert page.painted == "good result"

    page.explode = True
    desk.input_key = ("key", 9)
    page.refresh(desk)
    page.ensure_current()
    qtbot.waitUntil(lambda: page._running_token is None, timeout=30_000)

    assert page.painted == "good result", "the last good result must survive a failure"
    assert not page.work_stamp.isHidden()
    text = page.work_stamp.text()
    assert "could not compute" in text
    assert "RuntimeError" in text and "the lake moved under me" in text
    assert "showing the" in text


# -- the key itself ---------------------------------------------------------


def test_the_input_key_moves_when_the_lake_does(config, tmp_path):
    config.paths.ensure()
    first = desk_input_key(config, 10000002, root=tmp_path)
    assert desk_input_key(config, 10000002, root=tmp_path) == first, "must be stable"

    partition = config.paths.bars_partition(10000002, 2026)
    partition.parent.mkdir(parents=True, exist_ok=True)
    partition.write_bytes(b"not really parquet, but it is a new file")
    assert desk_input_key(config, 10000002, root=tmp_path) != first


def test_the_input_key_moves_when_the_operator_edits_a_setup(config, tmp_path):
    config.paths.ensure()
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    setups = tmp_path / "config" / "setups.jsonl"
    setups.write_text("{}\n", encoding="utf-8")
    first = desk_input_key(config, 10000002, root=tmp_path)
    setups.write_text('{"name": "mine"}\n', encoding="utf-8")
    assert desk_input_key(config, 10000002, root=tmp_path) != first


def test_the_input_key_is_cheap_enough_for_a_timer(config, tmp_path):
    """It stats files; it must never parse the lake."""
    import time

    config.paths.ensure()
    start = time.perf_counter()
    for _ in range(20):
        desk_input_key(config, 10000002, root=tmp_path)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"20 key reads took {elapsed:.2f}s — too slow for a 60s timer"
