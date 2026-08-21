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


def test_no_compute_reads_the_pages_own_running_state():
    """§22 S3: R7's `compute` read `self._running_input` off the page.

    Any `self._running*` / `self._owed` read is a cross-thread read of page
    state, whether it goes through an attribute, a property or a helper. The
    frozen input arrives as the `job_input` argument and nowhere else.
    """
    offenders = []
    for path, node in _compute_methods():
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in {
                "_running",
                "_running_input",
                "_running_token",
                "_owed",
                "_queued_input",
                "_job",
                "data",
            }:
                if isinstance(child.value, ast.Name) and child.value.id == "self":
                    offenders.append(f"{path.name}:{child.lineno} self.{child.attr}")
    assert not offenders, f"compute() must read only its arguments: {offenders}"


def test_every_compute_declares_the_job_input_argument():
    """A page that forgot the parameter would silently read stale page state."""
    missing = []
    for path, node in _compute_methods():
        names = [arg.arg for arg in node.args.args]
        if "job_input" not in names:
            missing.append(f"{path.name}:{node.lineno}")
    assert not missing, f"compute() must accept job_input: {missing}"


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


class _Recording(
    DeskPage := __import__("evescreener.gui.pages.base", fromlist=["DeskPage"]).DeskPage
):
    """A heavy page that records what it computed and what it painted."""

    heavy = True
    title = "REC"

    def build(self):
        self.painted = []
        self.computed = []
        self.knob = 0

    def job_input(self):
        return (self.knob,)

    def compute(self, data, job_input=()):
        self.computed.append((getattr(data, "input_key", None), job_input))
        return ("result", getattr(data, "input_key", None), job_input)

    def paint(self, result):
        self.painted.append(result)


def _settle(qtbot, page, *, timeout=30_000):
    qtbot.waitUntil(lambda: page._running_token is None, timeout=timeout)


def test_a_data_refresh_that_touches_no_widget_still_gets_its_own_computation(qtbot, desk, config):
    """The S3 defect, stated exactly (§22 S3).

    A key-1 job is running with widget tuple A. The lake moves to key 2 while
    the widget is untouched, so the queued tuple is still A. R7 compared only
    the widget input, found it equal, queued nothing, painted the key-1 result,
    and scheduled no follow-up — the desk silently kept showing key-1 data.
    """
    import dataclasses

    page = _Recording(desk)
    qtbot.addWidget(page)
    page.data = dataclasses.replace(desk, input_key=("key", 1))
    assert page.ensure_current() is True

    # The lake moves. No control was touched, so job_input() is unchanged.
    page.data = dataclasses.replace(desk, input_key=("key", 2))
    page.ensure_current()
    assert page._owed is not None, "a newer generation must be recorded as owed"

    _settle(qtbot, page)
    qtbot.waitUntil(lambda: page.painted and page.painted[-1][1] == ("key", 2), timeout=30_000)
    keys = [key for key, _input in page.computed]
    assert ("key", 2) in keys, "the newer key must actually be computed"
    assert page.painted[-1][1] == ("key", 2), "and it must be what is finally shown"


def test_a_widget_change_during_a_job_also_gets_its_own_computation(qtbot, desk):
    import dataclasses

    page = _Recording(desk)
    qtbot.addWidget(page)
    page.data = dataclasses.replace(desk, input_key=("key", 1))
    page.knob = 1
    page.ensure_current()
    page.knob = 2
    page.ensure_current()

    _settle(qtbot, page)
    qtbot.waitUntil(lambda: page.painted and page.painted[-1][2] == (2,), timeout=30_000)
    assert page.painted[-1][2] == (2,), "the hub finally selected is the one shown"


def test_the_worker_is_handed_its_input_and_never_reads_the_page(qtbot, desk):
    """R7 passed job_input to the job and then read it back off `self`."""
    import inspect

    from evescreener.gui import work

    source = inspect.getsource(work.PageJob.run)
    assert "self.generation.data" in source
    assert "self.generation.job_input" in source

    page = _Recording(desk)
    qtbot.addWidget(page)
    page.knob = 7
    page.ensure_current()
    _settle(qtbot, page)
    assert page.computed[-1][1] == (7,), "the frozen tuple reached compute as an argument"


def test_a_result_computed_from_a_superseded_generation_is_not_painted(qtbot, desk):
    page = _Recording(desk)
    qtbot.addWidget(page)
    page.knob = 1
    page.ensure_current()
    _settle(qtbot, page)
    painted = len(page.painted)

    from evescreener.gui.work import Generation

    stale = Generation(token=-1, key=("old",), data=desk, job_input=(1,))
    page._work_finished(stale, "stale result")
    assert len(page.painted) == painted, "a superseded generation paints nothing"


# -- 3. shutdown is safe -----------------------------------------------------


def test_a_page_being_destroyed_stops_receiving_results(qtbot, desk):
    """Closing the window mid-compute must not emit into a dead QObject."""
    from evescreener.gui.pages.base import DeskPage

    class Page(DeskPage):
        heavy = True
        title = "P"

        def build(self):
            self.painted = []

        def compute(self, data, job_input=()):
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
    from evescreener.gui.work import Generation, PageJob

    generation = Generation(token=1, key=("k",), data=desk, job_input=())
    job = PageJob(lambda data, job_input: "x", generation)
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
