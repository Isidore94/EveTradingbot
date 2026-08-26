"""HAULING — what to put in the hold, for THIS pilot (plan.md §23.11).

The page is a thin shell over `hauling.py`: it captures the operator's own
constraints on the GUI thread, hands them to a worker as an immutable tuple,
and paints what comes back. It computes nothing itself and it cannot fetch —
nothing under `gui/` may import an ESI client, and the network-isolation probe
walks this module like every other one.

What the page owes the reader, on screen rather than in a footnote: both
generations and their ages, that a snapshot is not a tape, that order age is
"last placed **or repriced**" and unverified, how much exit depth `min_volume`
put out of reach, which SDE build the routes came from, and the reason behind
every UNKNOWN.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...hauling import (
    ALONG_ROUTE,
    DEDICATED,
    EXIT_MODELS,
    MODES,
    OBJECTIVES,
    ORDER_AGE_CAVEAT,
    SNAPSHOT_CAVEAT,
    HaulProfile,
    ShipProfile,
    scan_hauls,
    scan_inputs,
)
from ...haulreport import haul_basket
from ...positioning import render_basket
from ...routes import PROFILES
from ..widgets import BLANK, SortableTable, format_isk
from .base import DeskPage

__all__ = ["HaulingPage"]

HEADERS = [
    "item",
    "route",
    "qty",
    "capital",
    "net profit",
    "net ROI",
    "cargo m³",
    "pickup",
    "trip",
    "liquidation",
    "route risk",
    "reliability",
    "rank",
]

#: Where the control strip's state is remembered. `state.db`'s `meta` table,
#: never `config.toml`: that file is the hand-edited, comment-rich contract of
#: §11 D1, and no TOML writer exists among the four runtime dependencies.
FILTER_KEY = "hauling.filters"

CAVEAT = f"{SNAPSHOT_CAVEAT} {ORDER_AGE_CAVEAT}"


class HaulingPage(DeskPage):
    title = "HAULING"
    heavy = True

    #: How long the control strip must sit still before a scan starts. A spin
    #: box emits on every step, so dragging capital from 250 to 254 dispatched
    #: five scans: the token guard discarded four *results*, but four jobs had
    #: already been handed to a four-thread pool.
    DEBOUNCE_MS = 500

    # -- layout ------------------------------------------------------------
    def build(self) -> None:
        self._systems_loaded = False
        self._ships_loaded = False
        self._result = None
        self._loading = False

        # Built before the controls, because laying them out sets their
        # initial values and that fires their signals.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self._debounced_refresh)

        self.layout.addWidget(self._controls())

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.layout.addWidget(self.summary)

        self.stamp = QLabel("")
        self.stamp.setWordWrap(True)
        self.stamp.setStyleSheet("QLabel { color: #c48a20; }")
        self.layout.addWidget(self.stamp)

        caveat = QLabel(CAVEAT)
        caveat.setWordWrap(True)
        caveat.setStyleSheet("QLabel { color: #c48a20; }")
        self.layout.addWidget(caveat)

        splitter = QSplitter(Qt.Vertical)
        self.table = SortableTable(HEADERS)
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.cellDoubleClicked.connect(self._charted)
        splitter.addWidget(self.table)
        splitter.addWidget(self._drawer())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.layout.addWidget(splitter, 1)

        self._restore_filters()

    def _controls(self) -> QWidget:
        bar = QWidget()
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(0, 0, 0, 0)

        first = QHBoxLayout()
        self.origin = QLineEdit()
        self.origin.setPlaceholderText("current system")
        self.origin.editingFinished.connect(self._controls_changed)
        first.addWidget(QLabel("from"))
        first.addWidget(self.origin, 1)

        self.destination = QLineEdit()
        self.destination.setPlaceholderText("where you were going anyway (optional)")
        self.destination.editingFinished.connect(self._controls_changed)
        first.addWidget(QLabel("to"))
        first.addWidget(self.destination, 1)

        self.mode = QComboBox()
        for mode in MODES:
            self.mode.addItem(mode, mode)
        self.mode.currentIndexChanged.connect(self._controls_changed)
        first.addWidget(QLabel("mode"))
        first.addWidget(self.mode)

        self.ship = QComboBox()
        self.ship.currentIndexChanged.connect(self._controls_changed)
        first.addWidget(QLabel("ship"))
        first.addWidget(self.ship)
        outer.addLayout(first)

        second = QHBoxLayout()
        # Zero means "whatever the selected ship carries". A non-zero default
        # here silently overrode the ship picker, which made the picker look
        # live while the hold never changed.
        self.cargo = self._spin(
            second,
            "cargo m³",
            0.0,
            2_000_000.0,
            0.0,
            step=1000.0,
            special="use ship profile",
        )
        self.capital = self._spin(
            second, "capital (M)", 0.0, 1_000_000.0, 250.0, step=50.0, suffix=""
        )
        self.exposure = self._spin(second, "exposure (M)", 0.0, 1_000_000.0, 250.0, step=50.0)

        self.minutes = QSpinBox()
        self.minutes.setRange(1, 24 * 60)
        self.minutes.setValue(int(self.data.config.hauling.default_session_minutes))
        self.minutes.valueChanged.connect(self._controls_changed)
        second.addWidget(QLabel("minutes"))
        second.addWidget(self.minutes)

        self.max_jumps = QSpinBox()
        self.max_jumps.setRange(0, 100)
        self.max_jumps.setValue(0)
        self.max_jumps.setSpecialValueText("any")
        self.max_jumps.valueChanged.connect(self._controls_changed)
        second.addWidget(QLabel("max jumps"))
        second.addWidget(self.max_jumps)

        self.security = QComboBox()
        for profile in PROFILES:
            self.security.addItem(profile, profile)
        self.security.setCurrentText(self.data.config.routes.security_profile)
        self.security.currentIndexChanged.connect(self._controls_changed)
        second.addWidget(QLabel("security"))
        second.addWidget(self.security)

        self.objective = QComboBox()
        for objective in OBJECTIVES:
            self.objective.addItem(objective, objective)
        self.objective.setCurrentText(self.data.config.hauling.default_objective)
        self.objective.currentIndexChanged.connect(self._controls_changed)
        second.addWidget(QLabel("rank by"))
        second.addWidget(self.objective)

        self.exit_model = QComboBox()
        for model in EXIT_MODELS:
            self.exit_model.addItem(model, model)
        self.exit_model.currentIndexChanged.connect(self._controls_changed)
        second.addWidget(QLabel("exit"))
        second.addWidget(self.exit_model)

        self.max_wait_days = self._spin(
            second,
            "max wait days",
            0.0,
            365.0,
            float(self.data.config.hauling.default_max_wait_days),
            step=0.5,
            decimals=1,
            special="no cap",
        )

        self.nearest = QPushButton("Nearest first")
        self.nearest.clicked.connect(lambda: self.table.sort_by(HEADERS.index("pickup")))
        second.addStretch(1)
        second.addWidget(self.nearest)
        outer.addLayout(second)
        return bar

    def _spin(
        self,
        row,
        label,
        low,
        high,
        value,
        *,
        step=1.0,
        suffix="",
        special="",
        decimals=0,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        if special:
            spin.setSpecialValueText(special)
        spin.valueChanged.connect(self._controls_changed)
        row.addWidget(QLabel(label))
        row.addWidget(spin)
        return spin

    def _drawer(self) -> QWidget:
        self.drawer = QTabWidget()
        self.panes: dict[str, QPlainTextEdit] = {}
        for name in (
            "ladders",
            "why this size",
            "route",
            "costs",
            "liquidity",
            "mixed cargo",
            "rejected",
        ):
            pane = QPlainTextEdit()
            pane.setReadOnly(True)
            pane.setLineWrapMode(QPlainTextEdit.NoWrap)
            self.panes[name] = pane
            self.drawer.addTab(pane, name)
        return self.drawer

    # -- controls ----------------------------------------------------------
    def _controls_changed(self) -> None:
        """Restart the countdown. The operator is probably still adjusting."""
        if self._loading:
            return
        self._debounce.start()

    def _debounced_refresh(self) -> None:
        if self._shutdown:
            return
        self._save_filters()
        self.ensure_current(force=True)

    def shutdown(self) -> None:
        """Stop the countdown before the widgets go (§21 R7)."""
        timer = getattr(self, "_debounce", None)
        if timer is not None:
            timer.stop()
        super().shutdown()

    def _save_filters(self) -> None:
        try:
            self.data.db.set_meta(FILTER_KEY, json.dumps(self._filters()))
        except Exception:  # noqa: BLE001 - a saved filter is a convenience, never a gate
            pass

    def _filters(self) -> dict:
        return {
            "origin": self.origin.text().strip(),
            "destination": self.destination.text().strip(),
            "mode": self.mode.currentData(),
            "ship": self.ship.currentText(),
            "cargo": self.cargo.value(),
            "capital": self.capital.value(),
            "exposure": self.exposure.value(),
            "minutes": self.minutes.value(),
            "max_jumps": self.max_jumps.value(),
            "security": self.security.currentData(),
            "objective": self.objective.currentData(),
            "exit_model": self.exit_model.currentData(),
            "max_wait_days": self.max_wait_days.value(),
        }

    def _restore_filters(self) -> None:
        try:
            stored = self.data.db.get_meta(FILTER_KEY)
            saved = json.loads(stored) if stored else {}
        except Exception:  # noqa: BLE001 - a corrupt filter is not a broken desk
            saved = {}
        if not saved:
            return
        self._loading = True
        try:
            self.origin.setText(str(saved.get("origin", "")))
            self.destination.setText(str(saved.get("destination", "")))
            for combo, key in (
                (self.mode, "mode"),
                (self.security, "security"),
                (self.objective, "objective"),
                (self.exit_model, "exit_model"),
            ):
                if saved.get(key):
                    combo.setCurrentText(str(saved[key]))
            for spin, key in (
                (self.cargo, "cargo"),
                (self.capital, "capital"),
                (self.exposure, "exposure"),
                (self.max_wait_days, "max_wait_days"),
            ):
                if saved.get(key) is not None:
                    spin.setValue(float(saved[key]))
            if saved.get("minutes"):
                self.minutes.setValue(int(saved["minutes"]))
            if saved.get("max_jumps") is not None:
                self.max_jumps.setValue(int(saved["max_jumps"]))
        finally:
            self._loading = False

    # -- compute -----------------------------------------------------------
    def job_input(self) -> tuple:
        """Every widget-derived value, frozen on the GUI thread (§22 S3)."""
        if not hasattr(self, "origin"):
            return ()
        filters = self._filters()
        return tuple(sorted((key, value) for key, value in filters.items()))

    def compute(self, data, job_input=()):
        """Off-thread, local data only. The worker never reaches back."""
        controls = dict(job_input)
        with data.thread_local_db() as db:
            from ...routes import RouteCache

            sources, destinations, depths, graph, names, badges, packaged = scan_inputs(
                data.config, db
            )
            system_names = db.system_names()
            by_name = {name.lower(): system for system, name in system_names.items()}
            ships = [dict(row) for row in db.haul_profiles()]

            chosen = next(
                (ship for ship in ships if ship["name"] == controls.get("ship")),
                ships[0] if ships else None,
            )
            cargo = float(controls.get("cargo") or 0.0)
            ship = (
                ShipProfile.from_row(chosen)
                if chosen
                else ShipProfile.from_config(data.config, name="ad hoc", cargo_m3=cargo)
            )
            if cargo > 0:
                # An explicit override; zero defers to the ship profile.
                from dataclasses import replace

                ship = replace(ship, usable_cargo_m3=cargo)

            unresolved: list[str] = []
            origin = _resolve_system(by_name, controls.get("origin"), unresolved)
            intended = _resolve_system(by_name, controls.get("destination"), unresolved)
            max_jumps = int(controls.get("max_jumps") or 0) or None

            # A control strip can always be half-filled. The engine refuses an
            # along_route profile with nowhere to go, so the page resolves the
            # contradiction itself — ranked as dedicated, and it says so on
            # screen rather than crashing the worker or silently mis-charging.
            mode = str(controls.get("mode") or DEDICATED)
            if mode == ALONG_ROUTE and intended is None:
                unresolved.append(
                    "along_route needs a destination — ranked as dedicated, so the whole "
                    "trip is charged rather than the detour"
                )
                mode = DEDICATED

            profile = HaulProfile.from_config(
                data.config,
                ship=ship,
                current_system=origin,
                intended_destination=intended,
                mode=mode,
                capital_isk=float(controls.get("capital") or 0.0) * 1e6,
                max_exposure_isk=float(controls.get("exposure") or 0.0) * 1e6,
                session_minutes=float(controls.get("minutes") or 30),
                max_jumps=max_jumps,
                security_profile=str(controls.get("security") or "highsec"),
                objective=str(controls.get("objective") or "isk_per_active_minute"),
                exit_model=str(controls.get("exit_model") or "immediate"),
                max_wait_days=float(
                    controls.get("max_wait_days")
                    if controls.get("max_wait_days") is not None
                    else data.config.hauling.default_max_wait_days
                ),
            )
            scan = scan_hauls(
                data.config,
                profile,
                stations=sources,
                destinations=destinations,
                depths=depths,
                graph=graph,
                route_cache=RouteCache(db, enabled=data.config.routes.cache),
                names=names,
                badges=badges,
                packaged_volume=packaged,
                liquidity=_liquidity_for(data.config, db, depths, profile),
            )
        for note in unresolved:
            scan.notes.append(note)
        return {
            "scan": scan,
            # Built here, on the worker, by the same function the CLI calls, so
            # the desk and the report cannot drift into two baskets.
            "basket": render_basket(haul_basket(scan, config=data.config)),
            "systems": sorted(system_names.values()),
            "ships": [ship["name"] for ship in ships],
            "ship": ship.name,
        }

    # -- paint -------------------------------------------------------------
    def paint(self, result) -> None:
        self._result = result
        scan = result["scan"]
        self._populate_pickers(result)

        rows, payloads = [], []
        for plan in scan.plans:
            rows.append(_plan_row(plan))
            payloads.append(plan)
        for pair in scan.unknown_pairs:
            rows.append(_unknown_row(pair))
            payloads.append(pair)
        self.table.set_rows(rows, payloads)
        for index in range(self.table.rowCount()):
            payload = self.table.payload(index)
            badge = getattr(payload, "badge", None)
            if badge:
                self.table.badge_row(index, badge)

        counts = scan.rejection_counts
        dropped = scan.dropped_unrankable
        self.summary.setText(
            f"{len(scan.plans):,} plan(s) from {scan.candidates_considered:,} priced "
            f"candidate(s) across {scan.pairs_considered} station pair(s); "
            f"{len(scan.rejected):,} rejected"
            + (
                " — " + ", ".join(f"{reason} {count}" for reason, count in counts.items())
                if counts
                else ""
            )
            + (
                " · unrankable under this objective: "
                + ", ".join(f"{reason} {count}" for reason, count in sorted(dropped.items()))
                if dropped
                else ""
            )
            + (" · " + " · ".join(scan.notes) if scan.notes else "")
        )
        self.stamp.setText(_generation_stamp(scan))
        self.panes["rejected"].setPlainText(_rejected_text(scan))
        self.panes["mixed cargo"].setPlainText(result.get("basket") or "")
        if not scan.plans:
            for name in ("ladders", "why this size", "route", "costs", "liquidity"):
                self.panes[name].setPlainText(
                    "Nothing cleared. That is an answer: the Forge's median spread is "
                    "98.8%, and §17 measured 10–14 of 151,113 hub pairs clearing at "
                    "0.25B. Open the 'rejected' tab to see what was refused and why."
                )

    def _populate_pickers(self, result) -> None:
        self._loading = True
        try:
            if not self._systems_loaded and result.get("systems"):
                completer = QCompleter(result["systems"])
                completer.setCaseSensitivity(Qt.CaseInsensitive)
                self.origin.setCompleter(completer)
                self.destination.setCompleter(QCompleter(result["systems"]))
                self._systems_loaded = True
            names = result.get("ships") or []
            if names and [self.ship.itemText(i) for i in range(self.ship.count())] != names:
                current = self.ship.currentText()
                self.ship.clear()
                self.ship.addItems(names)
                if current in names:
                    self.ship.setCurrentText(current)
                self._ships_loaded = True
        finally:
            self._loading = False

    # -- row actions -------------------------------------------------------
    def _current(self):
        row = self.table.currentRow()
        return self.table.payload(row) if row >= 0 else None

    def _selected(self) -> None:
        payload = self._current()
        if payload is None or isinstance(payload, dict):
            reason = payload.get("reason") if isinstance(payload, dict) else None
            for name in ("ladders", "why this size", "route", "costs", "liquidity"):
                self.panes[name].setPlainText(f"UNKNOWN — {reason}" if reason else "select a row")
            return
        self.panes["ladders"].setPlainText(_ladders_text(payload))
        self.panes["why this size"].setPlainText(_why_text(payload))
        self.panes["route"].setPlainText(_route_text(payload, self.data))
        self.panes["costs"].setPlainText(_costs_text(payload))
        self.panes["liquidity"].setPlainText(_liquidity_text(payload))

    def _charted(self, view_row: int, _column: int) -> None:
        payload = self.table.payload(view_row)
        if payload is not None and not isinstance(payload, dict):
            self.chart_requested.emit(int(payload.type_id))


def _resolve_system(by_name: dict, text, unresolved: list) -> int | None:
    """Resolve a typed system name loudly, or not at all — never a guess.

    Module level rather than a method: `compute()` runs on a worker and must
    reach nothing on the page, not even a helper that happens to be pure
    (§22 S3).
    """
    if not text:
        return None
    system = by_name.get(str(text).strip().lower())
    if system is None:
        unresolved.append(f"no solar system named {text!r} in the SDE — check the spelling")
    return system


def _liquidity_for(config, db, depths, profile):
    """H3's scenario attachment, when this build has one."""
    try:
        from ...liquidity import liquidity_attachment
    except ImportError:  # pragma: no cover - present from H3 onward
        return None
    return liquidity_attachment(config, db, depths, profile)


