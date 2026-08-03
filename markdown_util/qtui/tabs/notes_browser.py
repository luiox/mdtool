"""Notes library tab — PySide6 port of ``tabs/notes_browser.py``.

Same storage/backend as the tkinter version (the :mod:`server.notes_db` layer
is untouched), but the UI is rebuilt with Qt widgets and the watchdog
write-back is simplified: the watchdog observer runs on its own thread and
emits a Qt signal, which Qt auto-delivers to the GUI thread — replacing the
hand-rolled "pending set + after() poller" from the tkinter version.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
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

from qtui.icons import file_icon, folder_icon
from qtui.widgets import BaseTab, LogPanel
from qtui.workers import start_worker
from server.notes_db import NotesDB

CONFIG_FILE = Path.home() / ".mdtool_notes.json"
DEFAULT_CONFIG = {
    "db_path": str(Path.home() / ".mdtool" / "notes.db"),
    "editor_command": "",  # empty → os.startfile (system default for .md)
}
TEMP_SUBDIR = "mdtool_edit"
_DEBOUNCE_MS = 600


def _load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def _save_config(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class _WatchdogWorker(QObject):
    """Runs the watchdog observer on a dedicated QThread.

    The observer dispatches events on the watchdog thread; we only ``emit``
    a signal from there (never touch Qt widgets or sqlite directly). Qt
    cross-thread signal delivery moves the slot call to the GUI thread, which
    is what makes this safe and far simpler than the tkinter polling scheme.
    """

    path_changed = Signal(str)  # emits absolute temp file path

    def __init__(self, temp_dir: Path):
        super().__init__()
        self.temp_dir = temp_dir
        self._observer = None

    def start(self):
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except Exception as e:  # pragma: no cover - import guard
            print(f"[notes] watchdog unavailable: {e}", file=sys.stderr)
            return

        outer = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if not event.is_directory:
                    outer.path_changed.emit(event.src_path)

            def on_created(self, event):
                if not event.is_directory:
                    outer.path_changed.emit(event.src_path)

        self._observer = Observer()
        self._observer.schedule(_Handler(), str(self.temp_dir), recursive=False)
        self._observer.daemon = True
        self._observer.start()

    def stop(self):
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:
                pass
            self._observer = None


class NotesBrowserTab(BaseTab):
    """File-tree + regex-search browser over a SQLite notes container."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.config = _load_config()
        self.db: Optional[NotesDB] = None
        self._open_notes: dict[int, dict] = {}  # note_id -> {temp_path, last_hash, name, timer_id}
        self.temp_dir = Path(tempfile.gettempdir()) / TEMP_SUBDIR
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self._watchdog_thread: Optional[QThread] = None
        self._watchdog_worker: Optional[_WatchdogWorker] = None

        self._build_ui()

        try:
            self._open_library(Path(self.config["db_path"]))
        except Exception:
            pass

    # ── UI ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Toolbar
        tb = QWidget()
        h = tb.layout() if tb.layout() else None
        from PySide6.QtWidgets import QHBoxLayout
        h = QHBoxLayout(tb)
        h.setContentsMargins(0, 0, 0, 0)
        for text, slot in [
            ("打开/新建笔记库", self.choose_library),
            ("导入文件夹", self.import_folder),
            ("导出为文件夹", self.export_folder),
            ("新建笔记", self.new_note),
            ("刷新", self.refresh_tree),
        ]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            h.addWidget(b)
        h.addStretch(1)
        self.db_label = QLabel("未打开")
        self.db_label.setStyleSheet("color: gray;")
        h.addWidget(self.db_label)
        root.addWidget(tb)

        # Search bar
        from PySide6.QtWidgets import QHBoxLayout as _HBL
        sbar = QWidget()
        sh = _HBL(sbar)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.returnPressed.connect(self.do_search)
        sh.addWidget(self.search_edit, 1)
        sh.addWidget(QLabel("范围:"))
        self.scope_box = QComboBox()
        self.scope_box.addItems(["文件名", "正文", "文件名+正文"])
        sh.addWidget(self.scope_box)
        b = QPushButton("搜索"); b.clicked.connect(self.do_search); sh.addWidget(b)
        b = QPushButton("清空"); b.clicked.connect(self.clear_search); sh.addWidget(b)
        root.addWidget(sbar)

        # Splitter: tree (left) + results (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("笔记")
        self.tree.setIconSize(__import__("PySide6.QtCore", fromlist=["QSize"]).QSize(16, 16))
        self.tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        splitter.addWidget(self.tree)

        self.results = QTableWidget(0, 4)
        self.results.setHorizontalHeaderLabels(["路径", "标题", "修改时间", "id"])
        self.results.setColumnHidden(3, True)  # hidden id carrier
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.cellDoubleClicked.connect(self._on_result_double_click)
        self.results.horizontalHeader().setStretchLastSection(False)
        self.results.setColumnWidth(0, 260)
        self.results.setColumnWidth(1, 200)
        splitter.addWidget(self.results)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # Log
        self.log_panel = LogPanel(height_lines=6)
        root.addWidget(self.log_panel)

    def log(self, msg: str, level: str = "INFO"):
        self.log_panel.append_line(msg, level)

    # ── library management ──

    def choose_library(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout as _HBL
        dlg = QDialog(self)
        dlg.setWindowTitle("选择/新建笔记库")
        form = QFormLayout(dlg)
        self._dlg_db = QLineEdit(self.config["db_path"])
        browse = QPushButton("浏览…")
        browse.clicked.connect(lambda: self._browse_db(self._dlg_db))
        dbrow = QWidget(); _HBL(dbrow).addWidget(self._dlg_db); _HBL(dbrow).addWidget(browse)
        form.addRow("数据库文件 (.db):", dbrow)
        self._dlg_editor = QLineEdit(self.config["editor_command"])
        form.addRow("编辑器命令 (空=系统默认):", self._dlg_editor)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.config["db_path"] = self._dlg_db.text().strip() or DEFAULT_CONFIG["db_path"]
            self.config["editor_command"] = self._dlg_editor.text().strip()
            _save_config(self.config)
            self._open_library(Path(self.config["db_path"]))

    def _browse_db(self, line: QLineEdit):
        p, _ = QFileDialog.getSaveFileName(
            self, "选择已有 .db 或输入新文件名", "", "SQLite 数据库 (*.db);;所有文件 (*.*)")
        if p:
            line.setText(p)

    def _open_library(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = NotesDB(db_path)
        self.db_label.setText(str(db_path))
        self.db_label.setStyleSheet("color: black;")
        self.log(f"已打开笔记库: {db_path}")
        if self._watchdog_thread is None:
            self._start_watchdog()
        self.refresh_tree()

    def _start_watchdog(self):
        self._watchdog_thread = QThread()
        self._watchdog_worker = _WatchdogWorker(self.temp_dir)
        self._watchdog_worker.moveToThread(self._watchdog_thread)
        self._watchdog_thread.started.connect(self._watchdog_worker.start)
        self._watchdog_worker.path_changed.connect(self._on_temp_changed)
        self._watchdog_thread.start()

    def _require_db(self) -> Optional[NotesDB]:
        if self.db is None:
            QMessageBox.warning(self, "提示", "请先打开或新建一个笔记库")
            return None
        return self.db

    # ── tree ──

    def refresh_tree(self):
        self.tree.clear()
        db = self.db
        if db is None:
            return
        nodes: dict[str, QTreeWidgetItem] = {}

        def ensure_folder(folder_path: str) -> QTreeWidgetItem:
            if folder_path in nodes:
                return nodes[folder_path]
            if folder_path == "":
                return self.tree.invisibleRootItem()
            parent_path = folder_path.rsplit("/", 1)[0] if "/" in folder_path else ""
            parent = ensure_folder(parent_path)
            name = folder_path.rsplit("/", 1)[-1]
            item = QTreeWidgetItem(parent, [name])
            item.setIcon(0, folder_icon())
            item.setExpanded(True)
            nodes[folder_path] = item
            return item

        for row in db.list_all():
            path = row["path"]
            folder = path.rsplit("/", 1)[0] if "/" in path else ""
            parent = ensure_folder(folder) if folder else self.tree.invisibleRootItem()
            item = QTreeWidgetItem(parent, [row["name"]])
            item.setIcon(0, file_icon())
            item.setData(0, Qt.ItemDataRole.UserRole, int(row["id"]))
            item.setToolTip(0, path)

    def _selected_note_id(self) -> Optional[int]:
        item = self.tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return int(data) if data is not None else None

    def _selected_folder_path(self) -> str:
        item = self.tree.currentItem()
        if item is None:
            return ""
        # note nodes carry a UserRole id; folder nodes don't.
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            parent = item.parent()
            # Reconstruct folder path from the tree walk to root.
            return self._folder_iid_of(parent) if parent is not None else ""
        return self._folder_iid_of(item)

    def _folder_iid_of(self, item: Optional[QTreeWidgetItem]) -> str:
        """Reconstruct a folder's logical path by walking up the tree."""
        if item is None:
            return ""
        parts = []
        cur = item
        while cur is not None and cur is not self.tree.invisibleRootItem():
            parts.append(cur.text(0))
            cur = cur.parent()
        return "/".join(reversed(parts))

    # ── tree interactions ──

    def _on_tree_double_click(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is not None:
            self._open_note_for_edit(int(data))

    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        is_note = item.data(0, Qt.ItemDataRole.UserRole) is not None
        if is_note:
            menu.addAction("打开（编辑）", self.open_selected)
            menu.addAction("重命名/移动", self.rename_selected)
            menu.addSeparator()
            menu.addAction("删除", self.delete_selected)
        else:
            menu.addAction("在此新建笔记", self.new_note_in_folder)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def open_selected(self):
        nid = self._selected_note_id()
        if nid is not None:
            self._open_note_for_edit(nid)

    def _on_result_double_click(self, row, _col):
        try:
            nid = int(self.results.item(row, 3).text())
        except (ValueError, AttributeError):
            return
        self._open_note_for_edit(nid)

    # ── editing ──

    def _open_note_for_edit(self, note_id: int):
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
            try:
                subprocess.Popen(cmd, shell=True)
                return
            except Exception as e:
                QMessageBox.critical(self, "编辑器启动失败", f"{e}\n回退到系统默认程序。")
        try:
            os.startfile(str(temp_path))  # Windows
        except AttributeError:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(temp_path)])

    def _on_temp_changed(self, src_path: str):
        """Slot for the watchdog signal — runs on the GUI thread."""
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
        info["timer"] = self.startTimer(_DEBOUNCE_MS)

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

    # ── note CRUD ──

    def new_note(self):
        self._new_note_at("")

    def new_note_in_folder(self):
        self._new_note_at(self._selected_folder_path())

    def _new_note_at(self, folder: str):
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
        self._open_note_for_edit(int(nid))

    def rename_selected(self):
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

    def delete_selected(self):
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

    # ── search ──

    def do_search(self):
        db = self._require_db()
        if db is None:
            return
        pattern = self.search_edit.text()
        scope_map = {"文件名": "name", "正文": "body", "文件名+正文": "both"}
        scope = scope_map.get(self.scope_box.currentText(), "name")
        self.results.setRowCount(0)
        if not pattern:
            self.log("搜索内容为空")
            return
        try:
            rows = db.search(pattern, scope)
        except Exception as e:
            QMessageBox.critical(self, "搜索错误", f"正则无效或查询失败: {e}")
            return
        for r in rows:
            mtime = r["mtime"]
            try:
                mtime = datetime.fromisoformat(mtime.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            rc = self.results.rowCount()
            self.results.insertRow(rc)
            self.results.setItem(rc, 0, QTableWidgetItem(r["path"]))
            self.results.setItem(rc, 1, QTableWidgetItem(r["title"] or ""))
            self.results.setItem(rc, 2, QTableWidgetItem(str(mtime)))
            self.results.setItem(rc, 3, QTableWidgetItem(str(r["id"])))
        self.log(f"搜索「{pattern}」(范围={self.scope_box.currentText()}) → {len(rows)} 条")

    def clear_search(self):
        self.results.setRowCount(0)
        self.search_edit.clear()

    # ── import / export (worker-backed) ──

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
            _import_folder_job, db=db, src_root=src_root,
            on_log=lambda m, l: self.log(m, l),
            on_finished=self._on_import_finished,
            on_error=lambda e: self.log(f"导入失败: {e}", "ERROR"),
        )

    def _on_import_finished(self, result):
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
            _export_folder_job, db=db, dst_root=dst_root,
            on_log=lambda m, l: self.log(m, l),
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
        # Stop watchdog thread.
        if self._watchdog_worker is not None:
            QThread  # ensure imported
            # Invoke stop on the worker thread via signal-less direct call;
            # the observer's stop/join is safe to call from here (it's just
            # threading primitives, no Qt).
            self._watchdog_worker.stop()
        if self._watchdog_thread is not None:
            self._watchdog_thread.quit()
            self._watchdog_thread.wait(2000)
            self._watchdog_thread = None
            self._watchdog_worker = None
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


# ── module-level worker functions (must be top-level for QRunnable pickling-ish use) ──

def _import_folder_job(db: NotesDB, src_root: Path, report) -> dict:
    md_files = sorted(src_root.rglob("*.md"))
    inserted = replaced = 0
    for i, md in enumerate(md_files):
        rel = md.relative_to(src_root).as_posix()
        try:
            body = md.read_text(encoding="utf-8")
        except Exception as e:
            report("log", msg=f"跳过（读取失败）: {rel} — {e}", level="WARNING")
            continue
        existed = db.get_note_by_path(rel) is not None
        try:
            db.upsert_note(rel, body)
        except Exception as e:
            report("log", msg=f"跳过（写入失败）: {rel} — {e}", level="ERROR")
            continue
        if existed:
            replaced += 1
        else:
            inserted += 1
        report("progress", current=i + 1, total=len(md_files))
    return {"inserted": inserted, "replaced": replaced, "total": len(md_files)}


def _export_folder_job(db: NotesDB, dst_root: Path, report) -> int:
    rows = db.list_all(include_body=True)
    n = 0
    for i, row in enumerate(rows):
        target = dst_root / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(row["body"], encoding="utf-8")
        n += 1
        report("progress", current=i + 1, total=len(rows))
    return n
