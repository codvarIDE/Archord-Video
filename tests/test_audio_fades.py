"""Audio fades and waveforms: model rules, FFmpeg commands, and real renders / decodes."""
import array
import math
import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from venma_video.core import ffmpeg_tools as ft
from venma_video.core import waveform
from venma_video.core.project import MAX_FADE, Clip, MediaItem, Project

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
RATE = 48000


def make_project():
    p = Project(1280, 720, 30)
    p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
    p.add_media(MediaItem("m2", "/tmp/b.png", "image", 5.0, 800, 600))
    p.add_media(MediaItem("m3", "/tmp/c.wav", "audio", 8.0, has_audio=True))
    p.add_media(MediaItem("m4", "/tmp/silent.mp4", "video", 10.0, 1920, 1080, 30, False))
    return p


# ── model ────────────────────────────────────────────────────────────────────────────────────
class FadeModelTests(unittest.TestCase):
    def setUp(self):
        self.p = make_project()
        self.a = self.p.place_clip("m3", duration=6.0)

    def test_defaults_and_save_format(self):
        d = self.a.to_dict()
        self.assertNotIn("fade_in", d)
        self.assertNotIn("fade_out", d)
        self.assertEqual(self.a.fades(), (0.0, 0.0))
        self.assertEqual(self.a.fade_gain(0.0), 1.0)

    def test_set_and_save_roundtrip(self):
        self.assertTrue(self.p.set_fade(self.a.id, "in", 1.5))
        self.assertTrue(self.p.set_fade(self.a.id, "out", 2.0))
        d = self.a.to_dict()
        self.assertEqual((d["fade_in"], d["fade_out"]), (1.5, 2.0))
        q = Project.from_snapshot(self.p.snapshot())
        c = q.clip(self.a.id)
        self.assertEqual((c.fade_in, c.fade_out), (1.5, 2.0))

    def test_old_project_files_load(self):
        d = self.a.to_dict()
        c = Clip.from_dict(d)
        self.assertEqual((c.fade_in, c.fade_out), (0.0, 0.0))

    def test_setting_the_same_value_changes_nothing(self):
        self.assertTrue(self.p.set_fade(self.a.id, "in", 1.0))
        self.assertFalse(self.p.set_fade(self.a.id, "in", 1.0))
        self.assertFalse(self.p.set_fade(self.a.id, "out", 0.0))

    def test_tiny_and_negative_values_mean_off(self):
        self.p.set_fade(self.a.id, "in", 1.0)
        self.assertTrue(self.p.set_fade(self.a.id, "in", 0.004))
        self.assertEqual(self.a.fade_in, 0.0)
        self.assertFalse(self.p.set_fade(self.a.id, "in", -3))

    def test_the_two_fades_cannot_overlap(self):
        self.p.set_fade(self.a.id, "in", 4.0)
        self.p.set_fade(self.a.id, "out", 5.0)               # only 2 s are left after the fade-in
        self.assertAlmostEqual(self.a.fade_out, 2.0)
        self.p.set_fade(self.a.id, "in", 100.0)              # capped at what the fade-out leaves
        self.assertAlmostEqual(self.a.fade_in, 4.0)

    def test_fade_is_capped_by_the_clip_and_max_fade(self):
        self.p.set_fade(self.a.id, "in", 100.0)
        self.assertAlmostEqual(self.a.fade_in, 6.0)
        self.assertLessEqual(MAX_FADE, 60.0)

    def test_which_must_be_in_or_out(self):
        with self.assertRaises(ValueError):
            self.p.set_fade(self.a.id, "middle", 1.0)

    def test_effective_fades_shrink_when_the_clip_is_trimmed(self):
        self.p.set_fade(self.a.id, "in", 2.0)
        self.p.set_fade(self.a.id, "out", 2.0)
        self.a.duration = 2.0                                  # e.g. trimmed down
        fi, fo = self.a.fades()
        self.assertAlmostEqual(fi, 1.0)
        self.assertAlmostEqual(fo, 1.0)
        self.assertAlmostEqual(self.a.fade_in, 2.0)            # what the user set is kept, it comes back if the clip grows

    def test_fade_gain_is_linear(self):
        self.p.set_fade(self.a.id, "in", 2.0)
        self.p.set_fade(self.a.id, "out", 3.0)
        g = self.a.fade_gain
        self.assertAlmostEqual(g(0.0), 0.0)
        self.assertAlmostEqual(g(1.0), 0.5)
        self.assertAlmostEqual(g(2.0), 1.0)
        self.assertAlmostEqual(g(3.0), 1.0)                    # fade-out starts at 6 - 3
        self.assertAlmostEqual(g(4.5), 0.5)
        self.assertAlmostEqual(g(6.0), 0.0)

    def test_only_clips_with_sound_can_fade(self):
        v = self.p.place_clip("m1", duration=3.0)
        i = self.p.place_clip("m2", start=30.0)
        s = self.p.place_clip("m4", start=40.0, duration=3.0)
        t = self.p.add_title(0.0)
        self.assertTrue(self.p.can_fade(self.a.id))
        self.assertTrue(self.p.can_fade(v.id))
        for c in (i, s, t):
            self.assertFalse(self.p.can_fade(c.id), c.id)
            self.assertFalse(self.p.set_fade(c.id, "in", 1.0))
            self.assertEqual(c.fade_in, 0.0)

    def test_locked_layer_refuses(self):
        self.p.tracks[self.a.track]["locked"] = True
        self.assertFalse(self.p.set_fade(self.a.id, "in", 1.0))
        self.assertEqual(self.a.fade_in, 0.0)

    def test_split_puts_the_fade_in_left_and_the_fade_out_right(self):
        self.p.set_fade(self.a.id, "in", 1.0)
        self.p.set_fade(self.a.id, "out", 2.0)
        right = self.p.split(self.a.id, 3.0)
        self.assertEqual((self.a.fade_in, self.a.fade_out), (1.0, 0.0))
        self.assertEqual((right.fade_in, right.fade_out), (0.0, 2.0))

    def test_reset_effects_leaves_fades_alone(self):
        from venma_video.core.project import CLIP_DEFAULTS
        self.p.set_fade(self.a.id, "in", 1.0)
        self.assertNotIn("fade_in", CLIP_DEFAULTS)
        self.assertNotIn("fade_out", CLIP_DEFAULTS)


