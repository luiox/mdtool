"""独立搜索页 — 全文检索，数据源自动跟随笔记库页当前状态。

不再自带源切换：笔记库页在散装模式就扫根目录，在 db 模式就查容器
（「打开过一次就有了」）。结果表列头按本次搜索的源动态适配；散装
结果双击用系统默认程序打开，db 结果双击跳回笔记库页开编辑会话。
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mdtool.desktop.qtui.tabs import file_browser as fb
from mdtool.desktop.qtui.widgets import BaseTab, SearchBar, muted_label
from mdtool.desktop.qtui.workers import start_worker

_SCOPE_MAP = {"文件名": "name", "正文": "body", "文件名+正文": "both"}


class SearchPage(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._result_mode = "fs"  # 本次结果的来源（双击行为依赖它）
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 0, 18, 14)
        root.setSpacing(8)

        bar = QHBoxLayout()
        self.src_hint = muted_label("搜索目标跟随笔记库页当前的数据源")
        bar.addWidget(self.src_hint)
        bar.addStretch(1)
        root.addLayout(bar)

        self.search_bar = SearchBar()
        self.search_bar.search_requested.connect(lambda *_: self.do_search())
        root.addWidget(self.search_bar)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.results = QTableWidget(0, 4)
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.verticalHeader().setVisible(False)
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setColumnWidth(0, 300)
        self.results.setColumnWidth(1, 180)
        self.results.setColumnWidth(2, 90)
        self.results.cellDoubleClicked.connect(self._on_result_double_click)
        root.addWidget(self.results, 1)

    # ── 当前数据源（跟随笔记库页）──

    def _current_source(self) -> tuple[str, Optional[str]]:
        """返回 ``(mode, 错误提示)``；提示非空表示源不可搜。"""
        mw = self.main_window
        lib = mw.library_page if mw is not None else None
        mode = lib.mode if lib is not None else "fs"
        if mode == "db":
            if lib.db is None:
                return mode, "笔记库未打开——请先在顶栏点「db 容器」选择 .db 文件"
            return mode, None
        if not self.root_dir:
            return mode, "未选择知识库根目录——请先在顶栏点「散装目录」选择"
        return mode, None

    def _fs_notes_root(self):
        """散装视图根 = 笔记树目录（markdown/ 优先双名兼容），与笔记库页一致。"""
        if not self.root_dir:
            return None
        import kb_bundle as kbb
        return kbb.resolve_notes_dir(self.root_dir)

    def _refresh_hint(self):
        mode, err = self._current_source()
        if err:
            self.src_hint.setText(err)
            return
        if mode == "db":
            db_path = self.main_window.library_page.config.get("db_path", "")
            self.src_hint.setText(f"搜索目标: 笔记库容器 · {db_path}")
        else:
            notes_root = self._fs_notes_root()
            if notes_root is None:
                self.src_hint.setText("搜索目标: 未选择知识库根目录")
            else:
                self.src_hint.setText(f"搜索目标: 散装笔记 · {notes_root}")

    def showEvent(self, event):  # noqa: N802 - Qt override
        """进入页面时刷新源提示（模式可能在别处被切换）。"""
        super().showEvent(event)
        self._refresh_hint()

    # ── hooks ──

    def set_root_dir(self, root_dir: Optional[Path]):
        super().set_root_dir(root_dir)
        self._refresh_hint()

    # ── 搜索 ──

    def do_search(self):
        pattern = self.search_bar.text()
        if not pattern:
            self.log("搜索内容为空")
            return
        scope = _SCOPE_MAP.get(self.search_bar.scope_text(), "name")
        mode, err = self._current_source()
        if err:
            QMessageBox.warning(self, "提示", err)
            return
        self._result_mode = mode
        self._apply_headers(mode)
        self.results.setRowCount(0)
        if mode == "db":
            self._search_db(pattern, scope)
        else:
            self._search_fs(pattern, scope)

    def _apply_headers(self, mode: str):
        if mode == "db":
            self.results.setHorizontalHeaderLabels(["路径", "标题", "修改时间", "id"])
            self.results.setColumnHidden(3, True)   # 隐藏 id 载体列
        else:
            self.results.setHorizontalHeaderLabels(["路径", "标题", "命中", "片段"])
            self.results.setColumnHidden(3, False)

    def _search_fs(self, pattern: str, scope: str):
        if self._running:  # 防重入
            return
        self._running = True
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.log(f"开始搜索「{pattern}」(范围={self.search_bar.scope_text()})")
        start_worker(
            fb._search_md_job,
            root=self._fs_notes_root(), pattern=pattern, scope=scope,
            on_progress=lambda cur, tot: (self.progress.setMaximum(max(tot, 1)),
                                          self.progress.setValue(cur)),
            on_log=self.log,
            on_finished=self._on_fs_finished,
            on_error=self._on_fs_error,
        )

    def _on_fs_finished(self, rows: list[dict]):
        self._running = False
        self.progress.setVisible(False)
        for r in rows:
            rc = self.results.rowCount()
            self.results.insertRow(rc)
            self.results.setItem(rc, 0, QTableWidgetItem(r["rel"]))
            self.results.setItem(rc, 1, QTableWidgetItem(r["title"] or ""))
            self.results.setItem(rc, 2, QTableWidgetItem(str(r["hits"])))
            self.results.setItem(rc, 3, QTableWidgetItem(r["snippet"]))
            self.results.item(rc, 0).setData(Qt.ItemDataRole.UserRole, r["rel"])
        self.log(f"搜索完成: {len(rows)} 条")

    def _on_fs_error(self, msg: str):
        self._running = False
        self.progress.setVisible(False)
        self.log(f"搜索失败: {msg}", "ERROR")

    def _search_db(self, pattern: str, scope: str):
        db = self.main_window.library_page.db
        try:
            rows = db.search(pattern, scope)
        except Exception as e:
            QMessageBox.critical(self, "搜索错误", f"正则无效或查询失败: {e}")
            return
        for r in rows:
            rc = self.results.rowCount()
            self.results.insertRow(rc)
            self.results.setItem(rc, 0, QTableWidgetItem(r["path"]))
            self.results.setItem(rc, 1, QTableWidgetItem(r["title"] or ""))
            self.results.setItem(rc, 2, QTableWidgetItem(_fmt_mtime(r["mtime"])))
            self.results.setItem(rc, 3, QTableWidgetItem(str(r["id"])))
        self.log(f"搜索完成: {len(rows)} 条")

    def _on_result_double_click(self, row, _col):
        item = self.results.item(row, 0)
        if item is None:
            return
        if self._result_mode == "fs":
            notes_root = self._fs_notes_root()
            if notes_root:
                rel = item.data(Qt.ItemDataRole.UserRole) or item.text()
                fb.open_external(notes_root / rel)
        else:
            try:
                nid = int(self.results.item(row, 3).text())
            except (ValueError, AttributeError):
                return
            # 编辑会话归笔记库页所有 → 跳回去打开
            if self.main_window is not None:
                self.main_window.open_db_note(nid)


def _fmt_mtime(mtime: str) -> str:
    try:
        return datetime.fromisoformat(mtime.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(mtime)
