"""Timeline data model for Venma Video (pure Python, no Qt).

Time is in seconds. A clip occupies [start, start + duration) on a track and plays the source
from ``in_point``. Tracks are listed top-to-bottom as shown in the timeline; for video tracks
the one nearer the top of the list is composited on top.
"""
import copy
import itertools
import json
import os

MIN_CLIP = 0.1
IMAGE_DEFAULT_DURATION = 5.0
PROJECT_VERSION = 1

# Titles are ordinary clips on a video track whose picture is drawn from text instead of read from a
# file. They point at one shared placeholder media item (kind "text") that never shows up in the media bin.
TEXT_KIND = "text"
TEXT_MEDIA_ID = "mtext"
STILL_KINDS = ("image", TEXT_KIND)       # single-frame / generated kinds: no source range, any length
TEXT_DEFAULT_DURATION = 4.0

# Text styling lives on the clip. Sizes are in pixels for a 1080-pixel-high frame and scale with the
# frame; pos_x / pos_y place the centre of the text box as a fraction of the frame (0..1).
TITLE_DEFAULTS = dict(text="Title", font="", size=96, bold=True, italic=False, color="#FFFFFF",
                      align="center", pos_x=0.5, pos_y=0.5, stroke=True,
                      box=False, box_color="#000000", box_opacity=0.55)
TITLE_PRESETS = {
    "Title": dict(text="Your Title", size=128, pos_y=0.45),
    "Lower third": dict(text="Name\nRole or caption", size=60, bold=False, align="left", pos_x=0.3,
                        pos_y=0.83, stroke=False, box=True),
    "Subtitle": dict(text="Subtitle text", size=56, bold=False, pos_y=0.90),
}

# Special "track" values meaning "make a brand-new layer for this clip": a video layer on top of all
# others, or an audio track below all others. Used by drag & drop on the timeline's "new layer" lanes.
NEW_TOP = -10
NEW_BOTTOM = -11

DEFAULT_TRACKS = [
    {"name": "V2", "kind": "video"},
    {"name": "V1", "kind": "video"},
    {"name": "A1", "kind": "audio"},
    {"name": "A2", "kind": "audio"},
]

PRESETS = [
    ("1080p  ·  1920 × 1080", 1920, 1080),
    ("720p  ·  1280 × 720", 1280, 720),
    ("4K  ·  3840 × 2160", 3840, 2160),
    ("Vertical 9:16  ·  1080 × 1920", 1080, 1920),
    ("Square 1:1  ·  1080 × 1080", 1080, 1080),
]

# clip colour/effect defaults
CLIP_DEFAULTS = dict(volume=1.0, opacity=1.0, brightness=0.0, contrast=1.0, saturation=1.0)


class MediaItem:
    def __init__(self, id, path, kind, duration, width=0, height=0, fps=0.0, has_audio=False):
        self.id = id
        self.path = path
        self.kind = kind                  # "video" | "audio" | "image"
        self.duration = float(duration)   # seconds (images: default clip length)
        self.width, self.height, self.fps = int(width), int(height), float(fps)
        self.has_audio = bool(has_audio)

    @property
    def name(self):
        return os.path.basename(self.path)

    def to_dict(self):
        return dict(id=self.id, path=self.path, kind=self.kind, duration=self.duration,
                    width=self.width, height=self.height, fps=self.fps, has_audio=self.has_audio)

    @staticmethod
    def from_dict(d):
        return MediaItem(**d)


class Clip:
    def __init__(self, id, media_id, track, start, in_point, duration, **props):
        self.id = id
        self.media_id = media_id
        self.track = int(track)
        self.start = float(start)
        self.in_point = float(in_point)
        self.duration = float(duration)
        for k, v in CLIP_DEFAULTS.items():
            setattr(self, k, props.get(k, v))
        t = props.get("title")                        # None for media clips, a style dict for titles
        self.title = None if t is None else dict(TITLE_DEFAULTS, **t)

    @property
    def end(self):
        return self.start + self.duration

    def to_dict(self):
        d = dict(id=self.id, media_id=self.media_id, track=self.track, start=self.start,
                 in_point=self.in_point, duration=self.duration)
        d.update({k: getattr(self, k) for k in CLIP_DEFAULTS})
        if self.title is not None:
            d["title"] = dict(self.title)
        return d

    @staticmethod
    def from_dict(d):
        d = dict(d)
        return Clip(d.pop("id"), d.pop("media_id"), d.pop("track"), d.pop("start"),
                    d.pop("in_point"), d.pop("duration"), **d)


