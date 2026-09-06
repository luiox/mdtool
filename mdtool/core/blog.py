"""Hexo 博客源管理——B0 识别/列表、B1 脚手架、B2 图片闭环的纯函数层。

博客形态是知识库规范的正当例外（docs/博客文章管理规划.md §2）：消费方是
GitHub Pages 静态托管，本地图床 URL 对读者不可达，媒体必须入库以
``assets/<name>`` 相对链接引用；Hexo ``_posts`` 单层平铺，不触发规范否决的
"任意深度相对链接移动即断链"场景。链接识别/改写走
:mod:`mdtool.core.link_resolver` 的 ``parse_hexo_dest`` / ``hexo`` 目标，
仅桌面管理侧使用。

目录约定（Hexo 标准）::

    <blog-root>/
    ├── _config.yml
    ├── scaffolds/post.md        # B1 脚手架模板（缺省用内置模板）
    └── source/
        ├── _posts/*.md          # 文章（允许子目录）
        ├── _drafts/*.md         # 草稿（列表可过滤）
        └── assets/*             # 媒体，扁平（沿用 image-时间戳命名）

写盘动作集中在 scaffold_post / apply_blog_migration / import_images 三个
入口，其余函数无副作用（plan_zip_bundle 同款分层），tests/test_blog.py 直接覆盖。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mdtool.core.link_resolver import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    is_external_url,
    iter_link_destinations,
    parse,
    parse_hexo_dest,
)
from mdtool.core.utils import (
    DEFAULT_EXTENSIONS,
    make_image_filename,
    make_image_filename_from_mtime,
    resolve_image_path,
)

CONFIG_NAME = "_config.yml"
SOURCE_DIR = "source"
POSTS_DIR = "_posts"
DRAFTS_DIR = "_drafts"
ASSETS_DIR = "assets"
SCAFFOLD_FILE = "scaffolds/post.md"

# 相对链接目标里不算"散落媒体"的文本/页面后缀；其余带后缀的都按媒体对待
_NON_MEDIA_EXTS = {".md", ".markdown", ".html", ".htm"}

DEFAULT_SCAFFOLD = """\
---
title: {{ title }}
date: {{ date }}
categories:
tags:
---

"""


def is_hexo_source(root: Path) -> bool:
    """B0 识别：含 ``_config.yml`` + ``source/_posts/`` 的目录视为博客源。"""
    root = Path(root)
    return (root / CONFIG_NAME).is_file() and (root / SOURCE_DIR / POSTS_DIR).is_dir()


def posts_dir(root: Path) -> Path:
    return Path(root) / SOURCE_DIR / POSTS_DIR


def drafts_dir(root: Path) -> Path:
    return Path(root) / SOURCE_DIR / DRAFTS_DIR


def assets_dir(root: Path) -> Path:
    return Path(root) / SOURCE_DIR / ASSETS_DIR


# ── front-matter（极简子集，无第三方依赖）──

def parse_front_matter(text: str) -> tuple[dict, str]:
    """解析 Hexo front-matter → (元数据, 正文)；无 front-matter 返回 ``({}, text)``。

    只覆盖足够列表展示与脚手架生成的子集：``key: value``（引号自动剥离）、
    flow list ``[a, b]``、块列表项 ``- x`` / ``- [a, b]``（多级分类）。
    复杂 YAML 不是目标——不为此引入 PyYAML。
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict = {}
    last_key: Optional[str] = None  # 待挂块列表项的空值键（tags: / categories:）
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if stripped == "---":
            return meta, "\n".join(lines[i + 1:])
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if last_key is not None:
                cur = meta[last_key]
                item = _fm_scalar(stripped[2:].strip())
                if isinstance(cur, list):
                    cur.append(item)
                else:
                    meta[last_key] = [cur, item]
            continue
        if ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        key, raw = key.strip(), raw.strip()
        if raw:
            meta[key] = _fm_scalar(raw)
            last_key = None
        else:
            meta[key] = []
            last_key = key
    return {}, text  # 未闭合 ---：当无 front-matter 兜底


