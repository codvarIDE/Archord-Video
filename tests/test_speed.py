"""Speed control and reverse: model rules, FFmpeg commands, and real renders."""
import math
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from venma_video.core import ffmpeg_tools as ft
from venma_video.core.project import (
    MAX_SPEED, MIN_CLIP, MIN_SPEED, Clip, MediaItem, Project,
)

try:
    import PyQt5  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_project():
    p = Project()
    p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
    p.add_media(MediaItem("m2", "/tmp/b.png", "image", 5.0, 800, 600))
    p.add_media(MediaItem("m3", "/tmp/c.wav", "audio", 8.0, has_audio=True))
    return p


# ── model ────────────────────────────────────────────────────────────────────────────────────
class SpeedModelTests(unittest.TestCase):
    def test_defaults_and_save_format(self):
        c = Clip("c1", "m1", 0, 0, 2.0, 4.0)
        self.assertEqual((c.speed, c.reverse, c.retimed()), (1.0, False, False))
        d = c.to_dict()
        self.assertNotIn("speed", d)                    # untouched clips save exactly as before
        self.assertNotIn("reverse", d)
        c.speed, c.reverse = 2.0, True
        back = Clip.from_dict(c.to_dict())
        self.assertEqual((back.speed, back.reverse), (2.0, True))

    def test_old_project_files_load(self):
        p = make_project()
        p.add_clip("m1", 1, 0.0, duration=3.0)
        data = p.to_dict()
        for cd in data["clips"]:
            cd.pop("speed", None)
            cd.pop("reverse", None)
        q = Project.from_dict(data)
        self.assertEqual((q.clips[0].speed, q.clips[0].reverse), (1.0, False))

    def test_speed_changes_length_and_keeps_the_source_window(self):
        p = make_project()
        c = p.add_clip("m1", 1, 1.0, duration=6.0, in_point=2.0)
        self.assertTrue(p.set_speed(c.id, 2.0))
        self.assertAlmostEqual(c.duration, 3.0)
        self.assertAlmostEqual(c.span, 6.0)
        self.assertAlmostEqual(c.in_point, 2.0)
        self.assertTrue(p.set_speed(c.id, 0.5))
        self.assertAlmostEqual(c.duration, 12.0)
        self.assertAlmostEqual(c.span, 6.0)             # still the same 6 s of source
        self.assertFalse(p.set_speed(c.id, 0.5))        # no change -> False (no empty undo step)

    def test_speed_is_clamped(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0, duration=8.0)
        p.set_speed(c.id, 1000)
        self.assertLessEqual(c.speed, MAX_SPEED)
        self.assertGreaterEqual(c.duration, MIN_CLIP - 1e-9)
        p.set_speed(c.id, 0.0001)
        self.assertAlmostEqual(c.speed, MIN_SPEED)

    def test_a_short_clip_cannot_get_shorter_than_min_clip(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0, duration=0.5)
        self.assertAlmostEqual(p.max_speed(c.id), 0.5 / MIN_CLIP)
        p.set_speed(c.id, 20.0)
        self.assertGreaterEqual(c.duration, MIN_CLIP - 1e-9)

    def test_slowing_down_pushes_later_clips_only_as_far_as_needed(self):
        p = make_project()
        a = p.add_clip("m1", 1, 0.0, duration=4.0)
        b = p.add_clip("m1", 1, 6.0, duration=2.0)
        c = p.add_clip("m1", 1, 9.0, duration=1.0)
        p.set_speed(a.id, 0.5)                          # 4 s -> 8 s: overlaps b by 2 s
        self.assertAlmostEqual(a.end, 8.0)
        self.assertAlmostEqual(b.start, 8.0)
        self.assertAlmostEqual(c.start, 11.0)           # everything after moves together, gaps preserved
        p.set_speed(a.id, 2.0)                          # shorter: nothing moves, a gap is left
        self.assertAlmostEqual(b.start, 8.0)

    def test_speed_stretches_keyframes(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0, duration=4.0)
        c.set_keyframe(0.0, pos_x=0.2)
        c.set_keyframe(2.0, pos_x=0.8)
        p.set_speed(c.id, 2.0)
        self.assertEqual([round(k.t, 3) for k in c.keyframes], [0.0, 1.0])

    def test_photos_titles_and_locked_layers_refuse(self):
        p = make_project()
        img = p.add_clip("m2", 1, 0.0, duration=3.0)
        title = p.add_title(5.0)
        v = p.add_clip("m1", 0, 0.0, duration=4.0)
        for c in (img, title):
            self.assertFalse(p.can_retime(c.id))
            self.assertFalse(p.set_speed(c.id, 2.0))
            self.assertFalse(p.set_reverse(c.id, True))
        p.tracks[v.track]["locked"] = True
        self.assertFalse(p.set_speed(v.id, 2.0))
        self.assertFalse(p.set_reverse(v.id, True))

    def test_source_time(self):
        c = Clip("c1", "m1", 0, 0, 2.0, 4.0, speed=2.0)         # plays source 2..10
        self.assertAlmostEqual(c.source_time(0), 2.0)
        self.assertAlmostEqual(c.source_time(1.5), 5.0)
        c.reverse = True
        self.assertAlmostEqual(c.source_time(0), 10.0)
        self.assertAlmostEqual(c.source_time(1.5), 7.0)
        self.assertAlmostEqual(c.source_time(4.0), 2.0)

    def test_reset_effects_does_not_touch_speed(self):
        from venma_video.core.project import CLIP_DEFAULTS
        self.assertNotIn("speed", CLIP_DEFAULTS)                # the Reset button would change the clip's length
        self.assertNotIn("reverse", CLIP_DEFAULTS)

    def test_reverse_keeps_length_and_window(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0, duration=4.0, in_point=1.0)
        self.assertTrue(p.set_reverse(c.id, True))
        self.assertEqual((c.duration, c.in_point), (4.0, 1.0))
        self.assertFalse(p.set_reverse(c.id, True))


