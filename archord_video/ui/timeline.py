"""Multi-track timeline widget: ruler, tracks, clips, playhead, trimming, snapping, drag & drop."""
import math

from PyQt5.QtCore import Qt, QRectF, QPointF, QRect, QEvent, pyqtSignal, QByteArray
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFont, QFontMetrics, QPolygonF, QPixmap
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollBar, QMenu, QToolTip

from . import theme
from ..core import animation as anim_core
from ..core.project import MIN_CLIP, NEW_BOTTOM, NEW_TOP, STILL_KINDS

MIME_MEDIA = "application/x-archord-media"
HEADER_W = 116
RULER_H = 28
TRACK_H = 58
LANE_H = 28           # the empty "drop here for a new layer" strips above the first and below the last track
BTN = 20              # layer switch buttons (hide / lock / mute) in the header column
BTN_GAP = 4
SWITCHES = ("hidden", "locked", "muted")
# key -> (icon when off, icon when on, tooltip when off, tooltip when on)
SWITCH_UI = {
    "hidden": ("eye", "eye_off", "Hide layer", "Show layer"),
    "locked": ("unlock", "lock", "Lock layer (no edits)", "Unlock layer"),
    "muted": ("volume", "volume_off", "Mute layer", "Unmute layer"),
}
EDGE = 7              # px grab zone for trimming
SNAP_PX = 9
KIND_COLORS = {"video": "#2F6E8E", "image": "#8A6A2F", "audio": "#1F8A83", "text": "#6D58B8"}
_STEPS = [0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800]


def format_tc(t, fps):
    """HH:MM:SS:FF timecode."""
    fps = max(1, int(fps))
    total_frames = int(round(max(0.0, t) * fps))
    f = total_frames % fps
    s = total_frames // fps
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def ruler_step(pps, min_px=80):
    for s in _STEPS:
        if s * pps >= min_px:
            return s
    return _STEPS[-1]


def label_time(t, step):
    m, s = divmod(t, 60)
    if step < 1:
        return f"{int(m)}:{s:04.1f}"
    return f"{int(m)}:{int(s):02d}"


