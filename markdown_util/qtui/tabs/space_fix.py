"""Space-fix tab — PySide6 port of ``tabs/space_fix.py``.

A 4-step wizard: pick markdown root → pick the messy image source dir →
match link filenames against the source dir → execute (move files, rewrite
links replacing spaces with underscores). Logic preserved verbatim; widgets
ported to QListWidget (multi-select) + QTreeWidget.
"""

import re
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qtui.widgets import BaseTab, LogPanel


class SpaceFixTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.md_root: Path | None = None
        self.messy_img_root: Path | None = None
        self.raw_links: list = []
        self.matched_items: list = []
        self.pattern = re.compile(r'!\[.*?]\(assets\/[^)]* [^)]*\)')
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        r1 = QHBoxLayout()
        b = QPushButton("1. 选择 Markdown 根目录"); b.clicked.connect(self.select_md_root)
        r1.addWidget(b)
        self.md_root_label = QLabel("未选择"); self.md_root_label.setStyleSheet("color: gray;")
        r1.addWidget(self.md_root_label); r1.addStretch(1)
        outer.addLayout(r1)

        r2 = QHBoxLayout()
        b = QPushButton("2. 选择混乱图片源目录（图片目前存放的杂乱位置）")
        b.clicked.connect(self.select_messy_img_root)
        r2.addWidget(b)
        self.messy_label = QLabel("未选择"); self.messy_label.setStyleSheet("color: gray;")
        r2.addWidget(self.messy_label); r2.addStretch(1)
        outer.addLayout(r2)

        r3 = QHBoxLayout()
        b = QPushButton("3. 文件名匹配（基于原始路径定位源文件）")
        b.clicked.connect(self.match_filenames)
        r3.addWidget(b); r3.addStretch(1)
        outer.addLayout(r3)

        outer.addWidget(QLabel("原始匹配链接（可多选）:"))
        self.list1 = QListWidget()
        self.list1.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        outer.addWidget(self.list1, 1)

        outer.addWidget(QLabel("匹配结果（选中后执行）:"))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["原始相对路径", "新相对路径（空格变_）", "图片文件名", "源文件绝对路径"])
        self.tree.setColumnWidth(0, 220); self.tree.setColumnWidth(1, 220)
        self.tree.setColumnWidth(2, 130); self.tree.setColumnWidth(3, 280)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setRootIsDecorated(False)
        outer.addWidget(self.tree, 1)

        r4 = QHBoxLayout()
        b = QPushButton("4. 执行（移动图片并更新链接）"); b.clicked.connect(self.execute)
        r4.addWidget(b)
        b = QPushButton("清空日志"); b.clicked.connect(lambda: self.log_panel.clear_log())
        r4.addWidget(b); r4.addStretch(1)
        outer.addLayout(r4)

        self.log_panel = LogPanel(height_lines=6)
        outer.addWidget(self.log_panel)

    def log(self, msg: str, level: str = "INFO"):
        self.log_panel.append_line(msg, level)

    # ── steps 1 & 2 ──

    def select_md_root(self):
        initial = str(self.root_dir) if self.root_dir else ""
        path = QFileDialog.getExistingDirectory(self, "选择包含 Markdown 文件的根目录", initial)
        if path:
            self.md_root = Path(path)
            self.md_root_label.setText(str(self.md_root))
            self.md_root_label.setStyleSheet("color: black;")
            self.log(f"Markdown 根目录已选择: {self.md_root}")
            self.scan_md_files()

    def select_messy_img_root(self):
        path = QFileDialog.getExistingDirectory(self, "选择混乱图片源目录（包含所有待整理图片的顶层文件夹）")
        if path:
            self.messy_img_root = Path(path)
            self.messy_label.setText(str(self.messy_img_root))
            self.messy_label.setStyleSheet("color: black;")
            self.log(f"混乱图片源目录已选择: {self.messy_img_root}")

    def scan_md_files(self):
        if not self.md_root:
            QMessageBox.warning(self, "警告", "请先选择 Markdown 根目录")
            return
        self.raw_links.clear()
        self.list1.clear()
        self.matched_items.clear()
        self.tree.clear()
        for md_file in self.md_root.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                for match in self.pattern.finditer(content):
                    link = match.group(0)
                    rel_path = re.search(r'!\[.*?]\((assets/[^)]+)\)', link).group(1)
                    self.raw_links.append({
                        "md_file": md_file, "link": link, "rel_path": rel_path,
                        "start": match.start(), "end": match.end(),
                    })
            except Exception as e:
                self.log(f"读取 {md_file} 失败: {e}", "ERROR")
        for item in self.raw_links:
            display = f"{item['md_file'].relative_to(self.md_root)} -> {item['rel_path']}"
            self.list1.addItem(display)
        if self.raw_links:
            self.log(f"扫描完成，找到 {len(self.raw_links)} 个带空格的图片链接")
        else:
            self.log("未找到任何匹配的图片链接", "WARNING")

    # ── step 3: match ──

    def match_filenames(self):
        if not self.messy_img_root:
            QMessageBox.warning(self, "警告", "请先选择混乱图片源目录")
            return
        selected = sorted(i.row() for i in self.list1.selectedIndexes())
        if not selected:
            QMessageBox.warning(self, "警告", "请在第一个列表框中选中要匹配的链接")
            return
        file_index: dict[str, Path] = {}
        for fp in self.messy_img_root.rglob("*"):
            if fp.is_file() and fp.name not in file_index:
                file_index[fp.name] = fp
        self.matched_items.clear()
        self.tree.clear()
        matched = 0
        for idx in selected:
            raw = self.raw_links[idx]
            original_rel = raw["rel_path"]
            img_filename = Path(original_rel).name
            if img_filename not in file_index:
                self.log(f"在混乱图片目录中未找到文件: {img_filename}", "WARNING")
                continue
            source_file = file_index[img_filename]
            new_rel = original_rel.replace(" ", "_")
            target_file = raw["md_file"].parent / new_rel
            self.matched_items.append({
                "md_file": raw["md_file"], "original_link": raw["link"],
                "original_rel": original_rel, "new_rel": new_rel,
                "img_filename": img_filename, "source_file": source_file,
                "target_file": target_file, "start": raw["start"], "end": raw["end"],
            })
            self.tree.addTopLevelItem(QTreeWidgetItem(
                [original_rel, new_rel, img_filename, str(source_file)]))
            matched += 1
        self.log(f"匹配完成，成功 {matched} 个，失败 {len(selected) - matched} 个")

    # ── step 4: execute ──

    def execute(self):
        selected = self.tree.selectedItems()
        if not selected:
            QMessageBox.warning(self, "警告", "请在匹配结果表格中选中要执行的行")
            return
        # Match tree rows back to data items by (original_rel, new_rel).
        to_execute = []
        for it in selected:
            original_rel, new_rel = it.text(0), it.text(1)
            for m in self.matched_items:
                if m["original_rel"] == original_rel and m["new_rel"] == new_rel:
                    to_execute.append(m)
                    break
        if not to_execute:
            self.log("未找到对应的数据项，请检查", "ERROR")
            return
        if QMessageBox.question(
            self, "确认",
            f"即将处理 {len(to_execute)} 个图片文件。\n移动文件并更新 Markdown 链接。\n是否继续？"
        ) != QMessageBox.StandardButton.Yes:
            return
        success = 0
        for item in to_execute:
            try:
                src, dst = item["source_file"], item["target_file"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    if QMessageBox.question(
                        self, "文件已存在",
                        f"{dst.name} 已存在，是否覆盖？\n{dst}"
                    ) != QMessageBox.StandardButton.Yes:
                        self.log(f"跳过移动 {src} -> {dst}", "WARNING")
                        continue
                shutil.move(str(src), str(dst))
                self.log(f"移动文件: {src} -> {dst}")
                md_file = item["md_file"]
                new_link = item["original_link"].replace(item["original_rel"], item["new_rel"])
                content = md_file.read_text(encoding="utf-8")
                if new_link in content:
                    new_content = content.replace(item["original_link"], new_link, 1)
                else:
                    escaped = re.escape(item["original_link"])
                    new_content = re.sub(escaped, new_link, content, count=1)
                if new_content != content:
                    md_file.write_text(new_content, encoding="utf-8")
                    self.log(f"更新链接: {md_file.relative_to(self.md_root)}")
                else:
                    self.log(f"链接未更新（可能已改变）: {md_file}", "WARNING")
                success += 1
            except Exception as e:
                self.log(f"处理失败: {e}", "ERROR")
        self.log(f"执行完成，成功 {success}/{len(to_execute)} 个")
        self.scan_md_files()