# -- rendering helpers ------------------------------------------------------


def _cell(text: str, value):
    return (text, None if value is None or value != value else value)


def _blank():
    return (BLANK, None)


def _plan_row(plan) -> list:
    name = plan.type_name or f"type {plan.type_id}"
    if plan.badge:
        name = f"{name} · {plan.badge}"
    route = f"{plan.source.label} → {plan.destination.label}"
    return [
        _cell(name, name),
        _cell(route, route),
        _cell(f"{plan.quantity:,.0f}", plan.quantity),
        _cell(format_isk(plan.source_cost), plan.source_cost),
        _cell(format_isk(plan.net_profit), plan.net_profit),
        _cell(f"{plan.net_roi_pct:.2f}%", plan.net_roi_pct),
        _cell(f"{plan.cargo_m3:,.0f}" if plan.cargo_m3 is not None else BLANK, plan.cargo_m3),
        _cell(
            f"{plan.pickup.jumps} j" if plan.pickup.known else BLANK,
            plan.pickup.jumps if plan.pickup.known else None,
        ),
        _cell(
            f"{plan.total_jumps} j / {plan.active_minutes:.0f} min"
            if plan.total_jumps is not None and plan.active_minutes is not None
            else BLANK,
            plan.active_minutes,
        ),
        _cell(
            f"{plan.liquidation_days:.2f} d" if plan.liquidation_days is not None else BLANK,
            plan.liquidation_days,
        ),
        _cell(_risk_text(plan), plan.haul.min_display_security),
        _cell(
            (plan.reliability or {}).get("grade", BLANK) if plan.reliability else BLANK,
            (plan.reliability or {}).get("score") if plan.reliability else None,
        ),
        _cell(format_isk(plan.rank_score), plan.rank_score),
    ]


