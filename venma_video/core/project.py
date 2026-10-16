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

    @property
    def end(self):
        return self.start + self.duration

    def to_dict(self):
        d = dict(id=self.id, media_id=self.media_id, track=self.track, start=self.start,
                 in_point=self.in_point, duration=self.duration)
        d.update({k: getattr(self, k) for k in CLIP_DEFAULTS})
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
                if c:
                    return c
        return None

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
        return idx[-1] if want == "video" else idx[0]

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

    def add_clip(self, media_id, track, start, duration=None, in_point=0.0):
        m = self.media[media_id]
        dur = duration if duration is not None else m.duration
        s = self.resolve_start(track, start, dur)
        c = Clip(self._new_id("c"), media_id, track, s, in_point, dur)
        self.clips.append(c)
        return c

    # ── editing ──
    def split(self, clip_id, t):
        """Split a clip at timeline time t. Returns the new right-hand clip or None."""
        c = self.clip(clip_id)
        if c is None or not (c.start + MIN_CLIP <= t <= c.end - MIN_CLIP):
            return None
        right = Clip(self._new_id("c"), c.media_id, c.track, t, c.in_point + (t - c.start), c.end - t,
                     **{k: getattr(c, k) for k in CLIP_DEFAULTS})
        c.duration = t - c.start
        self.clips.append(right)
        return right

    def trim_limits(self, clip_id):
        """(earliest start, latest end) the clip's edges may reach given media length and neighbours."""
        c = self.clip(clip_id)
        m = self.media[c.media_id]
        left_room = c.start if m.kind == "image" else min(c.start, c.in_point)
        earliest = c.start - left_room
        latest = c.end if m.kind == "image" else c.end
        if m.kind != "image":
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

    def move(self, clip_id, track, start):
        c = self.clip(clip_id)
        m = self.media[c.media_id]
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
