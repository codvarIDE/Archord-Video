"""Multi-select: group move rules (pure model) and the timeline's selection / drag / delete behaviour."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from archord_video.core.project import MediaItem, Project

try:
    import PyQt5  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


def make_project():
    p = Project()
    p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
    p.add_media(MediaItem("m2", "/tmp/b.png", "image", 5.0, 800, 600))
    a = p.place_clip("m1", start=2.0, duration=3.0)      # 2-5
    b = p.place_clip("m2", start=6.0, duration=2.0)      # 6-8 (same layer, next to a)
    c = p.place_clip("m2", start=12.0, duration=2.0)     # 12-14 (obstacle)
    return p, a, b, c


class GroupMoveModelTests(unittest.TestCase):
    def test_moves_together_keeping_gaps(self):
        p, a, b, _ = make_project()
        self.assertTrue(p.move_many([a.id, b.id], 1.5))
        self.assertEqual((a.start, b.start), (3.5, 7.5))

    def test_clamped_at_zero(self):
        p, a, b, _ = make_project()
        p.move_many([a.id, b.id], -10)
        self.assertEqual((a.start, b.start), (0.0, 4.0))

    def test_stops_at_unselected_neighbour(self):
        p, a, b, c = make_project()
        lo, hi = p.group_move_limits([a.id, b.id])
        self.assertAlmostEqual(hi, c.start - b.end)           # 4.0 s of room before the clip at 12
        p.move_many([a.id, b.id], 99)
        self.assertEqual((a.start, b.start, c.start), (6.0, 10.0, 12.0))

    def test_locked_layer_clips_stay_put(self):
        p, a, b, _ = make_project()
        p.tracks[a.track]["locked"] = True
        self.assertFalse(p.move_many([a.id, b.id], 1.0))
        self.assertEqual((a.start, b.start), (2.0, 6.0))


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class TimelineSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def canvas(self):
        from archord_video.ui.timeline import TimelineCanvas
        p, self.a, self.b, self.c = make_project()
        cv = TimelineCanvas()
        cv.resize(1200, 400)
        cv.set_project(p)
        return cv

    def test_toggle_and_select_all(self):
        cv = self.canvas()
        cv.select(self.a.id)
        cv.toggle_select(self.b.id)
        self.assertEqual(cv.selected_ids(), [self.a.id, self.b.id])
        self.assertEqual(cv.selected, self.b.id)               # the last one clicked is the primary
        cv.toggle_select(self.b.id)
        self.assertEqual((cv.selection, cv.selected), ({self.a.id}, self.a.id))
        cv.select_all()
        self.assertEqual(len(cv.selected_ids()), 3)
        cv.select(None)
        self.assertEqual((cv.selection, cv.selected), (set(), None))

    def test_deleted_clips_leave_the_selection(self):
        cv = self.canvas()
        cv.select_all()
        cv.project.delete([self.b.id])
        cv.set_project(cv.project)
        self.assertEqual(set(cv.selected_ids()), {self.a.id, self.c.id})

    def _center(self, cv, c):
        from PyQt5.QtCore import QPoint
        r = cv.clip_rect(c)
        return QPoint(int(r.center().x()), int(r.center().y()))

    @staticmethod
    def _move(cv, pos, mods=None):
        from PyQt5.QtCore import QEvent, QPointF, Qt
        from PyQt5.QtGui import QMouseEvent
        from PyQt5.QtWidgets import QApplication
        QApplication.sendEvent(cv, QMouseEvent(QEvent.MouseMove, QPointF(pos), Qt.NoButton, Qt.LeftButton,
                                               mods or Qt.NoModifier))

    def test_ctrl_click_marquee_and_group_drag(self):
        from PyQt5.QtCore import QPoint, Qt
        from PyQt5.QtTest import QTest
        cv = self.canvas()
        cv.snap_on = False
        got = []
        cv.clipsMoved.connect(lambda ids, d: got.append((sorted(ids), d)))
        QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, self._center(cv, self.a))
        QTest.mouseClick(cv, Qt.LeftButton, Qt.ControlModifier, self._center(cv, self.b))
        self.assertEqual(cv.selected_ids(), [self.a.id, self.b.id])
        # drag one of them: both move by the same shift, a single clipsMoved is emitted
        start = self._center(cv, self.b)
        QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, start)
        self._move(cv, start + QPoint(40, 0))
        self._move(cv, start + QPoint(80, 0))
        QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, start + QPoint(80, 0))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], sorted([self.a.id, self.b.id]))
        self.assertAlmostEqual(got[0][1], 80 / cv.pps, places=2)
        self.assertEqual(cv.selected_ids(), [self.a.id, self.b.id])      # still a group afterwards
        # plain click on a clip collapses to it; Ctrl-drag on empty space box-selects
        QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, self._center(cv, self.c))
        self.assertEqual(cv.selected_ids(), [self.c.id])
        ra, rc = cv.clip_rect(self.a), cv.clip_rect(self.c)
        top = QPoint(int(ra.left()) - 5, int(ra.top()) - 2)
        low = QPoint(int(rc.right()) + 5, int(rc.bottom()) + 2)
        QTest.mousePress(cv, Qt.LeftButton, Qt.ControlModifier, top)
        self._move(cv, low, Qt.ControlModifier)
        QTest.mouseRelease(cv, Qt.LeftButton, Qt.ControlModifier, low)
        self.assertEqual(len(cv.selected_ids()), 3)


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class MainWindowMultiSelectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_delete_move_and_split_act_on_the_whole_selection(self):
        from archord_video.ui.main_window import MainWindow
        w = MainWindow()
        try:
            p = w.project
            p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
            a = p.place_clip("m1", start=0.0, duration=4.0)
            b = p.place_clip("m1", start=5.0, duration=4.0)
            c = p.place_clip("m1", start=20.0, duration=2.0)
            w._reset_history("Test")
            w._refresh_all()
            cv = w.timeline.canvas
            cv.select(a.id)
            cv.toggle_select(b.id)
            w._on_clips_moved(cv.selected_ids(), 1.0)
            self.assertEqual((p.clip(a.id).start, p.clip(b.id).start), (1.0, 6.0))
            w.engine.seek(2.0)                                 # inside a only (a: 1-5), b is 6-10
            w.split_at_playhead()
            self.assertEqual(len(w.project.clips), 4)
            w.timeline.canvas.select_all()
            w.delete_selected(False)
            self.assertEqual(len(w.project.clips), 0)
            w.undo()
            self.assertEqual(len(w.project.clips), 4)
        finally:
            w.engine.shutdown()
            w.deleteLater()


if __name__ == "__main__":
    unittest.main()
