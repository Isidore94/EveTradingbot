"""`python -m evescreener <command>` — the single entry point (plan.md §11 D1)."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
