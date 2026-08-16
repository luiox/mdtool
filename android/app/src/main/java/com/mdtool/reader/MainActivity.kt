package com.mdtool.reader

import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.mdtool.reader.kb.KbRepository
import com.mdtool.reader.kb.KbRepository.SearchHit
import com.mdtool.reader.render.MarkdownRenderer
import com.mdtool.reader.server.LocalServer
import com.mdtool.reader.ui.MdtoolTheme
import fi.iki.elonen.NanoHTTPD
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {

    private var server: LocalServer? = null
    internal var serverError: String? = null

    /** 系统当前是否暗色模式（Activity 随 uiMode 变化重建，服务随之带上新配色）。 */
    internal fun isDarkMode(): Boolean =
        (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
            Configuration.UI_MODE_NIGHT_YES

    internal fun startServer(repo: KbRepository) {
        // 切换知识库时重启服务器，避免旧服务器继续指向旧根目录
        server?.stop()
        server = null
        val s = LocalServer(repo, MarkdownRenderer(isDark = isDarkMode()), applicationContext.contentResolver)
        try {
            s.start(NanoHTTPD.SOCKET_READ_TIMEOUT, false)
            server = s
            serverError = null
        } catch (e: Exception) {
            serverError = "本地服务器启动失败: ${e.message}"
        }
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        super.onDestroy()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MdtoolTheme {
                App(activity = this)
            }
        }
    }
}

private enum class Screen { Home, Search, Reader }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun App(activity: MainActivity) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("mdtool", Context.MODE_PRIVATE) }

    var rootUriString by remember { mutableStateOf(prefs.getString("kb_uri", null)) }
    var repo by remember {
        mutableStateOf(
            rootUriString?.let {
                runCatching { KbRepository(context, Uri.parse(it)) }.getOrNull()
            }
        )
    }
    // rememberSaveable：系统切换昼夜模式会重建 Activity，保住浏览位置
    var screen by rememberSaveable { mutableStateOf(Screen.Home) }
    var currentFolder by rememberSaveable { mutableStateOf("") }
    var openNotePath by rememberSaveable { mutableStateOf<String?>(null) }
    var refreshKey by remember { mutableIntStateOf(0) }

    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocumentTree()
    ) { uri ->
        if (uri != null) {
            try {
                context.contentResolver.takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            } catch (_: Exception) {
                // 某些文件提供方不支持持久授权，本次会话仍可用
            }
            prefs.edit().putString("kb_uri", uri.toString()).apply()
            rootUriString = uri.toString()
            repo = runCatching { KbRepository(context, uri) }.getOrNull()
            currentFolder = ""
            screen = Screen.Home
        }
    }

    LaunchedEffect(repo) {
        if (repo != null) activity.startServer(repo!!)
    }

    Scaffold { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                repo == null -> PlaceholderScreen(onPick = { picker.launch(null) })
                screen == Screen.Search -> SearchScreen(
                    repo = repo!!,
                    onBack = { screen = Screen.Home },
                    onOpen = { path ->
                        openNotePath = path
                        screen = Screen.Reader
                    },
                )
                screen == Screen.Reader && openNotePath != null -> ReaderScreen(
                    notePath = openNotePath!!,
                    onBack = { screen = Screen.Home },
                )
                else -> HomeScreen(
                    repo = repo!!,
                    currentFolder = currentFolder,
                    refreshKey = refreshKey,
                    onFolder = { currentFolder = it },
                    onOpenNote = { path ->
                        openNotePath = path
                        screen = Screen.Reader
                    },
                    onSearch = { screen = Screen.Search },
                    onChangeRoot = { picker.launch(null) },
                    onRefresh = {
                        repo!!.refresh()
                        refreshKey++
                    },
                    serverError = activity.serverError,
                )
            }
        }
    }
}

// ── 未选择知识库 ──

@Composable
private fun PlaceholderScreen(onPick: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("mdtool 阅读器", style = MaterialTheme.typography.headlineMedium)
        Text(
            "用同步工具（Syncthing / 坚果云等）把知识库文件夹同步到手机后，\n在这里选择该文件夹即可阅读。",
            modifier = Modifier.padding(vertical = 16.dp),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedButton(onClick = onPick) { Text("选择知识库文件夹") }
    }
}

