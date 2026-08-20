"""SETTINGS — operator settings the desk itself can change (plan.md §20.5).

**Why not `config.toml`.** That file is the locked configuration contract
(§11 D1): hand-edited, comment-rich, and mirrored by a committed
`config.example.toml`. Writing it from a GUI would need a TOML *writer*, which
is not among the four locked runtime dependencies, and a hand-rolled one would
silently eat the comments that make the file readable. So settings the desk
owns live in the `meta` key/value table of `state.db` — already present,
already outside git, and already the place `schema_version` lives.

**The desk still cannot send anything.** Nothing under `gui/` may import an
HTTP client (`tests/test_gui.py` walks the AST of every file here and fails on
`httpx`, `urllib`, `requests` or anything named `esi`). This page stores what
ntfy needs and no more; evaluating alert rules and delivering them is §20.5,
and it happens in the daemon, which is where a thing that reaches the network
belongs.

So the "Save" button here writes a row and tells you the truth: nothing will
be sent until the alert engine lands.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from .base import DeskPage

__all__ = ["SettingsPage", "NTFY_KEYS", "ntfy_settings", "save_ntfy_settings"]

#: `meta` keys this page owns. Namespaced so nothing collides with the
#: schema/version rows that already live in that table.
NTFY_KEYS = {
    "server": "ntfy.server",
    "topic": "ntfy.topic",
    "token": "ntfy.token",
    "priority": "ntfy.priority",
}

DEFAULT_SERVER = "https://ntfy.sh"
DEFAULT_PRIORITY = "default"


def ntfy_settings(db) -> dict[str, str]:
    """Read the stored ntfy settings. Missing keys come back as empty strings."""
    values = {name: (db.get_meta(key) or "") for name, key in NTFY_KEYS.items()}
    if not values["server"]:
        values["server"] = DEFAULT_SERVER
    if not values["priority"]:
        values["priority"] = DEFAULT_PRIORITY
    return values


def save_ntfy_settings(db, values: dict[str, str]) -> None:
    """Persist ntfy settings. Blank means blank — never a silent default."""
    for name, key in NTFY_KEYS.items():
        db.set_meta(key, str(values.get(name, "")).strip())


class SettingsPage(DeskPage):
    title = "SETTINGS"

    def build(self) -> None:
        header = QLabel("Notifications — ntfy")
        header.setStyleSheet("QLabel { font-size: 15px; font-weight: 600; }")
        self.layout.addWidget(header)

        blurb = QLabel(
            "Stored in the local state database, not in config.toml, and never "
            "committed. Leave the token blank for a public ntfy.sh topic; a "
            "public topic is readable by anyone who guesses its name, so pick "
            "an unguessable one."
        )
        blurb.setWordWrap(True)
        self.layout.addWidget(blurb)

        form = QWidget()
        fields = QFormLayout(form)
        self.server = QLineEdit()
        self.server.setPlaceholderText(DEFAULT_SERVER)
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("e.g. eve-desk-8f3a1c")
        self.token = QLineEdit()
        self.token.setPlaceholderText("optional — for a protected topic")
        self.token.setEchoMode(QLineEdit.Password)
        self.priority = QLineEdit()
        self.priority.setPlaceholderText(DEFAULT_PRIORITY)
        fields.addRow("server", self.server)
        fields.addRow("topic", self.topic)
        fields.addRow("token", self.token)
        fields.addRow("priority", self.priority)
        self.layout.addWidget(form)

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._save)
        self.layout.addWidget(self.save_button)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.layout.addWidget(self.message)

        self.pending = QLabel(
            "Nothing is delivered yet. The alert engine that reads these "
            "settings is plan.md §20.5 and is not built; the desk itself may "
            "not reach the network at all, by design."
        )
        self.pending.setWordWrap(True)
        self.pending.setStyleSheet("QLabel { color: #c48a20; }")
        self.layout.addWidget(self.pending)
        self.layout.addStretch(1)

    def repopulate(self) -> None:
        values = ntfy_settings(self.data.db)
        self.server.setText(values["server"])
        self.topic.setText(values["topic"])
        self.token.setText(values["token"])
        self.priority.setText(values["priority"])

    def _save(self) -> None:
        values = {
            "server": self.server.text().strip() or DEFAULT_SERVER,
            "topic": self.topic.text().strip(),
            "token": self.token.text().strip(),
            "priority": self.priority.text().strip() or DEFAULT_PRIORITY,
        }
        if not values["topic"]:
            # A server with no topic is not a partial setup, it is an unusable
            # one. Saying so beats storing it and failing later (§5).
            self.message.setText("a topic is required — nothing saved")
            return
        save_ntfy_settings(self.data.db, values)
        self.message.setText(
            f"saved — {values['server'].rstrip('/')}/{values['topic']}"
            + (" (token stored)" if values["token"] else " (no token)")
        )
