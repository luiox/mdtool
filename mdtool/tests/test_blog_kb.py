"""阶段 3 core 测试：勾选制清单语义、KB 形态站点配置、hexo→KB 一次性迁移。"""

from __future__ import annotations

import json

import pytest

from mdtool.core.sitegen.generate import build_site
from mdtool.core.sitegen.kb import (
    SiteConfig,
    load_site_config,
    manifest_path,
    manifest_post_inputs,
    save_site_config,
)
from mdtool.core.sitegen.manifest import (
    Manifest,
    ManifestEntry,
    apply_selection,
    load_manifest,
    save_manifest,
)
from mdtool.core.sitegen.migrate import (
    apply_hexo_to_kb,
    plan_hexo_to_kb,
)


# ── 清单勾选语义 ─────────────────────────────────────────────


def test_manifest_selected_roundtrip_and_legacy_compat(tmp_path):
    m = Manifest(3, (ManifestEntry(1, "blog/a.md", "2025-01-01", False),))
    save_manifest(tmp_path / "manifest.json", m)
    loaded = load_manifest(tmp_path / "manifest.json")
    assert loaded.entries[0].selected is False

    # 旧格式（无 selected 字段）读回 True——hexo 平移兼容线
    (tmp_path / "old.json").write_text(json.dumps(
        {"next_id": 2,
         "entries": [{"id": 1, "note": "blog/a.md", "published": ""}]}),
        encoding="utf-8")
    assert load_manifest(tmp_path / "old.json").entries[0].selected is True


def test_apply_selection_new_uncheck_recheck_id_stable():
    m, mapping = apply_selection(Manifest(1, ()), ["b.md", "a.md"],
                                 published="2026-09-06")
    # 新勾选按 note 排序追加，published 记首次勾选日期
    assert [e.id for e in m.entries] == [1, 2]
    assert mapping == {"a.md": 1, "b.md": 2}
    assert all(e.published == "2026-09-06" for e in m.entries)

    # 取消勾选：仅置 selected=False，id 保留
    m2, _ = apply_selection(m, ["a.md"])
    by_note = {e.note: e for e in m2.entries}
    assert by_note["b.md"].selected is False and by_note["b.md"].id == 2

    # 重新勾选：id 不变（URL 身份底线）
    m3, mapping3 = apply_selection(m2, ["a.md", "b.md"])
    assert mapping3["b.md"] == 2
    assert {e.note: e.selected for e in m3.entries} == {
        "a.md": True, "b.md": True}


# ── KB 形态站点配置与清单消费 ────────────────────────────────


def test_load_site_config_defaults_and_roundtrip(tmp_path):
    cfg = load_site_config(tmp_path)
    assert cfg.spec.title == tmp_path.name and cfg.spec.url == ""
    assert cfg.deploy_repo == "" and cfg.deploy_branch == "main"

    save_site_config(tmp_path, SiteConfig(
        spec=load_site_config(tmp_path).spec.__class__(
            title="站", url="https://x.io/", author="me", per_page=7),
        deploy_repo="git@x.io/y.git", deploy_branch="main"))
    cfg2 = load_site_config(tmp_path)
    assert cfg2.spec.title == "站" and cfg2.spec.url == "https://x.io"
    assert cfg2.spec.per_page == 7 and cfg2.deploy_repo == "git@x.io/y.git"

    # 坏 JSON 不炸，回退默认
    (tmp_path / "site.json").write_text("{bad", encoding="utf-8")
    assert load_site_config(tmp_path).spec.title == tmp_path.name


def test_site_config_theme_key_and_theme_root(tmp_path):
    """theme 键：roundtrip、非法名回退默认、目录不存在时 theme_root=None。"""
    from mdtool.core.sitegen.kb import theme_root

    save_site_config(tmp_path, SiteConfig(
        spec=load_site_config(tmp_path).spec, theme="themes/jacman"))
    cfg = load_site_config(tmp_path)
    assert cfg.theme == "themes/jacman"
    assert theme_root(tmp_path, cfg) is None  # 目录还没建

    (tmp_path / "themes" / "jacman").mkdir(parents=True)
    assert theme_root(tmp_path, cfg) == tmp_path / "themes" / "jacman"

    # 非法名（盘符/上跳/空）→ 回退默认名，绝不指到 B 之外；"/etc" 被强制相对化
    for bad in ("..\\x", "..", "", "C:/Windows", "/etc/../x"):
        (tmp_path / "site.json").write_text(
            json.dumps({"theme": bad}), encoding="utf-8")
        assert load_site_config(tmp_path).theme == "theme"
    (tmp_path / "site.json").write_text(json.dumps({"theme": "/etc"}),
                                        encoding="utf-8")
    assert load_site_config(tmp_path).theme == "etc"


def test_manifest_post_inputs_selected_filter_and_missing(tmp_path):
    (tmp_path / "blog").mkdir()
    (tmp_path / "blog" / "a.md").write_text("a", encoding="utf-8")
    # b.md 故意不存在；c.md 未勾选也不存在——未勾选不校验存在性
    m = Manifest(4, (
        ManifestEntry(1, "blog/a.md"),
        ManifestEntry(2, "blog/b.md"),
        ManifestEntry(3, "blog/c.md", selected=False),
    ))
    inputs, missing = manifest_post_inputs(tmp_path, m)
    assert [i.id for i in inputs] == [1]
    assert missing == ("blog/b.md (id=2)",)


