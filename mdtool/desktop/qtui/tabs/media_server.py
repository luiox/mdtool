"""Local media server page — 127.0.0.1:8765 媒体托管（手机端契约端口）。

后端 :class:`server.media_server.MediaServer` 不动；配置仍落在
``~/.monocodes_media.json``（file_browser 的 zip 导出也读它）。
UI 层变化：日志走全局 logbus，服务器状态用 ``[severity]`` 动态属性
表达，不再内联样式。
"""

import json
import uuid
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from mdtool.desktop.qtui.widgets import BaseTab, PathRow, SearchBar, set_severity
from mdtool.core.server.media_server import MediaServer

CONFIG_FILE = Path.home() / ".monocodes_media.json"
DEFAULT_CONFIG = {
    "media_root": "",
    "images_subdir": "images",
    "assets_subdir": "assets",
    "port": 8765,
    "host": "127.0.0.1",
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config_to_disk(config: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


class MediaServerTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = load_config()
        self.server = MediaServer(
            Path(self.config["media_root"] or "."),
            host=self.config["host"],
            port=self.config["port"],
            log_callback=lambda m: self.log(m),
        )
        self._query_result: list = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 0, 18, 14)
        outer.setSpacing(10)
        outer.addStretch(0)

        # ── 状态 ──
        gb_status = QGroupBox("服务器状态")
        sl = QVBoxLayout(gb_status)
        self.status_label = QLabel("● 已停止")
        set_severity(self.status_label, "error")
        sl.addWidget(self.status_label)
        url_row = QHBoxLayout()
        self.url_label = QLabel("地址: -")
        url_row.addWidget(self.url_label)
        url_row.addSpacing(20)
        url_row.addWidget(QLabel("图片: /images/  |  附件: /assets/"))
        url_row.addStretch(1)
        sl.addLayout(url_row)
        btn_row = QHBoxLayout()
        self.toggle_btn = QPushButton("启动服务器")
        self.toggle_btn.setProperty("variant", "primary")
        self.toggle_btn.clicked.connect(self.toggle_server)
        btn_row.addWidget(self.toggle_btn)
        b = QPushButton("在浏览器中打开")
        b.clicked.connect(self.open_browser)
        btn_row.addWidget(b)
        btn_row.addStretch(1)
        sl.addLayout(btn_row)
        outer.addWidget(gb_status)

        # ── 配置 ──
        gb_cfg = QGroupBox("配置")
        cfg_form = QFormLayout(gb_cfg)
        self.dir_row = PathRow(self.config.get("media_root", ""), mode="dir",
                               dialog_title="选择媒体根目录")
        cfg_form.addRow("媒体根目录:", self.dir_row)
        self.img_dir_edit = QLineEdit(self.config.get("images_subdir", "images"))
        self.ast_dir_edit = QLineEdit(self.config.get("assets_subdir", "assets"))
        sub_row = QHBoxLayout()
        sub_row.addWidget(self.img_dir_edit, 1)
        sub_row.addWidget(QLabel("附件子目录:"))
        sub_row.addWidget(self.ast_dir_edit, 1)
        cfg_form.addRow("图片子目录:", _wrap(sub_row))
        self.port_edit = QLineEdit(str(self.config.get("port", 8765)))
        self.port_edit.setMaximumWidth(80)
        self.host_edit = QLineEdit(self.config.get("host", "127.0.0.1"))
        self.host_edit.setMaximumWidth(160)
        ph_row = QHBoxLayout()
        ph_row.addWidget(self.port_edit)
        ph_row.addSpacing(15)
        ph_row.addWidget(QLabel("主机:"))
        ph_row.addWidget(self.host_edit)
        ph_row.addWidget(QLabel("(建议 127.0.0.1 仅本地)"))
        ph_row.addStretch(1)
        cfg_form.addRow("端口:", _wrap(ph_row))
        save_btn = QPushButton("保存配置")
        save_btn.clicked.connect(self.save_config)
        cfg_form.addRow(save_btn)
        outer.addWidget(gb_cfg)

        # ── 快速上传 ──
        gb_up = QGroupBox("快速上传")
        up = QGridLayout(gb_up)
        up.addWidget(QLabel("选择文件:"), 0, 0)
        self.upload_path_edit = QLineEdit()
        up.addWidget(self.upload_path_edit, 0, 1)
        b = QPushButton("浏览文件")
        b.clicked.connect(self._browse_file)
        up.addWidget(b, 0, 2)
        self.rb_image = QRadioButton("上传为图片 (images/)")
        self.rb_asset = QRadioButton("上传为附件 (assets/)")
        self.rb_image.setChecked(True)
        up.addWidget(self.rb_image, 1, 0, 1, 2)
        up.addWidget(self.rb_asset, 1, 2)
        b = QPushButton("上传")
        b.clicked.connect(self._do_upload)
        up.addWidget(b, 2, 0)
        self.upload_result_edit = QLineEdit(readOnly=True)
        up.addWidget(self.upload_result_edit, 2, 1, 1, 2)
        outer.addWidget(gb_up)

        # ── 查询 / 维护 ──
        gb_q = QGroupBox("查询元信息")
        ql = QHBoxLayout(gb_q)
        self.query_bar = SearchBar(scopes=(), show_scope=False, show_clear=False)
        ql.addWidget(self.query_bar, 1)
        b = QPushButton("搜索图片")
        b.clicked.connect(lambda: self._do_query("images"))
        ql.addWidget(b)
        b = QPushButton("搜索附件")
        b.clicked.connect(lambda: self._do_query("assets"))
        ql.addWidget(b)
        outer.addWidget(gb_q)

        gb_m = QGroupBox("维护")
        ml = QHBoxLayout(gb_m)
        ml.addWidget(QLabel("垃圾回收:"))
        b = QPushButton("GC Images")
        b.clicked.connect(lambda: self._do_gc("images"))
        ml.addWidget(b)
        b = QPushButton("GC Assets")
        b.clicked.connect(lambda: self._do_gc("assets"))
        ml.addWidget(b)
        b = QPushButton("GC All")
        b.clicked.connect(lambda: self._do_gc("all"))
        ml.addWidget(b)
        ml.addWidget(QLabel("(删除数据库中已丢失文件的记录)"))
        ml.addStretch(1)
        outer.addWidget(gb_m)

        outer.addStretch(1)

    # ── helpers ──

    def _collect_config(self) -> dict:
        return {
            "media_root": self.dir_row.text(),
            "images_subdir": self.img_dir_edit.text().strip() or "images",
            "assets_subdir": self.ast_dir_edit.text().strip() or "assets",
            "port": self.port_edit.text().strip(),
            "host": self.host_edit.text().strip(),
        }

    def _set_config_enabled(self, enabled: bool):
        self.dir_row.edit.setEnabled(enabled)
        self.img_dir_edit.setEnabled(enabled)
        self.ast_dir_edit.setEnabled(enabled)

    def _set_status(self, running: bool, url: str = "-"):
        self.status_label.setText("● 运行中" if running else "● 已停止")
        set_severity(self.status_label, "success" if running else "error")
        self.url_label.setText(f"地址: {url}")
        self.toggle_btn.setText("停止服务器" if running else "启动服务器")
        self._set_config_enabled(not running)

    def save_config(self):
        cfg = self._collect_config()
        try:
            cfg["port"] = int(cfg["port"])
        except ValueError:
            QMessageBox.warning(self, "警告", "端口必须是数字")
            return
        save_config_to_disk(cfg)
        self.config = cfg
        self.log("配置已保存")

    def toggle_server(self):
        if self.server.running:
            self.server.stop()
            self._set_status(False)
            self.log("服务器已停止")
        else:
            self.start_server()

    def start_server(self):
        cfg = self._collect_config()
        if not cfg["media_root"]:
            QMessageBox.warning(self, "警告", "请设置媒体根目录")
            return
        mr = Path(cfg["media_root"])
        if not mr.exists():
            if QMessageBox.question(self, "确认", f"目录不存在，是否创建?\n{mr}"
                                    ) != QMessageBox.StandardButton.Yes:
                return
            mr.mkdir(parents=True, exist_ok=True)
        try:
            cfg["port"] = int(cfg["port"])
        except ValueError:
            QMessageBox.warning(self, "警告", "端口必须是数字")
            return
        self.config = cfg
        self.server = MediaServer(
            mr, host=cfg["host"], port=cfg["port"],
            images_subdir=cfg["images_subdir"], assets_subdir=cfg["assets_subdir"],
            log_callback=lambda m: self.log(m),
        )
        try:
            self.server.start()
        except OSError as e:
            QMessageBox.critical(self, "启动失败", f"端口 {cfg['port']} 可能被占用:\n{e}")
            return
        url = f"http://{cfg['host']}:{cfg['port']}"
        self._set_status(True, url)
        self.log(f"服务器已启动: {url}")
        self.log(f"  图片: {url}/images/  →  {self.server.images_dir}")
        self.log(f"  附件: {url}/assets/  →  {self.server.assets_dir}")
        self.log(f"  根目录: {cfg['media_root']}")

    def open_browser(self):
        if self.server.running:
            import webbrowser
            webbrowser.open(f"http://{self.config['host']}:{self.config['port']}/")

    def set_meta_override(self, meta_path=None) -> bool:
        """db 模式激活期间把元数据源切到指定 sqlite（如 notes.db，规范 §4.1）；
        ``None`` 还原为媒体根下的 meta.db。处理器每次请求都读
        ``self.server.meta_db``，切换即时生效；服务器未运行时静默跳过。
        """
        if not getattr(self.server, "running", False):
            return False
        from mdtool.core.server.meta_db import MetaDB
        base = Path(meta_path) if meta_path else Path(self.config["media_root"]) / "meta.db"
        try:
            self.server.meta_db = MetaDB(base)
        except Exception as e:
            self.log(f"切换元数据源失败: {e}", "ERROR")
            return False
        self.log(f"元数据源: {base}")
        return True

    # ── upload ──

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择要上传的文件")
        if path:
            self.upload_path_edit.setText(path)

    def _do_upload(self):
        path = self.upload_path_edit.text().strip()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "警告", "请选择要上传的文件")
            return
        if not self.server.running:
            QMessageBox.warning(self, "警告", "请先启动服务器")
            return
        import urllib.request
        url = f"http://{self.config['host']}:{self.config['port']}/upload/" + (
            "image" if self.rb_image.isChecked() else "asset")
        boundary = "----MonocodesUpload" + uuid.uuid4().hex[:8]
        file_data = Path(path).read_bytes()
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
                self.upload_result_edit.setText(r["url"])
                self.log(f"上传成功: {r['filename']} → {r['url']}")
            else:
                self.log(f"上传失败: {result.get('error', '未知错误')}")
        except Exception as e:
            self.log(f"上传错误: {e}")

    # ── query ──

    def _do_query(self, category: str):
        if not self.server.running:
            QMessageBox.warning(self, "警告", "请先启动服务器")
            return
        pattern = self.query_bar.text()
        meta = self.server.meta_db
        rows = meta.search_images(pattern) if category == "images" else meta.search_assets(pattern)
        self._query_result = [dict(r) for r in rows]
        self.log(f"找到 {len(rows)} 条 {category} 记录:")
        for row in self._query_result[:10]:
            self.log(f"  {row['timestamp_name']} ← {row['original_name']} ({row['size']}B)")
        if len(rows) > 10:
            self.log(f"  ... 还有 {len(rows)-10} 条")

    # ── GC ──

    def _do_gc(self, which: str):
        if not self.server.running:
            QMessageBox.warning(self, "警告", "请先启动服务器")
            return
        label = "图片和附件" if which == "all" else which
        if QMessageBox.question(
            self, "确认",
            f"将扫描数据库中所有{label}记录，\n删除对应文件已丢失的条目。\n是否继续?"
        ) != QMessageBox.StandardButton.Yes:
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

    # ── lifecycle ──

    def shutdown(self):
        try:
            self.server.stop()
        except Exception:
            pass


def _wrap(layout) -> QWidget:
    """Wrap a layout in a QWidget so QFormLayout can accept it as a row."""
    w = QWidget()
    w.setLayout(layout)
    return w
