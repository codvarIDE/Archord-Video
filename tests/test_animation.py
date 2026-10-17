import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from venma_video.core import animation as A
from venma_video.core import ffmpeg_tools as ft
from venma_video.core.project import Clip, MediaItem, Project


def _project(duration=4.0, anim=None):
    p = Project(640, 360, 30)
    p.media["m1"] = MediaItem("m1", "a.png", "image", 5, 300, 300)
    c = Clip("c1", "m1", 0, 0, 0, duration, animation=anim)
    p.clips.append(c)
    return p, c


class PresetTests(unittest.TestCase):
    def test_every_preset_evaluates_and_stays_sane(self):
        slots = [{"in": n, "out": n} for n in A.IN_PRESETS] + [{"combo": n} for n in A.COMBO_PRESETS]
        for a in slots:
            for t in (0.0, 0.2, 0.5, 1.7, 3.6, 4.0):
                v = A.values_at(a, 4.0, t)
                with self.subTest(anim=a, t=t):
                    self.assertGreater(v["scale"], 0.0)
                    self.assertTrue(0.0 <= v["opacity"] <= 1.0)
                    self.assertTrue(all(abs(v[k]) < 1000 for k in v))

    def test_in_starts_away_and_ends_at_rest(self):
        a = {"in": "slide_left", "in_dur": 0.5}
        self.assertAlmostEqual(A.values_at(a, 4, 0.0)["dx"], 1.0)
        for t in (0.5, 2.0, 4.0):
            self.assertAlmostEqual(A.values_at(a, 4, t)["dx"], 0.0)

    def test_out_leaves_after_resting(self):
        a = {"out": "slide_left", "out_dur": 0.5}
        self.assertAlmostEqual(A.values_at(a, 4, 3.0)["dx"], 0.0)
        self.assertAlmostEqual(A.values_at(a, 4, 4.0)["dx"], -1.0)

    def test_fade_in_and_out_are_linear_opacity(self):
        a = {"in": "fade", "in_dur": 1.0, "out": "fade", "out_dur": 1.0}
        self.assertAlmostEqual(A.values_at(a, 4, 0.0)["opacity"], 0.0)
        self.assertAlmostEqual(A.values_at(a, 4, 0.5)["opacity"], 0.5)
        self.assertAlmostEqual(A.values_at(a, 4, 2.0)["opacity"], 1.0)
        self.assertAlmostEqual(A.values_at(a, 4, 3.5)["opacity"], 0.5)
        self.assertAlmostEqual(A.values_at(a, 4, 4.0)["opacity"], 0.0)

    def test_pop_overshoots_then_settles(self):
        a = {"in": "pop", "in_dur": 0.6}
        peak = max(A.values_at(a, 4, i * 0.01)["scale"] for i in range(61))
        self.assertGreater(peak, 1.02)
        self.assertAlmostEqual(A.values_at(a, 4, 0.6)["scale"], 1.0)

    def test_no_animation_is_neutral(self):
        self.assertEqual(A.values_at({}, 4, 1.0), A.NEUTRAL)
        self.assertIsNone(A.expressions({}, 4))

    def test_durations_shrink_to_fit_the_clip(self):
        d_in, d_out = A.durations({"in": "fade", "in_dur": 2, "out": "fade", "out_dur": 2}, 3.0)
        self.assertAlmostEqual(d_in + d_out, 3.0)
        self.assertEqual(A.durations({"combo": "pulse"}, 3.0), (0.0, 0.0))

    def test_set_slot_rules(self):
        a = A.set_slot({"in": "fade", "out": "fade"}, "combo", "pulse")
        self.assertEqual(a, {"combo": "pulse"})                       # combo replaces in / out
        a = A.set_slot(a, "in", "pop", 0.8)
        self.assertEqual(a, {"in": "pop", "in_dur": 0.8})             # ...and the other way round
        self.assertEqual(A.set_slot(a, "in", None), {})
        self.assertEqual(A.clean({"in": "nope", "combo": "nope"}), {})


