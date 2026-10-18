"""Detach audio and freeze frame."""
import os
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from archord_video.core import ffmpeg_tools as ft
from archord_video.core import freeze
from archord_video.core.project import FREEZE_DURATION, Keyframe, MediaItem, Project

try:
    import PyQt5  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_project():
    p = Project()
    p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
    p.add_media(MediaItem("m2", "/tmp/mute.mp4", "video", 10.0, 1920, 1080, 30, False))
    p.add_media(MediaItem("m3", "/tmp/c.wav", "audio", 8.0, has_audio=True))
    v = p.place_clip("m1", start=2.0, duration=4.0, in_point=1.0)
    return p, v


class DetachAudioModelTests(unittest.TestCase):
    def test_sound_moves_to_an_audio_track_and_video_is_muted(self):
        p, v = make_project()
        v.volume, v.fade_in, v.fade_out, v.speed, v.reverse = 0.6, 0.5, 1.0, 2.0, True
        a = p.detach_audio(v.id)
        m = p.media[a.media_id]
        self.assertEqual((m.kind, m.path, m.has_audio), ("audio", "/tmp/a.mp4", True))
        self.assertEqual(p.tracks[a.track]["kind"], "audio")
        self.assertEqual((a.start, a.duration, a.in_point, a.speed, a.reverse), (2.0, 4.0, 1.0, 2.0, True))
        self.assertEqual((a.volume, a.fade_in, a.fade_out), (0.6, 0.5, 1.0))
        self.assertEqual((v.volume, v.fade_in, v.fade_out), (0.0, 0.0, 0.0))
        self.assertEqual(p.clip(v.id).media_id, "m1")                    # the picture stays as it was

    def test_refusals(self):
        p, v = make_project()
        mute = p.place_clip("m2", start=20.0, duration=2.0)
        aud = p.place_clip("m3", start=0.0, duration=2.0)
        self.assertIsNone(p.detach_audio(mute.id))                       # no sound in the file
        self.assertIsNone(p.detach_audio(aud.id))                        # not a video clip
        p.tracks[v.track]["locked"] = True
        self.assertIsNone(p.detach_audio(v.id))                          # locked layer
        p.tracks[v.track]["locked"] = False
        self.assertIsNotNone(p.detach_audio(v.id))
        self.assertIsNone(p.detach_audio(v.id))                          # already muted: no second copy of the sound
        self.assertEqual(sum(1 for c in p.clips if p.media[c.media_id].kind == "audio"), 2)

    def test_second_detach_reuses_the_audio_media(self):
        p, v = make_project()
        v2 = p.place_clip("m1", start=30.0, duration=2.0)
        a1, a2 = p.detach_audio(v.id), p.detach_audio(v2.id)
        self.assertEqual(a1.media_id, a2.media_id)

    def test_overlapping_sound_goes_to_another_audio_track(self):
        p, v = make_project()
        p.place_clip("m3", start=0.0, duration=8.0)                      # the first audio track is busy
        a = p.detach_audio(v.id)
        self.assertEqual(sum(1 for c in p.clips if c.track == a.track), 1)


class FreezeModelTests(unittest.TestCase):
    def test_source_prefers_selected_then_topmost(self):
        p, v = make_project()
        top = p.place_clip("m1", start=2.0, duration=4.0)                # a second layer over the same time span
        self.assertEqual(p.freeze_source(3.0, v.id).id, v.id)
        self.assertEqual(p.freeze_source(3.0).id, min((v, top), key=lambda c: c.track).id)
        self.assertIsNone(p.freeze_source(50.0))
        aud = p.place_clip("m3", start=0.0, duration=8.0)
        self.assertNotEqual(p.freeze_source(3.0, aud.id).id, aud.id)     # audio is never a source

    def test_still_clip_is_3s_in_front_and_keeps_the_look(self):
        p, v = make_project()
        v.brightness, v.saturation, v.rotation = 0.2, 0.5, 10.0
        v.keyframes = [Keyframe(0.0, scale=1.0), Keyframe(2.0, scale=2.0)]
        n = p.add_freeze_frame(v.id, 3.0, "/tmp/Freeze 1.png")           # 1 s into the clip -> scale 1.5
        m = p.media[n.media_id]
        self.assertEqual((m.kind, m.path, m.width, m.height), ("image", "/tmp/Freeze 1.png", 1920, 1080))
        self.assertEqual((n.start, n.duration), (3.0, FREEZE_DURATION))
        self.assertEqual(FREEZE_DURATION, 3.0)
        self.assertAlmostEqual(n.scale, 1.5, places=3)
        self.assertEqual((n.brightness, n.saturation, n.rotation), (0.2, 0.5, 10.0))
        self.assertNotEqual(n.track, v.track)                            # in front, the video is untouched
        self.assertEqual((v.start, v.duration), (2.0, 4.0))
        self.assertEqual(n.keyframes, [])

    def test_same_image_reused(self):
        p, v = make_project()
        a = p.add_freeze_frame(v.id, 3.0, "/tmp/Freeze 1.png")
        b = p.add_freeze_frame(v.id, 20.0, "/tmp/Freeze 1.png")
        self.assertEqual(a.media_id, b.media_id)


