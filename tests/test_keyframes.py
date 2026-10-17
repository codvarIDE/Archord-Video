import os
import shutil
import subprocess
import tempfile
import unittest

from venma_video.core import ffmpeg_tools as ft
from venma_video.core.project import KF_SNAP, Clip, Keyframe, MediaItem, Project


def _clip(duration=4.0, **kfs):
    c = Clip("c1", "m1", 0, 0, 0, duration)
    for t, vals in kfs.items():
        c.set_keyframe(float(t.lstrip("t").replace("_", ".")), **vals)
    return c


class KeyframeModelTests(unittest.TestCase):
    def test_edit_without_keyframes_changes_the_clip(self):
        c = _clip()
        self.assertIsNone(c.edit_at(1.0, pos_x=0.2))
        self.assertAlmostEqual(c.pos_x, 0.2)
        self.assertEqual(c.keyframes, [])

    def test_edit_between_keyframes_creates_a_new_one(self):
        c = _clip(t0=dict(pos_x=0.2, pos_y=0.5, scale=1.0, rotation=0.0, opacity=1.0),
                  t4=dict(pos_x=0.8, pos_y=0.5, scale=1.0, rotation=0.0, opacity=1.0))
        kf = c.edit_at(2.0, pos_y=0.9)                      # drag the picture in the middle of the motion
        self.assertEqual([round(k.t, 2) for k in c.keyframes], [0.0, 2.0, 4.0])
        self.assertAlmostEqual(kf.values["pos_y"], 0.9)
        self.assertAlmostEqual(kf.values["pos_x"], 0.5)     # the rest of the look at that moment was recorded
        self.assertAlmostEqual(c.props_at(1.0)["pos_y"], 0.7)   # and the motion now bends through the new key

    def test_edit_on_a_keyframe_updates_it(self):
        c = _clip(t1=dict(pos_x=0.2), t3=dict(pos_x=0.8))
        c.edit_at(1.0 + KF_SNAP / 2, pos_x=0.4)
        self.assertEqual(len(c.keyframes), 2)
        self.assertAlmostEqual(c.keyframes[0].values["pos_x"], 0.4)

    def test_close_but_distinct_times_do_not_merge(self):
        c = _clip(t1=dict(pos_x=0.2))
        c.set_keyframe(1.1, pos_x=0.3)                      # 0.1 s apart: two keyframes (was merged before)
        self.assertEqual(len(c.keyframes), 2)

    def test_toggle(self):
        c = _clip()
        self.assertEqual(c.toggle_keyframe(1.0), "added")
        self.assertEqual(len(c.keyframes), 1)
        self.assertEqual(c.toggle_keyframe(1.0), "removed")
        self.assertEqual(c.keyframes, [])

    def test_prev_next(self):
        c = _clip(t1=dict(pos_x=0.1), t2=dict(pos_x=0.2), t3=dict(pos_x=0.3))
        self.assertIsNone(c.prev_keyframe(1.0))
        self.assertAlmostEqual(c.prev_keyframe(2.0).t, 1.0)
        self.assertAlmostEqual(c.next_keyframe(2.0).t, 3.0)
        self.assertIsNone(c.next_keyframe(3.0))
        self.assertAlmostEqual(c.next_keyframe(0.0).t, 1.0)

    def test_move_keyframe_keeps_order_and_stays_in_clip(self):
        c = _clip(t1=dict(pos_x=0.1), t2=dict(pos_x=0.2), t3=dict(pos_x=0.3))
        k1, k2, k3 = c.keyframes
        self.assertTrue(c.move_keyframe(k2, 2.4))
        self.assertAlmostEqual(k2.t, 2.4)
        c.move_keyframe(k2, 9.0)                            # cannot pass the next keyframe
        self.assertAlmostEqual(k2.t, 3.0 - KF_SNAP)
        c.move_keyframe(k1, -5.0)                           # cannot leave the clip
        self.assertAlmostEqual(k1.t, 0.0)
        c.move_keyframe(k3, 99.0)
        self.assertAlmostEqual(k3.t, 4.0)
        self.assertEqual(c.keyframes, [k1, k2, k3])

    def test_motion_follows_a_moved_keyframe(self):
        c = _clip(t0=dict(pos_x=0.0), t2=dict(pos_x=1.0))
        self.assertAlmostEqual(c.props_at(1.0)["pos_x"], 0.5)
        c.move_keyframe(c.keyframes[1], 1.0)
        self.assertAlmostEqual(c.props_at(0.5)["pos_x"], 0.5)
        self.assertAlmostEqual(c.props_at(3.0)["pos_x"], 1.0)

    def test_keyframes_survive_save_and_load(self):
        c = _clip(t1=dict(pos_x=0.1, scale=2.0))
        c2 = Clip.from_dict(c.to_dict())
        self.assertAlmostEqual(c2.keyframes[0].values["scale"], 2.0)


