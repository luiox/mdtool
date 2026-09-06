"""过渡期适配器：现有 Hexo 源仓 → sitegen 输入（阶段 1/2 用，阶段 3 退役）。

博客源仓（luiox.github.io-src）的文章与 id 索引搬到 sitegen 的模型：

- ``passage_index.json``（``{"next_index": N, "posts": [{"path","id"}]}``）
  → :class:`Manifest`，note 键原样保留仓内 posix 路径——存量 28 个 id 逐一
  平移，老 URL 全部存活。
- ``_config.yml`` 只用正则抽标量键（title/url/author/language），不为此
  引入 PyYAML——与 blog.py 的 front-matter 子集解析同一取舍。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mdtool.core.sitegen.generate import SiteSpec
from mdtool.core.sitegen.generate import PostInput
from mdtool.core.sitegen.manifest import Manifest, ManifestEntry

_INDEX_NAME = "passage_index.json"
_CONFIG_NAME = "_config.yml"


def load_legacy_spec(blog_root: Path) -> SiteSpec:
    """从 ``_config.yml`` 抽站点元信息；缺键用 Hexo 缺省兜底。"""
    text = _read(Path(blog_root) / _CONFIG_NAME)

    def scalar(key: str, default: str) -> str:
        m = re.search(rf"^{key}:\s*(.+?)\s*$", text, re.MULTILINE)
        if not m:
            return default
        v = m.group(1).strip().strip("'\"")
        return v or default

    return SiteSpec(
        title=scalar("title", "My Blog"),
        url=scalar("url", "").rstrip("/"),
        author=scalar("author", ""),
        language=scalar("language", "zh-CN"))


def load_legacy_manifest(blog_root: Path) -> Manifest:
    """``passage_index.json`` → Manifest（next_index 接棒 next_id）。"""
    try:
        data = json.loads(_read(Path(blog_root) / _INDEX_NAME))
    except (json.JSONDecodeError, ValueError):
        data = {}
    entries = tuple(
        ManifestEntry(id=int(p["id"]), note=str(p["path"]))
        for p in data.get("posts", [])
        if isinstance(p, dict) and p.get("id") and p.get("path"))
    entries = tuple(sorted(entries, key=lambda e: e.id))
    next_id = max([int(data.get("next_index", 1)),
                   *(e.id + 1 for e in entries), 1])
    return Manifest(next_id, entries)


def legacy_post_inputs(blog_root: Path,
                       manifest: Manifest) -> tuple[list[PostInput], tuple[str, ...]]:
    """清单 → 现存文件的 PostInput 列表；失联条目（文件没了）记入 skipped。"""
    root = Path(blog_root)
    inputs: list[PostInput] = []
    missing: list[str] = []
    for e in sorted(manifest.entries, key=lambda e: e.id):
        p = root / e.note
        if p.is_file():
            inputs.append(PostInput(id=e.id, path=p, note=e.note))
        else:
            missing.append(f"{e.note} (id={e.id})")
    return inputs, tuple(missing)


def legacy_assets_dir(blog_root: Path) -> Path:
    return Path(blog_root) / "source" / "assets"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