def _unknown_row(pair: dict) -> list:
    route = f"{pair['source']['label']} → {pair['destination']['label']}"
    return [
        _cell("UNKNOWN", None),
        _cell(route, route),
        *[_blank() for _ in range(9)],
        _cell(pair.get("state", "UNKNOWN"), None),
        _cell(pair.get("reason", ""), None),
    ]


def _risk_text(plan) -> str:
    haul = plan.haul
    if not haul.known:
        return "UNKNOWN"
    if haul.min_display_security is None:
        return "UNKNOWN security on route"
    parts = [f"min {haul.min_display_security:.1f}"]
    if haul.nullsec_systems:
        parts.append(f"{haul.nullsec_systems} null")
    if haul.lowsec_systems:
        parts.append(f"{haul.lowsec_systems} low")
    if haul.borderline_systems:
        parts.append(f"{haul.borderline_systems} at 0.5")
    return ", ".join(parts)


def _generation_stamp(scan) -> str:
    parts = []
    for region, generation in sorted(scan.generations.items()):
        age = generation.get("age_minutes")
        text = f"region {region}: " + (f"{age:.0f} min" if age is not None else "UNKNOWN")
        if generation.get("stale"):
            text += " — STALE, prices nothing"
        parts.append(text)
    parts.append(f"SDE build {scan.sde_build}")
    return "generations · " + " · ".join(parts)


