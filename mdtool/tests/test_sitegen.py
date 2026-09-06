"""sitegen 纯函数层测试：manifest id 只增语义、渲染语义、整站生成。"""

from __future__ import annotations

import json

import pytest

from mdtool.core.sitegen.generate import PostInput, SiteSpec, build_site, render_post
from mdtool.core.sitegen.manifest import (
    Manifest,
    ManifestEntry,
    assign_ids,
    load_manifest,
    save_manifest,
)
from mdtool.core.sitegen.render import render_markdown


# ── manifest：URL 身份的底线 ──────────────────────────────────────────

def test_assign_ids_appends_deterministic_and_never_reuses(tmp_path):
    m = Manifest(1, ())
    m2, map1 = assign_ids(m, ["b.md", "a.md"])
    # 确定性：按 note 排序分配
    assert map1 == {"a.md": 1, "b.md": 2}
    assert m2.next_id == 3
    # 再跑一轮：已入册的沿用旧 id，不新增
    m3, map2 = assign_ids(m2, ["a.md", "b.md"])
    assert map2 == map1
    assert m3.next_id == 3
    # 新笔记只从 next_id 前进
    m4, map3 = assign_ids(m3, ["c.md"])
    assert map3["c.md"] == 3


def test_manifest_roundtrip_and_garbage_recovery(tmp_path):
    p = tmp_path / "manifest.json"
    m, _ = assign_ids(Manifest(1, ()), ["笔记/中文.md"], published="2026-09-06")
    save_manifest(p, m)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["next_id"] == 2
    assert data["entries"][0]["note"] == "笔记/中文.md"
    loaded = load_manifest(p)
    assert loaded == m
    # 坏文件/不存在 → 空清单（首建友好）
    assert load_manifest(tmp_path / "nope.json") == Manifest(1, ())
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    assert load_manifest(bad) == Manifest(1, ())


def test_manifest_lookup():
    m = Manifest(5, (ManifestEntry(2, "a.md"), ManifestEntry(4, "b.md")))
    assert m.id_of("a.md") == 2
    assert m.note_of(4) == "b.md"
    assert m.id_of("zzz.md") is None
    assert m.note_of(99) is None


# ── 渲染：对齐 hexo 地面真相的三条语义 ───────────────────────────────

def test_render_rewrites_assets_links_and_keeps_external():
    src = (
        "![图](assets/任务状态转换图.jpg)\n\n"
        "[附件](assets/doc.pdf)\n\n"
        "[外链](https://example.com/x)\n\n"
        "[锚点](#sec)\n"
    )
    r = render_markdown(src)
    # 相对 assets/ → 根绝对，CJK 由 markdown-it normalizeLink 编码（单次）
    assert 'src="/assets/%E4%BB%BB%E5%8A%A1%E7%8A%B6%E6%80%81%E8%BD%AC%E6%8D%A2%E5%9B%BE.jpg"' in r.html
    assert 'href="/assets/doc.pdf"' in r.html
    # 外链加 target/rel；锚点不动
    assert '<a href="https://example.com/x" target="_blank" rel="noopener">' in r.html
    assert 'href="#sec"' in r.html


def test_render_double_encode_regression():
    """曾经 % 被二次编码成 %25（CJK 媒体 404）——锁定单次编码。"""
    r = render_markdown("![图](assets/图.png)")
    assert "%25" not in r.html


def test_render_heading_anchors_toc_and_excerpt():
    src = "## 引入库\n\n正文一段，用于摘要。" + "很长的内容" * 40 + "\n\n### 嵌套\n\n尾段。\n"
    r = render_markdown(src)
    assert [(t.level, t.text) for t in r.toc] == [(2, "引入库"), (3, "嵌套")]
    anchors = [t.anchor for t in r.toc]
    assert len(set(anchors)) == 2 and all(anchors)
    # TOC 锚点在 HTML 中有对应 id
    for a in anchors:
        assert f'id="{a}"' in r.html
    assert r.excerpt.startswith("正文一段")


