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
 *
 * 暗色：跟随系统（Activity 随 uiMode 重建 → startServer 重建本类），
 * 不用 CSS prefers-color-scheme 是因为 WebView 对它的支持随系统版本不稳定。
 */
class MarkdownRenderer(
    private val isDark: Boolean = false,
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

    /** 阅读页背景色（供 WebView setBackgroundColor，避免暗色下白闪）。 */
    val pageBackgroundColor: String = pageBackgroundHex(isDark)

    /** 完整页面（含阅读排版 CSS），供 /note/ 与首页使用。 */
    fun pageHtml(title: String, bodyHtml: String): String =
        """
        <!DOCTYPE html>
        <html lang="zh-CN"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta name="color-scheme" content="${if (isDark) "dark" else "light"}">
        <title>${escapeHtml(title)}</title>
        <style>${css()}</style></head>
        <body>$bodyHtml</body></html>
        """.trimIndent()

    private fun escapeHtml(s: String): String =
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    private fun css(): String {
        val dark = isDark
        val bg = if (dark) DARK_BG else LIGHT_BG
        val fg = if (dark) DARK_FG else LIGHT_FG
        val border = if (dark) DARK_BORDER else LIGHT_BORDER
        val codeBg = if (dark) DARK_CODE_BG else LIGHT_CODE_BG
        val preBg = if (dark) DARK_PRE_BG else LIGHT_PRE_BG
        val quoteFg = if (dark) DARK_QUOTE_FG else LIGHT_QUOTE_FG
        val link = if (dark) DARK_LINK else LIGHT_LINK
        return """
            body { font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", sans-serif;
                   max-width: 46rem; margin: 0 auto; padding: 1rem 1.1rem 3rem;
                   background: $bg; color: $fg; line-height: 1.7; font-size: 16px; }
            img { max-width: 100%; height: auto; border-radius: 6px; }
            h1,h2,h3,h4 { line-height: 1.3; margin-top: 1.6em; }
            h1 { font-size: 1.6em; border-bottom: 1px solid $border; padding-bottom: .3em; }
            h2 { font-size: 1.3em; }
            code { background: $codeBg; padding: .15em .35em; border-radius: 4px; font-size: .9em; }
            pre { background: $preBg; padding: .8rem 1rem; border-radius: 8px; overflow-x: auto; }
            pre code { background: none; padding: 0; }
            blockquote { border-left: 4px solid $border; margin: 0; padding: 0 1em; color: $quoteFg; }
            table { border-collapse: collapse; width: 100%; }
            th, td { border: 1px solid $border; padding: .4em .6em; text-align: left; }
            th { background: $preBg; }
            a { color: $link; }
            hr { border: none; border-top: 1px solid $border; margin: 2em 0; }
            ul, ol { padding-left: 1.5em; }
            .task-list-item { list-style: none; margin-left: -1.2em; }
            .task-list-item-checkbox { margin-right: .5em; }
        """.trimIndent()
    }

    companion object {
        /** 阅读页背景色（WebView 用，与 CSS body 背景一致）。 */
        fun pageBackgroundHex(isDark: Boolean): String = if (isDark) DARK_BG else LIGHT_BG

        // 亮色调色板（与 Compose LightColors 对齐）
        private const val LIGHT_BG = "#FAFAFC"
        private const val LIGHT_FG = "#1F2328"
        private const val LIGHT_BORDER = "#E2E4E8"
        private const val LIGHT_CODE_BG = "#F2F3F5"
        private const val LIGHT_PRE_BG = "#F6F8FA"
        private const val LIGHT_QUOTE_FG = "#57606A"
        private const val LIGHT_LINK = "#0969DA"

        // 暗色调色板（与 Compose DarkColors 对齐）
        private const val DARK_BG = "#121316"
        private const val DARK_FG = "#D5DAE0"
        private const val DARK_BORDER = "#33363D"
        private const val DARK_CODE_BG = "#1C1E22"
        private const val DARK_PRE_BG = "#1A1C20"
        private const val DARK_QUOTE_FG = "#9AA4AF"
        private const val DARK_LINK = "#8AB4F8"
    }
}