def _ladders_text(plan) -> str:
    lines = [
        f"{plan.type_name or plan.type_id} — {plan.quantity:,.0f} units",
        "",
        f"SOURCE {plan.source.label}: {plan.source_levels} level(s) consumed, "
        f"WAP {plan.source_wap:,.2f}, committed {plan.source_cost:,.0f} ISK",
        "  next unit would cost: "
        + (
            f"{plan.source_marginal_next_price:,.2f}"
            if plan.source_marginal_next_price is not None
            else "UNKNOWN — the curve ends here"
        ),
        f"  curve complete: {plan.source_depth_complete}",
        "",
        f"DESTINATION {plan.destination.label}: {plan.dest_levels} level(s) consumed, "
        f"WAP {plan.dest_wap:,.2f}, gross {plan.gross_sale:,.0f} ISK",
        "  next unit would fetch: "
        + (
            f"{plan.dest_marginal_next_price:,.2f}"
            if plan.dest_marginal_next_price is not None
            else "UNKNOWN — the curve ends here"
        ),
        f"  curve complete: {plan.dest_depth_complete}",
        f"  min_volume-blocked depth at this station: "
        f"{plan.min_volume_excluded_qty:,.0f} units (excluded on purpose)",
        "  structure-resident share of the depth sold into: "
        + (
            f"{plan.dest_structure_share:.0%}"
            if plan.dest_structure_share is not None
            else "UNKNOWN"
        ),
        f"  oldest order on the levels used: {plan.oldest_issued or 'UNKNOWN'}",
        "",
        ORDER_AGE_CAVEAT,
    ]
    return "\n".join(lines)


