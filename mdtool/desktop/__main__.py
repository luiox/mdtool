"""``python -m mdtool.desktop``：开发态启动桌面管理器（发行走 PyInstaller exe）。"""

import sys

from mdtool.desktop.main_qt import main

if __name__ == "__main__":
    sys.exit(main())
