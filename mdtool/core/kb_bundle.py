"""仓库形式与合并包核心逻辑（docs/仓库形式与合并包规范.md）。

纯函数层，不依赖任何 Qt/UI 模块。三种来源（文件夹 / bundle.zip / bundle.db）
读成统一的 :class:`Bundle`，分类与应用共用一套代码：

- :func:`plan_export` —— 导出计划：收集笔记引用的本库媒体、按开关/阈值过滤、
  提取 meta 子集（原始文件名是知识库数据，必须随包走）。
- :func:`write_zip_bundle` / :func:`write_db_bundle` —— 把计划写成两种容器。
- :func:`read_zip_bundle` / :func:`read_db_bundle` / :func:`read_folder_bundle`
  —— 读成 Bundle，含格式判别、版本检查、zip slip 防护与逐条 sha256 校验。
- :func:`classify_import` —— 与目标库对账：笔记分 新增/相同/冲突（文本比较前做
  CRLF/BOM 归一化），媒体三方判定 随包/目标已有/缺失。
- :func:`apply_import` —— 按用户逐条决定落地；默认动作永不覆盖既有内容。

设计要点：
- **包内笔记保存原始存储形式**（绝对 http://host:port/...），来源 host/port 在
  包内 kb.json；导入时用 link_resolver 做 URL 前缀替换到目标配置。
- 媒体身份 = ``images|assets/<时间戳名>``；笔记身份 = 笔记树内相对路径。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

from mdtool.core.link_resolver import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    is_external_url,
    iter_link_destinations,
    parse,
    rewrite_markdown,
)

BUNDLE_FORMAT = "mdtool-bundle"
BUNDLE_VERSION = 1

KIND_NOTE = "note"
KIND_IMAGE = "image"
KIND_ASSET = "asset"

# KB 根下不属于笔记树的媒体面（与 qtui.tabs.file_browser._HIDDEN_ENTRIES 同约定）
HIDDEN_ENTRIES = {"images", "assets", "meta.db"}

DEFAULT_KB_CONFIG: dict = {
    "format_version": 1,
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "images_subdir": "images",
    "assets_subdir": "assets",
}

MAX_ASSET_BYTES_DEFAULT = 20 * 1024 * 1024


class BundleFormatError(ValueError):
    """不是合并包 / 版本不支持 / 清单损坏——应整体拒绝导入。"""


# ── 数据结构 ──

@dataclass(frozen=True)
class BundleItem:
    """一个随包文件：path 为包内规范路径（markdown/** 或 images|assets/*）。"""

    path: str
    kind: str  # KIND_NOTE / KIND_IMAGE / KIND_ASSET
    data: bytes
    sha256: str
    size: int

    @staticmethod
    def from_bytes(path: str, kind: str, data: bytes) -> "BundleItem":
        return BundleItem(path, kind, data, hashlib.sha256(data).hexdigest(), len(data))


@dataclass
class ExportPlan:
    """导出计划：容器无关，write_*_bundle 只认它。"""

    notes: list[tuple[str, str]]  # (笔记树相对路径, 原始内容)
    media: list[BundleItem]       # 随包媒体
    skipped: list[str]            # 引用了但源库找不到的链接原文
    excluded: list[dict]          # 因开关/阈值未随包 [{path, size}]
    meta_rows: dict               # {"images": [row...], "assets": [row...]} 子集
    kb_config: dict               # 来源配置（写入包内 kb.json）

    @property
    def items(self) -> list[BundleItem]:
        out = [BundleItem.from_bytes(f"markdown/{rel}", KIND_NOTE, c.encode("utf-8"))
               for rel, c in self.notes]
        out.extend(self.media)
        return sorted(out, key=lambda i: i.path)


@dataclass
class Bundle:
    """统一读取结果。folder 来源没有 manifest（sha 现算）。"""

    kb_config: dict
    note_items: list[BundleItem]
    media_items: list[BundleItem]
    meta_rows: dict
    manifest: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)


# 笔记动作与状态
STATUS_NEW = "new"
STATUS_IDENTICAL = "identical"
STATUS_CONFLICT = "conflict"
ACTION_ADD = "add"              # 默认：新增
ACTION_SKIP = "skip"            # 默认：相同跳过；冲突未决定也跳过
ACTION_OVERWRITE = "overwrite"  # 用户看 diff 后逐条选择

# 媒体状态
MEDIA_BUNDLED = "bundled"      # 在包里 → 落地
MEDIA_TARGET_HAS = "target_has"  # 目标同名已有 → 不动
MEDIA_MISSING = "missing"      # 两边都没有 → 警告


@dataclass
class NoteAction:
    item: BundleItem
    status: str
    rel: str  # 笔记树内相对路径（去 markdown/ 前缀）
    hint: Optional[str] = None  # 如"内容与 X 相同"的移动/复制提示


@dataclass
class MediaAction:
    category: str  # "images" | "assets"
    name: str
    status: str


@dataclass
class ImportPlan:
    notes: list[NoteAction]
    media: list[MediaAction]
    warnings: list[str]


# ── kb.json ──

def load_kb_config(root: Path) -> dict:
    """读 KB 根的 kb.json；缺文件/坏 JSON/缺字段一律按默认兜底（存量库零迁移）。"""
    cfg = dict(DEFAULT_KB_CONFIG)
    try:
        raw = (Path(root) / "kb.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            cfg.update({k: data[k] for k in cfg if k in data})
    except (OSError, ValueError):
        pass
    try:
        cfg["port"] = int(cfg["port"])
    except (TypeError, ValueError):
        cfg["port"] = DEFAULT_PORT
    return cfg


def save_kb_config(root: Path, cfg: dict) -> None:
    payload = {k: cfg.get(k, d) for k, d in DEFAULT_KB_CONFIG.items()}
    (Path(root) / "kb.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_kb_config(root: Path) -> dict:
    """有则读，无则补写默认值后返回（首次导出时顺手让仓库形式自描述）。"""
    cfg = load_kb_config(root)
    if not (Path(root) / "kb.json").exists():
        save_kb_config(root, cfg)
    return cfg


def resolve_notes_dir(root: Path) -> Path:
    """笔记树目录：markdown/ 优先，退 notes/（双名兼容），再退根目录本身。"""
    root = Path(root)
    for name in ("markdown", "notes"):
        p = root / name
        if p.is_dir():
            return p
    return root


def note_rel(path: Path, kb_root: Path) -> str:
    """文件在笔记树内的身份路径（相对 markdown|notes|根 的解析结果）。

    合并包的身份约定基于此而非 KB 根相对路径——否则新布局下会产出
    ``markdown/markdown/x.md`` 双前缀。
    """
    return Path(path).relative_to(resolve_notes_dir(kb_root)).as_posix()


# ── 导出计划 ──

def preview_export(
    notes: list[tuple[str, str]],
    *,
    source_root: Optional[Path] = None,
    kb_config: Optional[dict] = None,
    media_root: Optional[Path] = None,
    include_images: bool = True,
    include_assets: bool = True,
    max_asset_bytes: int = MAX_ASSET_BYTES_DEFAULT,
) -> dict:
    """与 :func:`plan_export` 同一套判定，但只 stat 不读字节——供导出选项
    对话框随开关切换实时刷新统计（长库也不卡）。参数语义与 plan_export
    一致：散装给 source_root，db 给 media_root，两者必有其一。"""
    base = Path(media_root) if media_root else (
        Path(source_root) if source_root else None)
    if base is None:
        raise ValueError("preview_export 需要 source_root 或 media_root 之一")
    cfg = kb_config or (load_kb_config(Path(source_root)) if source_root
                        else dict(DEFAULT_KB_CONFIG))

    wanted: set[tuple[str, str]] = set()
    skipped: list[str] = []
    for _rel, content in notes:
        for dest, cat, name in _collect_media_refs(content, cfg):
            wanted.add((cat, name))
            ref_file = base / cfg["images_subdir" if cat == "images" else "assets_subdir"] / name
            if not ref_file.is_file():
                skipped.append(dest)

    out = {"notes": len(notes),
           "images": {"count": 0, "bytes": 0},
           "assets": {"count": 0, "bytes": 0},
           "skipped": sorted(dict.fromkeys(skipped)),
           "excluded": []}
    for cat, name in sorted(wanted):
        sub = cfg[f"{cat}_subdir"]
        fp = base / sub / name
        if not fp.is_file():
            continue
        size = fp.stat().st_size
        gate_ok = (cat == "images" and include_images) or (
            cat == "assets" and include_assets
            and (not max_asset_bytes or size <= max_asset_bytes))
        if gate_ok:
            out[cat]["count"] += 1
            out[cat]["bytes"] += size
        else:
            out["excluded"].append({"path": f"{sub}/{name}", "size": size})
    return out


def diff_unified(old_text: str, new_text: str, *, context: int = 3) -> list[tuple[str, str]]:
    """统一 diff 的行列表 ``[(tag, line)]``，tag ∈ ``{' ', '-', '+', '@'}``。

    比较前经 :func:`normalize_text` 归一化，换行符差异不会淹没真实改动；
    UI 层按 tag 着色即可，无需自带 Qt 依赖。
    """
    import difflib

    a = normalize_text(old_text).splitlines()
    b = normalize_text(new_text).splitlines()
    out: list[tuple[str, str]] = []
    for line in difflib.unified_diff(a, b, lineterm="", n=context):
        if line.startswith("@@"):
            tag = "@"
        elif line[:1] in (" ", "+", "-"):
            tag = line[0]
        else:  # ---/+++ 文件头
            tag = " "
        out.append((tag, line))
    return out


def migrate_meta_into_db(notes_db_path: Path, old_meta_path: Path) -> int:
    """老布局 meta.db 并入 notes.db 的 images/assets 表（规范 §4 一次性迁移）。

    返回并入行数；旧文件由调用方改名留底。行不存在时返回 0 且不动目标库。
    """
    rows = _meta_rows_from_bytes(Path(old_meta_path).read_bytes())
    if not any(rows.values()):
        return 0
    conn = sqlite3.connect(str(notes_db_path))
    try:
        conn.executescript(_META_SCHEMA)
        return _merge_meta_into(conn, rows)
    finally:
        conn.close()


def _collect_media_refs(content: str, kb_config: dict) -> list[tuple[str, str, str]]:
    """扫描一篇笔记里的本库媒体引用 → [(dest原文, category, name)]。"""
    out = []
    seen = set()
    for _s, _e, dest, _img in iter_link_destinations(content):
        ref = parse(dest, host=kb_config["host"], port=kb_config["port"])
        if ref is None:
            continue  # 外链 / 相对路径旧链接：原样透传，不入包（规范 §6 已知限制）
        key = (ref.category, ref.name)
        if key not in seen:
            seen.add(key)
            out.append((dest, ref.category, ref.name))
    return out


def plan_export(
    notes: list[tuple[str, str]],
    *,
    source_root: Optional[Path] = None,
    kb_config: Optional[dict] = None,
    media_root: Optional[Path] = None,
    meta_db: Optional[Path] = None,
    include_images: bool = True,
    include_assets: bool = True,
    max_asset_bytes: int = MAX_ASSET_BYTES_DEFAULT,
) -> ExportPlan:
    """构建导出计划。

    ``notes``：[(笔记树相对路径, 内容)]；媒体从 ``media_root/<子目录>`` 取文件。
    散装库传 ``source_root``（媒体与 meta 都在根下）；db 工作库传
    ``media_root``（图床位置）+ ``meta_db``（notes.db，元数据已并入其中），
    两者必有其一。大附件可不随包——目标可能同名已有（三方对账兜底）。
    """
    base = Path(media_root) if media_root else (
        Path(source_root) if source_root else None)
    if base is None:
        raise ValueError("plan_export 需要 source_root 或 media_root 之一")
    cfg = kb_config or (load_kb_config(Path(source_root)) if source_root
                        else dict(DEFAULT_KB_CONFIG))
    meta_path = Path(meta_db) if meta_db else (
        Path(source_root) / "meta.db" if source_root else base / "meta.db")

    wanted: dict[tuple[str, str], list[str]] = {}
    for _rel, content in notes:
        for dest, cat, name in _collect_media_refs(content, cfg):
            wanted.setdefault((cat, name), []).append(dest)

    subdirs = {"images": cfg["images_subdir"], "assets": cfg["assets_subdir"]}
    media: list[BundleItem] = []
    skipped: list[str] = []
    excluded: list[dict] = []
    included_names: dict[str, set[str]] = {"images": set(), "assets": set()}

    for (cat, name), dests in sorted(wanted.items()):
        fp = base / subdirs[cat] / name
        if not fp.is_file():
            skipped.extend(dict.fromkeys(dests))
            continue
        size = fp.stat().st_size
        if cat == "images" and not include_images:
            excluded.append({"path": f"{subdirs[cat]}/{name}", "size": size})
            continue
        if cat == "assets":
            if not include_assets:
                excluded.append({"path": f"{subdirs[cat]}/{name}", "size": size})
                continue
            if max_asset_bytes and size > max_asset_bytes:
                excluded.append({"path": f"{subdirs[cat]}/{name}", "size": size})
                continue
        included_names[cat].add(name)
        media.append(BundleItem.from_bytes(
            f"{subdirs[cat]}/{name}", KIND_IMAGE if cat == "images" else KIND_ASSET,
            fp.read_bytes()))

    return ExportPlan(
        notes=list(notes), media=media, skipped=skipped, excluded=excluded,
        meta_rows=_read_meta_subset(meta_path, included_names),
        kb_config=dict(cfg),
    )


# ── meta.db 子集 ──

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    timestamp_name TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    mime TEXT NOT NULL,
    upload_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    timestamp_name TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    mime TEXT NOT NULL,
    upload_time TEXT NOT NULL
);
"""


def _read_meta_subset(meta_path: Path, names: dict[str, set[str]]) -> dict:
    """从源 meta.db 抽出随包媒体的行；文件不存在或没有媒体 → 空集。"""
    out: dict[str, list[dict]] = {"images": [], "assets": []}
    if not meta_path.is_file() or not any(names.values()):
        return out
    conn = sqlite3.connect(str(meta_path))
    conn.row_factory = sqlite3.Row
    try:
        for table, keys in names.items():
            if not keys:
                continue
            marks = ",".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT timestamp_name, original_name, size, mime, upload_time "
                f"FROM {table} WHERE timestamp_name IN ({marks})", tuple(keys)).fetchall()
            out[table] = [dict(r) for r in rows]
    except sqlite3.Error:
        pass  # 坏 meta.db 不阻断导出，只是不带原始名
    finally:
        conn.close()
    return out


def _merge_meta_into(conn: sqlite3.Connection, meta_rows: dict) -> int:
    """把行 INSERT OR REPLACE 进一个已含 images/assets 表的连接，返回行数。"""
    n = 0
    for table in ("images", "assets"):
        for r in meta_rows.get(table, []):
            conn.execute(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?)",
                (r["timestamp_name"], r["original_name"], r["size"], r["mime"],
                 r["upload_time"]))
            n += 1
    conn.commit()
    return n


def _meta_db_bytes(meta_rows: dict) -> bytes:
    """子集行 → 一个独立 meta.db 文件的字节（放进 zip 包）。"""
    src = sqlite3.connect(":memory:")
    src.executescript(_META_SCHEMA)
    _merge_meta_into(src, meta_rows)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp_name = tf.name
    try:
        dst = sqlite3.connect(tmp_name)
        try:
            src.backup(dst)
        finally:
            dst.close()
        src.close()
        return Path(tmp_name).read_bytes()
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _meta_rows_from_bytes(data: bytes) -> dict:
    """meta.db 字节 → 行字典；坏库返回空集。"""
    out: dict[str, list[dict]] = {"images": [], "assets": []}
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tf.write(data)
        tmp_name = tf.name
    try:
        conn = sqlite3.connect(tmp_name)
        conn.row_factory = sqlite3.Row
        try:
            for table in ("images", "assets"):
                try:
                    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                    out[table] = [dict(r) for r in rows]
                except sqlite3.Error:
                    continue
        finally:
            conn.close()
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return out


# ── manifest ──

def build_manifest(plan: ExportPlan) -> dict:
    return {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": [
            {"path": i.path, "kind": i.kind, "sha256": i.sha256, "size": i.size}
            for i in plan.items
        ],
    }


def _validate_manifest(manifest: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get("format") != BUNDLE_FORMAT:
        raise BundleFormatError("不是 mdtool 合并包（format 不符）")
    version = manifest.get("version")
    if not isinstance(version, int) or version > BUNDLE_VERSION:
        raise BundleFormatError(f"合并包版本不受支持: {version!r}（请升级程序）")


def _is_safe_member(path: str) -> bool:
    """zip slip 防护：拒绝绝对路径、盘符、反斜杠与 .. 上跳。"""
    if not path or "\\" in path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    return ".." not in parts and not PurePosixPath(path).is_absolute()


def _check_sha(item: BundleItem) -> Optional[str]:
    actual = hashlib.sha256(item.data).hexdigest()
    return None if actual == item.sha256 else actual


# ── 写包 ──

def write_zip_bundle(zip_path: Path, plan: ExportPlan) -> None:
    """计划 → bundle.zip（zip 根 = 仓库形式根）。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("kb.json", json.dumps(plan.kb_config, ensure_ascii=False, indent=2))
        zf.writestr("manifest.json", json.dumps(build_manifest(plan), ensure_ascii=False, indent=2))
        for item in plan.items:
            zf.writestr(item.path, item.data)
        if any(plan.meta_rows.values()):
            zf.writestr("meta.db", _meta_db_bytes(plan.meta_rows))


_BUNDLE_TABLES = """
CREATE TABLE IF NOT EXISTS media (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    data BLOB NOT NULL
);
"""


def write_db_bundle(db_path: Path, plan: ExportPlan) -> None:
    """计划 → bundle.db：notes 表复用工作库 schema，媒体进 blob 表，
    manifest/kb.json 进 bundle_meta kv 表（该表的存在性就是"这是合并包"的判别标志）。"""
    from mdtool.core.server.notes_db import NotesDB

    db_path = Path(db_path)
    db = NotesDB(db_path)
    try:
        for rel, content in plan.notes:
            db.upsert_note(rel, content)
    finally:
        db.close()

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_META_SCHEMA)
        conn.execute(_BUNDLE_TABLES)
        conn.execute("CREATE TABLE IF NOT EXISTS bundle_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM images")
        conn.execute("DELETE FROM assets")
        for item in plan.media:
            conn.execute("INSERT OR REPLACE INTO media VALUES (?,?,?,?)",
                         (item.path, item.sha256, item.size, item.data))
        _merge_meta_into(conn, plan.meta_rows)
        conn.execute("INSERT OR REPLACE INTO bundle_meta VALUES ('manifest', ?)",
                     (json.dumps(build_manifest(plan), ensure_ascii=False),))
        conn.execute("INSERT OR REPLACE INTO bundle_meta VALUES ('kb_config', ?)",
                     (json.dumps(plan.kb_config, ensure_ascii=False),))
        conn.commit()
    finally:
        conn.close()


# ── 读包 ──

def _item_from_manifest_entry(entry: dict, data: bytes) -> BundleItem:
    return BundleItem(entry["path"], entry["kind"], data,
                      entry["sha256"], entry.get("size", len(data)))


def _split_items(items: list[BundleItem]) -> tuple[list[BundleItem], list[BundleItem]]:
    notes = [i for i in items if i.kind == KIND_NOTE]
    media = [i for i in items if i.kind != KIND_NOTE]
    return notes, media


def read_zip_bundle(zip_path: Path) -> Bundle:
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for required in ("manifest.json", "kb.json"):
            if required not in names:
                raise BundleFormatError(f"缺少 {required}，不是合并包")
        for name in names:
            if name.endswith("/"):
                continue
            if not _is_safe_member(name):
                raise BundleFormatError(f"包内出现不安全路径: {name}")
        kb_config = json.loads(zf.read("kb.json").decode("utf-8"))
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        _validate_manifest(manifest)

        bundle = Bundle(kb_config=kb_config, note_items=[], media_items=[],
                        meta_rows={"images": [], "assets": []}, manifest=manifest)
        for entry in manifest["items"]:
            path = entry["path"]
            if not _is_safe_member(path):
                bundle.warnings.append(f"清单条目路径不安全，已跳过: {path}")
                continue
            if path not in names:
                bundle.warnings.append(f"清单声明但包内缺失，已跳过: {path}")
                continue
            item = _item_from_manifest_entry(entry, zf.read(path))
            bad = _check_sha(item)
            if bad:
                bundle.warnings.append(
                    f"哈希不符已拒收: {path}（清单 {entry['sha256'][:12]}… 实际 {bad[:12]}…）")
                continue
            note_items, media_items = _split_items([item])
            bundle.note_items.extend(note_items)
            bundle.media_items.extend(media_items)

        if "meta.db" in names:
            bundle.meta_rows = _meta_rows_from_bytes(zf.read("meta.db"))
        return bundle


def read_db_bundle(db_path: Path) -> Bundle:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        has = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bundle_meta'"
        ).fetchone()
        if has is None:
            raise BundleFormatError("不是合并包（无 bundle_meta 表，可能是普通工作库）")
        kv = {r["key"]: r["value"] for r in
              conn.execute("SELECT key, value FROM bundle_meta")}
        kb_config = json.loads(kv["kb_config"])
        manifest = json.loads(kv["manifest"])
        _validate_manifest(manifest)

        bundle = Bundle(kb_config=kb_config, note_items=[], media_items=[],
                        meta_rows={"images": [], "assets": []}, manifest=manifest)
        by_path = {e["path"]: e for e in manifest["items"]}
        for row in conn.execute("SELECT path, name FROM notes ORDER BY path"):
            body_row = conn.execute("SELECT body FROM notes WHERE path=?", (row["path"],)).fetchone()
            data = body_row["body"].encode("utf-8")
            entry = by_path.get(f"markdown/{row['path']}")
            if entry is None:
                bundle.warnings.append(f"清单未声明的笔记已跳过: {row['path']}")
                continue
            item = _item_from_manifest_entry(entry, data)
            bad = _check_sha(item)
            if bad:
                bundle.warnings.append(f"哈希不符已拒收: {entry['path']}")
                continue
            bundle.note_items.append(item)
        for row in conn.execute("SELECT path, sha256, size, data FROM media ORDER BY path"):
            item = BundleItem(row["path"],
                              KIND_IMAGE if row["path"].startswith("images/") else KIND_ASSET,
                              row["data"], row["sha256"], row["size"])
            bad = _check_sha(item)
            if bad:
                bundle.warnings.append(f"哈希不符已拒收: {item.path}")
                continue
            bundle.media_items.append(item)
        for table in ("images", "assets"):
            try:
                bundle.meta_rows[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
            except sqlite3.Error:
                pass
        return bundle
    finally:
        conn.close()


def read_folder_bundle(root: Path) -> Bundle:
    """把一个仓库形式的工作库当来源读出来（无 manifest，sha 现算）。"""
    root = Path(root)
    cfg = load_kb_config(root)
    notes_dir = resolve_notes_dir(root)
    bundle = Bundle(kb_config=cfg, note_items=[], media_items=[],
                    meta_rows={"images": [], "assets": []}, manifest=None)

    def _walk(dir_path: Path, prefix: str):
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if entry.name.lower() in HIDDEN_ENTRIES or entry.name == "kb.json":
                continue
            if entry.is_dir():
                _walk(entry, f"{prefix}{entry.name}/")
            elif entry.suffix.lower() == ".md":
                bundle.note_items.append(BundleItem.from_bytes(
                    f"markdown/{prefix}{entry.name}", KIND_NOTE, entry.read_bytes()))

    if notes_dir != root:
        _walk(notes_dir, "")
    else:  # 根目录本身是笔记树：只收根下的散 .md，不再递归媒体面
        for entry in sorted(root.glob("*.md")):
            bundle.note_items.append(BundleItem.from_bytes(
                f"markdown/{entry.name}", KIND_NOTE, entry.read_bytes()))

    for cat, kind in (("images", KIND_IMAGE), ("assets", KIND_ASSET)):
        d = root / cfg[f"{cat}_subdir"]
        if d.is_dir():
            for fp in sorted(d.iterdir()):
                # 结构性文件与日志不是知识内容（真实库里 meta.db/server.log
                # 可能因历史媒体根配置落在 assets/ 下）
                if not fp.is_file():
                    continue
                if fp.name.lower() in {"meta.db", "notes.db"} or fp.suffix.lower() == ".log":
                    continue
                bundle.media_items.append(BundleItem.from_bytes(
                    f"{cat}/{fp.name}", kind, fp.read_bytes()))
    bundle.meta_rows = _read_meta_subset(
        root / "meta.db", {"images": {i.path.split("/", 1)[1] for i in bundle.media_items
                                      if i.path.startswith("images/")},
                           "assets": {i.path.split("/", 1)[1] for i in bundle.media_items
                                      if i.path.startswith("assets/")}})
    return bundle


# ── 导入分类 ──

def normalize_text(data) -> str:
    """CRLF/BOM 归一化后的文本，用于"内容是否相同"判定（落地仍写字节原样）。
    接受 bytes 或 str，方便测试与调用方直接比较。"""
    s = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else data
    if s.startswith("\ufeff"):
        s = s[1:]
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _target_notes_dir(root: Path) -> Path:
    """写入目标：沿用目标现有笔记树目录；根本身已有散 .md 则根即笔记树
    （存量散装布局，如 C:\\sw\\note）；都没有则用 markdown/（新规范默认）。"""
    root = Path(root)
    for name in ("markdown", "notes"):
        if (root / name).is_dir():
            return root / name
    if any(root.glob("*.md")):
        return root
    return root / "markdown"


def classify_import(
    bundle: Bundle,
    *,
    target_root: Optional[Path] = None,
    target_db=None,
    target_media_root: Optional[Path] = None,
    target_kb_config: Optional[dict] = None,
) -> ImportPlan:
    """与目标对账（不落盘）。

    散装目标给 ``target_root``（媒体根 = 根）；db 目标给 ``target_db``（NotesDB 实例）
    和 ``target_media_root``。冲突 = 归一化后仍不同的同路径笔记。
    """
    tgt_cfg = dict(target_kb_config or (load_kb_config(target_root) if target_root
                                        else DEFAULT_KB_CONFIG))
    notes_dir = _target_notes_dir(Path(target_root)) if target_root else None
    media_base = target_media_root if target_media_root is not None else (
        Path(target_root) if target_root else None)

    def _media_dir(cat: str) -> Optional[Path]:
        # db 目标可能不关心媒体落地位置（只做对账展示），此时视为无从存在
        return (media_base / tgt_cfg[f"{cat}_subdir"]) if media_base else None

    # 目标现有笔记按 casefold 建索引（Windows 大小写不敏感）
    existing: dict[str, tuple[str, bytes]] = {}  # casefold_rel -> (真实rel, body字节)
    if target_root is not None and notes_dir is not None and notes_dir.is_dir():
        for fp in notes_dir.rglob("*.md"):
            rel = fp.relative_to(notes_dir).as_posix()
            existing.setdefault(rel.casefold(), (rel, fp.read_bytes()))
    elif target_db is not None:
        for row in target_db.list_all(include_body=True):
            existing.setdefault(row["path"].casefold(), (row["path"], row["body"].encode("utf-8")))

    plan = ImportPlan(notes=[], media=[], warnings=list(bundle.warnings))
    # 移动提示按归一化内容判定（与"相同"同一标准），换行符差异不干扰；
    # 映射方向 = 内容摘要 → 已存在路径
    sha_to_rel: dict[str, str] = {
        hashlib.sha256(normalize_text(data).encode("utf-8")).hexdigest(): rel
        for rel, data in existing.values()}

    def _norm_sha(data) -> str:
        return hashlib.sha256(normalize_text(data).encode("utf-8")).hexdigest()

    for item in bundle.note_items:
        rel = item.path[len("markdown/"):]
        hit = existing.get(rel.casefold())
        hint = None
        if hit is None:
            status = STATUS_NEW
            same = sha_to_rel.get(_norm_sha(item.data))
            if same is not None:
                hint = f"内容与 {same} 相同（可能是移动/复制）"
        elif normalize_text(hit[1]) == normalize_text(item.data):
            status = STATUS_IDENTICAL
        else:
            status = STATUS_CONFLICT
        plan.notes.append(NoteAction(item=item, status=status, rel=rel, hint=hint))

    bundled_keys = {(i.path.split("/", 1)[0], i.path.split("/", 1)[1])
                    for i in bundle.media_items}
    referenced: set[tuple[str, str]] = set(bundled_keys)
    for item in bundle.note_items:
        text = item.data.decode("utf-8", errors="replace")
        for _d, cat, name in _collect_media_refs(text, bundle.kb_config):
            referenced.add((cat, name))
    for cat, name in sorted(referenced):
        if (cat, name) in bundled_keys:
            status = MEDIA_BUNDLED
        else:
            d = _media_dir(cat)
            status = (MEDIA_TARGET_HAS if d is not None and d.is_dir()
                      and (d / name).is_file() else MEDIA_MISSING)
            if status == MEDIA_MISSING:
                plan.warnings.append(f"媒体缺失: {cat}/{name}（未随包且目标库没有）")
        plan.media.append(MediaAction(category=cat, name=name, status=status))
    return plan


# ── 应用 ──

def rewrite_to_target(body: str, src_cfg: dict, tgt_cfg: dict) -> str:
    """把正文里指向来源 host:port 的媒体 URL 前缀替换为目标配置；其余原样。"""
    src = (str(src_cfg.get("host", DEFAULT_HOST)), int(src_cfg.get("port", DEFAULT_PORT)))
    tgt = (str(tgt_cfg.get("host", DEFAULT_HOST)), int(tgt_cfg.get("port", DEFAULT_PORT)))
    if src == tgt:
        return body
    return rewrite_markdown(body, target="phone",
                            base=f"http://{tgt[0]}:{tgt[1]}",
                            host=src[0], port=src[1])


def apply_import(
    plan: ImportPlan,
    *,
    target_root: Optional[Path] = None,
    target_db=None,
    target_media_root: Optional[Path] = None,
    target_kb_config: Optional[dict] = None,
    bundle: Optional[Bundle] = None,
    decisions: Optional[dict[str, str]] = None,
) -> dict:
    """按分类落地。``decisions``: {笔记rel: ACTION_*} 覆盖默认动作；
    默认动作 = 新增 add / 相同 skip / 冲突 skip（绝不静默覆盖，规范 §3.3）。"""
    if target_root is None and target_db is None:
        raise ValueError("散装目标需要 target_root，db 目标需要 target_db")
    if bundle is None:
        raise ValueError("需要 bundle 以取得来源配置与媒体字节")
    tgt_cfg = dict(target_kb_config or (load_kb_config(target_root) if target_root
                                        else DEFAULT_KB_CONFIG))
    decisions = decisions or {}
    src_cfg = bundle.kb_config
    media_base = Path(target_media_root) if target_media_root else (
        Path(target_root) if target_root else None)
    result = {"added": 0, "overwritten": 0, "skipped": 0,
              "media_written": 0, "meta_merged": 0}

    notes_dir = _target_notes_dir(Path(target_root)) if target_root else None
    for na in plan.notes:
        action = decisions.get(na.rel, {
            STATUS_NEW: ACTION_ADD,
            STATUS_IDENTICAL: ACTION_SKIP,
            STATUS_CONFLICT: ACTION_SKIP,
        }[na.status])
        if action != ACTION_OVERWRITE and action != ACTION_ADD:
            result["skipped"] += 1
            continue
        body = rewrite_to_target(na.item.data.decode("utf-8"), src_cfg, tgt_cfg)
        if target_db is not None:
            existed = target_db.get_note_by_path(na.rel) is not None
            target_db.upsert_note(na.rel, body)
            result["overwritten" if existed else "added"] += 1
        else:
            fp = notes_dir / na.rel
            existed = fp.exists()
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(body, encoding="utf-8", newline="")
            result["overwritten" if existed else "added"] += 1

    media_dirs = {c: (media_base / tgt_cfg[f"{c}_subdir"]) if media_base else None
                  for c in ("images", "assets")}
    bundled = {i.path: i for i in bundle.media_items}
    for ma in plan.media:
        if ma.status != MEDIA_BUNDLED:
            continue
        item = bundled[f"{ma.category}/{ma.name}"]
        d = media_dirs[ma.category]
        if d is None:
            continue
        d.mkdir(parents=True, exist_ok=True)
        (d / ma.name).write_bytes(item.data)
        result["media_written"] += 1

    meta_target = target_db.db_path if target_db is not None else (
        Path(target_root) / "meta.db" if target_root else None)
    if meta_target is not None and any(bundle.meta_rows.values()):
        conn = sqlite3.connect(str(meta_target))
        try:
            conn.executescript(_META_SCHEMA)
            result["meta_merged"] = _merge_meta_into(conn, bundle.meta_rows)
        finally:
            conn.close()
    return result
