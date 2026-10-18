"""Audio waveforms for the timeline: decode a file's sound once into a compact list of peaks.

No Qt in here (like the rest of ``core``). A :class:`Waveform` holds one peak per ``PEAKS_PER_SEC`` of source audio,
each a byte (0..255 = silence..full scale), so an hour of sound is about 180 KB. The timeline asks it for the peak of
any source range (``peak`` / ``bins``) at whatever zoom it is drawing, so zooming never decodes anything again.
"""
import array
import subprocess
import sys

from . import ffmpeg_tools as ft

PEAKS_PER_SEC = 50
DECODE_RATE = 4000                                 # mono samples per second the audio is decoded to
BUCKET = DECODE_RATE // PEAKS_PER_SEC              # samples folded into one peak (80)
READ_BYTES = BUCKET * 2 * 600                      # read ~12 s of audio at a time: memory stays flat on long files


class Waveform:
    """Peaks of one audio source. ``peaks`` is a ``bytes`` object, one value per ``rate``-th of a second."""

    def __init__(self, peaks, rate=PEAKS_PER_SEC):
        self.peaks = bytes(peaks)
        self.rate = float(rate)

    def __len__(self):
        return len(self.peaks)

    @property
    def duration(self):
        return len(self.peaks) / self.rate

    def peak(self, t0, t1):
        """Loudest peak (0..1) of the source between ``t0`` and ``t1`` seconds, given in either order.
        Anything outside the file counts as silence."""
        if t1 < t0:
            t0, t1 = t1, t0
        a = int(t0 * self.rate)
        b = max(a + 1, int(t1 * self.rate + 0.999999))
        a, b = max(0, a), min(len(self.peaks), b)
        if a >= b:
            return 0.0
        return max(self.peaks[a:b]) / 255.0

    def bins(self, t0, t1, n):
        """``n`` peaks (0..1) evenly spread over ``t0 .. t1``. ``t0 > t1`` gives them in reverse (a reversed clip)."""
        if n <= 0:
            return []
        step = (t1 - t0) / n
        return [self.peak(t0 + i * step, t0 + (i + 1) * step) for i in range(n)]


def decode(path, cancelled=None):
    """Decode ``path`` with FFmpeg into a :class:`Waveform`.

    ``cancelled`` is an optional function; when it returns True decoding stops and ``None`` comes back. Raises
    ``RuntimeError`` if FFmpeg is missing or the file has no decodable audio."""
    proc = subprocess.Popen(ft.waveform_cmd(path), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, **ft._NOWIN)
    peaks = bytearray()
    pending = b""
    try:
        while True:
            if cancelled is not None and cancelled():
                proc.kill()
                return None
            chunk = proc.stdout.read(READ_BYTES)
            if not chunk:
                break
            pending += chunk
            whole = len(pending) - len(pending) % (BUCKET * 2)
            if whole:
                _fold(pending[:whole], peaks)
                pending = pending[whole:]
        if len(pending) >= 2:                                   # the last, shorter bucket
            _fold(pending[:len(pending) - len(pending) % 2], peaks)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.wait()
    if not peaks:
        raise RuntimeError("no audio could be decoded from " + str(path))
    return Waveform(peaks)


def _fold(raw, out):
    """Append one peak byte per BUCKET samples of 16-bit little-endian PCM ``raw`` to ``out``."""
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    for i in range(0, len(a), BUCKET):
        seg = a[i:i + BUCKET]
        out.append(min(255, max(max(seg), -min(seg)) * 255 // 32768))
