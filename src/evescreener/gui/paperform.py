"""The Paper Buy / Close form (plan.md §19 Amendment 2).

**One ledger, two doors, identical validation.** This form calls
`PaperLedger.open_position` and `PaperLedger.close_position` — the same
methods the CLI calls, with the same refusals. There is no GUI-side pricing
path and no GUI-side relaxation of the rules: if the CLI would refuse a stale
book, this refuses it, renders the reason inline, and the refusal lands in the
ledger exactly as it does from the terminal.

**A refusal is shown before it is earned.** The form prices itself through
`paper.book_quote` — the ledger's own function, not a parallel one — so what
the operator reads is what the ledger will do. A book that cannot price this
trade says so in the header, greys the button and names the fix; it does not
show a confident number and then refuse the submit. That failure was the
whole complaint: a filled-in entry price beside a stale, pre-R1 book.

Everything else is prefilled so that taking a trade costs one confirm:

* the entry price for the chosen **fill model**, with the age of the book it
  came from shown,
* stop from the ATR risk unit,
* target from anchored value,
* setup tag from whichever setup fired (editable; `discretionary` allowed),
* notional from the configured default tier — and *only* the configured
  tiers, because those are the only sizes the depth walk measures and any
  other value is refused.

A prefill is a starting point, not a claim. Every field is editable, and a
field that could not be computed is left empty and labelled rather than
guessed at.

The **fill model** is the operator's choice between the two things he can
actually do (§12.2, amended 2026-08-21): cross the spread now, or post and
wait. Maker is priced one tick in front of the executable bid with the broker
fee charged, and the form says plainly that such a fill is *assumed* —
the book proves the price was postable, never that anyone traded into it.
There is no mid: no EVE order type fills there.

Reasons are required in both directions (§19 Amendment 3): an opening needs at
least one like tag, and the dialog will not submit without one.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..paper import FILL_MODELS, PaperLedger, Refusal, book_quote
from ..signals.atr import atr_last, risk_unit
from .widgets import format_isk

__all__ = [
    "PaperCloseDialog",
    "PaperOpenDialog",
    "PassDialog",
    "confirm",
    "entry_quote_for",
    "prefill_for",
]

#: Wide enough that a refusal sentence and a book stamp are read, not clipped.
#: The old dialog sized itself to its shortest row and cut both in half.
MIN_WIDTH = 620

FILL_MODEL_HELP = {
    "taker": (
        "taker — cross the spread now, walking the asks at this notional. "
        "The only fill the snapshot proves you could have got."
    ),
    "maker": (
        "maker — post one tick above the executable bid and wait, paying the "
        "broker fee. ASSUMED: the book proves the price was postable, never "
        "that anyone traded into it. Queue position is unpriced."
    ),
}


def entry_quote_for(data, type_id: int, *, notional_isk: float, fill_model: str):
    """The entry price the ledger would use, through the ledger's own function.

    The form must never compute a price the ledger would not accept. So it
    does not compute one at all: it calls `paper.book_quote` with the same
    staleness budget, the same tier and the same executable-quote contract,
    and renders whatever comes back — a price, or a reason.
    """
    config = data.config
    tiers = list(config.costs.notional_tiers_isk)
    tier_index = next(
        (index for index, tier in enumerate(tiers) if abs(tier - float(notional_isk)) < 1.0),
        None,
    )
    if tier_index is None:
        return None, (
            f"{float(notional_isk):,.0f} ISK is not one of the configured tiers "
            f"{', '.join(f'{tier:,.0f}' for tier in tiers)} — the depth walk only "
            "measures those"
        )
    side = "sell" if fill_model == "taker" else "buy"
    quote = book_quote(
        data.book,
        type_id=int(type_id),
        side=side,
        tier_index=tier_index,
        now=data.loaded_at,
        stale_after_minutes=config.paper.stale_book_minutes,
        fill_model=fill_model,
        tick=config.paper.maker_tick_isk,
    )
    return quote, None


def prefill_for(data, type_id: int, *, setup_tag: str = "discretionary") -> dict:
    """Everything the open form starts with, computed from local data only.

    A value that cannot be measured comes back as None, and the form shows an
    empty field with a reason. It is never replaced with a plausible number.
    """
    from ..screen import setup_params
    from ..signals.avwap import anchored_vwap_bands
    from ..signals.setup import anchor_grid

    config = data.config
    frame = data.frame_for(type_id)
    fill_model = str(config.paper.default_fill_model)
    prefill = {
        "type_id": int(type_id),
        "type_name": data.type_name(type_id),
        "tier": data.tier(type_id),
        "notional_isk": float(config.paper.default_notional_isk),
        "notional_tiers": [float(tier) for tier in config.costs.notional_tiers_isk],
        "fill_model": fill_model,
        "setup_tag": setup_tag,
        "entry_price": None,
        "stop_price": None,
        "target_price": None,
        "atr": None,
        "risk_unit": None,
        "book_age_minutes": data.book_age_minutes,
        "book_sweep_ts": data.book_sweep_ts,
        "book_stale": data.book_is_stale,
        "reasons": [],
    }
    if frame.empty:
        prefill["reasons"].append("no bars in the lake for this type")
        return prefill

    params = setup_params(config)
    prefill["atr"] = atr_last(
        frame,
        length=params.atr_length,
        winsor_k=params.atr_winsor_k,
        winsor_window=params.atr_winsor_window,
    )

    quote, tier_reason = entry_quote_for(
        data, type_id, notional_isk=prefill["notional_isk"], fill_model=fill_model
    )
    if tier_reason:
        prefill["reasons"].append(tier_reason)
    elif quote.price is None:
        # Keep naming the walk the operator asked for: "no ask-walk price" is
        # what a taker entry failing to price has always been called.
        label = "ask-walk price" if fill_model == "taker" else "postable bid"
        prefill["reasons"].append(f"no {label} in the local book — {quote.reason}")
    else:
        prefill["entry_price"] = float(quote.price)

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

    def on_change(self, slot) -> None:
        for _tag, box in self._boxes:
            box.toggled.connect(slot)


def _note(text: str, colour: str = "#c48a20") -> QLabel:
    """A wrapped line. Unwrapped, every one of these was clipped mid-sentence."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"QLabel {{ color: {colour}; }}")
    return label


