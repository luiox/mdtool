"""Main window — app shell of ``markdown_util`` (PySide6).

布局（重构后）：
- 左侧边栏：品牌区 + 分组导航（知识库 / 工具）+ 底部「日志」入口。
- 右侧：顶栏（当前页标题 + 知识库根目录选择）+ 页面栈，一次只显示
  一个页面——取代旧版上下两个 QTabWidget 垂直堆叠的布局。

页面统一走 :class:`qtui.widgets.BaseTab` 契约（``set_root_dir`` /
``shutdown``）；日志经 :mod:`qtui.logbus` 汇入日志页。托盘与退出确认
行为保持与旧版一致。
"""

import html
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from _version import get_version
from qtui.icons import nav_icon, tray_icon
from qtui.logbus import get_log_bus
from qtui.theme import THEMES
from qtui.widgets import BaseTab, muted_label

# 导航模型：(分组名, [(key, 标题, 图标名)])；日志页独立于分组置底。
# Tab 类在 _build_pages 中延迟导入，避免 main_qt 启动时的导入环。
NAV: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("知识库", [
        ("library", "笔记库", "notes"),  # 散装目录 ⇄ db 容器共用一页（见 tabs/library.py）
        ("search", "搜索", "search"),    # 全文检索独立成页，不与目录树抢空间
    ]),
    ("工具", [
        ("media", "媒体服务器", "server"),
        ("check", "图片校验", "check"),
        ("migrate", "图片迁移", "migrate"),
        ("space", "空格修复", "space"),
    ]),
]
PAGE_TITLES = {key: title for _, items in NAV for key, title, _ in items}
SIDEBAR_WIDTH = 188


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Markdown 工具集 v{get_version()}")
        self.resize(1180, 760)
        self.setMinimumSize(980, 640)
        self._closing = False
        self._tray = None

        # key -> {"tab": BaseTab | None(log页), "button": QPushButton}
        self._pages: dict[str, dict] = {}
        self._nav_keys: list[str] = []
        self._build_shell()
        self.switch_page("library")  # 默认落在笔记库
        self.update_title()
        self._setup_tray()

    # ── 外壳 ──

    def _build_shell(self):
        central = QWidget(self)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_right(), 1)
        self.setCentralWidget(central)

    def _build_sidebar(self) -> QWidget:
        sb = QWidget(objectName="sidebar")
        sb.setFixedWidth(SIDEBAR_WIDTH)
        lay = QVBoxLayout(sb)
        lay.setContentsMargins(12, 14, 12, 12)
        lay.setSpacing(2)

        lay.addWidget(QLabel("mdtool", objectName="brandTitle"))
        lay.addWidget(QLabel(f"v{get_version()}", objectName="brandVersion"))
        lay.addSpacing(14)

        for group_name, items in NAV:
            lay.addSpacing(8)
            lay.addWidget(QLabel(group_name, objectName="navGroupLabel"))
            for key, title, icon_name in items:
                btn = self._make_nav_button(key, title, icon_name)
                self._pages[key] = {"button": btn}
                self._nav_keys.append(key)
                lay.addWidget(btn)

        lay.addStretch(1)
        log_btn = self._make_nav_button("log", "日志", "log")
        self._pages["log"] = {"button": log_btn}
        self._nav_keys.append("log")
        lay.addWidget(log_btn)
        return sb

    def _make_nav_button(self, key: str, title: str, icon_name: str) -> QPushButton:
        btn = QPushButton(f" {title}", checkable=True, objectName="navButton")
        btn.setIcon(nav_icon(icon_name))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _checked, k=key: self.switch_page(k))
        return btn

    def _build_right(self) -> QWidget:
        right = QWidget()
        lay = QVBoxLayout(right)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_header())
        lay.addSpacing(10)

        self.stack = QStackedWidget(right)
        lay.addWidget(self.stack, 1)
        self._build_pages()
        self.stack.addWidget(LogPage(self))  # 日志页固定在栈末尾
        return right

    def _build_header(self) -> QWidget:
        bar = QWidget(objectName="headerBar")
        bar.setFixedHeight(52)
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 0, 16, 0)

        self.page_title = QLabel("", objectName="pageTitle")
        h.addWidget(self.page_title)
        h.addSpacing(20)

        # 数据源：两个按钮即入口——散装选目录、db 选 .db 文件，选完即切；
        # 当前在哪个源由窗口标题标注，散装根目录路径挂在按钮 tooltip 上
        self.src_fs = QPushButton("散装目录")
        self.src_fs.clicked.connect(self._on_src_fs_clicked)
        self.src_db = QPushButton("db 容器")
        self.src_db.clicked.connect(self.pick_db_file)
        h.addWidget(self.src_fs)
        h.addWidget(self.src_db)

        h.addStretch(1)
        return bar

    def _build_pages(self):
        """实例化六个页面并按侧边栏顺序入栈；类延迟导入防导入环。"""
        from qtui.tabs.image_check import ImageCheckTab
        from qtui.tabs.library import LibraryPage
        from qtui.tabs.media_server import MediaServerTab
        from qtui.tabs.migrate import MigrateTab
        from qtui.tabs.search import SearchPage
        from qtui.tabs.space_fix import SpaceFixTab

        classes = {
            "library": LibraryPage,
            "search": SearchPage,
            "media": MediaServerTab,
            "check": ImageCheckTab,
            "migrate": MigrateTab,
            "space": SpaceFixTab,
        }
        for key in self._nav_keys:
            if key == "log":
                continue
            tab = classes[key]()
            tab.main_window = self
            self._pages[key]["tab"] = tab
            self.stack.addWidget(tab)

    # ── 页面切换 / 根目录 ──

    def switch_page(self, key: str):
        entry = self._pages.get(key)
        if entry is None or "tab" not in entry and key != "log":
            return
        index = len(self._nav_keys) - 1 if key == "log" else self._nav_keys.index(key)
        self.stack.setCurrentIndex(index)
        self.page_title.setText(PAGE_TITLES.get(key, "日志"))
        # 数据源按钮只在笔记库相关页面有意义
        visible = key in ("library", "search")
        for b in (self.src_fs, self.src_db):
            b.setVisible(visible)
        for k, e in self._pages.items():  # 手动互斥（不用 QButtonGroup 的自动 id）
            e["button"].setChecked(k == key)

    def all_tabs(self) -> list[BaseTab]:
        return [e["tab"] for e in self._pages.values() if "tab" in e]

    @property
    def library_page(self):
        return self._pages["library"]["tab"]

    def _on_src_fs_clicked(self):
        """「散装目录」按钮：选根目录，取消则不切换。"""
        if self.select_root_dir() is None:
            return
        self.library_page._set_mode("fs")
        self.update_title()

    def pick_db_file(self, force_dialog: bool = False):
        """「db 容器」按钮：语义是**打开**——已配置的库直接打开切换，
        绝不诱导覆盖既有文件。未配置/文件失效或显式要求时才弹选择框
        （只选已有 .db；新建走 :meth:`create_db_file`）。"""
        lib = self.library_page
        cfg_path = lib.config.get("db_path")
        if not force_dialog and cfg_path and Path(cfg_path).is_file():
            already = (lib.mode == "db" and lib.db is not None
                       and Path(lib.db.db_path) == Path(cfg_path))
            if not already:
                lib.open_db_file(cfg_path)
                if lib.db is None:  # 打开失败已记日志
                    return
            lib._set_mode("db")
            self.update_title()
            return
        start_dir = str(cfg_path or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择笔记库 .db 文件", start_dir,
            "SQLite 数据库 (*.db);;所有文件 (*.*)")
        if path:
            self._open_db_and_switch(path)

    def create_db_file(self):
        """新建笔记库：指定新 .db 路径并初始化空库（schema 自动建表）。"""
        start_dir = str(self.library_page.config.get("db_path") or "")
        path, _ = QFileDialog.getSaveFileName(
            self, "新建笔记库（指定 .db 文件名）", start_dir,
            "SQLite 数据库 (*.db);;所有文件 (*.*)")
        if not path:
            return
        if Path(path).exists() and QMessageBox.question(
            self, "确认", f"文件已存在：\n{path}\n\n将其作为笔记库打开？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._open_db_and_switch(path)

    def _open_db_and_switch(self, path: str):
        lib = self.library_page
        lib.open_db_file(path)
        if lib.db is None:
            return
        lib._set_mode("db")
        self.update_title()

    def update_title(self):
        title = f"mdtool v{get_version()}"
        if self.library_page.mode == "db":
            title += " - db mode"
        self.setWindowTitle(title)

    def open_db_note(self, note_id: int):
        """搜索页双击 db 结果 → 切回笔记库页并打开编辑会话。"""
        self.switch_page("library")
        lib = self.library_page
        lib._set_mode("db")
        self.update_title()
        lib._db_open_note_for_edit(note_id)

    def select_root_dir(self) -> Optional[Path]:
        """弹目录选择；返回所选路径，取消返回 None。"""
        path = QFileDialog.getExistingDirectory(self, "选择知识库根目录")
        if not path:
            return None
        self.root_dir = Path(path)
        self.src_fs.setToolTip(f"当前知识库根目录: {self.root_dir}")
        for tab in self.all_tabs():
            tab.set_root_dir(self.root_dir)
        return self.root_dir

    @property
    def root_dir(self) -> Optional[Path]:
        return getattr(self, "_root_dir", None)

    @root_dir.setter
    def root_dir(self, value: Optional[Path]):
        self._root_dir = value

    def open_migrate(self, path):
        """文件浏览器右键「迁移图片」→ 切到迁移页并载入目标。"""
        self.switch_page("migrate")
        self._pages["migrate"]["tab"].load_file(path)

    # ── 托盘 ──

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(tray_icon(), self)
        self._tray.setToolTip("Markdown 工具集")
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        act_show = menu.addAction("显示窗口")
        act_show.triggered.connect(self._tray_show)
        act_quit = menu.addAction("退出程序")
        act_quit.triggered.connect(self._tray_quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
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

    # ── 关闭处理 ──

    def closeEvent(self, event):  # noqa: N802 - Qt override
        if self._closing:
            event.accept()
            return
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
        for tab in self.all_tabs():
            try:
                tab.shutdown()
            except Exception:
                pass


class LogPage(QWidget):
    """全局日志页：订阅 :mod:`qtui.logbus`，深底终端观感。

    页面创建晚于早期消息也没关系——总线保留环形缓冲，
    构造时先回放快照再订阅增量。
    """

    _LEVEL_COLORS = {"WARNING": "#fbbf24", "ERROR": "#f87171"}

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 0, 18, 14)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.addWidget(muted_label("所有页面的运行日志汇总于此（调试用）。"))
        bar.addStretch(1)
        clear = QPushButton("清空日志")
        clear.clicked.connect(get_log_bus().clear)
        bar.addWidget(clear)
        lay.addLayout(bar)

        self.view = QTextEdit(readOnly=True, objectName="logView")
        lay.addWidget(self.view, 1)

        bus = get_log_bus()
        for msg, level in bus.snapshot():
            self._append(msg, level)
        bus.message.connect(self._append)
        bus.cleared.connect(self.view.clear)

    def _dim_color(self) -> str:
        name = QApplication.instance().property("theme") or "light"
        return THEMES[name].log_muted

    def _append(self, msg: str, level: str):
        ts = datetime.now().strftime("%H:%M:%S")
        text = html.escape(msg)
        color = self._LEVEL_COLORS.get(level)
        stamp = f'<span style="color:{self._dim_color()}">[{ts}]</span>'
        if color:
            self.view.append(f'{stamp} <span style="color:{color}">[{level}] {text}</span>')
        else:
            self.view.append(f"{stamp} {text}")
