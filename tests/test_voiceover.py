import array
import math
import os
import tempfile
import unittest
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from archord_video.core import voiceover as vo
from archord_video.core.project import MediaItem, Project

try:
    import PyQt5  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


def tone(seconds, rate=48000, amp=0.5, channels=1):
    n = int(seconds * rate)
    a = array.array("h")
    for i in range(n):
        v = int(32767 * amp * math.sin(2 * math.pi * 440 * i / rate))
        a.extend([v] * channels)
    return a.tobytes()


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_wav_sink_roundtrip(self):
        path = os.path.join(self.tmp.name, "a.wav")
        s = vo.WavSink(path, 48000, 1)
        s.write(tone(0.5))
        s.write(tone(0.5))
        s.close()
        self.assertAlmostEqual(s.duration, 1.0, places=3)
        with wave.open(path, "rb") as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 48000))
            self.assertEqual(w.getnframes(), 48000)

    def test_wav_sink_never_splits_a_frame(self):
        path = os.path.join(self.tmp.name, "st.wav")
        s = vo.WavSink(path, 44100, 2)
        s.write(tone(0.1, 44100, channels=2) + b"\x01\x02\x03")        # trailing partial frame is dropped
        s.close()
        self.assertEqual(s.frames, 4410)
        with wave.open(path, "rb") as w:
            self.assertEqual(w.getnframes(), 4410)

    def test_write_after_close_is_ignored(self):
        s = vo.WavSink(os.path.join(self.tmp.name, "c.wav"))
        s.close()
        s.write(tone(0.1))
        self.assertEqual(s.frames, 0)
        s.close()                                                       # closing twice is fine

    def test_peak_level(self):
        self.assertEqual(vo.peak_level(b""), 0.0)
        self.assertEqual(vo.peak_level(bytes(1000)), 0.0)
        self.assertAlmostEqual(vo.peak_level(tone(0.05, amp=0.5)), 0.5, places=2)
        self.assertEqual(vo.peak_level(array.array("h", [-32768]).tobytes()), 1.0)
        self.assertLessEqual(vo.peak_level(tone(0.05, amp=1.0)), 1.0)

    def test_next_path_counts_up(self):
        d = os.path.join(self.tmp.name, "v")
        self.assertTrue(vo.next_voiceover_path(d).endswith("Voiceover 1.wav"))     # folder doesn't exist yet
        os.makedirs(d)
        for n in ("Voiceover 1.wav", "Voiceover 7.wav", "notes.txt", "Voiceover x.wav"):
            open(os.path.join(d, n), "w").close()
        self.assertTrue(vo.next_voiceover_path(d).endswith("Voiceover 8.wav"))

    def test_recordings_dir(self):
        self.assertEqual(vo.recordings_dir(None, "/fallback"), "/fallback")
        got = vo.recordings_dir(os.path.join(self.tmp.name, "edit.avproj"), "/fallback")
        self.assertEqual(got, os.path.join(os.path.abspath(self.tmp.name), vo.FOLDER_NAME))


