import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QFontDatabase, QGuiApplication, QImage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from archord_video.core.project import TITLE_DEFAULTS, Project


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class TitleRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from archord_video.ui import titles
        cls.titles = titles

    def style(self, **kw):
        return dict(TITLE_DEFAULTS, **kw)

    def test_blank_text_draws_nothing(self):
        self.assertIsNone(self.titles.layout(self.style(text="   \n "), QRectF(0, 0, 1920, 1080)))

    def test_size_scales_with_frame_height(self):
        full = self.titles.layout(self.style(size=100), QRectF(0, 0, 1920, 1080))
        half = self.titles.layout(self.style(size=100), QRectF(0, 0, 960, 540))
        self.assertEqual(full[5], 100)          # pixel size at the 1080p reference height
        self.assertEqual(half[5], 50)

    def test_box_stays_inside_frame_at_extreme_positions(self):
        frame = QRectF(0, 0, 1280, 720)
        for x, y in ((0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)):
            box = self.titles.layout(self.style(pos_x=x, pos_y=y), frame)[2]
            self.assertGreaterEqual(box.left(), frame.left() - 1e-6)
            self.assertGreaterEqual(box.top(), frame.top() - 1e-6)
            self.assertLessEqual(box.right(), frame.right() + 1e-6)
            self.assertLessEqual(box.bottom(), frame.bottom() + 1e-6)

    def test_background_box_adds_padding(self):
        frame = QRectF(0, 0, 1920, 1080)
        plain = self.titles.layout(self.style(box=False), frame)[2]
        boxed = self.titles.layout(self.style(box=True), frame)[2]
        self.assertGreater(boxed.width(), plain.width())
        self.assertGreater(boxed.height(), plain.height())

    def test_centre_position_is_centred(self):
        frame = QRectF(0, 0, 1920, 1080)
        box = self.titles.layout(self.style(pos_x=0.5, pos_y=0.5), frame)[2]
        self.assertAlmostEqual(box.center().x(), 960, delta=1)
        self.assertAlmostEqual(box.center().y(), 540, delta=1)

    def test_png_has_project_size_and_alpha(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.titles.render_title_png(os.path.join(d, "t.png"), 640, 360, self.style())
            img = QImage(path)
            self.assertEqual((img.width(), img.height()), (640, 360))
            self.assertTrue(img.hasAlphaChannel())
            self.assertEqual(img.pixelColor(0, 0).alpha(), 0)       # background is transparent

    def test_text_actually_paints_pixels(self):
        if not QFontDatabase().families():
            self.skipTest("no fonts installed on this machine")
        with tempfile.TemporaryDirectory() as d:
            path = self.titles.render_title_png(os.path.join(d, "t.png"), 640, 360, self.style(size=120))
            img = QImage(path)
            self.assertTrue(any(img.pixelColor(x, y).alpha() > 0
                                for x in range(0, 640, 4) for y in range(0, 360, 4)))

    def test_render_project_titles_makes_one_png_per_title(self):
        p = Project(1280, 720, 30)
        a = p.add_title(0.0)
        b = p.add_title(10.0, text="Second")
        with tempfile.TemporaryDirectory() as d:
            out = self.titles.render_project_titles(p, d)
            self.assertEqual(set(out), {a.id, b.id})
            self.assertTrue(all(os.path.isfile(f) for f in out.values()))


if __name__ == "__main__":
    unittest.main()