class PaperOpenDialog(QDialog):
    """Paper Buy. One confirm, after the reasons are given."""

    def __init__(self, data, prefill: dict, ledger: PaperLedger | None = None, parent=None):
        super().__init__(parent)
        self.data = data
        self.prefill = prefill
        self.ledger = ledger or PaperLedger(data.config.paths.paper_ledger, data.config)
        self.record: dict | None = None
        self.quote = None
        self.quote_reason: str | None = None
        self.setWindowTitle(f"Paper buy — {prefill['type_name']}")
        self.setModal(True)
        self.setMinimumWidth(MIN_WIDTH)

        layout = QVBoxLayout(self)
        header = QLabel(f"{prefill['type_name']} (type {prefill['type_id']})")
        header.setStyleSheet("QLabel { font-size: 14px; font-weight: 600; }")
        layout.addWidget(header)
        if prefill.get("tier") == "THIN":
            layout.addWidget(
                _note("THIN — 100–999 units/day. You may not get out of this at size.")
            )

        self.stamp = _note("")
        layout.addWidget(self.stamp)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Only the configured tiers exist. A free-entry spin box let the
        # operator type a number the ledger was always going to refuse.
        self.notional = QComboBox()
        tiers = prefill.get("notional_tiers") or list(data.config.costs.notional_tiers_isk)
        for tier in tiers:
            self.notional.addItem(f"{float(tier):,.0f}", float(tier))
        default = float(prefill["notional_isk"])
        index = self.notional.findData(default)
        self.notional.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("notional (ISK)", self.notional)

        self.fill_model = QComboBox()
        for model in FILL_MODELS:
            self.fill_model.addItem(model, model)
        model_index = self.fill_model.findData(str(prefill.get("fill_model") or "taker"))
        self.fill_model.setCurrentIndex(model_index if model_index >= 0 else 0)
        form.addRow("fill model", self.fill_model)

        self.entry = QLabel("")
        self.entry.setWordWrap(True)
        form.addRow("entry", self.entry)
        self.sizing = QLabel("")
        self.sizing.setWordWrap(True)
        form.addRow("you get", self.sizing)

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

        self.model_note = _note("", "#8899aa")
        layout.addWidget(self.model_note)

        self.tags = _TagBox("Why I like it (at least one)", data.vocabulary.likes)
        layout.addWidget(self.tags)
        self.free_text = QPlainTextEdit()
        self.free_text.setPlaceholderText("optional free text")
        self.free_text.setMaximumHeight(56)
        layout.addWidget(self.free_text)

        for reason in prefill.get("reasons", []):
            layout.addWidget(_note(f"· {reason}"))

        self.blocker = _note("", "#ff8080")
        layout.addWidget(self.blocker)
        self.error = _note("", "#ff8080")
        self.error.setVisible(False)
        layout.addWidget(self.error)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Record paper buy")
        self.buttons.accepted.connect(self.submit)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.notional.currentIndexChanged.connect(self.reprice)
        self.fill_model.currentIndexChanged.connect(self.reprice)
        self.thesis.textChanged.connect(self.revalidate)
        self.tags.on_change(self.revalidate)
        self.reprice()

    # -- pricing -----------------------------------------------------------
    def notional_isk(self) -> float:
        return float(self.notional.currentData())

    def current_fill_model(self) -> str:
        return str(self.fill_model.currentData())

    def reprice(self) -> None:
        """Re-ask the ledger's own pricing function, and render its answer."""
        model = self.current_fill_model()
        self.model_note.setText(FILL_MODEL_HELP.get(model, ""))
        self.quote, self.quote_reason = entry_quote_for(
            self.data,
            self.prefill["type_id"],
            notional_isk=self.notional_isk(),
            fill_model=model,
        )
        self.stamp.setText(self._stamp_text())
        price = self.quote.price if self.quote is not None else None
        if price is None:
            self.entry.setText("UNKNOWN")
            self.sizing.setText("—")
        else:
            broker = self.ledger.costs.broker_fee_at(self.quote.location_id)
            effective = price if model == "taker" else price * (1.0 + broker / 100.0)
            units = self.notional_isk() / effective
            source = (
                "ask walk at this notional"
                if model == "taker"
                else f"executable bid + {self.data.config.paper.maker_tick_isk:g} tick, "
                f"+{broker:.2f}% broker"
            )
            self.entry.setText(f"{format_isk(effective)}  ({source})")
            self.sizing.setText(f"{units:,.0f} units for {self.notional_isk():,.0f} ISK")
            # The stop follows the price it is a distance from, unless the
            # operator has already moved it himself.
            self._retrack_stop(effective)
        self.revalidate()

    def _retrack_stop(self, effective: float) -> None:
        unit = self.prefill.get("risk_unit")
        if not unit:
            return
        expected = self.stop.property("autofill")
        current = self.stop.text().strip()
        if current and expected is not None and current != str(expected):
            return  # operator typed his own stop; leave it alone
        value = f"{max(0.0, float(effective) - float(unit)):.4f}"
        self.stop.setText(value)
        self.stop.setProperty("autofill", value)

    def _stamp_text(self) -> str:
        age = self.prefill.get("book_age_minutes")
        if age is None:
            return "book: UNKNOWN — no sweep on disk; this will be refused. Run: sweep-books"
        text = f"book: swept {str(self.prefill.get('book_sweep_ts'))[:16]}Z, {age:.0f} min old"
        if self.prefill.get("book_stale"):
            text += " — STALE; a fill priced off this is refused, not repriced. Run: sweep-books"
        return text

    # -- validation --------------------------------------------------------
    def blocking_reason(self) -> str | None:
        """Why `Record paper buy` is greyed, in the ledger's own words.

        Every branch here is a refusal the ledger would raise anyway. Showing
        it first does not relax anything; it just stops the operator filling
        in a form whose answer was already no.
        """
        if self.quote_reason:
            return self.quote_reason
        if self.quote is None or self.quote.price is None:
            reason = self.quote.reason if self.quote is not None else "no book sweep available"
            return f"cannot price this entry: {reason}"
        if not self.thesis.text().strip():
            return "a thesis sentence is required — one you can argue with later"
        if not self.tags.selected():
            return "at least one 'why I like it' tag is required"
        return None

    def revalidate(self) -> None:
        reason = self.blocking_reason()
        self.blocker.setText(reason or "")
        self.blocker.setVisible(bool(reason))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(reason is None)

    def _price_field(self, value) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText("UNKNOWN — leave empty rather than guess")
        if value is not None:
            text = f"{float(value):.4f}"
            field.setText(text)
            field.setProperty("autofill", text)
        return field

    def _float(self, field: QLineEdit):
        text = field.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def submit(self) -> None:
        """One confirm. A refusal renders inline and is recorded, never hidden.

        The button is greyed while `blocking_reason()` has something to say,
        but this method never trusts that: the ledger validates again, and the
        ledger is the authority.
        """
        try:
            self.record = self.ledger.open_position(
                type_id=self.prefill["type_id"],
                type_name=self.prefill["type_name"],
                notional_isk=self.notional_isk(),
                book=self.data.book,
                thesis=self.thesis.text(),
                setup_tag=self.setup.currentText(),
                like_tags=self.tags.selected(),
                reason_text=self.free_text.toPlainText(),
                stop_price=self._float(self.stop),
                target_price=self._float(self.target),
                vocabulary=self.data.vocabulary,
                now=self.data.loaded_at,
                fill_model=self.current_fill_model(),
            )
        except Refusal as refusal:
            self.error.setText(f"Refused, and recorded as a refusal: {refusal}")
            self.error.setVisible(True)
            return
        self.accept()


