"""Image check tab — PySide6 port of ``tabs/image_check.py``.

Two-pass scan (broken links, then duplicate detection) over the project root.
The scan algorithm is moved into a worker function and run on the thread pool
via :class:`qtui.workers.GenericWorker`; progress/log flow back as Qt signals
so the UI stays responsive on large libraries.
"""

import csv
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qtui.widgets import BaseTab, LogPanel
from qtui.workers import start_worker
from utils import (
    HAVE_LIBMARKDOWN,
    extract_image_links,
    extract_image_links_ast,
    find_md_files,
    format_size,
    get_file_md5,
    resolve_image_path,
)

EXT_NAMES = ["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"]


class ImageCheckTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.broken_links: list = []
        self.duplicate_groups: list = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        # options
        gb = QGroupBox("扫描选项")
        g = QVBoxLayout(gb)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("解析引擎:"))
        self.rb_regex = QRadioButton("正则表达式"); self.rb_regex.setChecked(True)
        self.rb_ast = QRadioButton("AST 解析")
        if not HAVE_LIBMARKDOWN:
            self.rb_ast.setEnabled(False)
            r1.addWidget(QLabel("(需要安装 libmarkdown)"))
        r1.addWidget(self.rb_regex); r1.addWidget(self.rb_ast)
        r1.addStretch(1)
        g.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("图片格式:"))
        self.cb_std = QCheckBox("![]()"); self.cb_std.setChecked(True)
        self.cb_obs = QCheckBox("![[ ]]"); self.cb_obs.setChecked(True)
        r2.addWidget(self.cb_std); r2.addWidget(self.cb_obs)
        r2.addStretch(1)
        g.addLayout(r2)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("文件后缀:"))
        self.ext_checks: dict[str, QCheckBox] = {}
        for ext in EXT_NAMES:
            cb = QCheckBox(ext); cb.setChecked(True)
            self.ext_checks[ext] = cb
            r3.addWidget(cb)
        r3.addStretch(1)
        g.addLayout(r3)
        outer.addWidget(gb)

        # actions
        bar = QHBoxLayout()
        b = QPushButton("开始校验"); b.clicked.connect(self.start_check)
        bar.addWidget(b)
        b = QPushButton("导出报告 (CSV)"); b.clicked.connect(self.export_report)
        bar.addWidget(b)
        bar.addStretch(1)
        outer.addLayout(bar)

        self.progress = QProgressBar()
        outer.addWidget(self.progress)

        # broken tree
        outer.addWidget(QLabel("失效链接:"))
        self.broken_tree = QTreeWidget()
        self.broken_tree.setColumnCount(3)
        self.broken_tree.setHeaderLabels(["图片链接", "来源文件", "行号"])
        self.broken_tree.setColumnWidth(0, 280); self.broken_tree.setColumnWidth(1, 300)
        self.broken_tree.setRootIsDecorated(False)
        outer.addWidget(self.broken_tree, 1)

        # dup tree
        outer.addWidget(QLabel("重复图片（可展开分组）:"))
        self.dup_tree = QTreeWidget()
        self.dup_tree.setColumnCount(4)
        self.dup_tree.setHeaderLabels(["MD5", "图片路径", "大小", "被引用"])
        self.dup_tree.setColumnWidth(0, 230); self.dup_tree.setColumnWidth(1, 280)
        self.dup_tree.setColumnWidth(2, 80); self.dup_tree.setColumnWidth(3, 200)
        outer.addWidget(self.dup_tree, 1)

        self.log_panel = LogPanel(height_lines=5)
        outer.addWidget(self.log_panel)

    def log(self, msg: str, level: str = "INFO"):
        self.log_panel.append_line(msg, level)

    def _selected_exts(self) -> set:
        return {ext for ext, cb in self.ext_checks.items() if cb.isChecked()}

    def start_check(self):
        if not self.root_dir:
            QMessageBox.warning(self, "警告", "请先在工具栏选择项目根目录")
            return
        exts = self._selected_exts()
        if not exts:
            QMessageBox.warning(self, "警告", "请至少勾选一个文件后缀")
            return
        use_std, use_obs = self.cb_std.isChecked(), self.cb_obs.isChecked()
        if not use_std and not use_obs:
            QMessageBox.warning(self, "警告", "请至少勾选一种图片格式")
            return
        mode = "ast" if self.rb_ast.isChecked() else "regex"
        self.broken_links.clear()
        self.duplicate_groups.clear()
        self.broken_tree.clear()
        self.dup_tree.clear()
        self.progress.setValue(0)
        self.log("开始校验...")

        # Disable while running to avoid re-entry.
        for w in self.findChildren(QPushButton):
            w.setEnabled(False)

        start_worker(
            _scan_job,
            root_dir=self.root_dir, exts=exts, use_std=use_std,
            use_obs=use_obs, mode=mode,
            on_progress=lambda cur, tot: self.progress.setMaximum(tut_safe(tot)) or self.progress.setValue(cur),
            on_log=lambda m, l: self.log(m, l),
            on_finished=self._on_scan_finished,
            on_error=lambda e: (self.log(f"校验出错: {e}", "ERROR"), self._reenable()),
        )

    def _reenable(self):
        for w in self.findChildren(QPushButton):
            w.setEnabled(True)

    def _on_scan_finished(self, result):
        self._reenable()
        if result is None:
            return
        self.broken_links = result["broken"]
        self.duplicate_groups = result["duplicates"]
        for item in self.broken_links:
            it = QTreeWidgetItem([item["link"], item["source_file"], str(item["line"])])
            self.broken_tree.addTopLevelItem(it)
        for md5, group in self.duplicate_groups:
            parent = QTreeWidgetItem([md5, f"[{len(group)} 个重复文件]", "", ""])
            for it in group:
                QTreeWidgetItem(parent, [md5, it["path"], it["size"], it["referenced_by"]])
            self.dup_tree.addTopLevelItem(parent)
        self.log(f"失效链接: {len(self.broken_links)} 个 | 重复: {len(self.duplicate_groups)} 组")
        self.log("校验完成")

    def export_report(self):
        if not self.broken_links and not self.duplicate_groups:
            QMessageBox.information(self, "提示", "没有可导出的数据，请先运行校验")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出校验报告", "", "CSV 文件 (*.csv);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["=== 失效链接 ==="])
                w.writerow(["图片链接", "来源文件", "行号"])
                for item in self.broken_links:
                    w.writerow([item["link"], item["source_file"], item["line"]])
                w.writerow([])
                w.writerow(["=== 重复图片 ==="])
                w.writerow(["MD5", "图片路径", "大小", "被引用文件"])
                for md5, group in self.duplicate_groups:
                    w.writerow([f"组: {md5}", "", "", ""])
                    for it in group:
                        w.writerow([md5, it["path"], it["size"], it["referenced_by"]])
            self.log(f"报告已导出: {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))


def tut_safe(total: int) -> int:
    return total if total > 0 else 1


def _scan_job(root_dir: Path, exts: set, use_std: bool, use_obs: bool,
              mode: str, report) -> dict:
    """Pure scan logic, ported verbatim from the tkinter version's two passes.

    Emits progress via ``report("progress", ...)`` and log lines via
    ``report("log", ...)``. Returns ``{"broken": [...], "duplicates": [...]}``.
    """
    extractor = extract_image_links_ast if mode == "ast" else extract_image_links
    md_files = find_md_files(root_dir)
    report("log", msg=f"找到 {len(md_files)} 个 .md 文件")
    broken = []
    img_refs: dict[str, list] = {}

    total = max(len(md_files), 1)
    for i, md_file in enumerate(md_files):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception as e:
            report("log", msg=f"读取失败: {md_file} - {e}", level="WARNING")
            report("progress", current=i + 1, total=total)
            continue
        for link in extractor(content, use_standard=use_std, use_obsidian=use_obs, extensions=exts):
            rel_path = link["rel_path"]
            resolved = resolve_image_path(md_file, rel_path)
            if resolved is None:
                broken.append({"link": rel_path, "source_file": str(md_file), "line": link["line"]})
            else:
                img_refs.setdefault(str(resolved), []).append(
                    {"md_file": str(md_file), "rel_path": rel_path, "line": link["line"]})
        report("progress", current=i + 1, total=total)

    # Pass 2: duplicate detection (size prefilter then MD5)
    existing = list(img_refs.keys())
    duplicates = []
    if len(existing) >= 2:
        size_groups: dict[int, list] = {}
        for p in existing:
            try:
                size_groups.setdefault(Path(p).stat().st_size, []).append(p)
            except OSError:
                pass
        candidates = [p for ps in size_groups.values() if len(ps) >= 2 for p in ps]
        all_hashes: dict[str, list] = {}
        for p in candidates:
            try:
                all_hashes.setdefault(get_file_md5(Path(p)), []).append(p)
            except Exception:
                pass
        for md5, paths in all_hashes.items():
            if len(paths) < 2:
                continue
            group = []
            for p in paths:
                refs = img_refs.get(p, [])
                ref_str = "; ".join(r["md_file"] for r in refs)
                try:
                    sz = format_size(Path(p).stat().st_size)
                except OSError:
                    sz = "?"
                group.append({"path": p, "size": sz, "referenced_by": ref_str})
            duplicates.append((md5, group))

    report("log", msg=f"失效链接: {len(broken)} 个 | 重复: {len(duplicates)} 组")
    return {"broken": broken, "duplicates": duplicates}
