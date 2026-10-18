"""Freeze-frame helpers (pure Python, no Qt): where the stills are saved and how they are named."""
import os
import re

FOLDER_NAME = "Freeze Frames"
_NAME_RE = re.compile(r"^Freeze (\d+)\.png$", re.IGNORECASE)


def frames_dir(project_path, fallback):
    """Where stills are saved: a ``Freeze Frames`` folder next to the saved project file, else ``fallback``."""
    if project_path:
        return os.path.join(os.path.dirname(os.path.abspath(project_path)), FOLDER_NAME)
    return fallback


def next_freeze_path(folder):
    """``<folder>/Freeze N.png`` with N one above the highest number already in the folder."""
    top = 0
    try:
        for name in os.listdir(folder):
            m = _NAME_RE.match(name)
            if m:
                top = max(top, int(m.group(1)))
    except OSError:
        pass
    return os.path.join(folder, f"Freeze {top + 1}.png")
