package com.mdtool.reader.kb

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.net.Uri
import android.util.Log
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlin.coroutines.coroutineContext

/** 笔记树的标题提取：第一行 ATX 标题（简单实现，供列表/搜索展示）。 */
fun extractTitleFromMarkdown(md: String): String? {
    for (line in md.lineSequence()) {
        val t = line.trimStart()
        if (t.startsWith("#") && t.length > 1 && t[1].isWhitespace()) {
            val text = t.substring(1).trim().trimEnd('#').trim()
            if (text.isNotEmpty()) return text
        }
    }
    return null
}

/**
 * 知识库目录访问（DocumentsContract 直查 + TreeIndex 内存缓存）。
 *
 * KB 根：<root>/markdown/（笔记，任意子目录）+ <root>/images/ + <root>/assets/（媒体）+ meta.db。
 * 若所选目录没有 markdown/（旧布局 notes/ 或用户直接选了笔记目录），按兜底规则取笔记根。
 *
 * 缓存与失效：目录列表/搜索结果基于内存缓存，同步工具落地新文件后
 * 调 [refresh]（UI 的刷新按钮）；单篇阅读（[readNote]）总是直读文件，不受缓存影响。
 */
class KbRepository(context: Context, treeUri: Uri) {

    data class Note(val path: String, val name: String, val docId: String)
    data class Folder(val path: String, val name: String, val docId: String)
    data class SearchHit(val rel: String, val title: String, val snippet: String)
    data class DirListing(val folders: List<Folder>, val notes: List<Note>)

    /** 搜索范围。 */
    object SearchScope {
        const val NAME = "name"
        const val BODY = "body"
        const val BOTH = "both"
    }

    private val appContext = context.applicationContext
    private val index = TreeIndex(appContext, treeUri)
    private val contentResolver = appContext.contentResolver

    companion object {
        private const val TAG = "KbRepository"
        private val MEDIA_DIRS = setOf("images", "assets")

        /** 搜索用文件内容缓存的条目数与有效期（重复搜索/改关键词免重读全库）。 */
        private const val CONTENT_CACHE_LIMIT = 256
        private const val CONTENT_CACHE_TTL_MS = 60_000L
    }

    /** 笔记根：markdown/ 优先，退旧布局 notes/；都没有则把所选目录本身当笔记根。 */
    private val notesRootId: String by lazy {
        index.findChild(index.rootId(), "markdown")?.docId
            ?: index.findChild(index.rootId(), "notes")?.docId
            ?: index.rootId()
    }

    // ── 目录浏览 ──

    /** 列目录（一次缓存查询同时拿子目录与笔记）。 */
    fun listDir(folderPath: String): DirListing {
        val dirId = resolve(folderPath) ?: return DirListing(emptyList(), emptyList())
        val folders = mutableListOf<Folder>()
        val notes = mutableListOf<Note>()
        for (e in index.children(dirId)) {
            val rel = joinPath(folderPath, e.name)
            if (e.isDir) {
                folders.add(Folder(rel, e.name, e.docId))
            } else if (e.name.endsWith(".md", ignoreCase = true)) {
                notes.add(Note(rel, e.name, e.docId))
            }
        }
        return DirListing(folders, notes)
    }

    suspend fun listFolders(folderPath: String): List<Folder> =
        withContext(Dispatchers.IO) { listDir(folderPath).folders }

    suspend fun listNotes(folderPath: String): List<Note> =
        withContext(Dispatchers.IO) { listDir(folderPath).notes }

    suspend fun findNote(path: String): Note? = withContext(Dispatchers.IO) {
        val segs = path.split('/')
        if (segs.isEmpty() || segs.any { it.isEmpty() }) return@withContext null
        var cur = resolve(segs.dropLast(1).joinToString("/")) ?: return@withContext null
        val entry = index.findChild(cur, segs.last()) ?: return@withContext null
        if (entry.isDir) null else Note(path, segs.last(), entry.docId)
    }

    /** 读笔记正文：总是直读文件（阅读要最新内容，不走缓存）。 */
    suspend fun readNote(note: Note): String = withContext(Dispatchers.IO) {
        try {
            contentResolver.openInputStream(index.uriFor(note.docId))
                ?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
        } catch (e: Exception) {
            Log.w(TAG, "读取笔记失败: ${note.path}", e)
            ""
        }
    }

    // ── 媒体 ──

    /** 媒体文件 content URI（images/assets 下的时间戳名文件）。 */
    fun mediaUri(category: String, name: String): Uri? {
        val dir = index.findChild(index.rootId(), category) ?: return null
        return index.findChild(dir.docId, name)?.let { index.uriFor(it.docId) }
    }

    /**
     * 附件原始名（meta.db → 回传 Content-Disposition 用）。
     * meta.db 缺失/损坏时返回 null，调用方降级用时间戳名。
     */
    suspend fun originalName(category: String, name: String): String? = withContext(Dispatchers.IO) {
        metaName(name)
    }

    private var metaNames: Map<String, String>? = null
    private var metaNamesLoaded = false

