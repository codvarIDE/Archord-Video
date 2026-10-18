"""Copy / paste / duplicate of one or many clips."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from archord_video.core.project import Keyframe, MediaItem, Project

try:
    import PyQt5  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


def make_project():
    p = Project()
    p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
    p.add_media(MediaItem("m3", "/tmp/c.wav", "audio", 8.0, has_audio=True))
    v = p.place_clip("m1", start=2.0, duration=4.0, in_point=1.0)
    a = p.place_clip("m3", start=3.0, duration=5.0)
    v.fade_in, v.fade_out, v.speed, v.volume, v.opacity = 0.5, 1.0, 2.0, 0.4, 0.8
    v.keyframes = [Keyframe(0.0, scale=1.0), Keyframe(2.0, scale=1.5)]
    v.transition = {"type": "fade", "dur": 0.5}
    return p, v, a


class ClipboardModelTests(unittest.TestCase):
    def test_paste_keeps_fades_keyframes_and_effects(self):
        p, v, _ = make_project()
        new = p.paste_clips(p.copy_clips([v.id]), 20.0)
        self.assertEqual(len(new), 1)
        n = new[0]
        self.assertNotEqual(n.id, v.id)
        self.assertEqual((n.start, n.duration, n.in_point), (20.0, 4.0, 1.0))
        self.assertEqual((n.fade_in, n.fade_out, n.speed, n.volume, n.opacity), (0.5, 1.0, 2.0, 0.4, 0.8))
        self.assertEqual([(k.t, k.values) for k in n.keyframes], [(k.t, k.values) for k in v.keyframes])
        self.assertIsNot(n.keyframes[0], v.keyframes[0])                 # independent copies
        self.assertEqual(n.transition, {})                                # a transition isn't carried over
        self.assertEqual(n.track, v.track)                                # free spot: same layer
        n.fade_in = 0.0
        self.assertEqual(v.fade_in, 0.5)

    def test_many_clips_keep_their_spacing_and_layers(self):
        p, v, a = make_project()
        new = p.paste_clips(p.copy_clips([v.id, a.id]), 30.0)
        self.assertEqual([c.start for c in new], [30.0, 31.0])           # v at 2, a at 3 -> gap of 1 s kept
        self.assertEqual([c.track for c in new], [v.track, a.track])

    def test_busy_spot_goes_to_a_new_layer(self):
        p, v, _ = make_project()
        n = p.paste_clips(p.copy_clips([v.id]), v.start)[0]              # pasted right on top of the original
        self.assertNotEqual(n.track, p.clip(v.id).track)                 # a free layer (or a new one), never an overlap
        self.assertTrue(all(o.id == n.id or o.track != n.track or o.end <= n.start or o.start >= n.end
                            for o in p.clips))
        self.assertEqual(sum(1 for c in p.clips if c.media_id == "m1"), 2)
        # with every picture layer taken, a new layer is created
        for i in range(len(p.tracks)):
            if p.tracks[i].get("kind") != "audio" and i not in (n.track, v.track):
                p.place_clip("m1", track=i, start=v.start, duration=v.duration)
        before = len(p.tracks)
        p.paste_clips(p.copy_clips([v.id]), v.start)
        self.assertEqual(len(p.tracks), before + 1)

    def test_locked_layer_is_not_pasted_onto(self):
        p, v, _ = make_project()
        p.tracks[v.track]["locked"] = True
        n = p.paste_clips(p.copy_clips([v.id]), 40.0)[0]
        self.assertNotEqual(n.track, v.track)
        self.assertFalse(p.track_locked(n.track))

    def test_media_removed_since_copy_is_restored(self):
        p, v, _ = make_project()
        data = p.copy_clips([v.id])
        p.remove_media("m1")
        n = p.paste_clips(data, 0.0)[0]
        self.assertEqual(p.media[n.media_id].path, "/tmp/a.mp4")

    def test_title_clip_roundtrip(self):
        p, _, _ = make_project()
        t = p.add_title(1.0, text="Hello")
        n = p.paste_clips(p.copy_clips([t.id]), 9.0)[0]
        self.assertEqual((n.title["text"], n.start), ("Hello", 9.0))
        self.assertIsNot(n.title, t.title)

    def test_empty_copy(self):
        p, _, _ = make_project()
        self.assertIsNone(p.copy_clips([]))
        self.assertEqual(p.paste_clips(None, 0.0), [])


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class ClipboardWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        from archord_video.ui.main_window import MainWindow
        w = MainWindow()
        self.addCleanup(lambda: (w.engine.shutdown(), w.deleteLater()))
        p = w.project
        p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
        self.a = p.place_clip("m1", start=0.0, duration=4.0)
        self.b = p.place_clip("m1", start=5.0, duration=2.0)
        self.a.fade_in = 0.7
        w._reset_history("Test")
        w._refresh_all()
        return w

    def test_copy_paste_duplicate_and_undo(self):
        w = self.window()
        cv = w.timeline.canvas
        w.paste_clips()                                           # empty clipboard: nothing happens
        self.assertEqual(len(w.project.clips), 2)
        cv.select_many({self.a.id, self.b.id}, self.a.id)
        self.assertTrue(w.copy_selected())
        w.engine.seek(20.0)
        w.paste_clips()
        self.assertEqual(len(w.project.clips), 4)
        pasted = cv.selected_ids()                                # the pasted clips are selected afterwards
        self.assertEqual(len(pasted), 2)
        starts = sorted(w.project.clip(i).start for i in pasted)
        self.assertEqual(starts, [20.0, 25.0])
        self.assertTrue(any(w.project.clip(i).fade_in == 0.7 for i in pasted))
        cv.select(self.b.id)
        w.duplicate_selected()                                    # lands right after the original (ends at 7.0)
        dup = w.project.clip(cv.selected)
        self.assertEqual((dup.start, dup.track), (7.0, self.b.track))
        self.assertEqual(len(w.project.clips), 5)
        w.undo()
        w.undo()
        self.assertEqual(len(w.project.clips), 2)


if __name__ == "__main__":
    unittest.main()
