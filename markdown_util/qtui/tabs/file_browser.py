"""File browser tab — PySide6 port of ``tabs/file_browser.py``.

Walks the project root directory (the one picked in the toolbar) and shows a
tree of folders + ``.md`` files. Right-click offers "打包为 ZIP" (bundle a
markdown + its media into a self-contained zip, rewriting media URLs to
relative paths per docs/知识库规范.md §2/§5) and "迁移图片到图床" (hand off
to the Migrate tab). The migrate handoff uses a method on the main window
rather than a hard tab index, so it survives tab reordering.
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

from link_resolver import DEFAULT_HOST, DEFAULT_PORT, is_external_url, iter_link_destinations, parse
from qtui.icons import file_icon, folder_icon
from qtui.tabs.media_server import load_config as load_media_config
from qtui.widgets import BaseTab, LogPanel
from utils import resolve_image_path


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
            # 知识库根目录的媒体面（images/assets/meta.db）不属于笔记树
            if entry.name.lower() in ("assets", "images", "meta.db"):
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

        # 媒体根配置：把服务器 URL 解析到本地文件（知识库规范 §2）
        mcfg = load_media_config()
        host = mcfg.get("host") or DEFAULT_HOST
        try:
            port = int(mcfg.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        media_root = Path(mcfg["media_root"]) if mcfg.get("media_root") else None

        new_content, members, skipped = plan_zip_bundle(
            content, path,
            media_root=media_root,
            images_subdir=mcfg.get("images_subdir") or "images",
            assets_subdir=mcfg.get("assets_subdir") or "assets",
            host=host, port=port,
        )
        for d in skipped:
            self.log(f"媒体未找到 (已跳过): {d}", "WARNING")

        default_name = path.stem + ".zip"
        zip_path, _ = QFileDialog.getSaveFileName(
            self, "保存 ZIP 文件", default_name, "ZIP 文件 (*.zip)")
        if not zip_path:
            return
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(path.name, new_content)
                for member, src in members:
                    zf.write(src, member)
            self.log(f"打包完成: {zip_path} ({len(members)} 个媒体文件)")
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


# ── zip 打包计划（纯函数，可测试）──

def plan_zip_bundle(
    content: str,
    md_file: Path,
    *,
    media_root: Optional[Path] = None,
    images_subdir: str = "images",
    assets_subdir: str = "assets",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> tuple[str, list[tuple[str, Path]], list[str]]:
    """解析 md 内容中的媒体链接，产出 zip 打包计划。

    返回 ``(改写后的内容, [(zip成员名, 源文件路径), ...], [跳过的链接])``。

    - 本知识库媒体 URL（经 resolver 识别）→ 从媒体根 ``<media_root>/<subdir>`` 找文件；
      链接改写为 zip 内相对路径 ``images/<name>`` / ``assets/<name>``。
    - 相对路径（旧笔记风格）→ 按 ``md_file`` 所在目录解析，改写为 ``assets/<basename>``。
    - 外部链接 / 找不到文件 → 原样保留（找不到的记入 skipped 供日志）。
    """
    img_dir = Path(media_root) / images_subdir if media_root else None
    ast_dir = Path(media_root) / assets_subdir if media_root else None
    used: dict[str, int] = {}
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
        out.append(content[start:end].replace(dest, member, 1))
        pos = end
    out.append(content[pos:])
    return "".join(out), members, skipped
