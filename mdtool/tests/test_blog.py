"""博客源管理单元测试（docs/博客文章管理规划.md B0–B2，mdtool/core/blog.py）。"""

from datetime import datetime
from pathlib import Path

import pytest

from mdtool.core import blog

IMG = "http://127.0.0.1:8765/images/image-20201215174726729.png"


@pytest.fixture()
def blog_root(tmp_path):
    """最小 Hexo 源：_config.yml + source/_posts + source/assets。"""
    root = tmp_path / "blog"
    (root / "source" / "_posts").mkdir(parents=True)
    (root / "source" / "assets").mkdir(parents=True)
    (root / "_config.yml").write_text("title: demo\n", encoding="utf-8")
    return root


def _post(root, name, text, drafts=False):
    p = (blog.drafts_dir(root) if drafts else blog.posts_dir(root)) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── B0 识别 ──

def test_is_hexo_source(blog_root):
    assert blog.is_hexo_source(blog_root) is True
    assert blog.is_hexo_source(blog_root / "source") is False


def test_is_hexo_source_missing_parts(tmp_path):
    (tmp_path / "_config.yml").write_text("x: 1", encoding="utf-8")
    assert blog.is_hexo_source(tmp_path) is False  # 缺 source/_posts/
    (tmp_path / "scaffolds").mkdir()
    assert blog.is_hexo_source(tmp_path) is False


# ── front-matter ──

def test_parse_front_matter_basic():
    meta, body = blog.parse_front_matter(
        '---\ntitle: "你好: 世界"\ndate: 2025-08-12 20:30:00\ntags: [a, b]\n---\n\n正文')
    assert meta["title"] == "你好: 世界"
    assert meta["date"] == "2025-08-12 20:30:00"
    assert meta["tags"] == ["a", "b"]
    assert body == "\n正文"


def test_parse_front_matter_block_list_nested():
    meta, _ = blog.parse_front_matter(
        "---\ncategories:\n  - [Linux, KVM]\n  - 随笔\ntags:\n---\n正文")
    assert meta["categories"] == [["Linux", "KVM"], "随笔"]
    assert meta["tags"] == []


def test_parse_front_matter_missing():
    assert blog.parse_front_matter("正文无 front-matter") == ({}, "正文无 front-matter")
    assert blog.parse_front_matter("---\ntitle: 未闭合") == ({}, "---\ntitle: 未闭合")


def test_parse_date_formats():
    assert blog._parse_date("2025-08-12 20:30:00") == datetime(2025, 8, 12, 20, 30)
    assert blog._parse_date("2025-08-12") == datetime(2025, 8, 12)
    assert blog._parse_date("2025-08-12T20:30:00+08:00") == datetime(2025, 8, 12, 12, 30)
    assert blog._parse_date("not-a-date") is None
    assert blog._parse_date(None) is None


# ── B0 文章列表 ──

def test_list_posts_order_and_drafts(blog_root):
    _post(blog_root, "a.md", "---\ntitle: 甲\ndate: 2025-03-01 09:00:00\n---\n")
    _post(blog_root, "b.md", "---\ntitle: 乙\ndate: 2026-01-01 10:00:00\n---\n")
    _post(blog_root, "d.md", "---\ntitle: 草\ndate: 2025-06-01 08:00:00\n---\n",
          drafts=True)
    all_posts = blog.list_posts(blog_root)
    assert [p.title for p in all_posts] == ["乙", "草", "甲"]
    assert [p.draft for p in all_posts] == [False, True, False]
    only_posts = blog.list_posts(blog_root, include_drafts=False)
    assert [p.title for p in only_posts] == ["乙", "甲"]


def test_list_posts_missing_date_falls_back_mtime(blog_root):
    _post(blog_root, "nodate.md", "纯正文")
    posts = blog.list_posts(blog_root)
    assert len(posts) == 1
    assert posts[0].title == "nodate" and posts[0].date is not None


def test_list_posts_rel_paths(blog_root):
    _post(blog_root, "sub/deep.md", "# t")
    posts = blog.list_posts(blog_root)
    assert posts[0].rel == "_posts/sub/deep.md"


