"""Transitions between touching clips: model rules, export command and real renders, headless UI."""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from venma_video.core import ffmpeg_tools as ft
from venma_video.core.project import (
    DEFAULT_TRANSITION, MAX_TRANSITION, MIN_TRANSITION, Clip, MediaItem, Project,
)

try:
    import PyQt5  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def two_clips(gap=0.0, dur_a=2.0, dur_b=2.0):
    p = Project(64, 36, 10)
    p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 64, 36, 10, False))
    p.add_media(MediaItem("m2", "/tmp/b.png", "image", 5.0, 64, 36))
    a = p.add_clip("m1", 1, 0.0, duration=dur_a)
    b = p.add_clip("m1", 1, dur_a + gap, duration=dur_b)
    return p, a, b


class TransitionModelTests(unittest.TestCase):
    def test_save_format_and_defaults(self):
        p, a, b = two_clips()
        self.assertNotIn("transition", b.to_dict())
        self.assertTrue(p.set_transition(b.id, "fade"))
        self.assertEqual(b.transition, {"type": "fade", "dur": DEFAULT_TRANSITION})
        self.assertEqual(Clip.from_dict(b.to_dict()).transition, b.transition)

    def test_unknown_names_and_clamped_durations(self):
        p, a, b = two_clips()
        self.assertFalse(p.set_transition(b.id, "wobble"))
        p.set_transition(b.id, "fade", 99)
        self.assertEqual(b.transition["dur"], MAX_TRANSITION)
        p.set_transition(b.id, "fade", 0.0)
        self.assertEqual(b.transition["dur"], MIN_TRANSITION)
        self.assertFalse(p.set_transition(b.id, "fade", 0.0))            # unchanged -> no undo step
        self.assertTrue(p.set_transition(b.id, None))
        self.assertEqual(b.transition, {})

    def test_only_media_clips_unlocked(self):
        p, a, b = two_clips()
        t = p.add_title(5.0)
        self.assertFalse(p.set_transition(t.id, "fade"))
        p.tracks[b.track]["locked"] = True
        self.assertFalse(p.set_transition(b.id, "fade"))

    def test_needs_a_touching_clip_before(self):
        p, a, b = two_clips()
        p.set_transition(b.id, "fade", 1.0)
        self.assertIs(p.transition_prev(b), a)
        self.assertEqual(p.active_transition(b), (a, 1.0))
        p.move(b.id, b.track, 2.5)                                       # gap: the transition is inert, not lost
        self.assertIsNone(p.active_transition(b))
        self.assertEqual(b.transition["type"], "fade")
        p.move(b.id, b.track, 2.0)
        self.assertIsNotNone(p.active_transition(b))
        first = p.clips[0]
        self.assertIsNone(p.transition_prev(first))                      # nothing before the first clip

    def test_other_layers_dont_count(self):
        p, a, b = two_clips()
        p.move(b.id, 0, 2.0)
        p.set_transition(b.id, "fade")
        self.assertIsNone(p.transition_prev(b))

    def test_duration_is_limited_by_the_incoming_clip(self):
        p, a, b = two_clips(dur_b=0.6)
        p.set_transition(b.id, "fade", 2.0)
        self.assertAlmostEqual(p.active_transition(b)[1], 0.6)

    def test_outgoing_clip_is_held_only_during_the_transition(self):
        p, a, b = two_clips()
        p.set_transition(b.id, "fade", 1.0)
        self.assertEqual(p.held_clips_at(1.9), [])
        self.assertEqual([c.id for c in p.held_clips_at(2.5)], [a.id])
        self.assertEqual(p.held_clips_at(3.0), [])
        self.assertAlmostEqual(p.hold_after(a), 1.0)
        self.assertEqual([c.id for c in p.visual_clips_at(2.5)], [a.id, b.id])      # outgoing below, incoming on top
        self.assertEqual([c.id for c in p.video_clips_at(2.5, held=False)], [b.id])
        self.assertEqual([c.id for c in p.video_clips_at(2.5)], [a.id, b.id])
        self.assertAlmostEqual(p.duration(), 4.0)                                    # nothing gets longer

    def test_render_clip_bakes_the_transition_into_the_animation(self):
        p, a, b = two_clips()
        b.animation = {"in": "pop", "in_dur": 0.3, "out": "fade", "out_dur": 0.4}
        a.animation = {"out": "slide_left", "in": "grow"}
        p.set_transition(b.id, "slide_up", 1.0)
        rb, ra = p.render_clip(b), p.render_clip(a)
        self.assertEqual(rb.animation, {"in": "slide_up", "in_dur": 1.0, "out": "fade", "out_dur": 0.4})
        self.assertEqual(ra.animation, {"in": "grow"})                  # its Out would fade the held frame to black
        self.assertEqual(b.animation["in"], "pop")                      # the clips themselves are untouched

    def test_split_keeps_the_transition_on_the_first_half(self):
        p, a, b = two_clips()
        p.set_transition(b.id, "fade", 0.5)
        r = p.split(b.id, 3.0)
        self.assertEqual(b.transition["type"], "fade")
        self.assertEqual(r.transition, {})
        p2, a2, b2 = two_clips()
        p2.set_transition(b2.id, "fade", 0.5)
        right = p2.split(a2.id, 1.0)                                    # cutting the OUTGOING clip: its right half takes over
        self.assertIs(p2.transition_prev(b2), right)

    def test_photos_work_too(self):
        p, a, b = two_clips()
        img = p.add_clip("m2", 1, 4.0, duration=2.0)
        p.set_transition(img.id, "fade", 0.5)
        self.assertIs(p.transition_prev(img), b)


class TransitionCommandTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ft, "ffmpeg_path", return_value="ffmpeg")
        patcher.start()
        self.addCleanup(patcher.stop)

    def graph(self, p):
        cmd = ft.build_export_command(p, "o.mp4")
        return cmd[cmd.index("-filter_complex") + 1]

    def test_outgoing_clip_is_held_and_visible_longer(self):
        p, a, b = two_clips()
        before = self.graph(p)
        self.assertNotIn("tpad", before)
        p.set_transition(b.id, "fade", 1.0)
        g = self.graph(p)
        self.assertIn("tpad=stop_mode=clone:stop_duration=1", g)
        self.assertIn("between(t,0,3)", g)                              # outgoing clip 0..2 stays until 3
        self.assertIn("fade=t=in:st=0:d=1:alpha=1", g)                  # incoming fades in over the same second

    def test_photo_is_looped_longer_instead(self):
        p, a, b = two_clips()
        img = p.add_clip("m2", 1, 4.0, duration=2.0)
        c2 = p.add_clip("m1", 1, 6.0, duration=1.0)
        p.set_transition(c2.id, "fade", 0.5)
        cmd = ft.build_export_command(p, "o.mp4")
        i = cmd.index("/tmp/b.png")
        self.assertEqual(cmd[i - 2], "2.5")                             # -t 2 + 0.5 s hold

    def test_inert_transition_changes_nothing(self):
        p, a, b = two_clips(gap=1.0)
        plain = self.graph(p)
        p.set_transition(b.id, "slide_left", 1.0)
        self.assertEqual(self.graph(p), plain)