class TimelineCanvas(QWidget):
    playheadChanged = pyqtSignal(float)
    clipSelected = pyqtSignal(object)            # clip id or None
    clipMoved = pyqtSignal(str, int, float)      # id, track, start
    clipsMoved = pyqtSignal(object, float)       # ids (several selected clips dragged together), time shift
    clipTrimmed = pyqtSignal(str, str, float)    # id, "left" | "right", new edge time
    clipRippled = pyqtSignal(str, str, float)    # id, "left" | "right", new edge time (later clips shift)
    clipSlipped = pyqtSignal(str, float)         # id, new in-point (clip position/length unchanged)
    splitRequested = pyqtSignal(str, float)
    mediaDropped = pyqtSignal(str, int, float)   # media id(s) separated by newlines, track (or NEW_TOP / NEW_BOTTOM), time
    trackAddRequested = pyqtSignal(str, int)     # "video" | "audio", index to insert the new track at
    trackRemoveRequested = pyqtSignal(int)       # track index
    trackMoveRequested = pyqtSignal(int, int)    # track index, -1 (up) / +1 (down)
    trackFlagToggled = pyqtSignal(int, str)      # track index, "hidden" | "locked" | "muted"
    trackReordered = pyqtSignal(int, int)        # layer dragged from index, to index
    keyframeSelected = pyqtSignal(str, float)    # clip id, clip-local time: a keyframe diamond was clicked (playhead jumps to it)
    keyframeMoved = pyqtSignal(str, float, float)   # clip id, old clip-local time, new clip-local time (diamond dragged)
    keyframeDeleteRequested = pyqtSignal(str, float)   # clip id, clip-local time
    viewChanged = pyqtSignal()
    statusMessage = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.thumbs = {}            # media id -> QPixmap
        self.waves = {}             # media path -> core.waveform.Waveform (or None while / if there is none)
        self.pps = 80.0             # pixels per second
        self.scroll = 0.0           # seconds at the left edge of the track area
        self.vscroll = 0.0          # pixels scrolled down (any number of layers fit; the rest scroll)
        self.playhead = 0.0
        self.selected = None        # the primary clip (shown in the Properties panel)
        self.selection = set()      # every selected clip id (always contains `selected` when that is set)
        self._marquee = None        # (x0, y0, x1, y1, base ids) while box-selecting on empty space
        self.tool = "select"
        self.snap_on = True
        self._drag = None           # dict describing the active drag
        self.sel_kf = None                 # (clip id, clip-local time) of the selected keyframe diamond
        self._kfdrag = None                # live drag of a keyframe diamond
        self._scrub = False
        self._pan = None
        self._drop = None           # (track, time) while dragging media over
        self._tdrag = None          # layer being dragged by its header: dict(src, press, moved, target)
        self.rec = None             # live voiceover take: dict(track, t0, t1, levels, step) (painted over the cache)
        self._icons = {}
        self._cache = None          # pre-rendered tracks/clips/ruler (playhead is drawn on top)
        self._cache_key = None
        self._dirty = True
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(RULER_H + LANE_H + 2 * TRACK_H)

    def invalidate(self):
        """Something static changed (clips, zoom, selection, theme): re-render the cached layer."""
        self._dirty = True
        QWidget.update(self)

    # ── live voiceover take ──────────────────────────────────────────────────
    def set_recording(self, track, t0, t1, levels=(), step=0.05):
        """Show a take being recorded on ``track`` from ``t0`` to ``t1`` (``levels``: one 0..1 peak per ``step`` s)."""
        self.rec = dict(track=track, t0=t0, t1=max(t0, t1), levels=levels, step=step)
        QWidget.update(self)

    def clear_recording(self):
        if self.rec is not None:
            self.rec = None
            QWidget.update(self)

    # ── data ─────────────────────────────────────────────────────────────────
    def set_project(self, project):
        self.project = project
        self.vscroll = max(0.0, min(self.vscroll, max(0.0, self.content_height() - self.view_height())))
        self.selection = {i for i in self.selection if project.clip(i)}
        if self.selected and not project.clip(self.selected):
            self.selected = next(iter(sorted(self.selection)), None)
            self.clipSelected.emit(self.selected)
        self.viewChanged.emit()
        self.invalidate()

    def select(self, clip_id):
        if clip_id != self.selected:
            self.sel_kf = None
        self.selected = clip_id
        self.selection = {clip_id} if clip_id else set()
        self.clipSelected.emit(clip_id)
        self.invalidate()

    def selected_ids(self):
        """Selected clip ids in timeline order (the primary clip is always among them)."""
        if not self.project:
            return []
        return [c.id for c in self.project.clips if c.id in self.selection]

    def _set_selection(self, ids, primary=None):
        ids = set(ids)
        if primary not in ids:
            primary = self.selected if self.selected in ids else (sorted(ids)[0] if ids else None)
        changed = primary != self.selected
        if changed:
            self.sel_kf = None
        self.selected, self.selection = primary, ids
        if changed:
            self.clipSelected.emit(primary)
        if len(ids) > 1:
            self.statusMessage.emit(f"{len(ids)} clips selected")
        self.invalidate()

    def select_many(self, ids, primary=None):
        self._set_selection(ids, primary)

    def toggle_select(self, clip_id):
        """Ctrl/Shift-click: add the clip to the selection, or remove it if already selected."""
        ids = set(self.selection)
        if clip_id in ids:
            ids.discard(clip_id)
            self._set_selection(ids, self.selected if self.selected != clip_id else None)
        else:
            ids.add(clip_id)
            self._set_selection(ids, clip_id)

    def select_all(self):
        if self.project:
            self._set_selection({c.id for c in self.project.clips}, self.selected)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_A and e.modifiers() & Qt.ControlModifier:
            self.select_all()
        elif e.key() == Qt.Key_Escape and self.selection:
            self.select(None)
        else:
            super().keyPressEvent(e)

    # ── coordinate helpers ───────────────────────────────────────────────────
    def t2x(self, t):
        return HEADER_W + (t - self.scroll) * self.pps

    def x2t(self, x):
        return self.scroll + (x - HEADER_W) / self.pps

    def track_y(self, i):
        return RULER_H + LANE_H + i * TRACK_H - self.vscroll

    def lane_y(self, lane):
        """Top edge of the NEW_TOP / NEW_BOTTOM drop strip."""
        if lane == NEW_TOP:
            return RULER_H - self.vscroll
        return self.track_y(len(self.project.tracks))

    def content_height(self):
        n = len(self.project.tracks) if self.project else 0
        return 2 * LANE_H + n * TRACK_H

    def view_height(self):
        return max(1, self.height() - RULER_H)

    def track_at(self, y):
        if not self.project or y < RULER_H:
            return -1
        i = int((y - self.track_y(0)) // TRACK_H)
        return i if 0 <= i < len(self.project.tracks) else -1

    def lane_at(self, y):
        """NEW_TOP / NEW_BOTTOM when y is over one of the "new layer" strips, else None."""
        if not self.project or y < RULER_H:
            return None
        for lane in (NEW_TOP, NEW_BOTTOM):
            top = self.lane_y(lane)
            if top <= y < top + LANE_H:
                return lane
        return None

    # ── layer switches (hide / lock / mute) and header drag ──────────────────
    def switch_rect(self, i, key):
        """Button rectangle of one switch in track ``i``'s header (same column for every layer)."""
        y = self.track_y(i) + TRACK_H - BTN - 6
        return QRectF(10 + SWITCHES.index(key) * (BTN + BTN_GAP), y, BTN, BTN)

    def switches_for(self, i):
        """Switches a layer has: audio tracks have nothing to hide."""
        if not self.project or not (0 <= i < len(self.project.tracks)):
            return ()
        return SWITCHES if self.project.tracks[i]["kind"] == "video" else ("locked", "muted")

    def switch_at(self, pos):
        """(track index, switch key) under ``pos`` in the header column, or None."""
        i = self.track_at(pos.y())
        if i < 0 or pos.x() >= HEADER_W:
            return None
        pt = QPointF(pos)
        for key in self.switches_for(i):
            if self.switch_rect(i, key).adjusted(-2, -2, 2, 2).contains(pt):
                return i, key
        return None

    def _group_range(self, i):
        """(first, last) track index of the layers that share ``i``'s kind (the only places it may be dragged to)."""
        kind = self.project.tracks[i]["kind"]
        idx = [n for n, t in enumerate(self.project.tracks) if t["kind"] == kind]
        return idx[0], idx[-1]

    def _drag_target(self, y):
        lo, hi = self._group_range(self._tdrag["src"])
        row = int((y - self.track_y(0)) // TRACK_H)
        return min(max(row, lo), hi)

    def event(self, e):
        if e.type() == QEvent.ToolTip and self.project:
            hit = self.switch_at(e.pos())
            if hit:
                i, key = hit
                off_i, on_i, off_t, on_t = SWITCH_UI[key]
                QToolTip.showText(e.globalPos(), on_t if self.project.tracks[i].get(key) else off_t, self)
            elif e.pos().x() < HEADER_W and self.track_at(e.pos().y()) >= 0:
                QToolTip.showText(e.globalPos(), "Drag to reorder layers", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def _row_top(self, track):
        if track in (NEW_TOP, NEW_BOTTOM):                  # a clip being dragged into a new-layer strip
            return self.lane_y(track) + LANE_H / 2 - TRACK_H / 2
        return self.track_y(track)

    def view_seconds(self):
        return max(0.1, (self.width() - HEADER_W) / self.pps)

    def content_seconds(self):
        end = self.project.duration() if self.project else 0.0
        return max(end + 15.0, self.view_seconds())

    def clip_rect(self, c, start=None, dur=None, track=None):
        start = c.start if start is None else start
        dur = c.duration if dur is None else dur
        track = c.track if track is None else track
        return QRectF(self.t2x(start), self._row_top(track) + 3, max(2.0, dur * self.pps), TRACK_H - 6)

    # ── view control ─────────────────────────────────────────────────────────
    def set_scroll(self, s):
        s = max(0.0, min(s, max(0.0, self.content_seconds() - self.view_seconds())))
        if abs(s - self.scroll) > 1e-9:
            self.scroll = s
            self.invalidate()
            self.viewChanged.emit()

    def set_vscroll(self, v):
        v = max(0.0, min(v, max(0.0, self.content_height() - self.view_height())))
        if abs(v - self.vscroll) > 1e-9:
            self.vscroll = v
            self.invalidate()
            self.viewChanged.emit()

    def ensure_track_visible(self, i):
        top = LANE_H + i * TRACK_H                           # in content coordinates
        if i <= 0:
            self.set_vscroll(0.0)
        elif top < self.vscroll:
            self.set_vscroll(top)
        elif top + TRACK_H > self.vscroll + self.view_height():
            self.set_vscroll(top + TRACK_H - self.view_height())

    def _autoscroll(self, y):
        """Drags near the top / bottom edge scroll the layers so any layer can be reached."""
        if y > self.height() - 18:
            self.set_vscroll(self.vscroll + 14)
        elif RULER_H <= y < RULER_H + 16:
            self.set_vscroll(self.vscroll - 14)

    def zoom_by(self, factor, anchor_x=None):
        ax = (self.width() / 2) if anchor_x is None else anchor_x
        t_anchor = self.x2t(ax)
        self.pps = max(4.0, min(self.pps * factor, 1200.0))
        self.scroll = max(0.0, t_anchor - (ax - HEADER_W) / self.pps)
        self.invalidate()
        self.viewChanged.emit()

    def fit(self):
        dur = self.project.duration() if self.project else 0.0
        if dur > 0:
            self.pps = max(4.0, (self.width() - HEADER_W - 40) / (dur * 1.05))
        self.scroll = 0.0
        self.invalidate()
        self.viewChanged.emit()

    def set_playhead(self, t, follow=False):
        old_x = self.t2x(self.playhead)
        self.playhead = max(0.0, t)
        if follow:
            right = self.scroll + self.view_seconds()
            if self.playhead > right - 0.05 * self.view_seconds() or self.playhead < self.scroll:
                self.set_scroll(self.playhead - 0.1 * self.view_seconds())   # invalidates everything
                return
        new_x = self.t2x(self.playhead)
        lo, hi = int(min(old_x, new_x)) - 10, int(max(old_x, new_x)) + 10
        QWidget.update(self, lo, 0, hi - lo, self.height())

    def set_tool(self, tool):
        self.tool = tool
        self.setCursor({"hand": Qt.OpenHandCursor, "razor": Qt.CrossCursor, "zoom": Qt.CrossCursor,
                        "slip": Qt.SizeHorCursor}.get(tool, Qt.ArrowCursor))

    # ── snapping ─────────────────────────────────────────────────────────────
    def _snap_points(self, ignore=None):
        skip = {ignore} if isinstance(ignore, str) else set(ignore or ())
        pts = [0.0, self.playhead]
        for c in self.project.clips:
            if c.id not in skip:
                pts += [c.start, c.end]
        return pts

    def snap(self, t, ignore=None, also=()):
        """Snap t (and optionally t+offset for each offset in ``also``) to nearby edges / playhead.
        ``ignore`` is a clip id or a collection of clip ids whose edges are not snap targets."""
        if not self.snap_on or not self.project:
            return t
        tol = SNAP_PX / self.pps
        best, best_d = t, tol
        for p in self._snap_points(ignore):
            for off in (0.0,) + tuple(also):
                d = abs((t + off) - p)
                if d < best_d:
                    best, best_d = p - off, d
        return best

    # ── hit testing ──────────────────────────────────────────────────────────
    def hit(self, pos):
        if not self.project:
            return None, None
        for c in reversed(self.project.clips):
            r = self.clip_rect(c)
            if r.contains(QPointF(pos)):
                edge = min(EDGE, r.width() / 3)
                if pos.x() <= r.left() + edge:
                    return c, "left"
                if pos.x() >= r.right() - edge:
                    return c, "right"
                return c, "body"
        return None, None

    # ── keyframe diamonds ────────────────────────────────────────────────────
    KF_HIT = 7                                   # px: how close a click must be to a diamond to grab it

    @staticmethod
    def _kf_y(r):
        return r.center().y() + 6

    def kf_hit(self, pos):
        """(clip, keyframe) of the diamond under ``pos`` (front-most clip first), or (None, None)."""
        if not self.project or pos.x() < HEADER_W:
            return None, None
        for c in reversed(self.project.clips):
            if not c.keyframes:
                continue
            r = self.clip_rect(c)
            near_row = abs(pos.y() - self._kf_y(r)) <= self.KF_HIT + 2
            if not near_row or not (r.left() - self.KF_HIT <= pos.x() <= r.right() + self.KF_HIT):
                continue
            best, best_d = None, self.KF_HIT + 1
            for kf in c.keyframes:
                d = abs(pos.x() - self.t2x(c.start + kf.t))
                if d < best_d:
                    best, best_d = kf, d
            if best is not None:
                return c, best
        return None, None

    def _kf_shown_t(self, c, kf):
        """Where a diamond is drawn: at its dragged position while it is being dragged."""
        d = self._kfdrag
        if d is not None and d["id"] == c.id and abs(kf.t - d["t0"]) < 1e-6:
            return d["t"]
        return kf.t

    def _kf_is_selected(self, c, kf):
        k = self.sel_kf
        return k is not None and k[0] == c.id and abs(k[1] - kf.t) < 1e-4

    def _kf_menu(self, gpos, c, kf):
        menu = QMenu(self)
        a_seek = menu.addAction("Go to keyframe")
        a_del = menu.addAction("Delete keyframe")
        a_del.setEnabled(not self.project.track_locked(c.track))
        act = menu.exec_(gpos)
        if act is a_seek:
            self.keyframeSelected.emit(c.id, kf.t)
        elif act is a_del:
            self.keyframeDeleteRequested.emit(c.id, kf.t)

    # ── mouse ────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if not self.project:
            return
        self.setFocus()
        pos = e.pos()
        if e.button() == Qt.MidButton or (e.button() == Qt.LeftButton and self.tool == "hand"):
            self._pan = (e.globalPos().x(), self.scroll)
            self.setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.RightButton:
            if pos.x() < HEADER_W and pos.y() >= RULER_H:
                self._header_menu(e.globalPos(), pos)
            else:
                kc, kf = self.kf_hit(pos)
                if kc is not None:
                    if self.selected != kc.id:
                        self.select(kc.id)
                    self.sel_kf = (kc.id, kf.t)
                    self.invalidate()
                    self._kf_menu(e.globalPos(), kc, kf)
            return
        if e.button() != Qt.LeftButton:
            return
        if pos.y() < RULER_H and pos.x() >= HEADER_W:
            self._scrub = True
            self._seek_to_x(pos.x())
            return
        if pos.x() < HEADER_W and pos.y() >= RULER_H:          # track header column
            lane = self.lane_at(pos.y())
            if lane == NEW_TOP:
                self.trackAddRequested.emit("video", 0)
            elif lane == NEW_BOTTOM:
                self.trackAddRequested.emit("audio", len(self.project.tracks))
            else:
                sw = self.switch_at(pos)
                if sw:
                    self.trackFlagToggled.emit(*sw)
                else:
                    i = self.track_at(pos.y())
                    if i >= 0:                                   # press on the header body: maybe a reorder drag
                        self._tdrag = dict(src=i, press=pos, moved=False, target=i)
            return
        if self.tool == "zoom":
            self.zoom_by(0.8 if e.modifiers() & Qt.AltModifier else 1.25, pos.x())
            return
        if self.tool == "select":
            kc, kf = self.kf_hit(pos)
            if kc is not None:                                  # grab a keyframe diamond (click = go to it, drag = move it)
                if self.selected != kc.id:
                    self.select(kc.id)
                self.sel_kf = (kc.id, kf.t)
                self._kfdrag = dict(id=kc.id, t0=kf.t, t=kf.t, press=pos, moved=False,
                                    locked=self.project.track_locked(kc.track))
                self.keyframeSelected.emit(kc.id, kf.t)
                self.invalidate()
                return
        c, zone = self.hit(pos)
        if self.tool == "razor":
            if c and self.project.track_locked(c.track):
                self._locked_note(c.track)
                return
            if c:
                t = self.snap(self.x2t(pos.x()), ignore=c.id)
                self.splitRequested.emit(c.id, t)
            return
        multi = bool(e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)) and self.tool == "select"
        if c is None:
            if multi and pos.x() >= HEADER_W and pos.y() >= RULER_H:      # Ctrl/Shift + drag on empty space: box select
                self._marquee = (pos.x(), pos.y(), pos.x(), pos.y(), set(self.selection))
                return
            if self.selection:
                self.select(None)
            if pos.x() >= HEADER_W:
                self._scrub = True
                self._seek_to_x(pos.x())
            return
        if multi:
            self.toggle_select(c.id)
            return
        group = self.tool == "select" and zone == "body" and c.id in self.selection and len(self.selection) > 1
        if group:                                              # press inside a multi-selection: keep it, make this the primary
            if self.selected != c.id:
                self.sel_kf = None
                self.selected = c.id
                self.clipSelected.emit(c.id)
                self.invalidate()
        elif self.selected != c.id or self.selection != {c.id}:
            self.select(c.id)
        if self.project.track_locked(c.track):                 # selectable, but nothing can change
            self._locked_note(c.track)
            return
        ripple = self.tool == "ripple"
        if ripple and zone == "body":
            return                                  # ripple only acts on clip edges
        if self.tool == "slip":
            if self.project.media[c.media_id].kind in STILL_KINDS:
                self.statusMessage.emit("Slip needs a video or audio clip (images and titles have no source range).")
                return
            zone = "slip"
        self._drag = dict(id=c.id, zone=zone, grab=self.x2t(pos.x()) - c.start, start0=c.start, dur0=c.duration,
                          track0=c.track, start=c.start, dur=c.duration, track=c.track, moved=False,
                          press=pos, ripple=ripple, in0=c.in_point, t0=self.x2t(pos.x()), shift=0.0)
        self._drag["in"] = c.in_point               # live in-point while slipping
        if group:
            ids = [i for i in self.selected_ids() if not self.project.clip_locked(i)]
            self._drag.update(group=ids, delta=0.0, lim=self.project.group_move_limits(ids))

    def _locked_note(self, track):
        self.statusMessage.emit(f"{self.project.tracks[track]['name']} is locked. Click its lock to edit.")

    def mouseMoveEvent(self, e):
        if not self.project:
            return
        pos = e.pos()
        td = self._tdrag
        if td is not None:                                     # dragging a layer by its header
            if not td["moved"] and (pos - td["press"]).manhattanLength() < 5:
                return
            if not td["moved"]:
                td["moved"] = True
                self.setCursor(Qt.ClosedHandCursor)
            self._autoscroll(pos.y())
            td["target"] = self._drag_target(pos.y())
            self.invalidate()
            return
        if self._pan is not None:
            dx = (e.globalPos().x() - self._pan[0]) / self.pps
            self.set_scroll(self._pan[1] - dx)
            return
        if self._scrub:
            self._seek_to_x(pos.x())
            return
        if self._marquee is not None:                          # box select: everything the box touches
            x0, y0, _, _, base = self._marquee
            self._marquee = (x0, y0, pos.x(), pos.y(), base)
            box = QRectF(QPointF(x0, y0), QPointF(pos.x(), pos.y())).normalized()
            hit = {c.id for c in self.project.clips if box.intersects(self.clip_rect(c))}
            self._set_selection(base | hit)
            return
        kd = self._kfdrag
        if kd is not None:                                     # dragging a keyframe diamond along its clip
            if not kd["moved"] and (pos - kd["press"]).manhattanLength() < 3:
                return
            if kd["locked"]:
                return
            c = self.project.clip(kd["id"])
            kf = c.keyframe_at(kd["t0"], 1e-3) if c is not None else None
            if kf is None:
                return
            kd["moved"] = True
            t = self.x2t(pos.x()) - c.start
            if self.snap_on:                                   # stick to the playhead
                tol = SNAP_PX / self.pps
                if abs(t - (self.playhead - c.start)) < tol:
                    t = self.playhead - c.start
            lo, hi = c.keyframe_limits(kf)
            kd["t"] = min(max(t, lo), hi)
            self.setCursor(Qt.SizeHorCursor)
            self.invalidate()
            return
        d = self._drag
        if d is None and pos.x() < HEADER_W and pos.y() >= RULER_H:
            self.setCursor(Qt.PointingHandCursor if self.switch_at(pos) else
                           Qt.OpenHandCursor if self.track_at(pos.y()) >= 0 else Qt.ArrowCursor)
            return
        if d is None:
            c, zone = self.hit(pos)
            if self.tool == "select" and self.kf_hit(pos)[0] is not None:
                self.setCursor(Qt.PointingHandCursor)
            elif self.tool == "select":
                self.setCursor(Qt.SizeHorCursor if zone in ("left", "right") else
                               Qt.OpenHandCursor if zone == "body" else Qt.ArrowCursor)
            elif self.tool == "ripple":
                self.setCursor(Qt.SplitHCursor if zone in ("left", "right") else Qt.ArrowCursor)
            elif self.tool == "slip":
                self.setCursor(Qt.SizeHorCursor if c else Qt.ArrowCursor)
            return
        if not d["moved"] and (pos - d["press"]).manhattanLength() < 3:
            return
        d["moved"] = True
        c = self.project.clip(d["id"])
        t = self.x2t(pos.x())
        if d["zone"] == "slip":
            lo, hi = self.project.slip_limits(c.id)
            k = c.speed * (1.0 if c.reverse else -1.0)               # source seconds per timeline second dragged
            d["in"] = min(max(d["in0"] + k * (t - d["t0"]), lo), hi)   # content follows the cursor
        elif d["ripple"]:
            # clips that will shift are not snap targets (they move with the edge)
            skip = {c.id} | {o.id for o in self.project.track_clips(c.track) if o.start >= c.end - 1e-9}
            lo, hi = self.project.ripple_limits(c.id, d["zone"])
            edge = min(max(self.snap(t, ignore=skip), lo), hi)
            if d["zone"] == "left":
                delta = edge - c.start
                d["dur"], d["shift"] = c.duration - delta, -delta
            else:
                d["dur"], d["shift"] = edge - c.start, edge - c.end
            d["start"] = c.start
        elif d["zone"] == "body" and d.get("group"):             # several clips: one shared time shift, layers unchanged
            raw = self.snap(t - d["grab"], ignore=set(d["group"]), also=(c.duration,)) - d["start0"]
            lo, hi = d["lim"]
            d["delta"] = min(max(raw, lo), hi)
            d["start"], d["track"], d["dur"] = d["start0"] + d["delta"], d["track0"], c.duration
        elif d["zone"] == "body":
            self._autoscroll(pos.y())
            start = self.snap(t - d["grab"], ignore=c.id, also=(c.duration,))
            m = self.project.media[c.media_id]
            lane = self.lane_at(pos.y())
            is_audio = m.kind == "audio"
            if lane is not None and (lane == NEW_BOTTOM) == is_audio:
                track = lane                                   # over a "new layer" strip that fits this clip
            else:
                track = self.track_at(pos.y())
                if track < 0 or not self.project.track_accepts(track, m.kind) or self.project.track_locked(track):
                    track = d["track0"]
            d["track"] = track
            if track in (NEW_TOP, NEW_BOTTOM):
                d["start"] = max(0.0, start)                   # a brand-new layer is always empty
            else:
                d["start"] = self.project.resolve_start(track, max(0.0, start), c.duration, ignore=c.id)
            d["dur"] = c.duration
        elif d["zone"] == "left":
            lo, _ = self.project.trim_limits(c.id)
            ns = min(max(self.snap(t, ignore=c.id), lo), c.end - MIN_CLIP)
            d["start"], d["dur"] = ns, c.end - ns
        else:
            _, hi = self.project.trim_limits(c.id)
            ne = max(min(self.snap(t, ignore=c.id), hi), c.start + MIN_CLIP)
            d["start"], d["dur"] = c.start, ne - c.start
        self.invalidate()

    def mouseReleaseEvent(self, e):
        td, self._tdrag = self._tdrag, None
        if td is not None:
            self.set_tool(self.tool)
            if td["moved"] and td["target"] != td["src"]:
                self.trackReordered.emit(td["src"], td["target"])
            self.invalidate()
            return
        if self._pan is not None:
            self._pan = None
            self.set_tool(self.tool)
            return
        if self._scrub:
            self._scrub = False
            return
        if self._marquee is not None:
            self._marquee = None
            self.invalidate()
            return
        kd, self._kfdrag = self._kfdrag, None
        if kd is not None:
            self.set_tool(self.tool)
            if kd["moved"] and abs(kd["t"] - kd["t0"]) > 1e-6:
                self.sel_kf = (kd["id"], kd["t"])
                self.keyframeMoved.emit(kd["id"], kd["t0"], kd["t"])
            self.invalidate()
            return
        d, self._drag = self._drag, None
        if not d or not d["moved"]:
            self.invalidate()
            return
        if d["zone"] == "slip":
            if abs(d["in"] - d["in0"]) > 1e-6:
                self.clipSlipped.emit(d["id"], d["in"])
        elif d["ripple"]:
            if abs(d["shift"]) > 1e-6:
                edge = d["start0"] + (d["dur0"] - d["dur"]) if d["zone"] == "left" else d["start0"] + d["dur"]
                self.clipRippled.emit(d["id"], d["zone"], edge)
        elif d["zone"] == "body" and d.get("group"):
            if abs(d["delta"]) > 1e-6:
                self.clipsMoved.emit(list(d["group"]), d["delta"])
        elif d["zone"] == "body":
            if abs(d["start"] - d["start0"]) > 1e-6 or d["track"] != d["track0"]:
                self.clipMoved.emit(d["id"], d["track"], d["start"])
        elif d["zone"] == "left":
            if abs(d["start"] - d["start0"]) > 1e-6:
                self.clipTrimmed.emit(d["id"], "left", d["start"])
        else:
            if abs(d["dur"] - d["dur0"]) > 1e-6:
                self.clipTrimmed.emit(d["id"], "right", d["start"] + d["dur"])
        self.invalidate()

    def _seek_to_x(self, x):
        t = max(0.0, self.x2t(x))
        if self.snap_on and self.project:
            t = self.snap(t)
        self.playhead = t
        self.playheadChanged.emit(t)
        self.invalidate()

    def wheelEvent(self, e):
        dy = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.AltModifier:                   # Alt + wheel: scroll through the layers
            self.set_vscroll(self.vscroll - (dy / 120.0) * TRACK_H)
        elif e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.2 if dy > 0 else 1 / 1.2, e.pos().x())
        else:
            self.set_scroll(self.scroll - (dy / 120.0) * 60.0 / self.pps)
        e.accept()

    def resizeEvent(self, e):
        self.set_vscroll(self.vscroll)
        self.viewChanged.emit()
        super().resizeEvent(e)

    # ── drag & drop from the media bin ───────────────────────────────────────
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_MEDIA):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_MEDIA):
            lane = self.lane_at(e.pos().y())
            self._autoscroll(e.pos().y())
            row = lane if lane is not None else self.track_at(e.pos().y())
            if row >= 0 and self.project.track_locked(row):    # locked layer: nothing can be dropped on it
                self._drop = None
                self.invalidate()
                e.ignore()
                return
            self._drop = (row, max(0.0, self.snap(self.x2t(e.pos().x()))))
            self.invalidate()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drop = None
        self.invalidate()

    def dropEvent(self, e):
        self._drop = None
        if e.mimeData().hasFormat(MIME_MEDIA):
            mid = bytes(e.mimeData().data(MIME_MEDIA)).decode("utf-8")      # one id, or several joined by newlines
            t = max(0.0, self.snap(self.x2t(e.pos().x())))
            lane = self.lane_at(e.pos().y())
            row = lane if lane is not None else self.track_at(e.pos().y())
            if row >= 0 and self.project.track_locked(row):
                self._locked_note(row)
                e.ignore()
                self.invalidate()
                return
            self.mediaDropped.emit(mid, row, t)
            e.acceptProposedAction()
        self.invalidate()

    # ── layer menu (right-click a track header) ──────────────────────────────
    def _header_menu(self, global_pos, pos):
        tracks = self.project.tracks
        menu = QMenu(self)
        i = self.track_at(pos.y())
        lane = self.lane_at(pos.y())
        if i >= 0:
            kind = tracks[i]["kind"]
            noun = "layer" if kind == "video" else "audio track"
            for key in self.switches_for(i):
                off_i, on_i, off_t, on_t = SWITCH_UI[key]
                menu.addAction(on_t if tracks[i].get(key) else off_t,
                               lambda key=key: self.trackFlagToggled.emit(i, key))
            menu.addSeparator()
            menu.addAction(f"Add {noun} above", lambda: self.trackAddRequested.emit(kind, i))
            menu.addAction(f"Add {noun} below", lambda: self.trackAddRequested.emit(kind, i + 1))
            menu.addSeparator()
            up = menu.addAction("Move up (stack higher)" if kind == "video" else "Move up",
                                lambda: self.trackMoveRequested.emit(i, -1))
            up.setEnabled(i > 0 and tracks[i - 1]["kind"] == kind)
            down = menu.addAction("Move down (stack lower)" if kind == "video" else "Move down",
                                  lambda: self.trackMoveRequested.emit(i, 1))
            down.setEnabled(i + 1 < len(tracks) and tracks[i + 1]["kind"] == kind)
            menu.addSeparator()
            rm = menu.addAction(f"Delete {noun} {tracks[i]['name']} (and its clips)",
                                lambda: self.trackRemoveRequested.emit(i))
            rm.setEnabled(self.project.can_remove_track(i))
        elif lane is not None:
            menu.addAction("Add video layer on top", lambda: self.trackAddRequested.emit("video", 0))
            menu.addAction("Add audio track at the bottom",
                           lambda: self.trackAddRequested.emit("audio", len(tracks)))
        if not menu.isEmpty():
            menu.exec_(global_pos)

    # ── painting ─────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        key = (self.width(), self.height(), theme.NAME, self.devicePixelRatioF())
        if self._cache is None or self._dirty or key != self._cache_key:
            dpr = self.devicePixelRatioF()
            pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
            pm.setDevicePixelRatio(dpr)
            cp = QPainter(pm)
            cp.setRenderHint(QPainter.Antialiasing)
            self._paint_static(cp)
            cp.end()
            self._cache, self._cache_key, self._dirty = pm, key, False
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        p.setRenderHint(QPainter.Antialiasing)
        self._paint_recording(p)
        self._paint_marquee(p)
        self._paint_playhead(p)

    def _paint_marquee(self, p):
        if self._marquee is None:
            return
        x0, y0, x1, y1, _ = self._marquee
        box = QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()
        col = QColor(theme.T["accent"]) if "accent" in theme.T else QColor("#7c5cff")
        p.setPen(QPen(col, 1))
        fill = QColor(col); fill.setAlpha(40)
        p.setBrush(fill)
        p.drawRect(box)

    def _paint_recording(self, p):
        r = self.rec
        if not r or not self.project or not (0 <= r["track"] < len(self.project.tracks)):
            return
        width = max(3.0, (r["t1"] - r["t0"]) * self.pps)
        rect = QRectF(self.t2x(r["t0"]), self.track_y(r["track"]) + 3, width, TRACK_H - 6)
        p.save()
        p.setClipRect(QRectF(HEADER_W, RULER_H, self.width() - HEADER_W, self.height() - RULER_H))
        red = QColor("#E5484D")
        fill = QColor(red); fill.setAlpha(150)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        p.setPen(QPen(red, 1.5))
        p.setBrush(fill)
        p.drawPath(path)
        p.setClipPath(path)
        mid, half = rect.center().y(), (rect.height() - 10) / 2
        bar = max(1.0, r["step"] * self.pps - 1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 215))
        for i, lv in enumerate(r["levels"]):                 # live level trace, one bar per ~50 ms of audio
            x = self.t2x(r["t0"] + i * r["step"])
            if x + bar < HEADER_W or x > self.width():
                continue
            h = max(1.0, lv * half)
            p.drawRect(QRectF(x, mid - h, bar, 2 * h))
        f = QFont(p.font()); f.setPointSizeF(8.5); f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(rect.adjusted(8, 3, -6, 0), Qt.AlignLeft | Qt.AlignTop, "● REC  %.1fs" % (r["t1"] - r["t0"]))
        p.restore()

    def _paint_static(self, p):
        T = theme.T
        p.fillRect(self.rect(), QColor(T["sunken"]))
        if not self.project:
            return
        w, h = self.width(), self.height()
        n = len(self.project.tracks)
        body = QRectF(0, RULER_H, w, h - RULER_H)
        p.save()
        p.setClipRect(body)                                 # rows scrolled up simply slide under the ruler

        # track rows
        for i, tr in enumerate(self.project.tracks):
            y = self.track_y(i)
            if y + TRACK_H < RULER_H or y > h:
                continue
            p.fillRect(QRectF(HEADER_W, y, w - HEADER_W, TRACK_H), QColor(T["surface"] if i % 2 == 0 else T["panel"]))
            p.setPen(QPen(QColor(T["border"]), 1))
            p.drawLine(QPointF(HEADER_W, y + TRACK_H), QPointF(w, y + TRACK_H))

        # "new layer" drop strips (also clickable in the header column)
        hot = self._drop[0] if self._drop else None
        for lane, text in ((NEW_TOP, "Drop here for a new layer"), (NEW_BOTTOM, "Drop here for a new audio track")):
            y = self.lane_y(lane)
            if y + LANE_H < RULER_H or y > h:
                continue
            r = QRectF(HEADER_W + 6, y + 3, w - HEADER_W - 12, LANE_H - 6)
            if hot == lane:
                fill = QColor(T["accent"]); fill.setAlpha(60)
                p.setBrush(fill)
            else:
                p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(T["accent"] if hot == lane else T["faint"]), 1, Qt.DashLine))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor(T["accent"] if hot == lane else T["muted"]))
            p.drawText(r, Qt.AlignCenter, text)

        # clips
        p.save()
        p.setClipRect(QRectF(HEADER_W, RULER_H, w - HEADER_W, h - RULER_H))
        for c in self.project.clips:
            y = self.track_y(c.track)
            if (y + TRACK_H < RULER_H or y > h) and not (self._drag and self._drag["id"] == c.id):
                continue                                    # layer is scrolled out of view
            self._draw_clip(p, c)
        if self._drop and self._drop[0] >= 0:                          # drop indicator
            x = self.t2x(self._drop[1])
            y0 = self.track_y(self._drop[0])
            p.setPen(QPen(QColor(T["accent"]), 2, Qt.DashLine))
            p.drawLine(QPointF(x, y0), QPointF(x, y0 + TRACK_H))
        elif self._drop and self._drop[0] in (NEW_TOP, NEW_BOTTOM):
            x = self.t2x(self._drop[1])
            y0 = self.lane_y(self._drop[0])
            p.setPen(QPen(QColor(T["accent"]), 2, Qt.DashLine))
            p.drawLine(QPointF(x, y0), QPointF(x, y0 + LANE_H))
        p.restore()

        # headers
        p.fillRect(QRectF(0, RULER_H, HEADER_W, h - RULER_H), QColor(T["panel"]))
        p.setPen(QPen(QColor(T["border"]), 1))
        p.drawLine(QPointF(HEADER_W, 0), QPointF(HEADER_W, h))
        for lane, text in ((NEW_TOP, "+ Layer"), (NEW_BOTTOM, "+ Audio")):
            y = self.lane_y(lane)
            if y + LANE_H < RULER_H or y > h:
                continue
            p.setPen(QColor(T["accent"]))
            p.drawText(QRectF(10, y, HEADER_W - 14, LANE_H), Qt.AlignVCenter | Qt.AlignLeft, text)
        for i, tr in enumerate(self.project.tracks):
            y = self.track_y(i)
            if y + TRACK_H < RULER_H or y > h:
                continue
            p.setPen(QPen(QColor(T["border"]), 1))
            p.drawLine(QPointF(0, y + TRACK_H), QPointF(HEADER_W, y + TRACK_H))
            ttype = tr.get("type") or ("audio" if tr["kind"] == "audio" else "video")
            p.fillRect(QRectF(0, y + 6, 3, TRACK_H - 12), QColor(KIND_COLORS.get(ttype, "#888")))   # type colour stripe
            key = ({"video": "film", "image": "image", "text": "text", "audio": "music"}[ttype], T["muted"])
            icon = self._icons.get(key) or self._icons.setdefault(key, theme.pixmap(key[0], key[1], 16))
            p.drawPixmap(12, int(y + 8), icon)
            p.setPen(QColor(T["text"]))
            f = p.font(); f.setBold(True); p.setFont(f)
            p.drawText(QRectF(36, y + 6, HEADER_W - 40, 20), Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetrics(f).elidedText(tr["name"], Qt.ElideRight, HEADER_W - 42))
            f.setBold(False); p.setFont(f)
            self._draw_switches(p, i, tr)
            if self._tdrag and self._tdrag["moved"] and self._tdrag["src"] == i:      # the layer being dragged
                veil = QColor(T["accent"]); veil.setAlpha(45)
                p.fillRect(QRectF(0, y, w, TRACK_H), veil)
        if self._tdrag and self._tdrag["moved"]:                                       # drop position line
            src, tgt = self._tdrag["src"], self._tdrag["target"]
            if tgt != src:
                ly = self.track_y(tgt) + (TRACK_H if tgt > src else 0)
                p.setPen(QPen(QColor(T["accent"]), 3))
                p.drawLine(QPointF(0, ly), QPointF(w, ly))
        p.restore()

        self._draw_ruler(p)

    def _draw_switches(self, p, i, tr):
        """The hide / lock / mute buttons in a layer's header."""
        T = theme.T
        for key in self.switches_for(i):
            on = bool(tr.get(key))
            off_icon, on_icon, _, _ = SWITCH_UI[key]
            r = self.switch_rect(i, key)
            dead = key == "muted" and not on and not self.project.track_has_sound(i)   # nothing to mute here
            if on:
                bg = QColor(T["accent"]); bg.setAlpha(70)
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(r, 5, 5)
            col = T["accent"] if on else (T["faint"] if dead else T["muted"])
            name = on_icon if on else off_icon
            ik = (name, col, BTN)
            pm = self._icons.get(ik) or self._icons.setdefault(ik, theme.pixmap(name, col, 14))
            p.drawPixmap(int(r.x() + (BTN - 14) / 2), int(r.y() + (BTN - 14) / 2), pm)

    def _paint_playhead(self, p):
        T = theme.T
        if not self.project:
            return
        w = self.width()
        x = self.t2x(self.playhead)
        if HEADER_W <= x <= w:
            col = QColor("#FFFFFF" if theme.NAME == "dark" else T["text"])      # CapCut: a plain white playhead
            p.setPen(QPen(col, 1.5))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawPolygon(QPolygonF([QPointF(x - 7, 0), QPointF(x + 7, 0), QPointF(x + 7, 10), QPointF(x, 18), QPointF(x - 7, 10)]))

    def _draw_ruler(self, p):
        T = theme.T
        w = self.width()
        p.fillRect(QRectF(0, 0, w, RULER_H), QColor(T["panel"]))
        p.setPen(QPen(QColor(T["border_strong"]), 1))
        p.drawLine(QPointF(0, RULER_H), QPointF(w, RULER_H))
        step = ruler_step(self.pps)
        first = math.floor(self.scroll / step) * step
        t = first
        p.save()
        p.setClipRect(QRectF(HEADER_W, 0, w - HEADER_W, RULER_H))
        small = QFontMetrics(p.font())
        while self.t2x(t) < w + 40:
            x = self.t2x(t)
            p.setPen(QPen(QColor(T["muted"]), 1))
            p.drawLine(QPointF(x, RULER_H - 12), QPointF(x, RULER_H))
            p.drawText(QPointF(x + 4, RULER_H - 14), label_time(max(0, t), step))
            for k in range(1, 5):
                xm = self.t2x(t + step * k / 5)
                p.setPen(QPen(QColor(T["faint"]), 1))
                p.drawLine(QPointF(xm, RULER_H - 5), QPointF(xm, RULER_H))
            t += step
        p.restore()

    def _ripple_shift(self, c):
        """Preview offset (seconds) for clips that follow the one being ripple-trimmed."""
        d = self._drag
        if d and d["moved"] and d["ripple"] and d["id"] != c.id and c.track == d["track0"] \
                and c.start >= d["start0"] + d["dur0"] - 1e-9:
            return d["shift"]
        return 0.0

    WAVE_STEP = 2.0          # pixels per waveform bar

    def _draw_wave(self, p, c, m, r, in_point):
        """Draw the clip's sound: bars at the height of the loudest sample in each ~2 px slice of the clip.

        Each bar looks up the source time it stands for (``in_point`` plus speed / reverse), so a retimed or slipped
        clip shows the right part of the waveform; the clip's volume and its fades scale the bars, so what you
        see is what you hear. Only the visible slice of a long clip is walked."""
        audio = m.kind == "audio"
        wf = self.waves.get(m.path)
        if audio:
            y0, y1 = r.top() + 15, r.bottom() - 2
        else:
            y1 = r.bottom() - 2
            y0 = y1 - min(18.0, max(0.0, r.height() - 34))         # a strip, so the thumbnails stay readable
        if y1 - y0 < 6 or r.width() < 3 or (wf is None and not audio):
            return
        if wf is None:                                             # still decoding (or no sound): a quiet centre line
            p.setPen(QPen(QColor(255, 255, 255, 70), 1.5))
            p.drawLine(QPointF(r.left() + 3, (y0 + y1) / 2), QPointF(r.right() - 3, (y0 + y1) / 2))
            return
        step = self.WAVE_STEP
        x_hi = min(r.right(), float(self.width()))
        k0 = max(0, int((max(r.left(), HEADER_W) - r.left()) // step))
        x = r.left() + k0 * step
        if x >= x_hi:
            return
        if not audio:
            p.fillRect(QRectF(max(r.left(), HEADER_W), y0, x_hi - max(r.left(), HEADER_W), y1 - y0), QColor(0, 0, 0, 80))
        half = (y1 - y0) / 2
        mid = (y0 + y1) / 2
        p.setPen(QPen(QColor(255, 255, 255, 165 if audio else 150), max(1.0, step - 0.6), Qt.SolidLine, Qt.FlatCap))
        dur, speed, rev = c.duration, c.speed, c.reverse
        top = in_point + c.span

        def src(tc):                                               # source time shown tc s into the clip
            tc = min(max(0.0, tc), dur)
            return top - tc * speed if rev else in_point + tc * speed

        while x < x_hi:
            ta = (x - r.left()) / self.pps
            tb = ta + step / self.pps
            v = wf.peak(src(ta), src(tb)) * c.volume * c.fade_gain((ta + tb) / 2)
            v = min(1.0, v)
            cx = x + step / 2
            if audio:
                h = max(0.6, v * (half - 1))
                p.drawLine(QPointF(cx, mid - h), QPointF(cx, mid + h))
            else:
                p.drawLine(QPointF(cx, y1), QPointF(cx, y1 - max(0.6, v * (y1 - y0 - 1))))
            x += step

    def _draw_fades(self, p, c, r):
        """Audio fade wedges: the part of the clip that is turned down is shaded, with the volume ramp as a line."""
        fi, fo = c.fades()
        top, bot = r.top() + 14, r.bottom() - 1
        shade, line = QColor(255, 255, 255, 40), QPen(QColor(255, 255, 255, 215), 1.5)
        for w, left_side in ((fi * self.pps, True), (fo * self.pps, False)):
            w = min(r.width(), w)
            if w < 1:
                continue
            if left_side:
                tri = QPolygonF([QPointF(r.left(), top), QPointF(r.left() + w, top), QPointF(r.left(), bot)])
                a, b = QPointF(r.left(), bot), QPointF(r.left() + w, top)
            else:
                tri = QPolygonF([QPointF(r.right(), top), QPointF(r.right() - w, top), QPointF(r.right(), bot)])
                a, b = QPointF(r.right() - w, top), QPointF(r.right(), bot)
            p.setPen(Qt.NoPen)
            p.setBrush(shade)
            p.drawPolygon(tri)
            p.setPen(line)
            p.drawLine(a, b)
        p.setBrush(Qt.NoBrush)

    def _draw_clip(self, p, c):
        T = theme.T
        d = self._drag if (self._drag and self._drag["id"] == c.id and self._drag["moved"]) else None
        g = self._drag if (self._drag and self._drag["moved"] and self._drag.get("group") and c.id in self._drag["group"]
                           and not d) else None
        m = self.project.media.get(c.media_id)
        if m is None:
            return
        shift = self._ripple_shift(c)
        if d:
            r = self.clip_rect(c, d["start"], d["dur"], d["track"])
        elif g:                                            # a companion of the clip being dragged: same shift
            r = self.clip_rect(c, c.start + g["delta"])
        else:
            r = self.clip_rect(c, c.start + shift if shift else None)
        in_point = d["in"] if d and d["zone"] == "slip" else c.in_point
        if r.right() < HEADER_W or r.left() > self.width():
            return
        col = QColor(KIND_COLORS.get(m.kind, "#888"))
        body = QColor(col); body.setAlpha(235 if c.id in self.selection else 200)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(body)
        p.drawPath(path)

        p.save()
        p.setClipPath(path)
        th = self.thumbs.get(c.media_id)
        if th is not None and not th.isNull() and m.kind != "audio":
            ph = r.height() - 16
            tw = ph * th.width() / max(1, th.height())
            x0 = r.left() + 4
            if tw > 1:                                   # skip tiles far outside the view
                x0 += max(0, int((HEADER_W - tw - x0) // tw)) * tw
            stop = min(r.right() - 2, self.width() + tw)
            x = x0
            p.setOpacity(0.9)
            while x < stop:
                p.drawPixmap(QRectF(x, r.top() + 14, tw, ph), th, QRectF(th.rect()))
                x += tw
            p.setOpacity(1.0)
        if m.kind in ("audio", "video"):                 # the real waveform (a strip along the bottom of a video clip)
            self._draw_wave(p, c, m, r, in_point)
        fm = QFontMetrics(p.font())
        if c.title is not None:                          # title: show its text in the clip body
            body_txt = " ".join(c.title.get("text", "").split()) or "(empty title)"
            base_font = p.font()
            f = QFont(base_font); f.setBold(True)
            if base_font.pointSizeF() > 0:
                f.setPointSizeF(base_font.pointSizeF() + 3)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(QRectF(r.left() + 8, r.top() + 14, max(0, r.width() - 14), r.height() - 14),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetrics(f).elidedText(body_txt, Qt.ElideRight, int(max(0, r.width() - 16))))
            p.setFont(base_font)
        label = "Title" if c.title is not None else m.name
        # speed / reverse badge, e.g. "2×" or "◀ 0.5×" (shown when the clip plays at another speed or backwards)
        badge = ""
        if c.retimed():
            badge = ("◀ " if c.reverse else "") + (f"{c.speed:g}×" if abs(c.speed - 1.0) > 1e-9 else "")
            badge = badge.strip()
        badge_w = fm.horizontalAdvance(badge) + 10 if badge and r.width() > 60 else 0
        # label bar
        bar = QColor(0, 0, 0, 70)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), 14), bar)
        p.setPen(QColor("white"))
        p.drawText(QRectF(r.left() + 6, r.top(), max(0, r.width() - 10 - badge_w), 14), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(label, Qt.ElideRight, int(max(0, r.width() - 12 - badge_w))))
        if badge_w:
            pill = QRectF(r.right() - badge_w - 2, r.top() + 1, badge_w, 12)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 215))
            p.drawRoundedRect(pill, 6, 6)
            p.setPen(QColor(20, 20, 24))
            p.drawText(pill, Qt.AlignCenter, badge)
        if c.fade_in > 0 or c.fade_out > 0:               # audio fades: shaded wedge + line over the faded part
            self._draw_fades(p, c, r)
        if c.animation:                                   # In / Out ramps (or a Combo bar) along the clip's bottom edge
            d_in, d_out = anim_core.durations(c.animation, c.duration)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 105))
            bot = r.bottom() - 1
            if d_in > 0:
                w = min(r.width(), d_in * self.pps)
                p.drawPolygon(QPolygonF([QPointF(r.left(), bot), QPointF(r.left() + w, bot),
                                         QPointF(r.left() + w, bot - 9)]))
            if d_out > 0:
                w = min(r.width(), d_out * self.pps)
                p.drawPolygon(QPolygonF([QPointF(r.right() - w, bot), QPointF(r.right(), bot),
                                         QPointF(r.right() - w, bot - 9)]))
            if c.animation.get("combo"):
                p.drawRect(QRectF(r.left(), bot - 3, r.width(), 3))
        if c.transition:                                  # transition into this clip: a bar over its first seconds + a bow-tie
            prev = self.project.transition_prev(c)
            live = prev is not None
            dur = min(c.transition["dur"], c.duration)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 120 if live else 45))
            p.drawRect(QRectF(r.left(), r.top() + 14, min(r.width(), dur * self.pps), 3))
            cx, cy, k = r.left(), r.center().y() + 4, 6
            p.setBrush(QColor(255, 255, 255, 235 if live else 110))
            p.setPen(QPen(QColor(0, 0, 0, 150), 1))
            p.drawPolygon(QPolygonF([QPointF(cx, cy), QPointF(cx - k, cy - k), QPointF(cx - k, cy + k)]))
            p.drawPolygon(QPolygonF([QPointF(cx, cy), QPointF(cx + k, cy - k), QPointF(cx + k, cy + k)]))
        if self.project.track_hidden(c.track):            # hidden layer: the clip fades into the row
            veil = QColor(T["surface"]); veil.setAlpha(165)
            p.fillRect(r, veil)
        if self.project.track_locked(c.track):            # locked layer: diagonal hatching
            p.setPen(QPen(QColor(255, 255, 255, 38), 1.5))
            hh = r.height()
            x = max(r.left(), HEADER_W) - hh
            stop = min(r.right(), self.width())
            while x < stop:
                p.drawLine(QPointF(x, r.bottom()), QPointF(x + hh, r.top()))
                x += 9
        p.restore()

        sel = c.id in self.selection
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("white") if sel else QColor(0, 0, 0, 60), 2 if sel else 1))
        p.drawPath(path)

        # keyframe diamonds — drawn on top of the clip body (clipped to the clip rect)
        if c.keyframes:
            p.save()
            p.setClipPath(path)
            kf_y = self._kf_y(r)
            for kf in c.keyframes:
                kx = self.t2x(c.start + self._kf_shown_t(c, kf))
                if r.left() - 8 <= kx <= r.right() + 8:
                    on = self._kf_is_selected(c, kf)
                    rad = 7 if on else 6
                    diamond = QPolygonF([
                        QPointF(kx,       kf_y - rad),
                        QPointF(kx + rad, kf_y),
                        QPointF(kx,       kf_y + rad),
                        QPointF(kx - rad, kf_y),
                    ])
                    # Dark border for high visibility against any video clip colour; the selected one is white
                    p.setPen(QPen(QColor(0, 0, 0, 220), 1.5))
                    p.setBrush(QColor("#FFFFFF") if on else QColor("#FFCC00"))
                    p.drawPolygon(diamond)
            p.restore()

        if d and d["zone"] == "slip":     # live in-point read-out while slipping
            label = f"Slip  in {in_point:.2f}s  ({in_point - d['in0']:+.2f}s)"
            fm = QFontMetrics(p.font())
            w = min(r.width() - 8, fm.horizontalAdvance(label) + 16)
            if w > 30:
                pill = QRectF(r.center().x() - w / 2, r.center().y() - 2, w, 18)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 170))
                p.drawRoundedRect(pill, 9, 9)
                p.setPen(QColor("white"))
                p.drawText(pill, Qt.AlignCenter, fm.elidedText(label, Qt.ElideRight, int(w - 8)))
        if sel and r.width() > 20:        # trim handles
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawRoundedRect(QRectF(r.left() + 1.5, r.center().y() - 10, 3.5, 20), 1.7, 1.7)
            p.drawRoundedRect(QRectF(r.right() - 5, r.center().y() - 10, 3.5, 20), 1.7, 1.7)


