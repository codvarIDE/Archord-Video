"""Program monitor + playback engine.

Why this replaced the QMediaPlayer/QVideoWidget version
-------------------------------------------------------
QVideoWidget renders through the platform's media backend (DirectShow / Media Foundation /
GStreamer). When a codec or the native window handle is missing it shows a plain BLACK
rectangle, and two QMediaPlayers seeking and re-loading on every clip change made playback lag.

This engine decodes with FFmpeg instead (which already has to be installed for import/export),
so any file FFmpeg can read shows up, on every OS, with no codec packs:

  * one QThread per video stream  -> raw RGB frames in a small bounded queue
  * one QThread per audio stream  -> PCM chunks in a bounded queue, played through QAudioOutput
  * one QThread for still frames  -> instant scrubbing / frame-stepping (latest request wins, LRU cache)
  * the next clip is pre-decoded ~1 s ahead, so cuts between clips don't hitch
  * the UI thread only paints ready-made frames; it never waits for decoding

The playhead clock is a monotonic clock that starts only when the first video frame and the first
audio chunk are ready, so picture and sound begin together.
"""
import math
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict, deque

from PyQt5.QtCore import Qt, QObject, QThread, QTimer, QRect, QRectF, QPointF, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QImage, QPen, QBrush
from PyQt5.QtWidgets import QWidget

from ..core import ffmpeg_tools as ft
from . import theme
from .titles import draw_titles, layout as title_layout

try:
    from PyQt5.QtMultimedia import QAudio, QAudioFormat, QAudioOutput, QAudioDeviceInfo
    HAS_MULTIMEDIA = True            # now only needed for sound; video no longer depends on it
except Exception:
    HAS_MULTIMEDIA = False

_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


