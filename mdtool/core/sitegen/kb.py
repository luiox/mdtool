"""KB 形态的博客源（阶段 3）：文章活在笔记库，B 目录两个 JSON 定全部事实。

博客工作目录 B 是笔记树下的任意文件夹（示例 ``markdown/blog/``）：

- ``B/site.json``     站点配置——SiteSpec 标量 + deploy repo/branch；一次性
  迁移从 hexo ``_config.yml`` 提取生成，之后手改即生效，不引入 YAML 依赖
- ``B/manifest.json`` id 映射（manifest.py 同构；note 键 = KB 根相对 posix
  路径，与 hexo 时代的仓内路径键不同构，迁移时改写平移）
- ``B/assets/``       文章媒体；笔记树遍历按目录名排除 ``assets``（任意层
  级，既有约定），故对库其余部分不可见

build_site 不感知来源（只吃 PostInput）；本模块负责把 B 的两个文件变成它
要的输入，与 legacy.py 构成双形态后端（hexo 源仓回退路径保留）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mdtool.core.sitegen.generate import PostInput, SiteSpec
from mdtool.core.sitegen.manifest import Manifest

_SITE_NAME = "site.json"
_MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class SiteConfig:
    """site.json 的解码形态；deploy 为空表示未配置（调用方拒绝部署）。"""

    spec: SiteSpec
    deploy_repo: str = ""
    deploy_branch: str = "main"


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
        ),
        deploy_repo=str(deploy.get("repo") or ""),
        deploy_branch=str(deploy.get("branch") or "main"),
    )


def save_site_config(blog_dir: Path, config: SiteConfig) -> None:
    data = {
        "title": config.spec.title,
        "url": config.spec.url,
        "author": config.spec.author,
        "language": config.spec.language,
        "per_page": config.spec.per_page,
        "feed_limit": config.spec.feed_limit,
        "deploy": {"repo": config.deploy_repo, "branch": config.deploy_branch},
    }
    path = Path(blog_dir) / _SITE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


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