class TrackChoiceTests(unittest.TestCase):
    def make(self, typed=True):
        p = Project(typed=typed)
        p.add_media(MediaItem("m1", "/tmp/a.wav", "audio", 6.0, has_audio=True))
        return p

    def test_uses_the_empty_audio_track(self):
        p = self.make()
        n = len(p.tracks)
        i, created = p.voiceover_track(2.0)
        self.assertEqual((p.tracks[i]["kind"], created, len(p.tracks)), ("audio", False, n))

    def test_reuses_track_when_earlier_clips_end_before_start(self):
        p = self.make()
        i0, _ = p.voiceover_track(0.0)
        p.add_clip("m1", i0, 0.0)                                       # occupies 0..6
        i, created = p.voiceover_track(6.0)
        self.assertEqual((i, created), (i0, False))

    def test_new_track_when_something_is_at_or_after_start(self):
        p = self.make()
        i0, _ = p.voiceover_track(0.0)
        p.add_clip("m1", i0, 4.0)                                       # occupies 4..10
        n = len(p.tracks)
        for start in (0.0, 5.0):                                        # before it (would collide later) and inside it
            q = Project.from_snapshot(p.snapshot())
            i, created = q.voiceover_track(start)
            self.assertTrue(created)
            self.assertEqual(len(q.tracks), n + 1)
            self.assertEqual(q.tracks[i]["kind"], "audio")
            self.assertTrue(q.track_is_empty(i))

    def test_skips_locked_and_muted_tracks(self):
        p = self.make()
        i0, _ = p.voiceover_track(0.0)
        p.set_track_flag(i0, "locked", True)
        i, created = p.voiceover_track(0.0)
        self.assertTrue(created and i != i0)
        p2 = self.make()
        j0, _ = p2.voiceover_track(0.0)
        p2.set_track_flag(j0, "muted", True)
        self.assertTrue(p2.voiceover_track(0.0)[1])

    def test_legacy_project(self):
        p = self.make(typed=False)
        i, created = p.voiceover_track(0.0)
        self.assertEqual((p.tracks[i]["kind"], created), ("audio", False))

    def test_recorded_clip_survives_save_and_load(self):
        p = self.make()
        i, _ = p.voiceover_track(1.5)
        p.add_media(MediaItem("m2", "/tmp/Voiceover 1.wav", "audio", 2.5, has_audio=True))
        c = p.place_clip("m2", i, 1.5, 2.5)
        q = Project.from_snapshot(p.snapshot())
        c2 = q.clip(c.id)
        self.assertEqual((c2.track, c2.start, c2.duration), (i, 1.5, 2.5))


