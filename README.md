# Venma Video

[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)

A video editor built with the same design language and workspace layout as Venma (PyQt5):
warm paper/clay theme, vertical tool palette on the left, context options bar on top, tabbed
side panels, light/dark mode. Currently **v0.1** (initial version).

## Quick start

```bash
git clone https://github.com/OWNER/REPO.git
cd REPO
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m venma_video            # or: python main.py clip1.mp4 clip2.mp4
```

FFmpeg is required (`ffmpeg` + `ffprobe`):

| OS | Install |
|---|---|
| Windows | `winget install Gyan.FFmpeg` |
| macOS | `brew install ffmpeg` |
| Linux | `sudo apt install ffmpeg` |

Or drop the binaries into a local `ffmpeg/` folder in the repo root (git-ignored), or set the
`VENMA_FFMPEG` / `VENMA_FFPROBE` environment variables.

Optional: `pip install qtawesome` for a few extra legacy icons.

## What it does
- **Media bin**: import video, audio and images (button, `Ctrl+I`, or drag files onto the window), thumbnails, drag onto the timeline or double-click to append at the playhead.
- **Timeline**: 2 video tracks (V2 on top of V1) + 2 audio tracks, ruler, playhead scrubbing, snapping, zoom, scroll.
- **Editing**: move (also between tracks), trim by dragging clip edges, split (`S` / `Ctrl+K` / Razor tool), delete, ripple delete, unlimited-ish undo/redo (100 steps) with a History panel.
- **Clip properties**: volume (0–200 %), opacity, brightness, contrast, saturation.
- **Preview**: program monitor with transport (play/pause, frame step, start/end) and timecode. Decoded by FFmpeg in background QThreads (no QtMultimedia video / codec packs needed), so it never shows a black screen; sound plays through QAudioOutput.
- **Export**: MP4 (H.264 + AAC) or WebM (VP9 + Opus), quality High/Medium/Low, at the project resolution and frame rate. Layers are composited by track order with opacity; all audio is mixed.
- **Projects**: save/open `.vvproj` (JSON, references your media files by path).

## Shortcuts
| Key | Action |
|---|---|
| `V` / `C` / `H` / `Z` | Select / Razor / Hand / Zoom tool |
| `Space` | Play / pause |
| `Left` / `Right` | Previous / next frame |
| `Home` / `End` | Go to start / end |
| `S` or `Ctrl+K` | Split at playhead (selected clip, else every clip under the playhead) |
| `Delete` / `Shift+Delete` | Delete / ripple delete |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| `=` / `-` / `\` | Timeline zoom in / out / fit |
| `Ctrl+wheel` | Zoom timeline at the cursor (plain wheel scrolls) |
| `N` | Toggle snapping |
| `Ctrl+I` / `Ctrl+M` | Import / Export |
| `Ctrl+N` `Ctrl+O` `Ctrl+S` | New / Open / Save project |
| `Ctrl+Shift+D` | Dark / light theme |

## Known limits of v0.1
- Preview plays the topmost clip's own video and the first audio-track clip. Opacity, colour
  correction, volume boost above 100 % and mixing several audio tracks show up in the **export** only.
- Preview audio needs QtMultimedia (PyQt5 wheel includes it). Without an audio device the preview plays silently.
- No transitions, text/titles, speed changes, keyframes, or audio waveforms yet.
- The preview decodes at reduced size (max 960 px, 30 fps) so 4K sources stay smooth; export is always full quality.

## Project layout

```
venma_video/
├── app.py            # QApplication setup / entry point
├── __main__.py       # python -m venma_video
├── workers.py        # background QThreads: media import + export with progress
├── core/             # no Qt UI code — easy to test
│   ├── project.py        # timeline model (media, clips, tracks), edits, JSON snapshots for undo
│   └── ffmpeg_tools.py   # locate/probe/thumbnail + the filter_complex export builder
├── ui/
│   ├── main_window.py    # application layout, menus, commands
│   ├── timeline.py       # multi-track timeline widget
│   ├── preview.py        # preview monitor + threaded FFmpeg playback engine
│   ├── panels.py         # Media bin, Properties, History, New-project and Export dialogs
│   └── theme.py          # shared Venma design tokens, icons and stylesheet
└── assets/           # app icon (venma.ico) and other static files
tests/                # unittest/pytest suite
docs/ARCHITECTURE.md  # deeper notes
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the pieces fit together.

## Development

```bash
pip install -r requirements-dev.txt
pytest                # or: python -m unittest discover -s tests -t .
ruff check .          # lint
```

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.
