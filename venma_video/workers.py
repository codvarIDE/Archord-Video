"""Background threads: media probing / thumbnails and the FFmpeg export."""
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage

from .core import ffmpeg_tools as ft


class MediaWorker(QThread):
    """Probe and/or thumbnail a list of files without blocking the UI.

    jobs: list of dicts(path=..., probe=bool, kind=..., duration=...)
    Emits ``ready(path, info_or_None, QImage_or_None, error_text)`` once per job.
    """
    ready = pyqtSignal(str, object, object, str)

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = list(jobs)

    def _one(self, job):
        path, info, img, err = job["path"], None, None, ""
        try:
            if job.get("probe", True):
                info = ft.probe(path)
            kind = (info or job)["kind"]
            dur = (info or job).get("duration", 0.0)
            at = min(1.0, dur * 0.1) if kind == "video" else 0.0
            png = ft.thumbnail_png(path, kind, at, height=360)
            if png:
                q = QImage.fromData(png)
                img = q if not q.isNull() else None
        except Exception as e:       # keep going with the remaining files
            err = str(e)
        return path, info, img, err

    def run(self):
        # ffmpeg/ffprobe run as separate processes, so a thread pool probes several files at once;
        # results are still emitted in the original order.
        n = max(1, min(4, os.cpu_count() or 2, len(self.jobs)))
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(self._one, j) for j in self.jobs]
            for f in futures:
                self.ready.emit(*f.result())


class ExportWorker(QThread):
    """Run an ffmpeg export command and report progress (0..1).

    ``prepare`` is optional: a function ``prepare(progress, cancelled) -> argv`` run first, inside the thread, for
    work the command depends on (reversed clips are rendered backwards, then the command is built). Its progress fills
    the first quarter of the bar, the export itself the rest."""
    progress = pyqtSignal(float)
    status = pyqtSignal(str)                # what the dialog should say ("Preparing reversed clips…" / "Rendering…")
    finished_ok = pyqtSignal(bool, str)     # (success, message)

    PREP_SHARE = 0.25

    def __init__(self, cmd, total_seconds, parent=None, prepare=None):
        super().__init__(parent)
        self.cmd = cmd
        self.prepare = prepare
        self.total = max(0.001, float(total_seconds))
        self._proc = None
        self._cancel = False
        self._base, self._scale = 0.0, 1.0

    def cancel(self):
        self._cancel = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def run(self):
        err = tempfile.TemporaryFile()
        try:
            if self.prepare is not None:
                self.status.emit("Preparing reversed clips…")
                self.cmd = self.prepare(lambda f: self.progress.emit(self.PREP_SHARE * min(1.0, f)), lambda: self._cancel)
                self.status.emit("Rendering…")
                self._base, self._scale = self.PREP_SHARE, 1.0 - self.PREP_SHARE
            if self._cancel:
                self.finished_ok.emit(False, "Export cancelled.")
                return
            kw = {"creationflags": 0x08000000} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
            self._proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=err,
                                          stdin=subprocess.DEVNULL, text=True, **kw)
            for line in self._proc.stdout:
                key, _, val = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms"):      # ffmpeg reports microseconds in both
                    try:
                        frac = min(1.0, max(0.0, int(val) / 1e6 / self.total))
                        self.progress.emit(self._base + self._scale * frac)
                    except ValueError:
                        pass
            rc = self._proc.wait()
            if self._cancel:
                self.finished_ok.emit(False, "Export cancelled.")
            elif rc == 0:
                self.progress.emit(1.0)
                self.finished_ok.emit(True, "Export finished.")
            else:
                err.seek(0)
                msg = err.read().decode("utf-8", "replace").strip().splitlines()
                self.finished_ok.emit(False, "\n".join(msg[-6:]) or f"ffmpeg exited with code {rc}")
        except Exception as e:
            self.finished_ok.emit(False, str(e))
        finally:
            err.close()
