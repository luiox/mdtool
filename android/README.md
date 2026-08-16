# mdtool 阅读器（安卓端 v1）

只读浏览你的 Markdown 知识库。**不做同步**——用第三方同步工具（Syncthing / 坚果云 / OneDrive 等）把知识库文件夹同步到手机，然后在 app 里选择该文件夹即可。

## 工作机制

- app 内置本地 HTTP 服务器（`127.0.0.1:8765`，与桌面端媒体服务器端口一致）；
- 笔记里存储的 `http://127.0.0.1:8765/images/...` 链接在手机上**原样可解析**，零改写；
- 正文由 flexmark 服务端渲染为 HTML，WebView 展示；图片/附件由同一服务器提供；
- 全文搜索在端上直接扫描文件（支持正则，无效正则按字面匹配）；
- 附件下载按 `meta.db` 回传原始文件名（缺失时降级用时间戳名）。

## 构建（需要 Android Studio）

本仓库开发机没有 Android SDK，代码未经本地编译验证——请用 Android Studio 构建：

1. Android Studio（新版即可）→ Open → 选择本目录 `android/`；
2. 等待 Gradle Sync（首次会自动下载依赖，需要网络）；
3. Run 到真机或模拟器。

> 版本基线：AGP 8.5.2 / Kotlin 2.0.21 / Compose BOM 2024.10.01 / flexmark 0.64.8 / NanoHTTPD 2.3.1。
> 如你的环境需要更高版本，在 `gradle/libs.versions.toml` 中升级（注意三者兼容性）。
> `gradle-wrapper.properties` 已提供；wrapper 的二进制 jar 由 Android Studio 首次 Sync 自动处理。

## 使用

1. 在手机上用同步工具（如 [Syncthing](https://syncthing.net/)）同步知识库根目录
   （含 `notes/`、`images/`、`assets/`、`meta.db`）；
2. 打开 mdtool 阅读器 → 选择知识库文件夹（系统文件选择器）；
3. 浏览目录树 → 打开笔记阅读（图片/附件自动加载）→ 搜索。

## 目录结构

```
android/
├── gradle/libs.versions.toml      # 依赖版本目录
└── app/src/main/java/com/mdtool/reader/
    ├── MainActivity.kt            # Compose UI：选夹/树浏览/搜索/WebView 阅读
    ├── kb/KbRepository.kt         # DocumentFile 遍历、meta.db 原始名、端上搜索
    ├── render/MarkdownRenderer.kt # flexmark 渲染（GFM 表格/删除线/任务列表）
    ├── resolver/LinkResolver.kt   # 与桌面 link_resolver 同语义（规范 §2）
    ├── server/LocalServer.kt      # NanoHTTPD：/note /images /assets /index
    └── ui/Theme.kt                # Material3 主题
```

## v1 明确不做

编辑/上传/GC、db 容器读取、同步功能、服务器地址配置（固定 127.0.0.1:8765）。