class Project:
    def __init__(self, width=1920, height=1080, fps=30):
        self.width, self.height, self.fps = int(width), int(height), int(fps)
        self.tracks = copy.deepcopy(DEFAULT_TRACKS)
        self.media = {}          # id -> MediaItem
        self.clips = []          # list[Clip]
        self._ids = itertools.count(1)

    # ── ids ──
    def _new_id(self, prefix):
        return f"{prefix}{next(self._ids)}"

    # ── queries ──
    def duration(self):
        return max((c.end for c in self.clips), default=0.0)

    def clip(self, clip_id):
        return next((c for c in self.clips if c.id == clip_id), None)

    def track_clips(self, track, ignore=None):
        return sorted((c for c in self.clips if c.track == track and c.id != ignore), key=lambda c: c.start)

    def clip_at(self, track, t):
        for c in self.clips:
            if c.track == track and c.start <= t < c.end:
                return c
        return None

    def top_video_clip_at(self, t):
        """Topmost visible video/image clip at time t (tracks listed first are on top)."""
        for i, tr in enumerate(self.tracks):
            if tr["kind"] == "video":
                c = self.clip_at(i, t)
                if c and c.title is None:             # titles are overlays, not the picture
                    return c
        return None

    def titles_at(self, t):
        """Title clips visible at t, bottom-most first (so they can be painted in order)."""
        found = [c for c in self.clips if c.title is not None and c.start <= t < c.end]
        return sorted(found, key=lambda c: (-c.track, c.start))

    def video_clips_at(self, t):
        """Every visible picture clip (video / image, not titles) at t, bottom layer first."""
        found = [c for c in self.clips if c.title is None and c.start <= t < c.end
                 and 0 <= c.track < len(self.tracks) and self.tracks[c.track]["kind"] == "video"
                 and self.media[c.media_id].kind in ("video", "image")]
        return sorted(found, key=lambda c: -c.track)

    def library(self):
        """Media the user imported (excludes the hidden placeholder that titles use)."""
        return [m for m in self.media.values() if m.kind != TEXT_KIND]

    def audio_clip_at(self, t):
        """Clip that supplies preview audio at t: first audio-track clip, else the top video clip."""
        for i, tr in enumerate(self.tracks):
            if tr["kind"] == "audio":
                c = self.clip_at(i, t)
                if c:
                    return c
        return None

    def track_accepts(self, track, media_kind):
        if not (0 <= track < len(self.tracks)):
            return False
        kind = self.tracks[track]["kind"]
        return (kind == "audio") == (media_kind == "audio")

    def first_track_for(self, media_kind):
        """Lowest video track (V1) for video/images, first audio track for audio."""
        want = "audio" if media_kind == "audio" else "video"
        idx = [i for i, t in enumerate(self.tracks) if t["kind"] == want]
        if not idx:
            return 0
        if media_kind == TEXT_KIND:
            return idx[0]                             # titles sit on the top video track (V2)
        return idx[-1] if want == "video" else idx[0]


    # ── layers / tracks (unlimited) ──
    def video_track_count(self):
        return sum(1 for t in self.tracks if t["kind"] == "video")

    def renumber_tracks(self):
        """Name tracks V<n>..V1 (top to bottom) and A1..A<n>, so names always match the stacking order."""
        nv, na = self.video_track_count(), 0
        v = nv
        for t in self.tracks:
            if t["kind"] == "video":
                t["name"] = f"V{v}"
                v -= 1
            else:
                na += 1
                t["name"] = f"A{na}"

    def insert_track(self, index, kind):
        """Insert a new empty track of ``kind`` at ``index`` (clamped into that kind's group).

        Clips on tracks at or below ``index`` are shifted down one row so they stay on their own track.
        Returns the index the new track ended up at.
        """
        nv = self.video_track_count()
        lo, hi = (0, nv) if kind == "video" else (nv, len(self.tracks))
        index = min(max(int(index), lo), hi)
        for c in self.clips:
            if c.track >= index:
                c.track += 1
        self.tracks.insert(index, {"name": "", "kind": kind})
        self.renumber_tracks()
        return index

    def add_video_layer(self, above=True):
        """New video layer above every other (or just below the lowest video layer)."""
        return self.insert_track(0 if above else self.video_track_count(), "video")

    def add_audio_track(self):
        return self.insert_track(len(self.tracks), "audio")

    def can_remove_track(self, index):
        if not (0 <= index < len(self.tracks)):
            return False
        kind = self.tracks[index]["kind"]
        return sum(1 for t in self.tracks if t["kind"] == kind) > 1       # keep at least one of each kind

    def remove_track(self, index):
        """Delete a track and the clips on it. Refuses to remove the last video or audio track."""
        if not self.can_remove_track(index):
            return False
        self.clips = [c for c in self.clips if c.track != index]
        for c in self.clips:
            if c.track > index:
                c.track -= 1
        del self.tracks[index]
        self.renumber_tracks()
        return True

    def move_track(self, index, delta):
        """Swap a layer with its neighbour of the same kind (changes the stacking order). delta = -1 up, +1 down."""
        j = index + delta
        if not (0 <= index < len(self.tracks) and 0 <= j < len(self.tracks)):
            return False
        if self.tracks[index]["kind"] != self.tracks[j]["kind"]:
            return False
        for c in self.clips:
            if c.track == index:
                c.track = j
            elif c.track == j:
                c.track = index
        self.tracks[index], self.tracks[j] = self.tracks[j], self.tracks[index]
        self.renumber_tracks()
        return True

    def track_is_empty(self, index):
        return not any(c.track == index for c in self.clips)

    def remove_empty_tracks(self):
        """Delete every empty layer/track (always keeping one video and one audio track). Returns the count."""
        n = 0
        for i in range(len(self.tracks) - 1, -1, -1):
            if self.track_is_empty(i) and self.remove_track(i):
                n += 1
        return n

    def is_free(self, track, start, duration, ignore=None):
        return all(start + duration <= o.start + 1e-9 or start >= o.end - 1e-9
                   for o in self.track_clips(track, ignore))

    def find_layer(self, kind, preferred, start, duration, ignore=None):
        """Track index of a ``kind`` ("video" | "audio") track that is free at [start, start+duration).

        Tries ``preferred`` first, then the other tracks of that kind nearest-first. If every one is
        occupied a new layer is created (video: on top, audio: at the bottom), so you can keep stacking
        clips without limit.
        """
        idx = [i for i, t in enumerate(self.tracks) if t["kind"] == kind]
        if preferred in idx:
            idx.sort(key=lambda i: (abs(i - preferred), i))
        elif kind == "video":
            idx.sort(reverse=True)                    # V1 (lowest) first
        for i in idx:
            if self.is_free(i, start, duration, ignore):
                return i
        return self.new_layer(kind, preferred, start, duration)

    def new_layer(self, kind, near=None, start=0.0, duration=0.0):
        """Create a track for an automatic placement, right NEXT TO ``near`` (the layer the clip wanted but
        found busy): directly below it, and below any further layers that are also busy over
        ``[start, start + duration)``, so repeated additions keep the order they were made in.
        Without ``near`` it goes last in its group."""
        group = [i for i, t in enumerate(self.tracks) if t["kind"] == kind]
        if near in group:
            i = near
            while i + 1 in group and not self.is_free(i + 1, start, duration):
                i += 1
            return self.insert_track(i + 1, kind)
        return self.insert_track(self.video_track_count() if kind == "video" else len(self.tracks), kind)

    def _lane_track(self, kind, track):
        """Resolve NEW_TOP / NEW_BOTTOM to a freshly created track (None if the lane doesn't fit ``kind``)."""
        if track == NEW_TOP and kind == "video":
            return self.insert_track(0, "video")
        if track == NEW_BOTTOM and kind == "audio":
            return self.insert_track(len(self.tracks), "audio")
        return None

    # ── placement ──
    def resolve_start(self, track, start, duration, ignore=None):
        """Nearest start >= 0 to ``start`` at which a clip of ``duration`` doesn't overlap others."""
        others = self.track_clips(track, ignore)
        start = max(0.0, start)

        def free(s):
            return s >= -1e-9 and all(s + duration <= o.start + 1e-9 or s >= o.end - 1e-9 for o in others)
        if free(start):
            return start
        cands = [0.0] + [o.end for o in others] + [o.start - duration for o in others]
        cands = [c for c in cands if free(c)]
        return min(cands, key=lambda c: abs(c - start)) if cands else (others[-1].end if others else 0.0)

    def add_media(self, item):
        item.id = item.id or self._new_id("m")
        self.media[item.id] = item
        return item

    def new_media_id(self):
        return self._new_id("m")

    def place_clip(self, media_id, track=None, start=0.0, duration=None, in_point=0.0):
        """Add a clip the way a layered editor does: on the chosen track if it's free, otherwise on a free
        layer of the same kind, otherwise on a brand-new layer. ``track`` may be None (default track for
        the media kind), a track index, or NEW_TOP / NEW_BOTTOM to force a new layer."""
        m = self.media[media_id]
        dur = duration if duration is not None else m.duration
        kind = "audio" if m.kind == "audio" else "video"
        start = max(0.0, start)
        new = self._lane_track(kind, track) if track in (NEW_TOP, NEW_BOTTOM) else None
        if new is not None:
            track = new
        else:
            pref = track if (track is not None and self.track_accepts(track, m.kind)) else self.first_track_for(m.kind)
            track = self.find_layer(kind, pref, start, dur)
        c = Clip(self._new_id("c"), media_id, track, start, in_point, dur)
        self.clips.append(c)
        return c

    def add_clip(self, media_id, track, start, duration=None, in_point=0.0):
        m = self.media[media_id]
        dur = duration if duration is not None else m.duration
        s = self.resolve_start(track, start, dur)
        c = Clip(self._new_id("c"), media_id, track, s, in_point, dur)
        self.clips.append(c)
        return c

    def add_title(self, start, duration=TEXT_DEFAULT_DURATION, track=None, **style):
        """Add a title clip exactly at ``start`` (CapCut-style: it never slides along the timeline).

        With no ``track`` it goes on the top video layer if that is free at ``[start, start + duration)``;
        otherwise a brand-new layer is inserted right below it. So a title never joins a clip that is
        already there; it only shares a layer when the user drags it there. An explicit ``track`` keeps the
        old behaviour (nearest free spot on that track).
        """
        if TEXT_MEDIA_ID not in self.media:
            self.media[TEXT_MEDIA_ID] = MediaItem(TEXT_MEDIA_ID, "", TEXT_KIND, TEXT_DEFAULT_DURATION)
        if track is not None and self.track_accepts(track, TEXT_KIND):
            s = self.resolve_start(track, start, duration)
        else:
            s = max(0.0, start)
            track = self.first_track_for(TEXT_KIND)
            if not self.is_free(track, s, duration):
                track = self.new_layer("video", track, s, duration)
        c = Clip(self._new_id("c"), TEXT_MEDIA_ID, track, s, 0.0, duration, title=dict(TITLE_DEFAULTS, **style))
        self.clips.append(c)
        return c

    def set_title(self, clip_id, **style):
        """Update a title clip's style/text. Returns False if the clip isn't a title."""
        c = self.clip(clip_id)
        if c is None or c.title is None:
            return False
        c.title.update(style)
        return True

    # ── editing ──
    def split(self, clip_id, t):
        """Split a clip at timeline time t. Returns the new right-hand clip or None."""
        c = self.clip(clip_id)
        if c is None or not (c.start + MIN_CLIP <= t <= c.end - MIN_CLIP):
            return None
        right = Clip(self._new_id("c"), c.media_id, c.track, t, c.in_point + (t - c.start), c.end - t,
                     title=None if c.title is None else dict(c.title),
                     **{k: getattr(c, k) for k in CLIP_DEFAULTS})
        c.duration = t - c.start
        self.clips.append(right)
        return right

    def trim_limits(self, clip_id):
        """(earliest start, latest end) the clip's edges may reach given media length and neighbours."""
        c = self.clip(clip_id)
        m = self.media[c.media_id]
        left_room = c.start if m.kind in STILL_KINDS else min(c.start, c.in_point)
        earliest = c.start - left_room
        latest = c.end if m.kind in STILL_KINDS else c.end
        if m.kind not in STILL_KINDS:
            latest = c.start + (m.duration - c.in_point)
        else:
            latest = float("inf")
        for o in self.track_clips(c.track, clip_id):
            if o.end <= c.start + 1e-9:
                earliest = max(earliest, o.end)
            if o.start >= c.end - 1e-9:
                latest = min(latest, o.start)
        return earliest, latest

    def trim_left(self, clip_id, new_start):
        c = self.clip(clip_id)
        lo, _ = self.trim_limits(clip_id)
        new_start = min(max(new_start, lo), c.end - MIN_CLIP)
        delta = new_start - c.start
        c.in_point += delta
        c.duration -= delta
        c.start = new_start

    def trim_right(self, clip_id, new_end):
        c = self.clip(clip_id)
        _, hi = self.trim_limits(clip_id)
        new_end = max(min(new_end, hi), c.start + MIN_CLIP)
        c.duration = new_end - c.start

    # ── ripple trim ──
    def ripple_limits(self, clip_id, side):
        """(lowest, highest) edge time a ripple trim of ``side`` ("left" | "right") may reach.

        Neighbours don't limit a ripple (they shift out of the way); only the media length and
        MIN_CLIP do. Images can be extended freely.
        """
        c = self.clip(clip_id)
        m = self.media[c.media_id]
        if side == "left":
            lo = float("-inf") if m.kind in STILL_KINDS else c.start - c.in_point
            return lo, c.end - MIN_CLIP
        hi = float("inf") if m.kind in STILL_KINDS else c.start + (m.duration - c.in_point)
        return c.start + MIN_CLIP, hi

    def ripple_trim(self, clip_id, side, edge):
        """Trim a clip edge and shift every later clip on the same track by the change in length.

        ``left``: the clip's start stays put and its head is trimmed/extended (the in-point moves).
        ``right``: the tail is trimmed/extended. Returns the shift applied to the later clips
        (negative = they moved earlier), or 0.0 if nothing changed.
        """
        c = self.clip(clip_id)
        m = self.media[c.media_id]
        lo, hi = self.ripple_limits(clip_id, side)
        edge = min(max(edge, lo), hi)
        old_end = c.end
        later = [o for o in self.track_clips(c.track, clip_id) if o.start >= old_end - 1e-9]
        if side == "left":
            delta = edge - c.start               # >0 trims the head, <0 extends it
            if abs(delta) < 1e-9:
                return 0.0
            if m.kind not in STILL_KINDS:
                c.in_point += delta
            c.duration -= delta
            shift = -delta
        else:
            shift = edge - old_end
            if abs(shift) < 1e-9:
                return 0.0
            c.duration = edge - c.start
        for o in later:
            o.start = max(0.0, o.start + shift)
        return shift

    # ── slip ──
    def slip_limits(self, clip_id):
        """(min, max) in-point for a slip. Images have no source range, so they can't slip."""
        c = self.clip(clip_id)
        m = self.media[c.media_id]
        if m.kind in STILL_KINDS:
            return 0.0, 0.0
        return 0.0, max(0.0, m.duration - c.duration)

    def slip(self, clip_id, in_point):
        """Change which part of the source plays without moving the clip or changing its length.
        Returns True if the in-point changed."""
        c = self.clip(clip_id)
        lo, hi = self.slip_limits(clip_id)
        new = min(max(in_point, lo), hi)
        if abs(new - c.in_point) < 1e-9:
            return False
        c.in_point = new
        return True

    def move(self, clip_id, track, start):
        c = self.clip(clip_id)
        m = self.media[c.media_id]
        if track in (NEW_TOP, NEW_BOTTOM):
            kind = "audio" if m.kind == "audio" else "video"
            if (track == NEW_TOP) != (kind == "video"):
                track = c.track                         # that lane doesn't take this kind of clip
            else:
                track = self._lane_track(kind, track)   # shifts the clip's old row; c.track is updated below
        if not self.track_accepts(track, m.kind):
            track = c.track
        c.track = track
        c.start = self.resolve_start(track, start, c.duration, ignore=clip_id)

    def delete(self, clip_ids, ripple=False):
        ids = set(clip_ids)
        removed = [c for c in self.clips if c.id in ids]
        self.clips = [c for c in self.clips if c.id not in ids]
        if ripple:
            for r in sorted(removed, key=lambda c: -c.start):
                for o in self.clips:
                    if o.track == r.track and o.start >= r.end - 1e-9:
                        o.start -= r.duration

    def remove_media(self, media_id):
        self.clips = [c for c in self.clips if c.media_id != media_id]
        self.media.pop(media_id, None)

    # ── (de)serialisation ──
    def to_dict(self):
        return dict(version=PROJECT_VERSION, width=self.width, height=self.height, fps=self.fps,
                    tracks=self.tracks, media=[m.to_dict() for m in self.media.values()],
                    clips=[c.to_dict() for c in self.clips])

    @staticmethod
    def from_dict(d):
        p = Project(d.get("width", 1920), d.get("height", 1080), d.get("fps", 30))
        p.tracks = copy.deepcopy(d.get("tracks") or DEFAULT_TRACKS)
        for m in d.get("media", []):
            p.media[m["id"]] = MediaItem.from_dict(m)
        p.clips = [Clip.from_dict(c) for c in d.get("clips", [])]
        top = 0
        for key in list(p.media) + [c.id for c in p.clips]:
            digits = "".join(ch for ch in key if ch.isdigit())
            top = max(top, int(digits or 0))
        p._ids = itertools.count(top + 1)
        return p

    def snapshot(self):
        return json.dumps(self.to_dict())

    @staticmethod
    def from_snapshot(s):
        return Project.from_dict(json.loads(s))

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)

    @staticmethod
    def load(path):
        with open(path, "r", encoding="utf-8") as f:
            return Project.from_dict(json.load(f))
