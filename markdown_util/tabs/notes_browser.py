"""Container-mode notes browser tab.

A self-contained notes library backed by a single SQLite database
(:class:`server.notes_db.NotesDB`), presented as an Obsidian-like file tree
plus regex search. Markdown text lives in the DB; images keep using the
existing ``http://127.0.0.1:8765`` hosting pipeline so they need no
materialization.

Editing flow: double-click a note → its body is written to a temp file → the
configured editor (or the system default for ``.md``) opens it → a watchdog
observer watches the temp dir and writes content changes back to the DB
(debounced, marshalled to the Tk main thread).
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, simpledialog

from server.notes_db import NotesDB

try:
    from tabs.file_browser import _load_icons as _load_fb_icons, _FOLDER_ICON, _FILE_ICON  # noqa: F401
except Exception:
    _load_fb_icons = None


CONFIG_FILE = Path.home() / ".mdtool_notes.json"
DEFAULT_CONFIG = {
    "db_path": str(Path.home() / ".mdtool" / "notes.db"),
    "editor_command": "",  # empty → use os.startfile (system default for .md)
}
TEMP_SUBDIR = "mdtool_edit"  # under the system temp dir
_DEBOUNCE_MS = 600  # watchdog write-back debounce


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


class _TempWatchdog:
    """Watchdog observer wrapper, thread-safe with Tk.

    The watchdog observer dispatches events on its own thread. Touching Tk
    (``root.after``) from that thread raises "main thread is not in main loop",
    so we never call Tk from there. Instead the handler only records the
    changed path in a thread-safe pending set. A single ``after`` poller
    running on the Tk main thread drains that set every ``_POLL_MS`` and
    schedules the debounced writeback — which therefore always runs on the
    main thread, safe to hit sqlite3 and the Tk log.
    """

    _POLL_MS = 100  # how often the main thread checks the pending set

    def __init__(self, temp_dir: Path, root: tk.Misc, callback):
        self.temp_dir = temp_dir
        self.root = root
        self.callback = callback
        self._observer = None
        self._pending: set[str] = set()   # touched by watchdog thread
        self._lock = threading.Lock()
        self._poll_after_id = None
        self._debounce_after_ids: dict[str, str] = {}

    def start(self):
        if self._observer is not None:
            return
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except Exception as e:  # pragma: no cover - import guard
            print(f"[notes] watchdog unavailable: {e}", file=sys.stderr)
            return

        outer = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.is_directory:
                    return
                outer._record(event.src_path)

            def on_created(self, event):
                if event.is_directory:
                    return
                outer._record(event.src_path)

        self._observer = Observer()
        self._observer.schedule(_Handler(), str(self.temp_dir), recursive=False)
        self._observer.daemon = True
        self._observer.start()
        # Start the main-thread poller.
        self._schedule_poll()

    # Called on the watchdog thread — keep it trivial, no Tk, no sqlite.
    def _record(self, src_path: str):
        p = Path(src_path)
        try:
            if not p.is_file():
                return
        except OSError:
            return
        with self._lock:
            self._pending.add(str(p))

    # All methods below run on the Tk main thread.

    def _schedule_poll(self):
        if self._observer is None:
            return  # stopped
        self._poll_after_id = self.root.after(self._POLL_MS, self._poll)

    def _poll(self):
        self._schedule_poll()  # re-arm first so we keep polling
        with self._lock:
            paths = list(self._pending)
            self._pending.clear()
        for key in paths:
            # (Re)start the debounce timer for this path.
            prev = self._debounce_after_ids.pop(key, None)
            if prev is not None:
                try:
                    self.root.after_cancel(prev)
                except Exception:
                    pass
            self._debounce_after_ids[key] = self.root.after(_DEBOUNCE_MS, self._fire, key)

    def _fire(self, key: str):
        self._debounce_after_ids.pop(key, None)
        try:
            self.callback(Path(key))
        except Exception as e:  # pragma: no cover - defensive
            print(f"[notes] writeback error: {e}", file=sys.stderr)

    def stop(self):
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:
                pass
            self._observer = None
        if self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        for after_id in list(self._debounce_after_ids.values()):
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        self._debounce_after_ids.clear()


class NotesBrowserTab:
    """File-tree + regex-search browser over a SQLite notes container.

    Follows the project's tab contract: ``__init__(notebook, app)`` with
    ``self.frame``, a no-op ``on_root_dir_changed`` (this tab is independent of
    the project root), and the ``self.log`` disabled-Text idiom.
    """

    # ── lifecycle ──

    def __init__(self, notebook, app):
        self.app = app
        self.frame = ttk.Frame(notebook)

        self.config = _load_config()
        self.db: NotesDB | None = None
        self._open_notes: dict[int, dict] = {}  # note_id -> {temp_path, last_hash, name}
        self._watchdog: _TempWatchdog | None = None
        self.temp_dir = Path(tempfile.gettempdir()) / TEMP_SUBDIR
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.create_widgets()

        # Auto-open the configured library if it exists.
        try:
            self._open_library(Path(self.config["db_path"]))
        except Exception:
            pass

    def on_root_dir_changed(self):
        """No-op: the notes container is independent of the project root."""
        pass

    def shutdown(self):
        """Flush dirty notes, stop watchdog, clean temp dir. Call on app exit."""
        self._flush_all_open()
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog = None
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

    # ── widgets ──

    def create_widgets(self):
        # Toolbar
        top = ttk.Frame(self.frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(top, text="打开/新建笔记库", command=self.choose_library).pack(side=tk.LEFT, padx=2)
        self.db_label = ttk.Label(top, text="未打开", foreground="gray")
        self.db_label.pack(side=tk.LEFT, padx=10)
        ttk.Button(top, text="导入文件夹", command=self.import_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="导出为文件夹", command=self.export_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="新建笔记", command=self.new_note).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="刷新", command=self.refresh_tree).pack(side=tk.LEFT, padx=2)

        # Search bar
        sbar = ttk.Frame(self.frame)
        sbar.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Label(sbar, text="搜索:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        entry = ttk.Entry(sbar, textvariable=self.search_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        entry.bind("<Return>", lambda e: self.do_search())
        ttk.Label(sbar, text="范围:").pack(side=tk.LEFT, padx=(8, 2))
        self.scope_var = tk.StringVar(value="文件名")
        scope_box = ttk.Combobox(
            sbar, textvariable=self.scope_var, state="readonly", width=12,
            values=["文件名", "正文", "文件名+正文"],
        )
        scope_box.pack(side=tk.LEFT)
        ttk.Button(sbar, text="搜索", command=self.do_search).pack(side=tk.LEFT, padx=4)
        ttk.Button(sbar, text="清空", command=self.clear_search).pack(side=tk.LEFT, padx=2)

        # Main split: tree (left) + search results (right)
        main = ttk.Frame(self.frame)
        main.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left = ttk.LabelFrame(main, text="笔记树", padding=4)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(left, columns=("note_id",), show="tree", height=22)
        self.tree.column("note_id", width=0, stretch=False)
        self.tree.heading("#0", text="笔记")
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ls = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        ls.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=ls.set)
        if _load_fb_icons is not None:
            _load_fb_icons()
            try:
                from tabs import file_browser as fb
                if fb._FOLDER_ICON is not None:
                    self.tree.tag_configure("folder", image=fb._FOLDER_ICON)
                if fb._FILE_ICON is not None:
                    self.tree.tag_configure("file", image=fb._FILE_ICON)
            except Exception:
                pass
        self.tree.bind("<Double-1>", lambda e: self.open_selected())
        self.tree.bind("<Return>", lambda e: self.open_selected())
        self.tree.bind("<Button-3>", self._on_tree_rightclick)

        right = ttk.LabelFrame(main, text="搜索结果（双击打开）", padding=4)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cols = ("path", "title", "mtime", "note_id")
        self.results = ttk.Treeview(right, columns=cols, show="headings", height=22)
        self.results.heading("path", text="路径")
        self.results.heading("title", text="标题")
        self.results.heading("mtime", text="修改时间")
        self.results.column("path", width=260)
        self.results.column("title", width=200)
        self.results.column("mtime", width=150)
        self.results.column("note_id", width=0, stretch=False)  # hidden id carrier
        self.results.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rs = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.results.yview)
        rs.pack(side=tk.RIGHT, fill=tk.Y)
        self.results.configure(yscrollcommand=rs.set)
        self.results.bind("<Double-1>", lambda e: self.open_from_results())

        # Log
        logf = ttk.Frame(self.frame)
        logf.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Label(logf, text="日志:").pack(anchor=tk.W)
        self.log_text = tk.Text(self.frame, height=6, state=tk.DISABLED, font=("Consolas", 9))
        self.log_text.pack(fill=tk.X, padx=5, pady=(0, 5))

        # Context menus
        self.ctx_menu = tk.Menu(self.frame, tearoff=0)

    def log(self, msg, level="INFO"):
        self.log_text.config(state=tk.NORMAL)
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] [{level}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.frame.update_idletasks()

    # ── library management ──

    def choose_library(self):
        dlg = tk.Toplevel(self.frame)
        dlg.title("选择/新建笔记库")
        dlg.geometry("520x150")
        dlg.transient(self.frame)
        dlg.grab_set()
        dlg.resizable(False, False)

        ttk.Label(dlg, text="数据库文件 (.db):").grid(row=0, column=0, padx=8, pady=12, sticky=tk.W)
        var = tk.StringVar(value=self.config["db_path"])
        e = ttk.Entry(dlg, textvariable=var, width=46)
        e.grid(row=0, column=1, columnspan=2, padx=4, pady=12)
        ttk.Button(dlg, text="浏览…", command=lambda: self._browse_db(var)).grid(row=0, column=3, padx=4)

        ttk.Label(dlg, text="编辑器命令 (空=系统默认):").grid(row=1, column=0, padx=8, pady=4, sticky=tk.W)
        ec_var = tk.StringVar(value=self.config["editor_command"])
        ttk.Entry(dlg, textvariable=ec_var, width=46).grid(row=1, column=1, columnspan=2, padx=4, pady=4)

        def ok():
            dlg.destroy()
            self.config["db_path"] = var.get().strip() or DEFAULT_CONFIG["db_path"]
            self.config["editor_command"] = ec_var.get().strip()
            _save_config(self.config)
            self._open_library(Path(self.config["db_path"]))

        ttk.Button(dlg, text="确定", command=ok).grid(row=2, column=1, padx=4, pady=12, sticky=tk.E)
        ttk.Button(dlg, text="取消", command=dlg.destroy).grid(row=2, column=2, padx=4, pady=12, sticky=tk.W)

    def _browse_db(self, var: tk.StringVar):
        p = filedialog.asksaveasfilename(
            title="选择已有 .db 或输入新文件名",
            defaultextension=".db",
            filetypes=[("SQLite 数据库", "*.db"), ("所有文件", "*.*")],
        )
        if p:
            var.set(p)

    def _open_library(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = NotesDB(db_path)
        self.db_label.config(text=str(db_path), foreground="black")
        self.log(f"已打开笔记库: {db_path}")
        # Start watchdog if not yet running.
        if self._watchdog is None:
            self._watchdog = _TempWatchdog(self.temp_dir, self.app.root, self._on_temp_changed)
            self._watchdog.start()
        self.refresh_tree()

    def _require_db(self) -> NotesDB | None:
        if self.db is None:
            messagebox.showwarning("提示", "请先打开或新建一个笔记库")
            return None
        return self.db

    # ── tree building ──

    def refresh_tree(self):
        for c in self.tree.get_children():
            self.tree.delete(c)
        db = self.db
        if db is None:
            return
        rows = db.list_all()
        # Build parent->children structure from path segments.
        # Node iids are folder paths "" (root), "a", "a/b", or note ids.
        nodes = {}  # folder_path -> tree iid

        def ensure_folder(folder_path: str) -> str:
            """Create folder nodes recursively; returns the parent iid."""
            if folder_path in nodes:
                return nodes[folder_path]
            if folder_path == "":
                parent_iid = ""
            else:
                parent = folder_path.rsplit("/", 1)[0] if "/" in folder_path else ""
                parent_iid = ensure_folder(parent)
                name = folder_path.rsplit("/", 1)[-1]
                iid = self.tree.insert(
                    parent_iid, tk.END, iid=folder_path, text=name,
                    open=True, tags=("folder",),
                )
                nodes[folder_path] = iid
            return parent_iid if folder_path == "" else nodes[folder_path]

        for row in rows:
            path = row["path"]
            folder = path.rsplit("/", 1)[0] if "/" in path else ""
            parent_iid = ensure_folder(folder) if folder else ""
            iid = f"note:{row['id']}"
            self.tree.insert(
                parent_iid, tk.END, iid=iid, text=row["name"],
                values=(row["id"],), tags=("file",),
            )

    # ── context menu ──

    def _on_tree_rightclick(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.ctx_menu.delete(0, tk.END)
        is_note = iid.startswith("note:")
        if is_note:
            self.ctx_menu.add_command(label="打开（编辑）", command=self.open_selected)
            self.ctx_menu.add_command(label="重命名/移动", command=self.rename_selected)
            self.ctx_menu.add_separator()
            self.ctx_menu.add_command(label="删除", command=self.delete_selected)
        else:
            self.ctx_menu.add_command(label="在此新建笔记", command=self.new_note_in_folder)
        self.ctx_menu.post(event.x_root, event.y_root)

    def _selected_note_id(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if not iid.startswith("note:"):
            return None
        try:
            return int(iid.split(":", 1)[1])
        except ValueError:
            return None

    def _selected_folder_path(self) -> str:
        sel = self.tree.selection()
        if not sel:
            return ""
        iid = sel[0]
        if iid.startswith("note:"):
            # parent folder of the note
            parent = self.tree.parent(iid)
            return parent  # folder iid is the folder path (or "" for root)
        return iid if iid else ""

    # ── note operations ──

    def open_selected(self):
        note_id = self._selected_note_id()
        if note_id is None:
            return
        self._open_note_for_edit(note_id)

    def open_from_results(self):
        sel = self.results.selection()
        if not sel:
            return
        note_id = self.results.set(sel[0]).get("note_id")
        if not note_id:
            return
        self._open_note_for_edit(int(note_id))

    def _open_note_for_edit(self, note_id: int):
        db = self._require_db()
        if db is None:
            return
        row = db.get_note(note_id)
        if row is None:
            messagebox.showerror("错误", "笔记不存在")
            return
        body = row["body"]
        # temp file: <id>__<safe name>
        safe_name = row["name"].replace("/", "_").replace("\\", "_")
        temp_path = self.temp_dir / f"{note_id}__{safe_name}"
        # If already open, just refocus (don't rewrite — avoid clobbering edits).
        if note_id in self._open_notes:
            existing = self._open_notes[note_id]
            # Force overwrite with latest DB body only if user confirms.
            if existing["last_hash"] != hashlib.sha256(body.encode("utf-8")).hexdigest():
                if not messagebox.askyesno(
                    "重新打开",
                    f"已经打开过「{row['name']}」，数据库内容与当前编辑版本不同。\n是否用数据库内容覆盖编辑文件？",
                ):
                    return
            self._write_temp(temp_path, body)
            existing["last_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
            self._launch_editor(temp_path)
            self.log(f"重新打开: {row['name']}")
            return
        self._write_temp(temp_path, body)
        self._open_notes[note_id] = {
            "temp_path": temp_path,
            "last_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "name": row["name"],
        }
        self._launch_editor(temp_path)
        self.log(f"已打开: {row['name']} → {temp_path}")

    @staticmethod
    def _write_temp(temp_path: Path, body: str):
        temp_path.write_text(body, encoding="utf-8")

    def _launch_editor(self, temp_path: Path):
        cmd_template = self.config.get("editor_command", "").strip()
        if cmd_template:
            # Substitute {file}; shell=False so quote the path for the user.
            cmd = cmd_template.replace("{file}", str(temp_path))
            try:
                subprocess.Popen(cmd, shell=True)
                return
            except Exception as e:
                messagebox.showerror("编辑器启动失败", f"{e}\n回退到系统默认程序。")
        # System default for .md (Typora if registered).
        try:
            os.startfile(str(temp_path))  # Windows
        except AttributeError:
            # Non-Windows fallback.
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(temp_path)])

    def _on_temp_changed(self, temp_path: Path):
        """Watchdog callback (runs on Tk main thread via after)."""
        # Find which note this temp file belongs to.
        note_id = None
        for nid, info in self._open_notes.items():
            try:
                if info["temp_path"].resolve() == temp_path.resolve():
                    note_id = nid
                    break
            except OSError:
                if str(info["temp_path"]) == str(temp_path):
                    note_id = nid
                    break
        if note_id is None:
            return
        db = self.db
        if db is None:
            return
        try:
            body = temp_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        new_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        info = self._open_notes[note_id]
        if new_hash == info["last_hash"]:
            return  # our own write, or no real change
        try:
            db.update_body(note_id, body)
            info["last_hash"] = new_hash
            self.log(f"已自动保存: {info['name']}")
        except Exception as e:
            self.log(f"保存失败: {e}", "ERROR")

    def _flush_all_open(self):
        """Force-write any temp file newer than its last_hash back to DB."""
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

    def new_note(self):
        self._new_note_at("")

    def new_note_in_folder(self):
        self._new_note_at(self._selected_folder_path())

    def _new_note_at(self, folder: str):
        db = self._require_db()
        if db is None:
            return
        name = simpledialog.askstring("新建笔记", "笔记文件名（含 .md）：", parent=self.frame,
                                      initialvalue="未命名.md")
        if not name:
            return
        name = name.replace("\\", "/").strip("/")
        path = f"{folder}/{name}" if folder else name
        if not path.endswith(".md"):
            path += ".md"
        if db.get_note_by_path(path) is not None:
            messagebox.showwarning("提示", f"已存在同名笔记: {path}")
            return
        body = f"# {Path(name).stem}\n"
        nid = db.upsert_note(path, body)
        self.refresh_tree()
        self.log(f"新建笔记: {path}")
        self._open_note_for_edit(int(nid))

    def rename_selected(self):
        note_id = self._selected_note_id()
        if note_id is None:
            return
        db = self._require_db()
        if db is None:
            return
        row = db.get_note(note_id)
        if row is None:
            return
        new_path = simpledialog.askstring(
            "重命名/移动", "新的逻辑路径（用 / 分隔文件夹）：",
            parent=self.frame, initialvalue=row["path"])
        if not new_path:
            return
        new_path = db.normalize_path(new_path)
        if new_path == row["path"]:
            return
        if db.get_note_by_path(new_path) is not None:
            messagebox.showwarning("提示", f"目标路径已存在: {new_path}")
            return
        db.rename(note_id, new_path)
        # Refresh open-notes tracking if open.
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
        if note_id is None:
            return
        db = self._require_db()
        if db is None:
            return
        row = db.get_note(note_id)
        if row is None:
            return
        if not messagebox.askyesno("确认删除", f"删除笔记「{row['path']}」？此操作不可撤销。"):
            return
        db.delete(note_id)
        # Close and remove temp file if open.
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
        pattern = self.search_var.get()
        scope_map = {"文件名": "name", "正文": "body", "文件名+正文": "both"}
        scope = scope_map.get(self.scope_var.get(), "name")
        for c in self.results.get_children():
            self.results.delete(c)
        if not pattern:
            self.log("搜索内容为空")
            return
        try:
            rows = db.search(pattern, scope)
        except Exception as e:
            messagebox.showerror("搜索错误", f"正则无效或查询失败: {e}")
            return
        for r in rows:
            mtime = r["mtime"]
            try:
                mtime = datetime.fromisoformat(mtime.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            iid = self.results.insert(
                "", tk.END,
                values=(r["path"], r["title"] or "", mtime),
            )
            self.results.set(iid, "note_id", r["id"])  # hidden
        self.log(f"搜索「{pattern}」(范围={self.scope_var.get()}) → {len(rows)} 条")

    def clear_search(self):
        for c in self.results.get_children():
            self.results.delete(c)
        self.search_var.set("")

    # ── import / export ──

    def import_folder(self):
        db = self._require_db()
        if db is None:
            return
        src = filedialog.askdirectory(title="选择要导入的文件夹（含 .md）")
        if not src:
            return
        src_root = Path(src)
        md_files = sorted(src_root.rglob("*.md"))
        if not md_files:
            messagebox.showinfo("导入", "未找到任何 .md 文件")
            return
        inserted = replaced = 0
        for md in md_files:
            rel = md.relative_to(src_root)
            rel_posix = rel.as_posix()
            try:
                body = md.read_text(encoding="utf-8")
            except Exception as e:
                self.log(f"跳过（读取失败）: {rel_posix} — {e}", "WARNING")
                continue
            existed = db.get_note_by_path(rel_posix)
            try:
                db.upsert_note(rel_posix, body)
            except Exception as e:
                self.log(f"跳过（写入失败）: {rel_posix} — {e}", "ERROR")
                continue
            if existed:
                replaced += 1
            else:
                inserted += 1
            self.frame.update()
        self.refresh_tree()
        self.log(f"导入完成: 新增 {inserted}，覆盖 {replaced}，共 {len(md_files)} 个文件")

    def export_folder(self):
        db = self._require_db()
        if db is None:
            return
        dst = filedialog.askdirectory(title="选择导出目标文件夹")
        if not dst:
            return
        dst_root = Path(dst)
        dst_root.mkdir(parents=True, exist_ok=True)
        rows = db.list_all(include_body=True)
        n = 0
        for row in rows:
            target = dst_root / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(row["body"], encoding="utf-8")
            n += 1
            self.frame.update()
        self.log(f"导出完成: {n} 篇笔记 → {dst_root}")
