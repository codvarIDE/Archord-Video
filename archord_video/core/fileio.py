"""Crash-safe file writing (no Qt)."""
import os
import tempfile


def _umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask


def atomic_write_text(path, text):
    """Write ``text`` to ``path`` so a crash or full disk can never leave a half-written file.

    The text goes to a temporary file in the same folder, is flushed to disk, and then replaces ``path`` in a
    single step. If anything fails the original file (if there is one) is untouched and the temp file is removed.
    """
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:                       # keep the permissions of the file being replaced (mkstemp makes it private)
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        except FileNotFoundError:
            os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