    private fun metaName(name: String): String? {
        if (!metaNamesLoaded) {
            metaNames = loadMetaNames() ?: emptyMap()
            metaNamesLoaded = true
        }
        return metaNames?.get(name)
    }

    private fun loadMetaNames(): Map<String, String>? {
        try {
            val dbDoc = index.findChild(index.rootId(), "meta.db") ?: return null
            val tmp = File.createTempFile("kb_meta", ".db", appContext.cacheDir)
            contentResolver.openInputStream(index.uriFor(dbDoc.docId))?.use { input ->
                tmp.outputStream().use { output -> input.copyTo(output) }
            }
            val db = SQLiteDatabase.openDatabase(tmp.absolutePath, null, SQLiteDatabase.OPEN_READONLY)
            try {
                val map = HashMap<String, String>()
                for (table in listOf("images", "assets")) {
                    db.rawQuery(
                        "SELECT timestamp_name, original_name FROM $table", null
                    ).use { c ->
                        while (c.moveToNext()) map[c.getString(0)] = c.getString(1)
                    }
                }
                return map
            } finally {
                db.close()
                tmp.delete()
            }
        } catch (e: Exception) {
            Log.w(TAG, "meta.db 读取失败，附件将按时间戳名下载", e)
            return null
        }
    }

    // ── 搜索 ──

    /**
     * 全文搜索（端上遍历）：scope 见 [SearchScope]。正则无效时按字面子串匹配。
     * 文件名匹配只用目录树缓存（零文件 IO）；正文匹配读文件（60s 内容缓存）。
     * [onProgress] 每处理一篇回调（已扫描数, 命中数），在 IO 线程；协程取消即中止遍历。
     */
    suspend fun search(
        pattern: String,
        scope: String = SearchScope.BOTH,
        onProgress: (scanned: Int, hits: Int) -> Unit = { _, _ -> },
    ): List<SearchHit> = withContext(Dispatchers.IO) {
        val rx = try {
            Regex(pattern, RegexOption.IGNORE_CASE)
        } catch (e: Exception) {
            null
        }
        val match: (String) -> Boolean = { s ->
            if (rx != null) rx.containsMatchIn(s) else s.contains(pattern, ignoreCase = true)
        }
        val hits = mutableListOf<SearchHit>()
        var scanned = 0
        val ctx = coroutineContext // 捕获协程上下文，供非挂起的局部函数检查取消

        fun walk(folderPath: String, dirId: String) {
            for (e in index.children(dirId)) {
                ctx.ensureActive() // 响应取消/新搜索
                val rel = joinPath(folderPath, e.name)
                if (e.isDir) {
                    if (e.name.lowercase() in MEDIA_DIRS) continue
                    walk(rel, e.docId)
                    continue
                }
                if (!e.name.endsWith(".md", ignoreCase = true)) continue
                scanned++
                val nameHit = scope != SearchScope.BODY && match(e.name)
                var body: String? = null
                if (!nameHit && scope != SearchScope.NAME) {
                    body = cachedContent(e.docId)
                    if (!(body.isNotEmpty() && match(body))) {
                        onProgress(scanned, hits.size)
                        continue
                    }
                }
                val snippet = when {
                    nameHit -> e.name
                    else -> body!!.lineSequence().firstOrNull { match(it) }?.trim()?.take(160) ?: ""
                }
                val title = body?.let { extractTitleFromMarkdown(it) } ?: ""
                hits.add(SearchHit(rel, title, snippet))
                onProgress(scanned, hits.size)
            }
        }
        walk("", notesRootId)
        hits
    }

    /** 清空目录/内容缓存（刷新按钮、更换知识库后调用）。 */
    fun refresh() {
        index.clear()
        contentCache.clear()
    }

    // ── 内部 ──

    /** 路径 → docId；路径分段逐级查缓存表，整条路径命中缓存时零 IPC。 */
    private fun resolve(folderPath: String): String? {
        var cur = notesRootId
        if (folderPath.isEmpty()) return cur
        for (seg in folderPath.split('/')) {
            cur = index.findChild(cur, seg)?.docId ?: return null
        }
        return cur
    }

    private class TimedValue(val at: Long, val text: String)

    /** 搜索正文缓存：docId → (时间, 内容)，LRU + TTL。 */
    private val contentCache = object : LinkedHashMap<String, TimedValue>(64, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, TimedValue>): Boolean =
            size > CONTENT_CACHE_LIMIT
    }

    @Synchronized
    private fun cachedContent(docId: String): String {
        val now = System.currentTimeMillis()
        val hit = contentCache[docId]
        if (hit != null && now - hit.at < CONTENT_CACHE_TTL_MS) return hit.text
        val text = try {
            contentResolver.openInputStream(index.uriFor(docId))
                ?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
        } catch (e: Exception) {
            ""
        }
        contentCache[docId] = TimedValue(now, text)
        return text
    }

    private fun joinPath(parent: String, name: String): String =
        if (parent.isEmpty()) name else "$parent/$name"
}
