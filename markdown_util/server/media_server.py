"""Unified HTTP server serving both /images/ (display) and /assets/ (download)."""

import os
import json
import time
import re
import io
import mimetypes
import urllib.parse
import threading
import logging
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from typing import Optional, Callable

from server.meta_db import MetaDB


class MediaHandler(BaseHTTPRequestHandler):
    server_version = "MonocodesMediaServer/0.1"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # GET /images/image-xxx.png — serve image for browser display
        if path.startswith("/images/"):
            self._serve_file("images", path[8:], as_attachment=False)
            return

        # GET /assets/YYYYMMDDHHMMSSnnn.pdf — serve with Content-Disposition
        if path.startswith("/assets/"):
            self._serve_file("assets", path[8:], as_attachment=True)
            return

        self._send(404, b"Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # POST /upload or /upload/image — multipart upload → images directory
        if path in ("/upload", "/upload/image"):
            self._handle_upload("images")
            return

        # POST /upload/asset  — multipart upload → assets directory
        if path == "/upload/asset":
            self._handle_upload("assets")
            return

        self._send(404, b"Not Found")

    # ── serve ──

    def _serve_file(self, category: str, filename: str, as_attachment: bool):
        filename = Path(filename).name
        dir_key = "images_dir" if category == "images" else "assets_dir"
        base = Path(getattr(self.server, dir_key))
        filepath = base / filename
        if not filepath.exists() or not filepath.is_file():
            self._send(404, b"Not Found")
            self._log_status(404)
            return

        mime, _ = mimetypes.guess_type(filename)
        mime = mime or "application/octet-stream"

        headers = [("Content-Type", mime), ("Cache-Control", "no-cache")]

        if as_attachment:
            meta: MetaDB = self.server.meta_db
            row = meta.get_asset(filename)
            orig_name = row["original_name"] if row else filename
            disp = f'attachment; filename*=UTF-8\'\'{urllib.parse.quote(orig_name)}'
            headers.append(("Content-Disposition", disp))

        self.send_response(200)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        with open(filepath, "rb") as f:
            self.wfile.write(f.read())
        self._log_status(200)

    # ── upload ──

    def _handle_upload(self, category: str):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._send_json(400, {"success": False, "error": "Expected multipart/form-data"})
            self._log_status(400)
            return

        files = self._parse_multipart()
        if not files:
            self._send_json(400, {"success": False, "error": "No file found"})
            self._log_status(400)
            return

        meta: MetaDB = self.server.meta_db
        dir_key = "images_dir" if category == "images" else "assets_dir"
        target_dir = Path(getattr(self.server, dir_key))
        target_dir.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for field_name, info in files.items():
            body = info["body"]
            original_name = info.get("filename") or f"file_{int(time.time()*1000)}"
            safe_name = Path(original_name).name

            if category == "images":
                new_name = self._make_name(original_name, prefix="image-")
                target_path = target_dir / new_name
                url = f"/images/{new_name}"
            else:
                new_name = self._make_name(original_name, prefix="")
                target_path = target_dir / new_name
                url = f"/assets/{new_name}"

            with open(target_path, "wb") as f:
                f.write(body)

            mime, _ = mimetypes.guess_type(original_name)
            mime = mime or "application/octet-stream"
            size = len(body)

            if category == "images":
                meta.add_image(new_name, original_name, size, mime)
            else:
                meta.add_asset(new_name, original_name, size, mime)

            host = self.server.server_address[0]
            port = self.server.server_address[1]
            full_url = f"http://{host}:{port}{url}"
            results.append({"url": full_url, "filename": new_name, "original": original_name})

        self._send_json(200, {"success": True, "results": results})
        self._log_status(200)

    # ── helpers ──

    _seq = 0

    def _make_name(self, original_name: str, prefix: str = "image-") -> str:
        original_name = original_name.split("#")[0].split("?")[0]
        ext = Path(original_name).suffix or ".png"
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        MediaHandler._seq += 1
        name = f"{prefix}{ts}{MediaHandler._seq % 1000:03d}{ext}"
        images_dir = Path(self.server.images_dir)
        assets_dir = Path(self.server.assets_dir)
        while (images_dir / name).exists() or (assets_dir / name).exists():
            MediaHandler._seq += 1
            name = f"{prefix}{ts}{MediaHandler._seq % 1000:03d}{ext}"
        return name

    def _parse_multipart(self):
        ctype = self.headers.get("Content-Type", "")
        boundary = None
        for part in ctype.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[9:]
                if boundary.startswith('"') and boundary.endswith('"'):
                    boundary = boundary[1:-1]
                break
        if not boundary:
            return None

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        bbytes = boundary.encode("utf-8")
        chunks = raw.split(b"--" + bbytes)

        result = {}
        for chunk in chunks:
            chunk = chunk.strip(b"\r\n")
            if not chunk or chunk == b"--":
                continue
            hdr_end = chunk.find(b"\r\n\r\n")
            if hdr_end == -1:
                continue
            raw_hdrs = chunk[:hdr_end]
            data = chunk[hdr_end + 4:]

            field_name = None
            filename = None
            for line in raw_hdrs.decode("utf-8", errors="replace").split("\r\n"):
                lower = line.lower()
                if lower.startswith("content-disposition:"):
                    for token in line.split(";"):
                        token = token.strip()
                        if "=" in token:
                            k, v = token.split("=", 1)
                            v = v.strip('"')
                            if k.strip() == "name":
                                field_name = v
                            elif k.strip() == "filename":
                                filename = v
            if field_name:
                result[field_name] = {"body": data, "filename": filename}
        return result

    def _send(self, status: int, body: bytes, ctype: str = "text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json")

    def _log_status(self, status: int):
        cb = getattr(self.server, "log_callback", None)
        if cb:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cb(f"[{ts}] {self.command} {self.path} -> {status}")

    def log_message(self, format, *args):
        pass


class MediaServer:
    def __init__(self, media_root: Path, host: str = "127.0.0.1",
                 port: int = 8765, log_callback: Optional[Callable] = None,
                 images_subdir: str = "images", assets_subdir: str = "assets"):
        self.media_root = Path(media_root)
        self.host = host
        self.port = port
        self.log_callback = log_callback
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

        # File logger
        log_path = self.media_root / "server.log"
        self._file_logger = logging.getLogger(f"media_{id(self)}")
        self._file_logger.setLevel(logging.INFO)
        self._file_logger.handlers.clear()
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        self._file_logger.addHandler(fh)

        # Resolve custom subdirectories
        def _resolve(subdir: str) -> Path:
            p = Path(subdir)
            return p if p.is_absolute() else self.media_root / subdir

        self.images_dir = _resolve(images_subdir)
        self.assets_dir = _resolve(assets_subdir)

        # Ensure directories
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)

        # Init metadata database (always at media_root)
        self.meta_db = MetaDB(self.media_root / "meta.db")

    @property
    def running(self) -> bool:
        return self._server is not None

    def _log(self, msg: str) -> None:
        self._file_logger.info(msg)
        if self.log_callback:
            self.log_callback(msg)

    def start(self) -> bool:
        if self.running:
            return True

        class Handler(MediaHandler):
            pass

        self._server = HTTPServer((self.host, self.port), Handler)
        self._server.media_root = str(self.media_root)
        self._server.images_dir = str(self.images_dir)
        self._server.assets_dir = str(self.assets_dir)
        self._server.meta_db = self.meta_db
        self._server.log_callback = self.log_callback
        self._server.timeout = 0.5

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._log(f"服务器启动: http://{self.host}:{self.port}")
        return True

    def gc(self) -> dict:
        img_purged = self.meta_db.gc_images(self.images_dir)
        ast_purged = self.meta_db.gc_assets(self.assets_dir)
        self._log(f"GC: 清理 images={len(img_purged)} assets={len(ast_purged)}")
        return {"images": img_purged, "assets": ast_purged}

    def stop(self):
        if self._server:
            srv = self._server
            self._server = None
            self._thread = None
            # Non-blocking shutdown: run in a daemon thread so UI doesn't freeze
            threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()
        self._log("服务器已停止")
