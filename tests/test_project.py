import os
import tempfile
import unittest

from venma_video.core.project import (
    MIN_CLIP, TEXT_MEDIA_ID, TITLE_DEFAULTS, TITLE_PRESETS, Clip, MediaItem, Project,
)


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


class RippleTrimTests(unittest.TestCase):
    def setUp(self):
        self.p = make_project()
        self.a = self.p.add_clip("m1", 1, 0.0, duration=4.0, in_point=2.0)
        self.b = self.p.add_clip("m1", 1, 4.0, duration=3.0)
        self.other_track = self.p.add_clip("m3", 2, 5.0)        # audio track: must not move

    def test_shrinking_tail_pulls_later_clips_left(self):
        shift = self.p.ripple_trim(self.a.id, "right", 3.0)
        self.assertAlmostEqual(shift, -1.0)
        self.assertAlmostEqual(self.a.duration, 3.0)
        self.assertAlmostEqual(self.b.start, 3.0)
        self.assertAlmostEqual(self.other_track.start, 5.0)

    def test_extending_tail_pushes_later_clips_right(self):
        self.p.ripple_trim(self.a.id, "right", 6.0)
        self.assertAlmostEqual(self.a.duration, 6.0)
        self.assertAlmostEqual(self.b.start, 6.0)

    def test_tail_limited_by_media_length_not_by_neighbour(self):
        self.p.ripple_trim(self.a.id, "right", 100.0)
        self.assertAlmostEqual(self.a.end, 8.0)               # 10 s media - 2 s in-point
        self.assertAlmostEqual(self.b.start, 8.0)             # pushed, not blocked

    def test_head_trim_keeps_start_and_advances_in_point(self):
        shift = self.p.ripple_trim(self.a.id, "left", 1.0)
        self.assertAlmostEqual(shift, -1.0)
        self.assertAlmostEqual(self.a.start, 0.0)
        self.assertAlmostEqual(self.a.in_point, 3.0)
        self.assertAlmostEqual(self.a.duration, 3.0)
        self.assertAlmostEqual(self.b.start, 3.0)

    def test_head_extension_limited_by_in_point(self):
        self.p.ripple_trim(self.a.id, "left", -50.0)
        self.assertAlmostEqual(self.a.in_point, 0.0)
        self.assertAlmostEqual(self.a.duration, 6.0)
        self.assertAlmostEqual(self.b.start, 6.0)

    def test_cannot_shrink_below_min_clip(self):
        self.p.ripple_trim(self.a.id, "right", -5.0)
        self.assertAlmostEqual(self.a.duration, MIN_CLIP)

    def test_no_change_returns_zero(self):
        self.assertEqual(self.p.ripple_trim(self.a.id, "right", self.a.end), 0.0)

    def test_image_head_extension_is_unbounded_and_keeps_in_point(self):
        img = self.p.add_clip("m2", 0, 2.0)                     # 5 s image on V2
        self.p.ripple_trim(img.id, "left", 1.0)
        self.assertAlmostEqual(img.in_point, 0.0)
        self.assertAlmostEqual(img.duration, 6.0)

    def test_ripple_leaves_no_overlap(self):
        self.p.ripple_trim(self.a.id, "right", 1.0)
        clips = self.p.track_clips(1)
        for left, right in zip(clips, clips[1:]):
            self.assertLessEqual(left.end, right.start + 1e-9)


class SlipTests(unittest.TestCase):
    def setUp(self):
        self.p = make_project()
        self.c = self.p.add_clip("m1", 1, 3.0, duration=4.0, in_point=2.0)

    def test_slip_changes_only_in_point(self):
        self.assertTrue(self.p.slip(self.c.id, 5.0))
        self.assertAlmostEqual(self.c.in_point, 5.0)
        self.assertAlmostEqual(self.c.start, 3.0)
        self.assertAlmostEqual(self.c.duration, 4.0)

    def test_slip_clamps_to_source_range(self):
        self.p.slip(self.c.id, 99.0)
        self.assertAlmostEqual(self.c.in_point, 6.0)            # 10 s media - 4 s clip
        self.p.slip(self.c.id, -5.0)
        self.assertAlmostEqual(self.c.in_point, 0.0)

    def test_slip_to_same_value_is_a_noop(self):
        self.assertFalse(self.p.slip(self.c.id, 2.0))

    def test_images_cannot_slip(self):
        img = self.p.add_clip("m2", 0, 0.0)
        self.assertEqual(self.p.slip_limits(img.id), (0.0, 0.0))
        self.assertFalse(self.p.slip(img.id, 1.0))

    def test_slip_survives_snapshot(self):
        self.p.slip(self.c.id, 4.0)
        q = Project.from_snapshot(self.p.snapshot())
        self.assertAlmostEqual(q.clip(self.c.id).in_point, 4.0)