def test_render_code_block_pygments_and_copy_button():
    src = "```kotlin\nval x = 1\n```\n"
    r = render_markdown(src)
    assert '<figure class="codeblock">' in r.html
    assert '<span class="codeblock-lang">kotlin</span>' in r.html
    assert "code-copy-btn" in r.html
    # Pygments 生效（kotlin 关键字 span），内容转义保留
    assert "k" in r.html and "val" in r.html
    # 未知语言回退转义、不出 Pygments span
    r2 = render_markdown("```\nplain <text>\n```\n")
    assert "&lt;text&gt;" in r2.html


def test_render_table_strikethrough_html_passthrough():
    src = ("| a | b |\n| --- | --- |\n| 1 | 2 |\n\n"
           "~~删除~~\n\n<div raw>直通</div>\n")
    r = render_markdown(src)
    assert "<table>" in r.html and "<s>删除</s>" in r.html
    assert "<div raw>直通</div>" in r.html


def test_render_rewrites_link_open_targets():
    """"<a> 链接的属性挂在 link_open token 上——图片与链接都要改写。"""
    r = render_markdown("[附件](assets/doc.pdf) 和 [站内](/article/1.html)")
    assert 'href="/assets/doc.pdf"' in r.html
    assert 'href="/article/1.html"' in r.html  # 站内绝对路径不动


def test_split_meta_lenient_no_opening_fence(tmp_path):
    """hexo-admin 时代缺 opening --- 的文章：meta 不许漏进正文。"""
    from mdtool.core.sitegen.generate import render_post
    p = tmp_path / "legacy.md"
    p.write_bytes(
        "title: 二零二四年六月五日\r\nauthor: Canrad\r\n"
        "date: 2024-06-05 16:48:21\r\ntags:\r\n---\r\n正文开始\r\n".encode("utf-8"))
    post = render_post(PostInput(id=9, path=p))
    assert post.title == "二零二四年六月五日"
    assert "author" not in post.html and "title:" not in post.html
    assert "正文开始" in post.html


# ── render_post / build_site ─────────────────────────────────────────

def _make_post(tmp_path, name, text):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_render_post_front_matter_and_fallbacks(tmp_path):
    p = _make_post(tmp_path, "p1.md",
                   "---\ntitle: 标题一\ndate: 2026-01-02 03:04:05\n"
                   "categories: [Linux/KVM, 随笔]\ntags: [a, b]\n"
                   "description: 摘要\nmathjax: true\n---\n正文 $x$\n")
    post = render_post(PostInput(id=7, path=p, note="p1.md"))
    assert post.title == "标题一"
    assert post.url_path == "article/7.html"
    assert post.date is not None and post.date.year == 2026
    assert post.categories_display == "Linux/KVM, 随笔"
    assert post.category_path == "Linux/KVM"
    assert post.mathjax is True
    assert post.description == "摘要"
    # 无 front-matter：标题回落文件名、日期回落 mtime
    q = _make_post(tmp_path, "裸标题.md", "只有正文\n")
    post2 = render_post(PostInput(id=8, path=q))
    assert post2.title == "裸标题"
    assert post2.date is not None