// ── 首页：文件夹树 ──

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HomeScreen(
    repo: KbRepository,
    currentFolder: String,
    refreshKey: Int,
    onFolder: (String) -> Unit,
    onOpenNote: (String) -> Unit,
    onSearch: () -> Unit,
    onChangeRoot: () -> Unit,
    onRefresh: () -> Unit,
    serverError: String?,
) {
    var folders by remember(currentFolder, refreshKey) { mutableStateOf<List<KbRepository.Folder>>(emptyList()) }
    var notes by remember(currentFolder, refreshKey) { mutableStateOf<List<KbRepository.Note>>(emptyList()) }
    var loading by remember(currentFolder, refreshKey) { mutableStateOf(true) }

    LaunchedEffect(currentFolder, refreshKey) {
        loading = true
        val listing = withContext(Dispatchers.IO) { repo.listDir(currentFolder) }
        folders = listing.folders
        notes = listing.notes
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(if (currentFolder.isEmpty()) "知识库" else currentFolder.substringAfterLast('/'))
                        Text(
                            currentFolder.ifEmpty { "（知识库根目录）" },
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                        )
                    }
                },
                navigationIcon = {
                    if (currentFolder.isNotEmpty()) {
                        IconButton(onClick = { onFolder(currentFolder.substringBeforeLast('/', "")) }) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回上级")
                        }
                    }
                },
                actions = {
                    IconButton(onClick = onSearch) { Icon(Icons.Filled.Search, "搜索") }
                    IconButton(onClick = onRefresh) { Icon(Icons.Filled.Refresh, "刷新") }
                    IconButton(onClick = onChangeRoot) { Icon(Icons.Filled.FolderOpen, "更换知识库") }
                },
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            serverError?.let {
                Text(
                    it,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            when {
                loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                folders.isEmpty() && notes.isEmpty() -> Box(
                    Modifier.fillMaxSize(), contentAlignment = Alignment.Center
                ) {
                    Text("此目录为空", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                else -> LazyColumn {
                    items(folders, key = { "d:${it.path}" }) { folder ->
                        ListItem(
                            headlineContent = { Text(folder.name) },
                            leadingContent = { Icon(Icons.Filled.Folder, null) },
                            modifier = Modifier.clickable { onFolder(folder.path) },
                        )
                    }
                    if (folders.isNotEmpty() && notes.isNotEmpty()) {
                        item { HorizontalDivider() }
                    }
                    items(notes, key = { "n:${it.path}" }) { note ->
                        ListItem(
                            headlineContent = { Text(note.name.removeSuffix(".md")) },
                            leadingContent = { Icon(Icons.Filled.Description, null) },
                            modifier = Modifier.clickable { onOpenNote(note.path) },
                        )
                    }
                }
            }
        }
    }
}

// ── 搜索 ──

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SearchScreen(
    repo: KbRepository,
    onBack: () -> Unit,
    onOpen: (String) -> Unit,
) {
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<SearchHit>>(emptyList()) }
    var searching by remember { mutableStateOf(false) }
    var searchRequest by remember { mutableStateOf<String?>(null) }
    var searchSeq by remember { mutableIntStateOf(0) }
    var scope by remember { mutableStateOf(KbRepository.SearchScope.BOTH) }
    var scanned by remember { mutableIntStateOf(0) }
    var hitCount by remember { mutableIntStateOf(0) }

    // searchSeq/scope 变化即取消旧搜索（协程重启，walk 内 ensureActive 响应）
    LaunchedEffect(searchSeq, scope) {
        val q = searchRequest ?: return@LaunchedEffect
        searching = true
        scanned = 0
        hitCount = 0
        val r = withContext(Dispatchers.IO) {
            repo.search(q, scope) { s, h ->
                scanned = s
                hitCount = h
            }
        }
        results = r
        searching = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it },
                        placeholder = { Text("搜索文件名与内容（正则）") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回") }
                },
                actions = {
                    TextButton(
                        enabled = query.isNotBlank() && !searching,
                        onClick = {
                            searchRequest = query.trim()
                            searchSeq++
                        },
                    ) { Text("搜索") }
                },
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
            ) {
                FilterChip(
                    selected = scope == KbRepository.SearchScope.BOTH,
                    onClick = { scope = KbRepository.SearchScope.BOTH },
                    label = { Text("全部") },
                )
                FilterChip(
                    selected = scope == KbRepository.SearchScope.NAME,
                    onClick = { scope = KbRepository.SearchScope.NAME },
                    label = { Text("文件名") },
                )
                FilterChip(
                    selected = scope == KbRepository.SearchScope.BODY,
                    onClick = { scope = KbRepository.SearchScope.BODY },
                    label = { Text("内容") },
                )
            }
            Box(Modifier.fillMaxSize()) {
                when {
                    searching -> Column(
                        modifier = Modifier.align(Alignment.Center),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        CircularProgressIndicator()
                        Text(
                            "已扫描 $scanned 篇 · 命中 $hitCount",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 12.dp),
                        )
                    }
                    results.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            if (searchRequest == null) "输入关键词开始搜索"
                            else "无结果",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    else -> LazyColumn {
                        items(results, key = { it.rel }) { hit ->
                            Column(Modifier.clickable { onOpen(hit.rel) }.padding(horizontal = 16.dp, vertical = 10.dp)) {
                                Text(hit.rel, style = MaterialTheme.typography.titleSmall)
                                if (hit.title.isNotEmpty()) {
                                    Text(hit.title, style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.primary)
                                }
                                if (hit.snippet.isNotEmpty() && hit.snippet != hit.title) {
                                    Text(hit.snippet, style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2)
                                }
                            }
                            HorizontalDivider()
                        }
                    }
                }
            }
        }
    }
}

// ── 阅读页：WebView ← 本地服务器 ──

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ReaderScreen(notePath: String, onBack: () -> Unit) {
    val url = remember(notePath) {
        "http://127.0.0.1:${LocalServer.PORT}/note/${LocalServer.urlEncodePath(notePath)}"
    }
    val isDark = isSystemInDarkTheme()
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(notePath.substringAfterLast('/')) },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回") }
                },
            )
        },
    ) { padding ->
        AndroidView(
            factory = { ctx ->
                WebView(ctx).apply {
                    settings.javaScriptEnabled = false
                    setBackgroundColor(Color.parseColor(MarkdownRenderer.pageBackgroundHex(isDark)))
                    webViewClient = WebViewClient()
                }
            },
            update = { it.loadUrl(url) },
            modifier = Modifier.fillMaxSize().padding(padding),
        )
    }
}