# ── B1 脚手架 ──

def test_sanitize_post_filename():
    assert blog.sanitize_post_filename('a<b>c:"d/e\\f|g?h*i') == "abcdefghi.md"
    assert blog.sanitize_post_filename("部署 KVM 虚拟机") == "部署 KVM 虚拟机.md"
    assert blog.sanitize_post_filename("  ") == "untitled.md"
    assert blog.sanitize_post_filename("...") == "untitled.md"


def test_scaffold_post_default_template(blog_root):
    path = blog.scaffold_post(
        blog_root, title="T:1", date=datetime(2025, 1, 2, 3, 4, 5),
        categories=(("Linux", "KVM"), "随笔"), tags=("a", "b"))
    assert path == blog.posts_dir(blog_root) / "T1.md"
    meta, body = blog.parse_front_matter(path.read_text(encoding="utf-8"))
    assert meta["title"] == "T:1"
    assert meta["date"] == "2025-01-02 03:04:05"
    assert meta["categories"] == [["Linux", "KVM"], "随笔"]
    assert meta["tags"] == ["a", "b"]


def test_scaffold_post_custom_template_preserves_keys(blog_root):
    scaffold = blog_root / "scaffolds" / "post.md"
    scaffold.parent.mkdir()
    scaffold.write_text(
        "---\ntitle: {{ title }}\ndate: {{ date }}\npermalink: {{ slug }}\n"
        "tags:\n---\n\n正文\n", encoding="utf-8")
    path = blog.scaffold_post(blog_root, title="示例", tags=("x",))
    meta, body = blog.parse_front_matter(path.read_text(encoding="utf-8"))
    assert meta["permalink"] == "{{ slug }}"  # 模板自定义键原样保留
    assert meta["title"] == "示例"
    assert meta["tags"] == ["x"]
    assert body.strip().startswith("正文")


def test_scaffold_post_description(blog_root):
    path = blog.scaffold_post(blog_root, title="带摘要", description="这是 SEO 摘要")
    meta, _ = blog.parse_front_matter(path.read_text(encoding="utf-8"))
    assert meta["description"] == "这是 SEO 摘要"
    # 不传则不写该键（覆盖主题的截断逻辑交给搜索引擎兜底）
    path2 = blog.scaffold_post(blog_root, title="无摘要")
    meta2, _ = blog.parse_front_matter(path2.read_text(encoding="utf-8"))
    assert "description" not in meta2


def test_scaffold_post_slug_and_exists(blog_root):
    blog.scaffold_post(blog_root, title="A", slug="my-post")
    assert (blog.posts_dir(blog_root) / "my-post.md").is_file()
    with pytest.raises(FileExistsError):
        blog.scaffold_post(blog_root, title="B", slug="my-post")


# ── B2 校验 ──

def test_validate_blog(blog_root):
    (blog_root / "source" / "assets" / "good.png").write_bytes(b"P")
    (blog_root / "source" / "assets" / "orphan.png").write_bytes(b"P")
    _post(blog_root, "a.md", (
        f"![ok](assets/good.png)\n"
        f"![missing](assets/gone.png)\n"
        f"![local](local.png)\n"
        f"![ext](https://example.com/x.png)\n"
        f"![anchor](#sec)\n"
        f"[site](2025/08/01/post-slug)\n"
        f"![kb]({IMG})\n"))
    (blog.posts_dir(blog_root) / "local.png").write_bytes(b"P")

    report = blog.validate_blog(blog_root)
    assert report.scanned == 1 and report.assets_count == 2
    by_kind = {}
    for it in report.issues:
        by_kind.setdefault(it.kind, []).append(it)
    assert [it.dest for it in by_kind["broken"]] == ["assets/gone.png"]
    assert by_kind["migratable"][0].dest == "local.png"
    assert by_kind["kbfurl"][0].dest == IMG
    assert report.unreferenced == ("orphan.png",)