class FreezeFileTests(unittest.TestCase):
    def test_naming(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(os.path.basename(freeze.next_freeze_path(d)), "Freeze 1.png")
            for n in ("Freeze 1.png", "Freeze 7.png", "other.png"):
                open(os.path.join(d, n), "w").close()
            self.assertEqual(os.path.basename(freeze.next_freeze_path(d)), "Freeze 8.png")
            self.assertEqual(os.path.basename(freeze.next_freeze_path(os.path.join(d, "missing"))), "Freeze 1.png")
        self.assertEqual(freeze.frames_dir("/x/y/film.avproj", "/fb"), os.path.join("/x/y", "Freeze Frames"))
        self.assertEqual(freeze.frames_dir(None, "/fb"), "/fb")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class RealFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="archord_t_")
        cls.video = os.path.join(cls.tmp, "v.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", "-pix_fmt", "yuv420p",
                        cls.video], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_extract_frame_full_size_and_at_the_very_end(self):
        for at in (1.0, 3.0):                                            # the end (3.0 s) has no frame of its own: retried earlier
            out = os.path.join(self.tmp, f"f{at}.png")
            self.assertTrue(ft.extract_frame(self.video, at, out, 10.0), at)
            self.assertGreater(os.path.getsize(out), 100)
        info = ft.probe(os.path.join(self.tmp, "f1.0.png"))
        self.assertEqual((info["kind"], info["width"], info["height"]), ("image", 320, 180))
        self.assertFalse(ft.extract_frame(os.path.join(self.tmp, "nope.mp4"), 1.0, os.path.join(self.tmp, "x.png")))

    def test_detached_audio_exports_the_sound_once(self):
        p = Project(320, 180, 10)
        info = ft.probe(self.video)
        p.add_media(MediaItem("m1", self.video, **info))
        v = p.place_clip("m1", start=0.0, duration=2.0)
        a = p.detach_audio(v.id)
        out = os.path.join(self.tmp, "out.mp4")
        cmd = ft.build_export_command(p, out)
        argv = cmd[0] if isinstance(cmd, tuple) else cmd
        r = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        got = ft.probe(out)
        self.assertTrue(got["has_audio"])
        self.assertIsNotNone(a)


@unittest.skipUnless(HAVE_QT and HAVE_FFMPEG, "PyQt5 / ffmpeg not installed")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.mkdtemp(prefix="archord_w_")
        cls.video = os.path.join(cls.tmp, "v.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=4",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest", "-pix_fmt", "yuv420p",
                        cls.video], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def window(self):
        from archord_video.ui.main_window import MainWindow
        w = MainWindow()
        self.addCleanup(lambda: (w.engine.shutdown(), w.deleteLater()))
        w.path = os.path.join(self.tmp, "film.avproj")                   # stills go next to the project file
        w.project.add_media(MediaItem("m1", self.video, **ft.probe(self.video)))
        self.v = w.project.place_clip("m1", start=1.0, duration=3.0)
        w._reset_history("Test")
        w._refresh_all()
        return w

    def test_detach_then_undo(self):
        w = self.window()
        w.timeline.canvas.select(self.v.id)
        w.detach_audio()
        audio = [c for c in w.project.clips if w.project.media[c.media_id].kind == "audio"]
        self.assertEqual(len(audio), 1)
        self.assertEqual(w.project.clip(self.v.id).volume, 0.0)
        self.assertEqual(set(w.timeline.canvas.selected_ids()), {self.v.id, audio[0].id})
        w.detach_audio()                                                 # already muted: nothing more happens
        self.assertEqual(len([c for c in w.project.clips if w.project.media[c.media_id].kind == "audio"]), 1)
        w.undo()
        self.assertEqual(w.project.clip(self.v.id).volume, 1.0)
        self.assertEqual(len(w.project.clips), 1)

    def test_freeze_frame_adds_a_3s_still_next_to_the_project(self):
        w = self.window()
        w.engine.seek(2.0)
        w.freeze_frame()
        stills = [c for c in w.project.clips if w.project.media[c.media_id].kind == "image"]
        self.assertEqual(len(stills), 1)
        s = stills[0]
        self.assertEqual((s.start, s.duration), (2.0, 3.0))
        path = w.project.media[s.media_id].path
        self.assertEqual(os.path.dirname(path), os.path.join(self.tmp, "Freeze Frames"))
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(w.timeline.canvas.selected, s.id)
        w.freeze_frame()                                                 # a second one gets its own numbered file
        paths = {w.project.media[c.media_id].path for c in w.project.clips if w.project.media[c.media_id].kind == "image"}
        self.assertEqual(len(paths), 2)
        w.engine.seek(30.0)
        n = len(w.project.clips)
        w.freeze_frame()                                                 # no video under the playhead: nothing happens
        self.assertEqual(len(w.project.clips), n)


if __name__ == "__main__":
    unittest.main()
