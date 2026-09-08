"""博客源页 —— 双形态博客管理（hexo 源仓回退 + 笔记库 KB 形态）。

按顶栏选择的根目录自动分派：

- **hexo 形态**（``_config.yml`` + ``source/_posts/``）：阶段 1–3 的既有
  功能全保留——文章列表/新建/scaffold/图片闭环/校验/媒体迁移/npm 发布行/
  sitegen（legacy 适配器直读 passage_index.json）。
- **KB 形态**（kb.json 或 markdown/notes 笔记树）：文章活在笔记库任意
  位置，勾选制发布——「选择发布文章」弹文件夹三态复选树（bundle_io
  同款），勾选集写回 ``<博客目录>/manifest.json`` 的 selected 字段；博客
  目录 B 还承载 site.json（站点配置）、assets/（文章媒体）、public-sitegen/
  （产物）与部署克隆。生成/发布/预览与 hexo 形态共用按钮，内部分派。

纯逻辑在 :mod:`mdtool.core.blog` 与 :mod:`mdtool.core.sitegen`，本页只编排：
长任务走 workers 线程池，不占界面线程。
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
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
    QWidget,
)

from mdtool.core import blog
from mdtool.core.kb_bundle import resolve_notes_dir
from mdtool.core.sitegen import deploy as sg_deploy
from mdtool.core.sitegen import kb as sg_kb
from mdtool.core.sitegen import legacy as sg_legacy
from mdtool.core.sitegen import preview as sg_preview
from mdtool.core.sitegen.generate import build_site
from mdtool.core.sitegen.manifest import (
    apply_selection,
    load_manifest,
    save_manifest,
)
from mdtool.desktop.qtui.tabs.file_browser import (
    collect_media_config,
    list_md_tree,
    open_external,
)
from mdtool.desktop.qtui.widgets import BaseTab, muted_label
from mdtool.desktop.qtui.workers import start_worker

_ISSUE_KINDS = {
    "broken": "链接失效",
    "migratable": "散落媒体",
    "kbfurl": "图床链接",
}

# hexo 命令统一走 npm run（Windows 下 npm 自动解析本地 node_modules/.bin，
# 免去 hexo.cmd 路径问题）；命令清单见 docs/博客工作流.md。脚本名以博客仓
# package.json 为准：生成脚本是 build（不是 generate）。
_PUBLISH_COMMANDS = ["npm run build", "npm run deploy"]
_CLEAN_COMMANDS = ["npm run clean"]
_PREVIEW_URL = "http://localhost:4000"

# sitegen（自研生成器，阶段 2）：输出与部署克隆都放博客仓内、与 hexo 的
# public/.deploy_git 隔离，发布通道切换期两套并存互不踩。
_SITEGEN_OUT = "public-sitegen"
_SITEGEN_DEPLOY_DIR = ".deploy_git-sitegen"


def _sitegen_build(blog_root: Path, report) -> object:
    """整站生成到 ``<blog>/public-sitegen``（worker 线程，日志逐行回传）。"""
    spec = sg_legacy.load_legacy_spec(blog_root)
    manifest = sg_legacy.load_legacy_manifest(blog_root)
    inputs, missing = sg_legacy.legacy_post_inputs(blog_root, manifest)
    for m in missing:
        report("log", msg=f"[失联] {m}", level="WARN")
    report("log", msg=f"sitegen：{len(inputs)} 篇文章，开始生成…")
    rpt = build_site(inputs, spec, assets_src=sg_legacy.legacy_assets_dir(blog_root),
                     out_dir=Path(blog_root) / _SITEGEN_OUT)
    report("log", msg=f"生成完成：文章 {rpt.posts}，文件 {rpt.files}，"
                      f"跳过 {len(rpt.skipped)}")
    for s in rpt.skipped:
        report("log", msg=f"[skip] {s}", level="WARN")
    return rpt


def _sitegen_deploy(blog_root: Path, report) -> list[tuple[str, int]]:
    """生成 + git 直推产物仓（worker 线程）。repo 未配置即抛错拒绝。"""
    repo, branch = sg_legacy.load_legacy_deploy(blog_root)
    if not repo:
        raise RuntimeError("博客源 _config.yml 未配置 deploy.repo，无法直推产物仓")
    out = Path(blog_root) / _SITEGEN_OUT
    _sitegen_build(blog_root, report)
    plan = sg_deploy.plan_git_deploy(
        out_dir=out, repo_url=repo, branch=branch,
        deploy_dir=Path(blog_root) / _SITEGEN_DEPLOY_DIR)
    return sg_deploy.run_git_deploy(plan, out_dir=out, report=report)


# ── KB 形态（阶段 3）worker：输入来自 <博客目录>/manifest.json + site.json ──

def _kb_build(kb_root: Path, blog_dir: Path, report) -> object:
    manifest = load_manifest(sg_kb.manifest_path(blog_dir))
    inputs, missing = sg_kb.manifest_post_inputs(kb_root, manifest)
    for m in missing:
        report("log", msg=f"[失联] {m}", level="WARN")
    cfg = sg_kb.load_site_config(blog_dir)
    theme = sg_kb.theme_root(blog_dir, cfg)
    if theme is not None:
        report("log", msg=f"sitegen：主题 {cfg.theme}/（模板/静态逐文件覆盖包内默认）")
    report("log", msg=f"sitegen：{len(inputs)} 篇已勾选文章，开始生成…")
    rpt = build_site(inputs, cfg.spec,
                     assets_src=sg_kb.assets_dir(blog_dir),
                     theme_dir=theme,
                     out_dir=blog_dir / _SITEGEN_OUT)
    report("log", msg=f"生成完成：文章 {rpt.posts}，文件 {rpt.files}，"
                      f"跳过 {len(rpt.skipped)}")
    for s in rpt.skipped:
        report("log", msg=f"[skip] {s}", level="WARN")
    return rpt


def _kb_deploy(kb_root: Path, blog_dir: Path, report) -> list[tuple[str, int]]:
    cfg = sg_kb.load_site_config(blog_dir)
    if not cfg.deploy_repo:
        raise RuntimeError("site.json 未配置 deploy.repo，无法直推产物仓")
    out = blog_dir / _SITEGEN_OUT
    _kb_build(kb_root, blog_dir, report)
    plan = sg_deploy.plan_git_deploy(
        out_dir=out, repo_url=cfg.deploy_repo, branch=cfg.deploy_branch,
        deploy_dir=blog_dir / _SITEGEN_DEPLOY_DIR)
    return sg_deploy.run_git_deploy(plan, out_dir=out, report=report)


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
_HINT_NOT_SOURCE = "当前根目录不是博客源（hexo 形态需 _config.yml 与 source/_posts/；KB 形态需 kb.json 或 markdown/ 笔记树）"
_HINT_PICK = "顶栏「博客源」选择根目录（hexo 源仓或笔记库均可）"


def _is_kb_root(root) -> bool:
    """KB 形态判定：kb.json（新规范）或 markdown/、notes/ 笔记树目录。"""
    root = Path(root)
    return (root / "kb.json").is_file() or (root / "markdown").is_dir() \
        or (root / "notes").is_dir()


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
        self._preview: sg_preview.SitePreview | None = None
        self._build_ui()

    # ── UI ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 0, 18, 14)

        hint_bar = QHBoxLayout()
        self.hint = muted_label(_HINT_PICK)
        hint_bar.addWidget(self.hint, 1)
        self.btn_blog_dir = QPushButton("更改博客目录…")
        self.btn_blog_dir.clicked.connect(self.change_blog_dir)
        hint_bar.addWidget(self.btn_blog_dir)
        root.addLayout(hint_bar)

        bar = QHBoxLayout()
        btn_new = QPushButton("新建文章…")
        btn_new.setProperty("variant", "primary")
        btn_new.clicked.connect(self.new_post)
        self.btn_kb_import = QPushButton("从笔记库导入…")
        self.btn_kb_import.clicked.connect(self.import_from_kb)
        btn_img = QPushButton("导入图片…")
        btn_img.clicked.connect(self.import_images)
        self.btn_check = QPushButton("校验全库")
        self.btn_check.clicked.connect(self.validate_all)
        self.btn_mig = QPushButton("迁移散落媒体…")
        self.btn_mig.clicked.connect(self.migrate_media)
        self.btn_select = QPushButton("选择发布文章…")
        self.btn_select.setProperty("variant", "primary")
        self.btn_select.clicked.connect(self.select_publish)
        self._busy_btns = (self.btn_check, self.btn_mig, self.btn_kb_import,
                           self.btn_select)
        for b in (btn_new, self.btn_kb_import, btn_img, self.btn_check,
                  self.btn_mig, self.btn_select):
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

        # hexo 发布行：仅 hexo 形态可见（KB 形态由 sitegen 行承担全部发布）
        self.hexo_row = QWidget()
        pub = QHBoxLayout(self.hexo_row)
        pub.setContentsMargins(0, 0, 0, 0)
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
        root.addWidget(self.hexo_row)

        sg = QHBoxLayout()
        btn_sg_gen = QPushButton("生成站点（sitegen）")
        btn_sg_gen.clicked.connect(self.generate_site)
        btn_sg_pub = QPushButton("发布站点（sitegen）")
        btn_sg_pub.setProperty("variant", "primary")
        btn_sg_pub.clicked.connect(self.publish_sitegen)
        btn_sg_prev = QPushButton("预览站点")
        btn_sg_prev.clicked.connect(self.preview_sitegen)
        self._busy_btns = self._busy_btns + (btn_sg_gen, btn_sg_pub)
        for b in (btn_sg_gen, btn_sg_pub, btn_sg_prev):
            sg.addWidget(b)
        sg.addStretch(1)
        self.sg_hint = muted_label("sitegen = 自研生成器：生成到 public-sitegen/，"
                                   "发布 = 生成 + git 直推产物仓（hexo 通道保留作回退）")
        sg.addWidget(self.sg_hint)
        root.addLayout(sg)

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

    def _mode(self) -> str:
        """根目录形态分派：hexo 源仓 / KB（笔记库博客） / none。"""
        if not self.root_dir:
            return "none"
        if blog.is_hexo_source(self.root_dir):
            return "hexo"
        return "kb" if _is_kb_root(self.root_dir) else "none"

    def _kb_notes_root(self) -> Path:
        return resolve_notes_dir(Path(self.root_dir))

    def _blog_dir(self) -> Path:
        """博客工作目录 B：QSettings 记忆 > 默认 <notes>/blog > 唯一带清单的
        一级子目录（迁移产物探测）。清单/配置/媒体/产物都聚在 B 内。"""
        root = Path(self.root_dir)
        notes = self._kb_notes_root()
        saved = QSettings("mdtool", "blog").value(
            f"blogdir/{root.as_posix()}", "", str)
        if saved:
            return Path(saved)
        default = notes / "blog"
        if default.is_dir():
            return default
        if notes.is_dir():
            candidates = [d for d in notes.iterdir()
                          if d.is_dir() and (d / "manifest.json").is_file()]
            if len(candidates) == 1:
                return candidates[0]
        return default

    def change_blog_dir(self):
        start = self._blog_dir() if self.root_dir else Path.home()
        chosen = QFileDialog.getExistingDirectory(self, "选择博客目录", str(start))
        if not chosen:
            return
        QSettings("mdtool", "blog").setValue(
            f"blogdir/{Path(self.root_dir).as_posix()}", Path(chosen).as_posix())
        self._reload()

    def _source_ok(self) -> bool:
        """sitegen 按钮可用性：hexo 源仓，或 KB 形态（博客目录可解析）。"""
        mode = self._mode()
        if mode == "hexo":
            return True
        return mode == "kb" and bool(self.root_dir)

    def _require_source(self) -> bool:
        mode = self._mode()
        if mode == "hexo":
            return True
        if mode == "kb":
            return self._require_kb()
        QMessageBox.warning(self, "提示", f"请先选择博客源目录。{_HINT_NOT_SOURCE}")
        return False

    def _require_kb(self) -> tuple[Path, Path] | None:
        """KB 形态前置校验，返回 (kb_root, 博客目录 B)；不满足弹提示。"""
        if self._mode() != "kb":
            QMessageBox.warning(self, "提示", _HINT_NOT_SOURCE)
            return None
        blog_dir = self._blog_dir()
        if not sg_kb.manifest_path(blog_dir).is_file():
            QMessageBox.warning(
                self, "提示",
                f"博客目录 {blog_dir} 下没有 manifest.json。\n"
                "先运行迁移（mdtool blog-migrate）或勾选文章发布。")
            return None
        return Path(self.root_dir), blog_dir

    def _reload(self):
        self.table.clear()
        self.issues.clear()
        mode = self._mode()
        self.hexo_row.setVisible(mode == "hexo")
        self.btn_blog_dir.setVisible(mode == "kb")
        self.btn_select.setVisible(mode == "kb")
        self.btn_kb_import.setVisible(mode == "hexo")
        self.btn_check.setVisible(mode == "hexo")
        self.btn_mig.setVisible(mode == "hexo")
        self.drafts.setVisible(mode == "hexo")
        if mode == "none":
            self.hint.setText(_HINT_NOT_SOURCE if self.root_dir else _HINT_PICK)
            return
        if mode == "hexo":
            self.hint.setText(f"hexo 博客源：{self.root_dir}")
            self.sg_hint.setText("sitegen：生成到 public-sitegen/，"
                                 "发布 = 生成 + git 直推产物仓（hexo 通道保留作回退）")
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
            return
        # KB 形态：文章 = manifest 勾选条目（含未勾选，标注展示）
        blog_dir = self._blog_dir()
        self.hint.setText(f"笔记库：{self.root_dir} ｜ 博客目录：{blog_dir}")
        self.sg_hint.setText("sitegen：生成到博客目录 public-sitegen/，"
                             "发布 = 生成 + git 直推产物仓；勾选决定发布哪些文章")
        manifest = load_manifest(sg_kb.manifest_path(blog_dir))
        for e in sorted(manifest.entries, key=lambda e: e.id, reverse=True):
            path = Path(self.root_dir) / e.note
            info = blog.read_post_info(path, source_root=self.root_dir)
            title = ("" if e.selected else "[未选] ") + info.title
            item = QTreeWidgetItem([
                title,
                info.date.strftime("%Y-%m-%d %H:%M") if info.date else "",
                _join_meta(info.categories),
                _join_meta(info.tags, sep=" "),
                e.note,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, path)
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
        kwargs = dict(title=title, slug=slug_edit.text().strip(),
                      date=date_edit.dateTime().toPython(),
                      categories=_parse_meta(cat_edit.text()),
                      tags=_parse_meta(tag_edit.text()),
                      description=desc_edit.text().strip() or None)
        try:
            if self._mode() == "kb":
                path = blog.scaffold_post(self.root_dir, dest_dir=self._blog_dir(),
                                          **kwargs)
            else:
                path = blog.scaffold_post(self.root_dir, **kwargs)
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
        if self._mode() == "kb":
            target = sg_kb.assets_dir(self._blog_dir())
        else:
            target = blog.assets_dir(self.root_dir)
        try:
            pairs = blog.import_images([Path(f) for f in files], target)
        except OSError as e:
            QMessageBox.critical(self, "导入失败", str(e))
            return
        md = blog.links_markdown(pairs)
        QApplication.clipboard().setText(md)
        self.log(f"已导入 {len(pairs)} 个媒体入 {target.name}，"
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

    # ── sitegen 行（自研生成器；hexo/KB 双形态分派）──

    def generate_site(self):
        """sitegen 整站生成到 public-sitegen/（不动 hexo 的 public/）。"""
        if not self._require_source():
            return
        self._set_busy(True)
        if self._mode() == "kb":
            start_worker(
                _kb_build, Path(self.root_dir), self._blog_dir(),
                on_log=self.log,
                on_finished=self._on_sitegen_done,
                on_error=self._on_worker_error,
            )
        else:
            start_worker(
                _sitegen_build, self.root_dir,
                on_log=self.log,
                on_finished=self._on_sitegen_done,
                on_error=self._on_worker_error,
            )

    def _on_sitegen_done(self, rpt):
        self._set_busy(False)
        self.log(f"sitegen 生成完成：文章 {rpt.posts}，文件 {rpt.files}")

    def publish_sitegen(self):
        """sitegen 发布 = 生成 + git 直推产物仓（无需 node/hexo）。"""
        if not self._require_source():
            return
        if self._mode() == "kb":
            cfg = sg_kb.load_site_config(self._blog_dir())
            repo, branch = cfg.deploy_repo, cfg.deploy_branch
            where = "site.json"
        else:
            repo, branch = sg_legacy.load_legacy_deploy(self.root_dir)
            where = "_config.yml"
        if not repo:
            QMessageBox.warning(self, "提示",
                                f"{where} 未配置 deploy.repo，无法直推产物仓")
            return
        if QMessageBox.question(
                self, "sitegen 发布",
                f"生成整站并直推 {repo}（{branch} 分支）？\n"
                f"输出 public-sitegen/，部署克隆 .deploy_git-sitegen/（首推自动 clone）。"
                ) != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        if self._mode() == "kb":
            start_worker(
                _kb_deploy, Path(self.root_dir), self._blog_dir(),
                on_log=self.log,
                on_finished=self._on_sitegen_published,
                on_error=self._on_worker_error,
            )
        else:
            start_worker(
                _sitegen_deploy, self.root_dir,
                on_log=self.log,
                on_finished=self._on_sitegen_published,
                on_error=self._on_worker_error,
            )

    def _on_sitegen_published(self, done):
        self._set_busy(False)
        self.log("sitegen 发布完成：" + " → ".join(cmd for cmd, _rc in done))

    def preview_sitegen(self):
        """预览 sitegen 产物：未生成就先生成，然后起内置服务器并开浏览器。"""
        if not self._require_source():
            return
        out = self._sitegen_out_dir()
        if not (out / "index.html").is_file():
            self._set_busy(True)
            if self._mode() == "kb":
                start_worker(
                    _kb_build, Path(self.root_dir), self._blog_dir(),
                    on_log=self.log,
                    on_finished=self._on_preview_build_done,
                    on_error=self._on_worker_error,
                )
            else:
                start_worker(
                    _sitegen_build, self.root_dir,
                    on_log=self.log,
                    on_finished=self._on_preview_build_done,
                    on_error=self._on_worker_error,
                )
            return
        self._open_preview()

    def _sitegen_out_dir(self) -> Path:
        if self._mode() == "kb":
            return self._blog_dir() / _SITEGEN_OUT
        return self.root_dir / _SITEGEN_OUT

    def _on_preview_build_done(self, rpt):
        self._set_busy(False)
        self.log(f"sitegen 生成完成（{rpt.files} 个文件），预览即将打开")
        self._open_preview()

    def _open_preview(self):
        """内置预览服务器幂等启动（端口内核分配），开浏览器。"""
        if self._preview is None or not self._preview.running:
            self._preview = sg_preview.SitePreview(self._sitegen_out_dir())
            self._preview.start()
            self.log(f"站点预览已启动: {self._preview.url}（随应用退出停止）")
        open_external(self._preview.url)

    # ── KB 形态：勾选发布 ──

    def select_publish(self):
        """复选树勾选发布文章；确认即 apply_selection 落盘并刷新列表。"""
        if self._mode() != "kb":
            return
        blog_dir = self._blog_dir()
        manifest = load_manifest(sg_kb.manifest_path(blog_dir))
        dlg = PublishSelectDialog(Path(self.root_dir), manifest, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        checked = dlg.checked_notes()
        new_manifest, _mapping = apply_selection(
            manifest, checked, published=datetime.now().strftime("%Y-%m-%d"))
        save_manifest(sg_kb.manifest_path(blog_dir), new_manifest)
        self.log(f"发布勾选已保存：{len(checked)} 篇（清单共 "
                 f"{len(new_manifest.entries)} 条，id 只增不减）")
        self._reload()

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

    def shutdown(self):
        """停掉 sitegen 预览服务器（hexo server 是独立控制台，无需管理）。"""
        if self._preview is not None:
            self._preview.stop()
        super().shutdown()

    def _set_busy(self, busy: bool):
        for b in self._busy_btns:
            b.setEnabled(not busy)

    def _on_worker_error(self, msg: str):
        self._set_busy(False)
        self.log(f"博客任务失败: {msg}", "ERROR")


class PublishSelectDialog(QDialog):
    """发布勾选树（KB 形态）：文件夹三态复选，勾选集 = 要发布的文章。

    勾选事实单点在 manifest.selected：对话框打开时从清单恢复勾选，确认后
    由调用方 apply_selection 落盘——id 只增不减，取消勾选不回收映射。
    """

    def __init__(self, kb_root: Path, manifest, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择要发布的文章")
        self.resize(600, 680)
        self._updating = False
        self._selected = {e.note for e in manifest.entries if e.selected}

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["笔记", "路径"])
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 240)
        layout.addWidget(self.tree, 1)

        btn_row = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        btn_none = QPushButton("清空")
        btn_none.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch(1)
        self.stats = muted_label("")
        btn_row.addWidget(self.stats)
        layout.addLayout(btn_row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._build_tree(kb_root)
        self.tree.itemChanged.connect(self._on_item_changed)
        self._refresh_stats()

    def _build_tree(self, kb_root: Path):
        folders: dict[str, QTreeWidgetItem] = {}

        def folder_item(parts: tuple[str, ...]) -> QTreeWidgetItem:
            key = "/".join(parts)
            if key in folders:
                return folders[key]
            parent = folder_item(parts[:-1]) if len(parts) > 1 \
                else self.tree.invisibleRootItem()
            item = QTreeWidgetItem([parts[-1], ""])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled
                          | Qt.ItemFlag.ItemIsUserCheckable
                          | Qt.ItemFlag.ItemIsAutoTristate)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            parent.addChild(item)
            folders[key] = item
            return item

        for rel, _path in list_md_tree(kb_root):
            parts = tuple(rel.split("/"))
            parent = folder_item(parts[:-1]) if len(parts) > 1 \
                else self.tree.invisibleRootItem()
            item = QTreeWidgetItem([parts[-1], rel])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled
                          | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(0, Qt.ItemDataRole.UserRole, rel)
            item.setCheckState(
                0, Qt.CheckState.Checked if rel in self._selected
                else Qt.CheckState.Unchecked)
            parent.addChild(item)
        self._recompute_parents()

    def _leaves(self):
        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if child.childCount():
                    yield from walk(child)
                else:
                    yield child
        yield from walk(self.tree.invisibleRootItem())

    def _recompute_parents(self):
        """程序化设叶子勾选不自动上卷，父级三态手动重算（bundle_io 同款）。"""
        self._updating = True
        try:
            def fix(item) -> tuple[int, int]:
                checked = total = 0
                for i in range(item.childCount()):
                    child = item.child(i)
                    if child.childCount():
                        c, t = fix(child)
                        state = (Qt.CheckState.Checked if c == t
                                 else Qt.CheckState.Unchecked if c == 0
                                 else Qt.CheckState.PartiallyChecked)
                        child.setCheckState(0, state)
                    elif child.checkState(0) == Qt.CheckState.Checked:
                        c, t = 1, 1
                    else:
                        c, t = 0, 1
                    checked += c
                    total += t
                return checked, total
            fix(self.tree.invisibleRootItem())
        finally:
            self._updating = False

    def _on_item_changed(self, item, _col):
        if self._updating:
            return
        self._updating = True
        try:
            if item.childCount():  # 文件夹勾选 → 传播到全部子孙叶子
                state = item.checkState(0)

                def spread(it):
                    for i in range(it.childCount()):
                        child = it.child(i)
                        if child.childCount():
                            spread(child)
                        else:
                            child.setCheckState(0, state)
                spread(item)
        finally:
            self._updating = False
        self._recompute_parents()
        self._refresh_stats()

    def _set_all(self, state: Qt.CheckState):
        self._updating = True
        try:
            for leaf in self._leaves():
                leaf.setCheckState(0, state)
        finally:
            self._updating = False
        self._recompute_parents()
        self._refresh_stats()

    def _refresh_stats(self):
        leaves = list(self._leaves())
        n = sum(1 for leaf in leaves
                if leaf.checkState(0) == Qt.CheckState.Checked)
        self.stats.setText(f"已选 {n} / {len(leaves)} 篇")

    def checked_notes(self) -> list[str]:
        return sorted(leaf.data(0, Qt.ItemDataRole.UserRole)
                      for leaf in self._leaves()
                      if leaf.checkState(0) == Qt.CheckState.Checked)