if HAVE_QT:
    from PyQt5.QtCore import QObject, QSettings, pyqtSignal
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QDialog

    from archord_video.ui import voiceover as ui_vo
    from archord_video.ui.timeline import TimelineCanvas

    APP = QApplication.instance() or QApplication([])

    class FakeRecorder(QObject):
        chunk = pyqtSignal(bytes)
        failed = pyqtSignal(str)
        last = None
        fail_on_start = None

        @staticmethod
        def devices():
            return [("Fake mic", object())]

        def __init__(self, parent=None):
            super().__init__(parent)
            self.rate, self.channels, self.running = 48000, 1, False
            FakeRecorder.last = self

        def start(self, dev):
            if FakeRecorder.fail_on_start:
                raise RuntimeError(FakeRecorder.fail_on_start)
            self.running = True

        def stop(self):
            self.running = False

    class NoMic(FakeRecorder):
        @staticmethod
        def devices():
            return []

    class FakeEngine:
        def __init__(self, t=0.0):
            self.t, self.playing, self.muted, self.plays, self._warm = t, False, False, 0, 0
            self.mute_log = []

        def play(self):
            self.plays += 1
            self.playing, self._warm = True, 3

        def pause(self):
            self.playing = False

        def is_starting(self):
            if self._warm > 0:
                self._warm -= 1
                return True
            return False

        def set_muted(self, m):
            self.muted = m
            self.mute_log.append(m)

    class FakeCanvas:
        def __init__(self):
            self.recs, self.cleared, self.playheads = [], 0, []

        def set_recording(self, track, t0, t1, levels=(), step=0.05):
            self.recs.append((track, t0, t1, list(levels)))

        def clear_recording(self):
            self.cleared += 1

        def set_playhead(self, t, follow=False):
            self.playheads.append(t)

    def wait_for(cond, ms=3000):
        waited = 0
        while not cond() and waited < ms:
            QTest.qWait(10)
            waited += 10
        return cond()

    class DialogTests(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.TemporaryDirectory()
            self.addCleanup(self.tmp.cleanup)
            QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, os.path.join(self.tmp.name, "cfg"))
            self.folder = os.path.join(self.tmp.name, "Voiceovers")
            self.project = Project(typed=True)
            self.project.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 640, 360, 30, True))
            self.canvas = FakeCanvas()
            FakeRecorder.fail_on_start = None

        def dialog(self, engine=None, start=0.0, with_clip=False, factory=FakeRecorder):
            if with_clip:
                self.project.place_clip("m1", None, 0.0)
            track, _ = self.project.voiceover_track(start)
            self.engine = engine or FakeEngine(start)
            d = ui_vo.VoiceoverDialog(self.engine, self.project, self.canvas, track, start, self.folder,
                                      recorder_factory=factory)
            self.addCleanup(d.deleteLater)
            d.chk_count.setChecked(False)
            return d

        def test_take_is_written_and_reported(self):
            d = self.dialog(start=2.0)                                   # empty project: nothing to play
            self.assertFalse(d.chk_play.isEnabled())
            d._toggle()
            self.assertEqual(d.state, "recording")
            self.assertTrue(FakeRecorder.last.running)
            FakeRecorder.last.chunk.emit(tone(0.6))
            d._tick()
            self.assertEqual(self.canvas.recs[-1][:2], (d.track, 2.0))
            self.assertAlmostEqual(self.canvas.recs[-1][2], 2.6, places=2)
            self.assertTrue(any(v > 0.3 for v in self.canvas.recs[-1][3]))
            self.assertAlmostEqual(self.canvas.playheads[-1], 2.6, places=2)    # nothing is playing, so we move it
            d._toggle()                                                  # Stop
            self.assertEqual(d.result(), QDialog.Accepted)
            r = d.result_info
            self.assertEqual((r["start"], r["track"]), (2.0, d.track))
            self.assertAlmostEqual(r["duration"], 0.6, places=3)
            self.assertTrue(os.path.isfile(r["path"]) and r["path"].endswith("Voiceover 1.wav"))
            self.assertFalse(FakeRecorder.last.running)
            self.assertGreaterEqual(self.canvas.cleared, 1)

        def test_plays_the_project_muted_and_restores_sound(self):
            d = self.dialog(start=1.0, with_clip=True)
            self.assertTrue(d.chk_play.isChecked() and d.chk_mute.isChecked())
            d._toggle()
            self.assertEqual(d.state, "playing-in")                      # mic waits for the playback clock
            self.assertTrue(self.engine.playing and self.engine.muted)
            self.assertFalse(FakeRecorder.last.running)
            self.assertTrue(wait_for(lambda: d.state == "recording"))
            self.engine.t = 1.04                                         # where playback was when the mic opened
            self.assertTrue(FakeRecorder.last.running)
            FakeRecorder.last.chunk.emit(tone(0.5))
            d._toggle()
            self.assertEqual(d.result(), QDialog.Accepted)
            self.assertFalse(self.engine.playing)
            self.assertFalse(self.engine.muted)
            self.assertEqual(self.engine.mute_log[0], True)

        def test_start_time_follows_playback_clock(self):
            d = self.dialog(start=1.0, with_clip=True)

            class Drift(FakeEngine):
                def is_starting(self_inner):
                    busy = super().is_starting()
                    if not busy:
                        self_inner.t = 1.12                              # clock started a little after we asked
                    return busy
            d.engine = Drift(1.0)
            d._toggle()
            self.assertTrue(wait_for(lambda: d.state == "recording"))
            FakeRecorder.last.chunk.emit(tone(0.5))
            d._toggle()
            self.assertAlmostEqual(d.result_info["start"], 1.12, places=3)

        def test_playback_unchecked_records_without_playing(self):
            d = self.dialog(start=1.0, with_clip=True)
            d.chk_play.setChecked(False)
            self.assertFalse(d.chk_mute.isEnabled())
            d._toggle()
            self.assertEqual(d.state, "recording")
            self.assertEqual(self.engine.plays, 0)
            FakeRecorder.last.chunk.emit(tone(0.4))
            d._toggle()
            self.assertEqual(d.result_info["start"], 1.0)

        def test_discard_removes_the_file_and_restores_state(self):
            d = self.dialog(start=0.0, with_clip=True)
            d._toggle()
            self.assertTrue(wait_for(lambda: d.state == "recording"))
            FakeRecorder.last.chunk.emit(tone(0.5))
            path = d._sink.path
            self.assertTrue(os.path.isfile(path))
            d.reject()
            self.assertEqual(d.result(), QDialog.Rejected)
            self.assertFalse(os.path.exists(path))
            self.assertFalse(self.engine.playing or self.engine.muted)
            self.assertFalse(FakeRecorder.last.running)
            self.assertIsNone(d.result_info)

        def test_too_short_take_is_dropped_and_dialog_stays_open(self):
            d = self.dialog()
            d._toggle()
            FakeRecorder.last.chunk.emit(tone(0.05))
            path = d._sink.path
            d._toggle()
            self.assertEqual(d.state, "idle")
            self.assertFalse(os.path.exists(path))
            self.assertIsNone(d.result_info)
            self.assertTrue(d.device.isEnabled())

        def test_countdown_then_cancel(self):
            d = self.dialog()
            d.chk_count.setChecked(True)
            d._toggle()
            self.assertEqual(d.state, "countdown")
            self.assertEqual(d.clock.text(), "3")
            d._count_down()
            self.assertEqual(d.clock.text(), "2")
            d._toggle()                                                  # cancel during the count-in
            self.assertEqual(d.state, "idle")
            self.assertFalse(FakeRecorder.last.running)
            self.assertTrue(d.chk_count.isEnabled())

        def test_countdown_runs_into_recording(self):
            d = self.dialog()
            d.chk_count.setChecked(True)
            d._toggle()
            for _ in range(3):
                d._count_down()
            self.assertEqual(d.state, "recording")

        def test_mic_that_cannot_open_reports_why(self):
            d = self.dialog()
            FakeRecorder.fail_on_start = "The microphone couldn't be opened."
            d._toggle()
            self.assertEqual(d.state, "idle")
            self.assertIn("couldn't be opened", d.status.text())
            self.assertFalse(os.path.exists(os.path.join(self.folder, "Voiceover 1.wav")))

        def test_silent_stream_gives_up_with_a_message(self):
            d = self.dialog()
            d._toggle()
            d._wall0 -= 5                                                # 5 s with no data at all
            d._tick()
            self.assertEqual(d.state, "idle")
            self.assertIn("No audio", d.status.text())

        def test_device_failure_mid_take(self):
            d = self.dialog()
            d._toggle()
            FakeRecorder.last.chunk.emit(tone(0.5))
            FakeRecorder.last.failed.emit("The microphone stopped responding (was it unplugged?).")
            self.assertEqual(d.state, "idle")
            self.assertIn("unplugged", d.status.text())

        def test_no_microphone_disables_record(self):
            d = self.dialog(factory=NoMic)
            self.assertFalse(d.btn_rec.isEnabled())

        def test_successive_takes_get_new_names(self):
            for n in (1, 2):
                d = self.dialog()
                d._toggle()
                FakeRecorder.last.chunk.emit(tone(0.5))
                d._toggle()
                self.assertTrue(d.result_info["path"].endswith(f"Voiceover {n}.wav"))

    class CanvasOverlayTests(unittest.TestCase):
        def test_overlay_paints_and_clears(self):
            c = TimelineCanvas()
            c.resize(900, 300)
            p = Project(typed=True)
            c.set_project(p)
            i, _ = p.voiceover_track(0.0)
            c.set_recording(i, 1.0, 3.0, [0.2, 0.9, 0.5] * 20)
            before = c.grab().toImage()
            c.clear_recording()
            after = c.grab().toImage()
            self.assertIsNone(c.rec)
            self.assertNotEqual(before, after)                           # the red take disappeared

        def test_overlay_ignores_a_stale_track(self):
            c = TimelineCanvas()
            c.resize(600, 240)
            c.set_project(Project(typed=True))
            c.set_recording(99, 0.0, 1.0)
            c.grab()                                                     # must not raise


    class MainWindowTests(unittest.TestCase):
        """Drive MainWindow.record_voiceover with a stand-in dialog (no audio hardware needed)."""

        def setUp(self):
            from archord_video.ui import main_window as mw
            self.mw = mw
            self.tmp = tempfile.TemporaryDirectory()
            self.addCleanup(self.tmp.cleanup)
            QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, os.path.join(self.tmp.name, "cfg"))
            self.saved = (mw.HAS_INPUT, mw.VoiceRecorder, mw.VoiceoverDialog)
            mw.HAS_INPUT = True
            mw.VoiceRecorder = FakeRecorder
            self.win = mw.MainWindow()
            self.addCleanup(self._teardown)

        def _teardown(self):
            self.win.engine.shutdown()
            self.win.deleteLater()
            self.mw.HAS_INPUT, self.mw.VoiceRecorder, self.mw.VoiceoverDialog = self.saved

        def fake_dialog(self, accept, seconds=2.0):
            tmp = self.tmp.name

            class Dlg:
                result_info = None

                def __init__(self, engine, project, canvas, track, start, folder, parent=None):
                    self.track, self.start = track, start

                def exec_(self_inner):
                    if not accept:
                        return QDialog.Rejected
                    path = os.path.join(tmp, "Voiceover 1.wav")
                    s = vo.WavSink(path)
                    s.write(tone(seconds))
                    s.close()
                    self_inner.result_info = dict(path=path, start=self_inner.start, duration=seconds,
                                                  track=self_inner.track)
                    return QDialog.Accepted
            self.mw.VoiceoverDialog = Dlg

        def test_accepted_take_becomes_a_clip_on_an_audio_track(self):
            w, p = self.win, self.win.project
            self.fake_dialog(True, 2.0)
            w.engine.t = 1.5
            n_tracks = len(p.tracks)
            w.record_voiceover()
            self.assertEqual(len(p.clips), 1)
            c = p.clips[0]
            m = p.media[c.media_id]
            self.assertEqual((m.kind, m.has_audio), ("audio", True))
            self.assertEqual(p.tracks[c.track]["kind"], "audio")
            self.assertAlmostEqual(c.start, 1.5)
            self.assertAlmostEqual(c.duration, 2.0)
            self.assertEqual(len(p.tracks), n_tracks)                    # reused the empty Audio track
            self.assertEqual(w.labels[-1], "Record Voiceover")
            self.assertAlmostEqual(w.engine.t, 3.5)                      # playhead sits at the end of the take
            self.assertEqual(w.timeline.canvas.selected, c.id)
            self.assertEqual(w.media_bin.list.count(), 1)
            w.undo()                                                     # one undo step removes the whole take
            self.assertEqual(len(w.project.clips), 0)

        def test_second_take_over_the_first_goes_to_a_new_track(self):
            w = self.win
            self.fake_dialog(True, 2.0)
            w.engine.t = 0.0
            w.record_voiceover()
            n = len(w.project.tracks)
            w.engine.t = 1.0                                             # inside the first take
            w.record_voiceover()
            self.assertEqual(len(w.project.tracks), n + 1)
            self.assertEqual(len(w.project.clips), 2)
            self.assertNotEqual(w.project.clips[0].track, w.project.clips[1].track)
            self.assertEqual(w.project.tracks[w.project.clips[1].track]["kind"], "audio")

        def test_cancelled_take_leaves_no_trace(self):
            w, p = self.win, self.win.project
            self.fake_dialog(True, 2.0)
            w.engine.t = 0.0
            w.record_voiceover()                                         # occupies 0..2 on the first audio track
            snap, n_hist = p.snapshot(), len(w.history)
            self.fake_dialog(False)
            w.engine.t = 1.0                                             # would need a new track
            w.record_voiceover()
            self.assertEqual(w.project.snapshot(), snap)                 # the speculative track was removed again
            self.assertEqual(len(w.history), n_hist)

        def test_no_microphone_warns_and_does_nothing(self):
            from PyQt5.QtWidgets import QMessageBox
            self.mw.VoiceRecorder = NoMic
            shown = []
            orig = QMessageBox.warning
            QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(a[1]))
            try:
                self.win.record_voiceover()
            finally:
                QMessageBox.warning = orig
            self.assertEqual(shown, ["No microphone"])
            self.assertEqual(len(self.win.project.clips), 0)

        def test_action_and_shortcut_exist(self):
            self.assertEqual(self.win.a_record.shortcut().toString(), "Ctrl+R")


if __name__ == "__main__":
    unittest.main()
