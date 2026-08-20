"""The desk — PySide6 (plan.md §19, operator directive 2026-08-20 third).

**Optional by construction.** §10.6's no-GUI non-goal was revoked by that
directive, but the reason it existed — a 42k-LOC Qt desk consuming the
project (§2) — is answered by keeping Qt in its own dependency tier rather
than by keeping it out. `daemon`, `digest` and every CLI subcommand run on a
headless box with no Qt installed; `tests/test_headless.py` enforces that no
core module imports it.

Importing this package does **not** import Qt. `build_app` does, and says
plainly what to install when it is absent.
"""

from __future__ import annotations

MISSING_QT_MESSAGE = (
    "The desk needs PySide6, which is an optional extra.\n"
    "  uv sync --extra gui        (or: pip install 'evescreener[gui]')\n"
    "Everything else — daemon, digest, board, brief, scanner, paper — runs "
    "headless without it."
)


def qt_available() -> bool:
    """Is the `gui` extra installed? Checked without importing Qt."""
    import importlib.util

    return importlib.util.find_spec("PySide6") is not None


def run_desk(config, argv=None) -> int:
    """Launch the desk. Returns a process exit code; never raises on absence."""
    if not qt_available():
        print(MISSING_QT_MESSAGE)
        return 2
    from .app import launch

    return launch(config, argv=argv)
