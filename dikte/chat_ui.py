"""The conversation with the agent, drawn as a chat.

Every command and answer is already in the history file; this reads the ones
asked in the current project since the conversation was last reset, and shows
them as bubbles: what was said on the right, the answer on the left with its
markdown rendered. While the agent works, a last bubble names what it is doing
("Running a command…"), which is the same line the corner indicator shows.
"""

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from . import assistant
from .i18n import t

MAX_MESSAGES = 60


def conversation(rows, conf):
    """[(question, answer, ts, warning)] for the project's current conversation."""
    here = os.path.normcase(os.path.normpath(assistant.working_dir(conf)))
    since = conf["chat_since"] or ""
    out = []
    for row in rows:
        if row.get("mode") != "ask" or row.get("failed"):
            continue
        project = row.get("project")
        if project and os.path.normcase(os.path.normpath(project)) != here:
            continue
        if since and row.get("ts", "") < since:
            continue
        out.append((row.get("question") or row.get("raw", ""), row.get("text", ""),
                    row.get("ts", ""), row.get("cleanup_error", "")))
    return out[-MAX_MESSAGES:]


class Bubble(QFrame):
    def __init__(self, text, mine, meta="", markdown=False, parent=None):
        super().__init__(parent)
        self.setObjectName("bubble_user" if mine else "bubble_agent")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        self.body = QLabel(text)
        self.body.setTextFormat(Qt.TextFormat.MarkdownText if markdown
                                else Qt.TextFormat.PlainText)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                          | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.body.setOpenExternalLinks(True)
        self.body.setObjectName("bubble_text_user" if mine else "bubble_text")
        layout.addWidget(self.body)
        if meta:
            note = QLabel(meta)
            note.setObjectName("bubble_meta")
            note.setTextFormat(Qt.TextFormat.PlainText)
            note.setWordWrap(True)
            layout.addWidget(note)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
        self._natural = max((self.body.fontMetrics().horizontalAdvance(line)
                             for line in text.splitlines() or [""]), default=0) + 4

    def fit(self, width):
        """As wide as the text wants, up to `width`; a word-wrapped label
        otherwise settles on the narrowest width that still fits it."""
        self.setMaximumWidth(width)
        self.body.setMinimumWidth(min(self._natural, width - 24))


class ChatView(QScrollArea):
    """Bubbles in a column, kept scrolled to the newest one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chat")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._inner = QWidget()
        self._inner.setObjectName("chat_inner")
        self._column = QVBoxLayout(self._inner)
        self._column.setContentsMargins(4, 8, 4, 8)
        self._column.setSpacing(8)
        self._column.addStretch(1)
        self.setWidget(self._inner)
        self._messages = None
        self._pending = None
        self._pending_row = None
        self._empty = QLabel(t("Say “Claude” or press the button, and the "
                               "conversation shows up here."))
        self._empty.setObjectName("muted")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._column.addWidget(self._empty)

    def _row(self, bubble, mine):
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        if mine:
            line.addStretch(1)
        line.addWidget(bubble, 0)
        if not mine:
            line.addStretch(1)
        bubble.fit(self._bubble_width())
        return row

    def _bubble_width(self):
        return max(240, int(self.viewport().width() * 0.82))

    def _clear(self):
        while self._column.count() > 2:
            item = self._column.takeAt(2)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._pending_row = None

    def set_messages(self, messages, pending=None):
        """Redraw only when something changed, so a selection survives refreshes."""
        if messages == self._messages and pending == self._pending:
            return
        self._messages, self._pending = list(messages), pending
        self._clear()
        self._empty.setVisible(not messages and not pending)
        for question, answer, ts, warning in messages:
            if question:
                self._column.addWidget(self._row(Bubble(question, True, ts[11:16]), True))
            if answer:
                meta = warning.splitlines()[0] if warning else ""
                self._column.addWidget(self._row(
                    Bubble(answer, False, meta, markdown=True), False))
        if pending:
            bubble = Bubble(pending, False)
            bubble.setObjectName("bubble_pending")
            self._pending_row = self._row(bubble, False)
            self._column.addWidget(self._pending_row)
        QTimer.singleShot(0, self._to_bottom)

    def last_answer(self):
        for _question, answer, _ts, _warning in reversed(self._messages or []):
            if answer:
                return answer
        return ""

    def _to_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self._bubble_width()
        for bubble in self._inner.findChildren(Bubble):
            bubble.fit(width)
