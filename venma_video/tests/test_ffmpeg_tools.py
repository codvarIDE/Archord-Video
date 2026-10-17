import unittest
from unittest import mock

from venma_video.core import ffmpeg_tools as ft
from venma_video.core.project import MediaItem, Project


class HelperTests(unittest.TestCase):
    def test_guess_kind(self):
        self.assertEqual(ft.guess_kind("a.PNG"), "image")
        self.assertEqual(ft.guess_kind("a.wav"), "audio")
        self.assertEqual(ft.guess_kind("a.mp4"), "video")

    def test_rate(self):
        self.assertAlmostEqual(ft._rate("30000/1001"), 29.97, places=2)
        self.assertEqual(ft._rate("0/0"), 0.0)
        self.assertEqual(ft._rate("garbage"), 0.0)

    def test_preview_size_keeps_aspect_and_even_dims(self):
        w, h = ft.preview_size(3840, 2160)
        self.assertLessEqual(max(w, h), ft.PREVIEW_LIMIT)
        self.assertEqual((w % 2, h % 2), (0, 0))
        self.assertEqual(ft.preview_size(640, 360), (640, 360))   # never upscales


class ExportCommandTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ft, "ffmpeg_path", return_value="ffmpeg")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _project(self):
        p = Project(1280, 720, 30)
        p.add_media(MediaItem("m1", "clip.mp4", "video", 10.0, 1920, 1080, 30, True))
        p.add_clip("m1", 1, 0.0, duration=4.0, in_point=1.0)
        return p

    def test_empty_timeline_raises(self):
        with self.assertRaises(ValueError):
            ft.build_export_command(Project(), "out.mp4")

    def test_basic_command(self):
        cmd = ft.build_export_command(self._project(), "out.mp4", quality="High")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertEqual(cmd[-1], "out.mp4")
        self.assertIn("-filter_complex", cmd)
        self.assertIn("libx264", cmd)
        self.assertEqual(cmd[cmd.index("-crf") + 1], str(ft.QUALITY["High"]))

    def test_webm_uses_vp9(self):
        cmd = ft.build_export_command(self._project(), "out.webm", fmt="WebM (VP9 + Opus)")
        self.assertIn("libvpx-vp9", cmd)
        self.assertIn("libopus", cmd)


class TitleExportTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ft, "ffmpeg_path", return_value="ffmpeg")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = Project(1280, 720, 30)
        self.p.add_media(MediaItem("m1", "clip.mp4", "video", 10.0, 1920, 1080, 30, True))
        self.p.add_clip("m1", 1, 0.0, duration=6.0)
        self.title = self.p.add_title(1.0, duration=3.0)

    def test_title_png_is_looped_in_as_an_input(self):
        cmd = ft.build_export_command(self.p, "out.mp4", text_images={self.title.id: "/tmp/title.png"})
        i = cmd.index("/tmp/title.png")
        self.assertEqual(cmd[i - 1], "-i")
        self.assertIn("-loop", cmd[:i])
        self.assertNotIn("drawtext", " ".join(cmd))               # no dependency on ffmpeg fonts

    def test_title_is_overlaid_for_its_time_range_on_top_of_video(self):
        cmd = ft.build_export_command(self.p, "out.mp4", text_images={self.title.id: "/tmp/title.png"})
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertEqual(graph.count("overlay="), 2)              # video, then title on top
        self.assertIn("between(t,1,4)", graph)
        self.assertLess(graph.index("[v0]"), graph.index("[v1]"))  # lower track is input 0

    def test_title_opacity_is_applied(self):
        self.title.opacity = 0.5
        cmd = ft.build_export_command(self.p, "out.mp4", text_images={self.title.id: "/tmp/title.png"})
        self.assertIn("colorchannelmixer=aa=0.5", cmd[cmd.index("-filter_complex") + 1])

    def test_missing_rendered_title_is_an_error(self):
        with self.assertRaises(ValueError):
            ft.build_export_command(self.p, "out.mp4")

    def test_title_only_project_exports_video_without_audio(self):
        p = Project(1280, 720, 30)
        t = p.add_title(0.0, duration=2.0)
        cmd = ft.build_export_command(p, "out.mp4", text_images={t.id: "/tmp/t.png"})
        self.assertNotIn("[aout]", " ".join(cmd))


class FindBinaryTests(unittest.TestCase):
    def test_env_var_wins(self):
        with mock.patch.dict("os.environ", {"VENMA_FFMPEG": __file__}):
            self.assertEqual(ft._find("ffmpeg"), __file__)


if __name__ == "__main__":
    unittest.main()
