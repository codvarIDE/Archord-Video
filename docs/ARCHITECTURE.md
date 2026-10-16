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

## Project files

`.vvproj` is JSON (`Project.to_dict()`), versioned via `PROJECT_VERSION`. Media is referenced by
absolute path, not embedded. Bump `PROJECT_VERSION` and keep `from_dict` backward compatible when
changing the format.

## FFmpeg lookup order

1. `VENMA_FFMPEG` / `VENMA_FFPROBE` env vars
2. `<repo>/ffmpeg/` or `<repo>/ffmpeg/bin/`
3. `PATH`
