"""markdown → HTML 渲染层（sitegen）。

对齐 Hexo 时代地面真相的三条语义（见 public/ 产物反查，2026-09-06）：

1. 正文里 ``assets/<name>`` 相对链接 → 站点根绝对 ``/assets/<name>``，路径
   百分号编码（CJK 文件名在页面上可达的前提）。
2. 标题带 anchor id（CJK 保留），TOC 从同一份 token 树提取，页内锚点自洽。
3. 外链（非本站）加 ``target="_blank" rel="noopener"``（hexo external_link
   开启时的行为）。

代码块走 Pygments 构建期高亮（nowrap 内嵌 + 一份独立 CSS 主题），外层包
``figure.codeblock`` 带语言标签与复制按钮；客户端 JS 只做剪贴板——对应
jacman 时代的 ClipboardJS 修好后的行为，且甩掉 jQuery 依赖。

其余：CommonMark 基线 + table/strikethrough（hexo marked 的 GFM 习惯）、
raw HTML 透传（html=True，hexo 同）。纯函数，无 IO。
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from pygments import highlight as _pyg_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

# TOC 收集的标题层级（hexo/jacman 的 TOC 深度习惯：h2-h4）
_TOC_LEVELS = (2, 3, 4)
# 复制按钮文案缺省值（site.json 可覆盖；站点 JS 的反馈文案与首态一致，
# 主题换文案时须同步改 site.js，见 docs/博客主题定制.md 的渲染契约一节）
COPY_LABEL_DEFAULT = "复制"

_PYGM_FORMATTER = HtmlFormatter(nowrap=True)


def highlight_code(code: str, lang: str) -> str:
    """代码 → 高亮 HTML 片段（nowrap，无 <pre> 外壳）；未知语言回退转义。"""
    if lang:
        try:
            lexer = get_lexer_by_name(lang, stripnl=False, ensurenl=False)
        except ClassNotFound:
            return _html.escape(code)
        return _pyg_highlight(code, lexer, _PYGM_FORMATTER)
    return _html.escape(code)


def pygments_css(style: str = "friendly") -> str:
    """Pygments 高亮主题 CSS（构建期落成 css/pygments.css）。"""
    return HtmlFormatter(style=style).get_style_defs(".codeblock")


@dataclass(frozen=True)
class TocItem:
    level: int
    text: str
    anchor: str
    num: str = ""       # 层级编号（1. / 1.1 / 1.1.1，hexo toc list_number 同款）


@dataclass(frozen=True)
class RenderResult:
    html: str
    toc: tuple[TocItem, ...]
    excerpt: str


def _normalize_local_dest(dest: str) -> str | None:
    """``assets/…`` 相对目标 → 根绝对形式；其余返回 None 不动。

    只认约定形态 ``assets/<name>``（博客源纪律，校验页保证），不做文件系统
    探测——渲染保持纯函数，坏链接由校验层报告而非渲染层猜测。这里不做
    百分号编码：markdown-it 的 normalizeLink 会在渲染期统一编码（重复
    编码会把 % 变 %25，CJK 文件名即 404）。
    """
    if dest.startswith("assets/") and not dest.startswith("assets//"):
        return "/assets/" + dest[len("assets/"):]
    return None


def _is_external(dest: str, site_host: str) -> bool:
    if not dest.startswith(("http://", "https://")):
        return False
    if not site_host:
        return True
    # 站内绝对 URL（含端口歧义不考虑——Pages 托管固定 443）不算外链
    rest = dest.split("://", 1)[1]
    return not (rest == site_host or rest.startswith(site_host + "/"))


def _render_fence(self, tokens, idx: int, options, env, *,
                  copy_label: str = COPY_LABEL_DEFAULT) -> str:
    """fence 渲染：figure 外壳 + 语言标签 + 复制按钮 + Pygments 高亮。

    签名按 markdown-it-py 约定：首参是 renderer 实例（add_render_rule
    以显式 self 调用），其余为 (tokens, idx, options, env)；copy_label
    由 build_md 的闭包捕获（站点配置可换文案）。
    """
    token = tokens[idx]
    info = (token.info or "").strip()
    lang = info.split()[0] if info else ""
    lang_label = _html.escape(lang) if lang else "text"
    body = highlight_code(token.content, lang)
    return (
        '<figure class="codeblock">'
        f'<figcaption class="codeblock-bar"><span class="codeblock-lang">{lang_label}</span>'
        f'<button type="button" class="code-copy-btn">{_html.escape(copy_label)}</button></figcaption>'
        f'<pre><code class="language-{lang_label.lower()}">{body}</code></pre>'
        "</figure>\n"
    )


def build_md(*, copy_label: str = COPY_LABEL_DEFAULT) -> MarkdownIt:
    """构造渲染器实例（无状态可复用；anchors 的 slug 保留 CJK 并页内去重）。"""
    md = MarkdownIt("commonmark", {"html": True, "linkify": True})
    md.enable(["table", "strikethrough"])
    # slugify 默认实现保留 CJK 并页内去重（GitHub 风格），正合中文标题锚点
    anchors_plugin(md, min_level=min(_TOC_LEVELS), max_level=max(_TOC_LEVELS))

    def fence(renderer, tokens, idx, options, env):
        # 闭包捕获 copy_label——add_render_rule 以 __get__ 绑定首参，
        # partial 对象没有该方法，必须包一层普通函数
        return _render_fence(renderer, tokens, idx, options, env,
                             copy_label=copy_label)

    md.add_render_rule("fence", fence)
    return md


def _inline_text(inline_token) -> str:
    """inline token 的纯文本（text + code_inline；图片 alt 不入 TOC/摘要）。"""
    parts = []
    for child in inline_token.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
    return "".join(parts)


def _walk_inline(children, site_host: str) -> list[str]:
    """遍历 inline children：改写 img/a 目标（就地落 attrs），收集纯文本。

    heading 与段落的 inline 都走这里——每个 inline token 恰好经过一次，
    链接改写不会重入。返回文本供 TOC 标题与摘要复用。
    """
    texts: list[str] = []
    for child in children or []:
        if child.type == "image":
            src = child.attrGet("src") or ""
            new = _normalize_local_dest(src)
            if new is not None:
                child.attrSet("src", new)
        elif child.type in ("link", "link_open"):
            # 链接属性挂在 link_open 上（markdown-it 无 "link" 类型，两种都收
            # 以防记错）；图片恰好自成 image 类型
            href = child.attrGet("href") or ""
            new = _normalize_local_dest(href)
            if new is not None:
                child.attrSet("href", new)
            elif _is_external(href, site_host):
                child.attrSet("target", "_blank")
                child.attrSet("rel", "noopener")
        elif child.type in ("text", "code_inline"):
            texts.append(child.content)
    return texts


def render_markdown(src: str, *, site_host: str = "",
                    copy_label: str = COPY_LABEL_DEFAULT) -> RenderResult:
    """单篇正文 → (HTML, TOC, 纯文本摘要)。

    摘要取正文开头约 200 字（front-matter description 缺失时首页列表用）；
    parse 一次，token 树同时供链接改写、TOC 提取、摘要收集，渲染复用同一棵树。
    """
    md = build_md(copy_label=copy_label)
    tokens = md.parse(src)
    toc: list[TocItem] = []
    excerpt_parts: list[str] = []
    excerpt_len = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "heading_open":
            level = int(tok.tag[1]) if tok.tag[1:].isdigit() else 0
            inline = tokens[i + 1] if i + 1 < len(tokens) \
                and tokens[i + 1].type == "inline" else None
            if inline is not None:
                # 改写与文本收集对每个 inline token 只做一次（heading 内的
                # 图片/链接同样生效），TOC 取 anchor（anchors 插件写入）+纯文本
                _walk_inline(inline.children, site_host)
                text = _inline_text(inline)
                anchor = tok.attrs.get("id") or ""
                if level in _TOC_LEVELS and anchor:
                    toc.append(TocItem(level=level, text=text, anchor=anchor))
            i += 2
            continue
        if tok.type == "inline":
            texts = _walk_inline(tok.children, site_host)
            if excerpt_len < 160:
                chunk = " ".join(t for t in texts if t.strip())
                if chunk:
                    excerpt_parts.append(chunk)
                    excerpt_len += len(chunk)
        i += 1
    html_out = md.renderer.render(tokens, md.options, {})
    excerpt = " ".join(excerpt_parts).strip()
    if len(excerpt) > 200:
        excerpt = excerpt[:200].rstrip() + "…"
    return RenderResult(html_out, tuple(toc), excerpt)
