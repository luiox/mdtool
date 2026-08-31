"""统一媒体链接解析与改写（docs/知识库规范.md §2）。

分层模型：
- 权威标识：``/images/<name>``、``/assets/<name>``（URL 的 path 部分）
- 存储形式：完整 URL（host/port 默认 ``127.0.0.1:8765``）——笔记正文里的样子
- 渲染形式：按目标环境改写（desktop / zip / phone / lan）

任何消费方不得直接使用存储 URL，必须经本模块 ``parse`` → ``rewrite``。
非本知识库媒体（外链、本地路径、未知 host）一律原样透传，绝不改动。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# 媒体 URL：http(s)://host[:port]/images|assets/<时间戳文件名>
_URL_RE = re.compile(
    r"^https?://(?P<host>[^/:]+)(?::(?P<port>\d+))?/(?P<cat>images|assets)/(?P<name>[^/\s]+)$"
)
# markdown 链接目标 [..](dest) 与 ![..](dest)
_DEST_RE = re.compile(r"(?P<open>!?\[[^\]]*\])\((?P<dest>[^)\n]+)\)")
# 本地相对路径 / 外部 URL 的判别：dest 含 scheme 视为外部
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
# 博客形态相对链接：assets/<name>（容忍 ./ 与 ../ 前缀；Hexo _posts 平铺）
_HEXO_RE = re.compile(r"^(?:\.{1,2}/)*assets/(?P<name>[^/\s]+)$")


@dataclass(frozen=True)
class MediaRef:
    """权威标识：媒体在知识库根下的逻辑位置。"""

    category: str  # "images" | "assets"
    name: str      # 时间戳文件名，如 image-20201215174726729.png


def parse(
    url: str, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> Optional[MediaRef]:
    """识别本知识库媒体 URL → MediaRef；非本知识库 URL 返回 None。"""
    if not url:
        return None
    m = _URL_RE.match(url.strip())
    if not m:
        return None
    if m.group("host") != host:
        return None
    p = int(m.group("port")) if m.group("port") else DEFAULT_PORT
    if p != port:
        return None
    name = m.group("name").split("?")[0].split("#")[0]
    return MediaRef(category=m.group("cat"), name=name)


def rewrite(
    url: str,
    *,
    target: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    base: str = "",
) -> str:
    """按目标环境改写媒体 URL；未知 URL 原样返回。

    target:
      - ``"desktop"``: 原样（Typora / 本地渲染）
      - ``"zip"``:     zip 内相对路径 ``images/<name>`` 或 ``assets/<name>``
      - ``"hexo"``:    博客形态 ``assets/<name>``（图片附件统一平铺 source/assets/；
                       仅桌面管理侧使用，手机端不消费博客形态，LinkResolver.kt 无此目标）
      - ``"phone"`` / ``"lan"``: ``<base>/images/<name>``（base 如 ``http://127.0.0.1:8080``）
    """
    ref = parse(url, host=host, port=port)
    if ref is None:
        return url
    if target == "desktop":
        return url
    if target == "zip":
        return f"{ref.category}/{ref.name}"
    if target == "hexo":
        return f"assets/{ref.name}"
    if target in ("phone", "lan"):
        b = base.rstrip("/")
        if not b:
            raise ValueError("phone/lan 目标需要 base 参数")
        return f"{b}/{ref.category}/{ref.name}"
    raise ValueError(f"未知 target: {target}")


def iter_link_destinations(text: str) -> Iterator[tuple[int, int, str, bool]]:
    """遍历 markdown 中的 ``[..](dest)`` 与 ``![..](dest)``，跳过代码区。

    yield ``(start, end, dest, is_image)``；dest 已去除尾随 title 等。
    """
    from mdtool.core.utils import _code_regions, _in_code  # 同包共享，避免循环导入

    regions = _code_regions(text)
    for m in _DEST_RE.finditer(text):
        if _in_code(m.start(), regions):
            continue
        stripped = m.group("dest").strip()
        dest = stripped.split()[0] if stripped else ""
        if not dest:
            continue
        yield m.start(), m.end(), dest, m.group("open").startswith("!")


def is_external_url(dest: str) -> bool:
    """dest 是否外部 URL（带 scheme）或绝对路径；False = 可当相对路径解析。"""
    return bool(_SCHEME_RE.match(dest)) or dest.startswith("/")


def parse_hexo_dest(dest: str) -> Optional[str]:
    """识别博客形态相对链接 ``assets/<name>`` → name；其余返回 None。

    博客源豁免（docs/博客文章管理规划.md §2）：Hexo 文章统一以
    ``assets/<name>`` 引用 ``source/assets/`` 下的媒体。仅桌面管理侧使用，
    不回渗到知识库默认模型（手机端 LinkResolver.kt 无此语义）。
    """
    if not dest:
        return None
    m = _HEXO_RE.match(dest.strip())
    if not m:
        return None
    return m.group("name").split("?")[0].split("#")[0]


def rewrite_markdown(
    text: str,
    *,
    target: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    base: str = "",
) -> str:
    """改写整篇 markdown 中所有本知识库媒体链接；其余内容逐字保留。"""
    out: list[str] = []
    pos = 0
    for start, end, dest, _img in iter_link_destinations(text):
        new = rewrite(dest, target=target, host=host, port=port, base=base)
        if new == dest:
            continue
        out.append(text[pos:start])
        out.append(text[start:end].replace(dest, new, 1))
        pos = end
    out.append(text[pos:])
    return "".join(out)
