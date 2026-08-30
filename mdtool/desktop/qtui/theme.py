"""Theme system — token-based QSS generation.

设计约束：
- 所有视觉令牌（颜色/圆角/字号）收敛到 :class:`Palette`，QSS 由令牌渲染。
  新增主题 = 在 ``THEMES`` 注册一个新 Palette；页面代码禁止内联
  ``setStyleSheet``，语义化状态用动态属性 + QSS 属性选择器表达
  （``[muted="true"]``、``[variant="primary"]``、``[severity="..."]``）。
- 动态属性在运行时改变后必须 unpolish/polish 才生效，
  统一走 :func:`repolish`（widgets.py）。

Requires a live ``QApplication`` for font handling; call
:func:`apply_theme` once from ``main_qt.py`` after app construction.
"""

from dataclasses import dataclass

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    """One visual theme. Light values first; a dark palette only needs to
    override these fields, the QSS template is shared."""

    # 基底
    window: str          # 应用窗口背景
    surface: str         # 卡片/输入/列表表面
    sidebar: str         # 侧边栏背景
    sidebar_hover: str
    border: str          # 控件描边
    divider: str         # 分隔线/表头线

    # 文本
    text: str
    muted: str

    # 强调色与语义色
    accent: str
    accent_hover: str
    accent_soft: str     # 选中项浅色底（侧边栏 checked、列表选中）
    on_accent: str
    success: str
    warning: str
    danger: str

    # 输入态
    input_bg: str
    input_border: str
    disabled_bg: str
    disabled_text: str
    hover_bg: str        # 次级按钮 hover

    # 日志控制台（浅色主题下也用深底，终端观感、级别配色更清晰）
    log_bg: str
    log_text: str
    log_muted: str

    # 形状与字体
    radius: str = "6px"
    radius_lg: str = "8px"
    font_family: str = '"Segoe UI", "Microsoft YaHei UI", sans-serif'
    mono_family: str = '"Consolas", "Courier New", monospace'


LIGHT = Palette(
    window="#f5f6f8",
    surface="#ffffff",
    sidebar="#eceef2",
    sidebar_hover="#e1e5eb",
    border="#dde1e7",
    divider="#e5e7eb",
    text="#1f2937",
    muted="#6b7280",
    accent="#2563eb",
    accent_hover="#1d4ed8",
    accent_soft="#dbeafe",
    on_accent="#ffffff",
    success="#16a34a",
    warning="#d97706",
    danger="#dc2626",
    input_bg="#ffffff",
    input_border="#cfd4dc",
    disabled_bg="#f0f1f3",
    disabled_text="#9ca3af",
    hover_bg="#f2f4f7",
    log_bg="#10151d",
    log_text="#cbd5e1",
    log_muted="#64748b",
)

THEMES: dict[str, Palette] = {"light": LIGHT}


