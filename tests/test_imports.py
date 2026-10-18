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
    "archord_video.ui.theme",
    "archord_video.ui.titles",
    "archord_video.ui.timeline",
    "archord_video.ui.preview",
    "archord_video.ui.panels",
    "archord_video.ui.voiceover",
    "archord_video.ui.main_window",
    "archord_video.workers",
    "archord_video.app",
]


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class ImportSmokeTests(unittest.TestCase):
    def test_all_modules_import(self):
        for name in MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
