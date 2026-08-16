package com.mdtool.reader.render

import com.mdtool.reader.resolver.LinkResolver
import com.vladsch.flexmark.ext.autolink.AutolinkExtension
import com.vladsch.flexmark.ext.gfm.strikethrough.StrikethroughExtension
import com.vladsch.flexmark.ext.gfm.tasklist.TaskListExtension
import com.vladsch.flexmark.ext.tables.TablesExtension
import com.vladsch.flexmark.html.HtmlRenderer
import com.vladsch.flexmark.parser.Parser
import com.vladsch.flexmark.util.data.MutableDataSet

/**
 * 服务端 markdown 渲染：flexmark（GFM 表格/删除线/任务列表/自动链接）。
 * 渲染前先经 LinkResolver 改写媒体链接（与桌面导出共用同一改写语义）。
 */
class MarkdownRenderer(
    private val host: String = LinkResolver.DEFAULT_HOST,
    private val port: Int = LinkResolver.DEFAULT_PORT,
) {
    private val options = MutableDataSet().apply {
        set(
            Parser.EXTENSIONS, listOf(
                TablesExtension.create(),
                StrikethroughExtension.create(),
                TaskListExtension.create(),
                AutolinkExtension.create(),
            )
        )
    }
    private val parser = Parser.builder(options).build()
    private val renderer = HtmlRenderer.builder(options).build()

    fun renderMarkdown(markdown: String): String {
        val rewritten = LinkResolver.rewriteMarkdown(markdown, host, port)
        return renderer.render(parser.parse(rewritten))
    }

    /** 完整页面（含阅读排版 CSS），供 /note/ 与首页使用。 */
    fun pageHtml(title: String, bodyHtml: String): String =
        """
        <!DOCTYPE html>
        <html lang="zh-CN"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>${escapeHtml(title)}</title>
        <style>$CSS</style></head>
        <body>$bodyHtml</body></html>
        """.trimIndent()

    private fun escapeHtml(s: String): String =
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    companion object {
        val CSS: String = """
            body { font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", sans-serif;
                   max-width: 46rem; margin: 0 auto; padding: 1rem 1.1rem 3rem;
                   color: #1f2328; line-height: 1.7; font-size: 16px; }
            img { max-width: 100%; height: auto; border-radius: 6px; }
            h1,h2,h3,h4 { line-height: 1.3; margin-top: 1.6em; }
            h1 { font-size: 1.6em; border-bottom: 1px solid #e2e4e8; padding-bottom: .3em; }
            h2 { font-size: 1.3em; }
            code { background: #f2f3f5; padding: .15em .35em; border-radius: 4px; font-size: .9em; }
            pre { background: #f6f8fa; padding: .8rem 1rem; border-radius: 8px; overflow-x: auto; }
            pre code { background: none; padding: 0; }
            blockquote { border-left: 4px solid #d0d7de; margin: 0; padding: 0 1em; color: #57606a; }
            table { border-collapse: collapse; width: 100%; }
            th, td { border: 1px solid #d0d7de; padding: .4em .6em; text-align: left; }
            th { background: #f6f8fa; }
            a { color: #0969da; }
            hr { border: none; border-top: 1px solid #d0d7de; margin: 2em 0; }
            ul, ol { padding-left: 1.5em; }
            .task-list-item { list-style: none; margin-left: -1.2em; }
            .task-list-item-checkbox { margin-right: .5em; }
        """.trimIndent()
    }
}