def test_validate_blog_interlink_and_nested_media(blog_root):
    _post(blog_root, "a.md", "[另篇](b.md)\n![deep](img/pic.png)\n")
    _post(blog_root, "b.md", "# b")
    (blog.posts_dir(blog_root) / "img").mkdir()
    (blog.posts_dir(blog_root) / "img" / "pic.png").write_bytes(b"P")
    report = blog.validate_blog(blog_root)
    kinds = [it.kind for it in report.issues]
    assert kinds == ["migratable"]  # 互链与无后缀 permalink 不判


def test_validate_blog_drafts_toggle(blog_root):
    (blog_root / "source" / "assets" / "ok.png").write_bytes(b"P")
    _post(blog_root, "d.md", "![x](assets/gone.png)\n", drafts=True)
    assert any(it.kind == "broken" for it in blog.validate_blog(blog_root).issues)
    assert blog.validate_blog(blog_root, include_drafts=False).issues == ()


# ── B2 散落媒体迁移 ──

def _file(path: Path, size: int = 3):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"P" * size)
    return path


def test_plan_and_apply_migration(blog_root):
    _file(blog.posts_dir(blog_root) / "local.png")
    _post(blog_root, "a.md", "![x](local.png)\n![y](./local.png)\n")
    _post(blog_root, "b.md", "![x](local.png)\n")

    moves, dest_maps = blog.plan_blog_migration(blog_root)
    assert len(moves) == 1  # 同一文件只移动一次
    assert set(dest_maps) == {"_posts/a.md", "_posts/b.md"}
    assert sorted(dest_maps["_posts/a.md"]) == ["./local.png", "local.png"]
    plan = moves[0]
    assert plan.dst.parent == blog.assets_dir(blog_root)
    assert plan.dst.name.startswith("image-")

    moved, changed = blog.apply_blog_migration(blog_root, moves, dest_maps)
    assert (moved, changed) == (1, 2)
    new_name = plan.dst.name
    assert (blog.assets_dir(blog_root) / new_name).is_file()
    assert not (blog.posts_dir(blog_root) / "local.png").exists()
    a = (blog.posts_dir(blog_root) / "a.md").read_text(encoding="utf-8")
    assert a.count(f"assets/{new_name}") == 2  # ./local.png 与 local.png 都改写
    b = (blog.posts_dir(blog_root) / "b.md").read_text(encoding="utf-8")
    assert f"assets/{new_name}" in b

    # 迁移后幂等：再无散落媒体
    moves2, _ = blog.plan_blog_migration(blog_root)
    assert moves2 == []


def test_migration_attachment_no_prefix(blog_root):
    _file(blog.posts_dir(blog_root) / "doc.pdf")
    _post(blog_root, "c.md", "[下载](doc.pdf)\n")
    moves, dest_maps = blog.plan_blog_migration(blog_root)
    assert not moves[0].dst.name.startswith("image-")
    moved, _ = blog.apply_blog_migration(blog_root, moves, dest_maps)
    assert moved == 1
    c = (blog.posts_dir(blog_root) / "c.md").read_text(encoding="utf-8")
    assert f"assets/{moves[0].dst.name}" in c


def test_migration_ignores_outside_source(blog_root):
    outside = _file(blog_root / "outside.png")
    _post(blog_root, "a.md", "![x](../outside.png)\n")
    moves, dest_maps = blog.plan_blog_migration(blog_root)
    assert moves == [] and dest_maps == {}
    assert outside.is_file()


# ── B2 导入图片 ──

def test_import_images_and_links(blog_root):
    src = blog_root / "in.png"
    src.write_bytes(b"P")
    pairs = blog.import_images([src], blog.assets_dir(blog_root))
    name = pairs[0][1]
    assert name.startswith("image-") and (blog.assets_dir(blog_root) / name).is_file()
    assert src.is_file()  # 拷贝不移动
    md = blog.links_markdown(pairs)
    assert md == f"![in](assets/{name})"


def test_import_attachment_naming_and_alt(blog_root):
    src = blog_root / "随 笔[1].pdf"
    src.write_bytes(b"D")
    pairs = blog.import_images([src], blog.assets_dir(blog_root))
    name = pairs[0][1]
    assert not name.startswith("image-") and name.endswith(".pdf")
    assert blog.links_markdown(pairs) == f"![随 笔1](assets/{name})"


# ── 笔记库抽取 ──

