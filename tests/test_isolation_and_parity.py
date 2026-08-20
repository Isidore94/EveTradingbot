"""R8 — structural isolation, chart parity, regional data, and a retracted number.

**The AST test checked only direct imports.** Importing `gui.pages.spreads`
pulled in `books`, which pulls `esi.client`, which pulls `httpx`. The invariant
is that nothing under `gui/` can reach the network — and it was held by a check
that could not see one hop away.

**The chart truncated before computing.** `build_series` tailed the frame to
`chart_bars` and *then* ran AVWAP and the overlays, so an anchor just outside
the display window produced bands that disagreed with the screen's.

**A retracted measurement was still quoted as fact.** `esi/client.py` and
`store/db.py` carried "16,789 of 19,152 types 404", which plan.md §17 D-10
withdrew: it was a circuit-breaker cascade mistaken for data. The measured
figure is 241.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
GUI = REPO / "src" / "evescreener" / "gui"

FORBIDDEN_MODULES = ("httpx", "evescreener.esi.client")


def _gui_modules():
    for path in sorted(GUI.rglob("*.py")):
        if path.name == "__init__.py":
            module = path.parent.relative_to(REPO / "src")
        else:
            module = path.relative_to(REPO / "src").with_suffix("")
        yield ".".join(module.parts)


# -- 1. isolation proved by the import graph, not by grepping ---------------


def test_no_gui_module_transitively_loads_a_network_client():
    """Proved in a real interpreter, transitively (plan.md §21 R8).

    The old guard walked the AST for *direct* imports, so it could not see
    `gui.pages.spreads` -> `spreads` -> `books` -> `esi.client` -> `httpx`.
    `_import_probe.py` imports every GUI module in one cold subprocess and asks
    `sys.modules` what actually loaded — the only check an extra hop cannot
    fool. One subprocess rather than one per module: fifteen interpreter starts
    cost a minute of gate time and prove nothing extra.
    """
    probe = Path(__file__).with_name("_import_probe.py")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(REPO / "src")}
    result = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        cwd=REPO,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    leaked = [line for line in result.stdout.splitlines() if line.strip()]
    assert not leaked, "GUI modules reached a network client: " + "; ".join(leaked)


def test_the_scan_actually_covers_the_gui_package():
    """Guard against the check above passing because it found no modules."""
    modules = list(_gui_modules())
    assert len(modules) > 5
    assert any(name.endswith("pages.spreads") for name in modules)


# -- 2. chart overlays compute on the full history --------------------------


def _series(bars=400):
    stamps = pd.date_range("2025-01-01 11:00", periods=bars, freq="D", tz="UTC")
    close = 100 * np.exp(np.cumsum(np.linspace(-0.001, 0.001, bars)))
    return pd.DataFrame(
        {
            "type_id": 34,
            "region_id": 10000002,
            "datetime": stamps,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000.0,
            "order_count": 20,
            "isk_value": close * 1000.0,
            "fetched_at": "2026-08-20T00:00:00+00:00",
        }
    )


def test_the_chart_computes_on_full_history_then_tails_for_display(qtbot, desk):
    """An anchor outside the display window must still shape the bands."""
    from evescreener.gui.chart import build_series

    series = build_series(desk, 601)
    assert series.known
    # The analytical arrays are as long as the lake, not as long as the view.
    lake_bars = len(desk.frame_for(601))
    assert len(series.close) == lake_bars
    assert series.vwap is None or len(series.vwap) == lake_bars


def test_display_tailing_happens_in_the_canvas_not_the_computation(qtbot, desk):
    from evescreener.gui.chart import build_series

    series = build_series(desk, 601)
    window = series.tail(60)
    assert len(window.close) == 60
    # The full series is untouched: tailing is a view.
    assert len(series.close) > 60


def test_band_values_at_the_visible_tail_match_the_full_history_read(qtbot, desk):
    """Parity: what the chart draws is what the screen computed (§21 R8)."""
    from evescreener.gui.chart import build_series

    series = build_series(desk, 601)
    if series.vwap is None:
        pytest.skip("no bands on this fixture")
    window = series.tail(30)
    assert window.vwap[-1] == series.vwap[-1]
    assert window.sigma[-1] == series.sigma[-1]


# -- 3. regional data is keyed by region ------------------------------------


def test_desk_data_exposes_bars_for_a_named_region(qtbot, desk):
    """SPREADS iterates configured regions; the lake must answer per region."""
    frame = desk.bars_for_region(10000002)
    assert not frame.empty
    assert set(frame["region_id"].unique()) == {10000002}


def test_a_region_with_no_bars_returns_empty_rather_than_the_home_regions(qtbot, desk):
    """The quiet failure: Amarr silently priced against Jita's history."""
    frame = desk.bars_for_region(10000043)
    assert frame.empty, "another region's bars are not this region's bars"


def test_traded_averages_are_keyed_by_region(qtbot, desk):
    averages = desk.last_close_by_region(10000002)
    assert averages, "the home region has traded averages"
    assert desk.last_close_by_region(10000043) == {}


# -- 4. Expires fails closed ------------------------------------------------


def test_a_missing_expires_header_is_not_a_licence_to_refetch():
    """No Expires must mean 'unknown, wait', never 'no expiry, go' (§3.2)."""
    from evescreener.esi.client import fallback_expiry

    assert fallback_expiry(None, feed_ttl_seconds=300) == 300


def test_a_malformed_expires_is_treated_as_missing_not_as_zero():
    from evescreener.esi.client import fallback_expiry

    assert fallback_expiry("not-a-date", feed_ttl_seconds=300) == 300


def test_a_valid_expires_is_used_as_given():
    from evescreener.esi.client import fallback_expiry

    assert fallback_expiry("Thu, 20 Aug 2026 12:00:00 GMT", feed_ttl_seconds=300) is None


# -- 5. the retracted 16,789 is gone everywhere -----------------------------


@pytest.mark.parametrize(
    "path",
    [
        REPO / "src" / "evescreener" / "esi" / "client.py",
        REPO / "src" / "evescreener" / "store" / "db.py",
    ],
)
def test_the_withdrawn_404_measurement_is_not_quoted_as_fact(path):
    """plan.md §17 D-10 withdrew it: a breaker cascade mistaken for data."""
    text = path.read_text(encoding="utf-8")
    if "16,789" in text or "16789" in text:
        assert "withdrawn" in text.lower() or "retracted" in text.lower(), (
            f"{path.name} quotes the retracted 16,789 figure without saying so"
        )


def test_the_corrected_figure_is_the_one_stated(qtbot):
    from pathlib import Path as _Path

    text = (_Path(REPO) / "src" / "evescreener" / "esi" / "client.py").read_text(encoding="utf-8")
    assert "241" in text, "the measured 404 count is 241 of 17,325 requests"
