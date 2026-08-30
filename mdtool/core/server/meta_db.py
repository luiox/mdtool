"""SQLite metadata management for images and assets tables."""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


class MetaDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS images (
                timestamp_name TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                size INTEGER NOT NULL,
                mime TEXT NOT NULL,
                upload_time TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_images_original ON images(original_name);
            CREATE INDEX IF NOT EXISTS idx_images_upload_time ON images(upload_time);

            CREATE TABLE IF NOT EXISTS assets (
                timestamp_name TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                size INTEGER NOT NULL,
                mime TEXT NOT NULL,
                upload_time TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_assets_original ON assets(original_name);
            CREATE INDEX IF NOT EXISTS idx_assets_upload_time ON assets(upload_time);
        """)
        self._conn.commit()

    # ── images ──

    def add_image(self, timestamp_name: str, original_name: str, size: int, mime: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO images VALUES (?, ?, ?, ?, ?)",
            (timestamp_name, original_name, size, mime,
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        self._conn.commit()

    def get_image(self, timestamp_name: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM images WHERE timestamp_name = ?", (timestamp_name,)
        ).fetchone()

    def search_images(self, pattern: str = "") -> list:
        q = "%" + pattern + "%"
        return self._conn.execute(
            "SELECT * FROM images WHERE original_name LIKE ? ORDER BY upload_time DESC", (q,)
        ).fetchall()

    def list_images(self, limit: int = 100) -> list:
        return self._conn.execute(
            "SELECT * FROM images ORDER BY upload_time DESC LIMIT ?", (limit,)
        ).fetchall()

    def delete_image(self, timestamp_name: str) -> bool:
        cur = self._conn.execute("DELETE FROM images WHERE timestamp_name = ?", (timestamp_name,))
        self._conn.commit()
        return cur.rowcount > 0

    # ── assets ──

    def add_asset(self, timestamp_name: str, original_name: str, size: int, mime: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO assets VALUES (?, ?, ?, ?, ?)",
            (timestamp_name, original_name, size, mime,
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        self._conn.commit()

    def get_asset(self, timestamp_name: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM assets WHERE timestamp_name = ?", (timestamp_name,)
        ).fetchone()

    def search_assets(self, pattern: str = "") -> list:
        q = "%" + pattern + "%"
        return self._conn.execute(
            "SELECT * FROM assets WHERE original_name LIKE ? ORDER BY upload_time DESC", (q,)
        ).fetchall()

    def list_assets(self, limit: int = 100) -> list:
        return self._conn.execute(
            "SELECT * FROM assets ORDER BY upload_time DESC LIMIT ?", (limit,)
        ).fetchall()

    def delete_asset(self, timestamp_name: str) -> bool:
        cur = self._conn.execute("DELETE FROM assets WHERE timestamp_name = ?", (timestamp_name,))
        self._conn.commit()
        return cur.rowcount > 0

    def close(self):
        self._conn.close()

    # ── GC ──

    def gc_images(self, images_dir: Path) -> list[dict]:
        """Remove DB records whose file no longer exists on disk.  Returns list of purged rows."""
        purged = []
        for row in self.list_images(limit=999999):
            fp = images_dir / row["timestamp_name"]
            if not fp.exists():
                self.delete_image(row["timestamp_name"])
                purged.append(dict(row))
        return purged

    def gc_assets(self, assets_dir: Path) -> list[dict]:
        purged = []
        for row in self.list_assets(limit=999999):
            fp = assets_dir / row["timestamp_name"]
            if not fp.exists():
                self.delete_asset(row["timestamp_name"])
                purged.append(dict(row))
        return purged
