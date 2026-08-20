"""The Paper Buy / Close form (plan.md §19 Amendment 2).

**One ledger, two doors, identical validation.** This form calls
`PaperLedger.open_position` and `PaperLedger.close_position` — the same
methods the CLI calls, with the same refusals. There is no GUI-side pricing
path and no GUI-side relaxation of the rules: if the CLI would refuse a stale
book, this refuses it, renders the reason inline, and the refusal lands in the
ledger exactly as it does from the terminal.

Everything is prefilled so that taking a trade costs one confirm:

* live ask-walk entry price, with the age of the book it came from shown,
* stop from the ATR risk unit,
* target from anchored value,
* setup tag from whichever setup fired (editable; `discretionary` allowed),
* notional from the configured default tier.

A prefill is a starting point, not a claim. Every field is editable, and a
field that could not be computed is left empty and labelled rather than
guessed at.

Reasons are required in both directions (§19 Amendment 3): an opening needs at
least one like tag, and the dialog will not submit without one.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..paper import PaperLedger, Refusal
from ..signals.atr import atr_last, risk_unit
from .widgets import format_isk

__all__ = [
    "PaperCloseDialog",
    "PaperOpenDialog",
    "PassDialog",
    "confirm",
    "prefill_for",
]


def prefill_for(data, type_id: int, *, setup_tag: str = "discretionary") -> dict:
    """Everything the open form starts with, computed from local data only.

    A value that cannot be measured comes back as None, and the form shows an
    empty field with a reason. It is never replaced with a plausible number.
    """
    from ..screen import _tier_prices, setup_params
    from ..signals.avwap import anchored_vwap_bands
    from ..signals.setup import anchor_grid

    config = data.config
    frame = data.frame_for(type_id)
    tier0 = float(config.costs.notional_tiers_isk[0])
    prefill = {
        "type_id": int(type_id),
        "type_name": data.type_name(type_id),
        "tier": data.tier(type_id),
        "notional_isk": float(config.paper.default_notional_isk),
        "setup_tag": setup_tag,
        "entry_price": None,
        "stop_price": None,
        "target_price": None,
        "atr": None,
        "book_age_minutes": data.book_age_minutes,
        "book_sweep_ts": data.book_sweep_ts,
        "book_stale": data.book_is_stale,
        "reasons": [],
    }
    if frame.empty:
        prefill["reasons"].append("no bars in the lake for this type")
        return prefill

    params = setup_params(config)
    atr = atr_last(
        frame,
        length=params.atr_length,
        winsor_k=params.atr_winsor_k,
        winsor_window=params.atr_winsor_window,
    )
    prefill["atr"] = atr

    book = data.book
    if book is not None and not book.empty:
        sell = book[(book["type_id"] == int(type_id)) & (book["side"] == "sell")]
        if not sell.empty:
            prefill["entry_price"] = _tier_prices(sell.iloc[-1], (tier0,)).get(tier0)
    if prefill["entry_price"] is None:
        prefill["reasons"].append("no ask-walk price in the local book at the smallest tier")

    entry = prefill["entry_price"]
    # The stop is one risk unit below entry — the same unit the screen and the
    # brief use, so an R here means what an R means everywhere else.
    unit = risk_unit(
        frame,
        length=params.atr_length,
        winsor_k=params.atr_winsor_k,
        winsor_window=params.atr_winsor_window,
    )
    prefill["risk_unit"] = unit
    if entry and unit:
        prefill["stop_price"] = max(0.0, float(entry) - float(unit))
    elif not unit:
        prefill["reasons"].append("ATR UNKNOWN, so no stop could be prefilled")

    anchors = anchor_grid(
        frame, step_days=params.anchor_lookback_days, anchor_dates=data.anchor_dates
    )
    bands = anchored_vwap_bands(frame, anchors[-1] if anchors else 0)
    if bands.known:
        prefill["target_price"] = float(bands.vwap)
    else:
        prefill["reasons"].append("anchored value UNKNOWN, so no target could be prefilled")
    return prefill


class _TagBox(QGroupBox):
    """Checkboxes for one direction of the reason vocabulary."""

    def __init__(self, title: str, reasons, parent=None) -> None:
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        self._boxes: list[tuple[str, QCheckBox]] = []
        if not reasons:
            layout.addWidget(QLabel("no config/reasons.jsonl — decisions cannot be qualified"))
        for reason in reasons:
            box = QCheckBox(reason.label)
            box.setToolTip(reason.notes)
            layout.addWidget(box)
            self._boxes.append((reason.tag, box))

    def selected(self) -> list[str]:
        return [tag for tag, box in self._boxes if box.isChecked()]

    def check(self, tag: str) -> None:
        for candidate, box in self._boxes:
            if candidate == tag:
                box.setChecked(True)


class PaperOpenDialog(QDialog):
    """Paper Buy. One confirm, after the reasons are given."""

    def __init__(self, data, prefill: dict, ledger: PaperLedger | None = None, parent=None):
        super().__init__(parent)
        self.data = data
        self.prefill = prefill
        self.ledger = ledger or PaperLedger(data.config.paths.paper_ledger, data.config)
        self.record: dict | None = None
        self.setWindowTitle(f"Paper buy — {prefill['type_name']}")
        self.setModal(True)

        layout = QVBoxLayout(self)
        header = QLabel(f"{prefill['type_name']} (type {prefill['type_id']})")
        header.setStyleSheet("QLabel { font-size: 14px; font-weight: 600; }")
        layout.addWidget(header)
        if prefill.get("tier") == "THIN":
            badge = QLabel("THIN — 100–999 units/day. You may not get out of this at size.")
            badge.setStyleSheet("QLabel { color: #c48a20; }")
            layout.addWidget(badge)

        self.stamp = QLabel(self._stamp_text())
        self.stamp.setStyleSheet("QLabel { color: #c48a20; }")
        layout.addWidget(self.stamp)

        form = QFormLayout()
        self.notional = QDoubleSpinBox()
        self.notional.setRange(0.0, 1e15)
        self.notional.setDecimals(0)
        self.notional.setGroupSeparatorShown(True)
        self.notional.setValue(float(prefill["notional_isk"]))
        form.addRow("notional (ISK)", self.notional)

        self.entry = QLabel(
            format_isk(prefill["entry_price"]) if prefill["entry_price"] else "UNKNOWN"
        )
        form.addRow("entry (live ask walk)", self.entry)

        self.stop = self._price_field(prefill["stop_price"])
        form.addRow("stop (from ATR risk unit)", self.stop)
        self.target = self._price_field(prefill["target_price"])
        form.addRow("target (anchored value)", self.target)

        self.setup = QComboBox()
        self.setup.setEditable(True)
        names = [setup.name for setup in data.setups] + ["discretionary"]
        self.setup.addItems(names)
        self.setup.setCurrentText(prefill.get("setup_tag") or "discretionary")
        form.addRow("setup tag", self.setup)

        self.thesis = QLineEdit()
        self.thesis.setPlaceholderText("one sentence you can argue with")
        form.addRow("thesis", self.thesis)
        layout.addLayout(form)

        self.tags = _TagBox("Why I like it (at least one)", data.vocabulary.likes)
        layout.addWidget(self.tags)
        self.free_text = QPlainTextEdit()
        self.free_text.setPlaceholderText("optional free text")
        self.free_text.setMaximumHeight(56)
        layout.addWidget(self.free_text)

        for reason in prefill.get("reasons", []):
            note = QLabel(f"· {reason}")
            note.setStyleSheet("QLabel { color: #c48a20; }")
            layout.addWidget(note)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("QLabel { color: #ff8080; }")
        self.error.setVisible(False)
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Record paper buy")
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _price_field(self, value) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText("UNKNOWN — leave empty rather than guess")
        if value is not None:
            field.setText(f"{float(value):.4f}")
        return field

    def _stamp_text(self) -> str:
        age = self.prefill.get("book_age_minutes")
        if age is None:
            return "book: UNKNOWN — no sweep on disk; this will be refused"
        text = f"book: swept {str(self.prefill.get('book_sweep_ts'))[:16]}Z, {age:.0f} min old"
        if self.prefill.get("book_stale"):
            text += " — STALE; a fill priced off this is refused, not repriced"
        return text

    def _float(self, field: QLineEdit):
        text = field.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def submit(self) -> None:
        """One confirm. A refusal renders inline and is recorded, never hidden."""
        try:
            self.record = self.ledger.open_position(
                type_id=self.prefill["type_id"],
                type_name=self.prefill["type_name"],
                notional_isk=float(self.notional.value()),
                book=self.data.book,
                thesis=self.thesis.text(),
                setup_tag=self.setup.currentText(),
                like_tags=self.tags.selected(),
                reason_text=self.free_text.toPlainText(),
                stop_price=self._float(self.stop),
                target_price=self._float(self.target),
                vocabulary=self.data.vocabulary,
                now=self.data.loaded_at,
            )
        except Refusal as refusal:
            self.error.setText(f"Refused, and recorded as a refusal: {refusal}")
            self.error.setVisible(True)
            return
        self.accept()


class PaperCloseDialog(QDialog):
    """Symmetric exit: live bid walk prefilled, or a real fill you actually got."""

    def __init__(self, data, position: dict, ledger: PaperLedger | None = None, parent=None):
        super().__init__(parent)
        self.data = data
        self.position = position
        self.ledger = ledger or PaperLedger(data.config.paths.paper_ledger, data.config)
        self.record: dict | None = None
        self.setWindowTitle(f"Close — {position.get('type_name') or position.get('type_id')}")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"{position.get('type_name')} · opened {str(position.get('at'))[:16]}Z · "
                f"entry {format_isk(position.get('entry_effective_price'))}"
            )
        )
        stamp = QLabel(
            f"book: {self.data.book_age_minutes:.0f} min old"
            if self.data.book_age_minutes is not None
            else "book: UNKNOWN — no sweep on disk"
        )
        stamp.setStyleSheet("QLabel { color: #c48a20; }")
        layout.addWidget(stamp)

        form = QFormLayout()
        self.note = QLineEdit()
        form.addRow("note", self.note)
        self.actual = QLineEdit()
        self.actual.setPlaceholderText(
            "gross unit price you REALLY sold at — leave empty to price off the live bid walk"
        )
        form.addRow("actual fill price", self.actual)
        layout.addLayout(form)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("QLabel { color: #ff8080; }")
        self.error.setVisible(False)
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Record close")
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def submit(self) -> None:
        actual = self.actual.text().strip()
        try:
            self.record = self.ledger.close_position(
                position_id=self.position["position_id"],
                book=self.data.book,
                note=self.note.text(),
                actual_price=float(actual) if actual else None,
                now=self.data.loaded_at,
            )
        except (Refusal, ValueError) as refusal:
            self.error.setText(f"Refused, and recorded as a refusal: {refusal}")
            self.error.setVisible(True)
            return
        self.accept()


class PassDialog(QDialog):
    """ "Not today" / "bad signal" — a recorded decision, with its reasons."""

    def __init__(self, data, type_id: int, *, action="not_today", setup_tag=None, parent=None):
        super().__init__(parent)
        self.data = data
        self.type_id = int(type_id)
        self.action = action
        self.setup_tag = setup_tag
        self.record: dict | None = None
        self.ledger = PaperLedger(data.config.paths.paper_ledger, data.config)
        title = "Not today" if action == "not_today" else "Bad signal"
        self.setWindowTitle(f"{title} — {data.type_name(type_id)}")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "A pass with its reasons is a recorded decision, measured forward like "
                "a trade. 'Not today' clears this from today's queue only — it never "
                "removes a Focus name."
            )
        )
        self.tags = _TagBox("Why I don't like it (at least one)", data.vocabulary.dislikes)
        layout.addWidget(self.tags)
        self.free_text = QPlainTextEdit()
        self.free_text.setPlaceholderText("optional free text")
        self.free_text.setMaximumHeight(56)
        layout.addWidget(self.free_text)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("QLabel { color: #ff8080; }")
        self.error.setVisible(False)
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Record pass")
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def submit(self) -> None:
        frame = self.data.frame_for(self.type_id)
        close = float(frame["close"].iloc[-1]) if not frame.empty else None
        try:
            self.record = self.ledger.record_pass(
                type_id=self.type_id,
                type_name=self.data.type_name(self.type_id),
                action=self.action,
                dislike_tags=self.tags.selected(),
                reason_text=self.free_text.toPlainText(),
                setup_tag=self.setup_tag,
                close=close,
                vocabulary=self.data.vocabulary,
                now=self.data.loaded_at,
            )
        except Refusal as refusal:
            self.error.setText(f"Refused, and recorded as a refusal: {refusal}")
            self.error.setVisible(True)
            return
        self.accept()


def confirm(parent, title: str, text: str) -> bool:
    """A yes/no. Used only where an action is not itself reversible."""
    answer = QMessageBox.question(
        parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
    )
    return answer == QMessageBox.Yes
