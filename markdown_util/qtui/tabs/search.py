"""独立搜索页 — 散装目录与 db 容器的全文检索。

从「笔记库」页拆出：目录树不再与搜索/结果表抢空间。布局为
数据源切换 + 大输入框 + 全宽结果表 + 扫描进度条（worker 本就上报
progress，此前无消费方）。散装结果双击用系统默认程序打开；db 结果
双击跳回笔记库页打开编辑会话（编辑基础设施归笔记库页所有）。
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qtui.tabs import file_browser as fb
from qtui.widgets import BaseTab, SearchBar, muted_label
from qtui.workers import start_worker

_SCOPE_MAP = {"文件名": "name", "正文": "body", "文件名+正文": "both"}


class SearchPage(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "fs"
        self._running = False
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 0, 18, 14)
        root.setSpacing(8)

        # 数据源切换 + 提示
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.btn_fs = QPushButton("散装目录", checkable=True)
        self.btn_fs.setProperty("toggle", "source")
        self.btn_db = QPushButton("笔记库容器", checkable=True)
        self.btn_db.setProperty("toggle", "source")
        self._src_group = QButtonGroup(self)
        self._src_group.addButton(self.btn_fs, 0)
        self._src_group.addButton(self.btn_db, 1)
        self._src_group.idClicked.connect(lambda i: self._set_mode("fs" if i == 0 else "db"))
        h.addWidget(self.btn_fs)
        h.addWidget(self.btn_db)
        h.addStretch(1)
        self.hint_label = muted_label("")
        h.addWidget(self.hint_label)
        root.addWidget(bar)

        # 搜索行
        self.search_bar = SearchBar()
        self.search_bar.search_requested.connect(lambda *_: self.do_search())
        root.addWidget(self.search_bar)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # 结果表（全宽）
        self.results = QTableWidget(0, 4)
        self.results.setHorizontalHeaderLabels(["路径", "标题", "命中", "片段"])
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.verticalHeader().setVisible(False)
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setColumnWidth(0, 300)
        self.results.setColumnWidth(1, 180)
        self.results.setColumnWidth(2, 60)
        self.results.cellDoubleClicked.connect(self._on_result_double_click)
        root.addWidget(self.results, 1)

        self._set_mode("fs")

    # ── 模式 ──

    def _set_mode(self, mode: str):
        self.mode = mode
        self.btn_fs.setChecked(mode == "fs")
        self.btn_db.setChecked(mode == "db")
        if mode == "db":
            self.results.setHorizontalHeaderLabels(["路径", "标题", "修改时间", "id"])
            self.results.setColumnHidden(3, True)  # 隐藏 id 载体列
        else:
            self.results.setHorizontalHeaderLabels(["路径", "标题", "命中", "片段"])
            self.results.setColumnHidden(3, False)
        self.results.setRowCount(0)
        self._update_hint()

    def _update_hint(self):
        if self.mode == "fs":
            self.hint_label.setText(
                "" if self.root_dir else "未在顶栏选择知识库根目录")
        else:
            db = self._library_db()
            self.hint_label.setText("" if db is not None else "笔记库未打开")

    # ── hooks ──

    def set_root_dir(self, root_dir: Optional[Path]):
        super().set_root_dir(root_dir)
        self._update_hint()

    # ── db 源：借用笔记库页已打开的连接 ──

    def _library_db(self):
        mw = self.main_window
        if mw is not None and hasattr(mw, "library_page"):
            return mw.library_page.db
        return None

    # ── 搜索 ──

    def do_search(self):
        pattern = self.search_bar.text()
        if not pattern:
            self.log("搜索内容为空")
            return
        scope = _SCOPE_MAP.get(self.search_bar.scope_text(), "name")
        if self.mode == "db":
            self._search_db(pattern, scope)
        else:
            self._search_fs(pattern, scope)

    def _search_fs(self, pattern: str, scope: str):
        if not self.root_dir:
            QMessageBox.warning(self, "警告", "请先在顶栏选择知识库根目录")
            return
        if self._running:  # 防重入
            return
        self._running = True
        self.results.setRowCount(0)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.log(f"开始搜索「{pattern}」(范围={self.search_bar.scope_text()})")
        start_worker(
            fb._search_md_job, root=self.root_dir, pattern=pattern, scope=scope,
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
        db = self._library_db()
        if db is None:
            QMessageBox.warning(self, "提示", "请先在笔记库页打开一个笔记库")
            return
        self.results.setRowCount(0)
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
        if self.mode == "fs":
            if self.root_dir:
                rel = item.data(Qt.ItemDataRole.UserRole) or item.text()
                fb.open_external(self.root_dir / rel)
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
