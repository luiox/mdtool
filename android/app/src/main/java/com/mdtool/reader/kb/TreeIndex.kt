package com.mdtool.reader.kb

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract

/**
 * 知识库目录树索引：DocumentsContract 直查 + 内存缓存。
 *
 * 不用 DocumentFile 的原因：它的每次 name/isDirectory/isFile 访问都是一次
 * 跨进程查询，findFile 还是线性扫描——目录一多，进出文件夹就是上百次 IPC。
 * 这里对每个目录只做一次 query（一次拿回全部子项的 docId/名称/类型），
 * 之后同目录的访问全部走内存缓存。
 *
 * 缓存失效：SAF 没有可靠的变更通知，同步工具写入后由调用方 refresh()。
 */
class TreeIndex(private val context: Context, private val treeUri: Uri) {

    /** 目录树中的一个条目（文件或子目录）。 */
    class Entry(val docId: String, val name: String, val isDir: Boolean)

    private val rootDocId: String = DocumentsContract.getTreeDocumentId(treeUri)
    private val cache = HashMap<String, List<Entry>>()

    /** 某目录的全部子项（按名称排序）；查询失败返回空表（同样缓存，避免反复失败重试）。 */
    @Synchronized
    fun children(docId: String): List<Entry> {
        cache[docId]?.let { return it }
        val entries = queryChildren(docId)
        cache[docId] = entries
        return entries
    }

    @Synchronized
    fun findChild(parentDocId: String, name: String): Entry? {
        // findFile 语义：名称精确匹配；目录内重名文件由 SAF 保证不存在
        return children(parentDocId).firstOrNull { it.name == name }
    }

    @Synchronized
    fun clear() = cache.clear()

    fun rootId(): String = rootDocId

    /** 条目 → 可直接 openInputStream 的 content URI。 */
    fun uriFor(docId: String): Uri =
        DocumentsContract.buildDocumentUriUsingTree(treeUri, docId)

    private fun queryChildren(docId: String): List<Entry> {
        val uri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, docId)
        val projection = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
        )
        val result = mutableListOf<Entry>()
        try {
            context.contentResolver.query(uri, projection, null, null, null)?.use { c ->
                while (c.moveToNext()) {
                    val id = c.getString(0) ?: continue
                    val name = c.getString(1) ?: continue
                    val mime = c.getString(2) ?: continue
                    result.add(Entry(id, name, mime == DocumentsContract.Document.MIME_TYPE_DIR))
                }
            }
        } catch (_: Exception) {
            // 单个目录查询失败（提供方临时不可用等）按空处理，不中断整体遍历
        }
        result.sortBy { it.name }
        return result
    }
}
