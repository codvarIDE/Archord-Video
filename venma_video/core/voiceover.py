"""Voiceover helpers (pure Python, no Qt): WAV writing, level metering and file naming.

The microphone is captured as raw 16-bit PCM and written straight to a ``.wav`` file, so recording
needs no codec and no FFmpeg; the finished file is an ordinary audio media item.
"""
import array
import os
import re
import sys
import wave

SAMPLE_WIDTH = 2                      # bytes per sample: 16-bit signed PCM
MIN_TAKE = 0.25                       # seconds; a shorter take is treated as an accidental click and dropped
FOLDER_NAME = "Voiceovers"
_NAME_RE = re.compile(r"^Voiceover (\d+)\.wav$", re.IGNORECASE)


class WavSink:
    """Append raw little-endian 16-bit PCM chunks to a WAV file and keep count of what was written."""

    def __init__(self, path, rate=48000, channels=1):
        self.path = path
        self.rate = int(rate)
        self.channels = max(1, int(channels))
        self.frames = 0                                   # sample frames (one sample per channel) written so far
        self._w = wave.open(path, "wb")
        self._w.setnchannels(self.channels)
        self._w.setsampwidth(SAMPLE_WIDTH)
        self._w.setframerate(self.rate)

    @property
    def duration(self):
        return self.frames / float(self.rate)

    def write(self, pcm):
        if self._w is None or not pcm:
            return
        frame = SAMPLE_WIDTH * self.channels
        pcm = bytes(pcm[:len(pcm) - len(pcm) % frame])    # never split a frame (a half sample would shift the channels)
        if pcm:
            self._w.writeframes(pcm)
            self.frames += len(pcm) // frame

    def close(self):
        if self._w is not None:
            self._w.close()
            self._w = None


def peak_level(pcm):
    """Loudest sample in a chunk of 16-bit little-endian PCM, 0.0 (silence) .. 1.0 (full scale)."""
    n = len(pcm) // SAMPLE_WIDTH
    if n == 0:
        return 0.0
    a = array.array("h")
    a.frombytes(bytes(pcm[:n * SAMPLE_WIDTH]))
    if sys.byteorder == "big":
        a.byteswap()
    return min(1.0, max(max(a), -min(a)) / 32768.0)


def recordings_dir(project_path, fallback):
    """Where takes are saved: a ``Voiceovers`` folder next to the saved project file, else ``fallback``."""
    if project_path:
        return os.path.join(os.path.dirname(os.path.abspath(project_path)), FOLDER_NAME)
    return fallback


def next_voiceover_path(folder):
    """``<folder>/Voiceover N.wav`` with N one above the highest number already in the folder."""
    top = 0
    try:
        for name in os.listdir(folder):
            m = _NAME_RE.match(name)
            if m:
                top = max(top, int(m.group(1)))
    except OSError:
        pass
    return os.path.join(folder, f"Voiceover {top + 1}.wav")