class PaperCloseDialog(QDialog):
    """Symmetric exit: the position's own fill model prefilled, or a real fill."""

    def __init__(self, data, position: dict, ledger: PaperLedger | None = None, parent=None):
        super().__init__(parent)
        self.data = data
        self.position = position
        self.ledger = ledger or PaperLedger(data.config.paths.paper_ledger, data.config)
        self.record: dict | None = None
        self.setWindowTitle(f"Close — {position.get('type_name') or position.get('type_id')}")
        self.setModal(True)
        self.setMinimumWidth(MIN_WIDTH)

        opened_model = str(position.get("fill_model") or "taker")
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"{position.get('type_name')} · opened {str(position.get('at'))[:16]}Z · "
            f"entry {format_isk(position.get('entry_effective_price'))} · "
            f"opened as {opened_model}"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        stamp = _note(
            f"book: {self.data.book_age_minutes:.0f} min old"
            if self.data.book_age_minutes is not None
            else "book: UNKNOWN — no sweep on disk"
        )
        layout.addWidget(stamp)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.fill_model = QComboBox()
        for model in FILL_MODELS:
            self.fill_model.addItem(model, model)
        index = self.fill_model.findData(opened_model)
        self.fill_model.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("exit fill model", self.fill_model)
        self.note = QLineEdit()
        form.addRow("note", self.note)
        self.actual = QLineEdit()
        self.actual.setPlaceholderText(
            "gross unit price you REALLY sold at — leave empty to price off the book"
        )
        form.addRow("actual fill price", self.actual)
        layout.addLayout(form)

        self.model_note = _note("", "#8899aa")
        layout.addWidget(self.model_note)
        self.fill_model.currentIndexChanged.connect(self._describe)
        self._describe()

        self.error = _note("", "#ff8080")
        self.error.setVisible(False)
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Record close")
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _describe(self) -> None:
        model = str(self.fill_model.currentData())
        self.model_note.setText(
            "taker exit — walk the bids now, net of sales tax."
            if model == "taker"
            else "maker exit — post one tick inside the executable ask, net of sales tax "
            "AND the broker fee. Still an ASSUMED fill until it really trades."
        )

    def submit(self) -> None:
        actual = self.actual.text().strip()
        try:
            self.record = self.ledger.close_position(
                position_id=self.position["position_id"],
                book=self.data.book,
                note=self.note.text(),
                actual_price=float(actual) if actual else None,
                now=self.data.loaded_at,
                fill_model=str(self.fill_model.currentData()),
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
        self.setMinimumWidth(MIN_WIDTH)

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "A pass with its reasons is a recorded decision, measured forward like "
            "a trade. 'Not today' clears this from today's queue only — it never "
            "removes a Focus name."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)
        self.tags = _TagBox("Why I don't like it (at least one)", data.vocabulary.dislikes)
        layout.addWidget(self.tags)
        self.free_text = QPlainTextEdit()
        self.free_text.setPlaceholderText("optional free text")
        self.free_text.setMaximumHeight(56)
        layout.addWidget(self.free_text)

        self.error = _note("", "#ff8080")
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
