"""hexo 博客仓 → 笔记库博客目录 B 的一次性迁移（阶段 3 入口）。

只拷贝不改源仓——源仓在站点验证无误前保持可用（hexo 回退路径仍依赖它）。
幂等性靠拒绝重跑保障：``B/manifest.json`` 已存在即 raise，重复迁移会制造
重复文件并让 id 平移失去单次语义。

平移规则：
- ``source/_posts/**.md`` → ``B/<相对路径>``；清单键从仓内 posix 路径改写为
  KB 根相对路径（``source/_posts/x.md`` → ``markdown/blog/x.md``），id 原值
  平移
- 清单里有、``_posts`` 里没有、但 ``_drafts`` 里有同名文件的（hexo 语义：
  发布过的文章退回草稿）→ 文件照拷入 B、键指向草稿落点、``selected=False``，
  id 保留——重新勾选即以原 URL 复刊
- 清单里有、两侧都没有的真失联条目：键照改、id 照留（URL 身份归历史），报告列出
- ``_posts`` 里存在但清单没有的（hexo 时代漏建索引）：按路径序补新 id
- ``source/_drafts/**`` → 拷贝为普通笔记、未入册的不进清单（未勾选 = 草稿）
- ``source/assets/**`` → ``B/assets/**`` 原样拷贝（文章内 ``assets/x`` 相对
  链接零改写，sitegen 的根绝对化语义不变）
- ``_config.yml`` → site.json（load_legacy_spec / load_legacy_deploy 提取）
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mdtool.core.blog import is_hexo_source, posts_dir, drafts_dir, assets_dir
from mdtool.core.kb_bundle import resolve_notes_dir
from mdtool.core.sitegen.generate import SiteSpec
from mdtool.core.sitegen.kb import SiteConfig, manifest_path, save_site_config
from mdtool.core.sitegen.legacy import (
    load_legacy_deploy,
    load_legacy_manifest,
    load_legacy_spec,
)
from mdtool.core.sitegen.manifest import Manifest, ManifestEntry, save_manifest

_POSTS_PREFIX = "source/_posts/"


@dataclass(frozen=True)
class MigratePlan:
    """迁移计划（只读探查的产物）；apply 前可人工核对各清单。"""

    src_root: Path
    kb_root: Path
    blog_dir: Path            # B：笔记树下的博客目录（已含目标名）
    post_copies: tuple[tuple[Path, Path], ...]    # (源, 目标)，含清单内外全部
    draft_copies: tuple[tuple[Path, Path], ...]
    asset_copies: tuple[tuple[Path, Path], ...]
    manifest: Manifest        # 键已改写、unindexed 已补 id 的目标清单
    spec: SiteSpec
    deploy_repo: str
    deploy_branch: str
    unindexed: tuple[str, ...]      # 清单外补了新 id 的文章（B 内相对路径）
    drafted: tuple[str, ...]        # 退回草稿的（id 保留、selected=False）
    kept_missing: tuple[str, ...]   # 真失联（两侧都没有，id 保留）


@dataclass(frozen=True)
class MigrateReport:
    posts: int
    drafts: int
    assets: int
    unindexed: tuple[str, ...]
    drafted: tuple[str, ...]
    kept_missing: tuple[str, ...]


def _walk_md(root: Path) -> list[tuple[str, Path]]:
    """root 下全部 .md，返回 (posix 相对路径, 绝对路径)，按路径序确定性排列。"""
    out = [(p.relative_to(root).as_posix(), p)
           for p in root.rglob("*.md") if p.is_file()]
    return sorted(out)


def plan_hexo_to_kb(src_root: Path, kb_root: Path,
                    blog_dirname: str) -> MigratePlan:
    """探查两侧并产出计划；不改任何文件。源仓不是 hexo 形态则 raise。"""
    src_root = Path(src_root)
    kb_root = Path(kb_root)
    if not is_hexo_source(src_root):
        raise ValueError(f"不是 hexo 博客源（缺 _config.yml 或 source/_posts/）: {src_root}")

    notes_root = resolve_notes_dir(kb_root)
    notes_rel = notes_root.relative_to(kb_root).as_posix() if \
        notes_root != kb_root else ""
    blog_dir = notes_root / blog_dirname

    def kb_note_key(rel_in_posts: str) -> str:
        parts = [p for p in (notes_rel, blog_dirname, rel_in_posts) if p]
        return str(PurePosixPath(*parts))

    legacy = load_legacy_manifest(src_root)
    src_posts = posts_dir(src_root)
    draft_root = drafts_dir(src_root)
    draft_rels = {rel for rel, _p in _walk_md(draft_root)} \
        if draft_root.is_dir() else set()

    # 仓内路径 → KB 根相对新键；清单条目逐一平移（含退稿/失联条目，id 保留）
    key_map: dict[str, str] = {}
    entries: list[ManifestEntry] = []
    kept_missing: list[str] = []
    drafted: list[str] = []
    for e in sorted(legacy.entries, key=lambda e: e.id):
        rel = e.note[len(_POSTS_PREFIX):] if e.note.startswith(_POSTS_PREFIX) \
            else e.note
        new_key = kb_note_key(rel)
        key_map[e.note] = new_key
        if (src_posts / rel).is_file():
            selected = e.selected
        elif rel in draft_rels:
            # hexo 语义：发布过的文章退回 _drafts——id 保留、暂不发布
            selected = False
            drafted.append(rel)
        else:
            selected = e.selected
            kept_missing.append(f"{e.note} (id={e.id})")
        entries.append(ManifestEntry(id=e.id, note=new_key,
                                     published=e.published,
                                     selected=selected))

    # 清单外的文章（hexo 漏建索引）：按路径序补新 id
    indexed = set(key_map)
    unindexed: list[str] = []
    next_id = legacy.next_id
    for rel, _p in _walk_md(src_posts):
        old_key = f"{_POSTS_PREFIX}{rel}"
        if old_key in indexed:
            continue
        entries.append(ManifestEntry(id=next_id, note=kb_note_key(rel)))
        key_map[old_key] = kb_note_key(rel)
        unindexed.append(rel)
        next_id += 1

    manifest = Manifest(next_id, tuple(entries))

    post_rels = {rel for rel, _p in _walk_md(src_posts)}
    post_copies = [
        (p, blog_dir / rel) for rel, p in _walk_md(src_posts)]
    draft_copies = [
        (p, blog_dir / rel) for rel, p in _walk_md(draft_root)
        if draft_root.is_dir() and rel not in post_rels]
    asset_root = assets_dir(src_root)
    asset_copies = [
        (p, blog_dir / "assets" / p.relative_to(asset_root))
        for p in sorted(asset_root.rglob("*")) if p.is_file()] \
        if asset_root.is_dir() else []

    spec = load_legacy_spec(src_root)
    repo, branch = load_legacy_deploy(src_root)
    return MigratePlan(
        src_root=src_root, kb_root=kb_root, blog_dir=blog_dir,
        post_copies=tuple(post_copies), draft_copies=tuple(draft_copies),
        asset_copies=tuple(asset_copies), manifest=manifest, spec=spec,
        deploy_repo=repo, deploy_branch=branch,
        unindexed=tuple(unindexed), drafted=tuple(drafted),
        kept_missing=tuple(kept_missing))


def apply_hexo_to_kb(plan: MigratePlan) -> MigrateReport:
    """执行计划；拒绝重跑（B/manifest.json 已存在即 raise）。"""
    if manifest_path(plan.blog_dir).exists():
        raise RuntimeError(
            f"已存在 {manifest_path(plan.blog_dir)}，疑似重复迁移；"
            "如确需重来请先清空目标目录")
    for src, dst in (*plan.post_copies, *plan.draft_copies,
                     *plan.asset_copies):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    save_manifest(manifest_path(plan.blog_dir), plan.manifest)
    save_site_config(plan.blog_dir, SiteConfig(
        spec=plan.spec, deploy_repo=plan.deploy_repo,
        deploy_branch=plan.deploy_branch))
    return MigrateReport(
        posts=len(plan.post_copies), drafts=len(plan.draft_copies),
        assets=len(plan.asset_copies), unindexed=plan.unindexed,
        drafted=plan.drafted, kept_missing=plan.kept_missing)