class TimelineWidget(QWidget):
    """TimelineCanvas + horizontal (time) and vertical (layers) scroll bars."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = TimelineCanvas(self)
        self.bar = QScrollBar(Qt.Horizontal, self)
        self.vbar = QScrollBar(Qt.Vertical, self)
        self.vbar.setVisible(False)                        # only shown once the layers don't all fit
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.canvas, 1)
        row.addWidget(self.vbar)
        lay.addLayout(row, 1)
        lay.addWidget(self.bar)
        self.canvas.viewChanged.connect(self._sync_bar)
        self.bar.valueChanged.connect(lambda v: self.canvas.set_scroll(v / 100.0))
        self.vbar.valueChanged.connect(lambda v: self.canvas.set_vscroll(float(v)))

    def _sync_bar(self):
        c = self.canvas
        total, view = c.content_seconds(), c.view_seconds()
        self.bar.blockSignals(True)
        self.bar.setRange(0, int(max(0.0, total - view) * 100))
        self.bar.setPageStep(int(view * 100))
        self.bar.setSingleStep(int(view * 10))
        self.bar.setValue(int(c.scroll * 100))
        self.bar.blockSignals(False)
        rng = int(max(0.0, c.content_height() - c.view_height()))
        self.vbar.blockSignals(True)
        self.vbar.setRange(0, rng)
        self.vbar.setPageStep(int(c.view_height()))
        self.vbar.setSingleStep(TRACK_H // 2)
        self.vbar.setValue(int(c.vscroll))
        self.vbar.blockSignals(False)
        self.vbar.setVisible(rng > 0)
