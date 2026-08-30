#!/usr/bin/env python3
"""Monorepo 版本管理（single source of truth 在根 pyproject.toml）。

用法:
    python scripts/version.py current              打印当前版本
    python scripts/version.py bump patch|minor|major   计算并打印下一版本（不写文件）
    python scripts/version.py sync                 把根版本同步到所有子项目
    python scripts/version.py set 1.2.3            设置根版本并同步到所有子项目

遵循 https://semver.org/lang/zh-CN/ 的语义化版本规范。

关于 0.x.y 阶段（SemVer 特殊情况）：
  SemVer 规定 0.x.y 是初始开发阶段，任何 minor 变更都可能不兼容。
  本脚本在 major == 0 时做合理处理：
    - patch: 0.X.Y → 0.X.(Y+1)   （补丁/修复）
    - minor: 0.X.Y → 0.(X+1).0   （不兼容变更，0.x 阶段 minor 即 major 级别）
    - major: 0.X.Y → 1.0.0       （进入正式版）
  进入 1.0+ 后回归标准 SemVer：
    - patch: X.Y.Z → X.Y.(Z+1)
    - minor: X.Y.Z → X.(Y+1).0
    - major: X.Y.Z → (X+1).0.0
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_PYPROJECT = ROOT / "pyproject.toml"

def _setup_utf8_stdio() -> None:
    """Windows runner（默认 cp1252）下打印中文会 UnicodeEncodeError，强制 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 子项目版本写入位置（文件, 匹配正则）。正则里用 {ver} 占位。
# 根 pyproject 自身就是 mdtool 包的项目文件，无需再同步一份。
SUBPROJECTS = [
    {
        "file": ROOT / "libmarkdown" / "pyproject.toml",
        "pattern": re.compile(r'(^version\s*=\s*")([^"]+)(")', re.MULTILINE),
    },
    {
        "file": ROOT / "typora-uploader" / "Cargo.toml",
        "pattern": re.compile(r'(^version\s*=\s*")([^"]+)(")', re.MULTILINE),
    },
]


def read_root_version() -> tuple[int, int, int]:
    """读取根 pyproject.toml 的 version，返回 (major, minor, patch)。"""
    with open(ROOT_PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    ver = data["project"]["version"]
    return parse_version(ver)


def write_root_version(major: int, minor: int, patch: int) -> None:
    ver = f"{major}.{minor}.{patch}"
    text = ROOT_PYPROJECT.read_text(encoding="utf-8")
    new_text = re.sub(
        r'(^version\s*=\s*")([^"]+)(")',
        rf'\g<1>{ver}\g<3>',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    ROOT_PYPROJECT.write_text(new_text, encoding="utf-8")


def parse_version(ver: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", ver.strip())
    if not m:
        raise ValueError(f"非法版本号（需 X.Y.Z）: {ver!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def bump(major: int, minor: int, patch: int, level: str) -> tuple[int, int, int]:
    """计算 bump 后的版本。0.x 阶段特殊处理（见模块 docstring）。"""
    if level == "patch":
        return (major, minor, patch + 1)
    if level == "minor":
        # 0.x 阶段：minor 视为不兼容，递增第二位；1.0+ 标准处理。
        if major == 0:
            return (0, minor + 1, 0)
        return (major, minor + 1, 0)
    if level == "major":
        if major == 0:
            return (1, 0, 0)
        return (major + 1, 0, 0)
    raise ValueError(f"非法 bump 级别: {level!r}（需 patch|minor|major）")


def sync_to_subprojects(major: int, minor: int, patch: int) -> list[str]:
    """把版本同步到所有子项目，返回 (文件, 旧版本, 新版本) 的变更摘要列表。"""
    ver = f"{major}.{minor}.{patch}"
    changes = []
    for sub in SUBPROJECTS:
        path: Path = sub["file"]
        if not path.exists():
            changes.append(f"跳过（不存在）: {path.relative_to(ROOT)}")
            continue
        text = path.read_text(encoding="utf-8")
        m = sub["pattern"].search(text)
        old = m.group(2) if m else "<未找到>"
        new_text = sub["pattern"].sub(rf'\g<1>{ver}\g<3>', text, count=1)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changes.append(f"{path.relative_to(ROOT)}: {old} → {ver}")
        else:
            changes.append(f"{path.relative_to(ROOT)}: 已是 {ver}（无变化）")
    return changes


def main(argv: list[str]) -> int:
    _setup_utf8_stdio()
    if len(argv) < 2:
        print(__doc__)
        return 1

    cmd = argv[1]

    if cmd == "current":
        major, minor, patch = read_root_version()
        print(f"{major}.{minor}.{patch}")
        return 0

    if cmd == "bump":
        if len(argv) < 3:
            print("用法: version.py bump patch|minor|major", file=sys.stderr)
            return 1
        level = argv[2]
        major, minor, patch = read_root_version()
        new = bump(major, minor, patch, level)
        print(f"{major}.{minor}.{patch} --[{level}]--> {new[0]}.{new[1]}.{new[2]}")
        return 0

    if cmd == "sync":
        major, minor, patch = read_root_version()
        changes = sync_to_subprojects(major, minor, patch)
        for c in changes:
            print(f"  {c}")
        return 0

    if cmd == "set":
        if len(argv) < 3:
            print("用法: version.py set X.Y.Z", file=sys.stderr)
            return 1
        major, minor, patch = parse_version(argv[2])
        write_root_version(major, minor, patch)
        print(f"根版本设为 {major}.{minor}.{patch}")
        changes = sync_to_subprojects(major, minor, patch)
        for c in changes:
            print(f"  {c}")
        return 0

    print(f"未知命令: {cmd}", file=sys.stderr)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
