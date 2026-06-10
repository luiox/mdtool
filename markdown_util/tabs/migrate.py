import os
import json
import shutil
import mimetypes
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from datetime import datetime

from utils import (
    extract_image_links,
    extract_image_links_ast,
    resolve_image_path,
    make_image_filename,
    make_image_filename_from_mtime,
    is_image_hosting_name,
    HAVE_LIBMARKDOWN,
)
from server.meta_db import MetaDB

HOSTING_CONFIG = Path.home() / ".monocodes_media.json"
EXT_NAMES = ["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"]


class MigrateTab:
    def __init__(self, notebook, app):
        self.app = app
        self.frame = ttk.Frame(notebook)
        self._file_list: list[Path] = []
        self._current_file_index = -1
        self._mode_var = tk.StringVar(value="regex")
        self._std_var = tk.BooleanVar(value=True)
        self._obs_var = tk.BooleanVar(value=True)
        self._ext_vars = {ext: tk.BooleanVar(value=True) for ext in EXT_NAMES}
        self._analysis_results: list[dict] = []
        self.create_widgets()

    def create_widgets(self):
        # ── file list bar ──
        top_bar = ttk.Frame(self.frame)
        top_bar.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(top_bar, text="待处理文件:").pack(side=tk.LEFT)
        self._file_listbox = tk.Listbox(top_bar, height=5)
        self._file_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        fl_btns = ttk.Frame(top_bar)
        fl_btns.pack(side=tk.RIGHT)
        ttk.Button(fl_btns, text="分析全部", command=self.analyze_all).pack(pady=1)
        ttk.Button(fl_btns, text="清空列表", command=self._clear_file_list).pack(pady=1)

        self._file_label = ttk.Label(top_bar, text="(从文件浏览器右键选择文件或目录)")
        self._file_label.pack(side=tk.LEFT, padx=10, anchor=tk.W)

        # ── options ──
        opt = ttk.LabelFrame(self.frame, text="扫描选项", padding=5)
        opt.pack(fill=tk.X, padx=5, pady=5)

        r1 = ttk.Frame(opt)
        r1.pack(fill=tk.X, pady=2)
        ttk.Label(r1, text="解析引擎:").pack(side=tk.LEFT)
        ttk.Radiobutton(r1, text="正则表达式", variable=self._mode_var,
                        value="regex").pack(side=tk.LEFT, padx=5)
        rb = ttk.Radiobutton(r1, text="AST 解析", variable=self._mode_var, value="ast")
        rb.pack(side=tk.LEFT, padx=5)
        if not HAVE_LIBMARKDOWN:
            rb.config(state=tk.DISABLED)

        r2 = ttk.Frame(opt)
        r2.pack(fill=tk.X, pady=2)
        ttk.Label(r2, text="图片格式:").pack(side=tk.LEFT)
        ttk.Checkbutton(r2, text="![]()", variable=self._std_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(r2, text="![[ ]]", variable=self._obs_var).pack(side=tk.LEFT, padx=5)

        r3 = ttk.Frame(opt)
        r3.pack(fill=tk.X, pady=2)
        ttk.Label(r3, text="文件后缀:").pack(side=tk.LEFT)
        for ext in EXT_NAMES:
            ttk.Checkbutton(r3, text=ext, variable=self._ext_vars[ext]).pack(side=tk.LEFT, padx=2)

        # ── buttons ──
        bar = ttk.Frame(self.frame)
        bar.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(bar, text="分析当前文件", command=self.analyze_one).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="迁移当前文件", command=self.migrate_one).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="迁移全部", command=self.migrate_all).pack(side=tk.LEFT, padx=10)

        # ── image tree ──
        ttk.Label(self.frame, text="当前文件图片:").pack(anchor=tk.W, padx=5)
        tf = ttk.Frame(self.frame)
        tf.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        self._tree = ttk.Treeview(
            tf, columns=("orig", "new", "status"),
            show="headings", height=8)
        self._tree.heading("orig", text="原始文件名")
        self._tree.heading("new", text="新文件名")
        self._tree.heading("status", text="状态")
        self._tree.column("orig", width=300)
        self._tree.column("new", width=300)
        self._tree.column("status", width=120, anchor=tk.CENTER)
        vs = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vs.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vs.pack(side=tk.RIGHT, fill=tk.Y)

        # ── log ──
        ttk.Label(self.frame, text="日志:").pack(anchor=tk.W, padx=5, pady=(10, 0))
        self._log = tk.Text(self.frame, height=8, state=tk.DISABLED)
        self._log.pack(fill=tk.X, padx=5, pady=5)

    # ── public api ──

    def load_file(self, path: Path) -> None:
        """Called from file_browser.  path is a .md file or a directory."""
        self._clear_file_list()
        if path.is_dir():
            # Scan one level for .md files
            found = sorted(path.glob("*.md"))
            if not found:
                messagebox.showwarning("警告", f"目录 {path.name} 中没有 .md 文件")
                return
            for f in found:
                self._add_file(f)
            self._file_label.config(text=f"目录: {path} ({len(found)} 个 .md)", foreground="black")
            self.log(f"已加载目录 {path}，找到 {len(found)} 个 .md 文件")
        elif path.suffix == ".md":
            self._add_file(path)
            self._file_label.config(text=str(path), foreground="black")
            self.log(f"已加载文件: {path.name}")
        # Auto-analyze first file
        self._select_file(0)
        self.analyze_one()

    def on_root_dir_changed(self):
        pass

    # ── file list ──

    def _add_file(self, path: Path) -> None:
        self._file_list.append(path)
        self._file_listbox.insert(tk.END, str(path))

    def _clear_file_list(self) -> None:
        self._file_list.clear()
        self._file_listbox.delete(0, tk.END)
        self._current_file_index = -1
        for row in self._tree.get_children():
            self._tree.delete(row)

    def _select_file(self, idx: int) -> None:
        self._current_file_index = idx
        self._file_listbox.selection_clear(0, tk.END)
        if 0 <= idx < len(self._file_list):
            self._file_listbox.selection_set(idx)
            self._file_listbox.activate(idx)

    # ── helpers ──

    def _read_hosting_config(self) -> dict:
        try:
            if HOSTING_CONFIG.exists():
                with open(HOSTING_CONFIG) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def log(self, msg: str) -> None:
        self._log.config(state=tk.NORMAL)
        self._log.insert(tk.END, msg + "\n")
        self._log.see(tk.END)
        self._log.config(state=tk.DISABLED)
        self.frame.update_idletasks()

    def _get_extensions(self) -> set:
        return {e for e, v in self._ext_vars.items() if v.get()}

    def _build_migrate_items(self, md: Path, content: str, cfg: dict) -> list[dict]:
        """Analyze a single .md file and return migrate items (does not update UI)."""
        host = cfg.get("host", "127.0.0.1")
        port = cfg.get("port", 8765)
        server_prefix = f"http://{host}:{port}/images/"
        img_sub = cfg.get("images_subdir", "images")
        media_root = Path(cfg.get("media_root", "."))
        images_dir = Path(img_sub) if Path(img_sub).is_absolute() else media_root / img_sub

        extractor = extract_image_links_ast if self._mode_var.get() == "ast" else extract_image_links
        links = extractor(content, use_standard=self._std_var.get(),
                          use_obsidian=self._obs_var.get(), extensions=self._get_extensions())

        items = []
        for link in links:
            rel = link["rel_path"]
            resolved = resolve_image_path(md, rel)

            if rel.startswith(("http://", "https://")):
                if rel.startswith(server_prefix) and is_image_hosting_name(rel.rsplit("/", 1)[-1]):
                    continue  # already hosted
                status = "待迁移"
            elif resolved is None:
                status = "❌ 文件不存在"
            elif images_dir and str(resolved).startswith(str(images_dir)) and is_image_hosting_name(resolved.name):
                continue  # already in hosting dir
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
        """Migrate images for one .md file.  Raises on first error."""
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
                with open(src, "rb") as f:
                    img_data = f.read()
                src_name = src.name
                new_name = make_image_filename_from_mtime(images_dir, src, prefix="image-")

            dst = images_dir / new_name
            with open(dst, "wb") as f:
                f.write(img_data)
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
                self.log(f"  警告: 未匹配到原文")

        meta.close()
        if changed:
            md.write_text(content, encoding="utf-8")
            self.log(f"  ✅ {md.name} 已更新")

    # ── actions ──

    def analyze_one(self) -> None:
        """Analyze the currently selected file."""
        idx = self._file_listbox.curselection()
        if idx:
            self._select_file(int(idx[0]))
        if self._current_file_index < 0 or self._current_file_index >= len(self._file_list):
            messagebox.showwarning("警告", "请先在文件列表中选中一个 .md 文件")
            return

        md = self._file_list[self._current_file_index]
        cfg = self._read_hosting_config()

        for row in self._tree.get_children():
            self._tree.delete(row)
        self.log(f"\n分析: {md.name}")

        try:
            content = md.read_text(encoding="utf-8")
        except Exception as e:
            self.log(f"  ❌ 读取失败: {e}")
            return

        items = self._build_migrate_items(md, content, cfg)
        self._analysis_results = items

        for it in items:
            status = it["status"]
            display = it["original_name"]
            self._tree.insert("", tk.END, values=(display, "", status))

        pending = sum(1 for it in items if it["status"] == "待迁移")
        self.log(f"  共 {len(items)} 个链接，{pending} 个待迁移")

    def analyze_all(self) -> None:
        """Analyze all files in the list sequentially."""
        if not self._file_list:
            messagebox.showwarning("警告", "文件列表为空")
            return
        for idx in range(len(self._file_list)):
            self._select_file(idx)
            self.analyze_one()
            self.frame.update()

    def migrate_one(self) -> None:
        """Migrate the currently analyzed file."""
        md = None
        if self._current_file_index >= 0:
            md = self._file_list[self._current_file_index]
        if not md or not md.exists():
            messagebox.showwarning("警告", "请先分析一个文件")
            return

        cfg = self._read_hosting_config()
        if not cfg.get("media_root"):
            messagebox.showwarning("警告", "媒体服务器目录未配置，请在「本地媒体服务器」Tab 中配置")
            return

        pending = [it for it in self._analysis_results if it["status"] == "待迁移"]
        if not pending:
            messagebox.showinfo("提示", "当前文件没有待迁移的图片")
            return

        self.log(f"\n迁移: {md.name}")
        try:
            self._do_migrate_file(md, self._analysis_results, cfg)
        except Exception as e:
            self.log(f"  ❌ 迁移失败: {e}")
            messagebox.showerror("迁移失败", f"{md.name} 处理出错:\n{e}\n已停止")
            return

        # Refresh tree status
        for row in self._tree.get_children():
            self._tree.item(row, values=(self._tree.item(row, "values")[0], "", "✅ 已迁移"))
        self.log(f"  ✅ {md.name} 迁移完成")

    def migrate_all(self) -> None:
        """Batch migrate all files in the list.  Stops on first error."""
        if not self._file_list:
            messagebox.showwarning("警告", "文件列表为空")
            return

        cfg = self._read_hosting_config()
        if not cfg.get("media_root"):
            messagebox.showwarning("警告", "媒体服务器目录未配置")
            return

        total = len(self._file_list)
        self.log(f"\n{'='*40}\n批量迁移: {total} 个文件\n{'='*40}")

        for idx in range(total):
            md = self._file_list[idx]
            self._select_file(idx)
            self.frame.update()
            self.log(f"\n[{idx+1}/{total}] {md.name}")

            try:
                content = md.read_text(encoding="utf-8")
            except Exception as e:
                self.log(f"  ❌ 读取失败: {e}")
                messagebox.showerror("批量迁移中止", f"文件 {md.name}\n读取失败: {e}")
                return

            items = self._build_migrate_items(md, content, cfg)
            pending = [it for it in items if it["status"] == "待迁移"]
            if not pending:
                self.log(f"  无需迁移，跳过")
                continue

            self._analysis_results = items
            try:
                self._do_migrate_file(md, items, cfg)
            except Exception as e:
                self.log(f"  ❌ 迁移失败: {e}")
                messagebox.showerror("批量迁移中止", f"文件 {md.name}\n出错: {e}\n已停止在第 {idx+1}/{total} 个文件")
                return

            # Update tree for current file
            for row in self._tree.get_children():
                self._tree.delete(row)
            for it in items:
                st = it["status"]
                self._tree.insert("", tk.END, values=(it["original_name"], "", "✅" if "待迁移" in st else st))

            self.log(f"  ✅ 完成")

        self.log(f"\n{'='*40}\n批量迁移全部完成 ({total} 个文件)")