def _why_text(plan) -> str:
    lines = [
        f"ranked on {plan.rank_score:,.2f} ({'the objective for this run'})",
        "",
        "quantity         capital      net profit",
    ]
    for quantity, cost, net, rejected in plan.breakpoints:
        if rejected:
            marker = "  <- refused (marginal <= 0)"
        elif abs(quantity - plan.quantity) < 1e-9:
            marker = "  <- chosen"
        else:
            marker = ""
        lines.append(f"{quantity:>12,.0f}  {cost:>14,.0f}  {net:>14,.0f}{marker}")
    if plan.marginal_net_isk is not None:
        lines.append("")
        lines.append(f"the final chunk netted {plan.marginal_net_isk:,.0f} ISK")
    if plan.alternatives:
        lines.extend(["", "other objectives would have chosen:"])
        for objective, entry in sorted(plan.alternatives.items()):
            lines.append(f"  {objective}: {entry['quantity']:,.0f} units ({entry['value']:,.2f})")
    return "\n".join(lines)


def _route_text(plan, data) -> str:
    names = {}
    try:
        names = data.db.system_names()
    except Exception:  # noqa: BLE001 - a name is a nicety; the route is the fact
        names = {}
    lines = [
        f"profile: {plan.haul.profile} · SDE build {plan.haul.sde_build}",
        "pickup: "
        + (
            f"{plan.pickup.jumps} jumps" if plan.pickup.known else f"UNKNOWN — {plan.pickup.reason}"
        ),
        f"haul: {plan.haul.jumps} jumps",
        f"charged: {plan.total_jumps} jumps, {plan.active_minutes:.0f} active minutes"
        if plan.active_minutes is not None
        else "charged: UNKNOWN",
    ]
    if plan.detour_jumps is not None:
        lines.append(f"detour beyond the trip you were making anyway: {plan.detour_jumps} jumps")
    lines.append("")
    for system in plan.haul.systems:
        lines.append(f"  {names.get(system, system)}")
    return "\n".join(lines)


