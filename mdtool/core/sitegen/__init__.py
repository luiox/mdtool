"""sitegen——mdtool 自带静态站点生成器（替代 Hexo 的渲染/生成职责）。

背景见 docs/博客工作流.md 与博客文章管理规划：mdtool 已接管博客源的
识别/脚手架/图片/校验/发布命令，Hexo 只剩"markdown→HTML 渲染器 + 主题"。
sitegen 补上最后一块，目标形态是**文章活在笔记库、发布清单是一个 manifest
JSON、id 只增不改保证 ``article/<id>.html`` URL 永久稳定**。

分层（同 mdtool 惯例，纯函数 + 数据类，UI/写盘编排留给调用方）：

- :mod:`manifest` —— 发布清单：笔记路径 → 文章 id 的持久映射（只增语义）
- :mod:`render`   —— markdown → HTML（markdown-it-py + Pygments + 复制按钮）
- :mod:`generate` —— 整站生成：文章页/首页/归档/标签/atom/sitemap → 输出目录
- :mod:`legacy`   —— 过渡期适配器：读现有 Hexo 源仓（_posts + passage_index.json），
  阶段 3（文章迁入笔记库）完成后退役

模板与静态资源是包内数据目录（templates/ static/），打包进 PyInstaller 由
build.py spec 的 datas 显式携带；目录定位统一走 :func:`resource_dir`。
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_dir() -> Path:
    """sitegen 数据目录（templates/ static/ 的父目录）。

    frozen 态（PyInstaller onefile）从解包目录取——spec 把数据落在
    ``mdtool/core/sitegen/`` 相对路径下，与源码布局一致；源码态直接取本文件
    所在目录。唯一事实是目录布局，两种形态同构。
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "mdtool" / "core" / "sitegen"  # noqa: SLF001
    return Path(__file__).parent
