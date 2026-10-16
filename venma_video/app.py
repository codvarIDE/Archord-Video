"""Application entry point."""
import os
import sys

# Force Qt to use Windows Media Foundation (WMF) instead of outdated DirectShow,
# preventing DirectShowPlayerService 0x80040266 codec errors on Windows.
# Must be set before any Qt multimedia import.
if sys.platform == "win32":
    os.environ["QT_MULTIMEDIA_PREFERRED_PLUGINS"] = "windowsmediafoundation"

import ctypes

from PyQt5.QtWidgets import QApplication

from .ui import theme
from .ui.main_window import MainWindow


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("venma.videoeditor.app.1.0")
        except Exception:
            pass

    app = QApplication(argv)
    app.setStyle("Fusion")
    app.setApplicationName("Venma Video")
    theme.init(app)
    icon = theme.app_icon()
    app.setWindowIcon(icon)
    win = MainWindow()
    win.setWindowIcon(icon)
    win.show()
    if len(argv) > 1:
        win.import_files(argv[1:])
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
