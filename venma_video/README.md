# Venma Video

[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)

A desktop video editor (PyQt5 + FFmpeg) with a **CapCut Pro-style interface**: dark theme with a cyan accent, one slim
top bar (Menu, project name, Export), a Media / Text panel, the Player and a Details panel across the top, and the
timeline with its own toolbar underneath. Fixed layout with draggable splitters; dark/light mode. Currently **v0.1**.

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
- **Unlimited layers, CapCut style**: the timeline starts with V2/V1 + A1/A2 but you can stack as many video layers and audio tracks as you like. Drag a clip onto the dashed **new layer** strip above (or **new audio track** strip below) the tracks, click **+ Layer / + Audio**, or use `Ctrl+Shift+L` / `Ctrl+Shift+A`. Importing or dropping a clip where the track is busy puts it on a free layer or creates one. Right-click a track header to add, reorder (stacking order) or delete layers; `Menu > Insert > Remove Empty Layers` tidies up. Many layers scroll vertically (scrollbar or `Alt`+wheel).
- **Unlimited files**: import any number of files at once (one undo step per batch); select several in the media bin (`Ctrl`/`Shift`-click) and drag or add them together.
- **Timeline**: ruler, playhead scrubbing, snapping, zoom, scroll.
- **Editing**: move (also between tracks), trim by dragging clip edges, **Ripple Edit tool** (trim an edge and later clips on the track follow, so no gap is left), **Slip tool** (change which part of the source plays without moving or resizing the clip), split (`S` / `Ctrl+K` / Razor tool), delete, ripple delete, undo/redo (100 steps).
- **Text & titles**: add a Title, Lower third or Subtitle at the playhead (`Insert` menu, the **Text** toolbar button, or `Ctrl+T`). Titles are ordinary clips on the top video track, so they move, trim, split, ripple and undo like everything else. Edit the text, font, size, bold/italic, alignment, colour, outline, background box, opacity and X/Y position in the Properties panel; they show live in the preview.
- **Clip properties**: volume (0–200 %), opacity, brightness, contrast, saturation.
- **Preview**: program monitor with transport (play/pause, frame step, start/end) and timecode. Decoded by FFmpeg in background QThreads (no QtMultimedia video / codec packs needed), so it never shows a black screen; sound plays through QAudioOutput.
- **Export**: MP4 (H.264 + AAC) or WebM (VP9 + Opus), quality High/Medium/Low, at the project resolution and frame rate. Layers are composited by track order with opacity; all audio is mixed.
- **Projects**: save/open `.vvproj` (JSON, references your media files by path).

## Shortcuts
| Key | Action |
|---|---|
| `V` / `B` / `Y` / `C` | Select / Ripple Edit / Slip / Razor tool |
| `Space` | Play / pause |
| `Left` / `Right` | Previous / next frame |
| `Home` / `End` | Go to start / end |
| `S` or `Ctrl+K` | Split at playhead (selected clip, else every clip under the playhead) |
| `Delete` / `Shift+Delete` | Delete / ripple delete |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| `=` / `-` / `\` | Timeline zoom in / out / fit |
| `Ctrl+wheel` | Zoom timeline at the cursor (plain wheel scrolls) |
| `N` | Toggle snapping |
| `Ctrl+T` / `Ctrl+Shift+T` / `Ctrl+Alt+T` | Add title / lower third / subtitle at the playhead |
| `Ctrl+I` / `Ctrl+M` | Import / Export |
| `Ctrl+N` `Ctrl+O` `Ctrl+S` | New / Open / Save project |
| `Ctrl+Shift+D` | Dark / light theme |

## Known limits of v0.1
- While paused or scrubbing, the preview stacks all visible layers (opacity and colour included); during playback it plays the topmost clip's own video and the first audio-track clip. Opacity, colour
  correction, volume boost above 100 % and mixing several audio tracks show up in the **export** only.
- Preview audio needs QtMultimedia (PyQt5 wheel includes it). Without an audio device the preview plays silently.
- No transitions, speed changes, keyframes, or audio waveforms yet. Titles have no animation or fades yet, and
  a title's position is set with sliders (no on-canvas dragging yet).
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
│   ├── main_window.py    # top bar, panels, timeline toolbar, commands
│   ├── timeline.py       # multi-track timeline widget
│   ├── preview.py        # preview monitor + threaded FFmpeg playback engine
│   ├── titles.py         # Qt painter for titles, shared by the preview and the export
│   ├── panels.py         # Media bin, Text presets, Details (properties), New-project and Export dialogs
│   ├── docks.py          # CollapsibleSection and small toolbar helpers
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
