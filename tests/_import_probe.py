"""Import every GUI module in a fresh interpreter and report network leaks.

Run as a subprocess by `test_isolation_and_parity.py` (plan.md §21 R8). It has
to be a separate process: the question is what `sys.modules` holds after a cold
import, and the test runner has already imported half the world.

Prints one `module -> leaked,modules` line per offender, and nothing at all
when the package is clean.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

FORBIDDEN = {"httpx", "evescreener.esi.client"}


def gui_modules() -> list[str]:
    root = pathlib.Path(__file__).resolve().parents[1] / "src"
    gui = root / "evescreener" / "gui"
    names = []
    for path in sorted(gui.rglob("*.py")):
        target = path.parent if path.name == "__init__.py" else path.with_suffix("")
        names.append(".".join(target.relative_to(root).parts))
    return names


def main() -> int:
    offenders = []
    for name in gui_modules():
        for loaded in [
            module
            for module in sys.modules
            if module.startswith("evescreener") or module.startswith("httpx")
        ]:
            del sys.modules[loaded]
        importlib.import_module(name)
        leaked = sorted(module for module in sys.modules if module in FORBIDDEN)
        if leaked:
            offenders.append(f"{name} -> {','.join(leaked)}")
    for line in offenders:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
