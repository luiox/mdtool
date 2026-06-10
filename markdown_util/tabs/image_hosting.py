import os
import json
import io
import time
import threading
import mimetypes
import urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

CONFIG_FILE = Path.home() / ".monocodes_hosting.json"
DEFAULT_CONFIG = {
    "image_dir": "",
    "port": 8765,
    "host": "127.0.0.1",
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ── Image Naming ─────────────────────────────────────────────────────

_seq = 0

def make_image_filename(base_dir, original_filename="image.png"):
    global _seq
    ext = Path(original_filename).suffix or ".png"
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    _seq += 1
    name = f"image-{ts}{_seq % 1000:03d}{ext}"
    while Path(base_dir, name).exists():
        _seq += 1
        name = f"image-{ts}{_seq % 1000:03d}{ext}"
    return name


# ── HTTP Request Handler ──────────────────────────────────────────────

class ImageHostHandler(BaseHTTPRequestHandler):
    server_version = "MonocodesImageHost/0.1"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/images/"):
            rel = parsed.path[len("/images/"):]
            filename = Path(rel).name
            filepath = Path(self.server.image_dir) / filename
            if filepath.exists() and filepath.is_file():
                ctype, _ = mimetypes.guess_type(filename)
                ctype = ctype or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                with open(filepath, "rb") as f:
                    self.wfile.write(f.read())
                self._log(200)
                return
        self.send_response(404)
        self.end_headers()
        self._log(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/upload":
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                self._send_json(400, {"success": False, "error": "Expected multipart/form-data"})
                self._log(400)
                return

            files = self._parse_multipart()
            if not files:
                self._send_json(400, {"success": False, "error": "No file found"})
                self._log(400)
                return

            results = []
            for field_name, info in files.items():
                body = info["body"]
                original_name = info.get("filename") or "image.png"
                safe_name = make_image_filename(self.server.image_dir, original_name)
                dest = Path(self.server.image_dir) / safe_name
                with open(dest, "wb") as f:
                    f.write(body)
                host, port = self.server.server_address[:2]
                url = f"http://{host}:{port}/images/{safe_name}"
                results.append({"url": url, "filename": safe_name})

            self._send_json(200, {"success": True, "results": results})
            self._log(200)
            return

        self.send_response(404)
        self.end_headers()
        self._log(404)

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
        body = self.rfile.read(length)
        boundary_bytes = boundary.encode("utf-8")
        chunks = body.split(b"--" + boundary_bytes)

        result = {}
        for chunk in chunks:
            chunk = chunk.strip(b"\r\n")
            if not chunk or chunk == b"--":
                continue
            header_end = chunk.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            raw_headers = chunk[:header_end]
            data = chunk[header_end + 4:]

            field_name = None
            filename = None
            for line in raw_headers.decode("utf-8", errors="replace").split("\r\n"):
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

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _log(self, status):
        cb = getattr(self.server, "log_callback", None)
        if cb:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cb(f"[{ts}] {self.command} {self.path} -> {status}")

    def log_message(self, format, *args):
        pass


# ── Server Controller ────────────────────────────────────────────────

class ImageHostingServer:
    def __init__(self, config, log_callback=None):
        self.config = config
        self.log_callback = log_callback
        self.server = None
        self.thread = None

    @property
    def running(self):
        return self.server is not None

    def start(self):
        if self.running:
            return True
        image_dir = Path(self.config["image_dir"])
        image_dir.mkdir(parents=True, exist_ok=True)

        host = self.config.get("host", "127.0.0.1")
        port = int(self.config.get("port", 8765))

        class Handler(ImageHostHandler):
            pass

        self.server = HTTPServer((host, port), Handler)
        self.server.image_dir = str(image_dir)
        self.server.log_callback = self.log_callback
        self.server.timeout = 0.5

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread = None


# ── Tab UI ───────────────────────────────────────────────────────────

class ImageHostingTab:
    def __init__(self, notebook, app):
        self.app = app
        self.frame = ttk.Frame(notebook)
        self.config = load_config()
        self.server = ImageHostingServer(self.config, log_callback=self.log)
        self.create_widgets()

    def create_widgets(self):
        # Status
        sf = ttk.LabelFrame(self.frame, text="服务器状态", padding=5)
        sf.pack(fill=tk.X, padx=5, pady=5)

        self.status_label = ttk.Label(sf, text="● 已停止", foreground="red", font=("", 11, ""))
        self.status_label.pack(anchor=tk.W)
        self.url_label = ttk.Label(sf, text="地址: -", foreground="gray")
        self.url_label.pack(anchor=tk.W, pady=(2, 5))

        btn_row = ttk.Frame(sf)
        btn_row.pack(anchor=tk.W)
        self.toggle_btn = ttk.Button(btn_row, text="启动服务器", command=self.toggle_server)
        self.toggle_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="在浏览器中打开",
                   command=self.open_browser).pack(side=tk.LEFT, padx=2)

        # Config
        cf = ttk.LabelFrame(self.frame, text="配置", padding=5)
        cf.pack(fill=tk.X, padx=5, pady=5)

        r1 = ttk.Frame(cf)
        r1.pack(fill=tk.X, pady=2)
        ttk.Label(r1, text="图片存储路径:", width=14).pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value=self.config.get("image_dir", ""))
        self.dir_entry = ttk.Entry(r1, textvariable=self.dir_var)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(r1, text="浏览", command=self.browse_dir).pack(side=tk.LEFT)

        r2 = ttk.Frame(cf)
        r2.pack(fill=tk.X, pady=2)
        ttk.Label(r2, text="端口:", width=14).pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=str(self.config.get("port", 8765)))
        ttk.Entry(r2, textvariable=self.port_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(r2, text="主机:").pack(side=tk.LEFT, padx=(15, 0))
        self.host_var = tk.StringVar(value=self.config.get("host", "127.0.0.1"))
        ttk.Entry(r2, textvariable=self.host_var, width=18).pack(side=tk.LEFT, padx=5)
        tip = ttk.Label(r2, text="(建议 127.0.0.1 仅本地访问)", foreground="gray")
        tip.pack(side=tk.LEFT, padx=5)

        btn_row2 = ttk.Frame(cf)
        btn_row2.pack(anchor=tk.W, pady=3)
        ttk.Button(btn_row2, text="保存配置", command=self.save_config).pack(side=tk.LEFT, padx=2)

        # Log
        lf = ttk.LabelFrame(self.frame, text="日志", padding=5)
        lf.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        log_toolbar = ttk.Frame(lf)
        log_toolbar.pack(fill=tk.X)
        ttk.Button(log_toolbar, text="清空日志", command=self.clear_log).pack(side=tk.LEFT, padx=2)

        self.log_text = tk.Text(lf, height=12, state=tk.DISABLED, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=3)

    def on_root_dir_changed(self):
        pass

    # ── helpers ──

    def log(self, msg):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.frame.update_idletasks()

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    def browse_dir(self):
        path = filedialog.askdirectory(title="选择图床图片存储目录")
        if path:
            self.dir_var.set(path)

    def save_config(self):
        cfg = {
            "image_dir": self.dir_var.get().strip(),
            "port": int(self.port_var.get()),
            "host": self.host_var.get().strip(),
        }
        save_config(cfg)
        self.config = cfg
        self.log("配置已保存")

    def toggle_server(self):
        if self.server.running:
            self.server.stop()
            self.toggle_btn.config(text="启动服务器")
            self.status_label.config(text="● 已停止", foreground="red")
            self.url_label.config(text="地址: -")
            self._set_config_enabled(True)
            self.log("服务器已停止")
        else:
            self.start_server()

    def start_server(self):
        cfg = {
            "image_dir": self.dir_var.get().strip(),
            "port": self.port_var.get().strip(),
            "host": self.host_var.get().strip(),
        }

        if not cfg["image_dir"]:
            messagebox.showwarning("警告", "请设置图片存储路径")
            return

        img_dir = Path(cfg["image_dir"])
        if not img_dir.exists():
            if not messagebox.askyesno("确认", f"目录不存在，是否创建?\n{img_dir}"):
                return
            img_dir.mkdir(parents=True, exist_ok=True)

        try:
            cfg["port"] = int(cfg["port"])
        except ValueError:
            messagebox.showwarning("警告", "端口必须是数字")
            return

        self.config = cfg
        self.server = ImageHostingServer(cfg, log_callback=self.log)

        try:
            self.server.start()
        except OSError as e:
            messagebox.showerror("启动失败",
                                 f"无法启动服务器，端口 {cfg['port']} 可能被占用:\n{e}")
            return

        self.toggle_btn.config(text="停止服务器")
        self.status_label.config(text="● 运行中", foreground="green")
        url = f"http://{cfg['host']}:{cfg['port']}"
        self.url_label.config(text=f"地址: {url}")
        self._set_config_enabled(False)
        self.log(f"服务器已启动: {url}")
        self.log(f"图片存储: {cfg['image_dir']}")

    def _set_config_enabled(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        self.dir_entry.config(state=state)
        self.port_var.set(self.port_var.get())
        self.host_var.set(self.host_var.get())

    def open_browser(self):
        if self.server.running:
            import webbrowser
            webbrowser.open(f"http://{self.config['host']}:{self.config['port']}/")
