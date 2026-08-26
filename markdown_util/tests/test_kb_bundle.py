"""kb_bundle 纯逻辑测试：导出计划、双容器读写、导入分类与应用。

对应 docs/仓库形式与合并包规范.md §2（包格式）与 §3（导入合并语义）。
"""

import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb_bundle import (
    BundleFormatError,
    MEDIA_MISSING,
    MEDIA_TARGET_HAS,
    STATUS_CONFLICT,
    STATUS_IDENTICAL,
    STATUS_NEW,
    apply_import,
    classify_import,
    diff_unified,
    load_kb_config,
    migrate_meta_into_db,
    normalize_text,
    plan_export,
    preview_export,
    read_db_bundle,
    read_folder_bundle,
    read_zip_bundle,
    resolve_notes_dir,
    save_kb_config,
    write_db_bundle,
    write_zip_bundle,
)

IMG_NAME = "image-20201215174726729.png"
AST_NAME = "doc-20260101000000000.pdf"
IMG = f"http://127.0.0.1:8765/images/{IMG_NAME}"
AST = f"http://127.0.0.1:8765/assets/{AST_NAME}"


def _make_meta_db(path: Path, rows: dict):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE images (timestamp_name TEXT PRIMARY KEY, original_name TEXT NOT NULL,
            size INTEGER NOT NULL, mime TEXT NOT NULL, upload_time TEXT NOT NULL);
        CREATE TABLE assets (timestamp_name TEXT PRIMARY KEY, original_name TEXT NOT NULL,
            size INTEGER NOT NULL, mime TEXT NOT NULL, upload_time TEXT NOT NULL);
        """)
    for ts, orig in rows.items():
        table = "images" if ts.startswith("image-") else "assets"
        conn.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?)",
                     (ts, orig, 1, "application/octet-stream", "2026-01-01T00:00:00Z"))
    conn.commit()
    conn.close()


@pytest.fixture()
def src_kb(tmp_path):
    """仓库形式来源库：markdown/sub/a.md 引用一图一附件，媒体与 meta 齐备。"""
    root = tmp_path / "src"
    (root / "markdown" / "sub").mkdir(parents=True)
    (root / "images").mkdir()
    (root / "assets").mkdir()
    (root / "markdown" / "sub" / "a.md").write_text(
        f"# A\n\n![图]({IMG})\n\n[附件]({AST})\n", encoding="utf-8")
    (root / "images" / IMG_NAME).write_bytes(b"PNG1")
    (root / "assets" / AST_NAME).write_bytes(b"PDF-" + b"x" * 64)
    _make_meta_db(root / "meta.db", {IMG_NAME: "截图.png", AST_NAME: "用户手册.pdf"})
    return root


def _plan(src_root, **kw):
    notes = [(p.relative_to(src_root / "markdown").as_posix(),
              p.read_text(encoding="utf-8"))
             for p in sorted((src_root / "markdown").rglob("*.md"))]
    return plan_export(notes, source_root=src_root, **kw)


# ── kb.json 与目录约定 ──

def test_kb_config_defaults_on_missing_or_broken(tmp_path):
    assert load_kb_config(tmp_path)["port"] == 8765
    (tmp_path / "kb.json").write_text("{broken", encoding="utf-8")
    assert load_kb_config(tmp_path)["host"] == "127.0.0.1"


def test_kb_config_roundtrip(tmp_path):
    cfg = {"host": "10.0.0.5", "port": "9999", "images_subdir": "img",
           "assets_subdir": "att"}
    save_kb_config(tmp_path, cfg)
    loaded = load_kb_config(tmp_path)
    assert loaded["port"] == 9999 and loaded["images_subdir"] == "img"


def test_resolve_notes_dir_preference(tmp_path):
    assert resolve_notes_dir(tmp_path) == tmp_path  # 都没有 → 根本身
    (tmp_path / "notes").mkdir()
    assert resolve_notes_dir(tmp_path) == tmp_path / "notes"
    (tmp_path / "markdown").mkdir()
    assert resolve_notes_dir(tmp_path) == tmp_path / "markdown"


def test_note_rel_uses_notes_tree_not_kb_root(tmp_path):
    """新布局下身份相对笔记树，避免 markdown/ 双前缀。"""
    from kb_bundle import note_rel
    root = tmp_path / "kb"
    (root / "markdown" / "sub").mkdir(parents=True)
    p = root / "markdown" / "sub" / "a.md"
    assert note_rel(p, root) == "sub/a.md"
    loose = tmp_path / "loose" / "b.md"
    loose.parent.mkdir()
    assert note_rel(loose, loose.parent) == "b.md"  # 根即笔记树的存量布局


# ── 导出计划 ──

def test_plan_export_collect_dedupe_skip(src_kb):
    plan = _plan(src_kb)
    # 两类媒体各一个；重复引用天然去重
    assert [i.path for i in plan.media] == [f"assets/{AST_NAME}", f"images/{IMG_NAME}"]
    # 引用了但库里没有的链接进 skipped
    plan2 = plan_export([("a.md", f"![x](http://127.0.0.1:8765/images/nope.png)")],
                        source_root=src_kb)
    assert plan2.skipped == ["http://127.0.0.1:8765/images/nope.png"]


def test_plan_export_asset_filters(src_kb):
    full = _plan(src_kb)
    assert len(full.media) == 2
    no_assets = _plan(src_kb, include_assets=False)
    assert [i.kind for i in no_assets.media] == ["image"]
    assert no_assets.excluded and no_assets.excluded[0]["path"].startswith("assets/")
    # 阈值过滤只作用于附件
    tiny = _plan(src_kb, max_asset_bytes=4)
    assert {i.path for i in tiny.media} == {f"images/{IMG_NAME}"}
    assert tiny.excluded[0]["size"] > 4


def test_plan_export_meta_subset_follows_included(src_kb):
    assert len(_plan(src_kb).meta_rows["assets"]) == 1
    cut = _plan(src_kb, include_assets=False)
    assert cut.meta_rows["assets"] == [] and len(cut.meta_rows["images"]) == 1


# ── 容器读写 ──

def test_zip_bundle_roundtrip(src_kb, tmp_path):
    plan = _plan(src_kb)
    z = tmp_path / "b.zip"
    write_zip_bundle(z, plan)
    b = read_zip_bundle(z)
    assert b.kb_config["port"] == 8765
    assert b.manifest["format"] == "mdtool-bundle" and b.manifest["version"] == 1
    got_notes = {i.path: i.sha256 for i in b.note_items}
    assert set(got_notes) == {"markdown/sub/a.md"}
    assert {(i.path, i.size) for i in b.media_items} == {
        (f"assets/{AST_NAME}", 68), (f"images/{IMG_NAME}", 4)}
    assert b.meta_rows["images"][0]["original_name"] == "截图.png"
    assert b.warnings == []


def test_db_bundle_roundtrip_and_discriminator(src_kb, tmp_path):
    plan = _plan(src_kb)
    dbp = tmp_path / "b.db"
    write_db_bundle(dbp, plan)
    b = read_db_bundle(dbp)
    assert {i.path for i in b.note_items} == {"markdown/sub/a.md"}
    assert len(b.media_items) == 2
    assert b.meta_rows["assets"][0]["original_name"] == "用户手册.pdf"

    from server.notes_db import NotesDB
    plain = tmp_path / "plain.db"
    db = NotesDB(plain)
    try:
        db.upsert_note("x.md", "# X")
    finally:
        db.close()
    with pytest.raises(BundleFormatError):
        read_db_bundle(plain)


def test_read_rejects_bad_format_and_version(tmp_path):
    zbad = tmp_path / "bad.zip"
    with zipfile.ZipFile(zbad, "w") as zf:
        zf.writestr("kb.json", "{}")
        zf.writestr("manifest.json", json.dumps({"format": "other", "version": 1}))
    with pytest.raises(BundleFormatError):
        read_zip_bundle(zbad)

    znear = tmp_path / "future.zip"
    with zipfile.ZipFile(znear, "w") as zf:
        zf.writestr("kb.json", "{}")
        zf.writestr("manifest.json", json.dumps({"format": "mdtool-bundle", "version": 99}))
    with pytest.raises(BundleFormatError):
        read_zip_bundle(znear)


def test_read_zip_rejects_zip_slip(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("kb.json", "{}")
        zf.writestr("manifest.json", json.dumps(
            {"format": "mdtool-bundle", "version": 1, "items": [
                {"path": "../evil.md", "kind": "note", "sha256": "0" * 64, "size": 0}]}))
        zf.writestr("../evil.md", "boom")  # 名字含 .. 的成员
    with pytest.raises(BundleFormatError):
        read_zip_bundle(z)


def test_read_zip_drops_hash_mismatch_with_warning(src_kb, tmp_path):
    plan = _plan(src_kb)
    z = tmp_path / "tampered.zip"
    write_zip_bundle(z, plan)
    # 改清单里图片的 sha，使其与实际字节不符 → 该条目应被拒收并告警
    src_zip = zipfile.ZipFile(z)
    entries = {n: src_zip.read(n) for n in src_zip.namelist()}
    src_zip.close()
    manifest = json.loads(entries["manifest.json"])
    for e in manifest["items"]:
        if e["path"] == f"images/{IMG_NAME}":
            e["sha256"] = "0" * 64
    entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, data in entries.items():
            zf.writestr(n, data)

    b = read_zip_bundle(z)
    assert all(i.path != f"images/{IMG_NAME}" for i in b.media_items)
    assert any("哈希不符" in w for w in b.warnings)


# ── 分类 ──

def test_normalize_text_crlf_bom():
    assert normalize_text(b"\xef\xbb\xbf# A\r\nbody\r\n") == normalize_text("# A\nbody\n")

def test_classify_new_identical_conflict(src_kb, tmp_path):
    plan = _plan(src_kb)
    bundle_note = next(i for i in plan.items if i.kind == "note").data
    tgt = tmp_path / "tgt"
    (tgt / "markdown" / "sub").mkdir(parents=True)
    # 同路径 CRLF 变体 → 归一化后相同
    crlf = bundle_note.decode("utf-8").replace("\n", "\r\n")
    (tgt / "markdown" / "sub" / "a.md").write_bytes(crlf.encode("utf-8"))

    ip = classify_import(read_zip_bundle(_zip_of(plan, tmp_path)), target_root=tgt)
    [na] = ip.notes
    assert na.status == STATUS_IDENTICAL


def _zip_of(plan, tmp_path):
    z = tmp_path / "_tmp.zip"
    write_zip_bundle(z, plan)
    return z


def test_classify_conflict_and_new_and_casefold(src_kb, tmp_path):
    plan = _plan(src_kb)
    data = next(i for i in plan.items if i.kind == "note").data
    tgt = tmp_path / "tgt"
    (tgt / "markdown").mkdir(parents=True)
    (tgt / "markdown" / "sub").mkdir()
    (tgt / "markdown" / "sub" / "A.MD").write_bytes(
        data.replace(b"# A", "# A 改过".encode("utf-8")))
    (tgt / "markdown" / "other.md").write_bytes(data)  # 内容同、路径不同 → 对 a.md 无影响

    b = read_zip_bundle(_zip_of(plan, tmp_path))
    ip = classify_import(b, target_root=tgt)
    [na] = ip.notes
    assert na.status == STATUS_CONFLICT  # casefold 命中同名（大小写异）但内容不同


def test_classify_media_three_way(src_kb, tmp_path):
    tgt = tmp_path / "tgt"
    (tgt / "markdown").mkdir(parents=True)
    (tgt / "images").mkdir()
    (tgt / "images" / IMG_NAME).write_bytes(b"old-bytes")

    # 图片和附件都不随包：目标已有该图 → target_has；附件两边都没有 → missing 警告
    plan = _plan(src_kb, include_images=False, include_assets=False)
    b = read_zip_bundle(_zip_of(plan, tmp_path))
    ip = classify_import(b, target_root=tgt)
    status = {m.name: m.status for m in ip.media}
    assert status[IMG_NAME] == MEDIA_TARGET_HAS
    assert status[AST_NAME] == MEDIA_MISSING
    assert any("媒体缺失" in w for w in ip.warnings)

    # 正常随包 → bundled
    ip2 = classify_import(read_zip_bundle(_zip_of(_plan(src_kb), tmp_path)),
                          target_root=tgt)
    assert {m.name: m.status for m in ip2.media}[IMG_NAME] == "bundled"


def test_classify_db_target(src_kb, tmp_path):
    from server.notes_db import NotesDB
    dbp = tmp_path / "t.db"
    db = NotesDB(dbp)
    try:
        db.upsert_note("sub/a.md", "# A\n被改过的正文\n")
        ip = classify_import(read_db_bundle(_db_of(_plan(src_kb), tmp_path)),
                             target_db=db, target_media_root=tmp_path / "media")
    finally:
        db.close()
    assert ip.notes[0].status == STATUS_CONFLICT


def _db_of(plan, tmp_path):
    p = tmp_path / "_tmp.db"
    write_db_bundle(p, plan)
    return p


# ── 应用 ──

def test_apply_folder_default_never_overwrites(src_kb, tmp_path):
    plan = _plan(src_kb, kb_config={**load_kb_config(src_kb), "host": "10.0.0.5",
                                    "port": 9999})
    b = read_zip_bundle(_zip_of(plan, tmp_path))
    tgt = tmp_path / "tgt"
    (tgt / "markdown").mkdir(parents=True)
    (tgt / "markdown" / "sub").mkdir()
    (tgt / "markdown" / "sub" / "a.md").write_text("# 我是不一样的旧版\n", encoding="utf-8")

    ip = classify_import(b, target_root=tgt)
    assert ip.notes[0].status == STATUS_CONFLICT
    r = apply_import(ip, target_root=tgt, bundle=b,
                     target_kb_config=load_kb_config(tgt))
    assert r["added"] == 0 and r["overwritten"] == 0 and r["skipped"] == 1
    assert (tgt / "markdown" / "sub" / "a.md").read_text(encoding="utf-8") == "# 我是不一样的旧版\n"


def test_apply_folder_overwrite_rewrites_urls_and_merges_meta(tmp_path):
    """来自另一台机器（10.0.0.5:9999）的包导入默认配置目标：URL 前缀改写 + 元数据合并。"""
    src = tmp_path / "src"
    (src / "markdown" / "sub").mkdir(parents=True)
    (src / "images").mkdir()
    (src / "assets").mkdir()
    src_img = "http://10.0.0.5:9999/images/" + IMG_NAME
    src_ast = "http://10.0.0.5:9999/assets/" + AST_NAME
    (src / "markdown" / "sub" / "a.md").write_text(
        f"# A\n\n![图]({src_img})\n\n[附件]({src_ast})\n", encoding="utf-8")
    (src / "images" / IMG_NAME).write_bytes(b"PNG1")
    (src / "assets" / AST_NAME).write_bytes(b"PDFDATA")
    _make_meta_db(src / "meta.db", {IMG_NAME: "截图.png", AST_NAME: "用户手册.pdf"})
    save_kb_config(src, {"host": "10.0.0.5", "port": 9999})

    plan = _plan(src)
    # 包内保存原始存储形式——来源 URL 原样进包（规范 §2.1）
    assert "http://10.0.0.5:9999/images/" in plan.notes[0][1]
    b = read_zip_bundle(_zip_of(plan, tmp_path))
    tgt = tmp_path / "tgt"
    tgt.mkdir()

    ip = classify_import(b, target_root=tgt)
    assert ip.notes[0].status == STATUS_NEW
    r = apply_import(ip, target_root=tgt, bundle=b, target_kb_config=load_kb_config(tgt))
    assert r["added"] == 1 and r["media_written"] == 2 and r["meta_merged"] == 2

    body = (tgt / "markdown" / "sub" / "a.md").read_text(encoding="utf-8")
    # URL 已从来源前缀改写到目标默认 127.0.0.1:8765
    assert "http://127.0.0.1:8765/images/" in body
    assert "10.0.0.5" not in body
    assert (tgt / "images" / IMG_NAME).read_bytes() == b"PNG1"

    conn = sqlite3.connect(str(tgt / "meta.db"))
    orig = conn.execute(
        "SELECT original_name FROM assets WHERE timestamp_name=?", (AST_NAME,)).fetchone()[0]
    conn.close()
    assert orig == "用户手册.pdf"


def test_apply_db_target_upserts_media_and_meta(src_kb, tmp_path):
    from server.notes_db import NotesDB
    plan = _plan(src_kb)
    b = read_db_bundle(_db_of(plan, tmp_path))
    dbp = tmp_path / "t.db"
    db = NotesDB(dbp)
    try:
        ip = classify_import(b, target_db=db, target_media_root=tmp_path / "media")
        r = apply_import(ip, target_db=db, target_media_root=tmp_path / "media",
                         bundle=b, target_kb_config=DEFAULT_KB())
        assert r["added"] == 1
        row = db.get_note_by_path("sub/a.md")
        assert row is not None and IMG in row["body"]
    finally:
        db.close()
    assert (tmp_path / "media" / "images" / IMG_NAME).exists()
    conn = sqlite3.connect(str(dbp))
    n = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    conn.close()
    assert n == 1  # 元数据并表（规范 §4 的前向兼容）


def DEFAULT_KB():
    return {"host": "127.0.0.1", "port": 8765,
            "images_subdir": "images", "assets_subdir": "assets"}


def test_read_folder_bundle_loose_md_root(tmp_path):
    """存量散装布局（根即笔记树，如 C:\\sw\\note）也能当来源读。"""
    (tmp_path / "a.md").write_text(f"![x]({IMG})\n", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / AST_NAME).write_bytes(b"P")
    b = read_folder_bundle(tmp_path)
    assert {i.path for i in b.note_items} == {"markdown/a.md"}
    assert {i.path for i in b.media_items} == {f"assets/{AST_NAME}"}


def test_read_folder_bundle_skips_structural_files_in_media(src_kb):
    """媒体目录里的 meta.db / notes.db / *.log 是结构性杂物，不当作附件。"""
    (src_kb / "assets" / "meta.db").write_bytes(b"sqlite")
    (src_kb / "assets" / "server.log").write_bytes(b"log")
    (src_kb / "images" / "notes.db").write_bytes(b"sqlite")
    b = read_folder_bundle(src_kb)
    paths = {i.path for i in b.media_items}
    assert f"assets/{AST_NAME}" in paths and f"images/{IMG_NAME}" in paths
    assert all(not p.endswith((".db", ".log")) for p in paths)


# ── P2 支撑：统计预览 / diff / meta 并表 / 移动提示 ──

def test_preview_export_stats_and_exclusion(src_kb):
    content = (src_kb / "markdown" / "sub" / "a.md").read_text(encoding="utf-8")
    s = preview_export([("sub/a.md", content)], source_root=src_kb)
    assert s["notes"] == 1
    assert s["images"] == {"count": 1, "bytes": 4}
    assert s["assets"] == {"count": 1, "bytes": 68}
    cut = preview_export([("sub/a.md", content)], source_root=src_kb,
                         max_asset_bytes=4)
    assert cut["assets"]["count"] == 0 and len(cut["excluded"]) == 1


def test_diff_unified_tags_and_crlf_insensitive():
    tags = [t for t, _ in diff_unified("# A\nold line\n", "# A\nnew line\n")]
    assert "-" in tags and "+" in tags
    clean = diff_unified("# A\r\nbody\r\n", "# A\nbody\n")
    assert all(t != "-" for t, _ in clean)


def test_migrate_meta_into_db_idempotent(src_kb, tmp_path):
    """老 meta.db 并入工作库；重复执行等价（INSERT OR REPLACE）。"""
    from server.notes_db import NotesDB
    dbp = tmp_path / "working.db"
    db = NotesDB(dbp)
    try:
        db.upsert_note("x.md", "# X")
    finally:
        db.close()
    assert migrate_meta_into_db(dbp, src_kb / "meta.db") == 2
    conn = sqlite3.connect(str(dbp))
    assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
    orig = conn.execute("SELECT original_name FROM assets LIMIT 1").fetchone()[0]
    conn.close()
    assert orig == "用户手册.pdf"
    assert migrate_meta_into_db(dbp, src_kb / "meta.db") == 2


def test_classify_new_with_move_hint(src_kb, tmp_path):
    """内容相同但路径不同 → 照常按新增处理，但带移动/复制提示。"""
    content = (src_kb / "markdown" / "sub" / "a.md").read_text(encoding="utf-8")
    tgt = tmp_path / "tgt"
    (tgt / "markdown").mkdir(parents=True)
    (tgt / "markdown" / "old-place.md").write_text(content, encoding="utf-8")

    b = read_zip_bundle(_zip_of(plan_export([("new-place.md", content)],
                                            source_root=src_kb), tmp_path))
    ip = classify_import(b, target_root=tgt)
    [na] = ip.notes
    assert na.status == STATUS_NEW and na.rel == "new-place.md"
    assert na.hint and "old-place.md" in na.hint