# ══ view ══════════════════════════════════════════════════════════════════════════════════════
class PreviewView(QWidget):
    """Paints the current frame, letter-boxed to the project's aspect ratio.

    Titles can be edited directly on the picture: drag a title to move it, drag a corner handle of the
    selected title to resize it. The view never edits the project itself; it emits signals and the
    main window applies them (so undo, the Properties panel and saving all stay in one place).
    """

    clipDragged = pyqtSignal(str, object)       # picture (image / video) clip id, {pos_x, pos_y} or {scale}  (live)
    titlePicked = pyqtSignal(str)               # clip id of the title / picture that was clicked (select it)
    titleDragged = pyqtSignal(str, object)      # clip id, {pos_x, pos_y} or {size}  (live, while dragging)
    titleGestureDone = pyqtSignal(str, str)     # clip id, undo label (mouse released after a real change)

    HANDLE = 6                                   # half-size of a resize handle, in pixels
    SNAP = 0.012                                 # snap-to-centre distance, as a fraction of the frame

    def __init__(self, parent=None):
        super().__init__(parent)
        self.aspect = 16 / 9
        self.img = None
        self.titles_provider = None          # callable -> title clips visible at the current time
        self.selected_provider = None        # callable -> id of the selected clip (or None)
        self.layers_provider = None          # callable -> [(clip, (cx, cy, w, h) as frame fractions)] bottom first
        self._drag = None                    # active gesture, see mousePressEvent
        self._guides = (False, False)        # show the vertical / horizontal centre guide
        self.setMinimumSize(200, 120)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)

    def set_aspect(self, w, h):
        self.aspect = max(0.1, w / max(1, h))
        self.update()

    def _frame_rect(self):
        m = 10
        aw, ah = max(1, self.width() - 2 * m), max(1, self.height() - 2 * m)
        w, h = aw, int(aw / self.aspect)
        if h > ah:
            h, w = ah, int(ah * self.aspect)
        return QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def set_image(self, img):
        self.img = img
        self.update(self._frame_rect())

    def show_black(self):
        if self.img is not None:
            self.img = None
            self.update(self._frame_rect())

    # ── title geometry / hit testing ─────────────────────────────────────────────────────────
    def _title_boxes(self):
        """[(clip, box QRectF)] for visible, non-empty titles, bottom-most first."""
        if self.titles_provider is None:
            return []
        frame = QRectF(self._frame_rect())
        out = []
        for c in self.titles_provider() or []:
            lay = title_layout(c.title, frame)
            if lay is not None:
                out.append((c, lay[2]))
        return out

    def _layer_boxes(self):
        """[(clip, box QRectF)] for the picture layers visible now (images / videos), bottom-most first."""
        if self.layers_provider is None:
            return []
        f = QRectF(self._frame_rect())
        out = []
        for c, (cx, cy, w, h) in self.layers_provider() or []:
            out.append((c, QRectF(f.x() + (cx - w / 2) * f.width(), f.y() + (cy - h / 2) * f.height(),
                                  w * f.width(), h * f.height())))
        return out

    def _selected_box(self):
        sid = self.selected_provider() if self.selected_provider else None
        if not sid:
            return None, None
        for c, box in self._title_boxes() + self._layer_boxes():
            if c.id == sid:
                return c, box
        return None, None

    def _corners(self, box):
        return {"tl": box.topLeft(), "tr": box.topRight(), "bl": box.bottomLeft(), "br": box.bottomRight()}

    def _handle_at(self, pos):
        c, box = self._selected_box()
        if c is None:
            return None, None
        r = self.HANDLE + 3
        for name, pt in self._corners(box).items():
            if abs(pos.x() - pt.x()) <= r and abs(pos.y() - pt.y()) <= r:
                return c, name
        return None, None

    def _title_at(self, pos):
        """Top-most title, else top-most picture layer, under ``pos`` (titles always sit above pictures)."""
        for c, box in reversed(self._title_boxes()):          # top-most first
            if box.adjusted(-4, -4, 4, 4).contains(pos):
                return c
        for c, box in reversed(self._layer_boxes()):
            if box.adjusted(-4, -4, 4, 4).contains(pos):
                return c
        return None

    # ── mouse ────────────────────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        pos = QPointF(e.pos())
        frame = QRectF(self._frame_rect())
        c, corner = self._handle_at(pos)
        if c is not None:
            _, box = self._selected_box()
            center = box.center()
            d0 = max(4.0, math.hypot(pos.x() - center.x(), pos.y() - center.y()))
            pic = c.title is None
            self._drag = dict(mode="resize", id=c.id, center=center, d0=d0, pic=pic,
                              size0=float(c.scale if pic else c.title["size"]), changed=False)
            return
        c = self._title_at(pos)
        if c is not None:
            self.titlePicked.emit(c.id)
            pic = c.title is None
            self._drag = dict(mode="move", id=c.id, start=pos, pic=pic,
                              x0=float(c.pos_x if pic else c.title["pos_x"]),
                              y0=float(c.pos_y if pic else c.title["pos_y"]), frame=frame, changed=False)
            self.setCursor(Qt.SizeAllCursor)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = QPointF(e.pos())
        d = self._drag
        if d is None:
            self._update_cursor(pos)
            return super().mouseMoveEvent(e)
        if d["mode"] == "move":
            f = d["frame"]
            nx = d["x0"] + (pos.x() - d["start"].x()) / max(1.0, f.width())
            ny = d["y0"] + (pos.y() - d["start"].y()) / max(1.0, f.height())
            gx = gy = False
            if not (e.modifiers() & Qt.AltModifier):              # hold Alt to move without snapping
                if abs(nx - 0.5) < self.SNAP:
                    nx, gx = 0.5, True
                if abs(ny - 0.5) < self.SNAP:
                    ny, gy = 0.5, True
            lo, hi = (-0.5, 1.5) if d["pic"] else (0.0, 1.0)      # a picture may be pushed partly off the frame
            nx, ny = min(hi, max(lo, nx)), min(hi, max(lo, ny))
            self._guides = (gx, gy)
            d["changed"] = True
            vals = {"pos_x": round(nx, 4), "pos_y": round(ny, 4)}
            (self.clipDragged if d["pic"] else self.titleDragged).emit(d["id"], vals)
        else:
            ctr = d["center"]
            dist = math.hypot(pos.x() - ctr.x(), pos.y() - ctr.y())
            d["changed"] = True
            if d["pic"]:
                self.clipDragged.emit(d["id"], {"scale": round(min(10.0, max(0.05, d["size0"] * dist / d["d0"])), 4)})
            else:
                size = int(round(min(600.0, max(8.0, d["size0"] * dist / d["d0"]))))
                self.titleDragged.emit(d["id"], {"size": size})
        self.update()

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        self._guides = (False, False)
        if d is not None and e.button() == Qt.LeftButton:
            if d["changed"]:
                what = "Clip" if d["pic"] else "Title"
                self.titleGestureDone.emit(d["id"], f"{'Move' if d['mode'] == 'move' else 'Resize'} {what}")
            self._update_cursor(QPointF(e.pos()))
            self.update()
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        if self._drag is None:
            self.unsetCursor()
        super().leaveEvent(e)

    def _update_cursor(self, pos):
        c, corner = self._handle_at(pos)
        if c is not None:
            self.setCursor(Qt.SizeFDiagCursor if corner in ("tl", "br") else Qt.SizeBDiagCursor)
        elif self._title_at(pos) is not None:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.unsetCursor()

    # ── painting ─────────────────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.T["canvas"]))
        r = self._frame_rect()
        p.fillRect(r, Qt.black)
        if self.img is not None and not self.img.isNull():
            if self.img.width() != r.width() or self.img.height() != r.height():
                p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(r, self.img)
        if self.titles_provider is not None:        # titles are painted over the frame, so editing them is instant
            clips = self.titles_provider()
            if clips:
                p.setClipRect(r)
                draw_titles(p, QRectF(r), clips)
                p.setClipping(False)
        self._paint_overlay(p, r)

    def _paint_overlay(self, p, r):
        """Editing aids (never exported): centre guides while dragging, selection box and handles."""
        accent = QColor("#4DA3FF")                      # fixed bright blue: visible on any footage
        gx, gy = self._guides
        if gx or gy:
            p.setPen(QPen(QColor(255, 80, 200, 200), 1, Qt.DashLine))
            if gx:
                p.drawLine(r.center().x(), r.top(), r.center().x(), r.bottom())
            if gy:
                p.drawLine(r.left(), r.center().y(), r.right(), r.center().y())
        c, box = self._selected_box()
        if c is None:
            return
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(QPen(accent, 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(box)
        p.setPen(QPen(accent, 1))
        p.setBrush(QBrush(QColor("#FFFFFF")))
        h = self.HANDLE
        for pt in self._corners(box).values():
            p.drawRect(QRectF(pt.x() - h / 2.0, pt.y() - h / 2.0, h, h))


# ══ worker threads ════════════════════════════════════════════════════════════════════════════
class _Stream(QThread):
    """Runs an ffmpeg command and queues fixed-size chunks read from its stdout.

    The queue is bounded: when it is full the thread simply waits, which stops ffmpeg
    (its pipe fills) – so a paused/ahead stream costs no CPU.
    """
    failed = pyqtSignal(str)

    def __init__(self, cmd, chunk, max_items, key, t0, end_t, parent=None):
        super().__init__(parent)
        self.cmd, self.chunk, self.max_items = cmd, chunk, max_items
        self.key, self.t0, self.end_t = key, t0, end_t
        self._q = deque()
        self._cv = threading.Condition()
        self._stop = False
        self._eof = False
        self._proc = None
        self._count = 0

    # worker thread ---------------------------------------------------------------------------
    def run(self):
        err = tempfile.TemporaryFile()
        got_any = False
        try:
            self._proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=err,
                                          stdin=subprocess.DEVNULL, bufsize=1 << 20, **_NOWIN)
            rd = self._proc.stdout
            while not self._stop:
                buf = rd.read(self.chunk)
                if not buf:
                    break
                if len(buf) < self.chunk:
                    if not self._partial_ok:
                        break
                    buf = buf[:len(buf) - len(buf) % 4]
                    if not buf:
                        break
                got_any = True
                with self._cv:
                    while len(self._q) >= self.max_items and not self._stop:
                        self._cv.wait(0.05)
                    if self._stop:
                        break
                    self._q.append((self._count, buf))
                    self._count += 1
        except Exception as e:
            self.failed.emit(f"Preview: {e}")
        finally:
            with self._cv:
                self._eof = True
            try:
                if self._proc and self._proc.poll() is None:
                    self._proc.kill()
                if self._proc:
                    self._proc.wait(2)
            except Exception:
                pass
            if not got_any and not self._stop:
                try:
                    err.seek(0)
                    msg = err.read().decode("utf-8", "replace").strip().splitlines()
                    if msg:
                        self.failed.emit("Preview: " + msg[-1])
                except Exception:
                    pass
            err.close()

    _partial_ok = False

    # UI thread -------------------------------------------------------------------------------
    def stop(self):
        self._stop = True
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()
        except Exception:
            pass
        with self._cv:
            self._cv.notify_all()

    def ready(self):
        return bool(self._q) or self._eof

    def drained(self):
        return self._eof and not self._q


class _VideoStream(_Stream):
    def __init__(self, cmd, w, h, fps, key, t0, end_t, parent=None):
        super().__init__(cmd, w * h * 3, 10, key, t0, end_t, parent)
        self.w, self.h, self.fps = w, h, fps

    def pop_due(self, t):
        """Raw bytes of the newest frame whose time is <= t (older ones are dropped), else None."""
        rel, last = t - self.t0, None
        with self._cv:
            while self._q and self._q[0][0] / self.fps <= rel + 1e-6:
                last = self._q.popleft()[1]
            if last is not None:
                self._cv.notify()
        return last


class _AudioStream(_Stream):
    _partial_ok = True

    def __init__(self, cmd, key, t0, end_t, parent=None):
        super().__init__(cmd, 16384, 40, key, t0, end_t, parent)

    def take(self):
        with self._cv:
            if not self._q:
                return None
            item = self._q.popleft()[1]
            self._cv.notify()
            return item


class _StillWorker(QThread):
    """Decodes single frames for scrubbing. Only the NEWEST request is kept (latest wins)."""
    ready = pyqtSignal(object, object)          # key, raw bytes (or b"")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cv = threading.Condition()
        self._req = None
        self._stop = False

    def request(self, key, cmd, size):
        with self._cv:
            self._req = (key, cmd, size)
            self._cv.notify()

    def shutdown(self):
        with self._cv:
            self._stop = True
            self._cv.notify()

    def run(self):
        while True:
            with self._cv:
                while self._req is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                key, cmd, size = self._req
                self._req = None
            raw = b""
            try:
                r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, timeout=20, **_NOWIN)
                if len(r.stdout) >= size:
                    raw = r.stdout[:size]
            except Exception:
                pass
            self.ready.emit(key, raw)