# ── commands ─────────────────────────────────────────────────────────────────────────────────
class FadeCommandTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ft, "ffmpeg_path", return_value="ffmpeg")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_fades_no_filters(self):
        self.assertEqual(ft.fade_filters(0, 0, 5.0), [])

    def test_whole_clip(self):
        self.assertEqual(ft.fade_filters(1.0, 2.0, 6.0),
                         ["afade=t=in:st=0:d=1", "afade=t=out:st=4:d=2"])

    def test_starting_inside_the_fade_in_continues_the_ramp(self):
        self.assertEqual(ft.fade_filters(2.0, 0, 6.0, offset=0.5),
                         ["afade=t=in:st=0:d=1.5:silence=0.25"])

    def test_starting_after_the_fade_in_has_no_fade_in(self):
        self.assertEqual(ft.fade_filters(2.0, 0, 6.0, offset=2.0), [])

    def test_starting_before_the_fade_out_shifts_it(self):
        self.assertEqual(ft.fade_filters(0, 2.0, 6.0, offset=1.0), ["afade=t=out:st=3:d=2"])

    def test_starting_inside_the_fade_out_continues_the_ramp(self):
        # 1 s of the 2 s fade-out is already behind us: start at half gain and finish with the clip
        self.assertEqual(ft.fade_filters(0, 2.0, 6.0, offset=5.0),
                         ["afade=t=out:st=0:d=1:unity=0.5"])

    def test_export_applies_the_fades_after_volume_and_before_the_delay(self):
        p = make_project()
        c = p.place_clip("m3", start=2.0, duration=6.0)
        p.set_fade(c.id, "in", 1.0)
        p.set_fade(c.id, "out", 2.0)
        cmd = ft.build_export_command(p, "/tmp/out.mp4")
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("volume=1,afade=t=in:st=0:d=1,afade=t=out:st=4:d=2,adelay=2000|2000", graph)

    def test_export_without_fades_is_unchanged(self):
        p = make_project()
        p.place_clip("m3", duration=6.0)
        cmd = ft.build_export_command(p, "/tmp/out.mp4")
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertNotIn("afade", graph)
        self.assertIn("volume=1,adelay=0|0", graph)

    def test_export_fades_follow_the_clip_after_speed_change(self):
        p = make_project()
        c = p.place_clip("m3", duration=8.0)
        p.set_fade(c.id, "out", 3.0)
        p.set_speed(c.id, 4.0)                                  # now 2 s long: the 3 s fade shrinks to fit
        cmd = ft.build_export_command(p, "/tmp/out.mp4")
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("afade=t=out:st=0:d=2", graph)

    def test_preview_audio_command_carries_the_fades(self):
        cmd = ft.audio_stream_cmd("a.wav", 0.0, 6.0, 1.0, 48000, 2, fade=(1.0, 2.0, 6.0, 0.0))
        af = cmd[cmd.index("-af") + 1]
        self.assertTrue(af.endswith("afade=t=in:st=0:d=1,afade=t=out:st=4:d=2"), af)
        plain = ft.audio_stream_cmd("a.wav", 0.0, 6.0, 1.0, 48000, 2)
        self.assertNotIn("afade", plain[plain.index("-af") + 1])


