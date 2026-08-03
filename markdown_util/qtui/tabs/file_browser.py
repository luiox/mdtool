"""File browser tab — PySide6 port of ``tabs/file_browser.py``.

Walks the project root directory (the one picked in the toolbar) and shows a
tree of folders + ``.md`` files. Right-click offers "打包为 ZIP" (bundle a
markdown + its locally-referenced images) and "迁移图片到图床" (hand off to
the Migrate tab). The migrate handoff uses a method on the main window rather
than a hard tab index, so it survives tab reordering.
"""

import zipfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qtui.icons import file_icon, folder_icon
from qtui.widgets import BaseTab, LogPanel
from utils import extract_image_links, resolve_image_path


class FileBrowserTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("文件浏览器")
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self.tree, 1)

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
            if entry.name.lower() == "assets":
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

    # ── context menu ──

    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        has_md_path = item.data(0, Qt.ItemDataRole.UserRole) is not None
        is_dir = item.data(0, Qt.ItemDataRole.UserRole) is None and self._item_path(item) is not None
        if has_md_path or is_dir:
            menu.addAction("打包为 ZIP", self.pack_to_zip)
            menu.addAction("迁移图片到图床", self.migrate_images)
        if menu.actions():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

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
            QMessageBox.warning(self, "警告", "请先选中一个 .md 文件或目录")
            return None
        return self._item_path(item)

    # ── actions ──

    def pack_to_zip(self):
        path = self._selected_path()
        if path is None:
            return
        if path.is_dir() or path.suffix.lower() != ".md":
            QMessageBox.warning(self, "警告", "请选中一个 .md 文件节点")
            return
        if not path.exists():
            QMessageBox.critical(self, "错误", f"文件不存在: {path}")
            return
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取文件失败: {e}")
            return

        links = extract_image_links(content)
        image_paths = []
        for link in links:
            resolved = resolve_image_path(path, link["rel_path"])
            if resolved and resolved.is_file():
                image_paths.append(resolved)
            else:
                self.log(f"图片未找到 (已跳过): {link['rel_path']}", "WARNING")

        default_name = path.stem + ".zip"
        zip_path, _ = QFileDialog.getSaveFileName(
            self, "保存 ZIP 文件", default_name, "ZIP 文件 (*.zip)")
        if not zip_path:
            return
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(path, path.name)
                used = set()
                for img in image_paths:
                    name = f"assets/{img.name}"
                    if name in used:
                        base, ext = img.stem, img.suffix
                        c = 1
                        while f"assets/{base}_{c}{ext}" in used:
                            c += 1
                        name = f"assets/{base}_{c}{ext}"
                    used.add(name)
                    zf.write(img, name)
            self.log(f"打包完成: {zip_path} (1 个 .md, {len(image_paths)} 张图片)")
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