def test_build_site_full_tree(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "img.png").write_bytes(b"png")
    posts = [
        PostInput(id=1, path=_make_post(
            tmp_path, "a.md",
            "---\ntitle: 文章一\ndate: 2026-03-01 10:00:00\ntags: [t1, t2]\n"
            "categories: [C]\n---\n正文一\n\n```python\nprint(1)\n```\n")),
        PostInput(id=2, path=_make_post(
            tmp_path, "b.md",
            "---\ntitle: 文章二\ndate: 2026-05-01 10:00:00\ntags: [t1]\n---\n正文二\n")),
        PostInput(id=3, path=_make_post(
            tmp_path, "c.md",
            "---\ntitle: 文章三\ndate: 2025-01-01 10:00:00\ntags: [t2]\n---\n正文三\n")),
    ]
    spec = SiteSpec(title="测试站", url="https://blog.example.com",
                    author="tester", per_page=2)
    out = tmp_path / "public"
    report = build_site(posts, spec, assets_src=assets, out_dir=out)
    assert report.posts == 3 and not report.skipped

    # 文章页 + permALINK 形态
    assert (out / "article" / "1.html").is_file()
    # 首页分页（per_page=2 → 2 页）
    assert (out / "index.html").is_file()
    assert (out / "page" / "2" / "index.html").is_file()
    # 归档/标签/分类/feed/sitemap/静态资源/媒体
    assert (out / "archives" / "index.html").is_file()
    # 月度归档页（jacman 形态：左侧月份分组 + /archives/YYYY/MM/ 页面）
    assert (out / "archives" / "2026" / "03" / "index.html").is_file()
    assert (out / "archives" / "2026" / "05" / "index.html").is_file()
    month_05 = (out / "archives" / "2026" / "05" / "index.html").read_text(
        encoding="utf-8")
    assert "文章二" in month_05 and "文章一" not in month_05
    assert (out / "favicon.ico").is_file()
    assert (out / "tags" / "index.html").is_file()
    assert (out / "tags" / "t1" / "index.html").is_file()
    assert (out / "categories" / "C" / "index.html").is_file()
    assert (out / "atom.xml").is_file()
    assert (out / "sitemap.xml").is_file()
    assert (out / "css" / "style.css").is_file()
    assert (out / "css" / "pygments.css").is_file()
    assert (out / "js" / "site.js").is_file()
    assert (out / "assets" / "img.png").is_file()

    # 首页含最新两篇（日期序：文章二 05-01 > 文章一 03-01），旧文章在第二页
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "文章二" in index and "/article/2.html" in index
    assert "文章一" in index and "/article/1.html" in index
    page2 = (out / "page" / "2" / "index.html").read_text(encoding="utf-8")
    assert "文章三" in page2

    # 文章页：代码块 + 复制按钮 + 相邻导航 + 标签链接
    a1 = (out / "article" / "1.html").read_text(encoding="utf-8")
    assert "code-copy-btn" in a1
    assert "/article/2.html" in a1  # 较新邻居
    assert "/tags/t1/" in a1

    # feed：绝对 URL、limit 生效
    atom = (out / "atom.xml").read_text(encoding="utf-8")
    assert "https://blog.example.com/article/2.html" in atom

    # 重复生成幂等（覆盖同名文件，不报错不翻倍）
    report2 = build_site(posts, spec, assets_src=assets, out_dir=out)
    assert report2.posts == 3


def test_build_site_skips_bad_post_without_dying(tmp_path):
    good = _make_post(tmp_path, "good.md",
                      "---\ntitle: 好文章\ndate: 2026-01-01 00:00:00\n---\n正文\n")
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe\x00broken")  # 非法 UTF-8
    posts = [PostInput(id=1, path=good), PostInput(id=2, path=bad)]
    spec = SiteSpec(title="t", url="https://x.example", author="a")
    out = tmp_path / "public"
    report = build_site(posts, spec, out_dir=out)
    assert report.posts == 1
    assert len(report.skipped) == 1 and "bad.md" in report.skipped[0]
    assert (out / "article" / "1.html").is_file()
    assert not (out / "article" / "2.html").exists()


def test_render_post_date_fallback_mtime(tmp_path):
    """date 语义：front-matter 优先；缺失回落 mtime（与 blog.py/hexo 同），
    因此排序时 date 几乎不会为 None（stat 失败才可能）。"""
    from datetime import datetime

    pd = _make_post(tmp_path, "有日期.md",
                    "---\ntitle: A\ndate: 2020-01-01 00:00:00\n---\nx\n")
    pm = _make_post(tmp_path, "无日期.md", "---\ntitle: B\n---\nx\n")
    a = render_post(PostInput(id=1, path=pd))
    b = render_post(PostInput(id=2, path=pm))
    assert a.date == datetime(2020, 1, 1, 0, 0, 0)
    assert b.date is not None and (datetime.now() - b.date).days <= 1
