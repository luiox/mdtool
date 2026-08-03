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
