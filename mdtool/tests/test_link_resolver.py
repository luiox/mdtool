"""link_resolver 单元测试（docs/知识库规范.md §2）。"""

import sys

import pytest


from mdtool.core.link_resolver import (  # noqa: E402
    MediaRef,
    is_external_url,
    iter_link_destinations,
    parse,
    rewrite,
    rewrite_markdown,
)

IMG = "http://127.0.0.1:8765/images/image-20201215174726729.png"
AST = "http://127.0.0.1:8765/assets/20201215174726729.pdf"


# ── parse ──

def test_parse_image():
    assert parse(IMG) == MediaRef("images", "image-20201215174726729.png")


def test_parse_asset():
    assert parse(AST) == MediaRef("assets", "20201215174726729.pdf")


def test_parse_wrong_host():
    assert parse("http://192.168.1.2:8765/images/x.png") is None


def test_parse_wrong_port():
    assert parse("http://127.0.0.1:9999/images/x.png") is None


def test_parse_no_port():
    assert parse("http://127.0.0.1/images/x.png") == MediaRef("images", "x.png")


def test_parse_query_and_fragment():
    # 宽松识别：query/fragment 剥离后仍解析
    assert parse("http://127.0.0.1:8765/images/x.png?raw=1#top") == MediaRef("images", "x.png")


def test_parse_external_url():
    assert parse("https://example.com/images/x.png") is None
    assert parse("data:image/png;base64,AAAA") is None


def test_parse_custom_host_port():
    assert parse("http://192.168.1.2:9000/assets/a.zip", host="192.168.1.2", port=9000) \
        == MediaRef("assets", "a.zip")


# ── rewrite ──

def test_rewrite_desktop_passthrough():
    assert rewrite(IMG, target="desktop") == IMG


def test_rewrite_zip():
    assert rewrite(IMG, target="zip") == "images/image-20201215174726729.png"
    assert rewrite(AST, target="zip") == "assets/20201215174726729.pdf"


def test_rewrite_phone():
    assert rewrite(IMG, target="phone", base="http://127.0.0.1:8080") \
        == "http://127.0.0.1:8080/images/image-20201215174726729.png"
    assert rewrite(AST, target="lan", base="http://192.168.1.5:8765/") \
        == "http://192.168.1.5:8765/assets/20201215174726729.pdf"


def test_rewrite_unknown_passthrough():
    external = "https://example.com/a.png"
    assert rewrite(external, target="zip") == external
    relative = "./img.png"
    assert rewrite(relative, target="zip") == relative


def test_rewrite_phone_requires_base():
    with pytest.raises(ValueError):
        rewrite(IMG, target="phone")


# ── iter_link_destinations ──

def test_iter_basic():
    text = f"![alt]({IMG}) and [text]({AST})"
    got = list(iter_link_destinations(text))
    assert len(got) == 2
    assert got[0][3] is True and got[0][2] == IMG
    assert got[1][3] is False and got[1][2] == AST


def test_iter_skips_code():
    text = f"```\n![x]({IMG})\n```\n`![\u200b]({AST})`\n![ok]({IMG})"
    got = list(iter_link_destinations(text))
    assert len(got) == 1
    assert got[0][2] == IMG


def test_iter_strips_title():
    text = f'![alt]({IMG} "title here")'
    got = list(iter_link_destinations(text))
    assert got[0][2] == IMG


def test_iter_ignores_reference_links():
    text = "[text][ref]"
    assert list(iter_link_destinations(text)) == []


# ── rewrite_markdown ──

def test_rewrite_markdown_zip():
    text = f"# 标题\n\n![图]({IMG})\n\n[文件]({AST})\n\n外部: ![外](https://a.com/b.png)"
    out = rewrite_markdown(text, target="zip")
    assert "images/image-20201215174726729.png" in out
    assert "assets/20201215174726729.pdf" in out
    assert "https://a.com/b.png" in out  # 外链不动
    assert "127.0.0.1:8765" not in out


