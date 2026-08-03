import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
import threading
import pystray
from PIL import Image, ImageDraw, ImageFont

from tabs.file_browser import FileBrowserTab
from tabs.media_server_tab import MediaServerTab
from tabs.space_fix import SpaceFixTab
from tabs.image_check import ImageCheckTab
from tabs.migrate import MigrateTab
from tabs.notes_browser import NotesBrowserTab


def _make_tray_icon():
    """Create a 32x32 tray icon — blue circle with white M."""
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, 29, 29], fill=(70, 130, 220))
    try:
        font = ImageFont.truetype("segoeui.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "M", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((32 - tw) / 2, (32 - th) / 2 - 1), "M", fill="white", font=font)
    return img


class MarkdownToolApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Markdown 工具集")
        self.root.geometry("1100x750")
        self._closing = False
        self._tray: pystray.Icon | None = None

        self.root_dir = None
        self.pending_migrate_md = None

        self.create_widgets()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Destroy>", self._on_destroy)

    def _stop_server(self):
        try:
            self.tab2.server.stop()
        except Exception:
            pass
        # Flush dirty notes and stop the watchdog observer.
        try:
            self.tab0.shutdown()
        except Exception:
            pass

    def _show_tray(self):
        if self._tray:
            return
        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self._tray_show, default=True),
            pystray.MenuItem("退出程序", self._tray_quit),
        )
        self._tray = pystray.Icon("MarkdownUtil", _make_tray_icon(),
                                  "Markdown 工具集", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _hide_window(self):
        self.root.withdraw()

    def _tray_show(self):
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.root.deiconify()
        self.root.lift()

    def _tray_quit(self):
        if self._tray:
            self._tray.stop()
            self._tray = None
        self._closing = True
        self._stop_server()
        self.root.quit()
        self.root.destroy()

    def _on_close(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("退出确认")
        dlg.geometry("360x140")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.update_idletasks()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        dlg.geometry(f"+{px + (pw-360)//2}+{py + (ph-140)//2}")

        ttk.Label(dlg, text="关闭程序时执行什么操作？",
                  font=("", 11)).pack(pady=(15, 10))

        bf = ttk.Frame(dlg)
        bf.pack(pady=10)

        def do_exit():
            dlg.destroy()
            self._closing = True
            self._stop_server()
            self.root.quit()
            self.root.destroy()

        def do_minimize():
            dlg.destroy()
            self._hide_window()
            self._show_tray()

        ttk.Button(bf, text="退出程序", width=14, command=do_exit).pack(side=tk.LEFT, padx=8)
        ttk.Button(bf, text="最小化到托盘", width=14, command=do_minimize).pack(side=tk.LEFT, padx=8)
        dlg.wait_window()

    def _on_destroy(self, event):
        if event.widget is self.root and not self._closing:
            self._closing = True
            self._stop_server()

    def create_widgets(self):
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(toolbar, text="选择项目根目录",
                   command=self.select_root_dir).pack(side=tk.LEFT, padx=2)
        self.root_dir_label = ttk.Label(toolbar, text="未选择", foreground="gray")
        self.root_dir_label.pack(side=tk.LEFT, padx=10)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tab0 = NotesBrowserTab(self.notebook, self)
        self.tab1 = FileBrowserTab(self.notebook, self)
        self.tab2 = MediaServerTab(self.notebook, self)
        self.tab3 = SpaceFixTab(self.notebook, self)
        self.tab4 = ImageCheckTab(self.notebook, self)
        self.tab5 = MigrateTab(self.notebook, self)

        self.notebook.add(self.tab0.frame, text="笔记库")
        self.notebook.add(self.tab1.frame, text="文件浏览器")
        self.notebook.add(self.tab2.frame, text="本地媒体服务器")
        self.notebook.add(self.tab3.frame, text="空格转下划线修复")
        self.notebook.add(self.tab4.frame, text="图片校验")
        self.notebook.add(self.tab5.frame, text="图片迁移")

    def select_root_dir(self):
        path = filedialog.askdirectory(title="选择项目根目录")
        if path:
            self.root_dir = Path(path)
            self.root_dir_label.config(text=str(self.root_dir), foreground="black")
            self.on_root_dir_changed()

    def on_root_dir_changed(self):
        self.tab0.on_root_dir_changed()
        self.tab1.on_root_dir_changed()
        self.tab2.on_root_dir_changed()
        self.tab3.on_root_dir_changed()
        self.tab4.on_root_dir_changed()
        self.tab5.on_root_dir_changed()
