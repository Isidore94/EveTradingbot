"""The desk is optional. Core must run on a box with no Qt installed.

plan.md §19 makes PySide6 an optional `gui` tier precisely so `daemon`,
`digest` and every CLI subcommand keep working headless. That is only true if
nothing on those paths imports Qt, so this module enforces it by import graph
rather than by hope — a stray `from PySide6...` at module scope in
`screen.py` would not fail any other test, and would break the operator's
mini-PC the next time he pulled.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "evescreener"
QT_NAMES = ("PySide6", "shiboken6", "PyQt5", "PyQt6")

# Only these may touch Qt at all. Everything else is core.
GUI_ONLY = {"gui"}


def _module_paths():
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC)
        yield relative, path


def test_no_core_module_imports_qt():
    offenders = []
    for relative, path in _module_paths():
        if relative.parts[0] in GUI_ONLY:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in QT_NAMES:
                    offenders.append(f"{relative}:{node.lineno} imports {name}")
    assert offenders == [], "core modules must never import Qt:\n" + "\n".join(offenders)


def test_importing_the_cli_does_not_pull_in_qt():
    """Even lazily — `python -m evescreener --help` must not load Qt."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import evescreener.cli, sys; "
            "print(','.join(m for m in sys.modules if m.split('.')[0] in "
            "('PySide6','shiboken6','PyQt5','PyQt6')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", f"CLI import pulled in Qt: {result.stdout!r}"


def test_core_entry_points_import_without_qt():
    for name in (
        "evescreener.daemon",
        "evescreener.digest",
        "evescreener.screen",
        "evescreener.brief",
        "evescreener.paper",
        "evescreener.backtest",
    ):
        module = importlib.import_module(name)
        assert module is not None


def test_the_gui_package_declares_its_own_dependency():
    """`python -m evescreener gui` without the extra must say so, not traceback."""
    from evescreener import gui

    assert hasattr(gui, "MISSING_QT_MESSAGE")
    assert "gui" in gui.MISSING_QT_MESSAGE
    assert "pip install" in gui.MISSING_QT_MESSAGE or "uv sync" in gui.MISSING_QT_MESSAGE
