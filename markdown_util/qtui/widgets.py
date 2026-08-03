"""Shared Qt widgets used across all tabs.

Replaces the per-tab duplicated idioms from the tkinter version:
- the disabled ``tk.Text`` log console → :class:`LogPanel`
- the implicit ``(notebook, app)`` tab contract → :class:`BaseTab`
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class LogPanel(QTextEdit):
    """A read-only monospaced log console with timestamped lines.

    Drop-in replacement for the ``self.log_text`` disabled ``tk.Text`` +
    ``self.log(msg)`` idiom repeated in every tkinter tab. ``append`` already
    adds a newline in Qt, so callers pass a single line.
    """

    def __init__(self, parent=None, height_lines: int = 6):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 9))
        self._height_lines = height_lines

    def sizeHint(self):  # noqa: N802 - Qt override
        fm = self.fontMetrics()
        return self.minimumSizeHint()

    def append_line(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.append(f"[{ts}] [{level}] {msg}")
        self.moveCursor(QTextCursor.MoveOperation.End)

    def clear_log(self):
        self.clear()


class BaseTab(QWidget):
    """Base class for every tab.

    Replaces the tkinter ``__init__(notebook, app)`` + ``self.frame`` contract.
    Subclasses build their UI into ``self`` (a QWidget) directly. The main
    window drives every tab through :meth:`set_root_dir` and :meth:`shutdown`,
    so there is no per-tab ``on_root_dir_changed`` name to remember.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = None  # injected by MainWindow after construction
        self.root_dir: Optional[Path] = None

    # ── hooks (default no-ops) ──

    def set_root_dir(self, root_dir: Optional[Path]):
        """Called when the user picks/changes the project root."""
        self.root_dir = root_dir

    def shutdown(self):
        """Called on app exit. Override to flush state / stop threads."""
        pass

    # ── convenience ──

    def log(self, msg: str, level: str = "INFO"):
        """Override or set ``self.log_panel`` to enable logging."""
        panel = getattr(self, "log_panel", None)
        if isinstance(panel, LogPanel):
            panel.append_line(msg, level)


def hbox(parent=None, *widgets, spacing=4, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    """Quick horizontal layout helper to reduce boilerplate."""
    lay = QHBoxLayout(parent)
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        lay.addWidget(w)
    return lay


def vbox(parent=None, *widgets, spacing=4, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout(parent)
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        lay.addWidget(w)
    return lay


def labeled_row(label_text: str, widget: QWidget, label_width: int = 14,
                parent=None) -> QHBoxLayout:
    """A label + widget row matching the old ``ttk.Label(width=14)`` layout."""
    lay = QHBoxLayout(parent)
    lab = QLabel(label_text)
    lab.setMinimumWidth(label_width * 7)  # rough char→px; tuned visually later
    lay.addWidget(lab)
    lay.addWidget(widget, stretch=1)
    return lay


def make_button(text: str, slot, parent=None) -> QPushButton:
    b = QPushButton(text, parent)
    b.clicked.connect(slot)
    return b
