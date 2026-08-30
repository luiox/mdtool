"""PySide6 entry point for the Markdown toolset.

Run with:  uv run python main_qt.py

The app shares one backend (``server/``, ``utils.py``) and config files with
the Typora uploader workflow; the legacy tkinter entry was removed in the
kb-positioning cleanup.
"""

import sys

from PySide6.QtWidgets import QApplication

from mdtool.desktop.qtui.main_window import MainWindow
from mdtool.desktop.qtui.theme import apply_theme


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MarkdownUtil")
    apply_theme(app, "light")
    app.setQuitOnLastWindowClosed(False)  # keep running when minimized to tray

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
