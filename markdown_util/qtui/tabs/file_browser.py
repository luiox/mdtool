"""File browser tab — KB（散装）视图：浏览、搜索、导出。

Walk the knowledge-base root directory (picked in the toolbar) and show a
tree of folders + ``.md`` files (``images/``/``assets/``/``meta.db`` are the
media face of the KB root and are hidden). 提供：

- 正则全文搜索（直接扫文件，不依赖 notes.db；知识库规范 §3.3）
- 打包 ZIP：单篇 / 多篇（多选或整个文件夹）/ 全库；媒体 URL 经 resolver
  改写为 zip 内相对路径（知识库规范 §5），相对路径旧链接兼容，外链透传
- 导出为 db 包（散装 → SQLite 容器，复用笔记库导入逻辑）
- 迁移图片到图床（交给 Migrate tab）
"""

import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from link_resolver import DEFAULT_HOST, DEFAULT_PORT, is_external_url, iter_link_destinations, parse
from qtui.icons import file_icon, folder_icon
from qtui.tabs.media_server import load_config as load_media_config
from qtui.tabs.notes_browser import _import_folder_job  # 复用散装 → db 导入
from qtui.widgets import BaseTab, LogPanel
from qtui.workers import start_worker
from server.notes_db import extract_title
from utils import resolve_image_path

_HIDDEN_ENTRIES = {"images", "assets", "meta.db"}  # KB 根的媒体面，不属于笔记树


class FileBrowserTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # 工具行：搜索 + 全库打包
        bar = QWidget()
        from PySide6.QtWidgets import QHBoxLayout
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.returnPressed.connect(self.do_search)
        h.addWidget(self.search_edit, 1)
        self.scope_box = QComboBox()
        self.scope_box.addItems(["文件名", "正文", "文件名+正文"])
        h.addWidget(self.scope_box)
        b = QPushButton("搜索"); b.clicked.connect(self.do_search); h.addWidget(b)
        b = QPushButton("清空"); b.clicked.connect(self.clear_search); h.addWidget(b)
        b = QPushButton("打包全库 ZIP"); b.clicked.connect(self.pack_all_zip); h.addWidget(b)
        root.addWidget(bar)

        # 树 + 结果
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("知识库（散装笔记）")
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        splitter.addWidget(self.tree)

        self.results = QTableWidget(0, 4)
        self.results.setHorizontalHeaderLabels(["路径", "标题", "命中", "片段"])
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.cellDoubleClicked.connect(self._on_result_double_click)
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setColumnWidth(0, 260)
        self.results.setColumnWidth(1, 160)
        self.results.setColumnWidth(2, 60)
        splitter.addWidget(self.results)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self.log_panel = LogPanel(height_lines=6)
        root.addWidget(self.log_panel)

    def log(self, msg: str, level: str = "INFO"):
        self.log_panel.append_line(msg, level)

    # ── hooks ──

    def set_root_dir(self, root_dir: Optional[Path]):
        super().set_root_dir(root_dir)
        self.refresh_tree()

    # ── tree building ──

    def refresh_tree(self):
        self.tree.clear()
        if not self.root_dir:
            return
        self._add_nodes(self.tree.invisibleRootItem(), self.root_dir, self.root_dir)

    def _add_nodes(self, parent_item, current_path: Path, root_dir: Path):
        try:
            entries = sorted(current_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except (PermissionError, OSError) as e:
            self.log(f"无法读取目录 {current_path}: {e}", "WARNING")
            return
        for entry in entries:
            # 知识库根目录的媒体面（images/assets/meta.db）不属于笔记树
            if entry.name.lower() in _HIDDEN_ENTRIES:
                continue
            if entry.is_dir():
                item = QTreeWidgetItem(parent_item, [entry.name])
                item.setIcon(0, folder_icon())
                self._add_nodes(item, entry, root_dir)
            elif entry.suffix.lower() == ".md":
                rel = entry.relative_to(root_dir).as_posix()
                item = QTreeWidgetItem(parent_item, [entry.name])
                item.setIcon(0, file_icon())
                item.setData(0, Qt.ItemDataRole.UserRole, rel)  # store rel path
                item.setToolTip(0, rel)

    # ── selection helpers ──

    def _item_path(self, item: QTreeWidgetItem) -> Optional[Path]:
        """Resolve the filesystem path of a tree item (md file or folder)."""
        if not self.root_dir:
            return None
        rel = item.data(0, Qt.ItemDataRole.UserRole)
        if rel:  # md file
            return self.root_dir / rel
        # folder: walk up collecting names
        if item is self.tree.invisibleRootItem():
            return None
        parts = []
        cur = item
        while cur is not None and cur is not self.tree.invisibleRootItem():
            parts.append(cur.text(0))
            cur = cur.parent()
        return self.root_dir / Path(*reversed(parts))

    def _selected_path(self) -> Optional[Path]:
        item = self.tree.currentItem()
        if item is None:
            return None
        return self._item_path(item)

    def _selected_md_files(self) -> list[Path]:
        """收集当前选中的 .md 文件；选中目录时递归收集其下全部 .md。"""
        items = self.tree.selectedItems()
        if not items and self.tree.currentItem() is not None:
            items = [self.tree.currentItem()]
        md_files: list[Path] = []
        for it in items:
            p = self._item_path(it)
            if p is None:
                continue
            if p.is_dir():
                md_files.extend(p.rglob("*.md"))
            elif p.suffix.lower() == ".md":
                md_files.append(p)
        return sorted(set(md_files))

    # ── context menu ──

    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        md_files = self._selected_md_files()
        menu = QMenu(self)
        if md_files:
            label = f"打包为 ZIP（{len(md_files)} 篇）" if len(md_files) > 1 else "打包为 ZIP"
            menu.addAction(label, self.pack_to_zip)
            if len(md_files) == 1 and md_files[0].suffix.lower() == ".md":
                menu.addAction("迁移图片到图床", self.migrate_images)
        if item.data(0, Qt.ItemDataRole.UserRole) is not None or self._item_path(item) is not None:
            menu.addAction("导出为 db 包…", self.export_db)
        if menu.actions():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ── zip 导出 ──

    def _media_config(self) -> dict:
        mcfg = load_media_config()
        try:
            port = int(mcfg.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        return {
            "media_root": Path(mcfg["media_root"]) if mcfg.get("media_root") else None,
            "images_subdir": mcfg.get("images_subdir") or "images",
            "assets_subdir": mcfg.get("assets_subdir") or "assets",
            "host": mcfg.get("host") or DEFAULT_HOST,
            "port": port,
        }

    def pack_all_zip(self):
        if not self.root_dir:
            QMessageBox.warning(self, "警告", "请先选择知识库根目录")
            return
        md_files = sorted(self.root_dir.rglob("*.md"))
        md_files = [p for p in md_files
                    if not any(part.lower() in _HIDDEN_ENTRIES for part in p.parts)]
        if not md_files:
            QMessageBox.warning(self, "警告", "知识库根目录下没有 .md 文件")
            return
        self._pack_zip(md_files, base=self.root_dir, default_name="知识库导出.zip")

    def pack_to_zip(self):
        md_files = self._selected_md_files()
        if not md_files:
            QMessageBox.warning(self, "警告", "请选中 .md 文件或包含 .md 的目录")
            return
        if len(md_files) == 1:
            base = md_files[0].parent
            default_name = md_files[0].stem + ".zip"
        else:
            base = Path(os.path.commonpath([str(p.parent) for p in md_files]))
            default_name = "知识库导出.zip"
        self._pack_zip(md_files, base=base, default_name=default_name)

    def _pack_zip(self, md_files: list[Path], *, base: Path, default_name: str):
        zip_path, _ = QFileDialog.getSaveFileName(
            self, "保存 ZIP 文件", default_name, "ZIP 文件 (*.zip)")
        if not zip_path:
            return
        notes = []
        for p in md_files:
            try:
                content = p.read_text(encoding="utf-8")
            except Exception as e:
                self.log(f"读取失败（跳过）: {p} — {e}", "WARNING")
                continue
            notes.append((p.relative_to(base).as_posix(), p, content))
        if not notes:
            QMessageBox.critical(self, "错误", "没有可打包的笔记")
            return
        mc = self._media_config()
        results, skipped = plan_zip_bundle_many(
            notes, media_root=mc["media_root"], images_subdir=mc["images_subdir"],
            assets_subdir=mc["assets_subdir"], host=mc["host"], port=mc["port"],
        )
        for d in skipped:
            self.log(f"媒体未找到 (已跳过): {d}", "WARNING")
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for zip_rel, new_content, members in results:
                    zf.writestr(zip_rel, new_content)
                    for member, src in members:
                        zf.write(src, member)
            n_media = sum(len(m) for _, _, m in results)
            self.log(f"打包完成: {zip_path}（{len(notes)} 篇，{n_media} 个媒体文件）")
        except Exception as e:
            QMessageBox.critical(self, "打包失败", str(e))

    def migrate_images(self):
        path = self._selected_path()
        if path is None:
            return
        # Hand off to the Migrate tab via the main window (decoupled from index).
        if self.main_window is not None and hasattr(self.main_window, "open_migrate"):
            self.main_window.open_migrate(path)
        else:
            QMessageBox.information(self, "提示", "迁移功能未就绪")

    # ── 搜索（散装，文件扫描）──

    def do_search(self):
        if not self.root_dir:
            QMessageBox.warning(self, "警告", "请先选择知识库根目录")
            return
        pattern = self.search_edit.text().strip()
        if not pattern:
            QMessageBox.warning(self, "警告", "请输入搜索内容（支持正则）")
            return
        scope_map = {"文件名": "name", "正文": "body", "文件名+正文": "both"}
        scope = scope_map.get(self.scope_box.currentText(), "name")
        self.results.setRowCount(0)
        self.log(f"开始搜索「{pattern}」(范围={self.scope_box.currentText()})")
        start_worker(
            _search_md_job, root=self.root_dir, pattern=pattern, scope=scope,
            on_log=lambda m, l: self.log(m, l),
            on_finished=self._on_search_finished,
            on_error=lambda e: self.log(f"搜索失败: {e}", "ERROR"),
        )

    def _on_search_finished(self, rows):
        for r in rows:
            rc = self.results.rowCount()
            self.results.insertRow(rc)
            self.results.setItem(rc, 0, QTableWidgetItem(r["rel"]))
            self.results.setItem(rc, 1, QTableWidgetItem(r["title"] or ""))
            self.results.setItem(rc, 2, QTableWidgetItem(str(r["hits"])))
            self.results.setItem(rc, 3, QTableWidgetItem(r["snippet"]))
            self.results.item(rc, 0).setData(Qt.ItemDataRole.UserRole, r["rel"])
        self.log(f"搜索完成: {len(rows)} 条")

    def clear_search(self):
        self.results.setRowCount(0)
        self.search_edit.clear()

    def _on_result_double_click(self, row, _col):
        item = self.results.item(row, 0)
        if item is None or not self.root_dir:
            return
        rel = item.data(Qt.ItemDataRole.UserRole) or item.text()
        self._open_external(self.root_dir / rel)

    @staticmethod
    def _open_external(path: Path):
        try:
            os.startfile(str(path))  # Windows
        except AttributeError:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            import subprocess
            subprocess.Popen([opener, str(path)])

    # ── 散装 → db 包 ──

    def export_db(self):
        path = self._selected_path()
        if path is None:
            return
        src_root = path if path.is_dir() else path.parent
        db_path, _ = QFileDialog.getSaveFileName(
            self, "导出为 db 包", "notes.db", "SQLite 数据库 (*.db)")
        if not db_path:
            return
        self.log(f"开始导出 db 包: {src_root} → {db_path}")
        start_worker(
            _export_db_job, db_path=Path(db_path), src_root=src_root,
            on_log=lambda m, l: self.log(m, l),
            on_finished=lambda r: self.log(
                f"导出完成: 新增 {r['inserted']}，覆盖 {r['replaced']}，共 {r['total']} 篇 → {db_path}"),
            on_error=lambda e: self.log(f"导出失败: {e}", "ERROR"),
        )


# ── 模块级纯逻辑（可测试）──

def plan_zip_bundle(
    content: str,
    md_file: Path,
    *,
    depth: int = 0,
    media_root: Optional[Path] = None,
    images_subdir: str = "images",
    assets_subdir: str = "assets",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    used: Optional[dict] = None,
) -> tuple[str, list[tuple[str, Path]], list[str]]:
    """解析 md 内容中的媒体链接，产出 zip 打包计划（单篇）。

    返回 ``(改写后的内容, [(zip成员名, 源文件路径), ...], [跳过的链接])``。

    - ``depth``：笔记在 zip 内的目录层数（0 = zip 根），链接改写为
      ``"../" * depth + "images/<name>"``（媒体平铺在 zip 根）。
    - 本知识库媒体 URL → 从媒体根 ``<media_root>/<subdir>`` 找文件；
      相对路径旧链接 → 按 ``md_file`` 所在目录解析，改写为 ``assets/<basename>``。
    - 外部链接 / 找不到文件 → 原样保留（找不到的记入 skipped 供日志）。
    - ``used``：跨笔记共享的成员去重表（多篇导出时传入）。
    """
    img_dir = Path(media_root) / images_subdir if media_root else None
    ast_dir = Path(media_root) / assets_subdir if media_root else None
    if used is None:
        used = {}
    prefix = "../" * depth
    out: list[str] = []
    members: list[tuple[str, Path]] = []
    skipped: list[str] = []
    pos = 0
    for start, end, dest, _is_image in iter_link_destinations(content):
        out.append(content[pos:start])
        member: Optional[str] = None
        src: Optional[Path] = None
        ref = parse(dest, host=host, port=port)
        if ref is not None:
            base = img_dir if ref.category == "images" else ast_dir
            cand = base / ref.name if base else None
            if cand is not None and cand.is_file():
                member, src = f"{ref.category}/{ref.name}", cand
        elif not is_external_url(dest):
            cand = resolve_image_path(md_file, dest)
            if cand is not None and cand.is_file():
                member, src = f"assets/{Path(dest).name}", cand
        if member is None:
            out.append(content[start:end])
            if ref is not None or not is_external_url(dest):
                skipped.append(dest)
            pos = end
            continue
        if member in used:
            used[member] += 1
            stem, ext = Path(member).stem, Path(member).suffix
            member = f"{Path(member).parent}/{stem}_{used[member]}{ext}"
        else:
            used[member] = 1
        members.append((member, src))
        out.append(content[start:end].replace(dest, prefix + member, 1))
        pos = end
    out.append(content[pos:])
    return "".join(out), members, skipped


def plan_zip_bundle_many(
    notes: list[tuple[str, Path, str]],
    **kwargs,
) -> tuple[list[tuple[str, str, list[tuple[str, Path]]]], list[str]]:
    """多篇打包计划。

    ``notes``：``[(zip内相对路径如 folder/a.md, 磁盘md文件, 内容)]``。
    返回 ``([(zip内路径, 改写后内容, 成员)], 汇总跳过列表)``；成员去重在
    所有笔记间共享，媒体平铺 zip 根，链接按各自深度加 ``../`` 前缀。
    """
    used: dict = {}
    results: list[tuple[str, str, list[tuple[str, Path]]]] = []
    skipped_all: list[str] = []
    for zip_rel, md_file, content in notes:
        depth = zip_rel.count("/")
        new_content, members, skipped = plan_zip_bundle(
            content, md_file, depth=depth, used=used, **kwargs)
        results.append((zip_rel, new_content, members))
        skipped_all.extend(skipped)
    return results, skipped_all


def _search_md_job(root: Path, pattern: str, scope: str, report) -> list[dict]:
    """散装正则搜索（worker 线程）：扫 ``<root>`` 下 .md，跳过媒体面目录。"""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"正则无效: {e}") from e

    md_files = sorted(root.rglob("*.md"))
    md_files = [p for p in md_files
                if not any(part.lower() in _HIDDEN_ENTRIES for part in p.parts)]
    results: list[dict] = []
    total = len(md_files)
    for i, md in enumerate(md_files):
        rel = md.relative_to(root).as_posix()
        hits = 0
        snippet = ""
        if scope in ("name", "both") and rx.search(md.name):
            hits += 1
            snippet = md.name
        body = ""
        if scope in ("body", "both"):
            try:
                body = md.read_text(encoding="utf-8")
            except Exception:
                body = ""
            for line in body.splitlines():
                if rx.search(line):
                    hits += 1
                    if not snippet:
                        snippet = line.strip()[:160]
                    if hits >= 3:
                        break
        if hits:
            results.append({
                "rel": rel,
                "title": extract_title(body) if body else "",
                "hits": hits,
                "snippet": snippet,
            })
        report("progress", current=i + 1, total=total)
    return results


def _export_db_job(db_path: Path, src_root: Path, report) -> dict:
    """散装 → db 包（worker 线程）：建库并把 src_root 下 .md 导入。"""
    from server.notes_db import NotesDB
    db = NotesDB(db_path)
    try:
        return _import_folder_job(db, src_root, report)
    finally:
        db.close()