class KeyframeEditingTests(unittest.TestCase):
    def _project(self):
        p = Project()
        p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
        c = p.add_clip("m1", 1, 0.0)
        c.set_keyframe(0.0, pos_x=0.0, pos_y=0.5, scale=1.0, rotation=0.0, opacity=1.0)
        c.set_keyframe(10.0, pos_x=1.0, pos_y=0.5, scale=1.0, rotation=0.0, opacity=1.0)
        return p, c

    def test_split_puts_a_keyframe_on_the_cut_in_both_halves(self):
        p, c = self._project()
        right = p.split(c.id, 4.0)
        self.assertAlmostEqual(c.keyframes[-1].t, 4.0)
        self.assertAlmostEqual(c.keyframes[-1].values["pos_x"], 0.4)
        self.assertAlmostEqual(right.keyframes[0].t, 0.0)
        self.assertAlmostEqual(right.keyframes[0].values["pos_x"], 0.4)
        self.assertAlmostEqual(right.keyframes[-1].t, 6.0)

    def test_trim_left_keeps_the_motion_on_the_picture(self):
        p, c = self._project()
        before = c.props_at(5.0)["pos_x"]                   # timeline time 5 s
        p.trim_left(c.id, 2.0)
        self.assertAlmostEqual(c.props_at(5.0 - 2.0)["pos_x"], before)
        self.assertAlmostEqual(c.keyframes[0].t, 0.0)       # a keyframe holds the value at the new start
        self.assertAlmostEqual(c.keyframes[0].values["pos_x"], 0.2)

    def test_trim_right_drops_keyframes_past_the_end(self):
        p, c = self._project()
        p.trim_right(c.id, 5.0)
        self.assertAlmostEqual(c.keyframes[-1].t, 5.0)
        self.assertAlmostEqual(c.keyframes[-1].values["pos_x"], 0.5)
        self.assertTrue(all(k.t <= c.duration + 1e-9 for k in c.keyframes))


class ExpressionTests(unittest.TestCase):
    def test_held_before_first_and_after_last(self):
        c = _clip(t1=dict(pos_x=0.2), t3=dict(pos_x=0.6))
        e = ft._kf_expr(c, "pos_x")
        self.assertTrue(e.startswith("if(lte(t,1)"))
        self.assertIn("0.6", e)

    def test_single_value_is_a_constant(self):
        c = _clip(t1=dict(scale=1.5))
        self.assertEqual(ft._kf_expr(c, "scale"), "1.5")
        self.assertIsNone(ft._kf_expr(c, "opacity"))


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class RealRenderTests(unittest.TestCase):
    W, H, FPS = 160, 90, 10

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.img = os.path.join(cls.tmp, "red.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=120x120",
                        "-frames:v", "1", cls.img], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, **kf):
        p = Project(self.W, self.H, self.FPS)
        p.media["m1"] = MediaItem("m1", self.img, "image", 5, 120, 120)
        c = Clip("c1", "m1", 0, 0, 0, 2.0)
        for t, vals in kf.items():
            c.set_keyframe(float(t[1:].replace("_", ".")), **vals)
        p.clips.append(c)
        out = os.path.join(self.tmp, "o.mp4")
        r = subprocess.run(ft.build_export_command(p, out, quality="Low"), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out

    def pixel(self, video, t, x, y):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", video, "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        i = (y * self.W + x) * 3
        return raw[i], raw[i + 1], raw[i + 2]

    def test_position_keyframes_move_the_picture(self):
        out = self.render(t0=dict(pos_x=0.25, pos_y=0.5, scale=0.4), t1_9=dict(pos_x=0.75, pos_y=0.5, scale=0.4))
        self.assertGreater(self.pixel(out, 0.0, 40, 45)[0], 180)    # starts on the left...
        self.assertLess(self.pixel(out, 0.0, 120, 45)[0], 60)
        self.assertGreater(self.pixel(out, 1.9, 120, 45)[0], 180)   # ...ends on the right
        self.assertLess(self.pixel(out, 1.9, 40, 45)[0], 60)

    def test_scale_keyframes_zoom_the_picture(self):
        out = self.render(t0=dict(scale=0.25, pos_x=0.5, pos_y=0.5), t1_9=dict(scale=1.0, pos_x=0.5, pos_y=0.5))
        self.assertLess(self.pixel(out, 0.0, 110, 45)[0], 60)       # small at the start (22 px wide)
        self.assertGreater(self.pixel(out, 0.0, 85, 45)[0], 180)
        self.assertGreater(self.pixel(out, 1.9, 115, 45)[0], 180)   # big at the end (~85 px wide)

    def test_opacity_keyframes_fade_the_picture_and_hold_before_the_first(self):
        out = self.render(t0_5=dict(opacity=1.0), t1_5=dict(opacity=0.0))
        self.assertGreater(self.pixel(out, 0.0, 80, 45)[0], 180)    # held at 100 % before the first keyframe
        mid = self.pixel(out, 1.0, 80, 45)[0]
        self.assertTrue(60 < mid < 200, mid)                        # half way through the fade
        self.assertLess(self.pixel(out, 1.9, 80, 45)[0], 40)        # gone at the end

    def test_rotation_keyframes_render(self):
        self.render(t0=dict(rotation=0.0), t1_9=dict(rotation=90.0))


if __name__ == "__main__":
    unittest.main()
