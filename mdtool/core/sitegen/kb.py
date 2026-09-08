"""KB 形态的博客源（阶段 3）：文章活在笔记库，B 目录两个 JSON 定全部事实。

博客工作目录 B 是笔记树下的任意文件夹（示例 ``markdown/blog/``）：

- ``B/site.json``     站点配置——SiteSpec 标量 + deploy repo/branch + 主题名；
  一次性迁移从 hexo ``_config.yml`` 提取，之后手改即生效，不引入 YAML 依赖
- ``B/manifest.json`` id 映射（manifest.py 同构；note 键 = KB 根相对 posix
  路径，与 hexo 时代的仓内路径键不同构，迁移时改写平移）
- ``B/assets/``       文章媒体；笔记树遍历按目录名排除 ``assets``（任意层
  级，既有约定），故对库其余部分不可见
- ``B/theme/``        可选主题目录（templates/ static/，逐文件覆盖包内
  默认主题）；不存在即全用包内默认，契约见 docs/博客主题定制.md

build_site 不感知来源（只吃 PostInput）；本模块负责把 B 的两个文件变成它
要的输入，与 legacy.py 构成双形态后端（hexo 源仓回退路径保留，hexo 主题
归 hexo 自己管，不走这套）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mdtool.core.sitegen.generate import PostInput, SiteSpec
from mdtool.core.sitegen.manifest import Manifest

_SITE_NAME = "site.json"
_MANIFEST_NAME = "manifest.json"
_THEME_DEFAULT = "theme"


@dataclass(frozen=True)
class SiteConfig:
    """site.json 的解码形态；deploy 为空表示未配置（调用方拒绝部署）。

    theme 是 B 下的主题目录名（相对路径，禁止绝对/上跳），目录不存在时
    生成仍可用（全默认主题），故存名字而非 Path。
    """

    spec: SiteSpec
    deploy_repo: str = ""
    deploy_branch: str = "main"
    theme: str = _THEME_DEFAULT


def _clean_theme_name(v) -> str:
    """theme 键 → 安全的相对目录名。

    强制相对化：绝对路径/盘符/上跳/空段一律回退默认主题名——theme 只能
    指向 B 内部，绝不指到 B 之外。
    """
    name = str(v or _THEME_DEFAULT).strip().replace("\\", "/").strip("/")
    parts = name.split("/")
    if not name or ":" in name or any(p in ("", ".", "..") for p in parts):
        return _THEME_DEFAULT
    return name


def load_site_config(blog_dir: Path) -> SiteConfig:
    """读 site.json；缺失/坏 JSON 回退默认（title 用目录名），首建友好。"""
    data: dict = {}
    try:
        raw = json.loads((Path(blog_dir) / _SITE_NAME).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = raw
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        pass
    deploy = data.get("deploy") or {}
    if not isinstance(deploy, dict):
        deploy = {}
    return SiteConfig(
        spec=SiteSpec(
            title=str(data.get("title") or Path(blog_dir).name),
            url=str(data.get("url") or "").rstrip("/"),
            author=str(data.get("author") or ""),
            language=str(data.get("language") or "zh-CN"),
            per_page=int(data.get("per_page") or 10),
            feed_limit=int(data.get("feed_limit") or 20),
            pygments_style=str(data.get("pygments_style") or "friendly"),
            copy_label=str(data.get("copy_label") or "复制"),
        ),
        deploy_repo=str(deploy.get("repo") or ""),
        deploy_branch=str(deploy.get("branch") or "main"),
        theme=_clean_theme_name(data.get("theme")),
    )


def save_site_config(blog_dir: Path, config: SiteConfig) -> None:
    data = {
        "title": config.spec.title,
        "url": config.spec.url,
        "author": config.spec.author,
        "language": config.spec.language,
        "per_page": config.spec.per_page,
        "feed_limit": config.spec.feed_limit,
        "pygments_style": config.spec.pygments_style,
        "copy_label": config.spec.copy_label,
        "theme": config.theme,
        "deploy": {"repo": config.deploy_repo, "branch": config.deploy_branch},
    }
    path = Path(blog_dir) / _SITE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def theme_root(blog_dir: Path, config: SiteConfig) -> Optional[Path]:
    """B 下的主题目录；不存在返回 None（build_site 走纯默认主题）。"""
    p = Path(blog_dir) / config.theme
    return p if p.is_dir() else None


def manifest_path(blog_dir: Path) -> Path:
    return Path(blog_dir) / _MANIFEST_NAME


def assets_dir(blog_dir: Path) -> Path:
    return Path(blog_dir) / "assets"


def manifest_post_inputs(kb_root: Path, manifest: Manifest,
                         ) -> tuple[list[PostInput], tuple[str, ...]]:
    """清单 → PostInput（path = kb_root/note）；只取 selected 条目。

    未勾选条目不参与生成也不校验存在性（暂不发布 = 一切免谈）；勾选但
    文件失联的记入 missing，交由生成报告与桌面端提示。
    """
    root = Path(kb_root)
    inputs: list[PostInput] = []
    missing: list[str] = []
    for e in sorted(manifest.entries, key=lambda e: e.id):
        if not e.selected:
            continue
        p = root / e.note
        if p.is_file():
            inputs.append(PostInput(id=e.id, path=p, note=e.note))
        else:
            missing.append(f"{e.note} (id={e.id})")
    return inputs, tuple(missing)