def _fm_scalar(raw: str):
    """front-matter 标量：剥一层引号；flow list ``[a, b]`` → list（嵌套括号感知）。"""
    if raw.startswith("[") and raw.endswith("]"):
        return [_fm_scalar(p) for p in _split_flow(raw[1:-1])]
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _split_flow(inner: str) -> list[str]:
    """按顶层逗号切 flow list 内容（嵌套 ``[...]`` 内的逗号不切）。"""
    parts: list[str] = []
    depth, buf = 0, ""
    for ch in inner:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def _parse_date(value) -> Optional[datetime]:
    """front-matter date 值 → naive datetime（统一 UTC 折算，排序可比）。"""
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ── B0 文章列表 ──

@dataclass(frozen=True)
class PostInfo:
    rel: str                  # source/ 下 posix 相对路径（_posts/a.md、_drafts/b.md）
    path: Path                # 绝对路径
    title: str
    date: Optional[datetime]  # front-matter date；缺失回退文件 mtime
    categories: tuple
    tags: tuple
    draft: bool


def read_post_info(md: Path, *, source_root: Path, draft: bool = False) -> PostInfo:
    """单篇文章 → PostInfo（读失败不致命：字段取兜底值）。"""
    try:
        text = md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    meta, _ = parse_front_matter(text)
    date = _parse_date(meta.get("date"))
    if date is None:
        try:
            date = datetime.fromtimestamp(md.stat().st_mtime)
        except OSError:
            date = None
    return PostInfo(
        rel=md.relative_to(source_root).as_posix(),
        path=md,
        title=str(meta["title"]) if meta.get("title") else md.stem,
        date=date,
        categories=_tup(meta.get("categories")),
        tags=_tup(meta.get("tags")),
        draft=draft,
    )


def _tup(v) -> tuple:
    if isinstance(v, (list, tuple)):
        return tuple(v)
    return (v,) if v else ()


def list_posts(root: Path, *, include_drafts: bool = True) -> list[PostInfo]:
    """文章列表（B0）：``_posts`` 递归 + ``_drafts``（可过滤），日期倒序。"""
    src = Path(root) / SOURCE_DIR
    out: list[PostInfo] = []
    pdir = posts_dir(root)
    if pdir.is_dir():
        out += [read_post_info(p, source_root=src) for p in sorted(pdir.rglob("*.md"))]
    ddir = drafts_dir(root)
    if include_drafts and ddir.is_dir():
        out += [read_post_info(p, source_root=src, draft=True)
                for p in sorted(ddir.rglob("*.md"))]
    out.sort(key=lambda info: info.date or datetime.min, reverse=True)
    return out


def _post_files(root: Path, include_drafts: bool) -> list[tuple[str, Path]]:
    """(source 相对路径, 绝对路径) 列表，校验与迁移共用同一扫描口径。"""
    src = Path(root) / SOURCE_DIR
    out: list[tuple[str, Path]] = []
    pdir = posts_dir(root)
    if pdir.is_dir():
        out += [(p.relative_to(src).as_posix(), p) for p in sorted(pdir.rglob("*.md"))]
    ddir = drafts_dir(root)
    if include_drafts and ddir.is_dir():
        out += [(p.relative_to(src).as_posix(), p) for p in sorted(ddir.rglob("*.md"))]
    return out


# ── B1 新建文章脚手架 ──

