import os
import sys
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from utils import extract_image_links, resolve_image_path

try:
    import zipfile
except ImportError:
    zipfile = None

# ── 16×16 像素图标（PPM P3 格式）──────────────────────────────────
# 颜色索引: 0=背景白  1=边框  2=主体  3=标签/折角
_FOLDER_PIXELS = [
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,3,3,3,3,0,0,0,0,0,0,0,0,0],
    [0,0,1,2,2,2,2,1,0,0,0,0,0,0,0,0],
    [0,1,2,2,2,2,2,2,1,1,1,1,1,1,1,0],
    [0,1,2,2,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,1,2,2,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,1,2,2,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,1,2,2,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,1,2,2,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,1,2,2,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,1,2,2,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,1,2,2,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,1,2,2,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,1,2,2,2,2,2,2,2,2,2,2,1,0,0],
    [0,0,0,1,1,1,1,1,1,1,1,1,1,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
]
_FOLDER_PALETTE = [
    (255,255,255),  # 0
    (160,130,70),   # 1
    (240,210,150),  # 2
    (200,175,120),  # 3
]

_FILE_PIXELS = [
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,1,1,1,1,1,1,1,1,1,1,0,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,1,2,2,2,2,2,2,2,2,2,2,1,0],
    [0,0,0,0,1,1,1,1,1,1,1,1,1,1,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
]
_FILE_PALETTE = [
    (255,255,255),  # 0
    (120,150,190),  # 1
    (200,220,240),  # 2
]

_ICONS_LOADED = False
_FOLDER_ICON = None
_FILE_ICON = None


def _load_icons():
    global _ICONS_LOADED, _FOLDER_ICON, _FILE_ICON
    if _ICONS_LOADED:
        return
    try:
        _FOLDER_ICON = _ppm_to_photoimage(_pixels_to_ppm(_FOLDER_PIXELS, _FOLDER_PALETTE))
        _FILE_ICON = _ppm_to_photoimage(_pixels_to_ppm(_FILE_PIXELS, _FILE_PALETTE))
    except Exception:
        pass
    _ICONS_LOADED = True


def _pixels_to_ppm(pixels, palette):
    """PPM P6 (binary) format bytes."""
    w, h = len(pixels[0]), len(pixels)
    header = f"P6\n{w} {h}\n255\n".encode('ascii')
    data = bytearray()
    for row in pixels:
        for idx in row:
            r, g, b = palette[idx]
            data.extend([r, g, b])
    return header + bytes(data)


def _ppm_to_photoimage(ppm_data):
    fd, path = tempfile.mkstemp(suffix='.ppm')
    with os.fdopen(fd, 'wb') as f:
        f.write(ppm_data)
    try:
        return tk.PhotoImage(file=path)
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


