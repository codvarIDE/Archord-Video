# Venma Video — v0.1 (initial version)

A video editor built with the same design language and workspace layout as Venma (PyQt5):
warm paper/clay theme, vertical tool palette on the left, context options bar on top, tabbed
side panels, light/dark mode.

## Run
```bash
pip install -r requirements.txt
# FFmpeg is required (ffmpeg + ffprobe on PATH):
#   Windows: winget install Gyan.FFmpeg   macOS: brew install ffmpeg   Linux: sudo apt install ffmpeg
python main.py            # or: python main.py clip1.mp4 clip2.mp4
```
You can also drop `ffmpeg` / `ffprobe` into an `ffmpeg/` folder next to `main.py`, or set the
`VENMA_FFMPEG` / `VENMA_FFPROBE` environment variables.

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

## Architecture
- `project.py` — timeline model (media, clips, tracks), editing operations, JSON snapshots for undo.
- `ffmpeg_tools.py` — locate/probe/thumbnail, and the FFmpeg `filter_complex` export builder.
- `workers.py` — background threads: parallel import/thumbnails and export with progress.
- `timeline.py` — multi-track timeline widget.
- `preview.py` — preview monitor + threaded FFmpeg playback engine (video, audio and still-frame QThreads, next-clip prefetch).
- `panels.py` — Media bin, Properties, History, New-project and Export dialogs.
- `main_window.py` — application layout, menus, commands.
- `theme.py` — shared Venma design tokens, icons and stylesheet.
