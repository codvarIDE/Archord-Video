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
- **Layers, CapCut style**: drop or import anything and it lands on a layer, created for you when needed. Picture layers (video, photos and text can share one) stack **in the order they were added**: the first layer is at the bottom (`Video`, then `Layer 1`, `Layer 2`…), every new one goes on top, and the **last added layer is always in front** (numbered last). A clip only joins an existing layer if it is free there and nothing added earlier would cover it; otherwise a new layer is created above. Dropping on a busy layer inserts a new one right above it. Audio tracks always stay below the picture layers. `Layer` / `Audio` buttons, the dashed *new layer* strips and the right-click menu on a layer header add or reorder layers by hand; `Menu > Insert > Remove Empty Layers` tidies up. Projects saved by older versions keep their old layers.
- **Animations, CapCut style**: select a clip (video, photo or title) and open **Animation** in the Properties panel. Pick an **In** animation (Fade, Zoom In/Out, Pop, Slide ×4, Rise, Fall, Spin, Swing, Wipe ×5), an **Out** animation, or a looping **Combo** (Pulse, Heartbeat, Swing, Float, Bounce, Shake, Spin, Slow Zoom, Pan). The duration slider sets how long an In / Out lasts, a short preview plays as soon as you pick one, and the exact same motion is rendered on export. Animations stack on top of your own position / scale / keyframes. Clips show small ramps on the timeline where an In / Out runs.
- **Unlimited files**: import any number of files at once (one undo step per batch); select several in the media bin (`Ctrl`/`Shift`-click) and drag or add them together.
- **Timeline**: ruler, playhead scrubbing, snapping, zoom, scroll.
- **Editing**: move (also between tracks), trim by dragging clip edges, **Ripple Edit tool** (trim an edge and later clips on the track follow, so no gap is left), **Slip tool** (change which part of the source plays without moving or resizing the clip), split (`S` / `Ctrl+K` / Razor tool), delete, ripple delete, undo/redo (100 steps).
- **Text & titles**: add a Title, Lower third or Subtitle at the playhead (`Insert` menu, the **Text** toolbar button, or `Ctrl+T`). Titles are ordinary clips on the top video track, so they move, trim, split, ripple and undo like everything else. Edit the text, font, size, bold/italic, alignment, colour, outline, background box, opacity and X/Y position in the Properties panel; they show live in the preview.
- **Voiceover**: record from the microphone straight onto an audio track (`Ctrl+R`, the **Record** button in the timeline toolbar, or `Insert > Record Voiceover`). Pick the microphone, optionally count in 3-2-1, and the project plays (muted by default, so the speakers don't leak into the take) while you talk; a red take with a live level trace grows on the audio track from the playhead. **Stop** adds it as a normal clip (move, trim, split, volume, undo all work; one undo step removes the whole take). It goes on the first free audio track, or a new one if that would overlap something. Takes are saved as `Voiceover N.wav` in a `Voiceovers` folder next to your saved project (or the app's data folder until the project is saved). No FFmpeg or codec packs needed, and nothing is recorded until you press Record.
- **Speed & reverse**: select a video or audio clip and open **Speed** in the Properties panel: a slider (0.1×–20×), an exact-value box, one-click presets (0.25× … 8×) and **Play in reverse**. Also in `Menu > Clip` (`Alt+R` reverses). Faster clips get shorter and slower ones longer (later clips on the track are pushed along only as far as needed), sound follows the speed with its pitch kept, keyframes stretch with the clip, and trim / split / slip / ripple all understand speed and direction. A badge such as `2×` or `◀ 0.5×` marks retimed clips on the timeline. Export reverses long clips in short pieces, so memory stays flat.
- **Waveforms & audio fades**: audio clips show their real waveform on the timeline (a strip along the bottom of video clips that have sound), decoded in the background when you import a file, record a voiceover or open a project. The bars follow the clip: speed, reverse, trim and slip show the matching part of the sound, and the clip's volume and fades scale them, so what you see is what you hear. Select a clip with sound and drag **Fade in** / **Fade out** in the Audio section of the Properties panel (0 s up to the clip's length; the two never overlap). The sound ramps linearly up from / down to silence, a shaded wedge with the ramp line marks each fade on the clip, and the preview and the export play the same curve. Fades keep their length in seconds when you trim, speed up or split a clip (the fade-in stays on the left half, the fade-out goes to the right half) and are saved in the project.
- **Transitions, CapCut style**: select a clip and open **Transition** in the Properties panel. Pick Dissolve, Zoom In / Out, Pop, Slide ×4, Rise, Fall, Spin, Swing or a **Wipe** (Left / Right / Up / Down / Circle; the edge travels in the direction of the name) and set its length (0.1–3 s); a short preview plays across the cut. A transition belongs to the clip it leads *into* and shows when another clip on the same layer ends exactly where it starts: the earlier clip's last frame is held under it while the new clip animates in over it. Nothing gets shorter and the project length never changes. A bow-tie and a bar mark the transition on the timeline; if the clips stop touching it simply goes dormant (and comes back when they meet again). It is exported exactly as previewed.
- **Clip properties**: volume (0–200 %), opacity, brightness, contrast, saturation.
- **Preview**: program monitor with transport (play/pause, frame step, start/end) and timecode. Decoded by FFmpeg in background QThreads (no QtMultimedia video / codec packs needed), so it never shows a black screen; sound plays through QAudioOutput.
- **Export**: MP4 (H.264 + AAC) or WebM (VP9 + Opus), quality High/Medium/Low. Layers are composited by track order with opacity; all audio is mixed.
- **Social presets**: in the Export dialog pick **TikTok / Reels / Shorts 9:16** (1080 × 1920), **Square 1:1** (1080 × 1080), **YouTube 16:9** (1920 × 1080) or **4K UHD** (3840 × 2160), or keep the project's own size. A preset with a different shape is a temporary canvas switch: pictures are fitted inside the new frame, with the canvas background around them.
- **Canvas aspect switch & background**: `Menu > Canvas` switches the project between 16:9, 9:16, 1:1 and 4:5 (the short side is kept), and sets what shows around a picture that doesn't fill the frame: a **color fill** (pick any color) or a **blurred copy of the picture**. The same settings are in Project Settings, and are saved with the project. The preview shows exactly what is exported. Blur applies to untouched pictures on the bottom picture layer; a picture you move or resize shows the color fill instead.
- **GPU encoding**: the Export dialog uses a hardware H.264 encoder for MP4 when one really works on your machine (NVIDIA NVENC, Intel Quick Sync, AMD AMF or Apple VideoToolbox are tested with a short trial encode). If the GPU encoder fails mid-export, Venma retries on the CPU automatically. WebM always uses the CPU.
- **Projects**: save/open `.vvproj` (JSON, references your media files by path). Saving is crash-safe: the old file is only replaced once the new one is fully written.
- **Autosave & crash recovery**: unsaved work is snapshotted every 15 seconds while you edit. If Venma Video crashes, is killed or the computer loses power, the next start offers to **Recover** it (or Discard / Ask me later). Nothing is left behind after a normal exit or a Save. Snapshots live in the app data folder, not next to your project.

