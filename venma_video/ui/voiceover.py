"""Voiceover recording: capture the microphone straight onto an audio track.

``VoiceRecorder`` wraps QAudioInput (raw 16-bit PCM, no codecs). ``VoiceoverDialog`` is the small
window that drives a take: pick the microphone, optionally count in, play the project while you talk,
watch the level, and stop. It writes a ``.wav`` and reports where it started and how long it ran; the
main window turns that into a clip on the audio track chosen by ``Project.voiceover_track``.
"""
import os
import time

from PyQt5.QtCore import QObject, QSettings, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core import voiceover as vo
from . import theme
from .timeline import format_tc

try:
    from PyQt5.QtMultimedia import QAudio, QAudioDeviceInfo, QAudioFormat, QAudioInput
    HAS_INPUT = True
except Exception:                      # no QtMultimedia / audio libraries on this machine
    HAS_INPUT = False

DEVICE_KEY = "voiceover/device"
LEVEL_STEP = 0.05                      # seconds of audio per bar in the timeline's live level trace
RED = "#E5484D"


# ══ microphone ═════════════════════════════════════════════════════════════════════════════════
class VoiceRecorder(QObject):
    """Microphone -> ``chunk(bytes)`` of 16-bit little-endian PCM (polled, which every audio backend supports)."""
    chunk = pyqtSignal(bytes)
    failed = pyqtSignal(str)

    @staticmethod
    def devices():
        """``[(name, device), ...]`` for every input device, the system default first."""
        if not HAS_INPUT:
            return []
        try:
            default = QAudioDeviceInfo.defaultInputDevice().deviceName()
            found = [(d.deviceName(), d) for d in QAudioDeviceInfo.availableDevices(QAudio.AudioInput)]
        except Exception:
            return []
        found.sort(key=lambda nd: nd[0] != default)
        return found

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rate, self.channels = 48000, 1
        self._input = self._io = None
        self._stopping = False
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._poll)

    def start(self, device):
        """Open ``device`` and start delivering audio. Raises RuntimeError with a readable message."""
        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(1)
        fmt.setSampleSize(16)
        fmt.setCodec("audio/pcm")
        fmt.setByteOrder(QAudioFormat.LittleEndian)
        fmt.setSampleType(QAudioFormat.SignedInt)
        if not device.isFormatSupported(fmt):
            fmt = device.nearestFormat(fmt)
        if (fmt.sampleSize() != 16 or fmt.sampleType() != QAudioFormat.SignedInt or fmt.codec() != "audio/pcm"
                or fmt.byteOrder() != QAudioFormat.LittleEndian):
            raise RuntimeError("This microphone doesn't offer 16-bit PCM audio.")
        self.rate, self.channels = fmt.sampleRate(), fmt.channelCount()
        self._stopping = False
        self._input = QAudioInput(device, fmt, self)
        self._io = self._input.start()
        if self._io is None or self._input.error() != QAudio.NoError:
            msg = self._error_text()
            self._release()
            raise RuntimeError(msg)
        self._input.stateChanged.connect(self._state)      # only watch for failures once it has opened fine
        self._timer.start()

    def stop(self):
        """Deliver whatever is still buffered, then close the device."""
        self._stopping = True
        self._timer.stop()
        self._poll()
        self._release()

    def _release(self):
        if self._input is not None:
            try:
                self._input.stop()
            except Exception:
                pass
            self._input.deleteLater()
        self._input = self._io = None

    def _poll(self):
        if self._io is None:
            return
        data = self._io.readAll()
        if data.size():
            self.chunk.emit(bytes(data))

    def _state(self, state):
        if self._stopping or self._input is None:
            return
        if state == QAudio.StoppedState and self._input.error() != QAudio.NoError:
            self._timer.stop()
            self.failed.emit(self._error_text())

    def _error_text(self):
        err = self._input.error() if self._input is not None else QAudio.OpenError
        if err == QAudio.OpenError:
            return ("The microphone couldn't be opened. Another app may be using it, or access to the "
                    "microphone is blocked in your system's privacy settings.")
        if err == QAudio.IOError:
            return "The microphone stopped responding (was it unplugged?)."
        return "The microphone failed while recording."


