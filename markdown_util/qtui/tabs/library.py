"""统一「笔记库」页 — 散装目录与 db 容器是同一棵笔记树的两种数据源。

UI 合并自旧的 文件浏览器 Tab 与 笔记库 Tab：顶部数据源切换
（``fs`` 散装 ⇄ ``db`` 容器），树 / 搜索 / 结果表共用，模式相关的动作
按需出现（右键菜单 + 工具行按钮簇）。纯逻辑分别住在
:mod:`qtui.tabs.file_browser`（散装）与 :mod:`qtui.tabs.notes_browser`
（db 后端），本页只做编排。

双模式语义（docs/知识库规范.md）：散装 = 规范格式（Typora 直接编辑、
手机端可读）；db 容器 = 单文件内存态，桌面专属。
"""

import hashlib
import os
import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QThread
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

import kb_bundle as kbb
from server.notes_db import NotesDB
from qtui.icons import file_icon, folder_icon
from qtui.tabs import file_browser as fb
from qtui.tabs import notes_browser as nb
from qtui.tabs.bundle_io import BundleCenterDialog
from qtui.widgets import BaseTab, PathRow
from qtui.workers import start_worker


class _TreeDropFilter(QObject):
    """接管库树的 Drop：内部拖拽→真实移动；外部拖入→收 .md。

    事件一律 ignore()，阻止 QTreeWidget 默认的"只重排视图不落盘"。
    """

    def __init__(self, page):
        super().__init__(page)
        self._page = page

    def eventFilter(self, obj, ev):  # noqa: N802 - Qt override
        if ev.type() == QEvent.Type.Drop:
            external = ev.source() is None
            target = self._page.tree.itemAt(ev.position().toPoint())
            sources = [] if external else list(self._page.tree.selectedItems())
            urls = list(ev.mimeData().urls()) if external else []
            ev.ignore()
            if sources:
                self._page.handle_tree_drop(sources, target)
            elif urls:
                self._page.handle_external_drop(urls, target)
            return True
        return False


