import os
import re
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path


class SpaceFixTab:
    def __init__(self, notebook, app):
        self.app = app
        self.frame = ttk.Frame(notebook)

        self.md_root = None
        self.messy_img_root = None
        self.raw_links = []
        self.matched_items = []

        self.pattern = re.compile(r'!\[.*?]\(assets\/[^)]* [^)]*\)')

        self.create_widgets()

    def create_widgets(self):
        row1 = ttk.Frame(self.frame)
        row1.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(row1, text="1. 选择 Markdown 根目录", command=self.select_md_root).pack(side=tk.LEFT, padx=2)
        self.md_root_label = ttk.Label(row1, text="未选择", foreground="gray")
        self.md_root_label.pack(side=tk.LEFT, padx=10)

        row2 = ttk.Frame(self.frame)
        row2.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(row2, text="2. 选择混乱图片源目录（图片目前存放的杂乱位置）",
                   command=self.select_messy_img_root).pack(side=tk.LEFT, padx=2)
        self.messy_label = ttk.Label(row2, text="未选择", foreground="gray")
        self.messy_label.pack(side=tk.LEFT, padx=10)

        row3 = ttk.Frame(self.frame)
        row3.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(row3, text="3. 文件名匹配（基于原始路径定位源文件）",
                   command=self.match_filenames).pack(side=tk.LEFT, padx=2)

        ttk.Label(self.frame, text="原始匹配链接（可多选）:").pack(anchor=tk.W, padx=5)
        self.listbox1 = tk.Listbox(self.frame, selectmode=tk.EXTENDED, height=6)
        self.listbox1.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

        ttk.Label(self.frame, text="匹配结果（选中后执行）:").pack(anchor=tk.W, padx=5)
        self.tree = ttk.Treeview(
            self.frame,
            columns=("original_rel", "new_rel", "filename", "source_path"),
            show="headings", height=8, selectmode='extended'
        )
        self.tree.heading("original_rel", text="原始相对路径")
        self.tree.heading("new_rel", text="新相对路径（空格变_）")
        self.tree.heading("filename", text="图片文件名")
        self.tree.heading("source_path", text="源文件绝对路径")
        self.tree.column("original_rel", width=220)
        self.tree.column("new_rel", width=220)
        self.tree.column("filename", width=130)
        self.tree.column("source_path", width=280)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

        row4 = ttk.Frame(self.frame)
        row4.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(row4, text="4. 执行（移动图片并更新链接）", command=self.execute).pack(side=tk.LEFT, padx=2)
        ttk.Button(row4, text="清空日志", command=self.clear_log).pack(side=tk.LEFT, padx=2)

        ttk.Label(self.frame, text="运行日志:").pack(anchor=tk.W, padx=5)
        self.log_text = tk.Text(self.frame, height=5, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

    def log(self, msg, level="INFO"):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{level}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.frame.update_idletasks()

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    def on_root_dir_changed(self):
        pass

    def select_md_root(self):
        initial = str(self.app.root_dir) if self.app.root_dir else os.getcwd()
        path = filedialog.askdirectory(
            title="选择包含 Markdown 文件的根目录",
            initialdir=initial
        )
        if path:
            self.md_root = Path(path)
            self.md_root_label.config(text=str(self.md_root), foreground="black")
            self.log(f"Markdown 根目录已选择: {self.md_root}")
            self.scan_md_files()

    def select_messy_img_root(self):
        path = filedialog.askdirectory(
            title="选择混乱图片源目录（包含所有待整理图片的顶层文件夹）"
        )
        if path:
            self.messy_img_root = Path(path)
            self.messy_label.config(text=str(self.messy_img_root), foreground="black")
            self.log(f"混乱图片源目录已选择: {self.messy_img_root}")

    def scan_md_files(self):
        if not self.md_root:
            messagebox.showwarning("警告", "请先选择 Markdown 根目录")
            return

        self.raw_links.clear()
        self.listbox1.delete(0, tk.END)
        self.matched_items.clear()
        self.clear_tree()

        for md_file in self.md_root.rglob("*.md"):
            try:
                with open(md_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                for match in self.pattern.finditer(content):
                    link = match.group(0)
                    rel_path = re.search(r'!\[.*?]\((assets/[^)]+)\)', link).group(1)
                    self.raw_links.append({
                        "md_file": md_file,
                        "link": link,
                        "rel_path": rel_path,
                        "start": match.start(),
                        "end": match.end()
                    })
            except Exception as e:
                self.log(f"读取 {md_file} 失败: {e}", "ERROR")

        if self.raw_links:
            for item in self.raw_links:
                display = f"{item['md_file'].relative_to(self.md_root)} -> {item['rel_path']}"
                self.listbox1.insert(tk.END, display)
            self.log(f"扫描完成，找到 {len(self.raw_links)} 个带空格的图片链接")
        else:
            self.log("未找到任何匹配的图片链接", "WARNING")

    def clear_tree(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

    def match_filenames(self):
        if not self.messy_img_root:
            messagebox.showwarning("警告", "请先选择混乱图片源目录")
            return

        selected_indices = self.listbox1.curselection()
        if not selected_indices:
            messagebox.showwarning("警告", "请在第一个列表框中选中要匹配的链接")
            return

        file_index = {}
        for file_path in self.messy_img_root.rglob("*"):
            if file_path.is_file():
                if file_path.name not in file_index:
                    file_index[file_path.name] = file_path

        self.matched_items.clear()
        self.clear_tree()

        matched_count = 0
        for idx in selected_indices:
            raw = self.raw_links[idx]
            md_file = raw["md_file"]
            original_rel = raw["rel_path"]
            img_filename = Path(original_rel).name

            if img_filename not in file_index:
                self.log(f"在混乱图片目录中未找到文件: {img_filename}", "WARNING")
                continue

            source_file = file_index[img_filename]
            new_rel = original_rel.replace(" ", "_")
            target_file = md_file.parent / new_rel

            matched = {
                "md_file": md_file,
                "original_link": raw["link"],
                "original_rel": original_rel,
                "new_rel": new_rel,
                "img_filename": img_filename,
                "source_file": source_file,
                "target_file": target_file,
                "start": raw["start"],
                "end": raw["end"]
            }
            self.matched_items.append(matched)
            self.tree.insert("", tk.END, values=(
                original_rel, new_rel, img_filename, str(source_file)
            ))
            matched_count += 1

        self.log(f"匹配完成，成功 {matched_count} 个，失败 {len(selected_indices) - matched_count} 个")

    def execute(self):
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("警告", "请在匹配结果表格中选中要执行的行")
            return

        to_execute = []
        for item in selected_items:
            values = self.tree.item(item, "values")
            original_rel, new_rel, filename, src_path = values
            for m in self.matched_items:
                if m["original_rel"] == original_rel and m["new_rel"] == new_rel:
                    to_execute.append(m)
                    break

        if not to_execute:
            self.log("未找到对应的数据项，请检查", "ERROR")
            return

        if not messagebox.askyesno(
            "确认",
            f"即将处理 {len(to_execute)} 个图片文件。\n移动文件并更新 Markdown 链接。\n是否继续？"
        ):
            return

        success = 0
        for item in to_execute:
            try:
                src = item["source_file"]
                dst = item["target_file"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    overwrite = messagebox.askyesno(
                        "文件已存在",
                        f"{dst.name} 已存在，是否覆盖？\n{dst}"
                    )
                    if not overwrite:
                        self.log(f"跳过移动 {src} -> {dst}", "WARNING")
                        continue
                shutil.move(str(src), str(dst))
                self.log(f"移动文件: {src} -> {dst}")

                md_file = item["md_file"]
                new_link = item["original_link"].replace(item["original_rel"], item["new_rel"])
                with open(md_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                if new_link in content:
                    new_content = content.replace(item["original_link"], new_link, 1)
                else:
                    escaped = re.escape(item["original_link"])
                    new_content = re.sub(escaped, new_link, content, count=1)
                if new_content != content:
                    with open(md_file, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    self.log(f"更新链接: {md_file.relative_to(self.md_root)}")
                else:
                    self.log(f"链接未更新（可能已改变）: {md_file}", "WARNING")
                success += 1
            except Exception as e:
                self.log(f"处理失败: {e}", "ERROR")

        self.log(f"执行完成，成功 {success}/{len(to_execute)} 个")
        self.scan_md_files()
