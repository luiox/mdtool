"""SQLite storage for container-mode notes.

A single notes.db file holds all markdown text. Images and assets are NOT
stored here — they continue to live in the existing image-hosting pipeline
(``images/`` + ``assets/`` directories served via ``http://127.0.0.1:8765``),
so markdown bodies reference them as plain ``http://`` URLs and need no
materialization on disk.

Folder structure is implicit: a note's ``path`` uses ``/`` separators and the
tree is reconstructed in Python from path segments. This avoids orphan/empty-
directory bookkeeping and keeps the schema minimal.
"""

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_title(body: str) -> Optional[str]:
    """Return the text of the first ``# heading`` line, or None.

    Skips fenced code blocks so a ``#`` inside ``` is not mistaken for a
    heading. Trailing whitespace is stripped; an empty/whitespace title yields
    None.
    """
    in_fence = False
    fence_marker = ""
    for line in body.splitlines():
        stripped = line.lstrip()
        # Detect opening/closing of a fenced code block (``` or ~~~).
        if stripped[:3] in ("```", "~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        # ATX heading: 1-6 '#' followed by a space (or end of line).
        m = re.match(r"^(#{1,6})(?:\s+(.*?)|\s*)$", stripped)
        if m:
            text = (m.group(2) or "").strip()
            return text or None
    return None


class NotesDB:
    """Connection wrapper for the notes container database.

    Mirrors the style of :class:`server.meta_db.MetaDB`: a single long-lived
    sqlite3 connection opened with ``check_same_thread=False`` so it can be
    touched from the watchdog worker thread as well as the Tk main thread.
    Callers serialize access themselves (Tk is single-threaded for UI work;
    the watchdog handler marshals back to the main thread via ``after``).
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Default rollback journal (DELETE). Avoids WAL sidecar files (-wal/-shm)
        # that complicate "copy the single .db file anywhere" portability.
        self._register_regexp()
        self._init_tables()

    # ── setup ──

    def _register_regexp(self):
        def _regexp(pattern: str, value) -> int:
            if value is None:
                return 0
            try:
                return 1 if re.search(pattern, value) is not None else 0
            except re.error:
                # Fall back to a plain substring match on an invalid pattern
                # so the UI does not blow up on a half-typed regex.
                return 1 if pattern in str(value) else 0

        self._conn.create_function("REGEXP", 2, _regexp)

    def _init_tables(self):
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                path    TEXT NOT NULL UNIQUE,
                name    TEXT NOT NULL,
                title   TEXT,
                body    TEXT NOT NULL DEFAULT '',
                size    INTEGER NOT NULL DEFAULT 0,
                created TEXT NOT NULL,
                mtime   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_notes_path ON notes(path);
            CREATE INDEX IF NOT EXISTS idx_notes_name ON notes(name);
            """
        )
        self._conn.commit()

    # ── normalize ──

    @staticmethod
    def normalize_path(path: str) -> str:
        """Canonicalize a logical path: forward slashes, no leading slash."""
        path = path.replace("\\", "/").strip()
        # Collapse duplicate/leading slashes; strip a leading slash.
        while "//" in path:
            path = path.replace("//", "/")
        path = path.lstrip("/")
        return path

    # ── CRUD ──

    def upsert_note(self, path: str, body: str, title: Optional[str] = None) -> int:
        """Insert or replace a note at ``path``. Returns the note id.

        ``title`` defaults to the first H1 extracted from ``body``. On replace,
        ``created`` is preserved and ``mtime`` is refreshed.
        """
        path = self.normalize_path(path)
        if not path:
            raise ValueError("note path must not be empty")
        name = path.rsplit("/", 1)[-1]
        if title is None:
            title = extract_title(body)
        size = len(body.encode("utf-8"))
        now = _now_iso()
        existing = self.get_note_by_path(path)
        if existing:
            self._conn.execute(
                "UPDATE notes SET name=?, title=?, body=?, size=?, mtime=? WHERE id=?",
                (name, title, body, size, now, existing["id"]),
            )
            self._conn.commit()
            return existing["id"]
        self._conn.execute(
            "INSERT INTO notes (path, name, title, body, size, created, mtime) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (path, name, title, body, size, now, now),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM notes WHERE path=?", (path,)
        ).fetchone()["id"]

    def update_body(self, note_id: int, body: str, title: Optional[str] = None) -> None:
        if title is None:
            title = extract_title(body)
        size = len(body.encode("utf-8"))
        self._conn.execute(
            "UPDATE notes SET body=?, title=?, size=?, mtime=? WHERE id=?",
            (body, title, size, _now_iso(), note_id),
        )
        self._conn.commit()

    def get_note(self, note_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM notes WHERE id=?", (note_id,)
        ).fetchone()

    def get_note_by_path(self, path: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM notes WHERE path=?", (self.normalize_path(path),)
        ).fetchone()

    def rename(self, note_id: int, new_path: str) -> None:
        new_path = self.normalize_path(new_path)
        if not new_path:
            raise ValueError("note path must not be empty")
        name = new_path.rsplit("/", 1)[-1]
        self._conn.execute(
            "UPDATE notes SET path=?, name=?, mtime=? WHERE id=?",
            (new_path, name, _now_iso(), note_id),
        )
        self._conn.commit()

    def delete(self, note_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def list_all(self, include_body: bool = False) -> list:
        """All notes ordered by path — for building the tree and for export.

        ``include_body=False`` returns lightweight rows (no text) which is
        what the tree needs; pass True for export which must write the body.
        """
        cols = "id, path, name, title, mtime, body" if include_body else "id, path, name, title, mtime"
        return self._conn.execute(
            f"SELECT {cols} FROM notes ORDER BY path"
        ).fetchall()

    def search(self, pattern: str, scope: str = "name") -> list:
        """Regex search. ``scope`` ∈ {'name', 'body', 'both'}."""
        pattern = pattern or ""
        cols = []
        if scope in ("name", "both"):
            cols.append("name REGEXP ?")
        if scope in ("body", "both"):
            cols.append("body REGEXP ?")
        if not cols:
            cols = ["name REGEXP ?"]
        where = " OR ".join(cols)
        params = [pattern] * len(cols)
        return self._conn.execute(
            f"SELECT id, path, name, title, mtime FROM notes WHERE {where} "
            "ORDER BY path",
            params,
        ).fetchall()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