class LibraryPage(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "fs"

        # ── db 侧状态（沿用旧笔记库 Tab 的编辑模型）──
        self.config = nb.load_config()
        self.db: Optional[nb.NotesDB] = None
        self._open_notes: dict[int, dict] = {}  # note_id -> {temp_path, last_hash, name, timer}
        self.temp_dir = nb.resolve_temp_dir(self.config)
        self._watchdog_thread = None
        self._watchdog_worker = None

        self._build_ui()
        self._set_mode("fs")
        try:
            self._db_open_library(Path(self.config["db_path"]))
        except Exception:
            pass

    # ── UI ──

    def _build_ui(self):
        """纯树页面：无工具行、无模式切换（切换在顶栏，状态在窗口标题），
        所有操作经右键菜单（含空白处的全局菜单）。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 0, 18, 14)

        self.tree = QTreeWidget()
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # 拖拽启用；Drop 事件由过滤器接管（见下），默认视觉重排被禁止
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._drop_filter = _TreeDropFilter(self)
        self.tree.viewport().installEventFilter(self._drop_filter)
        # 键盘基础操作：F2 重命名 / Delete 删除（按当前模式分发）
        for keys, fn in ((QKeySequence(Qt.Key.Key_F2), self._rename_current),
                         (QKeySequence(Qt.Key.Key_Delete), self._delete_current)):
            sc = QShortcut(keys, self.tree)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(fn)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_click)
        root.addWidget(self.tree)

    # ── 拖拽 ──
    # 树是磁盘/数据库的投影，QTreeWidget 默认 InternalMove 只做视觉重排
    # 不落盘——等于假移动。这里只把"谁拖到了哪"交给 owner 解析执行。

    def handle_tree_drop(self, sources: list, target_item):
        """拖拽落点：目标为空 = 库根；目标是文件夹 = 其内；是笔记 = 其所在目录。"""
        if self.mode == "fs":
            self._fs_drop(sources, target_item)
        else:
            self._db_drop(sources, target_item)

    def handle_external_drop(self, urls: list, target_item):
        """从资源管理器拖入：仅收 .md（散装复制文件，db 写入正文）。"""
        dest = "" if target_item is None else (
            self._folder_iid_of(target_item)
            if target_item.data(0, Qt.ItemDataRole.UserRole) is None
            else self._folder_iid_of(target_item.parent()))
        n_in = 0
        for u in urls:
            p = Path(u.toLocalFile())
            if not p.exists():
                continue
            candidates = ([p] if p.suffix.lower() == ".md"
                          else sorted(p.rglob("*.md")) if p.is_dir() else [])
            if not candidates and p.is_file():
                self.log(f"跳过非 Markdown: {p.name}", "WARNING")
            for f in candidates:
                rel = f"{dest}/{f.name}" if dest else f.name
                try:
                    if self.mode == "fs":
                        notes_root = kbb.resolve_notes_dir(self.root_dir)
                        dst = notes_root / rel
                        if dst.exists():
                            self.log(f"已存在，跳过: {rel}", "WARNING")
                            continue
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dst)
                    else:
                        if self.db.get_note_by_path(rel) is not None:
                            self.log(f"已存在，跳过: {rel}", "WARNING")
                            continue
                        body = f.read_text(encoding="utf-8")
                        self.db.upsert_note(rel, body)
                    n_in += 1
                    self.log(f"拖入: {rel}")
                except (OSError, UnicodeDecodeError) as e:
                    self.log(f"拖入失败 {f}: {e}", "ERROR")
        if n_in:
            self.refresh_tree()

    def _drop_dest_folder(self, target_item) -> str:
        if target_item is None:
            return ""
        if target_item.data(0, Qt.ItemDataRole.UserRole) is None:
            return self._folder_iid_of(target_item)
        parent = target_item.parent()
        return self._folder_iid_of(parent) if parent is not None else ""

    def _into_descendant(self, src_prefix: str, dest: str) -> bool:
        return bool(src_prefix) and (dest == src_prefix
                                     or dest.startswith(src_prefix + "/"))

    def _fs_drop(self, sources: list, target_item):
        notes_root = self._fs_notes_root()
        if notes_root is None:
            return
        dest = self._drop_dest_folder(target_item)
        blocked: list[str] = []
        moved_first = None
        for item in sources:
            rel = self._fs_item_rel(item)
            if rel is None:
                continue
            is_dir = item.data(0, Qt.ItemDataRole.UserRole) is None
            prefix = rel if is_dir else ""
            if self._into_descendant(prefix, dest):
                blocked.append(f"{rel}（不能移入自身/子目录）")
                continue
            name = rel.rsplit("/", 1)[-1]
            dst_rel = f"{dest}/{name}" if dest else name
            if dst_rel == rel:
                continue
            src_p, dst_p = notes_root / rel, notes_root / dst_rel
            if dst_p.exists():
                blocked.append(f"{dst_rel}（已存在）")
                continue
            try:
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src_p), str(dst_p))
            except OSError as e:
                blocked.append(f"{rel}（{e}）")
                continue
            if moved_first is None:
                moved_first = dst_rel
            self.log(f"移动: {rel} → {dst_rel}")
        self.refresh_tree()
        if blocked:
            QMessageBox.warning(self, "部分未移动", "\n".join(blocked))
        if moved_first:
            self._reveal_rel(moved_first)

    def _db_drop(self, sources: list, target_item):
        db = self.db
        if db is None:
            return
        dest = self._drop_dest_folder(target_item)
        blocked: list[str] = []
        reveal_id = None
        for item in sources:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data is not None:  # 笔记
                nid = int(data)
                row = db.get_note(nid)
                if row is None:
                    continue
                name = row["path"].rsplit("/", 1)[-1]
                new_path = NotesDB.normalize_path(
                    f"{dest}/{name}" if dest else name)
                if new_path == row["path"]:
                    continue
                if db.get_note_by_path(new_path) is not None:
                    blocked.append(f"{new_path}（已存在）")
                    continue
                db.rename(nid, new_path)
                reveal_id = nid
                self.log(f"移动: {row['path']} → {new_path}")
            else:  # 文件夹 = 路径前缀，整枝改写
                prefix = self._folder_iid_of(item)
                rows = [r["path"] for r in db.list_all()
                        if r["path"] == prefix or r["path"].startswith(prefix + "/")]
                if not rows:
                    continue
                if self._into_descendant(prefix, dest):
                    blocked.append(f"{prefix}/（不能移入自身子目录）")
                    continue
                tail = prefix.rsplit("/", 1)[-1]
                new_prefix = f"{dest}/{tail}" if dest else tail
                plan = [(r, NotesDB.normalize_path(new_prefix + r[len(prefix):]))
                        for r in rows]
                if any(db.get_note_by_path(np) is not None
                       for _, np in plan if np != ""):
                    blocked.append(f"{prefix}/（目标已有同名）")
                    continue
                for old, new in plan:
                    nid_row = db.get_note_by_path(old)
                    if nid_row is not None:
                        db.rename(int(nid_row["id"]), new)
                reveal_id = reveal_id or (db.get_note_by_path(plan[0][1]) or {"id": None})["id"]
                self.log(f"移动文件夹: {prefix}/ → {new_prefix}/")
        self.refresh_tree()
        if blocked:
            QMessageBox.warning(self, "部分未移动", "\n".join(blocked))
        if reveal_id:
            self._reveal_note(reveal_id)

    def _reveal_rel(self, rel: str):
        """刷新后按相对路径逐段定位并选中（fs）。"""
        cur = self.tree.invisibleRootItem()

        def find(node, seg):
            for i in range(node.childCount()):
                ch = node.child(i)
                if ch.text(0) == seg:
                    return ch
            return None

        for seg in rel.split("/"):
            nxt = find(cur, seg) if cur is not None else None
            if nxt is None:
                return
            cur = nxt
        if cur is not None:
            self.tree.setCurrentItem(cur)
            self.tree.scrollToItem(cur)

    def _reveal_note(self, note_id: int):
        def walk(node) -> Optional[object]:
            for i in range(node.childCount()):
                ch = node.child(i)
                if ch.data(0, Qt.ItemDataRole.UserRole) == str(note_id) \
                        or ch.data(0, Qt.ItemDataRole.UserRole) == note_id:
                    return ch
                hit = walk(ch)
                if hit is not None:
                    return hit
            return None

        hit = walk(self.tree.invisibleRootItem())
        if hit is not None:
            self.tree.setCurrentItem(hit)
            self.tree.scrollToItem(hit)

    # ── 模式切换 ──

    def _set_mode(self, mode: str):
        self.mode = mode
        self.tree.setHeaderLabel(
            "知识库（散装笔记）" if mode == "fs" else "笔记库（SQLite 容器）")
        # 媒体元数据源跟随模式：db 模式指向 notes.db，散装还原 meta.db（规范 §4.1）
        self._set_media_meta_override(
            None if mode == "fs" else (Path(self.config["db_path"])
                                       if self.config.get("db_path") else None))
        self.refresh_tree()

    def _set_media_meta_override(self, meta_path: Optional[Path]):
        """把媒体服务器的元数据源切到指定 sqlite（服务器未运行时静默跳过）。"""
        if self.main_window is None:
            return
        try:
            media_tab = self.main_window._pages["media"]["tab"]
            media_tab.set_meta_override(meta_path)
        except (KeyError, AttributeError, TypeError):
            pass

    # ── hooks ──

    def set_root_dir(self, root_dir: Optional[Path]):
        super().set_root_dir(root_dir)
        if self.mode == "fs":
            self.refresh_tree()

    # ── 树构建 ──

    def refresh_tree(self):
        self.tree.clear()
        if self.mode == "fs":
            self._fs_build_tree()
        else:
            self._db_build_tree()

    def _fs_notes_root(self) -> Optional[Path]:
        """散装视图根 = 笔记树目录。markdown/ 是笔记的根而非仓库根，
        不应作为树的一级层级出现（双名兼容：无 markdown/ 时退 notes/，
        存量散装布局退根本身）。"""
        if not self.root_dir:
            return None
        return kbb.resolve_notes_dir(self.root_dir)

    def _fs_build_tree(self):
        notes_root = self._fs_notes_root()
        if notes_root is None:
            return
        folders: dict[str, QTreeWidgetItem] = {}

        def ensure_folder(folder_path: str) -> QTreeWidgetItem:
            if folder_path in folders:
                return folders[folder_path]
            if "/" in folder_path:
                parent_path, name = folder_path.rsplit("/", 1)
                parent = ensure_folder(parent_path)
            else:
                parent = self.tree.invisibleRootItem()
                name = folder_path
            item = QTreeWidgetItem(parent, [name])
            item.setIcon(0, folder_icon())
            folders[folder_path] = item
            return item

        for dirpath, dirnames, _files in os.walk(notes_root):
            # 空文件夹也要出现在树上（否则"新建文件夹"后像没生效）
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            dirnames.sort(key=str.lower)
            rel_dir = Path(dirpath).relative_to(notes_root).as_posix()
            if rel_dir != ".":
                ensure_folder(rel_dir)

        for rel, _abs in fb.list_md_tree(notes_root):
            folder, _, name = rel.rpartition("/")
            parent = ensure_folder(folder) if folder else self.tree.invisibleRootItem()
            item = QTreeWidgetItem(parent, [name])
            item.setIcon(0, file_icon())
            item.setData(0, Qt.ItemDataRole.UserRole, rel)
            item.setToolTip(0, rel)

    def _db_build_tree(self):
        if self.db is None:
            return
        folders: dict[str, QTreeWidgetItem] = {}

        def ensure_folder(folder_path: str) -> QTreeWidgetItem:
            if folder_path in folders:
                return folders[folder_path]
            if "/" in folder_path:
                parent_path, name = folder_path.rsplit("/", 1)
                parent = ensure_folder(parent_path)
            else:
                parent = self.tree.invisibleRootItem()
                name = folder_path
            item = QTreeWidgetItem(parent, [name])
            item.setIcon(0, folder_icon())
            folders[folder_path] = item
            return item

        for row in self.db.list_all():
            folder, _, name = row["path"].rpartition("/")
            parent = ensure_folder(folder) if folder else self.tree.invisibleRootItem()
            item = QTreeWidgetItem(parent, [name])
            item.setIcon(0, file_icon())
            item.setData(0, Qt.ItemDataRole.UserRole, int(row["id"]))
            item.setToolTip(0, row["path"])

    # ── 选中项解析 ──

    def _selected_note_id(self) -> Optional[int]:
        """db 模式：当前树项携带的笔记 id（文件夹节点无 id 返回 None）。"""
        item = self.tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return int(data) if data is not None else None

    def _folder_iid_of(self, item: Optional[QTreeWidgetItem]) -> str:
        parts = []
        cur = item
        while cur is not None and cur is not self.tree.invisibleRootItem():
            parts.append(cur.text(0))
            cur = cur.parent()
        return "/".join(reversed(parts))

    def _current_folder_path(self) -> str:
        """当前选中节点所属文件夹的逻辑路径（db 模式用 / 分隔）。"""
        item = self.tree.currentItem()
        if item is None:
            return ""
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            parent = item.parent()
            return self._folder_iid_of(parent) if parent is not None else ""
        return self._folder_iid_of(item)

    def _fs_item_rel(self, item: QTreeWidgetItem) -> Optional[str]:
        """fs 模式：树项的相对路径（md 节点取 UserRole；文件夹走树上溯）。"""
        if not self.root_dir or item is self.tree.invisibleRootItem():
            return None
        rel = item.data(0, Qt.ItemDataRole.UserRole)
        if rel:
            return rel
        return self._folder_iid_of(item)

    def _fs_selected_md_files(self) -> list[Path]:
        """当前选中的 .md；目录递归收集（相对笔记树根解析）。"""
        items = self.tree.selectedItems()
        if not items and self.tree.currentItem() is not None:
            items = [self.tree.currentItem()]
        notes_root = self._fs_notes_root()
        if notes_root is None:
            return []
        md_files: list[Path] = []
        for it in items:
            rel = self._fs_item_rel(it)
            if rel is None:
                continue
            p = notes_root / rel
            if p.is_dir():
                md_files.extend(p.rglob("*.md"))
            elif p.suffix.lower() == ".md":
                md_files.append(p)
        return sorted(set(md_files))

    # ── 右键菜单 ──
    # 结构约定：条目菜单 = 作用于该条目的动作 + 一组新建；空白处 = 根锚点
    # 的模式级动作；库级动作统一收进尾部「库」子菜单。同名动作在任何
    # 菜单里只出现一次。

    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            # 空白处 = 库根。锚点显式为 ""——绝不吸附当前选中项
            self._blank_menu(menu)
        else:
            self.tree.setCurrentItem(item)
            if self.mode == "fs":
                self._fs_menu(menu, item, anchor=self._fs_anchor_for_item(item))
            else:
                self._db_menu(menu, item)
        self._common_tail(menu)
        if menu.actions():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _new_entries(self, menu: QMenu, anchor: str = ""):
        """新建组：锚点目录落点（文件夹=其内 / 笔记=同级 / 空白=根）。"""
        act = menu.addAction("新建文件夹…", lambda: self._fs_new_folder(anchor))
        act.setEnabled(bool(self.root_dir))
        act = menu.addAction("新建 Markdown 文件…",
                             lambda: self._fs_new_note_at(anchor))
        act.setEnabled(bool(self.root_dir))

    def _blank_menu(self, menu: QMenu):
        if self.mode == "fs":
            self._new_entries(menu, anchor="")
            menu.addSeparator()
            act = menu.addAction("打包全库 ZIP", self.pack_all_zip)
            act.setEnabled(bool(self.root_dir))
        else:
            menu.addAction("新建笔记（根目录）", self.new_note)
            menu.addSeparator()
            menu.addAction("导入文件夹到笔记库…", self.import_folder)
            menu.addAction("导出为文件夹…", self.export_folder)
            menu.addSeparator()
            menu.addAction("编辑器与落地目录设置…", self.edit_library_settings)

    def _common_tail(self, menu: QMenu):
        """共用收尾：「库」子菜单 + 刷新。"""
        menu.addSeparator()
        lib = menu.addMenu("库")
        if self.main_window is not None:
            lib.addAction("打开笔记库 (.db)…", self.main_window.pick_db_file)
            lib.addAction("新建笔记库…", self.main_window.create_db_file)
        lib.addSeparator()
        lib.addAction("导入导出中心…", lambda: self.open_bundle_center())
        menu.addSeparator()
        menu.addAction("刷新", self.refresh_tree)

    def _fs_anchor_for_item(self, item: QTreeWidgetItem) -> str:
        """新建类操作的锚点目录：笔记 → 所在目录；文件夹 → 自身。"""
        rel = self._fs_item_rel(item)
        if rel is None:
            return ""
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            return rel.rpartition("/")[0]
        return rel

    def _fs_menu(self, menu: QMenu, item: QTreeWidgetItem, anchor: str = ""):
        rel = self._fs_item_rel(item)
        notes_root = self._fs_notes_root()
        if rel is None or notes_root is None:
            return
        p = notes_root / rel
        is_md_file = p.is_file() and p.suffix.lower() == ".md"
        # 新建（锚定本条目）
        self._new_entries(menu, anchor)
        # 打开 / 移动 / 删除 —— 文件与文件夹同等支持
        menu.addSeparator()
        if is_md_file:
            menu.addAction("打开（系统默认程序）", lambda: fb.open_external(p))
        menu.addAction("重命名/移动…", self._fs_rename_selected)
        menu.addAction("删除", self._fs_delete_selected)
        # 打包 / 迁移 / 导出导入
        md_files = self._fs_selected_md_files()
        if md_files:
            menu.addSeparator()
            label = f"打包为 ZIP（{len(md_files)} 篇）" if len(md_files) > 1 else "打包为 ZIP"
            menu.addAction(label, self.pack_to_zip)
            if len(md_files) == 1 and is_md_file:
                menu.addAction("迁移图片到图床", self.migrate_images)
            n = len(md_files)
            label = f"导出 / 导入（已选 {n} 篇）…" if n > 1 else "导出 / 导入…"
            menu.addAction(label, lambda: self.open_bundle_center(
                pre_rels=[kbb.note_rel(pp, self.root_dir) for pp in md_files]))

    def _db_menu(self, menu: QMenu, item: QTreeWidgetItem):
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            menu.addAction("打开（编辑）", self.open_selected)
            menu.addSeparator()
            menu.addAction("在此文件夹新建笔记…",
                           lambda: self._db_new_note_at(self._current_folder_path()))
            menu.addSeparator()
            menu.addAction("重命名/移动…", self._db_rename_selected)
            menu.addAction("删除", self._db_delete_selected)
        else:
            folder = self._folder_iid_of(item)
            menu.addAction("新建笔记…", lambda: self._db_new_note_at(folder))
            menu.addSeparator()
            menu.addAction("重命名/移动…", self._db_rename_selected)
            menu.addAction("删除", self._db_delete_selected)
        paths = self._db_selected_note_paths()
        if paths:
            menu.addSeparator()
            label = (f"导出 / 导入（已选 {len(paths)} 篇）…" if len(paths) > 1
                     else "导出 / 导入…")
            menu.addAction(label, lambda: self.open_bundle_center(pre_rels=paths))

    def _on_tree_double_click(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return
        if self.mode == "db":
            self._db_open_note_for_edit(int(data))
        elif self._fs_notes_root() is not None:
            fb.open_external(self._fs_notes_root() / str(data))

    # ── 散装侧操作 ──

    def _fs_new_folder(self, folder: str = "") -> Optional[str]:
        """在锚点目录（默认根）新建真实文件夹；返回新 rel 供选中定位。"""
        notes_root = self._fs_notes_root()
        if notes_root is None:
            QMessageBox.warning(self, "警告", "请先在顶栏选择知识库根目录")
            return None
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not ok or not name:
            return None
        name = name.strip().strip("/\\")
        if not name or "/" in name or "\\" in name or any(c in name for c in '<>:"|?*'):
            QMessageBox.warning(self, "提示", f"名称不合法: {name}")
            return None
        target = notes_root / folder / name if folder else notes_root / name
        if target.exists():
            QMessageBox.warning(self, "提示", f"已存在同名文件夹: {name}")
            return None
        try:
            target.mkdir(parents=True)
        except OSError as e:
            QMessageBox.critical(self, "新建文件夹失败", str(e))
            return None
        new_rel = f"{folder}/{name}" if folder else name
        self.log(f"新建文件夹: {new_rel}")
        self.refresh_tree()
        self._reveal_rel(new_rel)
        return new_rel

    def _fs_new_note_at(self, folder: str = ""):
        """在锚点目录新建 .md；名字可含子路径（自动建父级）。"""
        if not self.root_dir:
            QMessageBox.warning(self, "警告", "请先在顶栏选择知识库根目录")
            return
        name, ok = QInputDialog.getText(
            self, "新建笔记", "笔记文件名（可含子路径，如 sub/名字.md）：",
            text="未命名.md")
        if not ok or not name:
            return
        notes_root = self._fs_notes_root()
        target = notes_root / folder / name if folder else notes_root / name
        if not target.suffix:
            target = target.with_suffix(".md")
        if target.exists():
            QMessageBox.warning(self, "提示", f"文件已存在: {target}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {target.stem}\n", encoding="utf-8")
        new_rel = target.relative_to(notes_root).as_posix()
        self.log(f"新建笔记: {new_rel}")
        self.refresh_tree()
        self._reveal_rel(new_rel)
        fb.open_external(target)

    def _rename_current(self):
        """F2：按模式分发到重命名。"""
        if self.tree.currentItem() is None:
            return
        (self._fs_rename_selected if self.mode == "fs"
         else self._db_rename_selected)()

    def _delete_current(self):
        """Delete：按模式分发到删除。"""
        if self.tree.currentItem() is None:
            return
        (self._fs_delete_selected if self.mode == "fs"
         else self._db_delete_selected)()

    def _fs_single_target(self) -> Optional[tuple[str, bool]]:
        """当前条目的 (rel, 是否文件夹)；无选中返回 None。"""
        rel = self._fs_item_rel(self.tree.currentItem())
        if rel is None:
            return None
        is_dir = self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) is None
        return rel, is_dir

    def _fs_rename_selected(self):
        """重命名/移动当前条目——文件与文件夹同等支持。

        文件夹整枝 shutil.move；拒绝移入自身子目录。
        """
        notes_root = self._fs_notes_root()
        if notes_root is None:
            return
        target = self._fs_single_target()
        if target is None:
            QMessageBox.warning(self, "提示", "请先选中要重命名的条目")
            return
        rel, is_dir = target
        new_rel, ok = QInputDialog.getText(
            self, "重命名/移动", "新的相对路径（用 / 分隔文件夹）：", text=rel)
        if not ok or not new_rel or new_rel == rel:
            return
        if is_dir and (new_rel == rel or new_rel.startswith(rel + "/")):
            QMessageBox.warning(self, "提示", "不能把文件夹移动到其自身内部")
            return
        src, dst = notes_root / rel, notes_root / new_rel
        if dst.exists():
            QMessageBox.warning(self, "提示", f"目标已存在: {new_rel}")
            return
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except OSError as e:
            QMessageBox.critical(self, "重命名失败", str(e))
            return
        self.log(f"已重命名: {rel} → {new_rel}")
        self.refresh_tree()
        self._reveal_rel(new_rel)

    def _fs_delete_selected(self):
        """删除当前条目——文件 unlink，文件夹递归删除（确认文案明示）。"""
        notes_root = self._fs_notes_root()
        if notes_root is None:
            return
        target = self._fs_single_target()
        if target is None:
            QMessageBox.warning(self, "提示", "请先选中要删除的条目")
            return
        rel, is_dir = target
        what = "文件夹「{0}」及其全部内容" if is_dir else "文件「{0}」"
        if QMessageBox.question(
                self, "确认删除",
                f"删除{what.format(rel)}？此操作不可撤销。"
                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            if is_dir:
                shutil.rmtree(notes_root / rel)
            else:
                (notes_root / rel).unlink()
        except OSError as e:
            QMessageBox.critical(self, "删除失败", str(e))
            return
        self.log(f"已删除: {rel}")
        self.refresh_tree()

    def migrate_images(self):
        """单篇交接给迁移页（菜单项仅在恰好选中一篇时出现）。"""
        md_files = self._fs_selected_md_files()
        if len(md_files) == 1 and self.main_window is not None:
            self.main_window.open_migrate(md_files[0])

    # ── zip 导出（散装）──

    def pack_all_zip(self):
        notes_root = self._fs_notes_root()
        if notes_root is None:
            QMessageBox.warning(self, "警告", "请先选择知识库根目录")
            return
        md_files = [p for _, p in fb.list_md_tree(notes_root)]
        if not md_files:
            QMessageBox.warning(self, "警告", "知识库下没有 .md 文件")
            return
        self._pack_zip(md_files, base=notes_root, default_name="知识库导出.zip")

    def pack_to_zip(self):
        md_files = self._fs_selected_md_files()
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
        from qtui.tabs.media_server import load_config as load_media_config
        mc = fb.collect_media_config(load_media_config)
        results, skipped = fb.plan_zip_bundle_many(
            notes, media_root=mc["media_root"], images_subdir=mc["images_subdir"],
            assets_subdir=mc["assets_subdir"], host=mc["host"], port=mc["port"],
        )
        for d in skipped:
            self.log(f"媒体未找到 (已跳过): {d}", "WARNING")
        try:
            n_notes, n_media = fb.write_zip(zip_path, results, len(notes))
            self.log(f"打包完成: {zip_path}（{n_notes} 篇，{n_media} 个媒体文件）")
        except Exception as e:
            QMessageBox.critical(self, "打包失败", str(e))

    # ── 合并包导出 / 导入（规范 §2/§3；对话框与 job 在 qtui.tabs.bundle_io）──

    def _media_root_for_bundle(self) -> Optional[Path]:
        """媒体根：来自媒体服务器页配置；未配置返回 None。"""
        try:
            from qtui.tabs.media_server import load_config as load_media_config
            mr = load_media_config().get("media_root")
            return Path(mr) if mr else None
        except Exception:
            return None

    def _db_selected_note_paths(self) -> list[str]:
        """db 模式当前选中的笔记逻辑路径；文件夹节点按前缀收集整棵子树。"""
        items = self.tree.selectedItems()
        if not items and self.tree.currentItem() is not None:
            items = [self.tree.currentItem()]
        if self.db is None:
            return []
        exact: set[str] = set()
        prefixes: list[str] = []
        for it in items:
            data = it.data(0, Qt.ItemDataRole.UserRole)
            if data is None:
                prefixes.append(self._folder_iid_of(it))
            else:
                row = self.db.get_note(int(data))
                if row is not None:
                    exact.add(row["path"])
        for row in self.db.list_all():
            path = row["path"]
            if any(p and (path == p or path.startswith(p + "/")) for p in prefixes):
                exact.add(path)
        return sorted(exact)

    def open_bundle_center(self, pre_rels=None):
        """打开「导入导出中心」窗口：树形勾选导出范围 / 读包对账导入。

        ``pre_rels``：右键带入的当前选中笔记（笔记树相对路径），在导出页
        预先勾选；None = 全选。
        """
        target_root = None
        target_db = None
        if self.mode == "fs":
            if not self.root_dir:
                QMessageBox.warning(self, "警告", "请先在顶栏选择知识库根目录")
                return
            target_root = self.root_dir
        elif self._require_db() is not None:
            target_db = self.db
        else:
            return
        media_root = self._media_root_for_bundle()
        fs_mode = self.mode == "fs"
        if not fs_mode and media_root is None:
            QMessageBox.warning(
                self, "提示",
                "db 模式导出需要先在「媒体服务器」页设置媒体根目录，\n"
                "否则无法定位笔记引用的图片与附件（导入不受影响）。")
        src_root = target_root if fs_mode else media_root
        cfg = kbb.ensure_kb_config(src_root) if fs_mode and src_root else \
            kbb.load_kb_config(media_root) if media_root else dict(kbb.DEFAULT_KB_CONFIG)
        dlg = BundleCenterDialog(
            self, target_kind=self.mode, target_root=target_root,
            target_db=target_db, target_media_root=media_root,
            meta_db=(None if fs_mode else self.config.get("db_path")),
            kb_config=cfg, log=self.log, pre_selected_rels=pre_rels)
        dlg.exec()
        if dlg.applied:
            self.refresh_tree()

    # ── db 侧：库管理 ──

    def open_db_file(self, db_path: str):
        """顶栏「db 容器」按钮选完文件后：落配置并打开库。

        打开失败时置回 None 并记日志（不弹窗，避免对话框嵌套）。
        """
        try:
            self._db_open_library(Path(db_path))
        except Exception as e:
            self.db = None
            self.log(f"打开笔记库失败: {e}", "ERROR")
            return
        self.config["db_path"] = db_path
        nb.save_config(self.config)

    def edit_library_settings(self):
        """编辑器命令与落地目录（低频设置，入口在 db 模式右键菜单）。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("笔记库设置")
        form = QFormLayout(dlg)
        editor_edit = QLineEdit(self.config["editor_command"])
        form.addRow("编辑器命令 (空=系统默认):", editor_edit)
        temp_row = PathRow(self.config.get("temp_root", ""), mode="dir",
                           dialog_title="选择落地目录（如 RAM 盘根目录）",
                           placeholder="空 = 系统临时目录")
        form.addRow("落地目录 (支持 RAM 盘):", temp_row)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._db_set_temp_root(temp_row.text())
            self.config["editor_command"] = editor_edit.text().strip()
            nb.save_config(self.config)

    def _db_open_library(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = nb.NotesDB(db_path)
        self.log(f"已打开笔记库: {db_path}")
        self._maybe_migrate_meta(db_path)
        self._set_media_meta_override(Path(self.config["db_path"]))
        if self._watchdog_thread is None:
            self._db_start_watchdog()
        if self.mode == "db":
            self.refresh_tree()

    def _maybe_migrate_meta(self, db_path: Path):
        """老布局的 meta.db 并入 notes.db（规范 §4：一次性，搬完改名留底）。

        候选位置 = notes.db 同目录与媒体服务器配置的媒体根；目标表已有行时
        跳过（migrate 只在旧文件存在且可读时执行 INSERT OR REPLACE）。
        """
        candidates = [db_path.parent / "meta.db"]
        media_root = self._media_root_for_bundle()
        if media_root is not None:
            candidates.append(media_root / "meta.db")
        seen: set[Path] = set()
        for old in candidates:
            if old in seen or not old.is_file():
                continue
            seen.add(old)
            try:
                n = kbb.migrate_meta_into_db(db_path, old)
            except Exception as e:
                self.log(f"meta 并表失败（跳过）: {old} — {e}", "WARNING")
                continue
            if n:
                try:
                    old.rename(old.with_name("meta.db.migrated"))
                    self.log(f"媒体元数据并入 notes.db {n} 行（旧文件留底: "
                             f"{old.name}.migrated）")
                except OSError as e:
                    self.log(f"元数据已并表，但旧文件改名失败: {e}", "WARNING")

    def _db_start_watchdog(self):
        self._watchdog_thread = QThread()
        self._watchdog_worker = nb._WatchdogWorker(self.temp_dir)
        self._watchdog_worker.moveToThread(self._watchdog_thread)
        self._watchdog_thread.started.connect(self._watchdog_worker.start)
        self._watchdog_worker.path_changed.connect(self._on_temp_changed)
        self._watchdog_thread.start()

    def _db_stop_watchdog(self):
        if self._watchdog_worker is not None:
            try:
                # stop/join is safe from here: observer uses plain threading.
                self._watchdog_worker.stop()
            except Exception:
                pass
        if self._watchdog_thread is not None:
            try:
                self._watchdog_thread.quit()
                self._watchdog_thread.wait(2000)
            except Exception:
                pass
            self._watchdog_thread = None
            self._watchdog_worker = None

    def _db_set_temp_root(self, root: str):
        """切换落地目录：保存并关闭当前编辑会话 → 重启 watchdog。"""
        root = (root or "").strip()
        if root == self.config.get("temp_root", ""):
            return
        if self._open_notes:
            if QMessageBox.question(
                self, "切换落地目录",
                "切换落地目录将先保存并关闭当前编辑会话。是否继续?"
            ) != QMessageBox.StandardButton.Yes:
                return
            self._flush_all_open()
            for info in self._open_notes.values():
                try:
                    info["temp_path"].unlink()
                except OSError:
                    pass
            self._open_notes.clear()
        try:
            self._db_stop_watchdog()
            self.config["temp_root"] = root
            self.temp_dir = nb.resolve_temp_dir(self.config)
            nb.save_config(self.config)
            self._db_start_watchdog()
            self.log(f"落地目录: {self.temp_dir}")
        except Exception as e:
            QMessageBox.critical(self, "切换失败", str(e))

    def _require_db(self):
        if self.db is None:
            QMessageBox.warning(self, "提示", "请先打开或新建一个笔记库")
            return None
        return self.db

    # ── db 侧：编辑模型 ──

    def open_selected(self):
        nid = self._selected_note_id()
        if nid is not None:
            self._db_open_note_for_edit(nid)

    def _db_open_note_for_edit(self, note_id: int):
        db = self._require_db()
        if db is None:
            return
        row = db.get_note(note_id)
        if row is None:
            QMessageBox.critical(self, "错误", "笔记不存在")
            return
        body = row["body"]
        safe_name = row["name"].replace("/", "_").replace("\\", "_")
        temp_path = self.temp_dir / f"{note_id}__{safe_name}"

        if note_id in self._open_notes:
            info = self._open_notes[note_id]
            if info["last_hash"] != hashlib.sha256(body.encode("utf-8")).hexdigest():
                if QMessageBox.question(
                    self, "重新打开",
                    f"已经打开过「{row['name']}」，数据库内容与当前编辑版本不同。\n是否用数据库内容覆盖编辑文件？"
                ) != QMessageBox.StandardButton.Yes:
                    return
            self._write_temp(temp_path, body)
            info["last_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
            self._launch_editor(temp_path)
            self.log(f"重新打开: {row['name']}")
            return

        self._write_temp(temp_path, body)
        self._open_notes[note_id] = {
            "temp_path": temp_path,
            "last_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "name": row["name"],
            "timer": None,
        }
        self._launch_editor(temp_path)
        self.log(f"已打开: {row['name']} → {temp_path}")

    @staticmethod
    def _write_temp(temp_path: Path, body: str):
        temp_path.write_text(body, encoding="utf-8")

    def _launch_editor(self, temp_path: Path):
        cmd_template = self.config.get("editor_command", "").strip()
        if cmd_template:
            cmd = cmd_template.replace("{file}", str(temp_path))
            import subprocess
            try:
                subprocess.Popen(cmd, shell=True)
                return
            except Exception as e:
                QMessageBox.critical(self, "编辑器启动失败", f"{e}\n回退到系统默认程序。")
        import subprocess
        import sys
        try:
            os.startfile(str(temp_path))  # Windows
        except AttributeError:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(temp_path)])

    def _on_temp_changed(self, src_path: str):
        p = Path(src_path)
        note_id = None
        for nid, info in self._open_notes.items():
            try:
                if info["temp_path"].resolve() == p.resolve():
                    note_id = nid
                    break
            except OSError:
                if str(info["temp_path"]) == src_path:
                    note_id = nid
                    break
        if note_id is None or self.db is None:
            return
        info = self._open_notes[note_id]
        # Debounce: (re)start a single-shot timer for this note.
        if info.get("timer") is not None:
            self.killTimer(info["timer"])
        info["timer"] = self.startTimer(nb._DEBOUNCE_MS)

    def timerEvent(self, event):  # noqa: N802 - Qt override
        """Fire the debounced write-back for whichever note's timer expired."""
        expired = None
        for nid, info in self._open_notes.items():
            if info.get("timer") == event.timerId():
                expired = nid
                break
        if expired is None:
            return
        info = self._open_notes[expired]
        self.killTimer(event.timerId())
        info["timer"] = None
        if self.db is None:
            return
        try:
            body = info["temp_path"].read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        new_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if new_hash == info["last_hash"]:
            return
        try:
            self.db.update_body(expired, body)
            info["last_hash"] = new_hash
            self.log(f"已自动保存: {info['name']}")
        except Exception as e:
            self.log(f"保存失败: {e}", "ERROR")

    # ── db 侧：CRUD ──

    def new_note(self):
        self._db_new_note_at(self._current_folder_path())

    def _db_new_note_at(self, folder: str):
        db = self._require_db()
        if db is None:
            return
        name, ok = QInputDialog.getText(
            self, "新建笔记", "笔记文件名（可含子路径，如 sub/名字.md）：",
            text="未命名.md")
        if not ok or not name:
            return
        name = name.replace("\\", "/").strip("/")
        path = f"{folder}/{name}" if folder else name
        if not path.endswith(".md"):
            path += ".md"
        if db.get_note_by_path(path) is not None:
            QMessageBox.warning(self, "提示", f"已存在同名笔记: {path}")
            return
        body = f"# {Path(name).stem}\n"
        nid = db.upsert_note(path, body)
        self.refresh_tree()
        self.log(f"新建笔记: {path}")
        self._db_open_note_for_edit(int(nid))

    def _db_move_prefix(self, prefix: str, new_prefix: str) -> tuple[bool, str]:
        """把前缀 prefix 下所有笔记路径整枝改写到 new_prefix。

        返回 (是否成功, 失败原因)。冲突检查通过后才执行，不做半截移动。
        """
        rows = [r["path"] for r in self.db.list_all()
                if r["path"].startswith(prefix + "/")]
        if not rows:
            return False, "文件夹不存在或为空"
        if new_prefix == prefix or new_prefix.startswith(prefix + "/"):
            return False, "不能把文件夹移动到其自身内部"
        plan = []
        for old in rows:
            np = NotesDB.normalize_path(new_prefix + old[len(prefix):])
            if self.db.get_note_by_path(np) is not None:
                return False, f"目标路径已存在: {np}"
            plan.append((old, np))
        for old, np in plan:
            row = self.db.get_note_by_path(old)
            if row is not None:
                self.db.rename(int(row["id"]), np)
        return True, ""

    def _db_rename_selected(self):
        item = self.tree.currentItem()
        if item is None or self.db is None:
            return
        if item.data(0, Qt.ItemDataRole.UserRole) is None:
            # 文件夹：整枝前缀改写
            prefix = self._folder_iid_of(item)
            new_prefix, ok = QInputDialog.getText(
                self, "重命名/移动文件夹", "新的文件夹路径（用 / 分隔）：",
                text=prefix)
            if not ok or not new_prefix:
                return
            new_prefix = self.db.normalize_path(new_prefix)
            done, err = self._db_move_prefix(prefix, new_prefix)
            if not done:
                QMessageBox.warning(self, "无法移动", err)
                return
            self.refresh_tree()
            self.log(f"已移动文件夹: {prefix}/ → {new_prefix}/")
            return
        note_id = self._selected_note_id()
        row = self.db.get_note(note_id)
        if row is None:
            return
        new_path, ok = QInputDialog.getText(self, "重命名/移动", "新的逻辑路径（用 / 分隔文件夹）：",
                                            text=row["path"])
        if not ok or not new_path:
            return
        new_path = self.db.normalize_path(new_path)
        if new_path == row["path"]:
            return
        if self.db.get_note_by_path(new_path) is not None:
            QMessageBox.warning(self, "提示", f"目标路径已存在: {new_path}")
            return
        self.db.rename(note_id, new_path)
        if note_id in self._open_notes:
            safe = new_path.rsplit("/", 1)[-1].replace("/", "_").replace("\\", "_")
            new_temp = self.temp_dir / f"{note_id}__{safe}"
            old_temp = self._open_notes[note_id]["temp_path"]
            try:
                if old_temp.exists():
                    old_temp.rename(new_temp)
            except OSError:
                pass
            self._open_notes[note_id]["temp_path"] = new_temp
            self._open_notes[note_id]["name"] = new_path.rsplit("/", 1)[-1]
        self.refresh_tree()
        self.log(f"已重命名: {row['path']} → {new_path}")

    def _db_delete_selected(self):
        item = self.tree.currentItem()
        if item is None or self.db is None:
            return
        if item.data(0, Qt.ItemDataRole.UserRole) is None:
            # 文件夹：递归删除其下全部笔记（确认文案明示数量）
            prefix = self._folder_iid_of(item)
            victims = [r for r in self.db.list_all()
                       if r["path"].startswith(prefix + "/")]
            if not victims:
                QMessageBox.information(self, "提示", f"「{prefix}/」下没有笔记")
                return
            if QMessageBox.question(
                    self, "确认删除",
                    f"删除文件夹「{prefix}/」及其下 {len(victims)} 篇笔记？"
                    "此操作不可撤销。") != QMessageBox.StandardButton.Yes:
                return
            for r in victims:
                nid = int(r["id"])
                self.db.delete(nid)
                info = self._open_notes.pop(nid, None)
                if info:
                    try:
                        info["temp_path"].unlink()
                    except OSError:
                        pass
            self.refresh_tree()
            self.log(f"已删除文件夹: {prefix}/（{len(victims)} 篇）")
            return
        note_id = self._selected_note_id()
        row = self.db.get_note(note_id)
        if row is None:
            return
        if QMessageBox.question(self, "确认删除",
                                f"删除笔记「{row['path']}」？此操作不可撤销。"
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.db.delete(note_id)
        info = self._open_notes.pop(note_id, None)
        if info:
            try:
                info["temp_path"].unlink()
            except OSError:
                pass
        self.refresh_tree()
        self.log(f"已删除: {row['path']}")

    # ── db 侧：导入 / 导出 ──

    def import_folder(self):
        db = self._require_db()
        if db is None:
            return
        src = QFileDialog.getExistingDirectory(self, "选择要导入的文件夹（含 .md）")
        if not src:
            return
        src_root = Path(src)
        self.log(f"开始导入: {src_root}")
        start_worker(
            nb._import_folder_job, db=db, src_root=src_root,
            on_log=self.log,
            on_finished=self._on_import_finished,
            on_error=lambda e: self.log(f"导入失败: {e}", "ERROR"),
        )

    def _on_import_finished(self, result):
        if self.mode == "db":
            self.refresh_tree()
        if result:
            self.log(f"导入完成: 新增 {result['inserted']}，覆盖 {result['replaced']}，共 {result['total']} 个文件")

    def export_folder(self):
        db = self._require_db()
        if db is None:
            return
        dst = QFileDialog.getExistingDirectory(self, "选择导出目标文件夹")
        if not dst:
            return
        dst_root = Path(dst)
        dst_root.mkdir(parents=True, exist_ok=True)
        start_worker(
            nb._export_folder_job, db=db, dst_root=dst_root,
            on_log=self.log,
            on_finished=lambda n: self.log(f"导出完成: {n} 篇笔记 → {dst_root}"),
            on_error=lambda e: self.log(f"导出失败: {e}", "ERROR"),
        )

    # ── lifecycle ──

    def shutdown(self):
        # Stop any pending debounce timers.
        for info in self._open_notes.values():
            if info.get("timer") is not None:
                try:
                    self.killTimer(info["timer"])
                except Exception:
                    pass
                info["timer"] = None
        self._flush_all_open()
        self._db_stop_watchdog()
        self._set_media_meta_override(None)
        # Best-effort temp cleanup.
        try:
            for f in self.temp_dir.glob("*"):
                try:
                    f.unlink()
                except Exception:
                    pass
        except Exception:
            pass
        if self.db is not None:
            self.db.close()
            self.db = None

    def _flush_all_open(self):
        db = self.db
        if db is None:
            return
        for note_id, info in list(self._open_notes.items()):
            tp = info["temp_path"]
            try:
                body = tp.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            new_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if new_hash != info["last_hash"]:
                try:
                    db.update_body(note_id, body)
                    info["last_hash"] = new_hash
                    self.log(f"退出前已保存: {info['name']}")
                except Exception as e:
                    self.log(f"退出前保存失败: {e}", "ERROR")
