# mdtool

Python 包：`core/`（知识库核心）+ `desktop/`（PySide6 桌面管理器）+ `cli/`（命令行）。

## 功能

- **知识库**（主流程）
  - 双模式：db 容器（"内存态"，临时落地 + watchdog 回写）/ 散装文件夹，支持互相导入导出
  - 正则全文搜索
  - 导出：单篇/多篇 ZIP（markdown + 图片）、db 格式、导出为文件夹
- **维护工具**：本地媒体服务器（图片/附件托管 + meta.db + GC）、图片校验（AST/正则）、图片迁移、空格转下划线修复
- **CLI**：`import-media`——图片/附件复制入库 + meta.db 登记 + 输出 8765 规范 URL

## 运行

```powershell
# 仓库根目录
uv sync --extra ast
uv run python -m mdtool.desktop   # 桌面应用
uv run mdtool --help              # 命令行（console script，等价 python -m mdtool）
```

## 打包

```powershell
python scripts/build.py           # spec 由 build.py 生成到 build/，统一走根目录脚本
```

详细文档见仓库根 [`README.md`](../README.md) 与 [`docs/`](../docs/)。

## 目录结构

```
mdtool/
├── core/                # 与界面无关：link_resolver / kb_bundle / utils / server（media_server、meta_db、notes_db）
├── desktop/             # PySide6：main_qt 入口 + qtui/（main_window、widgets、workers、icons、tabs）
├── cli/                 # import-media 等脚本化维护动作（业务逻辑在 core）
├── tests/               # 纯函数测试（link_resolver / kb_bundle / file_browser 打包计划 / cli）
└── _version.py          # 运行时版本号（读仓库根 pyproject.toml，frozen 态读随包副本）
```