# ══ engine ════════════════════════════════════════════════════════════════════════════════════
class PreviewEngine(QObject):
    timeChanged = pyqtSignal(float)
    playingChanged = pyqtSignal(bool)
    message = pyqtSignal(str)

    LOOKAHEAD = 1.0            # seconds: pre-decode the next clip this far ahead
    EMIT_EVERY = 1 / 30.0      # playhead/timecode UI refresh rate

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.project = None
        self.thumbs = {}
        self.t = 0.0
        self.playing = False

        self._pw, self._ph, self._pfps = 960, 540, 30
        self._starting = False
        self._hold = False
        self._start_req = 0.0
        self._base_t = self._base_m = 0.0
        self._last_emit = 0.0
        self._vsess = self._vnext = None
        self._achain = []
        self._dead = []
        self._shown_img = None
        self._black = True
        self._cache = OrderedDict()
        self._want = None

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(6)
        self.timer.timeout.connect(self._tick)
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(70)
        self._seek_timer.timeout.connect(self._after_seek)

        self.view.titles_provider = self._titles_now
        self.view.layers_provider = self._layers_now
        self.stills = _StillWorker(self)
        self.stills.ready.connect(self._on_still)
        self.stills.start()

        self.aout = self.adev = None
        self._afmt = None
        self._apending = b""
        self._rate, self._ch = 48000, 2
        if HAS_MULTIMEDIA:
            self._init_audio()

    # ── audio device ─────────────────────────────────────────────────────────────────────────
    def _init_audio(self):
        try:
            fmt = QAudioFormat()
            fmt.setSampleRate(48000)
            fmt.setChannelCount(2)
            fmt.setSampleSize(16)
            fmt.setCodec("audio/pcm")
            fmt.setByteOrder(QAudioFormat.LittleEndian)
            fmt.setSampleType(QAudioFormat.SignedInt)
            info = QAudioDeviceInfo.defaultOutputDevice()
            if info.isNull():
                return
            if not info.isFormatSupported(fmt):
                fmt = info.nearestFormat(fmt)
            if fmt.sampleSize() != 16 or fmt.sampleType() != QAudioFormat.SignedInt or fmt.codec() != "audio/pcm":
                return
            self._afmt = fmt
            self._rate, self._ch = fmt.sampleRate(), fmt.channelCount()
            self.aout = QAudioOutput(fmt, self)
            self.aout.setBufferSize(int(self._rate * self._ch * 2 * 0.2))     # ~200 ms
        except Exception:
            self.aout = None

    def _reset_audio(self):
        self._apending = b""
        if self.aout is not None:
            self.aout.stop()
            self.adev = self.aout.start()

    def _close_audio(self):
        self._apending = b""
        if self.aout is not None:
            self.aout.stop()
        self.adev = None

    def _feed_audio(self):
        if self.adev is None or self._starting or not self.playing:
            return
        for _ in range(64):
            free = self.aout.bytesFree()
            if free <= 0:
                return
            if not self._apending:
                s = next((c for c in self._achain if not c.drained()), None)
                if s is None or self.t + 0.05 < s.t0:       # next audio clip isn't due yet
                    return
                chunk = s.take()
                if chunk is None:
                    return
                self._apending = chunk
            n = self.adev.write(self._apending[:free])
            if n is None or n <= 0:
                return
            self._apending = self._apending[n:]

    def _titles_now(self):
        return self.project.titles_at(self.t) if self.project else []

    def _layers_now(self):
        """Picture clips visible now with their on-frame boxes (so they can be picked / moved / resized)."""
        p = self.project
        return [(c, p.fit_box(c)) for c in p.video_clips_at(self.t)] if p else []

    # ── public API (same as before) ──────────────────────────────────────────────────────────
    def set_project(self, project, reset=False):
        self.project = project
        pw, ph = ft.preview_size(project.width, project.height)
        pfps = max(1, min(30, int(project.fps)))
        changed = (pw, ph, pfps) != (self._pw, self._ph, self._pfps)
        self._pw, self._ph, self._pfps = pw, ph, pfps
        self.view.set_aspect(project.width, project.height)
        if changed or reset:
            self._cache.clear()
        if self.playing:
            if changed or reset:
                self._hard_start()
            else:
                self._resync()
        else:
            self._shown_img = None
            self._show_still()
        self.view.update()

    def refresh(self):
        """Re-apply the current time after an edit (keeps playing state)."""
        if not self.project:
            return
        if self.playing:
            self._resync()
        else:
            self._shown_img = None
            self._show_still()
        self.view.update()

    def seek(self, t):
        self.t = max(0.0, t)
        if self.playing:
            # dragging the playhead while playing: show stills now, restart streams when it settles
            self._hold = True
            self._stop_streams()
            self._close_audio()
            self._seek_timer.start()
        self._shown_img = None
        self._show_still()
        self._emit_time(force=True)

    def toggle(self):
        self.pause() if self.playing else self.play()

    def play(self):
        if not self.project or self.project.duration() <= 0:
            return
        if self.t >= self.project.duration() - 1e-3:
            self.t = 0.0
        self.playing = True
        self._seek_timer.stop()
        self._hard_start()
        self.timer.start()
        self.playingChanged.emit(True)

    def pause(self):
        was = self.playing
        self.playing = False
        self._starting = self._hold = False
        self.timer.stop()
        self._seek_timer.stop()
        self._stop_streams()
        self._close_audio()
        self.playingChanged.emit(False)
        if was and self.project:
            self._shown_img = None
            self._show_still()

    def shutdown(self):
        """Stop every thread/ffmpeg process (call before the app exits)."""
        self.playing = False
        self.timer.stop()
        self._seek_timer.stop()
        self._stop_streams()
        self._close_audio()
        self.stills.shutdown()
        for s in self._dead + [self.stills]:
            s.wait(2000)

    # ── stream management ────────────────────────────────────────────────────────────────────
    def _kill(self, s):
        if s is not None:
            s.stop()
            self._dead.append(s)

    def _stop_streams(self):
        self._kill(self._vsess)
        self._kill(self._vnext)
        for s in self._achain:
            self._kill(s)
        self._vsess = self._vnext = None
        self._achain = []
        self._apending = b""

    def _vkey(self, c):
        m = self.project.media[c.media_id]
        return (c.id, m.path, round(c.start, 4), round(c.in_point, 4), round(c.duration, 4),
                round(c.brightness, 3), round(c.contrast, 3), round(c.saturation, 3),
                round(c.scale, 4), round(c.pos_x, 4), round(c.pos_y, 4),
                self._pw, self._ph, self._pfps)

    def _akey(self, c):
        m = self.project.media[c.media_id]
        return (c.id, m.path, round(c.start, 4), round(c.in_point, 4), round(c.duration, 4),
                round(c.volume, 3), self._rate, self._ch)

    def _start_video(self, c, t0):
        m = self.project.media[c.media_id]
        cmd = ft.video_stream_cmd(m.path, c.in_point + (t0 - c.start), c.end - t0,
                                  self._pw, self._ph, self._pfps, c)
        s = _VideoStream(cmd, self._pw, self._ph, self._pfps, self._vkey(c), t0, c.end, self)
        s.failed.connect(self.message)
        s.start()
        return s

    def _start_audio(self, c, t0):
        m = self.project.media[c.media_id]
        cmd = ft.audio_stream_cmd(m.path, c.in_point + (t0 - c.start), c.end - t0, c.volume,
                                  self._rate, self._ch)
        s = _AudioStream(cmd, self._akey(c), t0, c.end, self)
        s.failed.connect(self.message)
        s.start()
        return s

    def _audio_src(self, t):
        """Clip that supplies sound at t: an audio-track clip, else the video clip's own audio."""
        p = self.project
        c = p.audio_clip_at(t)
        if c is None:
            v = p.top_video_clip_at(t)
            if v is not None and p.media[v.media_id].kind == "video":
                c = v
        return c if c is not None and p.media[c.media_id].has_audio else None

    def _sync_sessions(self):
        """Make the running streams match the timeline at self.t. Returns True if a stream had to
        be started 'cold' (so the clock should wait for it)."""
        p, t, cold = self.project, self.t, False
        if not p:
            return False

        # video -------------------------------------------------------------------------------
        vc = p.top_video_clip_at(t)
        vm = p.media[vc.media_id] if vc else None
        if vc is not None and vm.kind == "video":
            self._shown_img = None
            self._black = False
            key = self._vkey(vc)
            if self._vsess is None or self._vsess.key != key:
                if self._vnext is not None and self._vnext.key == key:
                    self._kill(self._vsess)
                    self._vsess, self._vnext = self._vnext, None
                else:
                    self._kill(self._vsess)
                    self._vsess = self._start_video(vc, t)
                    cold = True
        else:
            if self._vsess is not None:
                self._kill(self._vsess)
                self._vsess = None
            if vc is None:
                if not self._black:
                    self._black = True
                    self._shown_img = None
                    self.view.show_black()
            elif self._shown_img != vc.id:                  # image clip: one still frame
                self._black = False
                self._shown_img = vc.id
                self._show_still()
        nv = p.top_video_clip_at(t + self.LOOKAHEAD)
        if nv is not None and p.media[nv.media_id].kind == "video":
            nk = self._vkey(nv)
            if (self._vsess is None or self._vsess.key != nk) and (self._vnext is None or self._vnext.key != nk):
                self._kill(self._vnext)
                self._vnext = self._start_video(nv, max(nv.start, t))
        elif self._vnext is not None:
            self._kill(self._vnext)
            self._vnext = None

        # audio -------------------------------------------------------------------------------
        if self.aout is not None:
            self._achain = [s for s in self._achain if s.end_t > t - 0.5 or self._kill(s)]
            ac = self._audio_src(t)
            if ac is not None:
                key = self._akey(ac)
                if not any(s.key == key for s in self._achain):
                    for s in self._achain:
                        self._kill(s)
                    self._achain = []
                    self._reset_audio()
                    self._achain.append(self._start_audio(ac, t))
                    cold = True
            nc = self._audio_src(t + self.LOOKAHEAD)
            if nc is not None:
                nk = self._akey(nc)
                if not any(s.key == nk for s in self._achain):
                    self._achain.append(self._start_audio(nc, max(nc.start, t)))
        return cold

    def _enter_starting(self):
        self._starting = True
        self._start_req = time.monotonic()

    def _hard_start(self):
        """(Re)start every stream at self.t and hold the clock until picture + sound are ready."""
        self._hold = False
        self._stop_streams()
        self._reset_audio()
        self._shown_img = None
        self._black = False
        self._enter_starting()
        self._show_still()
        self._sync_sessions()

    def _resync(self):
        if self._hold:
            return
        if self._sync_sessions() and not self._starting:
            self._enter_starting()

    def _after_seek(self):
        if self.playing:
            self._hard_start()

    # ── clock / playback loop ────────────────────────────────────────────────────────────────
    def _streams_ready(self):
        if self._vsess is not None and not self._vsess.ready():
            return False
        first = next((c for c in self._achain if not c.drained()), None)
        if first is not None and self.t + 0.05 >= first.t0 and not first.ready():
            return False
        return True

    def _tick(self):
        if not self.playing or not self.project or self._hold:
            return
        now = time.monotonic()
        if self._dead:
            self._dead = [s for s in self._dead if not s.isFinished()]
        if self._starting:
            if self._streams_ready() or now - self._start_req > 2.0:
                self._starting = False
                self._base_t, self._base_m = self.t, now
            else:
                return
        t = self._base_t + (now - self._base_m)
        end = self.project.duration()
        if t >= end:
            self.t = end
            self.pause()
            self._emit_time(force=True)
            return
        self.t = t
        if self._sync_sessions():                    # cold start mid-play: wait for the decoder
            self._enter_starting()
            self._emit_time(force=True)
            return
        s = self._vsess
        if s is not None:
            raw = s.pop_due(t)
            if raw is not None:
                img = QImage(raw, s.w, s.h, 3 * s.w, QImage.Format_RGB888).copy()
                self.view.set_image(img)
        self._feed_audio()
        self._emit_time()

    def _emit_time(self, force=False):
        now = time.monotonic()
        if force or now - self._last_emit >= self.EMIT_EVERY:
            self._last_emit = now
            self.view.update(self.view._frame_rect())      # titles follow the playhead
            self.timeChanged.emit(self.t)

    # ── still frames (scrubbing, frame step, paused display) ─────────────────────────────────
    def _show_still(self):
        p, t = self.project, self.t
        if not p:
            return
        layers = p.video_clips_at(t)
        if len(layers) > 1:                                  # several layers visible: show them stacked
            sig = tuple((c.media_id, round((t - c.start + c.in_point) * p.fps), round(c.opacity, 3),
                         round(c.brightness, 3), round(c.contrast, 3), round(c.saturation, 3),
                         round(c.scale, 4), round(c.pos_x, 4), round(c.pos_y, 4)) for c in layers)
            key = ("@layers", sig, self._pw, self._ph)
            self._want = key
            self._black = False
            img = self._cache.get(key)
            if img is not None:
                self._cache.move_to_end(key)
                self.view.set_image(img)
                return
            try:
                cmd = ft.composite_still_cmd(p, t, self._pw, self._ph)
            except RuntimeError as e:
                self.message.emit(str(e))
                return
            self.stills.request(key, cmd, self._pw * self._ph * 3)
            return
        vc = p.top_video_clip_at(t)
        if vc is None:
            self._want = None
            self._black = True
            self.view.show_black()
            return
        m = p.media[vc.media_id]
        if m.kind == "image":
            src = 0.0
        else:
            src = vc.in_point + (t - vc.start)
            src = min(max(0.0, src), max(0.0, m.duration - 1.0 / max(1, p.fps)))
        key = (m.path, round(src * p.fps), self._pw, self._ph,
               round(vc.brightness, 3), round(vc.contrast, 3), round(vc.saturation, 3),
               round(vc.scale, 4), round(vc.pos_x, 4), round(vc.pos_y, 4))
        self._want = key
        self._black = False
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
            self.view.set_image(img)
            return
        try:
            cmd = ft.still_cmd(m.path, m.kind, src, self._pw, self._ph, vc)
        except RuntimeError as e:
            self.message.emit(str(e))
            return
        self.stills.request(key, cmd, self._pw * self._ph * 3)

    def _on_still(self, key, raw):
        if not raw:
            if key == self._want:
                self.message.emit("Preview: could not decode this frame.")
            return
        img = QImage(raw, self._pw, self._ph, 3 * self._pw, QImage.Format_RGB888).copy()
        if key[2] == self._pw and key[3] == self._ph:
            self._cache[key] = img
            while len(self._cache) > 48:
                self._cache.popitem(last=False)
        # while the clock runs, live frames own the screen; otherwise show the requested still
        if key == self._want and (not self.playing or self._starting or self._hold or self._vsess is None):
            self.view.set_image(img)