def _costs_text(plan) -> str:
    return "\n".join(
        [
            f"source cost         {plan.source_cost:>18,.2f}",
            f"gross sale          {plan.gross_sale:>18,.2f}",
            f"sales tax           {-plan.sales_tax_isk:>18,.2f}",
            f"broker fee          {0.0:>18,.2f}  (a taker pays none; only a posted order does)",
            "                    " + "-" * 18,
            f"net profit          {plan.net_profit:>18,.2f}",
            f"net ROI             {plan.net_roi_pct:>17,.2f}%",
            "",
            f"ISK per active minute  {plan.isk_per_active_minute:,.2f}"
            if plan.isk_per_active_minute is not None
            else "ISK per active minute  UNKNOWN",
            f"ISK per capital-day    {plan.isk_per_capital_day:,.2f}"
            if plan.isk_per_capital_day is not None
            else "ISK per capital-day    UNKNOWN",
            "",
            _freight_line(plan),
            "",
            SNAPSHOT_CAVEAT,
        ]
    )


def _freight_line(plan) -> str:
    """Self-haul versus paying PushX. UNKNOWN never blocks the row above it."""
    freight = plan.freight or {}
    if freight.get("state") != "OK":
        return f"self-haul vs PushX   UNKNOWN — {freight.get('reason', 'not quoted')}"
    per_minute = freight.get("your_time_isk_per_minute")
    return (
        f"self-haul vs PushX   freight {freight['freight_isk']:,.0f} ISK "
        f"({freight['route']}), net if shipped {freight['net_if_shipped']:,.0f}"
        + (
            f" — flying it yourself is worth {per_minute:,.0f} ISK/min"
            if per_minute is not None
            else ""
        )
    )