class SplitTrimTests(unittest.TestCase):
    def setUp(self):
        self.p = make_project()

    def covered(self, c):
        """Source range (lo, hi) the clip plays, whichever way round."""
        a, b = c.source_time(0), c.source_time(c.duration)
        return round(min(a, b), 6), round(max(a, b), 6)

    def test_split_forward_with_speed(self):
        c = self.p.add_clip("m1", 1, 0.0, duration=3.0, in_point=1.0)
        self.p.set_speed(c.id, 2.0)                     # 1.5 s long, plays source 1..4
        r = self.p.split(c.id, 0.5)
        self.assertEqual(self.covered(c), (1.0, 2.0))
        self.assertEqual(self.covered(r), (2.0, 4.0))
        self.assertEqual((r.speed, r.reverse), (2.0, False))

    def test_split_reversed_halves_play_back_to_back(self):
        c = self.p.add_clip("m1", 1, 0.0, duration=4.0, in_point=1.0)
        self.p.set_reverse(c.id, True)                  # plays source 5 -> 1
        r = self.p.split(c.id, 1.0)
        self.assertEqual(self.covered(c), (4.0, 5.0))   # the left half shows the END of the source window
        self.assertEqual(self.covered(r), (1.0, 4.0))
        self.assertAlmostEqual(c.source_time(c.duration), r.source_time(0))      # no jump at the cut
        self.assertTrue(r.reverse)

    def test_split_reversed_and_sped_up_is_seamless(self):
        c = self.p.add_clip("m1", 1, 0.0, duration=6.0, in_point=1.0)
        self.p.set_speed(c.id, 2.0)
        self.p.set_reverse(c.id, True)                  # 3 s long, source 7 -> 1
        r = self.p.split(c.id, 1.0)
        self.assertAlmostEqual(c.source_time(0), 7.0)
        self.assertAlmostEqual(c.source_time(c.duration), r.source_time(0))
        self.assertAlmostEqual(r.source_time(r.duration), 1.0)

    def test_trim_head_forward_and_reversed(self):
        c = self.p.add_clip("m1", 1, 2.0, duration=4.0, in_point=1.0)       # forward: plays 1..5
        self.p.trim_left(c.id, 3.0)                      # cut 1 s off the head
        self.assertEqual(self.covered(c), (2.0, 5.0))
        r = self.p.add_clip("m1", 2, 2.0, duration=4.0, in_point=1.0)
        self.p.set_reverse(r.id, True)                   # plays 5 -> 1
        self.p.trim_left(r.id, 3.0)                      # the head of a reversed clip is the END of the window
        self.assertEqual(self.covered(r), (1.0, 4.0))

    def test_trim_tail_forward_and_reversed(self):
        c = self.p.add_clip("m1", 1, 0.0, duration=4.0, in_point=1.0)
        self.p.trim_right(c.id, 3.0)
        self.assertEqual(self.covered(c), (1.0, 4.0))
        r = self.p.add_clip("m1", 2, 0.0, duration=4.0, in_point=1.0)
        self.p.set_reverse(r.id, True)
        self.p.trim_right(r.id, 3.0)                     # the tail of a reversed clip is the START of the window
        self.assertEqual(self.covered(r), (2.0, 5.0))

    def test_trim_limits_follow_the_source(self):
        c = self.p.add_clip("m1", 1, 5.0, duration=2.0, in_point=4.0)        # source 4..6 of 10
        self.assertAlmostEqual(self.p.trim_limits(c.id)[1], 7.0 + 4.0)       # 4 s of source left after it
        self.p.set_speed(c.id, 2.0)                                          # now 1 s long, plays 4..6
        self.assertAlmostEqual(self.p.trim_limits(c.id)[1], 6.0 + 2.0)       # 4 s of source = 2 s on the timeline
        self.p.set_reverse(c.id, True)
        lo, hi = self.p.trim_limits(c.id)
        self.assertAlmostEqual(hi, 6.0 + 2.0)            # tail grows into the 4 s of source BEFORE the window
        self.assertAlmostEqual(lo, 5.0 - 2.0)            # head grows into the 4 s AFTER it

    def test_trimming_never_goes_past_the_media(self):
        c = self.p.add_clip("m1", 1, 0.0, duration=4.0, in_point=0.0)
        self.p.set_reverse(c.id, True)
        self.p.trim_right(c.id, 99.0)                    # tail can't grow: no source before the window
        self.assertAlmostEqual(c.duration, 4.0)
        self.assertGreaterEqual(c.in_point, 0.0)

    def test_ripple_trim_respects_reverse(self):
        c = self.p.add_clip("m1", 1, 0.0, duration=4.0, in_point=1.0)
        later = self.p.add_clip("m1", 1, 4.0, duration=2.0)
        self.p.set_reverse(c.id, True)
        self.p.ripple_trim(c.id, "right", 3.0)           # shorten the tail
        self.assertEqual(self.covered(c), (2.0, 5.0))
        self.assertAlmostEqual(later.start, 3.0)         # followers close the gap

    def test_slip_range_uses_the_source_span(self):
        c = self.p.add_clip("m1", 1, 0.0, duration=4.0, in_point=1.0)
        self.assertEqual(self.p.slip_limits(c.id), (0.0, 6.0))
        c.duration = 8.0
        self.p.set_speed(c.id, 2.0)                      # 4 s long now, still plays 8 s of the 10 s source
        self.assertEqual((c.duration, c.span), (4.0, 8.0))
        self.assertEqual(self.p.slip_limits(c.id), (0.0, 2.0))


