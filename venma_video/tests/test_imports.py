import importlib
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PyQt5  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

MODULES = [
    "venma_video.ui.theme",
    "venma_video.ui.titles",
    "venma_video.ui.timeline",
    "venma_video.ui.preview",
    "venma_video.ui.panels",
    "venma_video.ui.main_window",
    "venma_video.workers",
    "venma_video.app",
]


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class ImportSmokeTests(unittest.TestCase):
    def test_all_modules_import(self):
        for name in MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
