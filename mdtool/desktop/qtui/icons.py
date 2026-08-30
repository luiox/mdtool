"""Vector SVG icons, rendered crisp at any DPI.

Replaces the hand-coded 16×16 PPM pixel grids in ``tabs/file_browser.py``,
which blur badly on high-DPI Windows scaling. SVG → QPixmap → QIcon stays
sharp at 100%/150%/200% because Qt rasterizes on demand at the device pixel
ratio.

Rendering requires a live ``QApplication`` (QPixmap cannot exist without the
GUI plugin initialized), so the helpers below are **functions** — call them
from a tab's UI build (which runs after ``QApplication`` exists), never at
module import time.
"""

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Minimal, clean folder and document glyphs (Material-ish). Edit the SVG
# strings to restyle; nothing else in the app hard-codes icon size or color.
_FOLDER_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#e6c068" stroke="#b08040" stroke-width="1">
  <path d="M3 6.5C3 5.7 3.7 5 4.5 5h4.2c.5 0 1 .2 1.3.6L11.5 7h8C20.3 7 21 7.7 21 8.5v9c0 .8-.7 1.5-1.5 1.5h-15C3.7 19 3 18.3 3 17.5z"/>
</svg>
"""

_FILE_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#cfe0f0" stroke="#5b7ca8" stroke-width="1">
  <path d="M6 3.5C6 3.2 6.2 3 6.5 3h7L18 7.5v13c0 .3-.2.5-.5.5h-11C6.2 21 6 20.8 6 20.5z"/>
  <path d="M13.5 3v4.5c0 .3.2.5.5.5H18" fill="none" stroke="#5b7ca8"/>
</svg>
"""

# Tray icon: blue circle with white M (matches the old PIL-drawn tray glyph).
_TRAY_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <circle cx="16" cy="16" r="14" fill="#4682dc"/>
  <text x="16" y="22" font-family="Segoe UI, Arial, sans-serif" font-size="18"
        font-weight="bold" fill="white" text-anchor="middle">M</text>
</svg>
"""

# 导航图标：线性风格（Material-ish outline），描边色固定为中性灰——
# 选中态的高亮由 QSS 的背景/文字色承担，图标不参与换色。
_STROKE = 'fill="none" stroke="#5f6b7a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'


def _line_icon(paths: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" {_STROKE}>{paths}</svg>'


_NAV_SVGS: dict[str, str] = {
    # 笔记库：书
    "notes": _line_icon(
        '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/>'
        '<path d="M4 20.5V5.5M20 18v3H6.5"/>'
    ),
    # 文件浏览器：打开的文件夹
    "files": _line_icon(
        '<path d="M3 7V5.5C3 4.7 3.7 4 4.5 4h4l2 2.5h9c.8 0 1.5.7 1.5 1.5V9"/>'
        '<path d="M3 7h18l-2 11.5c-.1.9-.8 1.5-1.6 1.5H6.6c-.8 0-1.5-.6-1.6-1.5z"/>'
    ),
    # 媒体服务器：服务器机架
    "server": _line_icon(
        '<rect x="3.5" y="4" width="17" height="7" rx="1.5"/>'
        '<rect x="3.5" y="13" width="17" height="7" rx="1.5"/>'
        '<path d="M7 7.5h.01M7 16.5h.01"/>'
    ),
    # 图片校验：盾+对勾
    "check": _line_icon(
        '<path d="M12 3l7.5 2.8v5.4c0 4.6-3.1 8-7.5 9.8-4.4-1.8-7.5-5.2-7.5-9.8V5.8z"/>'
        '<path d="M8.8 12l2.2 2.2 4.2-4.4"/>'
    ),
    # 图片迁移：图片+箭头
    "migrate": _line_icon(
        '<rect x="3.5" y="5" width="13" height="11" rx="1.5"/>'
        '<path d="M3.5 13.5l3.5-3 4 3.5 2.5-2 3 2.5"/>'
        '<path d="M14 19.5h6.5M18 16.5l3 3-3 3" transform="translate(0 -2)"/>'
    ),
    # 空格修复：文字下划线（空格→_）
    "space": _line_icon(
        '<path d="M5 15h14"/>'
        '<path d="M8 19h8"/>'
    ),
    # 日志：终端
    "log": _line_icon(
        '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/>'
        '<path d="M7.5 9.5l3 2.5-3 2.5M12.5 15h4"/>'
    ),
    # 搜索：放大镜
    "search": _line_icon(
        '<circle cx="10.5" cy="10.5" r="6"/>'
        '<path d="M15 15l5.5 5.5"/>'
    ),
}

_DEFAULT_PX = 16

# Per-process cache so repeated calls don't re-rasterize.
_cache: dict[str, object] = {}


def svg_to_pixmap(svg_str: str, px: int = _DEFAULT_PX) -> QPixmap:
    """Render an SVG string to a device-pixel-ratio-aware QPixmap."""
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return pm


def svg_icon(svg_str: str, px: int = _DEFAULT_PX) -> QIcon:
    return QIcon(svg_to_pixmap(svg_str, px))


def folder_icon() -> QIcon:
    key = "folder"
    if key not in _cache:
        _cache[key] = svg_icon(_FOLDER_SVG)
    return _cache[key]


def file_icon() -> QIcon:
    key = "file"
    if key not in _cache:
        _cache[key] = svg_icon(_FILE_SVG)
    return _cache[key]


def tray_icon() -> QIcon:
    key = "tray"
    if key not in _cache:
        _cache[key] = svg_icon(_TRAY_SVG, 32)
    return _cache[key]


def nav_icon(name: str, px: int = 18) -> QIcon:
    """Sidebar navigation glyph by name; see ``_NAV_SVGS``."""
    key = f"nav:{name}:{px}"
    if key not in _cache:
        if name not in _NAV_SVGS:
            raise KeyError(f"未知导航图标: {name!r}，可用: {sorted(_NAV_SVGS)}")
        _cache[key] = svg_icon(_NAV_SVGS[name], px)
    return _cache[key]
