"""Main window — the app shell of ``markdown_util`` (PySide6).

Holds the QTabWidget and drives every tab through the uniform ``BaseTab``
contract (``set_root_dir`` / ``shutdown``).

The tray icon uses Qt's own :class:`QSystemTrayIcon`.
"""

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QToolBar,
    QWidget,
)

from qtui.icons import tray_icon
from qtui.widgets import BaseTab
from _version import get_version


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Markdown 工具集 v{get_version()}")
        self.resize(1100, 750)
        self._closing = False
        self._tray = None

        self._build_toolbar()
        self._build_tabs()
        self._setup_tray()

    # ── toolbar ──

    def _build_toolbar(self):
        tb = QToolBar(self)
        tb.setMovable(False)
        self.addToolBar(tb)

        act = QAction("选择项目根目录", self)
        act.triggered.connect(self.select_root_dir)
        tb.addAction(act)
        self.root_label = QLabel("未选择")
        self.root_label.setStyleSheet("color: gray; padding-left: 10px;")
        tb.addWidget(self.root_label)

    def select_root_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择项目根目录")
        if not path:
            return
        self.root_dir = Path(path)
        self.root_label.setText(str(self.root_dir))
        self.root_label.setStyleSheet("color: black; padding-left: 10px;")
        for tab in self._all_tabs():
            tab.set_root_dir(self.root_dir)

    @property
    def root_dir(self) -> Optional[Path]:
        return getattr(self, "_root_dir", None)

    @root_dir.setter
    def root_dir(self, value: Optional[Path]):
        self._root_dir = value

    # ── tabs ──

    def _build_tabs(self):
        from qtui.tabs.notes_browser import NotesBrowserTab
        from qtui.tabs.file_browser import FileBrowserTab
        from qtui.tabs.media_server import MediaServerTab
        from qtui.tabs.space_fix import SpaceFixTab
        from qtui.tabs.image_check import ImageCheckTab
        from qtui.tabs.migrate import MigrateTab

        self.tab_notes = NotesBrowserTab()
        self.tab_files = FileBrowserTab()
        self.tab_media = MediaServerTab()
        self.tab_space = SpaceFixTab()
        self.tab_check = ImageCheckTab()
        self.tab_migrate = MigrateTab()

        # Inject back-references so tabs can reach siblings (e.g. file browser
        # → migrate) and the main window without import cycles.
        for t in self._all_tabs():
            t.main_window = self

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.tab_notes, "笔记库")
        self.tabs.addTab(self.tab_files, "文件浏览器")
        self.tabs.addTab(self.tab_media, "本地媒体服务器")
        self.tabs.addTab(self.tab_space, "空格转下划线修复")
        self.tabs.addTab(self.tab_check, "图片校验")
        self.tabs.addTab(self.tab_migrate, "图片迁移")
        self.setCentralWidget(self.tabs)

    def _all_tabs(self):
        return [self.tab_notes, self.tab_files, self.tab_media,
                self.tab_space, self.tab_check, self.tab_migrate]

    def open_migrate(self, path):
        """Hand off a file/dir to the Migrate tab (called by the file browser)."""
        self.tabs.setCurrentWidget(self.tab_migrate)
        self.tab_migrate.load_file(path)

    # ── tray ──

    def _setup_tray(self):
        from PySide6.QtWidgets import QSystemTrayIcon, QMenu
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(tray_icon(), self)
        self._tray.setToolTip("Markdown 工具集")
        menu = QMenu(self)
        act_show = menu.addAction("显示窗口")
        act_show.triggered.connect(self._tray_show)
        act_quit = menu.addAction("退出程序")
        act_quit.triggered.connect(self._tray_quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        from PySide6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._tray_show()

    def _tray_show(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_quit(self):
        self._closing = True
        self._do_shutdown()
        QApplication.quit()

    # ── close handling ──

    def closeEvent(self, event):  # noqa: N802 - Qt override
        if self._closing:
            event.accept()
            return
        # Mirror the old app.py behavior: ask exit vs. minimize-to-tray.
        dlg = QMessageBox(self)
        dlg.setWindowTitle("退出确认")
        dlg.setText("关闭程序时执行什么操作？")
        b_exit = dlg.addButton("退出程序", QMessageBox.ButtonRole.AcceptRole)
        b_min = dlg.addButton("最小化到托盘", QMessageBox.ButtonRole.RejectRole)
        dlg.addButton("取消", QMessageBox.ButtonRole.DestructiveRole)
        dlg.exec()
        clicked = dlg.clickedButton()
        if clicked is b_exit:
            self._closing = True
            self._do_shutdown()
            event.accept()
        elif clicked is b_min:
            self.hide()
            event.ignore()
        else:
            event.ignore()

    def _do_shutdown(self):
        for tab in self._all_tabs():
            try:
                tab.shutdown()
            except Exception:
                pass


class _PlaceholderTab(BaseTab):
    """Shown for tabs not yet ported; points users to the legacy entry."""

    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QLabel
        lay = __import__("PySide6.QtWidgets", fromlist=["QVBoxLayout"]).QVBoxLayout(self)
        lab = QLabel(message)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setStyleSheet("color: gray; font-size: 13px;")
        lay.addWidget(lab)
