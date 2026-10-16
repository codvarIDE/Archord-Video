"""Multi-track timeline widget: ruler, tracks, clips, playhead, trimming, snapping, drag & drop."""
import math

from PyQt5.QtCore import Qt, QRectF, QPointF, QRect, pyqtSignal, QByteArray
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFontMetrics, QPolygonF, QPixmap
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QScrollBar

from . import theme
from ..core.project import MIN_CLIP

MIME_MEDIA = "application/x-venma-media"
HEADER_W = 84
RULER_H = 28
TRACK_H = 58
EDGE = 7              # px grab zone for trimming
SNAP_PX = 9
KIND_COLORS = {"video": "#D97757", "image": "#C79A3B", "audio": "#4F86B5"}
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
    clipTrimmed = pyqtSignal(str, str, float)    # id, "left" | "right", new edge time
    splitRequested = pyqtSignal(str, float)
    mediaDropped = pyqtSignal(str, int, float)   # media id, track, time
    viewChanged = pyqtSignal()
    statusMessage = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.thumbs = {}            # media id -> QPixmap
        self.pps = 80.0             # pixels per second
        self.scroll = 0.0           # seconds at the left edge of the track area
        self.playhead = 0.0
        self.selected = None
        self.tool = "select"
        self.snap_on = True
        self._drag = None           # dict describing the active drag
        self._scrub = False
        self._pan = None
        self._drop = None           # (track, time) while dragging media over
        self._icons = {}
        self._cache = None          # pre-rendered tracks/clips/ruler (playhead is drawn on top)
        self._cache_key = None
        self._dirty = True
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(RULER_H + 4 * TRACK_H + 6)

    def invalidate(self):
        """Something static changed (clips, zoom, selection, theme): re-render the cached layer."""
        self._dirty = True
        QWidget.update(self)

    # ── data ─────────────────────────────────────────────────────────────────
    def set_project(self, project):
        self.project = project
        self.setMinimumHeight(RULER_H + len(project.tracks) * TRACK_H + 6)
        if self.selected and not project.clip(self.selected):
            self.selected = None
            self.clipSelected.emit(None)
        self.viewChanged.emit()
        self.invalidate()

    def select(self, clip_id):
        self.selected = clip_id
        self.clipSelected.emit(clip_id)
        self.invalidate()

    # ── coordinate helpers ───────────────────────────────────────────────────
    def t2x(self, t):
        return HEADER_W + (t - self.scroll) * self.pps

    def x2t(self, x):
        return self.scroll + (x - HEADER_W) / self.pps

    def track_y(self, i):
        return RULER_H + i * TRACK_H

    def track_at(self, y):
        if not self.project:
            return -1
        i = int((y - RULER_H) // TRACK_H)
        return i if 0 <= i < len(self.project.tracks) else -1

    def view_seconds(self):
        return max(0.1, (self.width() - HEADER_W) / self.pps)

    def content_seconds(self):
        end = self.project.duration() if self.project else 0.0
        return max(end + 15.0, self.view_seconds())

    def clip_rect(self, c, start=None, dur=None, track=None):
        start = c.start if start is None else start
        dur = c.duration if dur is None else dur
        track = c.track if track is None else track
        return QRectF(self.t2x(start), self.track_y(track) + 3, max(2.0, dur * self.pps), TRACK_H - 6)

    # ── view control ─────────────────────────────────────────────────────────
    def set_scroll(self, s):
        s = max(0.0, min(s, max(0.0, self.content_seconds() - self.view_seconds())))
        if abs(s - self.scroll) > 1e-9:
            self.scroll = s
            self.invalidate()
            self.viewChanged.emit()

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
        self.setCursor({"hand": Qt.OpenHandCursor, "razor": Qt.CrossCursor, "zoom": Qt.CrossCursor}.get(tool, Qt.ArrowCursor))

    # ── snapping ─────────────────────────────────────────────────────────────
    def _snap_points(self, ignore=None):
        pts = [0.0, self.playhead]
        for c in self.project.clips:
            if c.id != ignore:
                pts += [c.start, c.end]
        return pts

    def snap(self, t, ignore=None, also=()):
        """Snap t (and optionally t+offset for each offset in ``also``) to nearby edges / playhead."""
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
        if e.button() != Qt.LeftButton:
            return
        if pos.y() < RULER_H and pos.x() >= HEADER_W:
            self._scrub = True
            self._seek_to_x(pos.x())
            return
        if self.tool == "zoom":
            self.zoom_by(0.8 if e.modifiers() & Qt.AltModifier else 1.25, pos.x())
            return
        c, zone = self.hit(pos)
        if self.tool == "razor":
            if c:
                t = self.snap(self.x2t(pos.x()), ignore=c.id)
                self.splitRequested.emit(c.id, t)
            return
        if c is None:
            if self.selected:
                self.select(None)
            if pos.x() >= HEADER_W:
                self._scrub = True
                self._seek_to_x(pos.x())
            return
        if self.selected != c.id:
            self.select(c.id)
        self._drag = dict(id=c.id, zone=zone, grab=self.x2t(pos.x()) - c.start, start0=c.start, dur0=c.duration,
                          track0=c.track, start=c.start, dur=c.duration, track=c.track, moved=False,
                          press=pos)

    def mouseMoveEvent(self, e):
        if not self.project:
            return
        pos = e.pos()
        if self._pan is not None:
            dx = (e.globalPos().x() - self._pan[0]) / self.pps
            self.set_scroll(self._pan[1] - dx)
            return
        if self._scrub:
            self._seek_to_x(pos.x())
            return
        d = self._drag
        if d is None:
            c, zone = self.hit(pos)
            if self.tool == "select":
                self.setCursor(Qt.SizeHorCursor if zone in ("left", "right") else
                               Qt.OpenHandCursor if zone == "body" else Qt.ArrowCursor)
            return
        if not d["moved"] and (pos - d["press"]).manhattanLength() < 3:
            return
        d["moved"] = True
        c = self.project.clip(d["id"])
        t = self.x2t(pos.x())
        if d["zone"] == "body":
            start = self.snap(t - d["grab"], ignore=c.id, also=(c.duration,))
            track = self.track_at(pos.y())
            m = self.project.media[c.media_id]
            if track < 0 or not self.project.track_accepts(track, m.kind):
                track = d["track0"]
            d["track"] = track
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
        if self._pan is not None:
            self._pan = None
            self.set_tool(self.tool)
            return
        if self._scrub:
            self._scrub = False
            return
        d, self._drag = self._drag, None
        if not d or not d["moved"]:
            self.invalidate()
            return
        if d["zone"] == "body":
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
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.2 if dy > 0 else 1 / 1.2, e.pos().x())
        else:
            self.set_scroll(self.scroll - (dy / 120.0) * 60.0 / self.pps)
        e.accept()

    def resizeEvent(self, e):
        self.viewChanged.emit()
        super().resizeEvent(e)

    # ── drag & drop from the media bin ───────────────────────────────────────
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_MEDIA):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_MEDIA):
            self._drop = (self.track_at(e.pos().y()), max(0.0, self.snap(self.x2t(e.pos().x()))))
            self.invalidate()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drop = None
        self.invalidate()

    def dropEvent(self, e):
        self._drop = None
        if e.mimeData().hasFormat(MIME_MEDIA):
            mid = bytes(e.mimeData().data(MIME_MEDIA)).decode("utf-8")
            t = max(0.0, self.snap(self.x2t(e.pos().x())))
            self.mediaDropped.emit(mid, self.track_at(e.pos().y()), t)
            e.acceptProposedAction()
        self.invalidate()

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
        self._paint_playhead(p)

    def _paint_static(self, p):
        T = theme.T
        p.fillRect(self.rect(), QColor(T["sunken"]))
        if not self.project:
            return
        w = self.width()
        n = len(self.project.tracks)

        # track rows
        for i, tr in enumerate(self.project.tracks):
            y = self.track_y(i)
            p.fillRect(QRectF(HEADER_W, y, w - HEADER_W, TRACK_H), QColor(T["surface"] if i % 2 == 0 else T["panel"]))
            p.setPen(QPen(QColor(T["border"]), 1))
            p.drawLine(HEADER_W, y + TRACK_H, w, y + TRACK_H)

        # clips
        p.save()
        p.setClipRect(QRectF(HEADER_W, RULER_H, w - HEADER_W, n * TRACK_H))
        for c in self.project.clips:
            self._draw_clip(p, c)
        if self._drop and self._drop[0] >= 0:                          # drop indicator
            x = self.t2x(self._drop[1])
            p.setPen(QPen(QColor(T["accent"]), 2, Qt.DashLine))
            p.drawLine(QPointF(x, self.track_y(self._drop[0])), QPointF(x, self.track_y(self._drop[0]) + TRACK_H))
        p.restore()

        # headers
        p.fillRect(QRectF(0, 0, HEADER_W, self.height()), QColor(T["panel"]))
        p.setPen(QPen(QColor(T["border"]), 1))
        p.drawLine(HEADER_W, 0, HEADER_W, self.height())
        for i, tr in enumerate(self.project.tracks):
            y = self.track_y(i)
            p.setPen(QPen(QColor(T["border"]), 1))
            p.drawLine(0, y + TRACK_H, HEADER_W, y + TRACK_H)
            key = ("film" if tr["kind"] == "video" else "music", T["muted"])
            icon = self._icons.get(key) or self._icons.setdefault(key, theme.pixmap(key[0], key[1], 16))
            p.drawPixmap(10, y + TRACK_H // 2 - 8, icon)
            p.setPen(QColor(T["text"]))
            f = p.font(); f.setBold(True); p.setFont(f)
            p.drawText(QRectF(34, y, HEADER_W - 38, TRACK_H), Qt.AlignVCenter | Qt.AlignLeft, tr["name"])
            f.setBold(False); p.setFont(f)

        self._draw_ruler(p)

    def _paint_playhead(self, p):
        T = theme.T
        if not self.project:
            return
        w = self.width()
        x = self.t2x(self.playhead)
        if HEADER_W <= x <= w:
            col = QColor(T["accent"])
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
        p.drawLine(0, RULER_H, w, RULER_H)
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

    def _draw_clip(self, p, c):
        T = theme.T
        d = self._drag if (self._drag and self._drag["id"] == c.id and self._drag["moved"]) else None
        m = self.project.media.get(c.media_id)
        if m is None:
            return
        r = self.clip_rect(c, d["start"] if d else None, d["dur"] if d else None, d["track"] if d else None)
        if r.right() < HEADER_W or r.left() > self.width():
            return
        col = QColor(KIND_COLORS.get(m.kind, "#888"))
        body = QColor(col); body.setAlpha(235 if c.id == self.selected else 200)
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
        elif m.kind == "audio":
            p.setPen(QPen(QColor(255, 255, 255, 90), 2))
            mid, amp = r.center().y() + 6, 9
            x = r.left() + 4
            x += max(0, int((HEADER_W - x) // 4)) * 4
            i = 0
            while x < min(r.right(), self.width() + 4):
                h = amp * (0.35 + 0.65 * abs(math.sin((x + c.in_point * self.pps) * 0.09) * math.cos((x) * 0.031)))
                p.drawLine(QPointF(x, mid - h), QPointF(x, mid + h))
                x += 4
                i += 1
        # label bar
        bar = QColor(0, 0, 0, 70)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), 14), bar)
        p.setPen(QColor("white"))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(r.left() + 6, r.top(), max(0, r.width() - 10), 14), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(m.name, Qt.ElideRight, int(max(0, r.width() - 12))))
        p.restore()

        sel = c.id == self.selected
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("white") if sel else QColor(0, 0, 0, 60), 2 if sel else 1))
        p.drawPath(path)
        if sel and r.width() > 20:        # trim handles
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawRoundedRect(QRectF(r.left() + 1.5, r.center().y() - 10, 3.5, 20), 1.7, 1.7)
            p.drawRoundedRect(QRectF(r.right() - 5, r.center().y() - 10, 3.5, 20), 1.7, 1.7)


class TimelineWidget(QWidget):
    """TimelineCanvas + horizontal scroll bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = TimelineCanvas(self)
        self.bar = QScrollBar(Qt.Horizontal, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.bar)
        self.canvas.viewChanged.connect(self._sync_bar)
        self.bar.valueChanged.connect(lambda v: self.canvas.set_scroll(v / 100.0))

    def _sync_bar(self):
        c = self.canvas
        total, view = c.content_seconds(), c.view_seconds()
        self.bar.blockSignals(True)
        self.bar.setRange(0, int(max(0.0, total - view) * 100))
        self.bar.setPageStep(int(view * 100))
        self.bar.setSingleStep(int(view * 10))
        self.bar.setValue(int(c.scroll * 100))
        self.bar.blockSignals(False)
