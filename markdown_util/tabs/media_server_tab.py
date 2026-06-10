import os
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from server.media_server import MediaServer

CONFIG_FILE = Path.home() / ".monocodes_media.json"
DEFAULT_CONFIG = {
    "media_root": "",
    "images_subdir": "images",
    "assets_subdir": "assets",
    "port": 8765,
    "host": "127.0.0.1",
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


class MediaServerTab:
    def __init__(self, notebook, app):
        self.app = app
        self.frame = ttk.Frame(notebook)
        self.config = load_config()
        self.server = MediaServer(
            Path(self.config["media_root"] or "."),
            host=self.config["host"],
            port=self.config["port"],
            log_callback=self.log,
        )
        self.create_widgets()

    def create_widgets(self):
        # ── Status ──
        sf = ttk.LabelFrame(self.frame, text="服务器状态", padding=5)
        sf.pack(fill=tk.X, padx=5, pady=5)

        self.status_label = ttk.Label(sf, text="● 已停止", foreground="red", font=("", 11))
        self.status_label.pack(anchor=tk.W)

        url_info = ttk.Frame(sf)
        url_info.pack(fill=tk.X, pady=2)
        self.url_label = ttk.Label(url_info, text="地址: -", foreground="gray")
        self.url_label.pack(side=tk.LEFT)
        ttk.Label(url_info, text="   图片: /images/  |  附件: /assets/", foreground="gray").pack(side=tk.LEFT, padx=20)

        btn_row = ttk.Frame(sf)
        btn_row.pack(anchor=tk.W, pady=3)
        self.toggle_btn = ttk.Button(btn_row, text="启动服务器", command=self.toggle_server)
        self.toggle_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="在浏览器中打开",
                   command=self.open_browser).pack(side=tk.LEFT, padx=2)

        # ── Config ──
        cf = ttk.LabelFrame(self.frame, text="配置", padding=5)
        cf.pack(fill=tk.X, padx=5, pady=5)

        r1 = ttk.Frame(cf)
        r1.pack(fill=tk.X, pady=2)
        ttk.Label(r1, text="媒体根目录:", width=14).pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value=self.config.get("media_root", ""))
        self.dir_entry = ttk.Entry(r1, textvariable=self.dir_var)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(r1, text="浏览", command=self.browse_dir).pack(side=tk.LEFT)

        r1b = ttk.Frame(cf)
        r1b.pack(fill=tk.X, pady=2)
        ttk.Label(r1b, text="图片子目录:", width=14).pack(side=tk.LEFT)
        self.img_dir_var = tk.StringVar(value=self.config.get("images_subdir", "images"))
        self.img_dir_entry = ttk.Entry(r1b, textvariable=self.img_dir_var)
        self.img_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Label(r1b, text="附件子目录:", width=10).pack(side=tk.LEFT)
        self.ast_dir_var = tk.StringVar(value=self.config.get("assets_subdir", "assets"))
        self.ast_dir_entry = ttk.Entry(r1b, textvariable=self.ast_dir_var)
        self.ast_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        r2 = ttk.Frame(cf)
        r2.pack(fill=tk.X, pady=2)
        ttk.Label(r2, text="端口:", width=14).pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=str(self.config.get("port", 8765)))
        ttk.Entry(r2, textvariable=self.port_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(r2, text="主机:").pack(side=tk.LEFT, padx=(15, 0))
        self.host_var = tk.StringVar(value=self.config.get("host", "127.0.0.1"))
        ttk.Entry(r2, textvariable=self.host_var, width=18).pack(side=tk.LEFT, padx=5)
        ttk.Label(r2, text="(建议 127.0.0.1 仅本地)", foreground="gray").pack(side=tk.LEFT, padx=5)

        btn2 = ttk.Frame(cf)
        btn2.pack(anchor=tk.W, pady=3)
        ttk.Button(btn2, text="保存配置", command=self.save_config).pack(side=tk.LEFT, padx=2)

        # ── Upload tab ──
        up = ttk.LabelFrame(self.frame, text="快速上传", padding=5)
        up.pack(fill=tk.X, padx=5, pady=5)

        urow1 = ttk.Frame(up)
        urow1.pack(fill=tk.X, pady=2)
        ttk.Label(urow1, text="选择文件:").pack(side=tk.LEFT)
        self._upload_path_var = tk.StringVar()
        ttk.Entry(urow1, textvariable=self._upload_path_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(urow1, text="浏览文件", command=self._browse_file).pack(side=tk.LEFT, padx=2)

        urow2 = ttk.Frame(up)
        urow2.pack(fill=tk.X, pady=2)
        self._upload_type = tk.StringVar(value="image")
        ttk.Radiobutton(urow2, text="上传为图片 (images/)", variable=self._upload_type,
                        value="image").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(urow2, text="上传为附件 (assets/)", variable=self._upload_type,
                        value="asset").pack(side=tk.LEFT, padx=5)
        ttk.Button(urow2, text="上传", command=self._do_upload).pack(side=tk.LEFT, padx=10)
        self._upload_result_var = tk.StringVar()
        ttk.Entry(urow2, textvariable=self._upload_result_var, width=60,
                  foreground="blue").pack(side=tk.LEFT, padx=5)

        # ── Query ──
        qf = ttk.LabelFrame(self.frame, text="查询元信息", padding=5)
        qf.pack(fill=tk.X, padx=5, pady=5)

        qrow = ttk.Frame(qf)
        qrow.pack(fill=tk.X, pady=2)
        ttk.Label(qrow, text="搜索(支持正则):").pack(side=tk.LEFT)
        self._query_var = tk.StringVar()
        ttk.Entry(qrow, textvariable=self._query_var, width=30).pack(side=tk.LEFT, padx=5)
        ttk.Button(qrow, text="搜索图片", command=lambda: self._do_query("images")).pack(side=tk.LEFT, padx=2)
        ttk.Button(qrow, text="搜索附件", command=lambda: self._do_query("assets")).pack(side=tk.LEFT, padx=2)

        # ── Log ──
        lf = ttk.LabelFrame(self.frame, text="日志", padding=5)
        lf.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        log_bar = ttk.Frame(lf)
        log_bar.pack(fill=tk.X)
        ttk.Button(log_bar, text="清空日志", command=self.clear_log).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_bar, text="查询结果", command=self._show_query_result).pack(side=tk.LEFT, padx=2)

        # ── Maintenance ──
        mf = ttk.LabelFrame(self.frame, text="维护", padding=5)
        mf.pack(fill=tk.X, padx=5, pady=5)

        mrow = ttk.Frame(mf)
        mrow.pack(fill=tk.X, pady=2)
        ttk.Label(mrow, text="垃圾回收:").pack(side=tk.LEFT)
        ttk.Button(mrow, text="GC Images", command=self._gc_images).pack(side=tk.LEFT, padx=5)
        ttk.Button(mrow, text="GC Assets", command=self._gc_assets).pack(side=tk.LEFT, padx=5)
        ttk.Button(mrow, text="GC All", command=self._gc_all).pack(side=tk.LEFT, padx=5)
        ttk.Label(mrow, text="(删除数据库中已丢失文件的记录)", foreground="gray").pack(side=tk.LEFT, padx=10)

        self.log_text = tk.Text(lf, height=8, state=tk.DISABLED, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=3)

        self._query_result: list = []

    # ── GC ──

    def _gc_images(self):
        self._do_gc("images")

    def _gc_assets(self):
        self._do_gc("assets")

    def _gc_all(self):
        self._do_gc("all")

    def _do_gc(self, which: str):
        if not self.server.running:
            messagebox.showwarning("警告", "请先启动服务器")
            return
        if not messagebox.askyesno("确认", f"将扫描数据库中所有{'图片和附件' if which == 'all' else which}记录，\n删除对应文件已丢失的条目。\n是否继续?"):
            return
        self.log("正在执行垃圾回收...")
        if which in ("images", "all"):
            purged = self.server.meta_db.gc_images(self.server.images_dir)
            for row in purged:
                self.log(f"  [images] 删除记录: {row['timestamp_name']} ({row['original_name']})")
            self.log(f"  images: 清理 {len(purged)} 条")
        if which in ("assets", "all"):
            purged = self.server.meta_db.gc_assets(self.server.assets_dir)
            for row in purged:
                self.log(f"  [assets] 删除记录: {row['timestamp_name']} ({row['original_name']})")
            self.log(f"  assets: 清理 {len(purged)} 条")
        self.log("垃圾回收完成")

    # ── helpers ──

    def on_root_dir_changed(self):
        pass

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

    def _show_query_result(self):
        if not self._query_result:
            self.log("(无查询结果)")
            return
        for row in self._query_result[:20]:
            self.log(f"  {row['timestamp_name']:40s} | {row['original_name']}")

    def browse_dir(self):
        path = filedialog.askdirectory(title="选择媒体根目录")
        if path:
            self.dir_var.set(path)

    def save_config(self):
        cfg = {"media_root": self.dir_var.get().strip(),
               "images_subdir": self.img_dir_var.get().strip() or "images",
               "assets_subdir": self.ast_dir_var.get().strip() or "assets",
               "port": int(self.port_var.get()),
               "host": self.host_var.get().strip()}
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
        cfg = {"media_root": self.dir_var.get().strip(),
               "images_subdir": self.img_dir_var.get().strip() or "images",
               "assets_subdir": self.ast_dir_var.get().strip() or "assets",
               "port": self.port_var.get().strip(),
               "host": self.host_var.get().strip()}
        if not cfg["media_root"]:
            messagebox.showwarning("警告", "请设置媒体根目录")
            return
        mr = Path(cfg["media_root"])
        if not mr.exists():
            if not messagebox.askyesno("确认", f"目录不存在，是否创建?\n{mr}"):
                return
            mr.mkdir(parents=True, exist_ok=True)
        try:
            cfg["port"] = int(cfg["port"])
        except ValueError:
            messagebox.showwarning("警告", "端口必须是数字")
            return

        self.config = cfg
        self.server = MediaServer(
            mr, host=cfg["host"], port=cfg["port"],
            images_subdir=cfg["images_subdir"],
            assets_subdir=cfg["assets_subdir"],
            log_callback=self.log,
        )
        try:
            self.server.start()
        except OSError as e:
            messagebox.showerror("启动失败", f"端口 {cfg['port']} 可能被占用:\n{e}")
            return

        self.toggle_btn.config(text="停止服务器")
        self.status_label.config(text="● 运行中", foreground="green")
        url = f"http://{cfg['host']}:{cfg['port']}"
        self.url_label.config(text=f"地址: {url}")
        self._set_config_enabled(False)
        self.log(f"服务器已启动: {url}")
        self.log(f"  图片: {url}/images/  →  {self.server.images_dir}")
        self.log(f"  附件: {url}/assets/  →  {self.server.assets_dir}")
        self.log(f"  根目录: {cfg['media_root']}")

    def _set_config_enabled(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        self.dir_entry.config(state=state)
        self.img_dir_entry.config(state=state)
        self.ast_dir_entry.config(state=state)

    def open_browser(self):
        if self.server.running:
            import webbrowser
            webbrowser.open(f"http://{self.config['host']}:{self.config['port']}/")

    # ── upload ──

    def _browse_file(self):
        path = filedialog.askopenfilename(title="选择要上传的文件")
        if path:
            self._upload_path_var.set(path)

    def _do_upload(self):
        path = self._upload_path_var.get().strip()
        if not path or not Path(path).exists():
            messagebox.showwarning("警告", "请选择要上传的文件")
            return
        if not self.server.running:
            messagebox.showwarning("警告", "请先启动服务器")
            return

        import urllib.request
        import uuid

        url = f"http://{self.config['host']}:{self.config['port']}/upload/{'image' if self._upload_type.get() == 'image' else 'asset'}"
        boundary = "----MonocodesUpload" + uuid.uuid4().hex[:8]

        with open(path, "rb") as f:
            file_data = f.read()

        filename = Path(path).name
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

        try:
            req = urllib.request.Request(url, data=body)
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            resp = urllib.request.urlopen(req)
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("success"):
                r = result["results"][0]
                self._upload_result_var.set(r["url"])
                self.log(f"上传成功: {r['filename']} → {r['url']}")
            else:
                self.log(f"上传失败: {result.get('error', '未知错误')}")
        except Exception as e:
            self.log(f"上传错误: {e}")

    # ── query ──

    def _do_query(self, category: str):
        if not self.server.running:
            messagebox.showwarning("警告", "请先启动服务器")
            return
        pattern = self._query_var.get().strip()
        meta = self.server.meta_db
        if category == "images":
            rows = meta.search_images(pattern)
        else:
            rows = meta.search_assets(pattern)

        self._query_result = [dict(r) for r in rows]
        self.log(f"找到 {len(rows)} 条 {category} 记录:")
        for row in self._query_result[:10]:
            self.log(f"  {row['timestamp_name']} ← {row['original_name']} ({row['size']}B)")
        if len(rows) > 10:
            self.log(f"  ... 还有 {len(rows)-10} 条")
