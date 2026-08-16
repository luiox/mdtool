package com.mdtool.reader.resolver

/**
 * 媒体链接解析与改写 — 与 markdown_util/link_resolver.py 同一语义（docs/知识库规范.md §2）。
 *
 * 手机端固定监听 127.0.0.1:8765（与桌面一致），因此笔记里存储的
 * `http://127.0.0.1:8765/images/...` 在手机上原样可解析（零改写）。
 * 身份判定 = host:port 完全一致；其余（外链、自定义端口的本机链接）一律原样透传，
 * 绝不改动——自定义端口场景 v1 不支持（无服务器地址配置）。
 */
object LinkResolver {
    const val DEFAULT_HOST = "127.0.0.1"
    const val DEFAULT_PORT = 8765

    private val urlRegex = Regex("""^https?://([^/:]+)(?::(\d+))?/(images|assets)/([^/\s]+)$""")

    data class MediaRef(val category: String, val name: String)

    /** 识别本知识库媒体 URL → MediaRef；非本知识库 URL 返回 null。 */
    fun parse(url: String, host: String = DEFAULT_HOST, port: Int = DEFAULT_PORT): MediaRef? {
        val m = urlRegex.matchEntire(url.trim()) ?: return null
        if (m.groupValues[1] != host) return null
        val p = m.groupValues[2].toIntOrNull() ?: DEFAULT_PORT
        if (p != port) return null
        val name = m.groupValues[4].substringBefore('?').substringBefore('#')
        return MediaRef(m.groupValues[3], name)
    }

    /** 改写为本地服务器地址；未知 URL 原样返回。 */
    fun rewrite(url: String, host: String = DEFAULT_HOST, port: Int = DEFAULT_PORT): String {
        val ref = parse(url, host, port) ?: return url
        return "http://$host:$port/${ref.category}/${ref.name}"
    }

    /**
     * 改写整篇 markdown 中所有本知识库媒体链接；跳过代码块/行内代码；
     * 其余内容逐字保留。
     */
    fun rewriteMarkdown(text: String, host: String = DEFAULT_HOST, port: Int = DEFAULT_PORT): String {
        val codeRegex = Regex("""(`[^`\n]+`|```[\s\S]*?```|~~~[\s\S]*?~~~)""")
        val destRegex = Regex("""(!?\[[^\]]*\])\(([^)\n]+)\)""")

        // 先用占位符保护代码区，避免其内部链接被改写
        val placeholders = mutableMapOf<String, String>()
        var i = 0
        val protected = codeRegex.replace(text) { m ->
            val key = "\u0000MDTOOL_CODE_${i++}\u0000"
            placeholders[key] = m.value
            key
        }
        val result = destRegex.replace(protected) { m ->
            val rawDest = m.groupValues[2].trim()
            val dest = rawDest.split(' ').firstOrNull() ?: ""
            val rewritten = rewrite(dest, host, port)
            if (rewritten == dest) m.value else m.value.replaceFirst(dest, rewritten)
        }
        return placeholders.entries.fold(result) { acc, (k, v) -> acc.replace(k, v) }
    }
}
