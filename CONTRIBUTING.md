# Contributing

Thanks for helping with Archord Video!

## Setup

```bash
git clone <your fork>
cd archord-video
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

Install FFmpeg (see the README) to run the app. Tests don't need FFmpeg or a display.

## Workflow

1. Create a branch from `main`: `git checkout -b feat/short-description`
   (prefixes: `feat/`, `fix/`, `docs/`, `refactor/`, `test/`).
2. Make focused commits. Imperative, present-tense messages: `Fix ripple delete on audio tracks`.
3. Run `pytest` and `ruff check .` locally.
4. Open a pull request against `main` and fill in the template. Keep PRs small — one concern each.
5. At least one review is needed before merging. Prefer *Squash and merge*.

## Code guidelines

- Keep `archord_video/core/` free of Qt imports so it stays testable.
- Use relative imports inside the package (`from ..core import ffmpeg_tools as ft`).
- Colours, fonts and icons come from `ui/theme.py` — don't hard-code them in widgets.
- New timeline behaviour = a method on `Project` + a test in `tests/test_project.py`.
- Never commit media files, FFmpeg binaries or `.avproj` files with personal paths
  (the `.gitignore` already blocks most of these).

## Reporting bugs

Use the bug-report issue template. Include your OS, Python version, FFmpeg version
(`ffmpeg -version`) and, if possible, the steps to reproduce.
