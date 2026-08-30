"""Migrate page — 本地/Obsidian 图片引用 → 8765 托管 URL。

把图片复制进托管目录并改写链接。分析与迁移的纯逻辑
（:func:`_build_migrate_items`、:func:`_do_migrate_file`）保持原样；
批量迁移跑线程池，日志走全局 logbus。入口：文件浏览器右键
「迁移图片到图床」。
"""

import json
import mimetypes
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mdtool.desktop.qtui.widgets import BaseTab
from mdtool.desktop.qtui.workers import start_worker
from mdtool.core.server.meta_db import MetaDB
from mdtool.core.utils import (
    HAVE_LIBMARKDOWN,
    extract_image_links,
    extract_image_links_ast,
    is_image_hosting_name,
    make_image_filename,
    make_image_filename_from_mtime,
    resolve_image_path,
)

HOSTING_CONFIG = Path.home() / ".monocodes_media.json"
EXT_NAMES = ["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"]


class MigrateTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_list: list[Path] = []
        self._current_index = -1
        self._mode = "regex"
        self._std = True
        self._obs = True
        self._exts: set = set(EXT_NAMES)
        self._analysis: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 0, 18, 14)
        outer.setSpacing(8)

        # file list + actions
        top = QHBoxLayout()
        top.addWidget(QLabel("待处理文件:"))
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.file_list.currentRowChanged.connect(self._on_file_selected)
        top.addWidget(self.file_list, 1)
        bcol = QVBoxLayout()
        b = QPushButton("分析全部")
        b.clicked.connect(self.analyze_all)
        bcol.addWidget(b)
        b = QPushButton("清空列表")
        b.clicked.connect(self._clear_files)
        bcol.addWidget(b)
        top.addLayout(bcol)
        outer.addLayout(top)
        self.file_label = QLabel("(从文件浏览器右键「迁移图片到图床」选择)")
        self.file_label.setProperty("muted", True)
        outer.addWidget(self.file_label)

        # options
        gb = QGroupBox("扫描选项")
        g = QVBoxLayout(gb)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("解析引擎:"))
        self.rb_regex = QRadioButton("正则表达式"); self.rb_regex.setChecked(True)
        self.rb_regex.toggled.connect(lambda _on: self._sync_mode())
        self.rb_ast = QRadioButton("AST 解析")
        self.rb_ast.toggled.connect(lambda _on: self._sync_mode())
        if not HAVE_LIBMARKDOWN:
            self.rb_ast.setEnabled(False)
        r1.addWidget(self.rb_regex); r1.addWidget(self.rb_ast); r1.addStretch(1)
        g.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("图片格式:"))
        self.cb_std = QCheckBox("![]()"); self.cb_std.setChecked(True)
        self.cb_std.toggled.connect(lambda on: setattr(self, "_std", on))
        self.cb_obs = QCheckBox("![[ ]]"); self.cb_obs.setChecked(True)
        self.cb_obs.toggled.connect(lambda on: setattr(self, "_obs", on))
        r2.addWidget(self.cb_std); r2.addWidget(self.cb_obs); r2.addStretch(1)
        g.addLayout(r2)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("文件后缀:"))
        self.ext_checks: dict[str, QCheckBox] = {}
        for ext in EXT_NAMES:
            cb = QCheckBox(ext); cb.setChecked(True)
            self.ext_checks[ext] = cb
            r3.addWidget(cb)
        r3.addStretch(1)
        g.addLayout(r3)
        outer.addWidget(gb)

        # action bar
        bar = QHBoxLayout()
        b = QPushButton("分析当前文件")
        b.clicked.connect(self.analyze_one)
        bar.addWidget(b)
        b = QPushButton("迁移当前文件")
        b.clicked.connect(self.migrate_one)
        bar.addWidget(b)
        b = QPushButton("迁移全部")
        b.setProperty("variant", "primary")
        b.clicked.connect(self.migrate_all)
        bar.addWidget(b)
        bar.addStretch(1)
        outer.addLayout(bar)

        # tree
        outer.addWidget(QLabel("当前文件图片:"))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["原始文件名", "新文件名", "状态"])
        self.tree.setColumnWidth(0, 300); self.tree.setColumnWidth(1, 300); self.tree.setColumnWidth(2, 120)
        self.tree.setRootIsDecorated(False)
        outer.addWidget(self.tree, 1)

    def _sync_mode(self):
        self._mode = "ast" if self.rb_ast.isChecked() else "regex"
        self._exts = {e for e, cb in self.ext_checks.items() if cb.isChecked()}

    # ── file list ──

    def load_file(self, path: Path):
        """Called from the file browser's 'migrate images' action."""
        self._clear_files()
        if path.is_dir():
            found = sorted(path.glob("*.md"))
            if not found:
                QMessageBox.warning(self, "警告", f"目录 {path.name} 中没有 .md 文件")
                return
            for f in found:
                self._add_file(f)
            self.file_label.setText(f"目录: {path} ({len(found)} 个 .md)")
            self.log(f"已加载目录 {path}，找到 {len(found)} 个 .md 文件")
        elif path.suffix == ".md":
            self._add_file(path)
            self.file_label.setText(str(path))
            self.log(f"已加载文件: {path.name}")
        if self._file_list:
            self.file_list.setCurrentRow(0)
            self.analyze_one()

    def _add_file(self, path: Path):
        self._file_list.append(path)
        self.file_list.addItem(str(path))

    def _clear_files(self):
        self._file_list.clear()
        self.file_list.clear()
        self._current_index = -1
        self.tree.clear()
        self.file_label.setText("(从文件浏览器右键「迁移图片到图床」选择)")

    def _on_file_selected(self, row):
        if 0 <= row < len(self._file_list):
            self._current_index = row

    def _read_hosting_config(self) -> dict:
        try:
            if HOSTING_CONFIG.exists():
                return json.loads(HOSTING_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _set_buttons_enabled(self, enabled: bool):
        for w in self.findChildren(QPushButton):
            w.setEnabled(enabled)

    # ── analysis (pure, preserved verbatim) ──

    def _build_migrate_items(self, md: Path, content: str, cfg: dict) -> list[dict]:
        host = cfg.get("host", "127.0.0.1")
        port = cfg.get("port", 8765)
        server_prefix = f"http://{host}:{port}/images/"
        img_sub = cfg.get("images_subdir", "images")
        media_root = Path(cfg.get("media_root", "."))
        images_dir = Path(img_sub) if Path(img_sub).is_absolute() else media_root / img_sub

        extractor = extract_image_links_ast if self._mode == "ast" else extract_image_links
        links = extractor(content, use_standard=self._std, use_obsidian=self._obs, extensions=self._exts)

        items = []
        for link in links:
            rel = link["rel_path"]
            resolved = resolve_image_path(md, rel)
            if rel.startswith(("http://", "https://")):
                if rel.startswith(server_prefix) and is_image_hosting_name(rel.rsplit("/", 1)[-1]):
                    continue
                status = "待迁移"
            elif resolved is None:
                status = "❌ 文件不存在"
            elif images_dir and str(resolved).startswith(str(images_dir)) and is_image_hosting_name(resolved.name):
                continue
            else:
                status = "待迁移"
            items.append({
                "rel_path": rel,
                "full_match": link.get("full_match", ""),
                "local_path": str(resolved) if resolved else "",
                "original_name": resolved.name if resolved else rel.rsplit("/", 1)[-1],
                "status": status,
                "type": link["type"],
                "is_url": rel.startswith(("http://", "https://")),
            })
        return items

    def _do_migrate_file(self, md: Path, items: list[dict], cfg: dict) -> None:
        media_root = Path(cfg["media_root"])
        img_sub = cfg.get("images_subdir", "images")
        images_dir = Path(img_sub) if Path(img_sub).is_absolute() else media_root / img_sub
        images_dir.mkdir(parents=True, exist_ok=True)
        host, port = cfg.get("host", "127.0.0.1"), cfg.get("port", 8765)
        meta = MetaDB(media_root / "meta.db")
        content = md.read_text(encoding="utf-8")
        changed = False
        for item in items:
            if item["status"] != "待迁移":
                continue
            if item.get("is_url"):
                import urllib.request
                resp = urllib.request.urlopen(item["rel_path"])
                img_data = resp.read()
                src_name = item["original_name"]
                new_name = make_image_filename(images_dir, src_name, prefix="image-")
            else:
                src = Path(item["local_path"])
                if not src.exists():
                    self.log(f"  跳过(不存在): {src}")
                    continue
                img_data = src.read_bytes()
                src_name = src.name
                new_name = make_image_filename_from_mtime(images_dir, src, prefix="image-")
            dst = images_dir / new_name
            dst.write_bytes(img_data)
            self.log(f"  {src_name} → {new_name}")
            mime_type, _ = mimetypes.guess_type(src_name)
            meta.add_image(new_name, src_name, len(img_data), mime_type or "image/png")
            url = f"http://{host}:{port}/images/{new_name}"
            orig_stem = Path(src_name).stem
            old_md = item.get("full_match", "")
            new_md = f"![{orig_stem}]({url})"
            if old_md and old_md in content:
                content = content.replace(old_md, new_md, 1)
                changed = True
                self.log(f"  替换: {old_md[:30]}…")
            else:
                self.log("  警告: 未匹配到原文", "WARNING")
        meta.close()
        if changed:
            md.write_text(content, encoding="utf-8")
            self.log(f"  ✅ {md.name} 已更新")

    # ── actions ──

    def _render_items(self, items, mark_done=False):
        self.tree.clear()
        for it in items:
            status = "✅ 已迁移" if mark_done and it["status"] == "待迁移" else it["status"]
            self.tree.addTopLevelItem(QTreeWidgetItem([it["original_name"], "", status]))

    def analyze_one(self):
        if self._current_index < 0 or self._current_index >= len(self._file_list):
            QMessageBox.warning(self, "警告", "请先在文件列表中选中一个 .md 文件")
            return
        self._sync_mode()
        md = self._file_list[self._current_index]
        cfg = self._read_hosting_config()
        self.tree.clear()
        self.log(f"分析: {md.name}")
        try:
            content = md.read_text(encoding="utf-8")
        except Exception as e:
            self.log(f"  ❌ 读取失败: {e}", "ERROR")
            return
        items = self._build_migrate_items(md, content, cfg)
        self._analysis = items
        self._render_items(items)
        pending = sum(1 for it in items if it["status"] == "待迁移")
        self.log(f"  共 {len(items)} 个链接，{pending} 个待迁移")

    def analyze_all(self):
        if not self._file_list:
            QMessageBox.warning(self, "警告", "文件列表为空")
            return
        for i in range(len(self._file_list)):
            self.file_list.setCurrentRow(i)
            self.analyze_one()

    def migrate_one(self):
        if not (0 <= self._current_index < len(self._file_list)):
            QMessageBox.warning(self, "警告", "请先分析一个文件")
            return
        md = self._file_list[self._current_index]
        if not md.exists():
            QMessageBox.warning(self, "警告", "请先分析一个文件")
            return
        cfg = self._read_hosting_config()
        if not cfg.get("media_root"):
            QMessageBox.warning(self, "警告", "媒体服务器目录未配置，请在「媒体服务器」页配置")
            return
        if not any(it["status"] == "待迁移" for it in self._analysis):
            QMessageBox.information(self, "提示", "当前文件没有待迁移的图片")
            return
        self.log(f"迁移: {md.name}")
        try:
            self._do_migrate_file(md, self._analysis, cfg)
        except Exception as e:
            self.log(f"  ❌ 迁移失败: {e}", "ERROR")
            QMessageBox.critical(self, "迁移失败", f"{md.name} 处理出错:\n{e}\n已停止")
            return
        self._render_items(self._analysis, mark_done=True)
        self.log(f"  ✅ {md.name} 迁移完成")

    def migrate_all(self):
        if not self._file_list:
            QMessageBox.warning(self, "警告", "文件列表为空")
            return
        cfg = self._read_hosting_config()
        if not cfg.get("media_root"):
            QMessageBox.warning(self, "警告", "媒体服务器目录未配置")
            return
        self._sync_mode()
        files = list(self._file_list)
        self.log(f"批量迁移: {len(files)} 个文件")
        self._set_buttons_enabled(False)
        start_worker(
            _migrate_all_job,
            files=files, cfg=cfg, mode=self._mode, use_std=self._std,
            use_obs=self._obs, exts=self._exts,
            on_log=self.log,
            on_finished=lambda _: (self.log(f"批量迁移全部完成（{len(files)} 个文件）"),
                                   self._set_buttons_enabled(True)),
            on_error=lambda e: (self.log(f"  ❌ 批量迁移中止: {e}", "ERROR"),
                                QMessageBox.critical(self, "批量迁移中止", e),
                                self._set_buttons_enabled(True)),
        )


def _migrate_all_job(files: list, cfg: dict, mode: str, use_std: bool,
                     use_obs: bool, exts: set, report):
    """Worker-side batch migration. Re-implements the per-file analyze+apply
    loop without touching widgets; mirrors :meth:`MigrateTab._do_migrate_file`
    but with a local MetaDB per file (same as the original)."""
    for idx, md in enumerate(files):
        report("log", msg=f"[{idx+1}/{len(files)}] {md.name}")
        try:
            content = md.read_text(encoding="utf-8")
        except Exception as e:
            report("log", msg=f"  ❌ 读取失败: {e}", level="ERROR")
            raise
        # Analyze using the same helper logic (duplicated to stay pure).
        items = _build_items_pure(md, content, cfg, mode, use_std, use_obs, exts)
        if not any(it["status"] == "待迁移" for it in items):
            report("log", msg="  无需迁移，跳过")
            continue
        media_root = Path(cfg["media_root"])
        img_sub = cfg.get("images_subdir", "images")
        images_dir = Path(img_sub) if Path(img_sub).is_absolute() else media_root / img_sub
        images_dir.mkdir(parents=True, exist_ok=True)
        host, port = cfg.get("host", "127.0.0.1"), cfg.get("port", 8765)
        meta = MetaDB(media_root / "meta.db")
        changed = False
        for item in items:
            if item["status"] != "待迁移":
                continue
            if item.get("is_url"):
                import urllib.request
                img_data = urllib.request.urlopen(item["rel_path"]).read()
                src_name = item["original_name"]
                new_name = make_image_filename(images_dir, src_name, prefix="image-")
            else:
                src = Path(item["local_path"])
                if not src.exists():
                    report("log", msg=f"  跳过(不存在): {src}", level="WARNING")
                    continue
                img_data = src.read_bytes()
                src_name = src.name
                new_name = make_image_filename_from_mtime(images_dir, src, prefix="image-")
            dst = images_dir / new_name
            dst.write_bytes(img_data)
            report("log", msg=f"  {src_name} → {new_name}")
            mime_type, _ = mimetypes.guess_type(src_name)
            meta.add_image(new_name, src_name, len(img_data), mime_type or "image/png")
            url = f"http://{host}:{port}/images/{new_name}"
            old_md = item.get("full_match", "")
            new_md = f"![{Path(src_name).stem}]({url})"
            if old_md and old_md in content:
                content = content.replace(old_md, new_md, 1)
                changed = True
        meta.close()
        if changed:
            md.write_text(content, encoding="utf-8")
            report("log", msg=f"  ✅ {md.name} 完成")
    return len(files)


def _build_items_pure(md, content, cfg, mode, use_std, use_obs, exts):
    """Pure version of :meth:`MigrateTab._build_migrate_items` for the worker."""
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8765)
    server_prefix = f"http://{host}:{port}/images/"
    img_sub = cfg.get("images_subdir", "images")
    media_root = Path(cfg.get("media_root", "."))
    images_dir = Path(img_sub) if Path(img_sub).is_absolute() else media_root / img_sub
    extractor = extract_image_links_ast if mode == "ast" else extract_image_links
    links = extractor(content, use_standard=use_std, use_obsidian=use_obs, extensions=exts)
    items = []
    for link in links:
        rel = link["rel_path"]
        resolved = resolve_image_path(md, rel)
        if rel.startswith(("http://", "https://")):
            if rel.startswith(server_prefix) and is_image_hosting_name(rel.rsplit("/", 1)[-1]):
                continue
            status = "待迁移"
        elif resolved is None:
            status = "❌ 文件不存在"
        elif images_dir and str(resolved).startswith(str(images_dir)) and is_image_hosting_name(resolved.name):
            continue
        else:
            status = "待迁移"
        items.append({
            "rel_path": rel, "full_match": link.get("full_match", ""),
            "local_path": str(resolved) if resolved else "",
            "original_name": resolved.name if resolved else rel.rsplit("/", 1)[-1],
            "status": status, "type": link["type"],
            "is_url": rel.startswith(("http://", "https://")),
        })
    return items
