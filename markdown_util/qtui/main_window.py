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
        self._quitting = False
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

    def pick_db_file(self):
        """「db 容器」按钮：与「散装目录」同语义——每次都弹选择框，
        打开用户指定的 .db 库并切换。上次路径仅作为对话框起始位置，
        绝不替用户决定打开哪个库；新建走 :meth:`create_db_file`。
        """
        lib = self.library_page
        start_dir = str(lib.config.get("db_path") or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要打开的笔记库 (.db)", start_dir,
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

    # ── 托盘与退出 ──
    # 设计原则：退出只有一条路径（really_quit，幂等，先停页面再清线程池）；
    # 托盘只是窗口的隐藏形态；没有托盘的环境绝不提供"最小化到托盘"。

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
        act_quit.triggered.connect(self.really_quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        # 单击/双击都唤起主窗口——用户不必记住该用哪一种
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._tray_show()

    def _tray_show(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):  # noqa: N802 - Qt override
        if self._quitting:
            event.accept()
            return
        choice = self._ask_on_close()
        if choice == "exit":
            self.really_quit()
            event.accept()
        elif choice == "tray":
            self.hide()
            event.ignore()
        else:
            event.ignore()

    def _ask_on_close(self) -> Optional[str]:
        """关闭确认 → 'exit' | 'tray' | None(取消)。托盘不可用时不提供隐藏项。"""
        dlg = QMessageBox(self)
        dlg.setWindowTitle("退出确认")
        dlg.setText("关闭程序时执行什么操作？")
        b_exit = dlg.addButton("退出程序", QMessageBox.ButtonRole.AcceptRole)
        b_tray = None
        if self._tray is not None:
            b_tray = dlg.addButton("最小化到托盘", QMessageBox.ButtonRole.YesRole)
        dlg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dlg.exec()
        clicked = dlg.clickedButton()
        if clicked is b_exit:
            return "exit"
        if b_tray is not None and clicked is b_tray:
            return "tray"
        return None

    def really_quit(self):
        """唯一退出路径（托盘菜单 / 关闭按钮共用）。幂等：重复调用只补发 quit。"""
        if getattr(self, "_quitting", False):
            QApplication.quit()
            return
        self._quitting = True
        self._closing = True
        self._do_shutdown()
        # 后台 worker（搜索/打包/导入导出 job 走全局线程池）：丢弃排队任务，
        # 等待在跑的收尾——否则事件循环结束后非守护线程会把进程吊住
        from PySide6.QtCore import QThreadPool
        pool = QThreadPool.globalInstance()
        pool.clear()
        pool.waitForDone(5000)
        if self._tray is not None:
            self._tray.hide()
        QApplication.setQuitOnLastWindowClosed(True)
        QApplication.quit()

    def _do_shutdown(self):
        for tab in self.all_tabs():
            try:
                tab.shutdown()
            except Exception as e:  # noqa: BLE001 - 关闭边界，失败必须可见
                get_log_bus().publish(
                    f"关闭 {type(tab).__name__} 失败: {e}", "ERROR")


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
