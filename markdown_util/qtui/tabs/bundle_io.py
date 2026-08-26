"""合并包导入 / 导出的 UI 编排层。

纯逻辑全部在 :mod:`kb_bundle`，本模块只有三件事：
- 三个 worker job（导出 / 读包分类 / 应用），长任务走 :mod:`qtui.workers` 线程池；
- :class:`ExportOptionsDialog` —— 导出选项与实时统计（规范 §2.5：媒体可选随包）；
- :class:`BundleImportDialog` + :class:`DiffDialog` —— 导入向导：读包对账、
  冲突逐条看 diff 决定（未决定的条目一律跳过落地）、应用后回报结果。
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
    QSpinBox,
    QSplitter,
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


# ── 导出选项 ──

class ExportOptionsDialog(QDialog):
    """媒体是否随包 + 附件大小阈值；统计随开关实时刷新（只 stat 不读字节）。"""

    def __init__(self, parent, *, notes: list, source_root, kb_config: dict,
                 media_root=None):
        super().__init__(parent)
        self.setWindowTitle("导出合并包")
        self.setMinimumWidth(420)
        self._notes = notes
        self._source_root = Path(source_root)
        self._kb_config = dict(kb_config or {})
        self._media_root = Path(media_root) if media_root else None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"将导出 {len(notes)} 篇笔记及其引用的媒体。"))
        self.chk_images = QCheckBox("包含图片")
        self.chk_images.setChecked(True)
        self.chk_assets = QCheckBox("包含附件（大文件可不随包，导入时目标库已有则不受影响）")
        self.chk_assets.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(QLabel("附件单文件超过"))
        self.spin_mb = QSpinBox()
        self.spin_mb.setRange(0, 10240)
        self.spin_mb.setValue(20)
        self.spin_mb.setSuffix(" MB")
        row.addWidget(self.spin_mb)
        row.addWidget(QLabel("MB 则跳过（0 = 不限）"))
        row.addStretch(1)

        lay.addWidget(self.chk_images)
        lay.addWidget(self.chk_assets)
        lay.addLayout(row)
        self.stats_label = QLabel("")
        self.stats_label.setWordWrap(True)
        lay.addWidget(self.stats_label)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("继续…")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        for w in (self.chk_images, self.chk_assets):
            w.toggled.connect(self._refresh_stats)
        self.spin_mb.valueChanged.connect(self._refresh_stats)
        self._refresh_stats()

    def options(self) -> dict:
        return {"include_images": self.chk_images.isChecked(),
                "include_assets": self.chk_assets.isChecked(),
                "max_asset_bytes": self.spin_mb.value() * 1024 * 1024}

    def _refresh_stats(self):
        opt = self.options()
        s = kbb.preview_export(
            self._notes, source_root=self._source_root, kb_config=self._kb_config,
            media_root=self._media_root, include_images=opt["include_images"],
            include_assets=opt["include_assets"],
            max_asset_bytes=opt["max_asset_bytes"])
        img, ast = s["images"], s["assets"]

        def fmt(d):
            return f"{d['count']} 个 / {d['bytes'] / 1048576:.1f} MB"

        parts = [f"笔记 {s['notes']} 篇", f"随包图片 {fmt(img)}", f"随包附件 {fmt(ast)}"]
        if s["excluded"]:
            parts.append(f"按要求排除 {len(s['excluded'])} 个")
        if s["skipped"]:
            parts.append(f"引用缺失 {len(s['skipped'])} 个（详见日志）")
        self.stats_label.setText("｜".join(parts))


# ── 导入向导 ──

_ACTION_IMPORT = "导入"
_ACTION_OVERWRITE = "用包内版本覆盖"
_ACTION_SKIP = "跳过"


class BundleImportDialog(QDialog):
    """选包 → 读包对账 → 冲突逐条决定 → 应用。

    目标在构造时锁定（fs 给 ``target_root``，db 给 ``target_db`` +
    ``target_media_root``）；应用走 worker，成功后置 :attr:`applied`。
    """

    def __init__(self, parent, *, target_kind: str, target_root=None,
                 target_db=None, target_media_root=None, target_kb_config=None,
                 log=None):
        super().__init__(parent)
        self.setWindowTitle("导入合并包")
        self.resize(860, 560)
        self.target_kind = target_kind
        self.target_root = Path(target_root) if target_root else None
        self.target_db = target_db
        self.target_media_root = Path(target_media_root) if target_media_root else None
        self.target_kb_config = target_kb_config
        self._log = log or (lambda *a, **k: None)
        self.bundle = None
        self.plan = None
        self.applied = False

        from qtui.widgets import PathRow

        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.path_row = PathRow(mode="open", dialog_title="选择合并包",
                                file_filter="合并包 (*.zip *.db);;所有文件 (*.*)",
                                placeholder="选择 .zip / .db 合并包，或直接填文件夹路径")
        bar.addWidget(self.path_row, 1)
        self.load_btn = QPushButton("读取并对账")
        self.load_btn.clicked.connect(self._on_load_clicked)
        bar.addWidget(self.load_btn)
        lay.addLayout(bar)

        self.info_label = QLabel("冲突条目默认跳过——双击行查看差异后自行决定；未点过的条目不会写入。")
        self.info_label.setWordWrap(True)
        lay.addWidget(self.info_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["路径", "状态", "决定"])
        self.tree.setRootIsDecorated(False)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self.tree, 1)

        btns = QHBoxLayout()
        self.diff_btn = QPushButton("查看所选差异")
        self.diff_btn.setEnabled(False)
        self.diff_btn.clicked.connect(self._show_selected_diff)
        btns.addWidget(self.diff_btn)
        btns.addStretch(1)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        btns.addWidget(self.apply_btn)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        lay.addLayout(btns)

    # ── 读包 ──

    def _on_load_clicked(self):
        path = self.path_row.text()
        if not path:
            return
        from qtui.workers import start_worker
        self.load_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.info_label.setText("正在读取并对账…")
        start_worker(
            bundle_load_job, path=path, target_root=self.target_root,
            target_db=self.target_db, target_media_root=self.target_media_root,
            target_kb_config=self.target_kb_config,
            on_finished=self._on_loaded,
            on_error=self._on_load_error)

    def _on_load_error(self, msg: str):
        self.load_btn.setEnabled(True)
        self.info_label.setText(f"读取失败：{msg}")
        self._log(f"读取合并包失败: {msg}", "ERROR")

    def _on_loaded(self, result):
        self.load_btn.setEnabled(True)
        self.bundle, self.plan = result
        for w in self.plan.warnings:
            self._log(w, "WARNING")
        self.tree.clear()
        n_conflict = 0
        for na in self.plan.notes:
            status_txt = _STATUS_CN[na.status]
            if na.hint:
                status_txt += f"（{na.hint}）"
            item = QTreeWidgetItem([na.rel, status_txt, ""])
            item.setData(0, Qt.ItemDataRole.UserRole, ("note", na))
            if na.status == kbb.STATUS_NEW:
                combo = self._make_combo([_ACTION_IMPORT, _ACTION_SKIP], 0)
                item.setText(2, "")
            elif na.status == kbb.STATUS_CONFLICT:
                combo = self._make_combo([_ACTION_SKIP, _ACTION_OVERWRITE], 0)
                n_conflict += 1
            else:  # 相同：固定跳过，不可操作
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setText(2, _ACTION_SKIP)
                self.tree.addTopLevelItem(item)
                continue
            self.tree.addTopLevelItem(item)
            self.tree.setItemWidget(item, 2, combo)
        for ma in self.plan.media:  # 媒体三方对账只展示，不可操作
            item = QTreeWidgetItem([f"{ma.category}/{ma.name}",
                                    _MEDIA_CN[ma.status], "-"])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(item)

        total = len(self.plan.notes)
        summary = f"共 {total} 篇笔记：" + "、".join(
            f"{cn} {sum(1 for x in self.plan.notes if x.status == st)}"
            for st, cn in _STATUS_CN.items())
        if self.plan.media:
            summary += f"｜媒体 {len(self.plan.media)} 项"
        if self.plan.warnings:
            summary += f"｜警告 {len(self.plan.warnings)} 条（见日志）"
        self.info_label.setText(summary + "　— 冲突默认跳过，请逐条确认。")
        self.apply_btn.setEnabled(total > 0)
        self.diff_btn.setEnabled(n_conflict > 0 or any(
            na.status == kbb.STATUS_IDENTICAL for na in self.plan.notes))

    @staticmethod
    def _make_combo(items: list[str], index: int):
        from PySide6.QtWidgets import QComboBox
        combo = QComboBox()
        combo.addItems(items)
        combo.setCurrentIndex(index)
        return combo

    # ── diff / 应用 ──

    def _selected_note_action(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return data[1] if data and data[0] == "note" else None

    def _current_target_note_text(self, rel: str) -> str:
        if self.target_db is not None:
            row = self.target_db.get_note_by_path(rel)
            return row["body"] if row is not None else ""
        fp = kbb._target_notes_dir(self.target_root) / rel
        return fp.read_text(encoding="utf-8") if fp.exists() else ""

    def _on_double_click(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != "note":
            return
        self._open_diff(data[1])

    def _show_selected_diff(self):
        na = self._selected_note_action()
        if na is not None and na.status != kbb.STATUS_NEW:
            self._open_diff(na)

    def _open_diff(self, na):
        old = self._current_target_note_text(na.rel)
        new = na.item.data.decode("utf-8", errors="replace")
        DiffDialog(self, na.rel, old, new).exec()

    def _collect_decisions(self) -> dict:
        decisions = {}
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data or data[0] != "note":
                continue
            na = data[1]
            combo = self.tree.itemWidget(item, 2)
            if combo is None:
                continue
            choice = combo.currentText()
            if choice == _ACTION_OVERWRITE:
                decisions[na.rel] = kbb.ACTION_OVERWRITE
            elif choice == _ACTION_SKIP and na.status == kbb.STATUS_NEW:
                decisions[na.rel] = kbb.ACTION_SKIP
        return decisions

    def _on_apply_clicked(self):
        decisions = self._collect_decisions()
        self.apply_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        from qtui.workers import start_worker
        start_worker(
            bundle_apply_job, bundle=self.bundle, plan=self.plan,
            decisions=decisions, target_root=self.target_root,
            target_db=self.target_db, target_media_root=self.target_media_root,
            target_kb_config=self.target_kb_config,
            on_finished=self._on_applied,
            on_error=lambda e: (self._log(f"应用失败: {e}", "ERROR"),
                                self._set_busy(False)))

    def _set_busy(self, busy: bool):
        self.apply_btn.setEnabled(not busy and self.plan is not None)
        self.load_btn.setEnabled(not busy)

    def _on_applied(self, result):
        self.applied = True
        self._log("合并包导入完成：新增 {added}，覆盖 {overwritten}，跳过 {skipped}，"
                  "媒体落地 {media_written}，元数据并入 {meta_merged}".format(**result))
        self.accept()
