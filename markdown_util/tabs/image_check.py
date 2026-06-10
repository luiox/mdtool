import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from utils import (
    extract_image_links,
    extract_image_links_ast,
    resolve_image_path,
    get_file_md5,
    find_md_files,
    format_size,
    HAVE_LIBMARKDOWN,
)

EXT_NAMES = ["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"]


class ImageCheckTab:
    def __init__(self, notebook, app):
        self.app = app
        self.frame = ttk.Frame(notebook)

        self.broken_links = []
        self.duplicate_groups = []

        self._mode_var = tk.StringVar(value="regex")
        self._std_var = tk.BooleanVar(value=True)
        self._obs_var = tk.BooleanVar(value=True)
        self._ext_vars = {ext: tk.BooleanVar(value=True) for ext in EXT_NAMES}

        self.create_widgets()

    def create_widgets(self):
        # ── options panel ──
        opt = ttk.LabelFrame(self.frame, text="扫描选项", padding=5)
        opt.pack(fill=tk.X, padx=5, pady=5)

        # Row 1: mode
        r1 = ttk.Frame(opt)
        r1.pack(fill=tk.X, pady=2)
        ttk.Label(r1, text="解析引擎:").pack(side=tk.LEFT)
        ttk.Radiobutton(r1, text="正则表达式", variable=self._mode_var,
                        value="regex").pack(side=tk.LEFT, padx=5)
        rb_ast = ttk.Radiobutton(r1, text="AST 解析", variable=self._mode_var,
                                 value="ast")
        rb_ast.pack(side=tk.LEFT, padx=5)
        if not HAVE_LIBMARKDOWN:
            rb_ast.config(state=tk.DISABLED)
            ttk.Label(r1, text="(需要安装 libmarkdown)", foreground="gray").pack(side=tk.LEFT)

        # Row 2: link format
        r2 = ttk.Frame(opt)
        r2.pack(fill=tk.X, pady=2)
        ttk.Label(r2, text="图片格式:").pack(side=tk.LEFT)
        ttk.Checkbutton(r2, text="![]()", variable=self._std_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(r2, text="![[ ]]", variable=self._obs_var).pack(side=tk.LEFT, padx=5)

        # Row 3: extensions
        r3 = ttk.Frame(opt)
        r3.pack(fill=tk.X, pady=2)
        ttk.Label(r3, text="文件后缀:").pack(side=tk.LEFT)
        for ext in EXT_NAMES:
            ttk.Checkbutton(r3, text=ext, variable=self._ext_vars[ext]).pack(side=tk.LEFT, padx=2)

        # ── action bar ──
        top = ttk.Frame(self.frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(top, text="开始校验", command=self.start_check).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="导出报告 (CSV)", command=self.export_report).pack(side=tk.LEFT, padx=2)

        self.progress = ttk.Progressbar(self.frame, mode="determinate")
        self.progress.pack(fill=tk.X, padx=5, pady=5)

        # ── result trees ──
        ttk.Label(self.frame, text="失效链接:").pack(anchor=tk.W, padx=5)
        bf = ttk.Frame(self.frame)
        bf.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        self.broken_tree = ttk.Treeview(
            bf, columns=("link", "source_file", "line"),
            show="headings", height=6)
        self.broken_tree.heading("link", text="图片链接")
        self.broken_tree.heading("source_file", text="来源文件")
        self.broken_tree.heading("line", text="行号")
        self.broken_tree.column("link", width=280)
        self.broken_tree.column("source_file", width=300)
        self.broken_tree.column("line", width=60, anchor=tk.CENTER)
        bs = ttk.Scrollbar(bf, orient=tk.VERTICAL, command=self.broken_tree.yview)
        self.broken_tree.configure(yscrollcommand=bs.set)
        self.broken_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bs.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(self.frame, text="重复图片（可展开分组）:").pack(anchor=tk.W, padx=5, pady=(10, 0))
        df = ttk.Frame(self.frame)
        df.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        self.dup_tree = ttk.Treeview(
            df, columns=("hash", "file", "size", "referenced_by"),
            show="tree headings", height=6)
        self.dup_tree.heading("hash", text="MD5")
        self.dup_tree.heading("file", text="图片路径")
        self.dup_tree.heading("size", text="大小")
        self.dup_tree.heading("referenced_by", text="被引用")
        self.dup_tree.column("hash", width=230)
        self.dup_tree.column("file", width=280)
        self.dup_tree.column("size", width=80, anchor=tk.CENTER)
        self.dup_tree.column("referenced_by", width=200)
        ds = ttk.Scrollbar(df, orient=tk.VERTICAL, command=self.dup_tree.yview)
        self.dup_tree.configure(yscrollcommand=ds.set)
        self.dup_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ds.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(self.frame, text="日志:").pack(anchor=tk.W, padx=5, pady=(10, 0))
        self.log_text = tk.Text(self.frame, height=5, state=tk.DISABLED)
        self.log_text.pack(fill=tk.X, padx=5, pady=5)

    # ── helpers ──

    def log(self, msg):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.frame.update_idletasks()

    def on_root_dir_changed(self):
        pass

    def clear_results(self):
        self.broken_links.clear()
        self.duplicate_groups.clear()
        for row in self.broken_tree.get_children():
            self.broken_tree.delete(row)
        for row in self.dup_tree.get_children():
            self.dup_tree.delete(row)

    def _get_extensions(self) -> set:
        return {ext for ext, var in self._ext_vars.items() if var.get()}

    # ── core ──

    def start_check(self):
        root_dir = self.app.root_dir
        if not root_dir:
            messagebox.showwarning("警告", "请先在工具栏选择项目根目录")
            return

        exts = self._get_extensions()
        if not exts:
            messagebox.showwarning("警告", "请至少勾选一个文件后缀")
            return

        use_standard = self._std_var.get()
        use_obsidian = self._obs_var.get()
        if not use_standard and not use_obsidian:
            messagebox.showwarning("警告", "请至少勾选一种图片格式")
            return

        self.clear_results()
        self.log("开始校验...")

        md_files = find_md_files(root_dir)
        if not md_files:
            self.log("未找到任何 .md 文件")
            return

        extractor = extract_image_links_ast if self._mode_var.get() == "ast" else extract_image_links
        self.log(f"解析引擎: {'AST' if self._mode_var.get() == 'ast' else '正则'} | "
                 f"格式: {'![]() ' if use_standard else ''}{'![[ ]] ' if use_obsidian else ''}| "
                 f"后缀: {','.join(sorted(exts))}")

        # Pass 1: extract links
        img_refs = {}
        self.progress["maximum"] = len(md_files)
        self.progress["value"] = 0

        for i, md_file in enumerate(md_files):
            try:
                with open(md_file, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                self.log(f"读取失败: {md_file} - {e}")
                self.progress["value"] = i + 1
                self.frame.update()
                continue

            links = extractor(content, use_standard=use_standard,
                              use_obsidian=use_obsidian, extensions=exts)
            for link_info in links:
                rel_path = link_info["rel_path"]
                resolved = resolve_image_path(md_file, rel_path)
                line_num = link_info["line"]

                if resolved is None:
                    self.broken_links.append({
                        "link": rel_path,
                        "source_file": str(md_file),
                        "line": line_num,
                    })
                else:
                    abs_key = str(resolved)
                    img_refs.setdefault(abs_key, []).append({
                        "md_file": str(md_file),
                        "rel_path": rel_path,
                        "line": line_num,
                    })

            self.progress["value"] = i + 1
            self.frame.update()

        for item in self.broken_links:
            self.broken_tree.insert("", tk.END, values=(
                item["link"], item["source_file"], item["line"]))
        self.frame.update()
        self.log(f"失效链接: {len(self.broken_links)} 个 | 图片引用: {len(img_refs)} 个")

        # Pass 2: duplicate detection
        existing_images = list(img_refs.keys())
        if len(existing_images) < 2:
            self.log("图片数量不足，跳过重复检测")
            self.progress["value"] = self.progress["maximum"]
            self.log("校验完成")
            return

        size_groups = {}
        self.progress["maximum"] = len(existing_images)
        self.progress["value"] = 0
        for p in existing_images:
            try:
                sz = Path(p).stat().st_size
                size_groups.setdefault(sz, []).append(p)
            except OSError:
                pass
            self.progress["value"] += 1
        self.frame.update()

        candidates = [p for paths in size_groups.values() if len(paths) >= 2 for p in paths]
        if not candidates:
            self.log("所有图片大小均不同，无重复图片")
            self.progress["value"] = self.progress["maximum"]
            self.log("校验完成")
            return

        all_hashes = {}
        self.progress["maximum"] = len(candidates)
        self.progress["value"] = 0
        for p in candidates:
            try:
                md5 = get_file_md5(Path(p))
                all_hashes.setdefault(md5, []).append(p)
            except Exception as e:
                self.log(f"计算 MD5 失败: {p} - {e}")
            self.progress["value"] += 1
            self.frame.update()

        dup_count = 0
        for md5, paths in all_hashes.items():
            if len(paths) < 2:
                continue
            group = []
            for p in paths:
                refs = img_refs.get(p, [])
                ref_str = "; ".join([r["md_file"] for r in refs])
                try:
                    size_str = format_size(Path(p).stat().st_size)
                except OSError:
                    size_str = "?"
                group.append({"path": p, "size": size_str, "referenced_by": ref_str})
            self.duplicate_groups.append((md5, group))
            pid = self.dup_tree.insert("", tk.END, text="",
                values=(md5, f"[{len(group)} 个重复文件]", "", ""), open=False)
            for item in group:
                self.dup_tree.insert(pid, tk.END, text="",
                    values=(md5, item["path"], item["size"], item["referenced_by"]))
            dup_count += len(group)

        self.frame.update()
        self.log(f"重复图片: {len(self.duplicate_groups)} 组, {dup_count} 个文件")
        self.log("校验完成")

    def export_report(self):
        if not self.broken_links and not self.duplicate_groups:
            messagebox.showinfo("提示", "没有可导出的数据，请先运行校验")
            return
        path = filedialog.asksaveasfilename(
            title="导出校验报告", defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["=== 失效链接 ==="])
                w.writerow(["图片链接", "来源文件", "行号"])
                for item in self.broken_links:
                    w.writerow([item["link"], item["source_file"], item["line"]])
                w.writerow([])
                w.writerow(["=== 重复图片 ==="])
                w.writerow(["MD5", "图片路径", "大小", "被引用文件"])
                for md5, group in self.duplicate_groups:
                    w.writerow([f"组: {md5}", "", "", ""])
                    for item in group:
                        w.writerow([md5, item["path"], item["size"], item["referenced_by"]])
            self.log(f"报告已导出: {path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))