def test_rewrite_markdown_phone():
    out = rewrite_markdown(f"![a]({IMG})", target="phone", base="http://127.0.0.1:8080")
    assert out == "![a](http://127.0.0.1:8080/images/image-20201215174726729.png)"


def test_rewrite_markdown_skips_code():
    text = f"```\n![a]({IMG})\n```\n正文 ![b]({IMG})"
    out = rewrite_markdown(text, target="zip")
    assert out.startswith(f"```\n![a]({IMG})\n```\n正文 ![b](images/image-20201215174726729.png)")


def test_rewrite_markdown_preserves_rest():
    text = "# T\n\n段落 **粗体** 保持原样。\n"
    assert rewrite_markdown(text, target="zip") == text


# ── is_external_url ──

def test_is_external_url():
    assert is_external_url("https://a.com/x") is True
    assert is_external_url("data:image/png;base64,AA") is True
    assert is_external_url("/abs/path.png") is True
    assert is_external_url("./img.png") is False
    assert is_external_url("images/foo.png") is False


# ── plan_zip_bundle（zip 打包计划，qtui.tabs.file_browser）──

@pytest.fixture()
def zip_env(tmp_path):
    media = tmp_path / "media"
    (media / "images").mkdir(parents=True)
    (media / "assets").mkdir(parents=True)
    # 文件名与 IMG/AST 常量一致，才能被 plan_zip_bundle 命中
    (media / "images" / "image-20201215174726729.png").write_bytes(b"PNG1")
    (media / "assets" / "20201215174726729.pdf").write_bytes(b"PDF1")
    notes = tmp_path / "notes"
    notes.mkdir()
    md = notes / "a.md"
    return media, md


def test_plan_zip_media_urls(zip_env):
    from mdtool.desktop.qtui.tabs.file_browser import plan_zip_bundle
    media, md = zip_env
    md.write_text(
        f"# T\n\n![图]({IMG})\n\n[文件]({AST})\n",
        encoding="utf-8",
    )
    content = md.read_text(encoding="utf-8")
    new_content, members, skipped = plan_zip_bundle(
        content, md, media_root=media, host="127.0.0.1", port=8765)
    assert skipped == []
    assert members == [
        ("images/image-20201215174726729.png", media / "images" / "image-20201215174726729.png"),
        ("assets/20201215174726729.pdf", media / "assets" / "20201215174726729.pdf"),
    ]
    assert "![图](images/image-20201215174726729.png)" in new_content
    assert "[文件](assets/20201215174726729.pdf)" in new_content


def test_plan_zip_missing_media_kept(zip_env):
    from mdtool.desktop.qtui.tabs.file_browser import plan_zip_bundle
    media, md = zip_env
    content = f"![缺](http://127.0.0.1:8765/images/image-missing.png)"
    new_content, members, skipped = plan_zip_bundle(
        content, md, media_root=media)
    assert members == []
    assert skipped == ["http://127.0.0.1:8765/images/image-missing.png"]
    assert new_content == content


def test_plan_zip_relative_link(zip_env):
    from mdtool.desktop.qtui.tabs.file_browser import plan_zip_bundle
    media, md = zip_env
    (md.parent / "local.png").write_bytes(b"PNG")
    content = "![本地](./local.png)"
    new_content, members, skipped = plan_zip_bundle(content, md, media_root=media)
    assert skipped == []
    assert members == [("assets/local.png", md.parent / "local.png")]
    assert new_content == "![本地](assets/local.png)"


def test_plan_zip_external_untouched(zip_env):
    from mdtool.desktop.qtui.tabs.file_browser import plan_zip_bundle
    media, md = zip_env
    content = "![外](https://example.com/a.png) ![数据](data:image/png;base64,AA)"
    new_content, members, skipped = plan_zip_bundle(content, md, media_root=media)
    assert members == [] and skipped == []
    assert new_content == content
