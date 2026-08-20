"""R7 — the desk's threading contract, held structurally.

Three defects, all of them the same shape: a rule that held by convention.

**Widget reads happened off the GUI thread.** `SpreadsPage.compute()` called
`QComboBox.currentData()` on a worker. Qt widgets are not thread-safe, and the
value can change mid-read.

**An input change during a running job was declined and then painted stale.**
`ensure_current()` returned early while a job was in flight, so the newer key
never got its own computation and the older result was painted over it.

**A worker could emit into a deleted page.** Closing the window during a
compute delivered `finished` to a destroyed `QObject`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

GUI = Path(__file__).resolve().parents[1] / "src" / "evescreener" / "gui"

#: Anything that reads or touches a widget. A `compute()` that mentions one is
#: reaching across a thread boundary, whatever it does with the value.
WIDGET_CALLS = {
    "currentData",
    "currentText",
    "currentIndex",
    "currentRow",
    "isChecked",
    "text",
    "value",
    "rowCount",
    "selectedItems",
    "setText",
    "setEnabled",
    "update",
}


def _compute_methods():
    for path in GUI.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "compute":
                yield path, node


# -- 1. no QObject access inside compute() ----------------------------------


def test_no_page_touches_a_widget_inside_compute():
    """Every widget-derived value is captured on the GUI thread (§21 R7)."""
    offenders = []
    for path, node in _compute_methods():
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in WIDGET_CALLS:
                offenders.append(f"{path.name}:{child.lineno} .{child.attr}")
            # `setText`, `setEnabled`, ... but not `setups`.
            if (
                isinstance(child, ast.Attribute)
                and child.attr.startswith("set")
                and len(child.attr) > 3
                and child.attr[3].isupper()
            ):
                offenders.append(f"{path.name}:{child.lineno} .{child.attr}")
    assert not offenders, f"compute() must not touch Qt objects: {offenders}"


def test_at_least_one_page_actually_declares_compute():
    """Guard against the check above passing because it found nothing."""
    assert list(_compute_methods()), "no compute() methods found — the scan is broken"


def test_widget_state_reaches_compute_as_immutable_job_input(qtbot, desk):
    """The hub selection is read on the GUI thread and frozen before dispatch."""
    from evescreener.gui.pages.spreads import SpreadsPage

    page = SpreadsPage(desk)
    qtbot.addWidget(page)
    captured = page.job_input()
    assert isinstance(captured, tuple), "job input must be immutable"
    assert captured, "the hub selection must actually be captured"
    # Changing the widget afterwards cannot reach a job already dispatched.
    before = page.job_input()
    page.hub.setCurrentIndex(page.hub.count() - 1)
    assert page.job_input() != before


# -- 2. an input change invalidates and re-runs ------------------------------


def test_an_input_change_during_a_job_guarantees_a_follow_up(qtbot, desk):
    """The old code declined the new key and later painted the old result."""
    from evescreener.gui.pages.base import DeskPage

    class Slow(DeskPage):
        heavy = True
        title = "SLOW"

        def build(self):
            self.painted = []

        def compute(self, data):
            return ("computed", self.captured)

        def job_input(self):
            return (getattr(self, "knob", 0),)

        def paint(self, result):
            self.painted.append(result)

    page = Slow(desk)
    qtbot.addWidget(page)
    page.knob = 1
    assert page.ensure_current() is True
    # A second request while the first is in flight must not be dropped.
    page.knob = 2
    page.ensure_current()
    assert page._queued_input == (2,), "the newer input must be remembered"


def test_a_result_computed_from_superseded_input_is_not_painted(qtbot, desk):
    from evescreener.gui.pages.base import DeskPage

    class Page(DeskPage):
        heavy = True
        title = "P"

        def build(self):
            self.painted = []

        def compute(self, data):
            return "result"

        def job_input(self):
            return (getattr(self, "knob", 0),)

        def paint(self, result):
            self.painted.append(result)

    page = Page(desk)
    qtbot.addWidget(page)
    page.knob = 1
    page.ensure_current()
    running = page._running_token
    page.knob = 2  # the world moved while the job ran
    page._work_finished(running, "stale result")
    assert page.painted == [], "a result from superseded input must be discarded"


# -- 3. shutdown is safe -----------------------------------------------------


def test_a_page_being_destroyed_stops_receiving_results(qtbot, desk):
    """Closing the window mid-compute must not emit into a dead QObject."""
    from evescreener.gui.pages.base import DeskPage

    class Page(DeskPage):
        heavy = True
        title = "P"

        def build(self):
            self.painted = []

        def compute(self, data):
            return "result"

        def paint(self, result):
            self.painted.append(result)

    page = Page(desk)
    qtbot.addWidget(page)
    page.ensure_current()
    token = page._running_token
    page.shutdown()
    assert page._running_token is None
    page._work_finished(token, "late result")
    assert page.painted == [], "a shut-down page paints nothing"


def test_the_window_shuts_its_pages_down_on_close(qtbot, desk, config):
    from evescreener.gui.app import DeskWindow

    window = DeskWindow(config, data=desk)
    qtbot.addWidget(window)
    window.timer.stop()
    window.close()
    for page in window.pages.values():
        assert page._shutdown is True


def test_a_job_whose_page_has_gone_does_not_raise(qtbot, desk):
    """The RuntimeError seen in teardown: emit into a deleted signal source."""
    from evescreener.gui.work import PageJob

    job = PageJob(lambda data: "x", desk, 1)
    job.cancel()
    job.run()  # must be a no-op rather than an emit
    assert job.cancelled


# -- 4. sqlite stays in its own thread ---------------------------------------


def test_worker_database_connections_are_opened_and_closed_in_the_worker():
    """`thread_local_db` is a context manager for exactly this reason."""
    import inspect

    from evescreener.gui.data import DeskData

    source = inspect.getsource(DeskData.thread_local_db)
    assert "Database(" in source
    for path, node in _compute_methods():
        text = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
        if "thread_local_db" in text:
            assert "with " in text, f"{path.name}: worker db must be a `with` block"


@pytest.mark.parametrize("name", ["BOARD", "SCANNER", "SPREADS", "LEARNING"])
def test_every_heavy_page_can_capture_its_input_on_the_gui_thread(qtbot, desk, name):
    from evescreener.gui.pages import PAGES

    factory = dict(PAGES)[name]
    page = factory(desk)
    qtbot.addWidget(page)
    captured = page.job_input()
    assert isinstance(captured, tuple)
