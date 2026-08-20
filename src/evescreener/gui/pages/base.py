"""What every page shares (plan.md §19 Part 2, §19.2 amended).

**The GUI thread never computes; it paints.**

A page declares which kind it is. A light page overrides `repopulate()` and
runs inline, because re-rendering rows already in memory costs milliseconds. A
heavy page sets `heavy = True` and splits itself in two: `compute(data)` is
pure and runs on a worker thread, `paint(result)` touches widgets and runs on
the GUI thread. Neither kind computes in `build()` — `build()` lays out
widgets and returns, so opening the window is fast no matter how large the
lake is.

Work is started by `ensure_current()`, which the window calls when a page
becomes visible and when the operator asks for a refresh. It is a no-op when
the input key has not changed, because daily bars change once a day and
recomputing on a 60-second timer was modelling the timer rather than the data.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..work import PageJob, pool

__all__ = ["DeskPage"]


class DeskPage(QWidget):
    """A page over one `DeskData`.

    `chart_requested` is how every surface reaches the single chart window:
    a page never opens a chart itself, it says which type it wants charted and
    the window re-points. That is what keeps "one window, re-pointed" true no
    matter how many pages grow a clickable name.
    """

    chart_requested = Signal(int)
    ledger_changed = Signal()
    title = "PAGE"

    #: Heavy pages compute off-thread. See the module docstring.
    heavy = False

    #: A page that wants to show the chart declares a slot for it and the
    #: window **moves its one `ChartPanel`** into whichever such page is
    #: visible. That is how "one chart window, re-pointed, never a stack"
    #: (§19 Part 2 page 2) survives a second page wanting to show one: there
    #: is still exactly one panel, one anchor set and one set of overlays, and
    #: it is impossible to end up comparing two names against two of them.
    chart_slot = None
    panel = None

    def dock_chart(self, panel) -> None:
        """Take custody of the window's single chart panel."""
        if self.chart_slot is None or panel is None:
            return
        self.panel = panel
        self.chart_slot.addWidget(panel)

    def __init__(self, data, parent=None) -> None:
        super().__init__(parent)
        self.data = data
        self.layout = QVBoxLayout(self)
        self._computed_key = None
        self._pending_key = None
        self._running_token = None
        self._token = 0
        self._result = None
        self._result_at = None
        self._error: Exception | None = None
        self._job = None
        if self.heavy:
            self.work_stamp = QLabel("")
            self.work_stamp.setWordWrap(True)
            self.work_stamp.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.work_stamp.setVisible(False)
            self.layout.addWidget(self.work_stamp)
        self.build()

    # -- the two halves ----------------------------------------------------

    def build(self) -> None:  # pragma: no cover - overridden by every page
        """Lay out widgets. MUST NOT compute — see the module docstring."""
        raise NotImplementedError

    def compute(self, data):  # pragma: no cover - overridden by heavy pages
        """Pure, off-thread. No Qt object may be touched in here."""
        raise NotImplementedError

    def paint(self, result) -> None:  # pragma: no cover - overridden by heavy pages
        """GUI thread. Render whatever `compute` returned."""
        raise NotImplementedError

    def repopulate(self) -> None:  # pragma: no cover - overridden by light pages
        raise NotImplementedError

    # -- scheduling --------------------------------------------------------

    def refresh(self, data) -> None:
        """Take a newer `DeskData`. Local files only — never ESI.

        This no longer paints by itself. The window hands every page the new
        data and then asks the *visible* one to bring itself current; an
        invisible page recomputes when it is next looked at.
        """
        self.data = data

    def ensure_current(self, *, force: bool = False) -> bool:
        """Bring the page up to date if its inputs moved.

        Returns True when background work was started, so a caller (and a
        test) can tell "already current" apart from "now computing".
        """
        key = getattr(self.data, "input_key", None)
        current = key is not None and key == self._computed_key and self._error is None
        if current and not force:
            return False
        if not self.heavy:
            self._computed_key = key
            self.repopulate()
            return False
        if self._running_token is not None and not force:
            return False
        self._token += 1
        self._running_token = self._token
        self._pending_key = key
        self._show_working()
        job = PageJob(self.compute, self.data, self._token)
        job.signals.finished.connect(self._work_finished)
        # Held so the runnable (and the signal object living on it) outlives
        # the worker thread that emits from it.
        self._job = job
        pool().start(job)
        return True

    # -- worker results ----------------------------------------------------

    def _work_finished(self, token, result) -> None:
        if token != self._running_token:
            return  # a newer job overtook this one; this answer is the stale one
        self._running_token = None
        self._computed_key = self._pending_key
        if isinstance(result, Exception):
            # Last good result stays on screen, stamped. A blanked panel would
            # be strictly worse than a stale one: a blank reads as "nothing
            # here", which is the failure mode this whole system avoids (§5).
            self._error = result
            tail = (
                f" — showing the {self._result_at} result"
                if self._result_at
                else " — nothing has computed yet"
            )
            self._show_stamp(f"could not compute: {type(result).__name__}: {result}{tail}")
            return
        self._error = None
        self._result = result
        self._result_at = self._stamp_time()
        self._hide_stamp()
        self.paint(result)

    # -- the stamp ---------------------------------------------------------

    def _stamp_time(self) -> str:
        from ...timeutil import utcnow

        return utcnow().strftime("%H:%M")

    def _show_working(self) -> None:
        if self._result_at:
            self._show_stamp(f"computing… (showing the {self._result_at} result)")
        else:
            self._show_stamp("computing…")

    def _show_stamp(self, text: str) -> None:
        if not self.heavy:
            return
        self.work_stamp.setText(text)
        self.work_stamp.setVisible(True)

    def _hide_stamp(self) -> None:
        if self.heavy:
            self.work_stamp.setVisible(False)
