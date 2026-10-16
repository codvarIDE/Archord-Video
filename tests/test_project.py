import os
import tempfile
import unittest

from venma_video.core.project import MIN_CLIP, Clip, MediaItem, Project


def make_project():
    p = Project()
    p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
    p.add_media(MediaItem("m2", "/tmp/b.png", "image", 5.0, 800, 600))
    p.add_media(MediaItem("m3", "/tmp/c.wav", "audio", 8.0, has_audio=True))
    return p


class ProjectTests(unittest.TestCase):
    def test_add_clip_and_duration(self):
        p = make_project()
        c = p.add_clip("m1", 1, 2.0)
        self.assertEqual((c.start, c.duration), (2.0, 10.0))
        self.assertAlmostEqual(p.duration(), 12.0)

    def test_overlap_is_resolved(self):
        p = make_project()
        p.add_clip("m1", 1, 0.0)
        c2 = p.add_clip("m1", 1, 3.0)          # would overlap the first clip
        self.assertGreaterEqual(c2.start, 10.0 - 1e-9)

    def test_split(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0)
        right = p.split(c.id, 4.0)
        self.assertIsNotNone(right)
        self.assertAlmostEqual(c.duration, 4.0)
        self.assertAlmostEqual(right.start, 4.0)
        self.assertAlmostEqual(right.in_point, 4.0)
        self.assertAlmostEqual(right.duration, 6.0)

    def test_split_too_close_to_edge_is_rejected(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0)
        self.assertIsNone(p.split(c.id, MIN_CLIP / 2))

    def test_trim_clamps_to_media_bounds(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0, duration=4.0, in_point=2.0)
        p.trim_right(c.id, 100.0)
        self.assertAlmostEqual(c.end, 8.0)      # 10 s of media - 2 s in-point
        p.trim_left(c.id, -5.0)
        self.assertAlmostEqual(c.start, 0.0)

    def test_move_rejects_wrong_track_kind(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0)            # video on V1
        p.move(c.id, 2, 1.0)                    # track 2 is audio -> stays on V1
        self.assertEqual(c.track, 1)
        self.assertAlmostEqual(c.start, 1.0)

    def test_ripple_delete_closes_gap(self):
        p = make_project()
        a = p.add_clip("m1", 1, 0.0)
        b = p.add_clip("m1", 1, 10.0)
        p.delete([a.id], ripple=True)
        self.assertAlmostEqual(b.start, 0.0)

    def test_remove_media_removes_its_clips(self):
        p = make_project()
        p.add_clip("m1", 1, 0.0)
        p.remove_media("m1")
        self.assertEqual(p.clips, [])

    def test_top_video_clip_prefers_upper_track(self):
        p = make_project()
        low = p.add_clip("m1", 1, 0.0)
        high = p.add_clip("m2", 0, 0.0)
        self.assertIs(p.top_video_clip_at(1.0), high)
        self.assertIsNot(p.top_video_clip_at(1.0), low)

    def test_first_track_for(self):
        p = make_project()
        self.assertEqual(p.first_track_for("video"), 1)   # V1 is the lower video track
        self.assertEqual(p.first_track_for("audio"), 2)

    def test_snapshot_roundtrip_preserves_state_and_id_counter(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0)
        c.volume, c.opacity = 1.5, 0.5
        q = Project.from_snapshot(p.snapshot())
        self.assertEqual(q.to_dict(), p.to_dict())
        new = q.add_clip("m1", 1, 50.0)
        self.assertNotIn(new.id, {c.id})            # ids keep increasing after a reload

    def test_save_and_load(self):
        p = make_project()
        p.add_clip("m1", 1, 0.0)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.vvproj")
            p.save(path)
            self.assertEqual(Project.load(path).to_dict(), p.to_dict())

    def test_clip_dict_roundtrip(self):
        c = Clip("c1", "m1", 1, 1.0, 2.0, 3.0, volume=0.7)
        self.assertEqual(Clip.from_dict(c.to_dict()).to_dict(), c.to_dict())


if __name__ == "__main__":
    unittest.main()
