"""整站生成：文章清单 → 静态目录树（article/ index archives tags categories atom sitemap css js assets）。

URL 语义与 Hexo 产物对齐（阶段 1 验收线）：

- 文章 ``article/<id>.html``（id 来自 manifest，URL 永久稳定）
- 首页分页 ``/``、``/page/<k>/``；归档 ``/archives/``；标签 ``/tags/``、
  ``/tags/<name>/``；分类页 ``/categories/<path>/``（多级以 / 连接）
- ``atom.xml``（feed_limit 篇）、``sitemap.xml``、``css/``、``js/``、``assets/``

模板/静态资源是包内数据目录；jinja autoescape 常开，正文 HTML 以 ``| safe``
显式放行（仅来自 render_markdown）。写盘集中在 build_site，其余纯函数。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader

from mdtool.core.blog import parse_front_matter
from mdtool.core.sitegen import resource_dir
from mdtool.core.sitegen.render import TocItem, pygments_css, render_markdown

# 静态资源落位：包内 static/<name> → 输出 <dir>/<name>
_STATIC_FILES = {"style.css": "css/style.css", "site.js": "js/site.js"}
_FEED_LIMIT_DEFAULT = 20


@dataclass(frozen=True)
class SiteSpec:
    """站点元信息（Hexo _config.yml 的 sitegen 对应物）。url 无尾斜杠。"""

    title: str
    url: str
    author: str
    language: str = "zh-CN"
    per_page: int = 10
    feed_limit: int = _FEED_LIMIT_DEFAULT


@dataclass(frozen=True)
class PostInput:
    id: int
    path: Path
    note: str = ""       # manifest 键（回写发布日期等用；生成期仅透传）


@dataclass(frozen=True)
class RenderedPost:
    id: int
    note: str
    url_path: str
    title: str
    date: Optional[datetime]
    categories: tuple
    tags: tuple
    description: str
    mathjax: bool
    toc: tuple[TocItem, ...]
    html: str
    excerpt: str

    @property
    def date_display(self) -> str:
        return self.date.strftime("%Y-%m-%d") if self.date else ""

    @property
    def date_iso(self) -> str:
        # og:/atom 用的 ISO 形态；naive 时间按 hexo 习惯标 Z（站点时区统一）
        return self.date.strftime("%Y-%m-%dT%H:%M:%S.000Z") if self.date else ""

    @property
    def categories_display(self) -> str:
        def flat(x):
            if isinstance(x, (list, tuple)):
                return "/".join(flat(i) for i in x)
            return str(x)
        return ", ".join(flat(c) for c in self.categories)

    @property
    def category_path(self) -> str:
        """多级分类的 URL 路径段（hexo 语义：/categories/父/子/）。"""
        def flat(x):
            if isinstance(x, (list, tuple)):
                return "/".join(flat(i) for i in x)
            return str(x)
        return flat(self.categories[0]) if self.categories else ""


def _safe_read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _tup(v) -> tuple:
    if isinstance(v, (list, tuple)):
        return tuple(v)
    return (v,) if v else ()


def _parse_date(value) -> Optional[datetime]:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace(" ", "T"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is None \
        else dt.astimezone().replace(tzinfo=None)


def _split_meta(text: str) -> tuple[dict, str]:
    """严格栅栏解析优先；无 opening ``---`` 但首段是 key: value 块且以 ``---``
    收口时按 hexo 的宽容语义解析。

    hexo-admin 时代的存量文章存在缺 opening 栅栏的形态（hexo-front-matter
    容忍之），meta 行漏进正文的代价是文章页顶端渲染出裸 YAML 文本。
    """
    meta, body = parse_front_matter(text)
    if meta:
        return meta, body
    lines = text.split("\n")
    end = None
    for i, line in enumerate(lines[:20]):
        if line.strip() == "---":
            end = i
            break
    if end is None or end == 0:
        return {}, text
    block = lines[:end]
    if all((":" in line) or not line.strip() for line in block):
        meta2, _ = parse_front_matter("---\n" + "\n".join(block) + "\n---\n")
        if meta2:
            return meta2, "\n".join(lines[end + 1:])
    return {}, text


def _number_toc(toc: tuple[TocItem, ...]) -> tuple[TocItem, ...]:
    """TOC 层级编号（h2→X.、h3→X.Y、h4→X.Y.Z），对齐 hexo toc list_number 形态。"""
    counters = [0, 0, 0]
    out: list[TocItem] = []
    for item in toc:
        idx = min(max(item.level - 2, 0), 2)
        counters[idx] += 1
        for j in range(idx + 1, 3):
            counters[j] = 0
        num = ".".join(str(c) for c in counters[: idx + 1])
        out.append(TocItem(item.level, item.text, item.anchor, num))
    return tuple(out)


def render_post(inp: PostInput, *, site_host: str = "") -> RenderedPost:
    """单篇 → RenderedPost（front-matter 解析 + 渲染）。

    文件不可读（IO/非 UTF-8）抛 ValueError 交由 build_site 记入 skipped；
    可读但为空则渲染空文章（hexo 同语义）。
    """
    text = _safe_read(inp.path)
    if not text:
        raise ValueError(f"无法读取（IO 失败或非 UTF-8）: {inp.path.name}")
    meta, body = _split_meta(text)
    rr = render_markdown(body, site_host=site_host)
    date = _parse_date(meta.get("date"))
    if date is None:
        try:
            date = datetime.fromtimestamp(inp.path.stat().st_mtime)
        except OSError:
            date = None
    return RenderedPost(
        id=inp.id, note=inp.note, url_path=f"article/{inp.id}.html",
        title=str(meta["title"]) if meta.get("title") else inp.path.stem,
        date=date, categories=_tup(meta.get("categories")), tags=_tup(meta.get("tags")),
        description=str(meta.get("description") or ""),
        mathjax=str(meta.get("mathjax", "")).strip().lower() == "true",
        toc=_number_toc(rr.toc), html=rr.html, excerpt=rr.excerpt)


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(resource_dir() / "templates"),
        autoescape=True, trim_blocks=True, lstrip_blocks=True)


def _sorted_by_date(posts: list[RenderedPost]) -> list[RenderedPost]:
    """日期倒序（无日期垫底；同日期 id 降序，保证确定性）。"""
    return sorted(posts, key=lambda p: (p.date or datetime.min, p.id),
                  reverse=True)


def build_site(posts: list[PostInput], spec: SiteSpec, *,
               assets_src: Optional[Path] = None,
               out_dir: Path) -> "BuildReport":
    """整站生成。posts 全量渲染后写各页面 + 静态资源；单篇失败记入 skipped
    不中断整站（与 mdtool 读取兜底哲学一致）。输出目录不清理——重复合成
    覆盖同名文件，陈旧文件清理归调用方（对齐 hexo clean/generate 分离）。"""
    rendered: list[RenderedPost] = []
    skipped: list[str] = []
    for inp in posts:
        try:
            rendered.append(render_post(inp, site_host=spec.url.split("://", 1)[-1]))
        except Exception as e:  # noqa: BLE001 —— 单篇坏不拖垮整站
            skipped.append(f"{inp.path.name}: {e}")
    ordered = _sorted_by_date(rendered)

    env = _jinja_env()
    env.globals["abs_url"] = _abs_url_factory(spec)
    env.globals["coll_url"] = _coll_url

    out = Path(out_dir)
    written = 0

    def write(rel: str, text: str) -> None:
        nonlocal written
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        written += 1

    # 文章页（含相邻导航：按日期序的左右邻）
    for i, post in enumerate(ordered):
        newer = ordered[i - 1] if i > 0 else None
        older = ordered[i + 1] if i + 1 < len(ordered) else None
        ctx = dict(spec=spec, post=post, newer=newer, older=older, mathjax=post.mathjax)
        write(post.url_path, env.get_template("post.html").render(**ctx))

    # 首页分页
    per = max(1, spec.per_page)
    total_pages = max(1, (len(ordered) + per - 1) // per)
    for page_no in range(1, total_pages + 1):
        chunk = ordered[(page_no - 1) * per: page_no * per]
        write("index.html" if page_no == 1 else f"page/{page_no}/index.html",
              env.get_template("index.html").render(
                  spec=spec, posts=chunk, page_no=page_no,
                  total_pages=total_pages,
                  page_url=_page_url, mathjax=False))

    # 归档（按年分组，年倒序）
    by_year: dict[int, list[RenderedPost]] = {}
    for p in ordered:
        if p.date is not None:
            by_year.setdefault(p.date.year, []).append(p)
    years = sorted(by_year, reverse=True)
    write("archives/index.html",
          env.get_template("archive.html").render(
              spec=spec, years=[(y, by_year[y]) for y in years], mathjax=False))

    # 标签云 + 标签页；分类页
    tag_counts = _counts(p.tags for p in ordered)
    write("tags/index.html", env.get_template("tags.html").render(
        spec=spec, tag_counts=tag_counts, mathjax=False))
    for name, _n in tag_counts:
        write(f"tags/{quote(str(name))}/index.html",
              env.get_template("list.html").render(
                  spec=spec, heading=f"标签：{name}",
                  posts=[p for p in ordered if name in p.tags], mathjax=False))
    cat_names = {p.category_path for p in ordered if p.category_path}
    for cpath in sorted(cat_names):
        write(f"categories/{quote(cpath, safe='/')}/index.html",
              env.get_template("list.html").render(
                  spec=spec, heading=f"分类：{cpath}",
                  posts=[p for p in ordered if p.category_path == cpath],
                  mathjax=False))

    # feed / sitemap
    feed = ordered[:max(1, spec.feed_limit)]
    write("atom.xml", env.get_template("atom.xml").render(
        spec=spec, posts=feed,
        updated_iso=feed[0].date_iso if feed else _now_iso(), mathjax=False))
    site_paths = ["/", "/archives/"] + [f"/{p.url_path}" for p in ordered] \
        + [f"/page/{k}/" for k in range(2, total_pages + 1)] \
        + [f"/tags/{quote(str(n))}/" for n, _ in tag_counts] \
        + [f"/categories/{quote(c, safe='/')}/" for c in sorted(cat_names)]
    write("sitemap.xml", env.get_template("sitemap.xml").render(
        spec=spec, paths=site_paths,
        lastmod=_today(), mathjax=False))

    # 静态资源 + 高亮主题 + 媒体
    static_dir = resource_dir() / "static"
    for name, rel in _STATIC_FILES.items():
        write(rel, (static_dir / name).read_text(encoding="utf-8"))
    write("css/pygments.css", pygments_css() + "\n")
    if assets_src is not None and Path(assets_src).is_dir():
        shutil.copytree(assets_src, out / "assets", dirs_exist_ok=True)

    return BuildReport(len(ordered), written, tuple(skipped))


@dataclass(frozen=True)
class BuildReport:
    posts: int
    files: int
    skipped: tuple[str, ...]


def _counts(tag_iter) -> list[tuple[str, int]]:
    counter: dict[str, int] = {}
    for p in tag_iter:
        for t in p:
            counter[str(t)] = counter.get(str(t), 0) + 1
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def _abs_url_factory(spec: SiteSpec):
    base = spec.url.rstrip("/")
    return lambda path: base + path if path.startswith("/") else f"{base}/{path}"


def _coll_url(kind: str, name: str) -> str:
    """标签/分类页 URL（多级分类以 / 连接并逐段编码，hexo 同语义）。"""
    return f"/{kind}/{quote(str(name), safe='/')}/"


def _page_url(page_no: int) -> str:
    """首页分页页码 → URL（第 1 页就是根路径，hexo 同语义）。"""
    return "/" if page_no <= 1 else f"/page/{page_no}/"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")
