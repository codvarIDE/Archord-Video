"""Autosave and crash recovery (no Qt, so it is easy to test).

How it works
------------
Every running copy of the app is a *session* with a random id. While it runs it keeps two files in the autosave
folder:

* ``<session>.lock``     - says "this session is alive" (its process id; the file is touched every few seconds).
* ``<session>.avauto``   - the latest snapshot of the unsaved project (JSON), rewritten atomically.

A normal exit (or a successful Save) removes the snapshot, and a normal exit removes the lock too. If the app
crashes, is killed or the power goes, the snapshot is left behind with a dead session, and the next start offers to
recover it. A session counts as dead when its process is gone *or* its lock has not been touched for
``HEARTBEAT_TTL`` seconds (this covers a reused process id after a reboot). Snapshots that belong to another app
window that is still running are never offered.
"""
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field

from .fileio import atomic_write_text

FOLDER = "Autosave"           # sub-folder of the app data folder
FORMAT = 1
AUTOSAVE_EXT = ".avauto"
LOCK_EXT = ".lock"
CORRUPT_EXT = ".corrupt"
HEARTBEAT_TTL = 180.0          # seconds without a heartbeat before a session is treated as dead
JUNK_AGE = 24 * 3600.0         # leftover temp files older than this are deleted


def pid_alive(pid):
    """True if a process with this id exists. When in doubt it says True; the heartbeat age is the backstop."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:                        # never os.kill here: on Windows that would terminate the process
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            handle = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5                   # access denied: it exists
            try:
                code = wintypes.DWORD()
                if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == 259                              # STILL_ACTIVE
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class Entry:
    """One recoverable snapshot found on disk."""
    session: str
    path: str
    lock: str
    project: dict = field(repr=False)
    project_path: str = None       # where the project was saved before the crash (None: never saved)
    saved_at: float = 0.0

    @property
    def name(self):
        return os.path.basename(self.project_path) if self.project_path else "Untitled"


class Autosaver:
    """Owns this session's lock and snapshot. Nothing here raises into the UI: failures return False."""

    def __init__(self, directory, session=None):
        self.directory = directory
        self.session = session or uuid.uuid4().hex
        self.path = os.path.join(directory, self.session + AUTOSAVE_EXT)
        self.lock = os.path.join(directory, self.session + LOCK_EXT)
        self.last_error = None
        self._last_key = None      # (snapshot, project path) that the snapshot file currently holds

    def start(self):
        try:
            os.makedirs(self.directory, exist_ok=True)
            atomic_write_text(self.lock, json.dumps({"pid": os.getpid(), "started": time.time()}))
            return True
        except OSError as e:
            self.last_error = e
            return False

    def heartbeat(self):
        try:
            os.utime(self.lock, None)
        except OSError:
            self.start()           # the lock vanished (cleaned up by something else): put it back

    def write(self, project_dict, project_path=None):
        payload = {"format": FORMAT, "saved_at": time.time(), "pid": os.getpid(),
                   "project_path": project_path, "project": project_dict}
        try:
            atomic_write_text(self.path, json.dumps(payload))
            self.last_error = None
            return True
        except (OSError, TypeError, ValueError) as e:
            self.last_error = e
            return False

    def sync(self, snapshot, project_path=None):
        """Bring the snapshot file up to date with ``snapshot`` (a ``Project.snapshot()`` string).

        Does nothing if it already holds exactly that. Returns True when the file is current, False if writing failed
        (it is simply tried again next time).
        """
        key = (snapshot, project_path)
        if key == self._last_key:
            return True
        if not self.write(json.loads(snapshot), project_path):
            return False
        self._last_key = key
        return True

    def clear(self):
        """Forget the snapshot (the project was saved, or the user chose to discard it)."""
        self._last_key = None
        _remove(self.path)

    def close(self):
        """Clean exit: nothing is left behind to recover."""
        self.clear()
        _remove(self.lock)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _session_alive(lock_path, now):
    try:
        age = now - os.path.getmtime(lock_path)
        with open(lock_path, "r", encoding="utf-8") as f:
            pid = json.load(f).get("pid")
    except FileNotFoundError:
        return False
    except (OSError, ValueError, AttributeError):
        return False               # unreadable lock: nobody is keeping it fresh
    return age < HEARTBEAT_TTL and pid_alive(pid)


def find_recoverable(directory, exclude_session=None, now=None):
    """Snapshots left behind by sessions that are no longer running, newest first. Also tidies up stale files."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    entries = []
    for name in names:
        full = os.path.join(directory, name)
        stem, ext = os.path.splitext(name)
        if ext == AUTOSAVE_EXT:
            if stem == exclude_session:
                continue
            lock = os.path.join(directory, stem + LOCK_EXT)
            if _session_alive(lock, now):
                continue
            try:
                with open(full, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not isinstance(data.get("project"), dict):
                    raise ValueError("not an autosave")
            except (OSError, ValueError):
                _set_aside(full)
                continue
            if data.get("format") != FORMAT:
                continue           # written by a different version of the app: leave it alone
            pp = data.get("project_path")
            entries.append(Entry(stem, full, lock, data["project"], pp if isinstance(pp, str) else None,
                                 float(data.get("saved_at") or os.path.getmtime(full))))
        elif ext == LOCK_EXT:
            if stem != exclude_session and not os.path.exists(os.path.join(directory, stem + AUTOSAVE_EXT)) \
                    and not _session_alive(full, now):
                _remove(full)      # orphan lock of a session that ended without anything to recover
        elif ext == ".part":
            try:
                if now - os.path.getmtime(full) > JUNK_AGE:
                    _remove(full)
            except OSError:
                pass
    entries.sort(key=lambda e: e.saved_at, reverse=True)
    return entries


def _set_aside(path):
    """Keep an unreadable snapshot for debugging, but out of the recovery list."""
    try:
        os.replace(path, path + CORRUPT_EXT)
    except OSError:
        _remove(path)


def discard(entry):
    _remove(entry.path)
    _remove(entry.lock)


def quarantine(entry):
    """A snapshot that turned out to be unusable: keep it for debugging, stop offering it."""
    _set_aside(entry.path)
    _remove(entry.lock)


def export(entry, dest_path):
    """Write a recovered snapshot out as an ordinary project file (same JSON as Project.save)."""
    atomic_write_text(dest_path, json.dumps(entry.project, indent=1))


def rescue(entries, folder, ext=".avproj"):
    """Save snapshots as ordinary project files in ``folder`` (never overwriting) and drop them. Returns how many."""
    saved = 0
    try:
        os.makedirs(folder, exist_ok=True)
        for e in entries:
            stem = os.path.splitext(e.name)[0]
            label = f"{stem} (recovered {time.strftime('%Y-%m-%d %H%M', time.localtime(e.saved_at))})"
            dest, n = os.path.join(folder, label + ext), 2
            while os.path.exists(dest):
                dest = os.path.join(folder, f"{label} {n}{ext}")
                n += 1
            export(e, dest)
            discard(e)
            saved += 1
    except OSError:
        pass
    return saved
