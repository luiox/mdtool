"""博客（Hexo）源页 —— 博客源作为与散装/db 并列的知识库形态（B0–B2）。

识别含 ``_config.yml`` + ``source/_posts/`` 的目录；文章列表读 front-matter
（标题/日期/分类/标签，草稿可过滤）；新建文章走 Hexo scaffold；图片闭环
（导入图片入 source/assets → 链接上剪贴板；全库校验；散落媒体迁移改写）。
纯逻辑在 :mod:`mdtool.core.blog`，本页只编排（plan_zip_bundle 同款分层）：
长任务（校验/迁移计划/迁移执行）走 workers 线程池，不占界面线程。
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from mdtool.core import blog
from mdtool.desktop.qtui.tabs.file_browser import collect_media_config, open_external
from mdtool.desktop.qtui.widgets import BaseTab, muted_label
from mdtool.desktop.qtui.workers import start_worker

_ISSUE_KINDS = {
    "broken": "链接失效",
    "migratable": "散落媒体",
    "kbfurl": "图床链接",
}

# hexo 命令统一走 npm run（Windows 下 npm 自动解析本地 node_modules/.bin，
# 免去 hexo.cmd 路径问题）；命令清单见 docs/博客工作流.md
_PUBLISH_COMMANDS = ["npm run generate", "npm run deploy"]
_CLEAN_COMMANDS = ["npm run clean"]
_PREVIEW_URL = "http://localhost:4000"


def _hexo_job(root: Path, commands: list[str], report) -> list[tuple[str, int]]:
    """顺序执行 hexo 命令（worker 线程），stdout 逐行回传日志总线。

    任一命令非零退出即抛错中止，后续命令不执行（发布半途失败时不会
    出现"生成了但没推"之外的半成品状态——generate 失败自然不 deploy）。
    """
    done: list[tuple[str, int]] = []
    for cmd in commands:
        report("log", msg=f"$ {cmd}")
        proc = subprocess.Popen(cmd, cwd=str(root), shell=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        assert proc.stdout is not None
        for line in proc.stdout:
            report("log", msg=line.rstrip())
        code = proc.wait()
        done.append((cmd, code))
        if code != 0:
            raise RuntimeError(f"{cmd} 退出码 {code}")
    return done

# 源码内不算问题的问题（validate_blog 已滤，这里仅兜底展示用）
_HINT_NOT_SOURCE = "当前根目录不是 Hexo 博客源（需要 _config.yml 与 source/_posts/）"


def _join_meta(items, sep: str = "/") -> str:
    """分类/标签展示：嵌套 tuple（Hexo 多级分类）以 sep 连接，多个值逗号分隔。"""

    def flat(x):
        if isinstance(x, (list, tuple)):
            return sep.join(flat(i) for i in x)
        return str(x)

    return ", ".join(flat(i) for i in items)


def _parse_meta(text: str) -> tuple:
    """输入 'Linux/KVM, 随笔' → (('Linux', 'KVM'), '随笔')；空段忽略。"""
    out = []
    for part in re.split(r"[,，]", text):
        seg = tuple(s.strip() for s in part.split("/") if s.strip())
        if seg:
            out.append(seg if len(seg) > 1 else seg[0])
    return tuple(out)


class BlogPage(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ── UI ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 0, 18, 14)

        self.hint = muted_label("顶栏「博客源」选择博客根目录"
                                "（需含 _config.yml 与 source/_posts/）")
        root.addWidget(self.hint)

        bar = QHBoxLayout()
        btn_new = QPushButton("新建文章…")
        btn_new.setProperty("variant", "primary")
        btn_new.clicked.connect(self.new_post)
        btn_kb = QPushButton("从笔记库导入…")
        btn_kb.clicked.connect(self.import_from_kb)
        btn_img = QPushButton("导入图片…")
        btn_img.clicked.connect(self.import_images)
        btn_check = QPushButton("校验全库")
        btn_check.clicked.connect(self.validate_all)
        btn_mig = QPushButton("迁移散落媒体…")
        btn_mig.clicked.connect(self.migrate_media)
        self._busy_btns = (btn_check, btn_mig, btn_kb)
        for b in (btn_new, btn_kb, btn_img, btn_check, btn_mig):
            bar.addWidget(b)
        bar.addStretch(1)
        self.drafts = QCheckBox("含草稿")
        self.drafts.setChecked(True)
        self.drafts.toggled.connect(lambda _on: self._reload())
        bar.addWidget(self.drafts)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self._reload)
        bar.addWidget(refresh)
        root.addLayout(bar)

        pub = QHBoxLayout()
        btn_pub = QPushButton("一键发布")
        btn_pub.setProperty("variant", "primary")
        btn_pub.clicked.connect(self.publish)
        btn_prev = QPushButton("本地预览")
        btn_prev.clicked.connect(self.preview_server)
        btn_clean = QPushButton("清理缓存")
        btn_clean.clicked.connect(self.clean_cache)
        self._busy_btns = self._busy_btns + (btn_pub, btn_clean)
        for b in (btn_pub, btn_prev, btn_clean):
            pub.addWidget(b)
        pub.addStretch(1)
        pub.addWidget(muted_label("发布 = hexo generate + deploy（输出见日志页）；"
                                  "预览 = hexo server，独立控制台"))
        root.addLayout(pub)

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(["标题", "日期", "分类", "标签", "文件"])
        self.table.setRootIsDecorated(False)
        self.table.setAllColumnsShowFocus(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemDoubleClicked.connect(
            lambda item, _col: self._open_item(item))
        split.addWidget(self.table)

        self.issues = QTreeWidget()
        self.issues.setHeaderLabels(["类型", "文章", "行", "链接", "说明"])
        self.issues.setRootIsDecorated(False)
        self.issues.setAllColumnsShowFocus(True)
        split.addWidget(self.issues)
        split.setSizes([3, 1])
        root.addWidget(split, 1)

    # ── 数据源 ──

    def set_root_dir(self, root_dir):
        super().set_root_dir(root_dir)
        self._reload()

    def _source_ok(self) -> bool:
        return bool(self.root_dir) and blog.is_hexo_source(self.root_dir)

    def _require_source(self) -> bool:
        if not self._source_ok():
            QMessageBox.warning(self, "提示", f"请先选择博客源目录。{_HINT_NOT_SOURCE}")
            return False
        return True

    def _reload(self):
        self.table.clear()
        self.issues.clear()
        if not self._source_ok():
            self.hint.setText(_HINT_NOT_SOURCE if self.root_dir
                              else "顶栏「博客源」选择博客根目录"
                                   "（需含 _config.yml 与 source/_posts/）")
            return
        self.hint.setText(f"博客源：{self.root_dir}")
        for info in blog.list_posts(self.root_dir,
                                    include_drafts=self.drafts.isChecked()):
            title = ("[草稿] " if info.draft else "") + info.title
            item = QTreeWidgetItem([
                title,
                info.date.strftime("%Y-%m-%d %H:%M") if info.date else "",
                _join_meta(info.categories),
                _join_meta(info.tags, sep=" "),
                info.rel,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, info.path)
            self.table.addTopLevelItem(item)

    def _open_item(self, item):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path is not None:
            open_external(Path(path))

    def _on_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if item is None:
            return
        path = Path(item.data(0, Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction("打开（系统默认程序）", lambda: open_external(path))
        menu.addAction("打开所在文件夹", lambda: open_external(path.parent))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ── B1 新建文章 ──

    def new_post(self):
        if not self._require_source():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("新建文章")
        form = QFormLayout(dlg)
        title_edit = QLineEdit()
        slug_edit = QLineEdit()
        slug_edit.setPlaceholderText("空 = 用标题（自动去文件系统非法字符）")
        date_edit = QDateTimeEdit(datetime.now())
        date_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        cat_edit = QLineEdit()
        cat_edit.setPlaceholderText("逗号分隔，多级分类用 /，如 Linux/KVM, 随笔")
        tag_edit = QLineEdit()
        tag_edit.setPlaceholderText("逗号分隔")
        desc_edit = QLineEdit()
        desc_edit.setPlaceholderText("SEO 摘要（可选，不填则搜索引擎截取正文开头）")
        form.addRow("标题:", title_edit)
        form.addRow("文件名:", slug_edit)
        form.addRow("日期:", date_edit)
        form.addRow("分类:", cat_edit)
        form.addRow("标签:", tag_edit)
        form.addRow("描述:", desc_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        title = title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "提示", "标题不能为空")
            return
        try:
            path = blog.scaffold_post(
                self.root_dir, title=title, slug=slug_edit.text().strip(),
                date=date_edit.dateTime().toPython(),
                categories=_parse_meta(cat_edit.text()),
                tags=_parse_meta(tag_edit.text()),
                description=desc_edit.text().strip() or None)
        except FileExistsError as e:
            QMessageBox.warning(self, "提示", str(e))
            return
        except OSError as e:
            QMessageBox.critical(self, "新建文章失败", str(e))
            return
        self.log(f"新建文章: {path.name}")
        self._reload()
        open_external(path)

    # ── B2 导入图片 ──

    def import_images(self):
        if not self._require_source():
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片（Typora 专职编辑：链接复制到剪贴板后在光标处粘贴）",
            "", "图片 (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.svg);;所有文件 (*.*)")
        if not files:
            return
        try:
            pairs = blog.import_images([Path(f) for f in files],
                                       blog.assets_dir(self.root_dir))
        except OSError as e:
            QMessageBox.critical(self, "导入失败", str(e))
            return
        md = blog.links_markdown(pairs)
        QApplication.clipboard().setText(md)
        self.log(f"已导入 {len(pairs)} 个媒体入 source/assets，"
                 f"Markdown 链接已复制到剪贴板: {md}")

    # ── 从笔记库导入 ──

    def import_from_kb(self):
        """笔记 → 博客文章：图床 URL 改写为 assets/<名>，媒体一并拷入。"""
        if not self._require_source():
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要抽取的笔记库文章（可多选）", "", "Markdown (*.md)")
        if not files:
            return
        from mdtool.desktop.qtui.tabs.media_server import load_config as load_media_config
        mc = collect_media_config(load_media_config)
        plan = blog.plan_note_extract(
            [Path(f) for f in files], self.root_dir,
            media_root=mc["media_root"], host=mc["host"], port=mc["port"],
            images_subdir=mc["images_subdir"], assets_subdir=mc["assets_subdir"])
        if not plan.posts:
            QMessageBox.information(self, "导入", "没有可导入的文章（目标同名均已存在）")
            return
        msg = (f"导入 {len(plan.posts)} 篇到 source/_posts/，"
               f"随行媒体 {len(plan.media)} 个入 source/assets/。")
        if plan.skipped:
            msg += "\n\n注意：\n" + "\n".join(plan.skipped[:10])
        if plan.media and mc["media_root"] is None:
            msg += "\n\n警告：未配置媒体根目录，图床链接不会被改写。"
        if QMessageBox.question(self, "从笔记库导入", msg) != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        start_worker(
            blog.apply_note_extract, plan,
            on_finished=self._on_extract_applied,
            on_error=self._on_worker_error,
        )

    def _on_extract_applied(self, result):
        self._set_busy(False)
        n_posts, n_media = result
        self.log(f"笔记库导入完成: {n_posts} 篇文章入 _posts，{n_media} 个媒体入 assets")
        self._reload()

    # ── 发布行（B3）──

    def publish(self):
        if not self._require_source():
            return
        if QMessageBox.question(
                self, "一键发布",
                "执行 hexo generate + hexo deploy，并把 public/ 推送到产物仓库？"
                ) != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        start_worker(
            _hexo_job, self.root_dir, list(_PUBLISH_COMMANDS),
            on_log=self.log,
            on_finished=self._on_published,
            on_error=self._on_worker_error,
        )

    def _on_published(self, result):
        self._set_busy(False)
        self.log(f"发布完成: {' → '.join(cmd for cmd, _code in result)}")

    def preview_server(self):
        """hexo server 起独立控制台窗口（用户自己 Ctrl+C 停），并开浏览器。"""
        if not self._require_source():
            return
        try:
            subprocess.Popen(
                "npm run server", cwd=str(self.root_dir), shell=True,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except OSError as e:
            QMessageBox.critical(self, "预览启动失败", str(e))
            return
        self.log(f"本地预览已启动: {_PREVIEW_URL}（独立控制台，Ctrl+C 停止）")
        open_external(_PREVIEW_URL)

    def clean_cache(self):
        if not self._require_source():
            return
        self._set_busy(True)
        start_worker(
            _hexo_job, self.root_dir, list(_CLEAN_COMMANDS),
            on_log=self.log,
            on_finished=lambda _r: (self._set_busy(False), self.log("缓存已清理")),
            on_error=self._on_worker_error,
        )

    # ── B2 校验 ──

    def validate_all(self):
        if not self._require_source():
            return
        self._set_busy(True)
        start_worker(
            blog.validate_blog, self.root_dir,
            include_drafts=self.drafts.isChecked(),
            on_finished=self._on_validated,
            on_error=self._on_worker_error,
        )

    def _on_validated(self, report):
        self._set_busy(False)
        self.issues.clear()
        for it in report.issues:
            self.issues.addTopLevelItem(QTreeWidgetItem([
                _ISSUE_KINDS.get(it.kind, it.kind), it.post,
                str(it.line), it.dest, it.message]))
        kinds = [it.kind for it in report.issues]
        self.log(
            f"校验完成: {report.scanned} 篇，链接问题 {len(kinds)} 处"
            f"（失效 {kinds.count('broken')} / 散落媒体 {kinds.count('migratable')}"
            f" / 图床链接 {kinds.count('kbfurl')}）；"
            f"assets 共 {report.assets_count} 个，其中 {len(report.unreferenced)} 个无引用")

    # ── B2 迁移 ──

    def migrate_media(self):
        if not self._require_source():
            return
        self._set_busy(True)
        start_worker(
            blog.plan_blog_migration, self.root_dir,
            include_drafts=self.drafts.isChecked(),
            on_finished=self._on_migration_planned,
            on_error=self._on_worker_error,
        )

    def _on_migration_planned(self, planned):
        moves, dest_maps = planned
        if not moves:
            self._set_busy(False)
            QMessageBox.information(
                self, "迁移", "没有可迁移的散落媒体（文章引用的媒体都已在 source/assets/）")
            return
        lines = [f"{m.src.name} → source/{m.new_dest}" for m in moves[:30]]
        if len(moves) > 30:
            lines.append(f"… 共 {len(moves)} 项")
        ans = QMessageBox.question(
            self, "迁移散落媒体",
            f"将移动 {len(moves)} 个媒体到 source/assets/，改写 "
            f"{len(dest_maps)} 篇文章里的链接（assets/<时间戳名>，其余内容不动）：\n\n"
            + "\n".join(lines))
        if ans != QMessageBox.StandardButton.Yes:
            self._set_busy(False)
            return
        start_worker(
            blog.apply_blog_migration, self.root_dir, moves, dest_maps,
            on_finished=self._on_migration_applied,
            on_error=self._on_worker_error,
        )

    def _on_migration_applied(self, result):
        self._set_busy(False)
        moved, changed = result
        self.log(f"迁移完成: 移动 {moved} 个媒体，改写 {changed} 篇文章链接")
        self._reload()

    # ── 杂项 ──

    def _set_busy(self, busy: bool):
        for b in self._busy_btns:
            b.setEnabled(not busy)

    def _on_worker_error(self, msg: str):
        self._set_busy(False)
        self.log(f"博客任务失败: {msg}", "ERROR")
