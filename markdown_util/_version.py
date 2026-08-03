"""运行时版本号读取。

优先级：
1. build 时 PyInstaller 注入的 VERSION 环境变量（打包产物）
2. 根目录 pyproject.toml 的 version（开发环境，monorepo 权威源）
3. fallback "dev"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_FALLBACK = "dev"


def get_version() -> str:
    # 1. PyInstaller 打包时通过环境变量注入（见 scripts/build.py spec）。
    #    Frozen 模式下读不到 pyproject.toml，必须用注入值。
    if getattr(sys, "frozen", False):
        v = os.environ.get("MDTOOL_VERSION")
        if v:
            return v

    # 2. 开发环境：从 monorepo 根 pyproject.toml 读取（权威源）。
    #    markdown_util/ 的父目录就是仓库根。
    try:
        root = Path(__file__).resolve().parent.parent
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            import re
            m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                return m.group(1)
    except Exception:
        pass

    # 3. 子项目自己的 pyproject（兜底）。
    try:
        root = Path(__file__).resolve().parent
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            import re
            m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                return m.group(1)
    except Exception:
        pass

    return _FALLBACK


__version__ = get_version()