# ── waveform data ────────────────────────────────────────────────────────────────────────────
class WaveformDataTests(unittest.TestCase):
    def test_peak_picks_the_loudest_value_in_the_range(self):
        w = waveform.Waveform(bytes([0, 10, 255, 20, 0]), rate=10)
        self.assertAlmostEqual(w.duration, 0.5)
        self.assertAlmostEqual(w.peak(0.0, 0.2), 10 / 255)
        self.assertAlmostEqual(w.peak(0.0, 0.5), 1.0)
        self.assertAlmostEqual(w.peak(0.3, 0.5), 20 / 255)

    def test_range_order_does_not_matter_and_outside_is_silence(self):
        w = waveform.Waveform(bytes([0, 10, 255, 20, 0]), rate=10)
        self.assertEqual(w.peak(0.5, 0.0), w.peak(0.0, 0.5))
        self.assertEqual(w.peak(5.0, 6.0), 0.0)
        self.assertEqual(w.peak(-3.0, -1.0), 0.0)

    def test_a_range_narrower_than_one_peak_still_reads_it(self):
        w = waveform.Waveform(bytes([0, 200, 0]), rate=10)
        self.assertAlmostEqual(w.peak(0.12, 0.121), 200 / 255)

    def test_bins_and_reverse(self):
        w = waveform.Waveform(bytes([10, 10, 100, 100, 200, 200]), rate=2)
        fwd = w.bins(0.0, 3.0, 3)
        self.assertEqual([round(v * 255) for v in fwd], [10, 100, 200])
        self.assertEqual(w.bins(3.0, 0.0, 3), fwd[::-1])
        self.assertEqual(w.bins(0, 1, 0), [])

    def test_fold_turns_samples_into_peaks(self):
        a = array.array("h", [0] * waveform.BUCKET + [16384] * waveform.BUCKET + [-32768] * waveform.BUCKET)
        out = bytearray()
        waveform._fold(a.tobytes(), out)
        self.assertEqual(list(out), [0, 127, 255])


def write_wav(path, segments, rate=RATE, channels=1):
    """segments: [(seconds, amplitude 0..1)] -> a square-ish constant-level wav (easy to measure)."""
    data = array.array("h")
    for secs, amp in segments:
        data.extend([int(amp * 32767)] * int(secs * rate) * channels)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def write_sine(path, seconds, amp=0.5, freq=440.0, rate=RATE):
    """A steady tone (not a constant level: codecs and filters treat DC specially)."""
    n = int(seconds * rate)
    data = array.array("h", [int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)])
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def decode_pcm(path, rate=RATE):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = array.array("h")
    a.frombytes(raw)
    return a


