package com.mdtool.reader.kb

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.util.Log
import androidx.documentfile.provider.DocumentFile
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

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
 * 知识库目录访问（SAF DocumentFile）。
 *
 * KB 根：<root>/notes/（笔记，任意子目录）+ <root>/images/ + <root>/assets/（媒体）+ meta.db。
 * 若所选目录没有 notes/（用户直接选了笔记目录），则把所选目录本身当笔记根。
 */
class KbRepository(private val context: Context, private val rootDoc: DocumentFile) {

    data class Note(val path: String, val name: String, val doc: DocumentFile)
    data class Folder(val path: String, val name: String, val doc: DocumentFile)
    data class SearchHit(val rel: String, val title: String, val snippet: String)

    companion object {
        private const val TAG = "KbRepository"
        private val MEDIA_DIRS = setOf("images", "assets")
    }

    /** DocumentFile 没有 openInputStream()，统一走 ContentResolver。 */
    private fun openStream(doc: DocumentFile): java.io.InputStream? =
        try {
            context.contentResolver.openInputStream(doc.uri)
        } catch (e: Exception) {
            null
        }

    fun notesRoot(): DocumentFile = rootDoc.findFile("notes") ?: rootDoc

    suspend fun listFolders(folderPath: String): List<Folder> = withContext(Dispatchers.IO) {
        val dir = resolve(folderPath) ?: return@withContext emptyList()
        dir.listFiles().orEmpty()
            .filter { it.isDirectory && it.name != null }
            .sortedBy { it.name }
            .map { Folder(joinPath(folderPath, it.name!!), it.name!!, it) }
    }

    suspend fun listNotes(folderPath: String): List<Note> = withContext(Dispatchers.IO) {
        val dir = resolve(folderPath) ?: return@withContext emptyList()
        dir.listFiles().orEmpty()
            .filter { it.isFile && it.name != null && it.name!!.endsWith(".md", ignoreCase = true) }
            .sortedBy { it.name }
            .map { Note(joinPath(folderPath, it.name!!), it.name!!, it) }
    }

    suspend fun findNote(path: String): Note? = withContext(Dispatchers.IO) {
        val segs = path.split('/')
        if (segs.isEmpty() || segs.any { it.isEmpty() }) return@withContext null
        val parent = resolve(segs.dropLast(1).joinToString("/"))
        val doc = parent?.findFile(segs.last()) ?: return@withContext null
        if (!doc.isFile) null else Note(path, segs.last(), doc)
    }

    suspend fun readNote(note: Note): String = withContext(Dispatchers.IO) {
        try {
            openStream(note.doc)?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
        } catch (e: Exception) {
            Log.w(TAG, "读取笔记失败: ${note.path}", e)
            ""
        }
    }

    /** 媒体文件（images/assets 下的时间戳名文件）。 */
    fun mediaFile(category: String, name: String): DocumentFile? {
        val dir = rootDoc.findFile(category) ?: return null
        return dir.findFile(name)
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
            val dbDoc = rootDoc.findFile("meta.db") ?: return null
            val tmp = File(context.cacheDir, "kb_meta.db")
            openStream(dbDoc)?.use { input ->
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

    /** 全文搜索（端上遍历）：scope ∈ {"name", "body", "both"}。正则无效时按字面子串匹配。 */
    suspend fun search(pattern: String, scope: String = "both"): List<SearchHit> = withContext(Dispatchers.IO) {
        val rx = try {
            Regex(pattern, RegexOption.IGNORE_CASE)
        } catch (e: Exception) {
            null
        }
        val match: (String) -> Boolean = { s ->
            if (rx != null) rx.containsMatchIn(s) else s.contains(pattern, ignoreCase = true)
        }
        val hits = mutableListOf<SearchHit>()

        fun walk(folderPath: String) {
            val dir = resolve(folderPath) ?: return
            for (f in dir.listFiles().orEmpty()) {
                val name = f.name ?: continue
                val rel = joinPath(folderPath, name)
                if (f.isDirectory) {
                    if (name.lowercase() in MEDIA_DIRS) continue
                    walk(rel)
                    continue
                }
                if (!name.endsWith(".md", ignoreCase = true)) continue
                val nameHit = scope != "body" && match(name)
                var body: String? = null
                val bodyHit = scope != "name" && run {
                    body = try {
                        openStream(f)?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
                    } catch (e: Exception) {
                        ""
                    }
                    body!!.isNotEmpty() && match(body!!)
                }
                if (!nameHit && !bodyHit) continue
                val snippet = when {
                    nameHit -> name
                    else -> body!!.lineSequence().firstOrNull { match(it) }?.trim()?.take(160) ?: ""
                }
                val title = body?.let { extractTitleFromMarkdown(it) } ?: ""
                hits.add(SearchHit(rel, title, snippet))
            }
        }
        walk("")
        hits
    }

    private fun resolve(folderPath: String): DocumentFile? {
        if (folderPath.isEmpty()) return notesRoot()
        var cur = notesRoot()
        for (seg in folderPath.split('/')) {
            cur = cur.findFile(seg) ?: return null
        }
        return cur
    }

    private fun joinPath(parent: String, name: String): String =
        if (parent.isEmpty()) name else "$parent/$name"
}
