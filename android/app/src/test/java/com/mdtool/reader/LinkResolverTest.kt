package com.mdtool.reader

import com.mdtool.reader.kb.extractTitleFromMarkdown
import com.mdtool.reader.resolver.LinkResolver
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class LinkResolverTest {

    // ── parse ──

    @Test
    fun parse_validImageUrl() {
        val ref = LinkResolver.parse("http://127.0.0.1:8765/images/image-20201215174726729.png")
        assertEquals(LinkResolver.MediaRef("images", "image-20201215174726729.png"), ref)
    }

    @Test
    fun parse_defaultPortWhenMissing() {
        val ref = LinkResolver.parse("http://127.0.0.1/assets/doc-123.pdf")
        assertEquals(LinkResolver.MediaRef("assets", "doc-123.pdf"), ref)
    }

    @Test
    fun parse_rejectsForeignHost() {
        assertNull(LinkResolver.parse("http://example.com/images/a.png"))
    }

    @Test
    fun parse_rejectsForeignPort() {
        assertNull(LinkResolver.parse("http://127.0.0.1:9999/images/a.png"))
    }

    @Test
    fun parse_rejectsNonMediaPath() {
        assertNull(LinkResolver.parse("http://127.0.0.1:8765/notes/readme.md"))
    }

    @Test
    fun parse_stripsQueryAndFragment() {
        val ref = LinkResolver.parse("http://127.0.0.1:8765/images/a.png?x=1#frag")
        assertEquals(LinkResolver.MediaRef("images", "a.png"), ref)
    }

    // ── rewrite ──

    @Test
    fun rewrite_externalUntouched() {
        val url = "https://cdn.example.com/img.png"
        assertEquals(url, LinkResolver.rewrite(url))
    }

    @Test
    fun rewrite_customPortPassthrough() {
        // 身份判定 = host:port 一致；自定义端口的本机链接按外链透传（与 Python 版同语义）
        val url = "http://127.0.0.1:9000/images/a.png"
        assertEquals(url, LinkResolver.rewrite(url))
    }

    // ── rewriteMarkdown ──

    @Test
    fun rewriteMarkdown_keepsCustomPortLinks() {
        val md = "![图](http://127.0.0.1:9000/images/a.png)"
        assertEquals(md, LinkResolver.rewriteMarkdown(md))
    }

    @Test
    fun rewriteMarkdown_keepsDefaultLinksVerbatim() {
        val md = "![图](http://127.0.0.1:8765/images/a.png) 和 [外链](https://example.com/x)"
        assertEquals(md, LinkResolver.rewriteMarkdown(md))
    }

    @Test
    fun rewriteMarkdown_skipsInlineCode() {
        val md = "代码里 `http://127.0.0.1:8765/images/a.png` 不改"
        assertEquals(md, LinkResolver.rewriteMarkdown(md))
    }

    @Test
    fun rewriteMarkdown_skipsFencedCodeBlock() {
        val md = "前文\n\n```\n![raw](http://127.0.0.1:9000/images/a.png)\n```\n\n后文"
        assertEquals(md, LinkResolver.rewriteMarkdown(md))
    }

    // ── 标题提取 ──

    @Test
    fun title_firstAtxHeading() {
        assertEquals("标题一", extractTitleFromMarkdown("前文\n\n# 标题一\n\n正文"))
    }

    @Test
    fun title_noneWhenMissing() {
        assertNull(extractTitleFromMarkdown("没有标题的笔记"))
    }
}
