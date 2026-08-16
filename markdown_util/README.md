# markdown_util

PySide6 桌面应用：**知识库管理器 + Markdown 工具集**。入口 `main_qt.py`。

## 功能

- **知识库**（主流程）
  - 双模式：db 容器（"内存态"，临时落地 + watchdog 回写）/ 散装文件夹，支持互相导入导出
  - 正则全文搜索
  - 导出：单篇/多篇 ZIP（markdown + 图片）、db 格式、导出为文件夹
- **维护工具**：本地媒体服务器（图片/附件托管 + meta.db + GC）、图片校验（AST/正则）、图片迁移、空格转下划线修复

## 运行

```powershell
uv sync --extra ast
uv run python main_qt.py
```

## 打包

```powershell
uv run pyinstaller MarkdownUtilQt.spec   # 或统一走仓库根目录的 scripts/build.py
```

详细文档见仓库根 [`README.md`](../README.md) 与 [`docs/`](../docs/)。

## 目录结构

```
markdown_util/
├── main_qt.py           # 入口（PySide6）
├── qtui/                # Qt UI 层（main_window / widgets / workers / icons / tabs）
├── server/              # 后端：media_server（HTTP 图床）/ meta_db / notes_db
├── utils.py             # 共享工具（图片链接提取、路径解析、命名生成）
└── 图片和附件管理规范.md # 图床链接与元信息规范
```