# ── hexo → KB 迁移 ───────────────────────────────────────────


def _make_hexo_src(root):
    posts = root / "source" / "_posts"
    posts.mkdir(parents=True)
    (root / "_config.yml").write_text(
        "title: 测试站\nurl: 'https://blog.example.com'\nauthor: tester\n"
        "language: zh-CN\ndeploy:\n  repo: git@x.io/y.git\n  branch: main\n",
        encoding="utf-8")
    (posts / "a.md").write_text("---\ntitle: 甲\ndate: 2025-01-01\n---\n甲\n",
                                encoding="utf-8")
    (posts / "b.md").write_text("---\ntitle: 乙\ndate: 2025-02-01\n---\n乙\n",
                                encoding="utf-8")
    (posts / "c.md").write_text("---\ntitle: 丙\ndate: 2025-03-01\n---\n丙\n",
                                encoding="utf-8")
    # 清单里有 id=4 的 e.md 已退回 _drafts（hexo 语义：发布过的文章转草稿）
    drafts = root / "source" / "_drafts"
    drafts.mkdir(parents=True)
    (drafts / "e.md").write_text("退稿\n", encoding="utf-8")
    (drafts / "d.md").write_text("纯草稿\n", encoding="utf-8")
    assets = root / "source" / "assets"
    assets.mkdir()
    (assets / "img.png").write_bytes(b"png")
    (root / "passage_index.json").write_text(json.dumps({
        "next_index": 5,
        "posts": [{"path": "source/_posts/a.md", "id": 1},
                  {"path": "source/_posts/b.md", "id": 2},
                  {"path": "source/_posts/gone.md", "id": 3},
                  {"path": "source/_posts/e.md", "id": 4}],
    }), encoding="utf-8")
    return root


def test_migrate_plan_keys_ids_and_apply(tmp_path):
    src = _make_hexo_src(tmp_path / "src")
    kb = tmp_path / "kb"
    (kb / "markdown").mkdir(parents=True)

    plan = plan_hexo_to_kb(src, kb, "blog")
    assert plan.blog_dir == kb / "markdown" / "blog"
    # 键改写为 KB 根相对路径，id 原值平移；退稿 selected=False；失联保留
    entries = {e.id: e for e in plan.manifest.entries}
    assert {e.id: e.note for e in plan.manifest.entries} == {
        1: "markdown/blog/a.md", 2: "markdown/blog/b.md",
        3: "markdown/blog/gone.md", 4: "markdown/blog/e.md",
        5: "markdown/blog/c.md"}
    assert entries[4].selected is False
    assert all(e.selected for e in plan.manifest.entries if e.id != 4)
    assert plan.manifest.next_id == 6
    assert plan.unindexed == ("c.md",)
    assert plan.drafted == ("e.md",)
    assert plan.kept_missing == ("source/_posts/gone.md (id=3)",)
    # 草稿拷贝（退稿 + 纯草稿），都不与文章拷贝重叠
    assert sorted(dst.name for _s, dst in plan.draft_copies) == ["d.md", "e.md"]
    assert plan.spec.title == "测试站" and plan.deploy_repo == "git@x.io/y.git"

    report = apply_hexo_to_kb(plan)
    assert (report.posts, report.drafts, report.assets) == (3, 2, 1)
    assert (plan.blog_dir / "a.md").is_file()
    assert (plan.blog_dir / "e.md").is_file()
    assert (plan.blog_dir / "assets" / "img.png").is_file()

    # 清单/配置落盘且可回读
    m = load_manifest(manifest_path(plan.blog_dir))
    assert m.id_of("markdown/blog/b.md") == 2
    cfg = load_site_config(plan.blog_dir)
    assert cfg.spec.url == "https://blog.example.com"

    # 拒绝重跑
    with pytest.raises(RuntimeError):
        apply_hexo_to_kb(plan)


def test_migrated_kb_end_to_end_build(tmp_path):
    src = _make_hexo_src(tmp_path / "src")
    kb = tmp_path / "kb"
    (kb / "markdown").mkdir(parents=True)
    plan = plan_hexo_to_kb(src, kb, "blog")
    apply_hexo_to_kb(plan)

    manifest = load_manifest(manifest_path(plan.blog_dir))
    inputs, missing = manifest_post_inputs(kb, manifest)
    # gone.md 真失联进 missing；e.md 退稿未勾选——不生成也不校验
    assert sorted(missing) == ["markdown/blog/gone.md (id=3)"]
    assert sorted(i.id for i in inputs) == [1, 2, 5]
    cfg = load_site_config(plan.blog_dir)
    out = tmp_path / "public"
    build_site(inputs, cfg.spec, assets_src=plan.blog_dir / "assets",
               out_dir=out)
    assert (out / "article" / "1.html").is_file()
    assert (out / "article" / "5.html").is_file()
    assert not (out / "article" / "3.html").exists()
    assert not (out / "article" / "4.html").exists()
    assert (out / "assets" / "img.png").is_file()


def test_migrate_rejects_non_hexo_source(tmp_path):
    with pytest.raises(ValueError):
        plan_hexo_to_kb(tmp_path, tmp_path / "kb", "blog")
