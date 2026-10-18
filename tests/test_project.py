import os
import tempfile
import unittest

from archord_video.core.project import (
    MIN_CLIP, NEW_BOTTOM, NEW_TOP, TEXT_MEDIA_ID, TITLE_DEFAULTS, TITLE_PRESETS, TYPED_TRACKS, Clip, MediaItem,
    Project,
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
            path = os.path.join(d, "x.avproj")
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

    def test_overlapping_title_gets_its_own_layer_and_keeps_its_time(self):
        a = self.p.add_title(0.0)
        b = self.p.add_title(1.0)
        self.assertEqual(b.start, 1.0)                            # not pushed to the end of `a`
        self.assertNotEqual(a.track, b.track)                     # stacked on a new layer instead
        self.assertEqual(self.p.tracks[b.track]["kind"], "video")

    def test_title_over_a_video_on_the_top_layer_does_not_join_it(self):
        v = self.p.add_clip("m1", 0, 0.0)                         # a video already sitting on V2
        t = self.p.add_title(1.0)
        self.assertEqual(t.start, 1.0)
        self.assertNotEqual(t.track, self.p.clip(v.id).track)
        self.assertEqual(self.p.tracks[t.track]["kind"], "video")

    def test_new_layers_are_added_last_in_order(self):
        a = self.p.add_title(0.0)                                 # V2 is free -> uses it
        b = self.p.add_title(1.0)                                 # busy -> new layer
        c = self.p.add_title(2.0)                                 # busy again -> another new layer
        self.assertLess(a.track, b.track)
        self.assertLess(b.track, c.track)                         # last added = last (lowest) layer
        self.assertEqual([t["kind"] for t in self.p.tracks][-1], "audio")   # audio group stays below video

    def test_new_layer_goes_right_next_to_the_busy_one(self):
        v = self.p.add_clip("m1", 0, 0.0)                         # busy clip on V2 (index 0); V1 (index 1) is empty
        t = self.p.add_title(1.0)
        self.assertEqual(t.track, 1)                              # directly below V2, not at the end
        self.assertEqual(self.p.clip(v.id).track, 0)
        self.assertEqual([x["kind"] for x in self.p.tracks], ["video", "video", "video", "audio", "audio"])

    def test_title_after_existing_ones_reuses_the_free_layer(self):
        a = self.p.add_title(0.0)                                 # 0..4
        b = self.p.add_title(10.0)                                # free there -> same layer
        self.assertEqual(a.track, b.track)

    def test_audio_at_playhead_stacks_on_a_new_track_instead_of_joining(self):
        a1 = self.p.place_clip("m3", None, 0.0)
        a2 = self.p.place_clip("m3", None, 1.0)
        self.assertEqual(a2.start, 1.0)
        self.assertNotEqual(a1.track, a2.track)
        self.assertEqual(self.p.tracks[a2.track]["kind"], "audio")

    def test_old_project_files_without_titles_still_load(self):
        d = make_project().to_dict()
        self.assertEqual(Project.from_dict(d).to_dict(), d)


class LayerControlTests(unittest.TestCase):
    def _three_layers(self):
        p = Project(typed=True)
        for i in (1, 2, 3):
            p.add_media(MediaItem(f"m{i}", f"/tmp/{i}.mp4", "video", 10.0, 1920, 1080, 30, True))
        for i in (1, 2, 3):
            p.place_clip(f"m{i}", start=0.0)             # c1 bottom ... c3 top
        return p

    def test_reorder_track_moves_clips_with_their_layer(self):
        p = self._three_layers()
        self.assertEqual([(c.id, c.track) for c in p.clips], [("c1", 2), ("c2", 1), ("c3", 0)])
        self.assertTrue(p.reorder_track(0, 2))           # top layer dragged to the bottom
        self.assertEqual([(c.id, c.track) for c in p.clips], [("c1", 1), ("c2", 0), ("c3", 2)])
        self.assertFalse(p.reorder_track(0, len(p.tracks) - 1))      # can't mix with the audio track

    def test_hidden_layer_is_not_visible_and_muted_flag_round_trips(self):
        p = self._three_layers()
        p.set_track_flag(0, "hidden", True)
        p.set_track_flag(1, "muted", True)
        self.assertEqual([c.id for c in p.visual_clips_at(1.0)], ["c1", "c2"])      # bottom layer first
        q = Project.from_snapshot(p.snapshot())
        self.assertTrue(q.track_hidden(0))
        self.assertTrue(q.track_muted(1))
        self.assertFalse(q.track_locked(2))

    def test_locked_layer_refuses_edits(self):
        p = self._three_layers()
        p.set_track_flag(1, "locked", True)
        c = p.clip("c2")
        p.move("c2", 0, 5.0)
        p.trim_right("c2", 3.0)
        p.delete(["c2"])
        self.assertIsNone(p.split("c2", 2.0))
        self.assertEqual((c.track, c.start, c.duration), (1, 0.0, 10.0))
        self.assertIn(c, p.clips)
        p.set_track_flag(1, "locked", False)
        self.assertIsNotNone(p.split("c2", 2.0))


if __name__ == "__main__":
    unittest.main()


class LayerTests(unittest.TestCase):
    def test_insert_track_shifts_clips_and_renames(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0)                     # V1
        i = p.add_video_layer(above=True)
        self.assertEqual(i, 0)
        self.assertEqual(c.track, 2)                     # still on V1, which moved down a row
        self.assertEqual([t["name"] for t in p.tracks], ["V3", "V2", "V1", "A1", "A2"])

    def test_unlimited_layers(self):
        p = make_project()
        for _ in range(60):
            p.add_video_layer()
        self.assertEqual(p.video_track_count(), 62)
        self.assertEqual(p.tracks[0]["name"], "V62")
        self.assertEqual(p.tracks[-1]["kind"], "audio")

    def test_place_clip_stacks_overlapping_media_on_new_layers(self):
        p = make_project()
        ids = [p.place_clip("m1", None, 0.0).id for _ in range(6)]   # all want 0 s -> must stack
        tracks = {p.clip(i).track for i in ids}
        self.assertEqual(len(tracks), 6)
        for i in ids:
            self.assertEqual(p.clip(i).start, 0.0)
        self.assertTrue(all(p.tracks[t]["kind"] == "video" for t in tracks))

    def test_place_clip_prefers_chosen_track_when_free(self):
        p = make_project()
        c = p.place_clip("m1", 0, 3.0)                   # V2 is free
        self.assertEqual((c.track, c.start), (0, 3.0))

    def test_place_audio_creates_new_audio_track(self):
        p = make_project()
        for _ in range(3):
            p.place_clip("m3", None, 0.0)
        self.assertEqual(sum(1 for t in p.tracks if t["kind"] == "audio"), 3)

    def test_new_lane_creates_layer(self):
        p = make_project()
        c = p.place_clip("m2", NEW_TOP, 1.0)
        self.assertEqual(c.track, 0)
        self.assertEqual(p.tracks[0]["name"], "V3")
        a = p.place_clip("m3", NEW_BOTTOM, 0.0)
        self.assertEqual(p.tracks[a.track]["kind"], "audio")
        self.assertEqual(a.track, len(p.tracks) - 1)

    def test_move_clip_to_new_top_lane(self):
        p = make_project()
        c = p.add_clip("m1", 1, 0.0)
        p.move(c.id, NEW_TOP, 2.0)
        self.assertEqual(c.track, 0)
        self.assertAlmostEqual(c.start, 2.0)
        self.assertEqual(p.video_track_count(), 3)
        d = p.add_clip("m3", 3, 0.0)                     # audio clip can't use the video lane
        p.move(d.id, NEW_TOP, 1.0)
        self.assertEqual(p.tracks[d.track]["kind"], "audio")

    def test_remove_track_drops_its_clips_and_keeps_last_of_kind(self):
        p = make_project()
        keep = p.add_clip("m1", 0, 0.0)
        gone = p.add_clip("m1", 1, 0.0)
        self.assertTrue(p.remove_track(1))
        self.assertIsNone(p.clip(gone.id))
        self.assertEqual(keep.track, 0)
        self.assertFalse(p.remove_track(0))              # last video track
        self.assertTrue(p.remove_track(1))               # A1 (A2 remains)
        self.assertFalse(p.remove_track(1))              # last audio track

    def test_move_track_swaps_stacking_order(self):
        p = make_project()
        top, bottom = p.add_clip("m1", 0, 0.0), p.add_clip("m2", 1, 0.0)
        self.assertTrue(p.move_track(0, 1))
        self.assertEqual((top.track, bottom.track), (1, 0))
        self.assertFalse(p.move_track(1, 1))             # next row is audio
        self.assertEqual(p.video_clips_at(1.0)[-1].id, bottom.id)   # bottom clip is now the top layer

    def test_remove_empty_tracks(self):
        p = make_project()
        p.add_clip("m1", 1, 0.0)
        for _ in range(5):
            p.add_video_layer()
        self.assertEqual(p.remove_empty_tracks(), 7)     # 5 new + V2 + one of the two audio tracks (V1 has a clip)


class TypedLayerTests(unittest.TestCase):
    """CapCut-style layers: picture layers stack in the order they were added (last added = in front)."""

    def setUp(self):
        self.p = make_project()
        self.p.typed = True
        self.p.tracks = [dict(t) for t in TYPED_TRACKS]

    def kinds(self):
        return [t["kind"] for t in self.p.tracks]

    def names(self):
        return [t["name"] for t in self.p.tracks]

    def test_new_project_starts_with_video_and_audio_only(self):
        q = Project(typed=True)
        self.assertEqual([t["name"] for t in q.tracks], ["Video", "Audio"])

    def test_last_added_layer_is_on_top_and_numbered_last(self):
        v = self.p.place_clip("m1", None, 0.0)                # first picture: the base layer
        img = self.p.place_clip("m2", None, 0.0)              # overlaps -> new layer ABOVE it
        t = self.p.add_title(0.0)                             # overlaps both -> another layer above
        self.assertLess(t.track, img.track)
        self.assertLess(img.track, v.track)
        self.assertEqual(self.names(), ["Layer 3", "Layer 2", "Layer 1", "Audio"])
        self.assertEqual([c.id for c in self.p.visual_clips_at(1.0)], [v.id, img.id, t.id])   # paint order

    def test_audio_stays_below_every_picture_layer(self):
        self.p.place_clip("m3", None, 0.0)
        for _ in range(3):
            self.p.place_clip("m1", None, 0.0)
        self.assertEqual(self.kinds(), ["video", "video", "video", "audio"])

    def test_photo_video_and_text_can_share_a_layer_when_not_overlapping(self):
        v = self.p.place_clip("m1", None, 0.0)                # 0..10
        img = self.p.place_clip("m2", None, 12.0)             # free on the base layer
        self.assertEqual(img.track, v.track)
        self.assertEqual(self.kinds().count("video"), 1)

    def test_clip_is_never_hidden_behind_an_earlier_overlapping_layer(self):
        self.p.place_clip("m1", None, 0.0)                    # base layer 0..10
        top = self.p.place_clip("m1", None, 5.0)              # overlay layer 5..15
        late = self.p.place_clip("m2", None, 12.0)            # base layer is free at 12, but `top` covers it
        self.assertLessEqual(late.track, top.track)           # so it must be on or above `top`
        self.assertNotEqual(late.track, top.track)            # `top` is busy at 12..17 -> new layer above it

    def test_title_joins_the_top_layer_only_when_free(self):
        v = self.p.place_clip("m1", None, 0.0)                # 0..10 on the only layer
        t1 = self.p.add_title(0.0)                            # busy -> new layer on top
        t2 = self.p.add_title(2.0)                            # top layer busy at 2 -> new layer above that
        t3 = self.p.add_title(20.0)                           # nothing there -> joins the top layer
        self.assertLess(t1.track, v.track)
        self.assertLess(t2.track, t1.track)
        self.assertEqual(t3.track, t2.track)

    def test_dropping_on_a_busy_layer_inserts_a_new_one_just_above(self):
        v = self.p.place_clip("m1", None, 0.0)
        img = self.p.place_clip("m2", v.track, 2.0)           # dropped on the busy video layer
        self.assertEqual(img.track, 0)
        self.assertEqual(self.p.clip(v.id).track, 1)

    def test_new_layer_lane_creates_a_layer_on_top(self):
        self.p.place_clip("m1", None, 0.0)
        c = self.p.place_clip("m2", NEW_TOP, 20.0)            # even though the base layer is free
        self.assertEqual(c.track, 0)
        aud = self.p.place_clip("m3", NEW_TOP, 0.0)           # audio ignores the top lane, goes to its own track
        self.assertEqual(self.p.tracks[aud.track]["kind"], "audio")

    def test_pictures_can_move_between_picture_layers_but_not_to_audio(self):
        v = self.p.place_clip("m1", None, 0.0)
        img = self.p.place_clip("m2", None, 0.0)              # on a layer above
        self.p.move(img.id, v.track, 11.0)                    # photo onto the video layer (free at 11)
        self.assertEqual(img.track, v.track)
        a = self.p.place_clip("m3", None, 0.0)
        self.p.move(v.id, a.track, 1.0)
        self.assertEqual(self.p.tracks[v.track]["kind"], "video")

    def test_picture_layers_can_be_reordered_but_not_below_audio(self):
        self.p.place_clip("m1", None, 0.0)
        self.p.place_clip("m2", None, 0.0)
        self.assertTrue(self.p.move_track(0, 1))              # swap the two picture layers
        self.assertFalse(self.p.move_track(1, 1))             # can't go beneath the audio track
        self.assertEqual(self.kinds(), ["video", "video", "audio"])

    def test_typed_flag_round_trips_and_old_files_stay_legacy(self):
        self.p.place_clip("m2", None, 0.0)
        q = Project.from_snapshot(self.p.snapshot())
        self.assertTrue(q.typed)
        self.assertEqual([t["kind"] for t in q.tracks], self.kinds())
        d = Project().to_dict()
        d.pop("typed")
        self.assertFalse(Project.from_dict(d).typed)


class TestPictureTransform(unittest.TestCase):
    def test_fit_box_and_export_filters(self):
        from archord_video.core import ffmpeg_tools as ft
        p = Project(1920, 1080, 30)
        p.media["m1"] = MediaItem("m1", "a.png", "image", 5, 1000, 1000)
        c = Clip("c1", "m1", 0, 0, 0, 2)
        p.clips.append(c)
        cx, cy, w, h = p.fit_box(c)
        self.assertAlmostEqual(h, 1.0)
        self.assertAlmostEqual(w * 1920, 1080)
        self.assertFalse(ft.has_transform(c))
        c.scale, c.pos_x = 0.5, 0.75
        self.assertTrue(ft.has_transform(c))
        self.assertAlmostEqual(p.fit_box(c)[3], 0.5)
        cmd = ft.build_export_command(p, "out.mp4")
        self.assertIn("overlay=x=", " ".join(cmd))
        d = Clip.from_dict(c.to_dict())                 # survives save / undo
        self.assertEqual((d.scale, d.pos_x, d.pos_y), (0.5, 0.75, 0.5))
