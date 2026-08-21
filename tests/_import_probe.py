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

#: Exact module names that must never load. Kept as a set so a leak names the
#: module rather than a pattern.
FORBIDDEN_EXACT = {
    "httpx",
    "requests",
    "evescreener.esi.client",
    # Network-capable urllib submodules. `urllib.parse` is pure string work
    # imported by half the standard library, so it is deliberately absent.
    "urllib.request",
    "urllib.error",
    "urllib3",
    "aiohttp",
}

#: Deliberately NOT forbidden: `socket`, `ssl` and `http.client`. Qt and the
#: standard library load those on import regardless of what this package does,
#: so flagging them would fail always and prove nothing. The list above is the
#: set of clients our own code would have to *choose*, which is the thing the
#: invariant is actually about.


def is_forbidden(name: str) -> bool:
    """True for anything that can open a socket, or any ESI module at all.

    R8 checked two exact names. A GUI module could therefore have reached the
    network through `requests`, through `urllib.request`, or through any ESI
    module other than `client` — and the guard would have passed (§22 S8).

    Any dotted component equal to `esi` is rejected, so a future
    `evescreener.esi.anything` cannot slip in under a new name.
    """
    if name in FORBIDDEN_EXACT:
        return True
    parts = name.split(".")
    if "esi" in parts:
        return True
    return any(name.startswith(f"{prefix}.") for prefix in ("httpx", "urllib3", "aiohttp"))


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
            if module.startswith("evescreener")
            or module.startswith(("httpx", "requests", "urllib3", "aiohttp"))
        ]:
            del sys.modules[loaded]
        importlib.import_module(name)
        leaked = sorted(module for module in sys.modules if is_forbidden(module))
        if leaked:
            offenders.append(f"{name} -> {','.join(leaked)}")
    for line in offenders:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
