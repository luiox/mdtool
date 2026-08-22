# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the PySide6 (Qt) build of markdown_util.

Run via scripts/build.py, or directly:
    uv run pyinstaller markdown_util/MarkdownUtilQt.spec --noconfirm

Produces dist/MarkdownUtilQt.exe (onefile by default). For onedir, pass
--onedir via build.py which rewrites the EXE() call below.
"""
from PyInstaller.utils.hooks import collect_all

block_cipher = None

import os

datas = []
# 根 pyproject.toml 随包打入：frozen 态 _version.py 从解包目录读版本号，
# 保持"根 pyproject 是版本唯一权威源"（build 期环境变量注入不可靠）。
_root_pyproject = os.path.join(SPECPATH, os.pardir, 'pyproject.toml')
if os.path.exists(_root_pyproject):
    datas.append((_root_pyproject, '.'))
binaries = []
hiddenimports = [
    # backend (untouched by the Qt port)
    'server.meta_db',
    'server.media_server',
    'server.notes_db',
    'utils',
    # Qt UI layer
    'qtui',
    'qtui.widgets',
    'qtui.icons',
    'qtui.workers',
    'qtui.theme',
    'qtui.logbus',
    'qtui.main_window',
    'qtui.tabs.library',
    'qtui.tabs.search',
    'qtui.tabs.notes_browser',
    'qtui.tabs.file_browser',
    'qtui.tabs.media_server',
    'qtui.tabs.space_fix',
    'qtui.tabs.image_check',
    'qtui.tabs.migrate',
]

# 轻量第三方包整体收集；PySide6 不做 collect_all（会把 WebEngine/3D 等
# 全部拖进来，onefile 体积 240MB+），改为显式列出用到的 Qt 模块，
# 插件（platforms/imageformats/iconengines）由 PyInstaller 的 Qt hook
# 按已用模块自动带出。
for pkg in ('libmarkdown', 'watchdog'):
    tmp = collect_all(pkg)
    datas += tmp[0]
    binaries += tmp[1]
    hiddenimports += tmp[2]

hiddenimports += [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtSvg',
    'shiboken6',
]

a = Analysis(
    ['main_qt.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MarkdownUtilQt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,               # TODO: add an .ico later for taskbar/dock icon
)
