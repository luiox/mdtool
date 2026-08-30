"""qtui.tabs.file_browser 纯逻辑测试：多篇打包、散装搜索、db 包导出。"""

import sys

import pytest


IMG = "http://127.0.0.1:8765/images/image-20201215174726729.png"


@pytest.fixture()
def kb(tmp_path):
    """标准 KB 根：notes/ + images/ + assets/ + meta.db。"""
    root = tmp_path / "kb"
    (root / "notes" / "sub").mkdir(parents=True)
    (root / "images").mkdir()
    (root / "assets").mkdir()
    (root / "meta.db").write_bytes(b"sqlite")
    (root / "images" / "image-20201215174726729.png").write_bytes(b"PNG1")
    return root


# ── plan_zip_bundle：深度改写 ──

def test_plan_zip_depth_prefix(kb):
    from mdtool.desktop.qtui.tabs.file_browser import plan_zip_bundle
    md = kb / "notes" / "sub" / "a.md"
    content = f"![图]({IMG})"
    new_content, members, skipped = plan_zip_bundle(
        content, md, depth=2, media_root=kb)
    assert skipped == []
    assert members == [("images/image-20201215174726729.png",
                        kb / "images" / "image-20201215174726729.png")]
    # depth=2（notes/sub/a.md）→ ../../images/...
    assert new_content == f"![图](../../images/image-20201215174726729.png)"


# ── plan_zip_bundle_many：多篇 + 跨篇去重 ──

def test_plan_zip_many_depth_and_dedupe(tmp_path):
    from mdtool.desktop.qtui.tabs.file_browser import plan_zip_bundle_many
    media = tmp_path / "media"
    (media / "images").mkdir(parents=True)
    (media / "images" / "image-1.png").write_bytes(b"P1")
    # 两篇笔记各自引用同名本地图片（不同目录）→ zip 内去重为 assets/local.png / assets/local_2.png
    d1 = tmp_path / "x"; d1.mkdir(); (d1 / "local.png").write_bytes(b"A")
    d2 = tmp_path / "y"; d2.mkdir(); (d2 / "local.png").write_bytes(b"B")
    notes = [
        ("a.md", d1 / "a.md", "![a](./local.png) ![s](http://127.0.0.1:8765/images/image-1.png)"),
        ("sub/b.md", d2 / "b.md", "![b](./local.png)"),
    ]
    results, skipped = plan_zip_bundle_many(notes, media_root=media)
    assert skipped == []
    by_path = {r[0]: r for r in results}
    assert by_path["a.md"][1] == "![a](assets/local.png) ![s](images/image-1.png)"
    # sub/b.md 深度 1 → ../assets/local_2.png（与 a.md 的 local.png 去重）
    assert by_path["sub/b.md"][1] == "![b](../assets/local_2.png)"
    members = [(m[0]) for _, _, ms in results for m in ms]
    assert "assets/local.png" in members and "assets/local_2.png" in members


# ── 散装搜索 ──

def test_search_md_job(kb):
    from mdtool.desktop.qtui.tabs.file_browser import _search_md_job
    (kb / "notes" / "a.md").write_text("# 甲\n\n关键词出现一次\n", encoding="utf-8")
    (kb / "notes" / "sub" / "b.md").write_text("# 乙\n\n无关内容\n", encoding="utf-8")
    (kb / "images" / "note.md").write_text("关键词", encoding="utf-8")  # 媒体面应被跳过

    rows = _search_md_job(kb, "关键词", "body", lambda *a, **k: None)
    rels = [r["rel"] for r in rows]
    assert rels == ["notes/a.md"]
    assert rows[0]["title"] == "甲"

    rows_name = _search_md_job(kb, "b\\.md", "name", lambda *a, **k: None)
    assert [r["rel"] for r in rows_name] == ["notes/sub/b.md"]


def test_search_md_job_bad_regex(kb):
    from mdtool.desktop.qtui.tabs.file_browser import _search_md_job
    with pytest.raises(ValueError):
        _search_md_job(kb, "([", "body", lambda *a, **k: None)


# ── 散装 → db 包 ──

def test_export_db_job(kb):
    from mdtool.desktop.qtui.tabs.file_browser import _export_db_job
    from mdtool.core.server.notes_db import NotesDB
    (kb / "notes" / "a.md").write_text("# A\n正文\n", encoding="utf-8")
    (kb / "notes" / "sub" / "b.md").write_text("# B\n正文\n", encoding="utf-8")
    db_path = kb / "out.db"
    result = _export_db_job(db_path, kb / "notes", lambda *a, **k: None)
    assert result["total"] == 2 and result["inserted"] == 2
    db = NotesDB(db_path)
    try:
        assert {r["path"] for r in db.list_all()} == {"a.md", "sub/b.md"}
    finally:
        db.close()
