import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from venma_video.core import ffmpeg_tools as ft
from venma_video.core.project import (ASPECTS, EXPORT_PRESETS, MediaItem, Project, normalize_color)


def _project(w=1080, h=1920, media_wh=(1920, 1080)):
    p = Project(w, h, 30)
    p.add_media(MediaItem("m1", "clip.mp4", "video", 10.0, media_wh[0], media_wh[1], 30, True))
    p.add_clip("m1", 1, 0.0, duration=2.0, in_point=0.0)
    return p


class CanvasTests(unittest.TestCase):
    def test_set_canvas_keeps_short_side(self):
        p = Project(1920, 1080)
        p.set_canvas(9, 16)
        self.assertEqual((p.width, p.height), (1080, 1920))
        p.set_canvas(1, 1)
        self.assertEqual((p.width, p.height), (1080, 1080))
        p.set_canvas(16, 9)
        self.assertEqual((p.width, p.height), (1920, 1080))
        p.set_canvas(4, 5)
        self.assertEqual((p.width, p.height), (1080, 1350))

    def test_4k_switch_stays_even(self):
        p = Project(3840, 2160)
        p.set_canvas(9, 16)
        self.assertEqual((p.width, p.height), (2160, 3840))

    def test_background_round_trip(self):
        p = Project()
        p.set_background("blur", "#ABC")
        q = Project.from_snapshot(p.snapshot())
        self.assertEqual((q.bg_mode, q.bg_color), ("blur", "#aabbcc"))

    def test_old_projects_default_to_black(self):
        d = Project().to_dict()
        d.pop("bg_mode"), d.pop("bg_color")
        q = Project.from_dict(d)
        self.assertEqual((q.bg_mode, q.bg_color), ("color", "#000000"))

    def test_bad_colour_and_mode(self):
        self.assertEqual(normalize_color("nope"), "#000000")
        p = Project()
        p.set_background("sparkle", "zzz")
        self.assertEqual((p.bg_mode, p.bg_color), ("color", "#000000"))

    def test_presets_are_even_and_cover_social_sizes(self):
        sizes = {(w, h) for _, w, h in EXPORT_PRESETS if w}
        self.assertTrue({(1080, 1920), (1080, 1080), (1920, 1080), (3840, 2160)} <= sizes)
        self.assertEqual(len(ASPECTS), 4)


class BackdropRuleTests(unittest.TestCase):
    def test_only_in_blur_mode_and_when_shape_differs(self):
        p = _project()
        c = p.clips[0]
        self.assertFalse(ft.wants_backdrop(p, c))             # colour mode
        p.set_background("blur")
        self.assertTrue(ft.wants_backdrop(p, c))              # 16:9 footage on a 9:16 canvas
        p.set_canvas(16, 9)
        self.assertFalse(ft.wants_backdrop(p, c))             # already fills the frame

    def test_moved_clip_has_no_backdrop(self):
        p = _project()
        p.set_background("blur")
        p.clips[0].scale = 0.5
        self.assertFalse(ft.wants_backdrop(p, p.clips[0]))


class CommandTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ft, "ffmpeg_path", return_value="ffmpeg")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _graph(self, cmd):
        return cmd[cmd.index("-filter_complex") + 1]

    def test_colour_fill_is_the_base(self):
        p = _project()
        p.set_background("color", "#ff8800")
        g = self._graph(ft.build_export_command(p, "o.mp4"))
        self.assertIn("color=c=0xff8800:s=1080x1920", g)
        self.assertNotIn("boxblur", g)

    def test_blur_adds_a_backdrop(self):
        p = _project()
        p.set_background("blur")
        g = self._graph(ft.build_export_command(p, "o.mp4"))
        self.assertIn("boxblur", g)
        self.assertIn("split", g)

    def test_gpu_args_replace_x264(self):
        p = _project()
        cmd = ft.build_export_command(p, "o.mp4", quality="High", gpu="h264_nvenc")
        self.assertIn("h264_nvenc", cmd)
        self.assertNotIn("libx264", cmd)
        self.assertNotIn("-crf", cmd)
        self.assertEqual(cmd[cmd.index("-cq") + 1], "18")

    def test_gpu_ignored_for_webm(self):
        cmd = ft.build_export_command(_project(), "o.webm", fmt="WebM (VP9 + Opus)", gpu="h264_nvenc")
        self.assertIn("libvpx-vp9", cmd)
        self.assertNotIn("h264_nvenc", cmd)

    def test_cpu_default_unchanged(self):
        cmd = ft.build_export_command(_project(), "o.mp4")
        self.assertIn("libx264", cmd)
        self.assertIn("-crf", cmd)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class RealRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="venma_t_")
        cls.src = os.path.join(cls.dir, "src.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=2",
                        "-f", "lavfi", "-i", "sine=d=2", "-shortest", "-pix_fmt", "yuv420p", cls.src], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _render(self, mode, size=(1080, 1920)):
        p = Project(size[0], size[1], 30)
        p.add_media(MediaItem("m1", self.src, "video", 2.0, 1920, 1080, 30, True))
        p.add_clip("m1", 1, 0.0, duration=1.0, in_point=0.0)
        p.set_background(mode, "#2040ff")
        out = os.path.join(self.dir, f"o_{mode}_{size[0]}.mp4")
        r = subprocess.run(ft.build_export_command(p, out), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out

    def _frame(self, path, x, y):
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"format=rgb24,crop=1:1:{x}:{y}", "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        return tuple(r.stdout[:3])

    def test_colour_fill_shows_in_the_bars(self):
        px = self._frame(self._render("color"), 540, 100)       # top bar of a 16:9 clip on a 9:16 canvas
        self.assertLess(abs(px[0] - 0x20), 12)
        self.assertGreater(px[2], 0xe0)

    def test_blur_bars_are_not_flat_black_or_the_fill_colour(self):
        out = self._render("blur")
        a, b = self._frame(out, 100, 100), self._frame(out, 900, 1800)
        self.assertNotEqual(a, (0, 0, 0))
        self.assertNotEqual(a, b)

    def test_size_is_exact(self):
        out = self._render("blur")
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", out], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "1080,1920")

    def test_composite_still_with_blur(self):
        p = Project(1080, 1920, 30)
        p.add_media(MediaItem("m1", self.src, "video", 2.0, 1920, 1080, 30, True))
        p.add_clip("m1", 1, 0.0, duration=1.0, in_point=0.0)
        p.set_background("blur")
        r = subprocess.run(ft.composite_still_cmd(p, 0.2, 270, 480), capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout), 270 * 480 * 3)


if __name__ == "__main__":
    unittest.main()