COLORS = {"red": (252, 0, 0), "green": (0, 126, 0)}


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class TransitionRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.paths = {}
        for col in COLORS:
            cls.paths[col] = os.path.join(cls.tmp, col + ".mp4")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={col}:s=64x36:r=10:d=2",
                            "-pix_fmt", "yuv420p", cls.paths[col]], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, name, dur):
        p = Project(64, 36, 10)
        p.add_media(MediaItem("r", self.paths["red"], "video", 2.0, 64, 36, 10, False))
        p.add_media(MediaItem("g", self.paths["green"], "video", 2.0, 64, 36, 10, False))
        p.add_clip("r", 1, 0.0)
        b = p.add_clip("g", 1, 2.0)
        if name:
            p.set_transition(b.id, name, dur)
        out = os.path.join(self.tmp, f"{name}.mp4")
        r = subprocess.run(ft.build_export_command(p, out, quality="Low"), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        return out

    def pixel(self, video, t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", video, "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        i = (18 * 64 + 32) * 3
        return raw[i], raw[i + 1], raw[i + 2]

    def duration(self, path):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
        return float(r.stdout)

    def test_dissolve_is_a_real_crossfade_over_the_held_frame(self):
        out = self.render("fade", 1.0)
        self.assertAlmostEqual(self.duration(out), 4.0, delta=0.15)             # length unchanged
        r0, g0, _ = self.pixel(out, 1.5)
        self.assertGreater(r0, 200)                                             # before the cut: red
        r1, g1, _ = self.pixel(out, 2.5)
        self.assertTrue(60 < r1 < 190 and 30 < g1 < 100, (r1, g1))              # half way: a mix, NOT dipping to black
        self.assertGreater(r1 + g1, 150)
        r2, g2, _ = self.pixel(out, 3.5)
        self.assertEqual((r2 < 30, g2 > 100), (True, True))                     # after: green

    def test_slide_covers_the_outgoing_clip(self):
        out = self.render("slide_left", 1.0)
        self.assertGreater(self.pixel(out, 2.05)[0], 200)                       # starts still showing the held red
        self.assertGreater(self.pixel(out, 2.9)[1], 100)                        # ends fully green
        self.assertLess(self.pixel(out, 2.9)[0], 40)

    def test_no_transition_is_a_hard_cut(self):
        out = self.render("", 0.5)
        self.assertGreater(self.pixel(out, 1.9)[0], 200)
        self.assertGreater(self.pixel(out, 2.1)[1], 100)
        self.assertLess(self.pixel(out, 2.1)[0], 40)


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class TransitionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _dispose(w):
        """Stop the preview threads and let their queued signals land while the window still exists, THEN delete it
        (a late "could not decode" message hitting a deleted window would crash the next test)."""
        from PyQt5.QtCore import QCoreApplication, QEvent
        w.engine.shutdown()
        QCoreApplication.processEvents()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def window(self):
        from venma_video.ui.main_window import MainWindow
        w = MainWindow()
        self.addCleanup(self._dispose, w)
        p = w.project
        p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 64, 36, 10, False))
        w.a = p.place_clip("m1", duration=2.0)
        w.b = p.place_clip("m1", start=2.0, duration=2.0)
        w.c = p.place_clip("m1", start=30.0, duration=2.0)
        w._reset_history("Test")
        w._refresh_all()
        return w

    def test_panel_picks_a_transition_as_one_undo_step(self):
        w = self.window()
        w.timeline.canvas.select(w.b.id)
        self.assertFalse(w.props.sec_trans.isHidden())
        self.assertEqual(w.b.track, w.a.track)
        n = len(w.history)
        with mock.patch.object(w.engine, "play"), mock.patch.object(w.engine, "seek"):
            w.props._pick_transition("slide_left")
        self.assertEqual(len(w.history), n + 1)
        self.assertEqual(w.labels[-1], "Transition: Slide Left")
        self.assertTrue(w.props._trans_btns["slide_left"].isChecked())
        self.assertEqual(w.props._trans_hint.text(), "")                        # it has a clip to start from
        w.props._pick_transition("")
        self.assertEqual(w.labels[-1], "Remove Transition")
        w.undo()
        self.assertEqual(w.project.clip(w.b.id).transition["type"], "slide_left")

    def test_duration_slider_is_one_undo_step(self):
        w = self.window()
        w.timeline.canvas.select(w.b.id)
        with mock.patch.object(w.engine, "play"), mock.patch.object(w.engine, "seek"):
            w.props._pick_transition("fade")
        n = len(w.history)
        for v in (8, 12, 15):
            w.props._trans_dur.setValue(v)
        self.assertEqual(len(w.history), n)
        w._edit_timer.stop()
        w.commit(w._edit_label)
        self.assertEqual(w.labels[-1], "Change Transition")
        self.assertAlmostEqual(w.project.clip(w.b.id).transition["dur"], 1.5)

    def test_clip_without_a_neighbour_explains_itself(self):
        w = self.window()
        w.timeline.canvas.select(w.c.id)
        with mock.patch.object(w.engine, "play"):
            w.props._pick_transition("fade")
        self.assertIn("Transition set", w.statusBar().currentMessage())
        self.assertIn("Not showing yet", w.props._trans_hint.text())

    def test_hidden_for_titles_and_reset_clears_it(self):
        w = self.window()
        w.add_title("Title")
        self.assertTrue(w.props.sec_trans.isHidden())
        w.project.set_transition(w.b.id, "fade")
        w.timeline.canvas.select(w.b.id)
        w._on_reset_props()
        self.assertEqual(w.project.clip(w.b.id).transition, {})

    def test_timeline_and_preview_stack_paint(self):
        w = self.window()
        w.project.set_transition(w.b.id, "fade", 1.0)
        w.timeline.canvas.set_project(w.project)
        w.timeline.resize(900, 300)
        self.assertFalse(w.timeline.grab().isNull())
        w.engine.t = 2.4
        stack = w.engine._stack_now()
        self.assertEqual([c.id for c in stack], [w.a.id, w.b.id])
        self.assertEqual(stack[1].animation["in"], "fade")                      # what the view paints
        w.view.resize(400, 250)
        self.assertFalse(w.view.grab().isNull())


if __name__ == "__main__":
    unittest.main()