class ClipTests(unittest.TestCase):
    def test_roundtrip_and_old_projects(self):
        p, c = _project(anim={"in": "pop", "in_dur": 0.7, "out": "fade"})
        d = Clip.from_dict(c.to_dict())
        self.assertEqual(d.animation, {"in": "pop", "in_dur": 0.7, "out": "fade"})
        plain = Clip("c2", "m1", 0, 0, 0, 1).to_dict()
        self.assertNotIn("animation", plain)                          # files without animations stay unchanged
        self.assertEqual(Clip.from_dict(plain).animation, {})

    def test_project_set_animation_and_lock(self):
        p, c = _project()
        self.assertTrue(p.set_animation("c1", "in", "fade", 0.4))
        self.assertFalse(p.set_animation("c1", "in", "fade", 0.4))    # no change
        self.assertFalse(p.set_animation("c1", "in", "bogus"))
        self.assertFalse(p.set_animation("c1", "weird", "fade"))
        p.set_track_flag(0, "locked", True)
        self.assertFalse(p.set_animation("c1", "out", "fade"))
        p.set_track_flag(0, "locked", False)
        self.assertTrue(p.clear_animation("c1"))
        self.assertEqual(c.animation, {})

    def test_split_gives_in_to_the_left_and_out_to_the_right(self):
        p, c = _project(anim={"in": "fade", "out": "slide_left"})
        right = p.split("c1", 2.0)
        self.assertEqual(c.animation, {"in": "fade"})
        self.assertEqual(right.animation, {"out": "slide_left"})
        p2, c2 = _project(anim={"combo": "pulse"})
        r2 = p2.split("c1", 2.0)
        self.assertEqual((c2.animation, r2.animation), ({"combo": "pulse"}, {"combo": "pulse"}))

    def test_undo_snapshot_keeps_animation(self):
        p, c = _project(anim={"combo": "shake"})
        self.assertEqual(Project.from_snapshot(p.snapshot()).clip("c1").animation, {"combo": "shake"})


class ExportGraphTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ft, "ffmpeg_path", return_value="ffmpeg")
        patcher.start()
        self.addCleanup(patcher.stop)

    def graph(self, anim):
        p, _ = _project(anim=anim)
        cmd = ft.build_export_command(p, "out.mp4")
        return cmd[cmd.index("-filter_complex") + 1]

    def test_unanimated_clip_is_unchanged(self):
        g = self.graph(None)
        self.assertNotIn("eval=frame", g)
        self.assertNotIn("fade=", g)

    def test_animation_forces_the_placed_path(self):
        p, c = _project(anim={"in": "fade"})
        self.assertTrue(ft.has_transform(c))
        self.assertFalse(ft.has_transform(_project()[1]))

    def test_zoom_and_fade_filters(self):
        g = self.graph({"in": "grow", "in_dur": 0.5, "out": "grow", "out_dur": 0.5})
        self.assertIn("eval=frame", g)
        self.assertIn("fade=t=in:st=0:d=0.5:alpha=1", g)
        self.assertIn("fade=t=out:st=3.5:d=0.5:alpha=1", g)
        self.assertLess(g.index("fade=t=in"), g.index("eval=frame"))     # zoom must come last in the chain

    def test_slide_moves_the_overlay_and_spin_rotates(self):
        self.assertIn("640*(", self.graph({"in": "slide_left"}))
        self.assertIn("rotate=", self.graph({"combo": "swing"}))


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class RealExportTests(unittest.TestCase):
    """Render every preset with the real FFmpeg: the expressions must be valid filter syntax."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.img = os.path.join(cls.tmp, "red.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=120x120",
                        "-frames:v", "1", cls.img], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_all_presets_render(self):
        combos = [{"in": n, "out": n} for n in A.IN_PRESETS] + [{"combo": n} for n in A.COMBO_PRESETS]
        for anim in combos:
            with self.subTest(anim=anim):
                p = Project(160, 90, 10)
                p.media["m1"] = MediaItem("m1", self.img, "image", 5, 120, 120)
                c = Clip("c1", "m1", 0, 0, 0, 1.0, animation=anim)
                p.clips.append(c)
                out = os.path.join(self.tmp, "o.mp4")
                r = subprocess.run(ft.build_export_command(p, out, quality="Low"), capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
