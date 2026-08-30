"""运行时版本号读取。

优先级：
1. 运行时环境变量 MDTOOL_VERSION（调试用覆盖入口）
2. 根目录 pyproject.toml 的 version（monorepo 权威源；开发态读仓库文件，
   frozen 态读打包时随附的副本，见 MarkdownUtilQt.spec 的 datas 注入）
3. 子项目自己的 pyproject.toml（兜底）
4. fallback "dev"
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_FALLBACK = "dev"


def _read_version_in(pyproject: Path) -> str | None:
    try:
        if not pyproject.exists():
            return None
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
        return m.group(1) if m else None
    except Exception:
        return None


def get_version() -> str:
    # 1. 显式环境变量（运行时覆盖，一般不用）。
    v = os.environ.get("MDTOOL_VERSION")
    if v:
        return v

    # 2. monorepo 根 pyproject.toml。开发态 = 仓库根；frozen 态 = PyInstaller
    #    解包目录（spec 把根 pyproject.toml 作为 data 打了进来）。
    roots = [Path(__file__).resolve().parent.parent]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            roots.insert(0, Path(meipass))
        else:
            roots.insert(0, Path(sys.executable).resolve().parent)
    for root in roots:
        v = _read_version_in(root / "pyproject.toml")
        if v:
            return v

    # 3. 子项目自己的 pyproject（兜底）。
    v = _read_version_in(Path(__file__).resolve().parent / "pyproject.toml")
    if v:
        return v

    return _FALLBACK


__version__ = get_version()


__version__ = get_version()