def level(samples, t0, t1, rate=RATE):
    """Peak level 0..1 of ``samples`` between t0 and t1 seconds."""
    seg = samples[int(t0 * rate):int(t1 * rate)]
    return max(abs(x) for x in seg) / 32768.0


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class RealWaveformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_decode_follows_the_loudness_over_time(self):
        path = os.path.join(self.tmp, "levels.wav")
        write_wav(path, [(1.0, 0.1), (1.0, 0.5), (1.0, 0.9)])
        w = waveform.decode(path)
        self.assertAlmostEqual(w.duration, 3.0, delta=0.1)
        self.assertAlmostEqual(w.peak(0.2, 0.8), 0.1, delta=0.02)
        self.assertAlmostEqual(w.peak(1.2, 1.8), 0.5, delta=0.02)
        self.assertAlmostEqual(w.peak(2.2, 2.8), 0.9, delta=0.02)

    def test_decode_a_video_files_sound_and_stereo(self):
        path = os.path.join(self.tmp, "tone.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=10:d=2",
                        "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", path], check=True)
        w = waveform.decode(path)
        self.assertAlmostEqual(w.duration, 2.0, delta=0.2)
        self.assertGreater(w.peak(0.5, 1.5), 0.1)

    def test_a_file_without_sound_raises(self):
        path = os.path.join(self.tmp, "mute.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=10:d=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)
        with self.assertRaises(RuntimeError):
            waveform.decode(path)

    def test_cancel_returns_none(self):
        path = os.path.join(self.tmp, "long.wav")
        write_wav(path, [(5.0, 0.3)])
        self.assertIsNone(waveform.decode(path, cancelled=lambda: True))

    def test_long_files_are_decoded_in_pieces(self):
        # longer than one READ_BYTES block, with a loud burst past the first block
        path = os.path.join(self.tmp, "burst.wav")
        write_wav(path, [(14.0, 0.05), (1.0, 0.8), (1.0, 0.05)])
        w = waveform.decode(path)
        self.assertAlmostEqual(w.duration, 16.0, delta=0.15)
        self.assertAlmostEqual(w.peak(14.2, 14.8), 0.8, delta=0.02)
        self.assertAlmostEqual(w.peak(5.0, 6.0), 0.05, delta=0.02)


# ── real renders: do the fades actually change the sound? ───────────────────────────────────
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class RealFadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = os.path.join(cls.tmp, "tone.wav")
        write_sine(cls.src, 8.0, amp=0.5)                       # a steady tone: gain = level / reference level
        cls._ref = None

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def project(self, dur=6.0, start=0.0):
        p = Project(320, 180, 10)
        p.add_media(MediaItem("m3", self.src, "audio", 8.0, has_audio=True))
        c = p.place_clip("m3", start=start, duration=dur)
        return p, c

    def render(self, p, name="out.mkv"):
        """Run the real export graph but keep the sound lossless (WebM / Opus would colour the measurements)."""
        out = os.path.join(self.tmp, name)
        cmd = ft.build_export_command(p, out, fmt="WebM (VP9 + Opus)", quality="Low")
        cmd = cmd[:cmd.index("-map")] + ["-map", "[vout]", "-map", "[aout]", "-c:v", "mpeg4", "-c:a", "pcm_s16le",
                                          "-t", f"{p.duration():.3f}", out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        return decode_pcm(out)

    @property
    def ref(self):
        """What "full level" measures after the export (the mono -> stereo -> mono trip changes it a little)."""
        if type(self)._ref is None:
            p, _ = self.project()
            type(self)._ref = level(self.render(p, "ref.mkv"), 2.0, 3.0)
        return type(self)._ref

    def gain(self, pcm, t0, t1):
        return level(pcm, t0, t1) / self.ref

    def test_export_fades_in_and_out(self):
        p, c = self.project()
        p.set_fade(c.id, "in", 2.0)
        p.set_fade(c.id, "out", 2.0)
        pcm = self.render(p)
        self.assertLess(self.gain(pcm, 0.0, 0.1), 0.12)         # starts silent
        self.assertAlmostEqual(self.gain(pcm, 0.9, 1.1), 0.5, delta=0.12)    # half way up
        self.assertAlmostEqual(self.gain(pcm, 2.5, 3.5), 1.0, delta=0.1)     # steady in the middle
        self.assertAlmostEqual(self.gain(pcm, 4.9, 5.1), 0.5, delta=0.12)    # half way down
        self.assertLess(self.gain(pcm, 5.85, 5.99), 0.12)       # ends silent

    def test_no_fades_stay_at_full_level(self):
        p, c = self.project()
        pcm = self.render(p, "plain.mkv")
        self.assertAlmostEqual(self.gain(pcm, 0.0, 0.1), 1.0, delta=0.1)
        self.assertAlmostEqual(self.gain(pcm, 5.8, 5.95), 1.0, delta=0.1)

    def test_fades_work_with_speed_and_a_delayed_start(self):
        p, c = self.project(dur=8.0, start=1.0)
        p.set_speed(c.id, 2.0)                                  # 4 s long, from 1 s to 5 s on the timeline
        p.set_fade(c.id, "in", 1.0)
        p.set_fade(c.id, "out", 1.0)
        pcm = self.render(p, "speed.mkv")
        self.assertLess(self.gain(pcm, 0.0, 0.9), 0.05)         # nothing before the clip
        self.assertLess(self.gain(pcm, 1.0, 1.1), 0.2)
        self.assertAlmostEqual(self.gain(pcm, 2.5, 3.5), 1.0, delta=0.1)
        self.assertLess(self.gain(pcm, 4.85, 4.99), 0.2)

    def test_preview_stream_starting_mid_fade_continues_the_ramp(self):
        p, c = self.project()
        p.set_fade(c.id, "in", 2.0)
        p.set_fade(c.id, "out", 2.0)
        fi, fo = c.fades()

        gain = lambda a, t0, t1: level(a, t0, t1) / 0.5        # noqa: E731  (preview streams are mono, no downmix)

        def play(off):                                          # the preview starting ``off`` s into the clip
            cmd = ft.audio_stream_cmd(self.src, c.source_time(off), (c.duration - off) * c.speed, 1.0, RATE, 1,
                                      fade=(fi, fo, c.duration, off))
            raw = subprocess.run(cmd, capture_output=True, check=True).stdout
            a = array.array("h")
            a.frombytes(raw)
            return a

        a = play(1.0)                                            # in the middle of the fade-in: starts at half gain
        self.assertAlmostEqual(gain(a, 0.0, 0.05), 0.5, delta=0.1)
        self.assertAlmostEqual(gain(a, 0.95, 1.05), 1.0, delta=0.1)
        b = play(5.0)                                            # one second before the end: half gain, then silence
        self.assertAlmostEqual(gain(b, 0.0, 0.05), 0.5, delta=0.1)
        self.assertLess(gain(b, 0.9, len(b) / RATE - 0.001), 0.15)
        d = play(3.0)                                            # before the fade-out: it starts later
        self.assertAlmostEqual(gain(d, 0.0, 0.5), 1.0, delta=0.1)
        self.assertLess(gain(d, 2.9, len(d) / RATE - 0.001), 0.15)

    def test_reversed_preview_pieces_fade_in_step(self):
        p, c = self.project(dur=4.0)
        p.set_reverse(c.id, True)
        p.set_fade(c.id, "out", 2.0)
        fi, fo = c.fades()
        top = c.in_point + c.span
        total = array.array("h")
        for lo, hi, _ in ft.reverse_chunks(c, 0.0, 1.0):
            off = (top - hi) / c.speed
            cmd = ft.audio_stream_cmd(self.src, lo, hi - lo, 1.0, RATE, 1, speed=c.speed, reverse=True,
                                      fade=(fi, fo, c.duration, off))
            part = array.array("h")
            part.frombytes(subprocess.run(cmd, capture_output=True, check=True).stdout)
            total.extend(part)
        gain = lambda a, t0, t1: level(a, t0, t1) / 0.5        # noqa: E731
        self.assertAlmostEqual(gain(total, 0.2, 1.8), 1.0, delta=0.1)
        self.assertAlmostEqual(gain(total, 2.9, 3.1), 0.5, delta=0.15)
        self.assertLess(gain(total, 3.85, 3.99), 0.15)


# ── UI (headless) ────────────────────────────────────────────────────────────────────────────
try:
    import PyQt5  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PyQt5 not installed")
class FadeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _dispose(w):
        from PyQt5.QtCore import QCoreApplication, QEvent
        w.engine.shutdown()
        QCoreApplication.processEvents()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def window(self):
        from venma_video.ui.main_window import MainWindow
        w = MainWindow()
        self.addCleanup(self._dispose, w)
        p = w.project
        p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
        p.add_media(MediaItem("m2", "/tmp/b.png", "image", 5.0, 800, 600))
        p.add_media(MediaItem("m3", "/tmp/c.wav", "audio", 8.0, has_audio=True))
        w.vclip = p.place_clip("m1", duration=6.0)
        w.iclip = p.place_clip("m2", start=20.0)
        w.aclip = p.place_clip("m3", duration=6.0)
        w._reset_history("Test")
        w._refresh_all()
        return w

    def test_fade_rows_only_for_clips_with_sound(self):
        w = self.window()
        for clip, expect in ((w.aclip, True), (w.vclip, True), (w.iclip, False)):
            w.timeline.canvas.select(clip.id)
            for which in ("in", "out"):
                row = w.props.fade_rows[which]
                self.assertEqual(row.isEnabled(), expect, (clip.id, which))

    def test_slider_edit_changes_the_clip_and_is_one_undo_step(self):
        w = self.window()
        w.timeline.canvas.select(w.aclip.id)
        steps = len(w.history)
        w.props.fade_rows["in"].slider.setValue(15)             # tenths of a second
        w.props.fade_rows["in"].slider.setValue(20)
        w._edit_timer.stop()
        w.commit(w._edit_label)
        self.assertAlmostEqual(w.project.clip(w.aclip.id).fade_in, 2.0)
        self.assertEqual(len(w.history), steps + 1)
        w.undo()
        self.assertEqual(w.project.clip(w.aclip.id).fade_in, 0.0)

    def test_overlapping_fades_snap_the_slider_back(self):
        w = self.window()
        w.timeline.canvas.select(w.aclip.id)
        w.props.fade_rows["in"].slider.setValue(40)
        w.props.fade_rows["out"].slider.setValue(60)
        c = w.project.clip(w.aclip.id)
        self.assertAlmostEqual(c.fade_in + c.fade_out, 6.0, delta=0.01)
        self.assertEqual(w.props.fade_rows["out"].slider.value(), round(c.fade_out * 10))

    def test_locked_layer_refuses(self):
        w = self.window()
        w.timeline.canvas.select(w.aclip.id)
        w.project.tracks[w.aclip.track]["locked"] = True
        w._on_fade("in", 1.0)
        self.assertEqual(w.project.clip(w.aclip.id).fade_in, 0.0)

    def test_timeline_paints_waveforms_and_fade_ramps(self):
        from PyQt5.QtGui import QPixmap
        w = self.window()
        w.project.set_fade(w.aclip.id, "in", 1.0)
        w.project.set_fade(w.aclip.id, "out", 1.0)
        w.waves["/tmp/c.wav"] = waveform.Waveform(bytes([40, 120, 220, 90] * 100))
        w.waves["/tmp/a.mp4"] = waveform.Waveform(bytes([60] * 400))
        w.resize(1200, 800)
        w.timeline.canvas.invalidate()
        pm = QPixmap(w.timeline.canvas.size())
        w.timeline.canvas.render(pm)                            # must not raise
        self.assertFalse(pm.isNull())


if __name__ == "__main__":
    unittest.main()