# ── ffmpeg commands ──────────────────────────────────────────────────────────────────────────
class CommandTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ft, "ffmpeg_path", return_value="ffmpeg")
        patcher.start()
        self.addCleanup(patcher.stop)

    def project(self, speed=1.0, reverse=False):
        p = Project(1280, 720, 30)
        p.add_media(MediaItem("m1", "clip.mp4", "video", 10.0, 1920, 1080, 30, True))
        c = p.add_clip("m1", 1, 0.0, duration=4.0, in_point=1.0)
        p.set_speed(c.id, speed)
        p.set_reverse(c.id, reverse)
        return p, c

    def test_atempo_chain(self):
        self.assertEqual(ft._atempo_chain(1.0), [])
        self.assertEqual(ft._atempo_chain(2.0), ["atempo=2"])
        self.assertEqual(ft._atempo_chain(1.5), ["atempo=1.5"])
        self.assertEqual(ft._atempo_chain(4.0), ["atempo=2", "atempo=2"])
        self.assertEqual(ft._atempo_chain(0.25), ["atempo=0.5", "atempo=0.5"])
        for sp in (0.1, 0.3, 0.75, 3.0, 7.0, 20.0):
            prod = 1.0
            for f in ft._atempo_chain(sp):
                v = float(f.split("=")[1])
                self.assertTrue(0.5 <= v <= 2.0, f)     # every stage within what old ffmpeg builds accept
                prod *= v
            self.assertAlmostEqual(prod, sp, places=4)

    def test_export_reads_the_whole_source_window(self):
        p, c = self.project(speed=2.0)                   # 2 s long, 4 s of source
        cmd = ft.build_export_command(p, "o.mp4")
        i = cmd.index("-i")
        self.assertEqual(cmd[i - 4:i], ["-ss", "1", "-t", "4"])
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("setpts=(PTS-STARTPTS)/2", graph)
        self.assertIn("atempo=2", graph)
        self.assertNotIn("reverse", graph)

    def test_unchanged_clips_export_as_before(self):
        p, c = self.project()
        graph = ft.build_export_command(p, "o.mp4")[ft.build_export_command(p, "o.mp4").index("-filter_complex") + 1]
        self.assertNotIn("setpts=(PTS", graph)
        self.assertNotIn("atempo", graph)
        self.assertNotIn("reverse", graph)

    def test_inline_reverse_when_nothing_was_prerendered(self):
        p, c = self.project(reverse=True)
        graph = ft.build_export_command(p, "o.mp4")[ft.build_export_command(p, "o.mp4").index("-filter_complex") + 1]
        self.assertIn("reverse,", graph)
        self.assertIn("areverse", graph)

    def test_prerendered_reverse_is_read_from_its_start(self):
        p, c = self.project(speed=2.0, reverse=True)
        cmd = ft.build_export_command(p, "o.mp4", reversed_media={c.id: "rev.mkv"})
        self.assertIn("rev.mkv", cmd)
        self.assertNotIn("clip.mp4", cmd)
        i = cmd.index("-i")
        self.assertEqual(cmd[i - 2:i], ["-t", "4"])      # no -ss: the file already starts at the window's end
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertNotIn("areverse", graph)              # ...and is not reversed a second time
        self.assertIn("atempo=2", graph)

    def test_reversed_clips_lists_only_what_renders(self):
        p, c = self.project(reverse=True)
        self.assertEqual([x.id for x in ft.reversed_clips(p)], [c.id])
        p.tracks[c.track]["hidden"] = True
        p.tracks[c.track]["muted"] = True
        self.assertEqual(ft.reversed_clips(p), [])       # hidden + muted: nothing to render, nothing to reverse

    def test_reverse_chunks_cover_the_window_back_to_front(self):
        c = Clip("c1", "m1", 0, 0, 1.0, 4.0, speed=2.0, reverse=True)       # 4 s long on the timeline, source 1..9
        parts = list(ft.reverse_chunks(c, 0.0, chunk=0.75, fps=30))
        self.assertAlmostEqual(parts[0][1], 9.0)                  # starts at the END of the source window
        self.assertAlmostEqual(parts[-1][0], 1.0)                 # finishes at its beginning
        for (lo1, hi1, _), (lo2, hi2, _) in zip(parts, parts[1:]):
            self.assertAlmostEqual(lo1, hi2)                      # seamless, no gaps or overlaps
        self.assertEqual(sum(n for _, _, n in parts), 120)         # exactly 4 s of frames: no clock drift

    def test_reverse_chunks_from_the_middle_of_a_clip(self):
        c = Clip("c1", "m1", 0, 0, 0.0, 4.0, reverse=True)
        parts = list(ft.reverse_chunks(c, 1.5, chunk=1.0, fps=25))
        self.assertAlmostEqual(parts[0][1], 4.0 - 1.5)
        self.assertEqual(sum(n for _, _, n in parts), round(2.5 * 25))

    def test_preview_commands(self):
        p, c = self.project(speed=2.0)
        vf = " ".join(ft.layer_stream_cmd("clip.mp4", 1.0, 4.0, 960, 540, 30, c))
        self.assertIn("fps=15", vf)                      # 15 samples per SOURCE second -> 30 per timeline second
        self.assertIn("settb=1/30,setpts=N,fps=30", vf)
        plain = " ".join(ft.layer_stream_cmd("clip.mp4", 1.0, 4.0, 960, 540, 30, Clip("x", "m1", 0, 0, 0, 4)))
        self.assertNotIn("setpts", plain)                # normal clips decode exactly as before
        af = " ".join(ft.audio_stream_cmd("clip.mp4", 1.0, 4.0, 1.0, 48000, 2, speed=2.0, reverse=True))
        self.assertIn("areverse,atempo=2,volume=1", af)


