# Architecture

## Layers

```
ui/  ──────►  core/        (ui depends on core; core never imports Qt or ui)
 │
 └──►  workers.py ──► core/
```

- **`core/project.py`** — pure-Python timeline model. Time is in seconds; a clip occupies
  `[start, start + duration)` on a track and plays its source from `in_point`. Every edit
  mutates the `Project`; undo/redo works by JSON snapshots (`Project.snapshot()`).
- **`core/ffmpeg_tools.py`** — everything that shells out to FFmpeg: binary discovery,
  `probe`, thumbnails, the `filter_complex` export command, and the raw-frame/PCM commands the
  preview uses.
- **`workers.py`** — `QThread`s so import/thumbnailing/export never block the UI.
- **`ui/`** — widgets. `main_window.py` wires the timeline, preview, panels and commands together.

## Rule of thumb

Put logic that can be tested without a display in `core/`. If you add a feature that changes the
timeline data, add the operation to `Project` and cover it in `tests/test_project.py`.

## Titles (text)

A title is a normal `Clip` with a `title` style dict (text, font, size, colour, position, …) and no media
file. It points at one shared placeholder `MediaItem` (`kind="text"`, id `mtext`) so trim, move, ripple,
split, undo and save/load need no special cases; `STILL_KINDS` (`image`, `text`) marks the kinds that have
no source range. The placeholder is hidden from the media bin via `Project.library()`.

- **Model:** `Project.add_title`, `set_title`, `titles_at(t)`. `top_video_clip_at` skips titles, because
  they are overlays and never the picture the preview decodes.
- **Preview:** `PreviewView` paints `titles_at(engine.t)` over the frame in `paintEvent` using
  `ui/titles.py`, so editing text needs no FFmpeg round-trip.
- **Export:** `ui/titles.py` renders each title to a transparent project-sized PNG (same painter code as
  the preview, so the two match and no FFmpeg fonts / `drawtext` are needed). `build_export_command`
  receives them as `text_images` and overlays them like looped still images, with opacity.
- **Sizes** are pixels at a 1080-high frame and scale with the frame; position is a 0..1 fraction.

## Layers and the preview

- **Stacking order.** In a typed project every picture layer (video / photo / text clips) is ordered by when it
  was added: `Project.find_layer(auto=True)` reuses the lowest layer that is free *and not covered by an earlier
  layer*, otherwise it inserts a new layer at the top. `Project.visual_clips_at(t)` returns every picture and
  title visible at `t` in true paint order (bottom first); export (`build_export_command`) overlays in the same order.
- **Preview.** `PreviewEngine` runs one FFmpeg decoder per visible video layer at that layer's *fitted* size
  (`ffmpeg_tools.layer_stream_cmd` / `layer_still_cmd`); `PreviewView.paintEvent` draws each layer's latest frame
  and each title in stacking order, applying position, scale and opacity with Qt. So moving / resizing a layer is
  instant (nothing is re-decoded), clicking picks the front-most layer, and what you see matches the export.

## Layer controls (hide / lock / mute / reorder)

Each layer is a dict in `Project.tracks`; the switches are plain keys on it (`hidden`, `locked`, `muted`, absent
when off), so they are saved in `.vvproj`, and undo/redo restores them with the rest of the snapshot.

- **Hide** removes the layer's pictures and titles from `visual_clips_at` / `video_clips_at` / `titles_at`
  (preview) and from `build_export_command`. Sound from a hidden video layer still plays; use Mute for that.
- **Mute** silences the layer in the preview (`audio_clip_at`, `PreviewEngine._audio_src`) and in the export.
- **Lock** is enforced in the model (`move`, `trim_*`, `ripple_trim`, `slip`, `split`, `delete`, `set_title`),
  not just in the UI, and `find_layer` never auto-places clips on a locked layer. The timeline shows the clips
  hatched, the preview treats a locked layer as "glass" (can't be picked or resized), and the media bin can't
  drop onto it.
- **Reorder:** drag a layer by its header in the timeline. `Project.reorder_track(src, dst)` moves it within its
  kind (picture layers and audio tracks never mix) and the clips follow their layer. The context-menu
  "Move up / down" still works.

## Project files

`.vvproj` is JSON (`Project.to_dict()`), versioned via `PROJECT_VERSION`. Media is referenced by
absolute path, not embedded. Bump `PROJECT_VERSION` and keep `from_dict` backward compatible when
changing the format.

## FFmpeg lookup order

1. `VENMA_FFMPEG` / `VENMA_FFPROBE` env vars
2. `<repo>/ffmpeg/` or `<repo>/ffmpeg/bin/`
3. `PATH`