def build_qss(p: Palette) -> str:
    """Render the full application stylesheet from a palette."""
    return f"""
* {{ outline: none; }}

QWidget {{
    color: {p.text};
    font-family: {p.font_family};
    font-size: 9pt;
}}

QMainWindow, QDialog {{
    background: {p.window};
}}

/* ── 按钮 ── */
QPushButton {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {p.radius};
    padding: 5px 14px;
}}
QPushButton:hover {{ background: {p.hover_bg}; }}
QPushButton:pressed {{ background: {p.sidebar}; }}
QPushButton:focus {{ border-color: {p.accent}; }}
QPushButton:disabled {{
    background: {p.disabled_bg};
    color: {p.disabled_text};
    border-color: {p.border};
}}
QPushButton[variant="primary"] {{
    background: {p.accent};
    border-color: {p.accent};
    color: {p.on_accent};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {p.accent_hover}; }}
QPushButton[variant="danger"] {{
    color: {p.danger};
    border-color: {p.border};
}}

/* ── 输入控件 ── */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {p.input_bg};
    border: 1px solid {p.input_border};
    border-radius: {p.radius};
    padding: 4px 8px;
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {p.accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {p.disabled_bg};
    color: {p.disabled_text};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
}}

/* ── 分组框 ── */
QGroupBox {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {p.radius_lg};
    margin-top: 12px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    top: 0px;
    padding: 0 4px;
    color: {p.text};
}}

/* ── 列表/树/表 ── */
QTreeWidget, QTableWidget, QListWidget, QTextEdit, QPlainTextEdit {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {p.radius};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
}}
QHeaderView::section {{
    background: {p.hover_bg};
    color: {p.muted};
    border: none;
    border-right: 1px solid {p.divider};
    border-bottom: 1px solid {p.divider};
    padding: 5px 8px;
    font-weight: 600;
}}
QTreeView::item, QTableView::item, QListView::item {{ padding: 2px 4px; }}

/* ── 进度条 / 菜单 / 工具提示 ── */
QProgressBar {{
    background: {p.divider};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
QMenu {{
    background: {p.surface};
    border: 1px solid {p.border};
    padding: 4px;
}}
QMenu::item {{ padding: 5px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QMenu::separator {{ height: 1px; background: {p.divider}; margin: 4px 6px; }}
QToolTip {{
    background: #111827;
    color: #f9fafb;
    border: none;
    padding: 4px 8px;
}}
QMessageBox, QInputDialog {{ background: {p.window}; }}

/* ── 滚动条 ── */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.input_border}; border-radius: 4px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.input_border}; border-radius: 4px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── 分隔器 ── */
QSplitter::handle {{ background: {p.divider}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}

/* ── 语义化标签状态 ── */
QLabel[muted="true"] {{ color: {p.muted}; }}
QLabel[severity="success"] {{ color: {p.success}; font-weight: 600; }}
QLabel[severity="warning"] {{ color: {p.warning}; font-weight: 600; }}
QLabel[severity="error"] {{ color: {p.danger}; font-weight: 600; }}

/* ── 外壳：侧边栏 ── */
#sidebar {{
    background: {p.sidebar};
    border-right: 1px solid {p.border};
}}
#brandTitle {{
    font-size: 13pt;
    font-weight: 700;
    color: {p.text};
    background: transparent;
}}
#brandVersion {{
    color: {p.muted};
    font-size: 8pt;
    background: transparent;
}}
#navGroupLabel {{
    color: {p.muted};
    font-size: 8pt;
    font-weight: 600;
    padding-left: 4px;
    background: transparent;
}}
QPushButton[nav="true"] {{
    background: transparent;
    border: none;
    border-radius: {p.radius};
    padding: 7px 10px;
    text-align: left;
    color: {p.text};
}}
QPushButton[nav="true"]:hover {{ background: {p.sidebar_hover}; }}
QPushButton[nav="true"]:checked {{
    background: {p.accent_soft};
    color: {p.accent};
    font-weight: 600;
}}

/* ── 外壳：顶栏 ── */
#headerBar {{
    background: {p.window};
    border-bottom: 1px solid {p.border};
}}
#pageTitle {{ font-size: 12pt; font-weight: 600; background: transparent; }}
#rootPathLabel {{ color: {p.muted}; background: transparent; }}

/* ── 日志控制台 ── */
#logView {{
    background: {p.log_bg};
    color: {p.log_text};
    border: 1px solid {p.border};
    font-family: {p.mono_family};
    font-size: 9pt;
}}
"""


def apply_theme(app: QApplication, name: str = "light") -> None:
    """Apply a registered theme to the whole application."""
    palette = THEMES.get(name)
    if palette is None:
        raise KeyError(f"未知主题: {name!r}，可用: {sorted(THEMES)}")
    app.setFont(QFont("Segoe UI", 9))
    app.setStyleSheet(build_qss(palette))
    app.setProperty("theme", name)