## Shortcuts
| Key | Action |
|---|---|
| `V` / `B` / `Y` / `C` | Select / Ripple Edit / Slip / Razor tool |
| `Space` | Play / pause |
| `Left` / `Right` | Previous / next frame |
| `Home` / `End` | Go to start / end |
| `Alt+R` | Reverse the selected clip |
| `S` or `Ctrl+K` | Split at playhead (selected clip, else every clip under the playhead) |
| `Delete` / `Shift+Delete` | Delete / ripple delete |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| `=` / `-` / `\` | Timeline zoom in / out / fit |
| `Ctrl+wheel` | Zoom timeline at the cursor (plain wheel scrolls) |
| `N` | Toggle snapping |
| `Ctrl+T` / `Ctrl+Shift+T` / `Ctrl+Alt+T` | Add title / lower third / subtitle at the playhead |
| `Ctrl+R` | Record a voiceover at the playhead |
| `Ctrl+I` / `Ctrl+M` | Import / Export |
| `Ctrl+N` `Ctrl+O` `Ctrl+S` | New / Open / Save project |
| `Ctrl+Shift+D` | Dark / light theme |

## Known limits of v0.1
- Autosave keeps the **project** (the timeline, titles, settings), not your media files. If a source file was moved or deleted, recovery opens the project with the usual "Missing media" notice. Voiceover takes of an unsaved project are in the app data folder and survive.
- While paused or scrubbing, the preview stacks all visible layers (opacity and colour included); during playback it plays the topmost clip's own video and the first audio-track clip. Opacity, colour
  correction, volume boost above 100 % and mixing several audio tracks show up in the **export** only.
- The preview plays one audio track at a time (the first audio track that has a clip at that moment). A voiceover that had
  to go on a second audio track (because the first one is busy) is mixed in the **export**, but isn't heard in the preview
  while the first track is sounding there.
- Voiceover recording needs QtMultimedia too (it is part of the PyQt5 wheel) and an input device; on macOS and Windows the
  system may ask you to allow microphone access the first time.
- Preview audio needs QtMultimedia (PyQt5 wheel includes it). Without an audio device the preview plays silently.
- Transitions need the two clips to touch on one layer and hold the outgoing clip's last frame (no cross-clip source handles; wipes reveal against the whole frame, with a hard edge). No speed ramps (one constant speed per clip) yet. Audio fades are set with the Properties sliders (no dragging the fade handles on the clip yet) and are linear; waveforms are decoded again each time a project is opened. Titles have no animation or picture fades yet, and
  a title's position is set with sliders (no on-canvas dragging yet).
- The preview decodes at reduced size (max 960 px, 30 fps) so 4K sources stay smooth; export is always full quality.

## Project layout

```
venma_video/
├── app.py            # QApplication setup / entry point
├── __main__.py       # python -m venma_video
├── workers.py        # background QThreads: media import, waveform decoding + export with progress
├── core/             # no Qt UI code — easy to test
│   ├── project.py        # timeline model (media, clips, tracks), edits, JSON snapshots for undo
│   ├── voiceover.py      # WAV writer, level meter maths and take naming for voiceover recording
│   ├── autosave.py       # autosave snapshots + crash detection/recovery (no Qt)
│   ├── fileio.py         # atomic_write_text: crash-safe file writes
│   ├── waveform.py       # decodes a file's sound to compact peaks the timeline draws at any zoom
│   └── ffmpeg_tools.py   # locate/probe/thumbnail + the filter_complex export builder
├── ui/
│   ├── main_window.py    # top bar, panels, timeline toolbar, commands
│   ├── timeline.py       # multi-track timeline widget
│   ├── preview.py        # preview monitor + threaded FFmpeg playback engine
│   ├── titles.py         # Qt painter for titles, shared by the preview and the export
│   ├── voiceover.py      # microphone capture (QAudioInput) and the Record Voiceover dialog
│   ├── panels.py         # Media bin, Text presets, Details (properties), New-project and Export dialogs
│   ├── docks.py          # CollapsibleSection and small toolbar helpers
│   └── theme.py          # shared Venma design tokens, icons and stylesheet
└── assets/           # Venma logo (venma_cut_violet.ico) and other static files (a packaged copy lives in venma_video/assets/)
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