# ── real ffmpeg ──────────────────────────────────────────────────────────────────────────────
COLORS = ("red", "green", "blue", "yellow")
W, H, FPS = 64, 36, 10


def classify(rgb):
    r, g, b = rgb
    if r > 140 and g < 80 and b < 80:
        return "red"
    if g > 90 and r < 60 and b < 60:
        return "green"
    if b > 140 and r < 80 and g < 80:
        return "blue"
    if r > 140 and g > 140 and b < 80:
        return "yellow"
    return f"?{rgb}"


def probe_duration(path, stream="v"):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", "stream=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class RealRenderTests(unittest.TestCase):
    """A 4 s source: red, green, blue, yellow for one second each (with a tone), so the picture at any moment says
    exactly which part of the source is playing."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = os.path.join(cls.tmp, "colors.mp4")
        inputs = []
        for col in COLORS:
            inputs += ["-f", "lavfi", "-i", f"color=c={col}:s={W}x{H}:r={FPS}:d=1"]
        graph = "".join(f"[{i}:v]" for i in range(4)) + "concat=n=4:v=1:a=0[v]"
        subprocess.run(["ffmpeg", "-y", "-v", "error"] + inputs + ["-f", "lavfi", "-i", "sine=f=440:d=4",
                        "-filter_complex", graph, "-map", "[v]", "-map", "4:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", cls.src], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def project(self, speed=1.0, reverse=False, in_point=0.0, dur=4.0):
        p = Project(W, H, FPS)
        p.add_media(MediaItem("m1", self.src, "video", 4.0, W, H, FPS, True))
        c = p.add_clip("m1", 1, 0.0, duration=dur, in_point=in_point)
        p.set_speed(c.id, speed)
        p.set_reverse(c.id, reverse)
        return p, c

    def pixel(self, video, t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        i = ((H // 2) * W + W // 2) * 3
        return classify((raw[i], raw[i + 1], raw[i + 2]))

    def render(self, p, rev=None, name="out.mp4"):
        out = os.path.join(self.tmp, name)
        r = subprocess.run(ft.build_export_command(p, out, quality="Low", reversed_media=rev),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        return out

    def check_colors(self, out, expected_at):
        for t, want in expected_at:
            self.assertEqual(self.pixel(out, t), want, f"at {t}s")

    def test_speed_up_plays_the_source_faster(self):
        p, c = self.project(speed=2.0)
        out = self.render(p)
        self.assertAlmostEqual(probe_duration(out), 2.0, delta=0.15)
        self.assertAlmostEqual(probe_duration(out, "a"), 2.0, delta=0.15)       # sound is sped up too
        self.check_colors(out, [(0.2, "red"), (0.7, "green"), (1.2, "blue"), (1.7, "yellow")])

    def test_slow_motion_stretches_the_source(self):
        p, c = self.project(speed=0.5, dur=2.0)                     # source 0..2 over 4 s
        out = self.render(p)
        self.assertAlmostEqual(probe_duration(out), 4.0, delta=0.15)
        self.check_colors(out, [(0.5, "red"), (1.5, "red"), (2.5, "green"), (3.5, "green")])

    def test_reverse_inline(self):
        p, c = self.project(reverse=True)
        out = self.render(p)
        self.assertAlmostEqual(probe_duration(out), 4.0, delta=0.15)
        self.check_colors(out, [(0.5, "yellow"), (1.5, "blue"), (2.5, "green"), (3.5, "red")])

    def test_reverse_in_pieces_gives_the_same_picture(self):
        p, c = self.project(reverse=True)
        d = tempfile.mkdtemp(dir=self.tmp)
        steps = []
        rev = ft.render_reversed_media(p, d, chunk=1.0, progress=steps.append)
        self.assertEqual(list(rev), [c.id])
        self.assertAlmostEqual(steps[-1], 1.0)                      # progress reaches 100 %
        self.assertEqual(os.listdir(d), [os.path.basename(rev[c.id])])      # temporary pieces are cleaned up
        out = self.render(p, rev)
        self.assertAlmostEqual(probe_duration(out), 4.0, delta=0.15)
        self.assertAlmostEqual(probe_duration(out, "a"), 4.0, delta=0.2)
        self.check_colors(out, [(0.5, "yellow"), (1.5, "blue"), (2.5, "green"), (3.5, "red")])

    def test_reverse_plus_speed_on_a_trimmed_window(self):
        p, c = self.project(speed=2.0, reverse=True, in_point=1.0, dur=3.0)     # source 1..4 backwards, 2x -> 1.5 s
        d = tempfile.mkdtemp(dir=self.tmp)
        out = self.render(p, ft.render_reversed_media(p, d, chunk=1.2))
        self.assertAlmostEqual(probe_duration(out), 1.5, delta=0.15)
        self.check_colors(out, [(0.2, "yellow"), (0.7, "blue"), (1.25, "green")])

    def test_cancel_stops_the_reverse_render(self):
        p, c = self.project(reverse=True)
        with self.assertRaises(RuntimeError) as cm:
            ft.render_reversed_media(p, tempfile.mkdtemp(dir=self.tmp), chunk=1.0, cancelled=lambda: True)
        self.assertIn("cancel", str(cm.exception).lower())

    def test_preview_streams_emit_exactly_the_frames_the_clock_expects(self):
        w, h, fps = 32, 18, 10
        for speed in (0.5, 1.0, 2.0, 4.0):
            for reverse in (False, True):
                with self.subTest(speed=speed, reverse=reverse):
                    p, c = self.project(speed=speed, reverse=reverse)
                    if reverse:
                        n = 0
                        for lo, hi, frames in ft.reverse_chunks(c, 0.0, 1.0, fps):
                            r = subprocess.run(ft.layer_stream_cmd(self.src, lo, hi - lo, w, h, fps, c, frames=frames),
                                               capture_output=True)
                            self.assertEqual(r.returncode, 0, r.stderr[-300:])
                            n += len(r.stdout) // (w * h * 3)
                    else:
                        r = subprocess.run(ft.layer_stream_cmd(self.src, c.source_time(0), c.span, w, h, fps, c),
                                           capture_output=True)
                        self.assertEqual(r.returncode, 0, r.stderr[-300:])
                        n = len(r.stdout) // (w * h * 3)
                    self.assertAlmostEqual(n, round(c.duration * fps), delta=2)

    def test_preview_audio_has_the_right_length(self):
        for speed, reverse in ((2.0, False), (0.5, True), (1.0, True)):
            with self.subTest(speed=speed, reverse=reverse):
                p, c = self.project(speed=speed, reverse=reverse)
                total = 0
                plan = ft.reverse_chunks(c, 0.0, 2.0) if reverse else [(0.0, c.span, None)]
                for lo, hi, _ in plan:
                    r = subprocess.run(ft.audio_stream_cmd(self.src, lo, hi - lo, 1.0, 48000, 2, speed=speed,
                                                           reverse=reverse), capture_output=True)
                    self.assertEqual(r.returncode, 0, r.stderr[-300:])
                    total += len(r.stdout)
                self.assertAlmostEqual(total / (48000 * 2 * 2), c.duration, delta=0.25)


# ── UI (headless) ────────────────────────────────────────────────────────────────────────────
@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class UiTests(unittest.TestCase):
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
        p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
        p.add_media(MediaItem("m2", "/tmp/b.png", "image", 5.0, 800, 600))
        w.vclip = p.place_clip("m1", duration=6.0, in_point=1.0)
        w.iclip = p.place_clip("m2", start=20.0)
        w._reset_history("Test")
        w._refresh_all()
        return w

    def select(self, w, clip):
        w.timeline.canvas.select(clip.id)

    def test_speed_section_only_for_clips_with_a_source(self):
        w = self.window()
        self.select(w, w.vclip)
        self.assertFalse(w.props.sec_speed.isHidden())
        self.select(w, w.iclip)
        self.assertTrue(w.props.sec_speed.isHidden())
        w.add_title("Title")
        self.assertTrue(w.props.sec_speed.isHidden())

    def test_panel_controls_stay_in_sync(self):
        w = self.window()
        self.select(w, w.vclip)
        w.props._speed_chosen(2.0)                                   # a preset chip
        self.assertAlmostEqual(w.props._speed_spin.value(), 2.0)
        self.assertEqual(w.props._speed_slider.value(), 0 + round(math.log10(2.0) * 100))
        self.assertTrue(w.props._speed_chips[2.0].isChecked())
        self.assertFalse(w.props._speed_chips[1.0].isChecked())
        self.assertIn("6.00 s of the source in 3.00 s", w.props._speed_info.text())
        self.assertAlmostEqual(w.vclip.speed, 2.0)

    def test_speed_edit_is_one_undo_step_and_undo_restores_the_length(self):
        w = self.window()
        self.select(w, w.vclip)
        before = len(w.history)
        w.props._speed_chosen(2.0)
        w.props._speed_chosen(3.0)
        w.props._speed_chosen(4.0)                                   # a slider drag: three live changes...
        self.assertEqual(len(w.history), before)
        w._edit_timer.stop()
        w.commit(w._edit_label)                                      # ...committed once when it settles
        self.assertEqual(len(w.history), before + 1)
        self.assertEqual(w.labels[-1], "Change Speed")
        self.assertAlmostEqual(w.project.clip(w.vclip.id).duration, 1.5)
        w.undo()
        c = w.project.clip(w.vclip.id)
        self.assertEqual((c.speed, round(c.duration, 3)), (1.0, 6.0))
        w.redo()
        self.assertAlmostEqual(w.project.clip(w.vclip.id).speed, 4.0)

    def test_menu_speed_and_reverse_actions(self):
        w = self.window()
        self.select(w, w.vclip)
        w.a_speed[0.5].trigger()
        self.assertAlmostEqual(w.project.clip(w.vclip.id).duration, 12.0)
        self.assertEqual(w.labels[-1], "Change Speed")
        w.a_reverse.trigger()
        self.assertTrue(w.project.clip(w.vclip.id).reverse)
        self.assertEqual(w.labels[-1], "Reverse Clip")
        self.assertTrue(w.props._reverse_box.isChecked())
        w.a_reverse.trigger()
        self.assertFalse(w.project.clip(w.vclip.id).reverse)
        self.assertEqual(w.labels[-1], "Play Clip Forward")

    def test_photo_is_refused_with_a_message(self):
        w = self.window()
        self.select(w, w.iclip)
        before = len(w.history)
        w.a_reverse.trigger()
        w.a_speed[2.0].trigger()
        self.assertEqual(len(w.history), before)
        self.assertEqual((w.iclip.speed, w.iclip.reverse), (1.0, False))
        self.assertIn("Photos and titles", w.statusBar().currentMessage())

    def test_too_fast_is_clamped_and_reported(self):
        w = self.window()
        self.select(w, w.vclip)
        w.project.set_speed(w.vclip.id, 1.0)
        w.project.clip(w.vclip.id).duration = 0.5                    # a 0.5 s clip can't go beyond 5x
        w._apply_speed(8.0, live=False)
        self.assertLessEqual(w.project.clip(w.vclip.id).speed, 5.0 + 1e-6)
        self.assertIn("can't go faster", w.statusBar().currentMessage())
        self.assertLessEqual(w.props._speed_spin.value(), 5.0 + 1e-6)         # the controls show the real speed

    def test_locked_layer_refuses(self):
        w = self.window()
        self.select(w, w.vclip)
        w.project.tracks[w.vclip.track]["locked"] = True
        w.a_reverse.trigger()
        self.assertFalse(w.project.clip(w.vclip.id).reverse)

    def test_timeline_paints_badge_and_slip_follows_speed(self):
        w = self.window()
        w.project.set_speed(w.vclip.id, 2.0)
        w.project.set_reverse(w.vclip.id, True)
        w.timeline.canvas.set_project(w.project)
        w.timeline.resize(900, 300)
        pm = w.timeline.grab()                                       # exercises _draw_clip with the badge
        self.assertFalse(pm.isNull())

    def test_chunked_preview_stream_delivers_every_frame(self):
        if not HAVE_FFMPEG:
            self.skipTest("ffmpeg not installed")
        from venma_video.ui.preview import _VideoStream
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "s.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x36:r=10:d=3",
                        "-pix_fmt", "yuv420p", src], check=True)
        c = Clip("c1", "m1", 0, 0, 0.0, 3.0, reverse=True)
        w, h, fps = 32, 18, 10

        def cmd():
            for lo, hi, n in ft.reverse_chunks(c, 0.0, 1.0, fps):
                yield ft.layer_stream_cmd(src, lo, hi - lo, w, h, fps, c, frames=n)
        s = _VideoStream(cmd, w, h, fps, "k", 0.0, 3.0)
        s.start()
        got, deadline = 0, time.time() + 30
        while time.time() < deadline:
            with s._cv:
                while s._q:
                    s._q.popleft()
                    got += 1
                s._cv.notify_all()
            if s.drained():
                break
            time.sleep(0.01)
        s.wait(3000)
        with s._cv:
            got += len(s._q)
        self.assertEqual(got, 30)                                    # three 1-second pieces, no frame lost or doubled

    def test_export_worker_prepares_reversed_clips_then_renders(self):
        if not HAVE_FFMPEG:
            self.skipTest("ffmpeg not installed")
        from venma_video.workers import ExportWorker
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "s.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x36:r=10:d=3",
                        "-f", "lavfi", "-i", "sine=d=3", "-pix_fmt", "yuv420p", "-shortest", src], check=True)
        p = Project(64, 36, 10)
        p.add_media(MediaItem("m1", src, "video", 3.0, 64, 36, 10, True))
        c = p.add_clip("m1", 1, 0.0, duration=3.0)
        p.set_reverse(c.id, True)
        out = os.path.join(tmp, "o.mp4")
        rev_dir = os.path.join(tmp, "rev")
        os.makedirs(rev_dir)

        def prepare(progress, cancelled):
            rev = ft.render_reversed_media(p, rev_dir, progress=progress, cancelled=cancelled)
            return ft.build_export_command(p, out, quality="Low", reversed_media=rev)
        worker = ExportWorker(None, p.duration(), prepare=prepare)
        seen, status, fractions = [], [], []
        worker.finished_ok.connect(lambda ok, msg: seen.append((ok, msg)))
        worker.status.connect(status.append)
        worker.progress.connect(fractions.append)
        worker.run()                                                 # synchronously, on this thread
        self.assertEqual(seen, [(True, "Export finished.")])
        self.assertEqual(status, ["Preparing reversed clips…", "Rendering…"])
        self.assertEqual(fractions, sorted(fractions))               # the bar never jumps backwards
        self.assertAlmostEqual(probe_duration(out), 3.0, delta=0.2)

    def test_export_worker_cancel_during_preparation(self):
        from venma_video.workers import ExportWorker
        seen = []

        def prepare(progress, cancelled):
            progress(0.5)
            if cancelled():
                raise RuntimeError("Export cancelled.")
            return ["true"]
        worker = ExportWorker(None, 1.0, prepare=prepare)
        worker.finished_ok.connect(lambda ok, msg: seen.append((ok, msg)))
        worker.cancel()
        worker.run()
        self.assertEqual(seen, [(False, "Export cancelled.")])


if __name__ == "__main__":
    unittest.main()
