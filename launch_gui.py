"""Open the desk with a double-click (plan.md §19 Part 2).

Equivalent to `python -m evescreener gui`, in a form a Windows shortcut or a
Task Scheduler action can point at directly. It changes the working directory
to the repository root first, because the committed config lives beside this
file — `config.toml`, `config/setups.jsonl`, `config/sectors.jsonl`,
`config/reasons.jsonl` — and a shortcut launched from elsewhere would
otherwise start with an empty sector map and no setups and give no clue why.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / "src"))
    from evescreener.config import ConfigError, load_config
    from evescreener.gui import run_desk

    try:
        config = load_config()
    except ConfigError as error:
        print(f"config error: {error}")
        return 2
    return run_desk(config)


if __name__ == "__main__":
    raise SystemExit(main())
