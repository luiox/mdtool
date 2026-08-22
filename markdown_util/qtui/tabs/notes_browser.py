"""db 容器后端层 — 统一「笔记库」页（qtui/tabs/library.py）的 SQLite 源。

本模块不含 UI。历史上它是笔记库 Tab，UI 合并后保留：

- 配置读写（``~/.mdtool_notes.json``：db 路径 / 编辑器命令 / 落地目录）
- :class:`_WatchdogWorker` — 落地目录观察器（独立 QThread，只 emit 信号）
- :func:`_import_folder_job` / :func:`_export_folder_job` — 散装 ⇄ db 导入导出

编辑模型（由 library 页驱动）：笔记落地为临时文件 → 外部编辑器打开；
watchdog 监听改动，600ms 去抖后写回数据库。落地目录可指向 RAM 盘
（内存态模式）。
"""

import json
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from server.notes_db import NotesDB

CONFIG_FILE = Path.home() / ".mdtool_notes.json"
DEFAULT_CONFIG = {
    "db_path": str(Path.home() / ".mdtool" / "notes.db"),
    "editor_command": "",  # empty → os.startfile (system default for .md)
    "temp_root": "",       # 落地目录：空=系统临时目录；可填 RAM 盘路径（内存态模式）
}
TEMP_SUBDIR = "mdtool_edit"
_DEBOUNCE_MS = 600


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def resolve_temp_dir(config: dict) -> Path:
    root = (config.get("temp_root") or "").strip()
    base = Path(root) if root else Path(tempfile.gettempdir())
    d = base / TEMP_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


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


# ── module-level worker jobs (top-level for QRunnable use) ──

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