class TitleTests(unittest.TestCase):
    def setUp(self):
        self.p = make_project()

    def test_title_goes_on_top_video_track_with_defaults(self):
        c = self.p.add_title(2.0)
        self.assertEqual(c.track, 0)                              # V2
        self.assertEqual((c.start, c.duration), (2.0, 4.0))
        self.assertEqual(c.title, TITLE_DEFAULTS)
        self.assertIn(TEXT_MEDIA_ID, self.p.media)

    def test_style_overrides_and_presets(self):
        c = self.p.add_title(0.0, **TITLE_PRESETS["Lower third"])
        self.assertEqual(c.title["align"], "left")
        self.assertTrue(c.title["box"])
        self.assertEqual(c.title["text"], "Name\nRole or caption")

    def test_placeholder_media_is_hidden_from_library(self):
        self.p.add_title(0.0)
        self.assertEqual({m.id for m in self.p.library()}, {"m1", "m2", "m3"})
        self.assertEqual(len(self.p.media), 4)

    def test_titles_are_not_the_picture_but_overlay_it(self):
        video = self.p.add_clip("m1", 1, 0.0)                     # V1
        title = self.p.add_title(1.0)                             # V2, above the video
        self.assertIs(self.p.top_video_clip_at(2.0), video)       # picture is still the video
        self.assertEqual(self.p.titles_at(2.0), [title])
        self.assertEqual(self.p.titles_at(0.5), [])               # not yet visible
        self.assertEqual(self.p.titles_at(title.end), [])         # ends exclusive

    def test_title_alone_has_no_picture_clip(self):
        self.p.add_title(0.0)
        self.assertIsNone(self.p.top_video_clip_at(1.0))

    def test_titles_at_orders_bottom_to_top(self):
        low = self.p.add_title(0.0, track=1)                      # V1
        high = self.p.add_title(0.0, track=0)                     # V2
        self.assertEqual(self.p.titles_at(1.0), [low, high])

    def test_set_title_updates_only_titles(self):
        t = self.p.add_title(0.0)
        v = self.p.add_clip("m1", 1, 10.0)
        self.assertTrue(self.p.set_title(t.id, text="Hello", size=40))
        self.assertEqual((t.title["text"], t.title["size"]), ("Hello", 40))
        self.assertFalse(self.p.set_title(v.id, text="nope"))

    def test_title_survives_save_snapshot_and_split(self):
        t = self.p.add_title(0.0, text="Persist me", color="#FF0000")
        q = Project.from_snapshot(self.p.snapshot())
        self.assertEqual(q.clip(t.id).title["text"], "Persist me")
        self.assertEqual(q.to_dict(), self.p.to_dict())
        right = self.p.split(t.id, 2.0)
        self.assertEqual(right.title["color"], "#FF0000")
        right.title["text"] = "changed"                            # copies are independent
        self.assertEqual(t.title["text"], "Persist me")

    def test_media_clips_have_no_title_and_dont_serialise_one(self):
        c = self.p.add_clip("m1", 1, 0.0)
        self.assertIsNone(c.title)
        self.assertNotIn("title", c.to_dict())

    def test_title_can_be_moved_trimmed_and_rippled_freely(self):
        t = self.p.add_title(0.0)
        later = self.p.add_title(10.0)
        self.p.ripple_trim(t.id, "right", 9.0)                    # titles have no source length limit
        self.assertAlmostEqual(t.duration, 9.0)
        self.assertAlmostEqual(later.start, 15.0)
        self.assertEqual(self.p.slip_limits(t.id), (0.0, 0.0))    # but they can't slip

    def test_titles_stay_on_video_tracks(self):
        c = self.p.add_title(0.0, track=2)                        # A1 is an audio track
        self.assertEqual(self.p.tracks[c.track]["kind"], "video")

    def test_overlapping_titles_are_pushed_apart(self):
        a = self.p.add_title(0.0)
        b = self.p.add_title(1.0)
        self.assertGreaterEqual(b.start, a.end - 1e-9)

    def test_old_project_files_without_titles_still_load(self):
        d = make_project().to_dict()
        self.assertEqual(Project.from_dict(d).to_dict(), d)


if __name__ == "__main__":
    unittest.main()
