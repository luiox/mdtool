# AGENTS.md

mdtool 是个人 Markdown 知识库管理器 + 工具集，三端分工：桌面 PySide6 管理器（mdtool/desktop）+ Typora 专职编辑 + 安卓只读阅读器（android）。改代码前先读 [docs/定位与架构分析.md](docs/定位与架构分析.md)（分工与路线图）与 [docs/知识库规范.md](docs/知识库规范.md)（链接模型与目录布局）；安卓端另见 [docs/安卓端规划.md](docs/安卓端规划.md)。

## Repository layout

```
mdtool/          Python 包：core/（link_resolver、kb_bundle、server 媒体服务器与 db）+ desktop/（PySide6 界面，入口 main_qt.py）+ cli/（import-media 命令行）
libmarkdown/     自研幂等 Markdown AST 读写库（uv_build 打包，零依赖）
typora-uploader/ Rust 版 Typora 图片上传 CLI（ureq + serde_json，仅 2 依赖）
android/         Kotlin + Compose 只读阅读器（SAF + 本地 NanoHTTPD，见 android/README.md）
scripts/         打包（build.py，本地与 CI 共用）与版本同步（version.py）
docs/            定位/架构、知识库规范、安卓端规划——规范以这里为唯一权威
build/ dist/     PyInstaller / cargo 产物目录（内容不入库）
```

## Commands

```sh
# 桌面 Python（uv；mdtool 项目就在仓库根，uv sync 后包可编辑安装，命令全部根目录直跑）
uv run python -m pytest mdtool/tests -q
uv run --directory libmarkdown python -m pytest tests -q

# Rust 上传器
cd typora-uploader && cargo check --release

# 安卓（JDK 17；SDK 路径在 android/local.properties，已 gitignore，不入库）
cd android && ./gradlew :app:assembleDebug :app:testDebugUnitTest

# 打包 / 版本（版本单一来源是根 pyproject.toml）
python scripts/build.py             # onefile 双 exe；--onedir / --skip-rust / --skip-py
python scripts/version.py sync      # bump patch|minor|major 只计算不写文件
```

### 就地跑相关检查

按改动面选最小验证，不重复跑已通过的检查；全量矩阵归 CI（ci.yml：导入冒烟 + pytest + Qt boot 冒烟 + cargo check）。

- 链接语义改动：mdtool 的 link_resolver 测试与安卓 `LinkResolverTest` 一起改——两端语义必须一致。
- zip 导出改动：`mdtool/tests/test_file_browser.py` 的 plan_zip_bundle 系列。
- UI 改动：pytest 之外跑 CI 同款 boot 冒烟；纯布局微调可只靠编译通过。

## Conventions

- **媒体链接必须经 resolver**：存储形式 = `http://127.0.0.1:8765/images|assets/<name>` 绝对 URL；任何消费方（zip 导出、手机渲染）先 `parse` → `rewrite`，非本库 URL 一律原样透传。`mdtool/core/link_resolver.py` 与 `android/.../resolver/LinkResolver.kt` 同语义，改一端必同步另一端及其测试。
- **端口 8765 是双端契约**：手机端零改写依赖两端同 host:port；不引入可配置端口，除非同时改知识库规范。
- **双模式边界**：散装文件 = 规范格式；db 容器 = 内存态（落地目录可配置）。手机端只读、不支持 db；meta.db 只在桌面写，手机只读。
- **安卓不用 DocumentFile 遍历**：SAF 的每次属性访问都是一次 IPC；目录访问一律走 `kb/TreeIndex.kt`（DocumentsContract 直查 + 内存缓存）。缓存不自动失效，同步工具落地后靠 refresh()；单篇阅读必须直读文件，不走缓存。
- **安卓渲染在服务端**：flexmark → HTML，WebView 关闭 JavaScript；新语法用 flexmark 按需扩展模块（不用 flexmark-all，会引入 openhtmltopdf 的 license 打包冲突）。
- **长任务不占界面线程**：桌面走 `mdtool/desktop/qtui/workers.py` 线程池 + 信号回 GUI；安卓走 `withContext(Dispatchers.IO)`，遍历类任务支持协程取消。
- **libmarkdown 幂等是底线**：未支持的语法走 RawBlock/Text 兜底，roundtrip 测试必须全绿；AST 是可选 extra，依赖它的功能（图片校验/迁移）必须能回退正则实现。
- **测试写纯函数**：可测逻辑抽到 UI 之外（参考 plan_zip_bundle）；UI 正确性靠 CI boot 冒烟兜底。
- **提交信息用中文**，风格 `feat|fix|refactor|build|docs: 中文摘要`；注释与 docstring 讲设计动机和约束，不复述代码。

## Editing these instructions

`CLAUDE.md` 是指向本文件的 symlink（git 以 120000 模式存储；Windows 未启用 symlink 支持时检出为内容为 `AGENTS.md` 的普通文件，属预期，勿"修复"）。只编辑 AGENTS.md；规则保持自包含，细节链接到 docs/ 对应文档，一处事实一个家。