class FileBrowserTab:
    def __init__(self, notebook, app):
        self.app = app
        self.frame = ttk.Frame(notebook)
        _load_icons()
        self.create_widgets()

    def create_widgets(self):
        top = ttk.Frame(self.frame)
        top.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(top, text="刷新文件树", command=self.refresh_tree).pack(side=tk.LEFT, padx=2)

        tree_frame = ttk.Frame(self.frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tree = ttk.Treeview(tree_frame, columns=("path",), show="tree headings", height=20)
        self.tree.column("path", width=0, stretch=False)
        self.tree.heading("#0", text="文件浏览器")
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        v_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=v_scroll.set)

        self.context_menu = tk.Menu(self.frame, tearoff=0)
        self.context_menu.add_command(label="打包为 ZIP", command=self.pack_to_zip)
        self.context_menu.add_command(label="迁移图片到图床", command=self.migrate_images)

        self.tree.bind("<Button-3>", self.show_context_menu)

        log_frame = ttk.Frame(self.frame)
        log_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Label(log_frame, text="日志:").pack(anchor=tk.W)
        self.log_text = tk.Text(self.frame, height=6, state=tk.DISABLED)
        self.log_text.pack(fill=tk.X, padx=5, pady=(0, 5))

    def log(self, msg, level="INFO"):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{level}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.frame.update_idletasks()

    def on_root_dir_changed(self):
        self.refresh_tree()

    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        root_dir = self.app.root_dir
        if not root_dir:
            return

        self._add_nodes("", root_dir, root_dir)

    def _add_nodes(self, parent, current_path, root_dir):
        entries = sorted(current_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for entry in entries:
            if entry.name.lower() == 'assets':
                continue
            if entry.is_dir():
                rel = entry.relative_to(root_dir)
                node_iid = str(rel)
                kw = dict(text=entry.name, iid=node_iid, open=False)
                if _FOLDER_ICON:
                    kw['image'] = _FOLDER_ICON
                node = self.tree.insert(parent, tk.END, **kw)
                self._add_nodes(node, entry, root_dir)
            elif entry.suffix.lower() == '.md':
                rel = entry.relative_to(root_dir)
                kw = dict(text=entry.name, values=(str(rel),))
                if _FILE_ICON:
                    kw['image'] = _FILE_ICON
                self.tree.insert(parent, tk.END, **kw)

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        values = self.tree.item(item, "values")
        has_path = bool(values and values[0])
        is_dir = False
        if not has_path:
            # Directory node — item itself is the iid
            if self.app.root_dir:
                full = self.app.root_dir / item
                is_dir = full.is_dir()
        if has_path or is_dir:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def pack_to_zip(self):
        if zipfile is None:
            messagebox.showerror("错误", "zipfile 模块不可用")
            return

        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("警告", "请先选中一个 .md 文件")
            return

        item = selected[0]
        values = self.tree.item(item, "values")
        if not values or not values[0]:
            messagebox.showwarning("警告", "请选中 .md 文件节点")
            return

        rel_path = values[0]
        root_dir = self.app.root_dir
        if not root_dir:
            messagebox.showwarning("警告", "请先在工具栏选择项目根目录")
            return

        md_path = root_dir / rel_path
        if not md_path.exists():
            messagebox.showerror("错误", f"文件不存在: {md_path}")
            return

        try:
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("错误", f"读取文件失败: {e}")
            return

        links = extract_image_links(content)
        image_paths = []
        for link in links:
            resolved = resolve_image_path(md_path, link['rel_path'])
            if resolved and resolved.is_file():
                image_paths.append(resolved)
            else:
                self.log(f"图片未找到 (已跳过): {link['rel_path']}", "WARNING")

        default_name = md_path.stem + ".zip"
        zip_path = filedialog.asksaveasfilename(
            title="保存 ZIP 文件",
            defaultextension=".zip",
            initialfile=default_name,
            filetypes=[("ZIP 文件", "*.zip")]
        )
        if not zip_path:
            return

        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(md_path, md_path.name)

                used_names = set()
                for img_path in image_paths:
                    name = f"assets/{img_path.name}"
                    if name in used_names:
                        base = img_path.stem
                        ext = img_path.suffix
                        counter = 1
                        while f"assets/{base}_{counter}{ext}" in used_names:
                            counter += 1
                        name = f"assets/{base}_{counter}{ext}"
                    used_names.add(name)
                    zf.write(img_path, name)

            self.log(f"打包完成: {zip_path} (1 个 .md, {len(image_paths)} 张图片)")
        except Exception as e:
            messagebox.showerror("打包失败", str(e))

    def _get_selected_md_path(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("警告", "请先选中一个 .md 文件或目录")
            return None
        item_id = selected[0]
        values = self.tree.item(item_id, "values")
        root_dir = self.app.root_dir
        if not root_dir:
            messagebox.showwarning("警告", "请先在工具栏选择项目根目录")
            return None

        if values and values[0]:
            md_path = root_dir / values[0]
            if not md_path.exists():
                messagebox.showerror("错误", f"文件不存在: {md_path}")
                return None
            return md_path

        # Directory node — item_id IS the iid (relative path)
        full = root_dir / item_id
        if full.is_dir():
            return full
        messagebox.showwarning("警告", "请选中 .md 文件节点")
        return None

    def migrate_images(self):
        path = self._get_selected_md_path()
        if path is None:
            return
        self.app.pending_migrate_md = path
        self.app.notebook.select(4)
        self.app.tab5.load_file(path)
