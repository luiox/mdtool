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
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QThread
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qtui.icons import file_icon, folder_icon
from qtui.tabs import file_browser as fb
from qtui.tabs import notes_browser as nb
from qtui.widgets import BaseTab, PathRow, muted_label
from qtui.workers import start_worker


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
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 0, 18, 14)
        root.setSpacing(8)

        # 源切换 + 模式相关动作簇
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
        h.addSpacing(12)

        self.fs_actions = QWidget()
        fa = QHBoxLayout(self.fs_actions)
        fa.setContentsMargins(0, 0, 0, 0)
        fa.setSpacing(6)
        b = QPushButton("打包全库 ZIP")
        b.setProperty("variant", "primary")
        b.clicked.connect(self.pack_all_zip)
        fa.addWidget(b)
        b = QPushButton("刷新")
        b.clicked.connect(self.refresh_tree)
        fa.addWidget(b)
        h.addWidget(self.fs_actions)

        self.db_actions = QWidget()
        da = QHBoxLayout(self.db_actions)
        da.setContentsMargins(0, 0, 0, 0)
        da.setSpacing(6)
        b = QPushButton("打开/新建笔记库")
        b.setProperty("variant", "primary")
        b.clicked.connect(self.choose_library)
        da.addWidget(b)
        for text, slot in [
            ("新建笔记", self.new_note),
            ("导入文件夹", self.import_folder),
            ("导出为文件夹", self.export_folder),
            ("刷新", self.refresh_tree),
        ]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            da.addWidget(b)
        h.addWidget(self.db_actions)

        h.addStretch(1)
        self.src_label = muted_label("")
        h.addWidget(self.src_label)
        root.addWidget(bar)

        # 目录树（搜索已拆分为独立页面，树独占整页）
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("笔记")
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_click)
        root.addWidget(self.tree, 1)

    # ── 模式切换 ──

    def _set_mode(self, mode: str):
        self.mode = mode
        self.btn_fs.setChecked(mode == "fs")
        self.btn_db.setChecked(mode == "db")
        self.fs_actions.setVisible(mode == "fs")
        self.db_actions.setVisible(mode == "db")
        if mode == "fs":
            self.tree.setHeaderLabel("知识库（散装笔记）")
        else:
            self.tree.setHeaderLabel("笔记库（SQLite 容器）")
        self._update_src_label()
        self.refresh_tree()

    def _update_src_label(self):
        """散装模式的路径顶栏已展示，页内不重复；仅 db 模式显示库文件路径。"""
        if self.mode == "fs":
            self.src_label.setVisible(False)
            return
        self.src_label.setVisible(True)
        self.src_label.setText(
            str(self.config["db_path"]) if self.db is not None else "未打开笔记库")

    # ── hooks ──

    def set_root_dir(self, root_dir: Optional[Path]):
        super().set_root_dir(root_dir)
        if self.mode == "fs":
            self.refresh_tree()
        self._update_src_label()

    # ── 树构建 ──

    def refresh_tree(self):
        self.tree.clear()
        if self.mode == "fs":
            self._fs_build_tree()
        else:
            self._db_build_tree()

    def _fs_build_tree(self):
        if not self.root_dir:
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

        for rel, _abs in fb.list_md_tree(self.root_dir):
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
        """当前选中的 .md；目录递归收集。"""
        items = self.tree.selectedItems()
        if not items and self.tree.currentItem() is not None:
            items = [self.tree.currentItem()]
        md_files: list[Path] = []
        for it in items:
            rel = self._fs_item_rel(it)
            if rel is None:
                continue
            p = self.root_dir / rel
            if p.is_dir():
                md_files.extend(p.rglob("*.md"))
            elif p.suffix.lower() == ".md":
                md_files.append(p)
        return sorted(set(md_files))

    # ── 右键菜单 ──

    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        if self.mode == "fs":
            self._fs_menu(menu, item)
        else:
            self._db_menu(menu, item)
        if menu.actions():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _fs_menu(self, menu: QMenu, item: QTreeWidgetItem):
        rel = self._fs_item_rel(item)
        if rel is None:
            return
        p = self.root_dir / rel
        if p.suffix.lower() == ".md":
            menu.addAction("打开（系统默认程序）", lambda: fb.open_external(p))
            menu.addAction("重命名/移动…", self._fs_rename_selected)
            menu.addAction("删除", self._fs_delete_selected)
            menu.addSeparator()
        md_files = self._fs_selected_md_files()
        if md_files:
            label = f"打包为 ZIP（{len(md_files)} 篇）" if len(md_files) > 1 else "打包为 ZIP"
            menu.addAction(label, self.pack_to_zip)
            if len(md_files) == 1 and md_files[0].suffix.lower() == ".md":
                menu.addAction("迁移图片到图床", self.migrate_images)
        menu.addAction("导出为 db 包…", self.export_db)
        menu.addAction("在此新建笔记", self._fs_new_note_here)

    def _db_menu(self, menu: QMenu, item: QTreeWidgetItem):
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            menu.addAction("打开（编辑）", self.open_selected)
            menu.addAction("重命名/移动…", self._db_rename_selected)
            menu.addSeparator()
            menu.addAction("删除", self._db_delete_selected)
        else:
            menu.addAction("在此新建笔记", self.new_note)

    def _on_tree_double_click(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return
        if self.mode == "db":
            self._db_open_note_for_edit(int(data))
        elif self.root_dir:
            fb.open_external(self.root_dir / str(data))

    # ── 散装侧操作 ──

    def _fs_new_note_here(self):
        if not self.root_dir:
            QMessageBox.warning(self, "警告", "请先在顶栏选择知识库根目录")
            return
        folder = self._current_folder_path().replace("/", os.sep)
        name, ok = QInputDialog.getText(self, "新建笔记", "笔记文件名（含 .md）：",
                                        text="未命名.md")
        if not ok or not name:
            return
        target = self.root_dir / folder / name if folder else self.root_dir / name
        if not target.suffix:
            target = target.with_suffix(".md")
        if target.exists():
            QMessageBox.warning(self, "提示", f"文件已存在: {target}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {target.stem}\n", encoding="utf-8")
        self.log(f"新建笔记: {target.relative_to(self.root_dir).as_posix()}")
        self.refresh_tree()
        fb.open_external(target)

    def _fs_rename_selected(self):
        if not self.root_dir:
            return
        rel = self._fs_item_rel(self.tree.currentItem())
        if rel is None or not rel.lower().endswith(".md"):
            QMessageBox.warning(self, "提示", "仅支持重命名 .md 文件")
            return
        new_rel, ok = QInputDialog.getText(
            self, "重命名/移动", "新的相对路径（用 / 分隔文件夹）：", text=rel)
        if not ok or not new_rel or new_rel == rel:
            return
        src, dst = self.root_dir / rel, self.root_dir / new_rel
        if dst.exists():
            QMessageBox.warning(self, "提示", f"目标已存在: {new_rel}")
            return
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        except OSError as e:
            QMessageBox.critical(self, "重命名失败", str(e))
            return
        self.log(f"已重命名: {rel} → {new_rel}")
        self.refresh_tree()

    def _fs_delete_selected(self):
        if not self.root_dir:
            return
        rel = self._fs_item_rel(self.tree.currentItem())
        if rel is None or not rel.lower().endswith(".md"):
            QMessageBox.warning(self, "提示", "仅支持删除 .md 文件")
            return
        if QMessageBox.question(self, "确认删除",
                                f"删除文件「{rel}」？此操作不可撤销。"
                                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            (self.root_dir / rel).unlink()
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
        if not self.root_dir:
            QMessageBox.warning(self, "警告", "请先选择知识库根目录")
            return
        md_files = [p for _, p in fb.list_md_tree(self.root_dir)]
        if not md_files:
            QMessageBox.warning(self, "警告", "知识库根目录下没有 .md 文件")
            return
        self._pack_zip(md_files, base=self.root_dir, default_name="知识库导出.zip")

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

    def export_db(self):
        """散装 → db 包：交给 worker 建库导入。"""
        if not self.root_dir:
            return
        rel = self._fs_item_rel(self.tree.currentItem())
        if rel is None:
            return
        p = self.root_dir / rel
        src_root = p if p.is_dir() else p.parent
        db_path, _ = QFileDialog.getSaveFileName(
            self, "导出为 db 包", "notes.db", "SQLite 数据库 (*.db)")
        if not db_path:
            return
        start_worker(
            fb._export_db_job, db_path=Path(db_path), src_root=src_root,
            on_log=self.log,
            on_finished=lambda r: self.log(
                f"导出完成: 新增 {r['inserted']}，覆盖 {r['replaced']}，共 {r['total']} 篇 → {db_path}"),
            on_error=lambda e: self.log(f"导出失败: {e}", "ERROR"),
        )

    # ── db 侧：库管理 ──

    def choose_library(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("选择/新建笔记库")
        form = QFormLayout(dlg)
        db_row = PathRow(self.config["db_path"], mode="save",
                         dialog_title="选择已有 .db 或输入新文件名",
                         file_filter="SQLite 数据库 (*.db);;所有文件 (*.*)")
        form.addRow("数据库文件 (.db):", db_row)
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
            self.config["db_path"] = db_row.text() or nb.DEFAULT_CONFIG["db_path"]
            self.config["editor_command"] = editor_edit.text().strip()
            nb.save_config(self.config)
            self._db_open_library(Path(self.config["db_path"]))
            self._update_src_label()

    def _db_open_library(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = nb.NotesDB(db_path)
        self.log(f"已打开笔记库: {db_path}")
        if self._watchdog_thread is None:
            self._db_start_watchdog()
        if self.mode == "db":
            self.refresh_tree()

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
        name, ok = QInputDialog.getText(self, "新建笔记", "笔记文件名（含 .md）：",
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

    def _db_rename_selected(self):
        note_id = self._selected_note_id()
        if note_id is None or self.db is None:
            return
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
        note_id = self._selected_note_id()
        if note_id is None or self.db is None:
            return
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
