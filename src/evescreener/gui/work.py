"""The desk's background worker and its cache key (plan.md §19.2, amended).

**The GUI thread never computes; it paints.** That is the whole contract, and
it exists because the first real universe broke the old design: 2,947 tracked
types and 4,052,335 bars took `ScannerPage` 145.9 s and `BoardPage` 56.5 s,
both synchronously inside `build()`, on the thread that draws the window — and
then a 60-second timer asked for it all again. The desk opened in 3.6 minutes
and never became interactive.

Nothing here fetches. This module reads file stats and one SQLite column, and
the work it hands to a thread is the same local-only computation `gui/data.py`
already guarantees is network-free (§3.2). A background thread cannot make a
safe read unsafe.

The cache key lives in `gui/data.py`, which imports no Qt on purpose; only
the worker needs Qt and only the worker is here.

That key is the point of the exercise: **daily bars change once a day.**
A 60-second full rescan was modelling the timer, not the data. When the key is
unchanged there is nothing to recompute, and the timer's only job is to
re-read the cheap things that do move — book age, paper marks, staleness
stamps.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

__all__ = ["DeskWorkSignals", "PageJob", "pool"]


def pool() -> QThreadPool:
    """The desk's shared pool. Bounded, because these jobs are memory-hungry."""
    shared = QThreadPool.globalInstance()
    if shared.maxThreadCount() > 4:
        shared.setMaxThreadCount(4)
    return shared


class DeskWorkSignals(QObject):
    """`QRunnable` is not a `QObject`, so the signals live here."""

    finished = Signal(object, object)  # (token, result-or-Exception)


class PageJob(QRunnable):
    """Run one page's `compute()` off the GUI thread.

    An exception is *delivered*, not raised: a page that cannot compute shows
    its last good result and says so. Last-good-on-failure is the same rule the
    rest of this system follows for a failed publish (§5) — a blanked panel
    would be strictly worse than a stale one, because a blank reads as "no
    opportunity" and a stamped stale result reads as what it is.
    """

    def __init__(self, compute, data, token, job_input=None) -> None:
        super().__init__()
        # NOT auto-delete. Qt would free the runnable as soon as `run` returns,
        # and `signals` is an attribute of it — a queued emit could then be
        # delivered to a half-collected object. The page holds the reference
        # instead and drops it when the next job replaces it.
        self.setAutoDelete(False)
        self._compute = compute
        self._data = data
        self._token = token
        #: Immutable snapshot of every widget-derived value this job needs,
        #: captured on the GUI thread before dispatch (§21 R7).
        self.job_input = job_input
        self.cancelled = False
        self.signals = DeskWorkSignals()

    def cancel(self) -> None:
        """Abandon this job. A cancelled job emits nothing, ever.

        The window closing mid-compute used to deliver `finished` into a
        destroyed page — `RuntimeError: Signal source has been deleted`.
        Cancellation is checked both before the work starts and again before
        the emit, because the page can go away during the computation itself.
        """
        self.cancelled = True

    def run(self) -> None:  # pragma: no cover - exercised through the pages
        if self.cancelled:
            return
        try:
            result = self._compute(self._data)
        except Exception as exc:  # noqa: BLE001 - delivered to the page, never swallowed
            result = exc
        if self.cancelled:
            return
        self.signals.finished.emit(self._token, result)
