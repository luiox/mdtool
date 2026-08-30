"""Shared Qt building blocks used across all pages.

- :class:`BaseTab` — 页面统一契约（``set_root_dir`` / ``shutdown``），
  ``log()`` 直接发布到全局 :mod:`qtui.logbus`，页面不再各自持有日志面板。
- :class:`SearchBar` / :class:`PathRow` — 两个高频重复模式（搜索行、
  路径选择行）的组件化收敛。
- 视觉状态一律走动态属性 + theme.py 的 QSS 属性选择器
  （``[muted="true"]``、``[severity="..."]``），禁止内联样式表；
  运行时改属性后必须调用 :func:`repolish` 让 QSS 重新求值。
"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from mdtool.desktop.qtui.logbus import get_log_bus


def repolish(widget: QWidget) -> None:
    """Re-evaluate QSS attribute selectors after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def muted_label(text: str = "") -> QLabel:
    """A gray secondary-text label (QSS: ``QLabel[muted="true"]``)."""
    lab = QLabel(text)
    lab.setProperty("muted", True)
    return lab


def set_severity(label: QLabel, severity: Optional[str]) -> None:
    """Set ``[severity="success|warning|error"]`` (None clears the state)."""
    if severity is None:
        label.setProperty("severity", "")
    else:
        label.setProperty("severity", severity)
    repolish(label)


class BaseTab(QWidget):
    """Base class for every page.

    The main window drives pages through :meth:`set_root_dir` and
    :meth:`shutdown`; logging flows through the global log bus so pages
    carry no log UI of their own.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = None  # injected by MainWindow after construction
        self.root_dir: Optional[Path] = None

    # ── hooks (default no-ops) ──

    def set_root_dir(self, root_dir: Optional[Path]):
        """Called when the user picks/changes the knowledge-base root."""
        self.root_dir = root_dir

    def shutdown(self):
        """Called on app exit. Override to flush state / stop threads."""
        pass

    # ── convenience ──

    def log(self, msg: str, level: str = "INFO"):
        get_log_bus().publish(msg, level)


class SearchBar(QWidget):
    """搜索行：输入框（正则提示）+ 范围下拉 + 搜索/清空。

    notes_browser 与 file_browser 原本各写一份完全相同的布局，收敛于此。
    """

    search_requested = Signal(str, str)  # pattern, scope

    def __init__(self, scopes=("文件名", "正文", "文件名+正文"),
                 show_scope: bool = True, show_clear: bool = True, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("搜索…（支持正则）")
        self.edit.returnPressed.connect(self._emit_search)
        lay.addWidget(self.edit, 1)
        self.scope = QComboBox()
        self.scope.addItems(list(scopes))
        self.scope.setVisible(show_scope)
        lay.addWidget(self.scope)
        btn = QPushButton("搜索")
        btn.setProperty("variant", "primary")
        btn.clicked.connect(self._emit_search)
        lay.addWidget(btn)
        if show_clear:
            clear = QPushButton("清空")
            clear.clicked.connect(self.clear_input)
            lay.addWidget(clear)

    def _emit_search(self):
        self.search_requested.emit(self.edit.text().strip(), self.scope.currentText())

    def text(self) -> str:
        return self.edit.text().strip()

    def scope_text(self) -> str:
        return self.scope.currentText()

    def clear_input(self):
        self.edit.clear()
        self.edit.setFocus()


class PathRow(QWidget):
    """路径输入 + 浏览按钮。``mode``: dir | open | save。"""

    path_picked = Signal(str)

    def __init__(self, path: str = "", mode: str = "dir",
                 dialog_title: str = "选择目录", file_filter: str = "",
                 placeholder: str = "", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.dialog_title = dialog_title
        self.file_filter = file_filter
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = QLineEdit(path)
        self.edit.setPlaceholderText(placeholder)
        lay.addWidget(self.edit, 1)
        btn = QPushButton("浏览…")
        btn.clicked.connect(self.browse)
        lay.addWidget(btn)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_path(self, path: str):
        self.edit.setText(path)

    def browse(self):
        if self.mode == "dir":
            p = QFileDialog.getExistingDirectory(self, self.dialog_title)
        elif self.mode == "save":
            p, _ = QFileDialog.getSaveFileName(self, self.dialog_title, "", self.file_filter)
        else:
            p, _ = QFileDialog.getOpenFileName(self, self.dialog_title, "", self.file_filter)
        if p:
            self.edit.setText(p)
            self.path_picked.emit(p)
