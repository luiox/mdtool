"""导入导出中心（单窗口）与配套 worker jobs。

纯逻辑全部在 :mod:`kb_bundle`；本模块是 UI 编排：
- :class:`BundleCenterDialog` —— 一个窗口两个标签页：
  * 导出页：整棵笔记树勾选范围（文件夹三态传递）＋媒体选项＋实时统计；
  * 导入页：读包对账清单，冲突逐条看 diff 决定，未决定一律跳过落地。
- :class:`DiffDialog` —— 双栏对照查看器。
- 三个 worker job（导出 / 读包分类 / 应用），长任务走 :mod:`qtui.workers`。
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import kb_bundle as kbb

_STATUS_CN = {
    kbb.STATUS_NEW: "新增",
    kbb.STATUS_IDENTICAL: "相同（跳过）",
    kbb.STATUS_CONFLICT: "冲突",
}
_MEDIA_CN = {
    kbb.MEDIA_BUNDLED: "随包",
    kbb.MEDIA_TARGET_HAS: "目标已有",
    kbb.MEDIA_MISSING: "缺失",
}


# ── worker jobs（模块级函数，供 start_worker 使用）──

def bundle_export_job(out_path: str, container: str, notes: list,
                      source_root=None, kb_config=None, media_root=None,
                      meta_db=None, include_images=True, include_assets=True,
                      max_asset_bytes=0, report=None):
    """计划并写出合并包。``max_asset_bytes=0`` 表示附件不设阈值。

    散装来源给 ``source_root``（媒体与 meta 同根）；db 来源给
    ``media_root``（图床）+ ``meta_db``（notes.db，规范 §4 元数据已并表）。
    """
    def log(msg, level="INFO"):
        if report:
            report("log", msg=msg, level=level)

    plan = kbb.plan_export(
        notes,
        source_root=Path(source_root) if source_root else None,
        kb_config=kb_config,
        media_root=Path(media_root) if media_root else None,
        meta_db=Path(meta_db) if meta_db else None,
        include_images=include_images, include_assets=include_assets,
        max_asset_bytes=max_asset_bytes or 0)
    for d in plan.skipped:
        log(f"媒体未找到（已跳过引用）: {d}", "WARNING")
    for e in plan.excluded:
        log(f"按设置未随包: {e['path']} ({e['size']} B)")
    out = Path(out_path)
    if container == "db":
        kbb.write_db_bundle(out, plan)
    else:
        kbb.write_zip_bundle(out, plan)
    return {"path": str(out), "container": container, "notes": len(plan.notes),
            "media": len(plan.media), "skipped": len(plan.skipped),
            "excluded": len(plan.excluded)}


def bundle_load_job(path: str, target_root=None, target_db=None,
                    target_media_root=None, target_kb_config=None):
    """读包 + 与目标对账。zip/db/文件夹按路径形态自动分派。"""
    p = Path(path)
    if p.is_dir():
        bundle = kbb.read_folder_bundle(p)
    elif p.suffix.lower() == ".db":
        bundle = kbb.read_db_bundle(p)
    else:
        bundle = kbb.read_zip_bundle(p)
    plan = kbb.classify_import(
        bundle, target_root=Path(target_root) if target_root else None,
        target_db=target_db, target_media_root=target_media_root,
        target_kb_config=target_kb_config)
    return bundle, plan


def bundle_apply_job(bundle, plan, decisions: dict, target_root=None,
                     target_db=None, target_media_root=None, target_kb_config=None):
    return kbb.apply_import(
        plan, target_root=Path(target_root) if target_root else None,
        target_db=target_db, target_media_root=target_media_root,
        target_kb_config=target_kb_config, bundle=bundle, decisions=decisions)


# ── diff 查看器 ──

class DiffDialog(QDialog):
    """双栏对照：左 = 目标当前版本，右 = 包内版本，滚动联动。"""

    def __init__(self, parent, rel: str, old_text: str, new_text: str):
        super().__init__(parent)
        self.setWindowTitle(f"差异 — {rel}")
        self.resize(980, 620)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("左 = 目标当前版本　　右 = 包内版本（滚动联动）"))
        split = QSplitter(self)
        self.left = QPlainTextEdit(readOnly=True)
        self.right = QPlainTextEdit(readOnly=True)
        self.left.setPlainText(old_text)
        self.right.setPlainText(new_text)
        split.addWidget(self.left)
        split.addWidget(self.right)
        split.setSizes([460, 460])
        lay.addWidget(split, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.clicked.connect(lambda *_: self.accept())
        lay.addWidget(btns)
        # 滚动联动（互设同值时 Qt 不再发信号，天然无递归）
        self.left.verticalScrollBar().valueChanged.connect(
            self.right.verticalScrollBar().setValue)
        self.right.verticalScrollBar().valueChanged.connect(
            self.left.verticalScrollBar().setValue)


# ── 导入导出中心 ──

_ACTION_IMPORT = "导入"
_ACTION_OVERWRITE = "用包内版本覆盖"
_ACTION_SKIP = "跳过"


class BundleCenterDialog(QDialog):
    """单窗口完成导出与导入。

    导出页以整棵笔记树的三态复选框圈定范围——文件夹勾选即整枝，
    这是对"树形选择哪些导出"的直接实现；统计随勾选实时刷新。
    导入页读包后列出对账结果，冲突必须看过 diff 逐条决定。
    """

    def __init__(self, parent, *, target_kind: str, target_root=None,
                 target_db=None, target_media_root=None, meta_db=None,
                 kb_config: dict = None, log=None, pre_selected_rels=None):
        super().__init__(parent)
        self.setWindowTitle("导入导出中心")
        self.resize(920, 640)
        self.target_kind = target_kind
        self.target_root = Path(target_root) if target_root else None
        self.target_db = target_db
        self.target_media_root = Path(target_media_root) if target_media_root else None
        self.meta_db = Path(meta_db) if meta_db else None
        self.kb_config = dict(kb_config or kbb.DEFAULT_KB_CONFIG)
        self._log = log or (lambda *a, **k: None)
        self.applied = False

        self._updating_tree = False
        self._contents: dict[str, str] = {}
        self._all_rels: list[str] = []
        self._load_source()

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_export_page(), "导出")
        self.tabs.addTab(self._build_import_page(), "导入")
        lay = QVBoxLayout(self)
        src = ("散装" if target_kind == "fs" else "db") + " · " + \
              str(self.target_root or (target_db.db_path if target_db else ""))
        top = QLabel(f"当前库：{src}")
        lay.addWidget(top)
        lay.addWidget(self.tabs, 1)

        if pre_selected_rels is not None:
            self._apply_selection(set(pre_selected_rels))
        self._refresh_stats()

    # ── 数据源装载 ──

    def _load_source(self):
        if self.target_kind == "fs":
            from qtui.tabs.file_browser import list_md_tree
            notes_root = kbb.resolve_notes_dir(self.target_root)
            for rel, fp in list_md_tree(notes_root):
                try:
                    self._contents[rel] = fp.read_text(encoding="utf-8")
                    self._all_rels.append(rel)
                except (OSError, UnicodeDecodeError) as e:
                    self._log(f"读取失败（跳过）: {fp} — {e}", "WARNING")
        elif self.target_db is not None:
            # 必须带 body：导出页要内容做统计/勾选，缺省的轻量行没有这一列
            for row in self.target_db.list_all(include_body=True):
                self._contents[row["path"]] = row["body"]
                self._all_rels.append(row["path"])
        self._all_rels.sort()

    # ── 导出页 ──

    def _build_export_page(self) -> QWidget:
        page = QWidget(self)
        lay = QHBoxLayout(page)

        left = QVBoxLayout()
        left.addWidget(QLabel("勾选要导出的笔记（文件夹 = 整枝）："))
        self.exp_tree = QTreeWidget()
        self.exp_tree.setHeaderLabels(["笔记树", "大小"])
        self.exp_tree.setColumnWidth(0, 380)
        self.exp_tree.itemChanged.connect(self._on_exp_item_changed)
        self.exp_tree.itemDoubleClicked.connect(self._open_note_diff_from_export)
        left.addWidget(self.exp_tree, 1)
        self.sel_label = QLabel("")
        left.addWidget(self.sel_label)
        lay.addLayout(left, 3)

        right = QVBoxLayout()
        right.addWidget(QLabel("容器："))
        self.rb_zip = QRadioButton("zip 包（通用）")
        self.rb_db = QRadioButton("db 包（SQLite 单文件）")
        self.rb_zip.setChecked(True)
        right.addWidget(self.rb_zip)
        right.addWidget(self.rb_db)
        right.addSpacing(10)
        right.addWidget(QLabel("媒体选项："))
        self.chk_images = QCheckBox("包含图片")
        self.chk_images.setChecked(True)
        self.chk_assets = QCheckBox("包含附件")
        self.chk_assets.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(QLabel("附件超过"))
        self.spin_mb = QSpinBox()
        self.spin_mb.setRange(0, 10240)
        self.spin_mb.setValue(20)
        self.spin_mb.setSuffix(" MB")
        row.addWidget(self.spin_mb)
        row.addWidget(QLabel("跳过（0=不限）"))
        row.addStretch(1)
        right.addWidget(self.chk_images)
        right.addWidget(self.chk_assets)
        right.addLayout(row)
        self.stats_label = QLabel("")
        self.stats_label.setWordWrap(True)
        right.addSpacing(8)
        right.addWidget(self.stats_label)
        btn_row = QHBoxLayout()
        btn_row.addWidget(QPushButton("全选", clicked=lambda: self._apply_selection(
            set(self._all_rels))))
        btn_row.addWidget(QPushButton("清空", clicked=lambda: self._apply_selection(set())))
        btn_row.addStretch(1)
        right.addLayout(btn_row)
        self.export_btn = QPushButton("导出…")
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.clicked.connect(self._on_export_clicked)
        right.addWidget(self.export_btn)
        right.addStretch(1)
        lay.addLayout(right, 2)

        self._build_exp_tree_items()
        for w in (self.chk_images, self.chk_assets):
            w.toggled.connect(self._refresh_stats)
        self.spin_mb.valueChanged.connect(self._refresh_stats)
        return page

    def _build_exp_tree_items(self):
        """由 rel 列表构建带复选框的树；叶子默认全选，父级随后自然呈三态。"""
        self._updating_tree = True
        self.exp_tree.clear()
        folders: dict[str, QTreeWidgetItem] = {}

        def ensure_folder(path: str) -> QTreeWidgetItem:
            if path in folders:
                return folders[path]
            parent_path, _, name = path.rpartition("/")
            parent = ensure_folder(parent_path) if parent_path \
                else self.exp_tree.invisibleRootItem()
            item = QTreeWidgetItem([name, ""])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsAutoTristate)
            folders[path] = item
            parent.addChild(item)
            return item

        for rel in self._all_rels:
            parent_path, _, name = rel.rpartition("/")
            parent = ensure_folder(parent_path) if parent_path \
                else self.exp_tree.invisibleRootItem()
            item = QTreeWidgetItem([name, f"{len(self._contents.get(rel, '').encode('utf-8'))} B"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setData(0, Qt.ItemDataRole.UserRole, rel)
            item.setToolTip(0, rel)
            parent.addChild(item)
        self._updating_tree = False

    def _apply_selection(self, wanted: set[str]):
        """把勾选状态设为恰好 wanted 中的叶子（其余取消），父级自动三态。"""
        self._updating_tree = True

        def walk(item: QTreeWidgetItem) -> bool:
            rel = item.data(0, Qt.ItemDataRole.UserRole)
            if rel is not None:
                item.setCheckState(0, Qt.CheckState.Checked if rel in wanted
                                   else Qt.CheckState.Unchecked)
                return rel in wanted
            any_child = False
            all_child = True
            for i in range(item.childCount()):
                c = walk(item.child(i))
                any_child = any_child or c
                all_child = all_child and c
            state = (Qt.CheckState.Checked if all_child else
                     Qt.CheckState.PartiallyChecked if any_child
                     else Qt.CheckState.Unchecked)
            item.setCheckState(0, state)
            return any_child

        walk(self.exp_tree.invisibleRootItem())
        self._updating_tree = False
        self._refresh_stats()

    def _collect_checked_rels(self) -> list[str]:
        out = []

        def walk(item: QTreeWidgetItem):
            rel = item.data(0, Qt.ItemDataRole.UserRole)
            if rel is not None:
                if item.checkState(0) == Qt.CheckState.Checked:
                    out.append(rel)
                return
            for i in range(item.childCount()):
                walk(item.child(i))

        walk(self.exp_tree.invisibleRootItem())
        return out

    def _on_exp_item_changed(self, item, _col):
        if self._updating_tree:
            return
        # 父级三态由 Qt 的 ItemIsAutoTristate 原生维护（实测程序化改子项
        # 同样向上传播），这里只负责刷新统计
        self._refresh_stats()

    def _refresh_stats(self):
        rels = self._collect_checked_rels()
        self.sel_label.setText(f"已选 {len(rels)} / {len(self._all_rels)} 篇")
        if not rels:
            self.stats_label.setText("未选择任何笔记")
            return
        s = kbb.preview_export(
            [(r, self._contents[r]) for r in rels], source_root=self.target_root,
            kb_config=self.kb_config,
            media_root=(self.target_media_root if self.target_kind == "db" else None),
            include_images=self.chk_images.isChecked(),
            include_assets=self.chk_assets.isChecked(),
            max_asset_bytes=self.spin_mb.value() * 1024 * 1024)
        img, ast = s["images"], s["assets"]

        def fmt(d):
            return f"{d['count']} 个 / {d['bytes'] / 1048576:.1f} MB"

        parts = [f"随包图片 {fmt(img)}", f"随包附件 {fmt(ast)}"]
        if s["excluded"]:
            parts.append(f"按要求排除 {len(s['excluded'])}")
        if s["skipped"]:
            parts.append(f"引用缺失 {len(s['skipped'])}")
        self.stats_label.setText("｜".join(parts))

    def _on_export_clicked(self):
        rels = self._collect_checked_rels()
        if not rels:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "提示", "请先在左侧勾选要导出的笔记")
            return
        container = "db" if self.rb_db.isChecked() else "zip"
        ext = ".db" if container == "db" else ".zip"
        from PySide6.QtWidgets import QFileDialog
        out_path, _ = QFileDialog.getSaveFileName(
            self, "保存合并包", f"合并包{ext}",
            "SQLite 数据库 (*.db)" if container == "db" else "ZIP 文件 (*.zip)")
        if not out_path:
            return
        notes = [(r, self._contents[r]) for r in rels]
        self.export_btn.setEnabled(False)
        from qtui.workers import start_worker
        start_worker(
            bundle_export_job, out_path=out_path, container=container,
            notes=notes,
            source_root=str(self.target_root) if self.target_kind == "fs" else None,
            kb_config=self.kb_config,
            media_root=str(self.target_media_root) if self.target_media_root else None,
            meta_db=str(self.meta_db) if self.meta_db else None,
            include_images=self.chk_images.isChecked(),
            include_assets=self.chk_assets.isChecked(),
            max_asset_bytes=self.spin_mb.value() * 1024 * 1024,
            on_log=self._log,
            on_finished=lambda r: (self.export_btn.setEnabled(True),
                                   self._log("导出完成: {notes} 篇、媒体 {media} 个 → {path}".format(**r))),
            on_error=lambda e: (self.export_btn.setEnabled(True),
                                self._log(f"导出失败: {e}", "ERROR")))

    def _open_note_diff_from_export(self, item, _col):
        rel = item.data(0, Qt.ItemDataRole.UserRole)
        if rel is None:
            return
        old = self._current_target_text(rel)
        DiffDialog(self, rel, old, self._contents.get(rel, "")).exec()

    def _current_target_text(self, rel: str) -> str:
        if self.target_db is not None:
            row = self.target_db.get_note_by_path(rel)
            return row["body"] if row is not None else ""
        fp = kbb._target_notes_dir(self.target_root) / rel
        return fp.read_text(encoding="utf-8") if fp.exists() else ""

    # ── 导入页 ──

    def _build_import_page(self) -> QWidget:
        from qtui.widgets import PathRow

        page = QWidget(self)
        lay = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.imp_path = PathRow(mode="open", dialog_title="选择合并包",
                                file_filter="合并包 (*.zip *.db);;所有文件 (*.*)",
                                placeholder="选择 .zip / .db 合并包，或直接填仓库形式文件夹路径")
        bar.addWidget(self.imp_path, 1)
        self.load_btn = QPushButton("读取并对账")
        self.load_btn.clicked.connect(self._on_load_clicked)
        bar.addWidget(self.load_btn)
        lay.addLayout(bar)

        self.imp_info = QLabel("冲突默认跳过——双击行看差异后自行决定；未点过的条目不会写入。")
        self.imp_info.setWordWrap(True)
        lay.addWidget(self.imp_info)

        self.imp_tree = QTreeWidget()
        self.imp_tree.setHeaderLabels(["路径", "状态", "决定"])
        self.imp_tree.setRootIsDecorated(False)
        self.imp_tree.itemDoubleClicked.connect(self._on_imp_double_click)
        lay.addWidget(self.imp_tree, 1)

        btns = QHBoxLayout()
        self.diff_btn = QPushButton("查看所选差异")
        self.diff_btn.setEnabled(False)
        self.diff_btn.clicked.connect(self._show_imp_diff)
        btns.addWidget(self.diff_btn)
        btns.addStretch(1)
        self.apply_btn = QPushButton("应用到当前库")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        btns.addWidget(self.apply_btn)
        lay.addLayout(btns)
        return page

    def _on_load_clicked(self):
        path = self.imp_path.text()
        if not path:
            return
        from qtui.workers import start_worker
        self.load_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.imp_info.setText("正在读取并对账…")
        tgt_cfg = self.kb_config
        start_worker(
            bundle_load_job, path=path, target_root=self.target_root,
            target_db=self.target_db, target_media_root=self.target_media_root,
            target_kb_config=tgt_cfg,
            on_finished=self._on_loaded,
            on_error=self._on_load_error)

    def _on_load_error(self, msg: str):
        self.load_btn.setEnabled(True)
        self.imp_info.setText(f"读取失败：{msg}")
        self._log(f"读取合并包失败: {msg}", "ERROR")

    def _on_loaded(self, result):
        self.load_btn.setEnabled(True)
        self.bundle, self.plan = result
        for w in self.plan.warnings:
            self._log(w, "WARNING")
        self.imp_tree.clear()
        n_conflict = 0
        for na in self.plan.notes:
            status_txt = _STATUS_CN[na.status]
            if na.hint:
                status_txt += f"（{na.hint}）"
            item = QTreeWidgetItem([na.rel, status_txt, ""])
            item.setData(0, Qt.ItemDataRole.UserRole, ("note", na))
            if na.status == kbb.STATUS_NEW:
                combo = self._make_combo([_ACTION_IMPORT, _ACTION_SKIP], 0)
            elif na.status == kbb.STATUS_CONFLICT:
                combo = self._make_combo([_ACTION_SKIP, _ACTION_OVERWRITE], 0)
                n_conflict += 1
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setText(2, _ACTION_SKIP)
                self.imp_tree.addTopLevelItem(item)
                continue
            self.imp_tree.addTopLevelItem(item)
            self.imp_tree.setItemWidget(item, 2, combo)
        for ma in self.plan.media:
            item = QTreeWidgetItem([f"{ma.category}/{ma.name}",
                                    _MEDIA_CN[ma.status], "-"])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.imp_tree.addTopLevelItem(item)

        summary = f"共 {len(self.plan.notes)} 篇：" + "、".join(
            f"{cn} {sum(1 for x in self.plan.notes if x.status == st)}"
            for st, cn in _STATUS_CN.items())
        if self.plan.warnings:
            summary += f"｜警告 {len(self.plan.warnings)} 条（见日志）"
        self.imp_info.setText(summary + "　— 冲突默认跳过，请逐条确认。")
        self.apply_btn.setEnabled(len(self.plan.notes) > 0)
        self.diff_btn.setEnabled(any(
            na.status != kbb.STATUS_NEW for na in self.plan.notes))

    @staticmethod
    def _make_combo(items: list[str], index: int):
        from PySide6.QtWidgets import QComboBox
        combo = QComboBox()
        combo.addItems(items)
        combo.setCurrentIndex(index)
        return combo

    def _imp_selected_note(self):
        item = self.imp_tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return data[1] if data and data[0] == "note" else None

    def _on_imp_double_click(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[0] == "note" and data[1].status != kbb.STATUS_NEW:
            self._open_imp_diff(data[1])

    def _show_imp_diff(self):
        na = self._imp_selected_note()
        if na is not None and na.status != kbb.STATUS_NEW:
            self._open_imp_diff(na)

    def _open_imp_diff(self, na):
        old = ""
        if self.target_db is not None:
            row = self.target_db.get_note_by_path(na.rel)
            old = row["body"] if row is not None else ""
        elif self.target_root is not None:
            fp = kbb._target_notes_dir(self.target_root) / na.rel
            old = fp.read_text(encoding="utf-8") if fp.exists() else ""
        DiffDialog(self, na.rel, old,
                   na.item.data.decode("utf-8", errors="replace")).exec()

    def _on_apply_clicked(self):
        decisions = {}
        for i in range(self.imp_tree.topLevelItemCount()):
            item = self.imp_tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data or data[0] != "note":
                continue
            na = data[1]
            combo = self.imp_tree.itemWidget(item, 2)
            if combo is None:
                continue
            choice = combo.currentText()
            if choice == _ACTION_OVERWRITE:
                decisions[na.rel] = kbb.ACTION_OVERWRITE
            elif choice == _ACTION_SKIP and na.status == kbb.STATUS_NEW:
                decisions[na.rel] = kbb.ACTION_SKIP
        self.apply_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        from qtui.workers import start_worker
        start_worker(
            bundle_apply_job, bundle=self.bundle, plan=self.plan,
            decisions=decisions, target_root=self.target_root,
            target_db=self.target_db, target_media_root=self.target_media_root,
            target_kb_config=self.kb_config,
            on_finished=self._on_applied,
            on_error=lambda e: (self._log(f"应用失败: {e}", "ERROR"),
                                self.apply_btn.setEnabled(True)))

    def _on_applied(self, result):
        self.applied = True
        self._log("合并包导入完成：新增 {added}，覆盖 {overwritten}，跳过 {skipped}，"
                  "媒体落地 {media_written}，元数据并入 {meta_merged}".format(**result))
        self.imp_info.setText("上次应用结果：新增 {added} / 覆盖 {overwritten} / "
                              "跳过 {skipped}".format(**result))