def _liquidity_text(plan) -> str:
    if not plan.liquidity:
        return (
            "No liquidity scenario attached to this row.\n\n"
            f"{plan.liquidation_reason or 'The exit is modelled as immediate.'}"
        )
    lines = ["scenario      days to liquidate"]
    for name, days in sorted((plan.liquidity.get("scenarios") or {}).items()):
        lines.append(f"{name:<12}  " + (f"{days:.2f}" if days is not None else "UNKNOWN"))
    for label, value in sorted((plan.liquidity.get("assumptions") or {}).items()):
        lines.append(f"ASSUMED {label}: {value}")
    if plan.maker:
        lines.extend(["", "maker scenario (display only, never the default rank):"])
        for key, value in sorted(plan.maker.items()):
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _rejected_text(scan) -> str:
    lines = ["Every rejected candidate keeps its reason. This is the denominator.", ""]
    for reason, count in scan.rejection_counts.items():
        lines.append(f"{reason}: {count:,}")
    lines.append("")
    for rejection in scan.rejected[:200]:
        subject = rejection.type_name or rejection.type_id or "pair"
        lines.append(
            f"  {rejection.reason:<24} {subject} "
            f"{rejection.source_station or ''}→{rejection.dest_station or ''} "
            f"{rejection.detail}"
        )
    if len(scan.rejected) > 200:
        lines.append(f"  … and {len(scan.rejected) - 200:,} more")
    return "\n".join(lines)