def sanitize_post_filename(title: str) -> str:
    """标题 → 文件名：去文件系统非法字符，保留中文；空则回退 untitled。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().strip(".")
    return f"{name or 'untitled'}.md"


def load_scaffold_template(root: Path) -> str:
    """读 ``scaffolds/post.md``；缺失/坏编码回退内置模板。"""
    try:
        return (Path(root) / SCAFFOLD_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DEFAULT_SCAFFOLD


def _yaml_quote(s: str) -> str:
    """值含 YAML 结构字符时加双引号（含转义），否则原样。"""
    if re.search(r"""[:#{}\[\],&*?|>%"']|^\s|\s$|^-""", s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _flow(items) -> str:
    """极简 YAML flow 序列化（categories/tags 值）；嵌套 list 递归。"""
    if isinstance(items, (list, tuple)):
        return "[" + ", ".join(_flow(i) for i in items) + "]"
    return _yaml_quote(str(items))


def render_scaffold(template: str, *, title: str, date: str,
                    categories: tuple = (), tags: tuple = (),
                    description: Optional[str] = None) -> str:
    """按 Hexo scaffold 约定渲染：替换 ``{{ title }}`` / ``{{ date }}`` 占位，
    并保证 title/date/categories/tags 齐备——模板缺键补行，键已有值则以
    传入值为准。模板自定义的其他键原样保留。description 仅在传入时写入
    （SEO 摘要，覆盖主题从正文截断产生的行号噪音）。"""
    text = template.replace("{{ title }}", _yaml_quote(title))
    text = text.replace("{{ date }}", date).replace("{{date}}", date)
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        fm = ["---", f"title: {_yaml_quote(title)}", f"date: {date}"]
        if categories:
            fm.append(f"categories: {_flow(categories)}")
        if tags:
            fm.append(f"tags: {_flow(tags)}")
        return "\n".join(fm + ["---", ""] + lines)
    closes = [i for i in range(1, len(lines)) if lines[i].strip() == "---"]
    if closes:
        end = closes[0]
    else:  # 模板 front-matter 未闭合：补闭合行，键插在其前
        lines.append("---")
        end = len(lines) - 1
    keys: dict[str, int] = {}
    for i in range(1, end):
        m = re.match(r"^([A-Za-z_-]+)\s*:", lines[i])
        if m and m.group(1) not in keys:
            keys[m.group(1)] = i
    # 自底向上 upsert（插入只发生在 end，行号不漂移）
    for key, val in (("tags", _flow(tags) if tags else None),
                     ("categories", _flow(categories) if categories else None),
                     ("description", _yaml_quote(description) if description else None),
                     ("date", date),
                     ("title", _yaml_quote(title))):
        if key in keys:
            if val is not None:
                lines[keys[key]] = f"{key}: {val}"
        elif val is not None:
            lines.insert(end, f"{key}: {val}")
    return "\n".join(lines)


def scaffold_post(root: Path, *, title: str, slug: str = "",
                  date: Optional[datetime] = None,
                  categories: tuple = (), tags: tuple = (),
                  description: Optional[str] = None,
                  dest_dir: Optional[Path] = None) -> Path:
    """生成新文章（B1），返回文件路径；同名文件已存在抛
    :class:`FileExistsError`——绝不静默覆盖。

    hexo 形态写 ``root/source/_posts/``；KB 形态传 ``dest_dir`` 直写博客
    目录（模板同样回退内置——笔记库没有 scaffolds/）。
    """
    d = date or datetime.now()
    target = (dest_dir or posts_dir(root)) / \
        sanitize_post_filename(slug.strip() or title)
    if target.exists():
        raise FileExistsError(f"文章已存在: {target.name}")
    body = render_scaffold(
        load_scaffold_template(root),
        title=title, date=d.strftime("%Y-%m-%d %H:%M:%S"),
        categories=tuple(categories), tags=tuple(tags),
        description=description)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


# ── B2 校验 ──

@dataclass(frozen=True)
class Issue:
    post: str    # source 相对路径
    line: int
    dest: str
    kind: str    # broken | migratable | kbfurl
    message: str


@dataclass(frozen=True)
class BlogReport:
    scanned: int                    # 扫描的文章数
    issues: tuple[Issue, ...]
    unreferenced: tuple[str, ...]   # assets 下无文章引用的媒体名
    assets_count: int


def validate_blog(root: Path, *, include_drafts: bool = True) -> BlogReport:
    """全库链接校验（B2）。逐篇分类每个链接目标：

    - ``broken``：``assets/<name>`` 目标文件不存在，或带后缀的相对路径解析不到文件
    - ``migratable``：文章旁的散落媒体（存在、在 source 内、assets 外）
    - ``kbfurl``：知识库图床 URL（静态托管下读者不可达，博客形态必须入库）
    - 外链、锚点、站点路径（无后缀的相对链接，如 Hexo permalink）不判定
    """
    src_root = Path(root) / SOURCE_DIR
    assets = assets_dir(root)
    issues: list[Issue] = []
    referenced: set[str] = set()
    files = _post_files(root, include_drafts)
    for rel, p in files:
        text = _safe_read(p)
        for start, _end, dest, _img in iter_link_destinations(text):
            if dest.startswith("#"):
                continue
            line = text[:start].count("\n") + 1
            # 知识库图床 URL 必须先于外链判断（它带 scheme，会被 is_external_url 吞掉）
            if parse(dest) is not None:
                issues.append(Issue(rel, line, dest, "kbfurl", "本地图床 URL，静态托管不可达"))
                continue
            if is_external_url(dest):
                continue
            name = parse_hexo_dest(dest)
            if name is not None:
                referenced.add(name)
                if not (assets / name).is_file():
                    issues.append(Issue(rel, line, dest, "broken", f"assets/{name} 不存在"))
                continue
            target = resolve_image_path(p, dest)
            if target is not None and target.is_file():
                if target.suffix.lower() in _NON_MEDIA_EXTS:
                    continue  # 文章互链等文本目标
                try:
                    target.relative_to(src_root)
                except ValueError:
                    continue  # source 外的文件不归博客管
                try:
                    target.relative_to(assets)
                    continue  # 已在 assets（如 ../assets/ 形式）
                except ValueError:
                    pass
                issues.append(Issue(rel, line, dest, "migratable", "散落媒体，可迁入 source/assets"))
            elif Path(dest).suffix:
                issues.append(Issue(rel, line, dest, "broken", "链接目标不存在"))
    asset_files = sorted(f.name for f in assets.iterdir() if f.is_file()) \
        if assets.is_dir() else []
    unreferenced = tuple(n for n in asset_files if n not in referenced)
    return BlogReport(len(files), tuple(issues), unreferenced, len(asset_files))


# ── B2 散落媒体迁移 ──

@dataclass(frozen=True)
class MovePlan:
    src: Path              # 散落媒体原位置（存在文件）
    dst: Path              # source/assets/ 下的目标位置
    new_dest: str          # 改写后的链接形式 assets/<name>
    refs: tuple[str, ...]  # 引用它的文章（source 相对路径）


def plan_blog_migration(root: Path, *, include_drafts: bool = True,
                        ) -> tuple[list[MovePlan], dict[str, dict[str, str]]]:
    """B2 迁移计划：文章引用的散落媒体 → 搬入 source/assets/ 并改写链接。

    返回 ``(moves, dest_maps)``：moves 供确认 UI 展示；``dest_maps[post_rel]``
    是该篇内 ``旧 dest → 新 dest`` 映射。同一媒体被多篇引用只移动一次，
    所有引用处一起改。命名沿用 ``image-`` + mtime 时间戳规则（重名自动顺延）。
    本函数不动文件不改文章。
    """
    src_root = Path(root) / SOURCE_DIR
    assets = assets_dir(root)
    moves: dict[Path, MovePlan] = {}
    dest_maps: dict[str, dict[str, str]] = {}
    for rel, p in _post_files(root, include_drafts):
        for _s, _e, dest, _img in iter_link_destinations(_safe_read(p)):
            if dest.startswith("#") or is_external_url(dest):
                continue
            if parse(dest) is not None or parse_hexo_dest(dest) is not None:
                continue
            target = resolve_image_path(p, dest)
            if target is None or not target.is_file():
                continue
            if target.suffix.lower() in _NON_MEDIA_EXTS:
                continue
            try:
                target.relative_to(src_root)
            except ValueError:
                continue
            try:
                target.relative_to(assets)
                continue
            except ValueError:
                pass
            plan = moves.get(target)
            if plan is None:
                prefix = _media_prefix(target)
                name = make_image_filename_from_mtime(assets, target, prefix=prefix)
                plan = MovePlan(src=target, dst=assets / name,
                                new_dest=f"{ASSETS_DIR}/{name}", refs=())
                moves[target] = plan
            dest_maps.setdefault(rel, {})[dest] = plan.new_dest
    # refs 聚合（frozen dataclass 重建带 refs 的版本）
    by_new = {m.new_dest: m for m in moves.values()}
    for rel, dm in dest_maps.items():
        for new in sorted(set(dm.values())):
            m = by_new[new]
            moves[m.src] = MovePlan(m.src, m.dst, m.new_dest, m.refs + (rel,))
    return list(moves.values()), dest_maps


def apply_blog_migration(root: Path, moves: list[MovePlan],
                         dest_maps: dict[str, dict[str, str]]) -> tuple[int, int]:
    """执行迁移计划：移动文件 + 逐篇改写链接。返回 ``(移动数, 改写篇数)``。

    dst 撞名（计划生成后 assets 又被写入）时按命名规则顺延且不覆盖旧文件；
    此时以实际落点名修正改写映射。
    """
    assets = assets_dir(root)
    assets.mkdir(parents=True, exist_ok=True)
    actual: dict[str, str] = {}  # 计划 new_dest → 实际 new_dest
    moved = 0
    for m in moves:
        dst = m.dst
        if dst.exists():
            name = make_image_filename(assets, m.src.name, prefix=_media_prefix(m.src))
            dst = assets / name
        shutil.move(str(m.src), str(dst))
        actual[m.new_dest] = f"{ASSETS_DIR}/{dst.name}"
        moved += 1
    changed = 0
    for rel, dm in dest_maps.items():
        dm2 = {old: actual.get(new, new) for old, new in dm.items()}
        p = Path(root) / SOURCE_DIR / rel
        text = _safe_read(p)
        new_text = rewrite_post_dests(text, dm2)
        if new_text != text:
            p.write_text(new_text, encoding="utf-8")
            changed += 1
    return moved, changed


def rewrite_post_dests(text: str, dest_map: dict[str, str]) -> str:
    """按映射整篇改写链接 dest；iter_link_destinations 重建，其余逐字保留。"""
    out: list[str] = []
    pos = 0
    for start, end, dest, _img in iter_link_destinations(text):
        new = dest_map.get(dest)
        if new is None:
            continue
        out.append(text[pos:start])
        out.append(text[start:end].replace(dest, new, 1))
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _media_prefix(p: Path) -> str:
    """图片扩展名 → ``image-`` 前缀；附件（pdf/zip 等）无前缀，与知识库命名一致。"""
    return "image-" if p.suffix.lower().lstrip(".") in DEFAULT_EXTENSIONS else ""


def _safe_read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# ── B2 导入图片（插图闭环）──

def import_images(files: list[Path], target_dir: Path) -> list[tuple[Path, str]]:
    """拷入 ``source/assets/``（时间戳命名，与图床语义一致），返回 ``[(源文件, 新名)]``。

    编辑仍由 Typora 专职：调用方把返回值经 :func:`links_markdown` 拼成
    Markdown 链接放剪贴板，由用户在编辑器光标处粘贴。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    pairs: list[tuple[Path, str]] = []
    for f in files:
        name = make_image_filename(target_dir, f.name, prefix=_media_prefix(f))
        shutil.copy2(f, target_dir / name)
        pairs.append((f, name))
    return pairs


def links_markdown(pairs: list[tuple[Path, str]]) -> str:
    """import_images 结果 → 空格分隔的 Markdown 图链（alt 取原文件名词干）。"""
    parts = []
    for src, name in pairs:
        alt = re.sub(r"[\[\]()]", "", src.stem).strip() or "image"
        parts.append(f"![{alt}]({ASSETS_DIR}/{name})")
    return " ".join(parts)


# ── 笔记库抽取（笔记 → 博客文章）──

@dataclass(frozen=True)
class ExtractPlan:
    """plan_note_extract 的产物：写盘集中在 apply_note_extract。"""

    posts: tuple[tuple[Path, str], ...]   # (目标 _posts 文件, 改写后全文)
    media: tuple[tuple[Path, Path], ...]  # (源媒体, 目标 source/assets/<名>)
    skipped: tuple[str, ...]              # 跳过原因（重名文章 / 找不到的媒体）


def plan_note_extract(
    notes: list[Path],
    blog_root: Path,
    *,
    media_root: Optional[Path] = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    images_subdir: str = "images",
    assets_subdir: str = "assets",
) -> ExtractPlan:
    """把笔记库文章抽成博客文章（落到 ``source/_posts/``）。

    - 知识库图床 URL → ``assets/<名>``，媒体从 ``<media_root>/<子目录>`` 拷入
      ``source/assets/``（时间戳名天然唯一，沿用原名）；媒体缺失则链接原样保留
      并记入 skipped（校验页会兜底报告）。
    - 笔记旁的相对媒体链接同样收编（重名按 ``_2`` 后缀顺延，同一计划内共享）。
    - 目标 ``_posts/`` 已有同名文章 → 整篇跳过，绝不覆盖。
    """
    assets = assets_dir(blog_root)
    posts_root = posts_dir(blog_root)
    posts: list[tuple[Path, str]] = []
    media: list[tuple[Path, Path]] = []
    skipped: list[str] = []
    taken: set[str] = set()
    planned: set[Path] = set()  # 本批次已占用的目标路径（磁盘检查覆盖不了同批重名）
    for note in notes:
        dest = posts_root / note.name
        if dest.exists() or dest in planned:
            skipped.append(f"{note.name}：_posts 已有同名文章，跳过")
            continue
        text = _safe_read(note)
        dest_map: dict[str, str] = {}
        for _s, _e, d, _img in iter_link_destinations(text):
            if d.startswith("#"):
                continue
            ref = parse(d, host=host, port=port)
            if ref is not None:
                # 知识库图床 URL（先于外链判断，它带 scheme）
                name = _free_asset_name(assets, ref.name, taken)
                src = None
                if media_root is not None:
                    sub = images_subdir if ref.category == "images" else assets_subdir
                    cand = Path(media_root) / sub / ref.name
                    if cand.is_file():
                        src = cand
                if src is None:
                    skipped.append(f"{note.name}：{d} 媒体缺失，链接未改写")
                    continue
                media.append((src, assets / name))
                dest_map[d] = f"{ASSETS_DIR}/{name}"
                continue
            if is_external_url(d) or parse_hexo_dest(d) is not None:
                continue
            target = resolve_image_path(note, d)
            if target is None or not target.is_file() \
                    or target.suffix.lower() in _NON_MEDIA_EXTS:
                continue
            name = _free_asset_name(assets, target.name, taken)
            media.append((target, assets / name))
            dest_map[d] = f"{ASSETS_DIR}/{name}"
        planned.add(dest)
        posts.append((dest, rewrite_post_dests(text, dest_map)))
    return ExtractPlan(tuple(posts), tuple(media), tuple(skipped))


def _free_asset_name(assets: Path, name: str, taken: set[str]) -> str:
    """assets 下未被占用（磁盘或本计划已分配）的名字；重名 ``_2`` 顺延。"""
    if name not in taken and not (assets / name).exists():
        taken.add(name)
        return name
    stem, ext = Path(name).stem, Path(name).suffix
    i = 2
    while True:
        cand = f"{stem}_{i}{ext}"
        if cand not in taken and not (assets / cand).exists():
            taken.add(cand)
            return cand
        i += 1


def apply_note_extract(plan: ExtractPlan) -> tuple[int, int]:
    """执行抽取：拷媒体 + 写文章。返回 ``(文章数, 媒体数)``。"""
    for src, dst in plan.media:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for path, text in plan.posts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return len(plan.posts), len(plan.media)
