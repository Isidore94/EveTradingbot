"""The desk's pages, in the operator's priority order (plan.md §19 Part 2).

Every page is a `QWidget` built from a `DeskData` — the local-only read in
`gui/data.py`. None of them fetches anything; a page refresh re-reads what is
already on disk, which is what makes a UI timer safe against the `Expires`
invariant (§3.2).

Two rules every page obeys:

* **UNKNOWN renders as UNKNOWN.** Never a zero, never a dash that could be
  mistaken for one, never a silently-priced row.
* **Clicking a name charts it** in the one re-pointing chart window, and every
  surface a name appears on offers Paper Buy (§19 Amendment 2).
"""

from __future__ import annotations

from .board import BoardPage
from .charts import ChartsPage
from .desk import DeskReviewPage
from .focus import FocusPage
from .health import HealthPage
from .learning import LearningPage
from .market import MarketPage
from .paper import PaperPage
from .scanner import ScannerPage

__all__ = [
    "BoardPage",
    "ChartsPage",
    "DeskReviewPage",
    "FocusPage",
    "HealthPage",
    "LearningPage",
    "MarketPage",
    "PAGES",
    "PaperPage",
    "ScannerPage",
]

# Left-rail order. This is the operator's stated priority, not alphabetical.
PAGES = (
    ("DESK", DeskReviewPage),
    ("MARKET", MarketPage),
    ("CHARTS", ChartsPage),
    ("BOARD", BoardPage),
    ("FOCUS", FocusPage),
    ("SCANNER", ScannerPage),
    ("PAPER", PaperPage),
    ("LEARNING", LearningPage),
    ("HEALTH", HealthPage),
)