# ══ level meter ════════════════════════════════════════════════════════════════════════════════
class LevelMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.level = 0.0
        self.setFixedHeight(14)
        self.setMinimumWidth(180)

    def feed(self, v):
        """New peak (0..1). Rises instantly, falls smoothly."""
        self.level = max(float(v), self.level * 0.82)
        self.update()

    def reset(self):
        self.level = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(QPen(QColor(theme.T["border_strong"]), 1))
        p.setBrush(QColor(theme.T["sunken"]))
        p.drawRoundedRect(r, 4, 4)
        w = int((r.width() - 2) * min(1.0, self.level ** 0.6))           # a little compression so quiet speech shows
        if w > 0:
            g = QLinearGradient(r.left(), 0, r.right(), 0)
            g.setColorAt(0.0, QColor("#34D399"))
            g.setColorAt(0.7, QColor("#FBBF24"))
            g.setColorAt(1.0, QColor(RED))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(r.adjusted(1, 1, -(r.width() - 1 - w), -1), 3, 3)


# ══ dialog ═════════════════════════════════════════════════════════════════════════════════════
class VoiceoverDialog(QDialog):
    """Record one take. Modal, so the timeline can't change underneath it.

    ``track`` / ``start``: the audio track and the project time the take is recorded at (the playhead).
    On ``Accepted``, ``result_info`` is ``dict(path, start, duration, track)``.
    ``recorder_factory`` is injectable for tests; it needs ``devices()`` and the VoiceRecorder interface.
    """
    COUNTDOWN = 3

    def __init__(self, engine, project, canvas, track, start, folder, parent=None, recorder_factory=VoiceRecorder):
        super().__init__(parent)
        self.setWindowTitle("Record Voiceover")
        self.engine, self.project, self.canvas = engine, project, canvas
        self.track, self.start, self.folder = track, float(start), folder
        self.result_info = None
        self.state = "idle"                                # idle | countdown | playing-in | recording
        self._sink = None
        self._path = None
        self._t0 = self.start
        self._wall0 = 0.0
        self._levels = []
        self._silent_noted = False
        self._playing = False
        self._count = 0
        self.recorder = recorder_factory(self)
        self.recorder.chunk.connect(self._on_chunk)
        self.recorder.failed.connect(self._on_failed)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(QLabel("Microphone"))
        self.device = QComboBox()
        self.device.setMinimumWidth(240)
        for name, dev in recorder_factory.devices():
            self.device.addItem(name, dev)
        saved = QSettings("Venma", "VideoEditor").value(DEVICE_KEY, "")
        i = self.device.findText(str(saved)) if saved else -1
        if i >= 0:
            self.device.setCurrentIndex(i)
        row.addWidget(self.device, 1)
        lay.addLayout(row)

        row = QHBoxLayout()
        self.meter = LevelMeter()
        row.addWidget(self.meter, 1)
        self.clock = QLabel(format_tc(0.0, project.fps))
        self.clock.setStyleSheet(f"font-family: {theme.MONO}; font-size: 15pt; font-weight: 600;")
        row.addWidget(self.clock)
        lay.addLayout(row)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(40)
        self.status.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.status.setStyleSheet(f"color: {theme.T['muted']};")
        lay.addWidget(self.status)

        can_play = self._can_play()
        self.chk_play = QCheckBox("Play the project while recording")
        self.chk_play.setChecked(can_play)
        self.chk_play.setEnabled(can_play)
        if not can_play:
            self.chk_play.setToolTip("There is nothing to play from the playhead.")
        self.chk_mute = QCheckBox("Mute project sound (turn off only with headphones)")
        self.chk_mute.setChecked(True)
        self.chk_mute.setEnabled(can_play)
        self.chk_play.toggled.connect(self.chk_mute.setEnabled)
        self.chk_count = QCheckBox("Count in 3 – 2 – 1")
        self.chk_count.setChecked(True)
        for c in (self.chk_play, self.chk_mute, self.chk_count):
            lay.addWidget(c)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_rec = QPushButton("●  Record")
        self.btn_rec.setDefault(True)
        self.btn_rec.setCursor(Qt.PointingHandCursor)
        self.btn_rec.clicked.connect(self._toggle)
        self._style_record(False)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_rec)
        lay.addLayout(row)
        theme.tidy(self)
        self.setMinimumWidth(460)

        self._timer = QTimer(self)                          # UI heartbeat while a take runs
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._cd_timer = QTimer(self)
        self._cd_timer.setInterval(1000)
        self._cd_timer.timeout.connect(self._count_down)
        self._wait_timer = QTimer(self)                     # waits for playback to be running before the mic opens
        self._wait_timer.setInterval(20)
        self._wait_timer.timeout.connect(self._wait_playback)

        if self.device.count() == 0:
            self._say("No microphone was found.", error=True)
            self.btn_rec.setEnabled(False)
        else:
            self._say(f"Recording starts at {format_tc(self.start, project.fps)} on {project.tracks[track]['name']}.")

    # ── small helpers ──
    def _say(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {RED if error else theme.T['muted']};")

    def _style_record(self, recording):
        self.btn_rec.setText("■  Stop" if recording else "●  Record")
        base, hover = ("#3A3A42", "#4A4A54") if recording else (RED, "#F0575C")
        self.btn_rec.setStyleSheet(
            f"QPushButton {{ background: {base}; color: white; border: none; border-radius: 6px; padding: 8px 20px;"
            f" font-weight: 600; }} QPushButton:hover {{ background: {hover}; }}"
            f" QPushButton:disabled {{ background: {theme.T['border']}; color: {theme.T['faint']}; }}")

    def _can_play(self):
        return self.project.duration() > 0 and self.start < self.project.duration() - 0.05

    def _lock_options(self, locked):
        can_play = self._can_play()
        self.device.setEnabled(not locked)
        self.chk_count.setEnabled(not locked)
        self.chk_play.setEnabled(not locked and can_play)
        self.chk_mute.setEnabled(not locked and can_play and self.chk_play.isChecked())
        self.btn_cancel.setText("Discard" if locked else "Cancel")

    # ── take lifecycle ──
    def _toggle(self):
        if self.state == "idle":
            self._begin()
        elif self.state in ("countdown", "playing-in"):
            self._abort("Cancelled.")
        elif self.state == "recording":
            self._finish()

    def _begin(self):
        if self.device.currentIndex() < 0:
            return
        QSettings("Venma", "VideoEditor").setValue(DEVICE_KEY, self.device.currentText())
        self._lock_options(True)
        if self.chk_count.isChecked():
            self.state, self._count = "countdown", self.COUNTDOWN
            self._style_record(True)
            self.btn_rec.setText("■  Cancel")
            self._show_count()
            self._cd_timer.start()
        else:
            self._go()

    def _show_count(self):
        self.clock.setText(str(self._count))
        self._say("Get ready…")

    def _count_down(self):
        self._count -= 1
        if self._count > 0:
            self._show_count()
        else:
            self._cd_timer.stop()
            self._go()

    def _go(self):
        """Countdown done: start the project (if asked), then open the microphone once it is really running."""
        self.state = "playing-in"
        self._style_record(True)
        self._playing = False
        if self.chk_play.isChecked():
            self.engine.set_muted(self.chk_mute.isChecked())
            self.engine.play()
            self._playing = bool(self.engine.playing)
        self._wall0 = time.monotonic()
        if self._playing:
            self._wait_timer.start()                        # the clock only starts once the decoders are ready
        else:
            self._open_mic()

    def _wait_playback(self):
        if not self.engine.is_starting() or time.monotonic() - self._wall0 > 3.0:
            self._wait_timer.stop()
            self._open_mic()

    def _open_mic(self):
        if self.state != "playing-in":
            return
        self._path = vo.next_voiceover_path(self.folder)
        try:
            os.makedirs(self.folder, exist_ok=True)
            dev = self.device.currentData()
            self.recorder.start(dev)
            self._sink = vo.WavSink(self._path, self.recorder.rate, self.recorder.channels)
        except (RuntimeError, OSError) as e:
            self._abort(str(e) or "Couldn't start recording.", error=True)
            return
        # the take's time zero is the project time at this instant (the playhead, or wherever playback got to)
        self._t0 = self.engine.t if (self._playing and self.engine.playing) else self.start
        self._levels = []
        self._silent_noted = False
        self._wall0 = time.monotonic()
        self.state = "recording"
        self.btn_rec.setEnabled(True)
        self._say("Recording… speak now.  Press Stop (or Space) when you're done.")
        self._timer.start()
        self._tick()

    def _on_chunk(self, data):
        if self._sink is None or self.state != "recording":
            return
        self._sink.write(data)
        lv = vo.peak_level(data)
        self.meter.feed(lv)
        i = int(self._sink.duration / LEVEL_STEP)
        while len(self._levels) <= i:
            self._levels.append(0.0)
        self._levels[i] = max(self._levels[i], lv)

    def _tick(self):
        if self.state != "recording" or self._sink is None:
            return
        elapsed = self._sink.duration
        self.clock.setText(format_tc(elapsed, self.project.fps))
        self.meter.feed(0.0)                                # lets the bar fall between chunks
        self.canvas.set_recording(self.track, self._t0, self._t0 + elapsed, self._levels, LEVEL_STEP)
        if not self.engine.playing:                         # project ended / not playing: we move the playhead
            self.canvas.set_playhead(self._t0 + elapsed, follow=True)
        waited = time.monotonic() - self._wall0
        if self._sink.frames == 0 and waited > 2.0:
            self._abort("No audio is coming from the microphone. Check that it is connected, not muted, and "
                        "that this app is allowed to use it.", error=True)
        elif not self._silent_noted and waited > 3.0 and max(self._levels or [0.0]) == 0.0:
            self._silent_noted = True
            self._say("The microphone seems silent. Check that it isn't muted and the right device is selected.",
                      error=True)

    def _on_failed(self, msg):
        if self.state in ("recording", "playing-in"):
            self._abort(msg, error=True)

    def _stop_everything(self):
        for t in (self._timer, self._cd_timer, self._wait_timer):
            t.stop()
        self.engine.set_muted(False)
        if self._playing:
            self.engine.pause()
        self._playing = False
        try:
            self.recorder.stop()
        except Exception:
            pass
        if self._sink is not None:
            self._sink.close()
        self.canvas.clear_recording()
        self.meter.reset()

    def _discard_file(self):
        sink, self._sink = self._sink, None
        if sink is not None:
            try:
                os.remove(sink.path)
            except OSError:
                pass

    def _abort(self, msg, error=False):
        """Throw the take away and go back to idle (the dialog stays open so you can try again)."""
        self._stop_everything()
        self._discard_file()
        self.state = "idle"
        self._style_record(False)
        self._lock_options(False)
        self.clock.setText(format_tc(0.0, self.project.fps))
        self._say(msg, error=error)

    def _finish(self):
        sink = self._sink
        self._stop_everything()
        duration = sink.duration if sink else 0.0
        if duration < vo.MIN_TAKE:
            self._discard_file()
            self.state = "idle"
            self._style_record(False)
            self._lock_options(False)
            self._say("That take was too short, so nothing was kept. Try again.", error=True)
            return
        self._sink = None
        self.state = "done"
        self.result_info = dict(path=sink.path, start=self._t0, duration=duration, track=self.track)
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_placed", False):                # park it top-right, clear of the timeline
            self._placed = True
            par = self.parentWidget()
            if par is not None:
                g = par.frameGeometry()
                self.move(max(g.left(), g.right() - self.width() - 24), g.top() + 64)
            self.btn_rec.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.state != "idle":  # Esc must not throw a take away; use Discard
            e.ignore()
            return
        super().keyPressEvent(e)

    def reject(self):
        """Cancel / Discard / close: a take in progress is thrown away."""
        if self.state != "idle":
            self._stop_everything()
            self._discard_file()
            self.state = "idle"
        super().reject()
