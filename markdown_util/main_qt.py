"""PySide6 entry point for the Markdown toolset.

Run with:  uv run python main_qt.py

The legacy tkinter entry point ``main.py`` is kept alongside for as long as
the Qt migration is in progress; both share the same backend
(``server/``, ``utils.py``) and config files.
"""

import sys

from PySide6.QtWidgets import QApplication

from qtui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MarkdownUtil")
    app.setQuitOnLastWindowClosed(False)  # keep running when minimized to tray

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
