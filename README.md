# mdtool（mdnote_util）

我的 Markdown 笔记工具集。解决 Markdown 笔记里**图片/附件管理混乱**的问题：本地图床 + 元信息数据库 + Typora 一键上传 + 可选笔记容器模式。

## 仓库组成

| 目录 | 说明 |
| --- | --- |
| `markdown_util/` | PySide6 桌面应用（推荐入口 `main_qt.py`；旧 tkinter 版 `main.py` 保留作回退） |
| `libmarkdown/` | 幂等 Markdown AST 读写库：读入 AST → 修改节点 → 写回，未修改部分逐字保留（字节级一致） |
| `typora-uploader/` | Rust 编写的 Typora 自定义图片上传器，把图片上传到本地图床并输出 URL |
| `scripts/` | 打包（`build.py`）与版本管理（`version.py`） |

`markdown_util` 共 6 个标签页：

- **笔记库**：笔记容器模式（SQLite）+ 正则全文搜索 + 导入/导出文件夹
- **文件浏览器**：浏览项目根目录下的 `.md`，支持打包 ZIP、迁移图片到图床
- **本地媒体服务器**：图片/附件托管服务 + `meta.db` 元信息查询 + GC
- **空格转下划线修复**：批量修复文件/目录名空格问题
- **图片校验**：校验 Markdown 里的图片链接是否有效（AST / 正则两种解析）
- **图片迁移**：把散落本地的图片批量迁移上传到图床并改写链接

## 核心设计：图片/附件管理规范

完整规范见 [`markdown_util/图片和附件管理规范.md`](markdown_util/图片和附件管理规范.md)，要点：

- 本地图床服务器默认监听 `127.0.0.1:8765`（与 Typora 约定一致，可在应用内调整端口）。
- 媒体根目录下**扁平化**存放：`images/`（图片）、`assets/`（附件）、`meta.db`（SQLite 元信息：原始文件名、大小、MIME、上传时间）。
- 图片链接格式：`![img](http://127.0.0.1:8765/images/image-YYYYMMDDHHMMSSnnn.png)`；附件链接：`http://127.0.0.1:8765/assets/YYYYMMDDHHMMSSnnn.pdf`（下载时按原始文件名回传）。
- 图片由 Typora 调用 `typora-uploader` 上传；图片/附件也可以在应用内直接上传、按名称模糊查询（笔记库支持正则搜索），并支持 GC 清理“文件已丢失”的数据库记录。

## 笔记的两种模式

- **db 模式（笔记库容器）**：所有笔记正文存在单个 `notes.db`（SQLite）里，目录结构由 `path` 字段隐式表达；图片仍以 `http://127.0.0.1:8765/...` URL 引用，不进库。编辑时经临时文件 + watchdog 自动回写。优点：单文件可整体备份、正则全文搜索、无空目录/孤儿文件。
- **散装模式**：笔记是真实 `.md` 文件散落在目录树中，Typora 直接打开文件夹编辑。
- **互转**：笔记库标签页的“导入文件夹”（散装 → db）与“导出为文件夹”（db → 展开成散装目录）。

## 快速开始

前置：Rust 工具链（cargo）、[uv](https://docs.astral.sh/uv/)、Python ≥ 3.11。

```powershell
# 1. 编译 Typora 上传器（Rust）
cd typora-uploader
cargo build --release          # 产物: target/release/typora-uploader.exe

# 2. 安装 Python 依赖（含本地 libmarkdown，--extra ast 必装）
cd ..\markdown_util
uv sync --extra ast

# 3. 启动桌面应用（PySide6 版）
uv run python main_qt.py
```

> 旧版 tkinter 入口：`uv run python main.py`（高 DPI 下模糊，仅回退用）。

### Typora 集成（自定义命令）

1. 启动应用 → “本地媒体服务器”标签页 → 设置媒体根目录 → “启动服务器”。
2. Typora 偏好设置 → 图像 → 上传服务 → 自定义命令，填：
   ```
   path/to/typora-uploader
   ```
   按你本机的编译产物实际路径填写（Windows 下例如 `D:\mdtool\typora-uploader\target\release\typora-uploader.exe`）。
也可以直接编辑 `%APPDATA%\Typora\conf\conf.user.json`，写 `"imageUploader": "custom"` 与 `"customImageUploader": "<exe路径>"`。
3. 想让“插入图片即上传”，Typora 偏好设置 → 图像 → 插入图片时 → 选择“上传图片”。

## 打包

`scripts/build.py` 一键打包两个 exe（本地与 CI 共用同一逻辑）：

```powershell
python scripts/build.py               # onefile，打包 MarkdownUtilQt.exe + typora-uploader.exe
python scripts/build.py --onedir      # onedir 模式（启动快，目录形式）
python scripts/build.py --skip-rust   # 只打包 Python 侧
python scripts/build.py --skip-py     # 只打包 Rust 侧
```

产物输出到 `dist/`。依赖：uv（Python 侧）+ cargo（Rust 侧）。

## 版本管理

版本单一来源是**根目录 `pyproject.toml`**，`scripts/version.py` 负责同步到所有子项目（`markdown_util`、`libmarkdown`、`typora-uploader`）。

```powershell
python scripts/version.py current                # 打印当前版本
python scripts/version.py bump patch|minor|major # 计算下一版本（不写文件）
python scripts/version.py sync                   # 根版本同步到所有子项目
python scripts/version.py set 1.2.3              # 设置根版本并同步
```

遵循 SemVer；`0.x.y` 阶段 minor 视为破坏性变更（`0.X.Y → 0.(X+1).0`）。

发版流程：`bump` → `git tag vX.Y.Z` → `git push origin vX.Y.Z` → CI 自动构建并发布 GitHub Release。

## CI / CD

- `.github/workflows/ci.yml`：main 分支 push / PR 触发 —— `markdown_util` 导入冒烟 + Qt boot 冒烟、`libmarkdown` pytest、`typora-uploader` cargo check。
- `.github/workflows/release.yml`：`v*` tag 触发 —— 同步版本号 → `scripts/build.py` 打包两个 exe → 上传 artifact 并创建 GitHub Release。

## 应用配置位置

| 配置 | 路径 |
| --- | --- |
| 媒体服务器（根目录/端口/子目录） | `~/.monocodes_media.json` |
| 笔记库（db 路径/编辑器命令） | `~/.mdtool_notes.json` |
| 笔记库数据库 | 默认 `~/.mdtool/notes.db`（可在笔记库标签页“打开/新建笔记库”更换） |
| Typora 上传配置 | `%APPDATA%\Typora\conf\conf.user.json` |
