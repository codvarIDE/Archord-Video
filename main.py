import os
import sys

# Force Qt to use Windows Media Foundation (WMF) instead of outdated DirectShow,
# preventing DirectShowPlayerService 0x80040266 codec errors on Windows.
if sys.platform == "win32":
    os.environ["QT_MULTIMEDIA_PREFERRED_PLUGINS"] = "windowsmediafoundation"

import ctypes
from PyQt5.QtWidgets import QApplication
import theme
from main_window import MainWindow


def main():
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("venma.videoeditor.app.1.0")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Venma Video")
    theme.init(app)
    icon = theme.app_icon()
    app.setWindowIcon(icon)
    win = MainWindow()
    win.setWindowIcon(icon)
    win.show()
    if len(sys.argv) > 1:
        win.import_files(sys.argv[1:])
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
