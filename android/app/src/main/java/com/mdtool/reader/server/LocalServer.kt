package com.mdtool.reader.server

import android.content.ContentResolver
import com.mdtool.reader.kb.KbRepository
import com.mdtool.reader.kb.extractTitleFromMarkdown
import com.mdtool.reader.render.MarkdownRenderer
import fi.iki.elonen.NanoHTTPD
import java.net.URLDecoder
import java.net.URLEncoder
import kotlinx.coroutines.runBlocking

/**
 * 手机端本地媒体/渲染服务器（127.0.0.1:8765，与桌面一致）。
 *
 * 路由：
 * - `/` / `/index`            笔记列表页（HTML）
 * - `/note/<path>`            渲染后的笔记页（flexmark）
 * - `/images/<name>`          图片（浏览器展示）
 * - `/assets/<name>`          附件（Content-Disposition 回传原始名，查 meta.db）
 */
class LocalServer(
    private val repo: KbRepository,
    private val renderer: MarkdownRenderer,
    private val contentResolver: ContentResolver,
) : NanoHTTPD("127.0.0.1", PORT) {

    override fun serve(session: IHTTPSession): Response {
        val uri = session.uri
        return try {
            when {
                uri == "/" || uri == "/index" -> serveIndex()
                uri.startsWith("/note/") -> serveNote(decode(uri.removePrefix("/note/")))
                uri.startsWith("/images/") -> serveMedia("images", decode(uri.removePrefix("/images/")))
                uri.startsWith("/assets/") -> serveMedia("assets", decode(uri.removePrefix("/assets/")))
                else -> newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "Not Found")
            }
        } catch (e: Exception) {
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR, MIME_PLAINTEXT, "Error: ${e.message}"
            )
        }
    }

    // ── 路由实现 ──

    private fun serveIndex(): Response {
        val sb = StringBuilder()
        sb.append("<h1>📚 知识库</h1>")
        fun walk(folderPath: String, depth: Int) {
            val dirs = runBlocking { repo.listFolders(folderPath) }
            val notes = runBlocking { repo.listNotes(folderPath) }
            for (d in dirs) {
                sb.append("<h${(depth + 2).coerceAtMost(4)}>📁 ${escapeHtml(d.name)}</h${(depth + 2).coerceAtMost(4)}>")
                walk(d.path, depth + 1)
            }
            if (notes.isNotEmpty()) {
                sb.append("<ul>")
                for (n in notes) {
                    sb.append("<li><a href=\"/note/${urlEncodePath(n.path)}\">${escapeHtml(n.name)}</a></li>")
                }
                sb.append("</ul>")
            }
        }
        walk("", 0)
        return html(renderer.pageHtml("知识库", sb.toString()))
    }

    private fun serveNote(notePath: String): Response {
        val note = runBlocking { repo.findNote(notePath) }
            ?: return newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "笔记不存在: $notePath")
        val md = runBlocking { repo.readNote(note) }
        val title = extractTitleFromMarkdown(md) ?: note.name
        return html(renderer.pageHtml(title, renderer.renderMarkdown(md)))
    }

    private fun serveMedia(category: String, name: String): Response {
        val doc = repo.mediaFile(category, name)
            ?: return newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "Not Found")
        val input = try {
            contentResolver.openInputStream(doc.uri)
        } catch (e: Exception) {
            null
        } ?: return newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "Not Found")
        val headers = mutableMapOf<String, String>()
        if (category == "assets") {
            val original = runBlocking { repo.originalName(category, name) } ?: name
            headers["Content-Disposition"] =
                "attachment; filename*=UTF-8''${URLEncoder.encode(original, "UTF-8")}"
        }
        val response = newChunkedResponse(Response.Status.OK, mimeFor(name), input)
        headers.forEach { (k, v) -> response.addHeader(k, v) }
        return response
    }

    // ── helpers ──

    private fun html(body: String) =
        newFixedLengthResponse(Response.Status.OK, "text/html; charset=utf-8", body)

    private fun decode(s: String): String =
        if ('%' in s) {
            try {
                URLDecoder.decode(s, "UTF-8")
            } catch (e: Exception) {
                s
            }
        } else {
            s
        }

    private fun escapeHtml(s: String): String =
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    private fun mimeFor(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
        "png" -> "image/png"
        "jpg", "jpeg" -> "image/jpeg"
        "gif" -> "image/gif"
        "webp" -> "image/webp"
        "svg" -> "image/svg+xml"
        "bmp" -> "image/bmp"
        "pdf" -> "application/pdf"
        "zip" -> "application/zip"
        "drawio", "xml" -> "application/xml"
        "md" -> "text/markdown; charset=utf-8"
        else -> "application/octet-stream"
    }

    companion object {
        const val PORT = 8765

        /** 路径 → URL：逐段编码（保留 / 分隔）。 */
        fun urlEncodePath(path: String): String =
            path.split('/').joinToString("/") {
                URLEncoder.encode(it, "UTF-8").replace("+", "%20")
            }
    }
}