AST = "http://127.0.0.1:8765/assets/20201215174726729.pdf"


@pytest.fixture()
def kb_env(tmp_path):
    """KB 媒体根 + 一篇引用图床 URL 的笔记。"""
    media = tmp_path / "media"
    (media / "images").mkdir(parents=True)
    (media / "assets").mkdir()
    (media / "images" / "image-20201215174726729.png").write_bytes(b"P")
    (media / "assets" / "20201215174726729.pdf").write_bytes(b"P")
    kb = tmp_path / "kb"
    kb.mkdir()
    return media, kb


def test_extract_kb_urls_rewritten_and_media_copied(blog_root, kb_env):
    media, kb = kb_env
    note = kb / "note1.md"
    note.write_text(f"# T\n\n![图]({IMG})\n\n[文件]({AST})\n", encoding="utf-8")
    plan = blog.plan_note_extract([note], blog_root, media_root=media)
    assert plan.skipped == ()
    dst, text = plan.posts[0]
    assert dst == blog.posts_dir(blog_root) / "note1.md"
    assert "assets/image-20201215174726729.png" in text
    assert "assets/20201215174726729.pdf" in text
    assert "127.0.0.1" not in text
    assert [(s.name, d.name) for s, d in plan.media] == [
        ("image-20201215174726729.png", "image-20201215174726729.png"),
        ("20201215174726729.pdf", "20201215174726729.pdf"),
    ]
    assert blog.apply_note_extract(plan) == (1, 2)
    assert dst.is_file() and note.is_file()  # 拷贝不移动
    assert (blog.assets_dir(blog_root) / "image-20201215174726729.png").is_file()


def test_extract_missing_media_kept_and_reported(blog_root, kb_env):
    media, kb = kb_env
    note = kb / "n.md"
    note.write_text(f"![有]({IMG})\n![缺](http://127.0.0.1:8765/images/gone.png)\n",
                    encoding="utf-8")
    plan = blog.plan_note_extract([note], blog_root, media_root=media)
    assert plan.posts[0][1] == (
        f"![有](assets/image-20201215174726729.png)\n"
        f"![缺](http://127.0.0.1:8765/images/gone.png)\n")
    assert len(plan.skipped) == 1 and "gone.png" in plan.skipped[0]


def test_extract_relative_media_and_name_collision(blog_root, kb_env):
    media, kb = kb_env
    (kb / "pic.png").write_bytes(b"P1")
    (kb / "sub").mkdir()
    (kb / "sub" / "pic.png").write_bytes(b"P2")
    (kb / "a.md").write_text("![x](pic.png)\n", encoding="utf-8")
    (kb / "sub" / "b.md").write_text("![y](pic.png)\n", encoding="utf-8")
    (kb / "sub" / "a.md").write_text("重名文章\n", encoding="utf-8")
    plan = blog.plan_note_extract(
        [kb / "a.md", kb / "sub" / "b.md", kb / "sub" / "a.md"], blog_root,
        media_root=media)
    assert len(plan.posts) == 2  # sub/a.md 重名整篇跳过
    assert any("同名" in s for s in plan.skipped)
    assert blog.apply_note_extract(plan) == (2, 2)
    a = (blog.posts_dir(blog_root) / "a.md").read_text(encoding="utf-8")
    b = (blog.posts_dir(blog_root) / "b.md").read_text(encoding="utf-8")
    assert "assets/pic.png" in a
    assert "assets/pic_2.png" in b  # 第二个同名 pic.png 顺延
    assert (blog.assets_dir(blog_root) / "pic.png").read_bytes() == b"P1"
    assert (blog.assets_dir(blog_root) / "pic_2.png").read_bytes() == b"P2"


def test_extract_never_overwrites_existing_post(blog_root, kb_env):
    media, kb = kb_env
    _post(blog_root, "a.md", "已发布的文章")
    note = kb / "a.md"
    note.write_text("新内容", encoding="utf-8")
    plan = blog.plan_note_extract([note], blog_root, media_root=media)
    assert plan.posts == ()
    assert (blog.posts_dir(blog_root) / "a.md").read_text(encoding="utf-8") == "已发布的文章"
