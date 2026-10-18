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

## Clip animations (In / Out / Combo)

CapCut-style presets stored on the clip as `Clip.animation`, e.g. `{"in": "pop", "in_dur": 0.6, "out": "fade"}` or
`{"combo": "pulse"}`. Picking a Combo replaces In / Out and vice versa (`animation.set_slot`). They are layered on top of
the clip's own position / scale / rotation / opacity and its keyframes, never written into them.

- **One definition, two renderers.** `core/animation.py` defines every preset as arithmetic expressions in FFmpeg's
  expression language (`t` = seconds into the clip). The preview evaluates the same strings in Python
  (`animation.values_at`, used by `PreviewView.paintEvent`), and the exporter hands them to FFmpeg
  (`animation.expressions`), so both always agree. Only `+ - * /`, `sin cos abs min max clip` and `PI` are allowed.
- **Export.** An animated clip always takes the "placed" path (`ffmpeg_tools.has_transform`). `_anim_filters` returns
  `rotate`, `fade` and `scale` groups that must be applied in that order: a time-varying `scale` (`eval=frame`)
  changes the frame size per frame, so it has to be the last filter before the overlay. Slides/shakes/pans add a
  per-frame offset to the overlay's x / y in `_place`.
- **Model.** `Project.set_animation` / `clear_animation` (lock-aware). `Project.split` gives the In to the left half
  and the Out to the right half; a Combo continues on both.
- **UI.** The Animation section of `PropertiesPanel` (In / Out / Combo tabs, preset chips, duration slider) emits
  `animationEdited`; `MainWindow._on_animation` commits it and plays a short preview. The timeline draws small ramps
  on clips that have an In / Out animation and a bar for a Combo.
- **Tests.** `tests/test_animation.py`, including one that renders every preset with the real FFmpeg.

## Keyframes (CapCut style)

A clip's `keyframes` are `Keyframe(t, **values)` objects (`t` = seconds from the clip start; values for `pos_x`,
`pos_y`, `scale`, `rotation`, `opacity`), always sorted by `t`. Between two keyframes every property is linear, and
it is held before the first / after the last one (`Clip.interpolate` for the preview, `ffmpeg_tools._kf_expr` for
the export, which builds the same curve as an FFmpeg expression).

- **Auto keyframes.** Once a clip has keyframes, `Clip.edit_at(t, **values)` is how every edit is made (dragging or
  resizing in the preview, the sliders): on a frame that already has a keyframe it updates it, anywhere else it
  creates a new keyframe holding the whole look at that moment. Without keyframes it edits the clip's own properties.
- **Diamond / Alt+K.** `Clip.toggle_keyframe` adds a keyframe at the playhead or removes the one under it.
  `KF_SNAP` (0.02 s) is "the same spot": the playhead is *on* a keyframe within that distance, and two keyframes
  can't be that close.
- **Timeline.** The diamonds on a clip can be clicked (the playhead jumps there), dragged along the clip
  (`Clip.move_keyframe`, clamped between the neighbours and the clip ends, snaps to the playhead), right-clicked
  (go to / delete) and deleted with Delete. Alt+Left / Alt+Right (and the arrows in the Keyframes panel) jump to the
  previous / next keyframe (`Clip.prev_keyframe` / `next_keyframe`).
- **Trim and split keep the motion on the picture.** `Clip.rebase_keyframes` shifts the keyframes when the head is
  trimmed and drops the ones that fall outside, first putting a keyframe on the new edge so nothing jumps.
  `Project.split` puts a keyframe holding the same value on the cut in both halves.
- **Export.** Position, scale, rotation and opacity keyframes all render. Scale is applied last (`eval=frame`), the
  picture being fitted at its biggest keyframe scale so zooming in stays sharp; opacity uses `geq` (slower) only when
  it really changes. `tests/test_keyframes.py` renders these with the real FFmpeg and checks pixels.

## Speed and reverse

`Clip.speed` (0.1–20) and `Clip.reverse` are saved only when non-default and are deliberately *not* in `CLIP_DEFAULTS`
("Reset effects" must never change a clip's length). `Clip.duration` stays the **timeline** length; the clip plays
`Clip.span = duration * speed` seconds of source, the window `[in_point, in_point + span]`, forwards or backwards.
`Clip.source_time(t)` is the single mapping from clip time to source time (preview stills, composites).

- **Model.** `Project.set_speed` keeps the source window, rescales length and keyframes and pushes later clips on the
  track just far enough to avoid overlap; speed is capped so a clip never drops below `MIN_CLIP`. `set_reverse` only
  flips the flag. Trim, ripple, slip and split all go through `_source_room` / `_trim_head` / `_trim_tail`: a reversed
  clip's *head* is the end of its source window and its *tail* the start, so they move `in_point` the other way round.
- **Export.** Video: `setpts=(PTS-STARTPTS)/speed` then `fps`; audio: chained `atempo` (each stage within 0.5–2).
  FFmpeg's `reverse` / `areverse` buffer everything in RAM, so `render_reversed_media` reverses the window in
  `REVERSE_CHUNK`-second pieces and joins them in reverse order (`-c copy`); `build_export_command(reversed_media=...)`
  then reads that file. Without it a reversed clip falls back to the inline filters (fine for short clips).
  `ExportWorker(prepare=...)` runs this step inside the thread, with progress and cancel.
- **Preview.** Streams sample the source at `fps / speed` and re-stamp with `settb=1/fps,setpts=N,fps=fps` (the
  time base must be set first or the timestamps collapse). Reversed clips play as a chain of 1 s pieces
  (`ffmpeg_tools.reverse_chunks`, which also gives each piece an exact frame count so the clock never drifts);
  `_Stream` accepts a function yielding several commands for this. Stream keys include speed / reverse.
- **UI.** `PropertiesPanel` Speed section (log slider, box, chips, reverse switch), `MainWindow._apply_speed` /
  `reverse_selected`, a `Clip` menu, and a badge in `TimelineCanvas._draw_clip`.
- **Tests.** `tests/test_speed.py` renders a red/green/blue/yellow source and checks which colour shows when.

## Waveforms and audio fades

- **Fades are clip data.** `Clip.fade_in` / `Clip.fade_out` are timeline seconds, saved only when set and (like speed)
  not in `CLIP_DEFAULTS`, so "Reset effects" leaves them alone. `Clip.fades()` returns what actually plays: the two
  never exceed the clip's length (they shrink in proportion if they would), so trimming or speeding a clip up needs no
  fix-up and the values the user chose come back if the clip grows again. `Clip.fade_gain(t)` is the same curve
  (linear) in Python, used by the timeline drawing and the tests. `Project.can_fade` (audio clips and video clips whose
  file has sound) and `Project.set_fade(id, "in" | "out", seconds)` (lock-aware, caps the value at what the other
  fade leaves, tiny values mean off). `Project.split` keeps the fade-in on the left half and moves the fade-out to the
  right half.
- **One definition, two renderers.** `ffmpeg_tools.fade_filters(fade_in, fade_out, duration, offset)` builds the `afade`
  filters. The exporter puts them after speed / reverse and `volume`, before `adelay`, so they run in the clip's own
  timeline. The preview's `audio_stream_cmd(..., fade=(fi, fo, clip_duration, offset))` uses the same function; the
  stream may start mid-clip (a seek, or a later piece of a reversed clip), so `offset` starts a fade-in at the gain it
  would have reached there (`silence=`) and a fade-out already under way at its current gain (`unity=`). The preview's
  stream key includes the fades, so moving a slider restarts the sound with the new ramp.
- **Waveforms.** `core/waveform.py` has no Qt. `decode(path)` runs `ffmpeg_tools.waveform_cmd` (mono, 4 kHz PCM) and
  folds it into one byte per 20 ms (`PEAKS_PER_SEC = 50`), reading ~12 s at a time so memory stays flat on long files.
  `Waveform.peak(t0, t1)` / `bins` return the loudest peak in any source range, in either order, so the timeline can
  draw at any zoom without decoding again. `WaveformWorker` decodes one file in a thread; `MainWindow._request_waves`
  queues files (after import, a voiceover take, or opening a project) and runs the workers one after another. Results
  live in `MainWindow.waves` (media path -> `Waveform`, `None` when the file has no sound) and are shared with the
  timeline canvas.
- **Timeline.** `TimelineCanvas._draw_wave` walks only the visible bars (2 px each); each bar maps its clip time to
  source time with the clip's speed / reverse / in-point and is scaled by `Clip.volume` and `fade_gain`. Audio clips
  fill the body, video clips get a strip along the bottom. `_draw_fades` shades the faded part with the ramp line.
- **UI.** Audio section of `PropertiesPanel`: two sliders in tenths of a second (`fadeEdited`; the maximum follows the
  clip's length). `MainWindow._on_fade` is like the speed slider: the change shows and plays at once and the undo step
  ("Change Fade") is recorded when the drag settles.
- **Tests.** `tests/test_audio_fades.py`, including real renders: the export is run losslessly and the level of a tone is
  measured along the clip, as are preview streams that start in the middle of a fade and reversed preview pieces.

## Transitions

`Clip.transition = {"type": <In preset>, "dur": seconds}` lives on the **incoming** clip and is saved only when set.
It is *active* when `Project.transition_prev` finds a picture clip on the same layer ending within `TRANSITION_TOUCH`
of the clip's start; otherwise it is dormant (kept, not drawn).

- **One definition.** The motion is simply that clip's In animation, so preview and export share `core/animation.py`.
  `Project.render_clip(c)` returns a copy whose `animation` has the transition baked in (it replaces In / Combo on the
  incoming clip and drops the Out of the outgoing one, whose held frame must not fade to black). Both renderers draw
  that copy: `PreviewEngine._stack_now` and `build_export_command`.
- **Wipes.** `wipe_left/right/up/down/circle` are In presets with a `wipe` key instead of a motion: a mask, not a
  transform. `animation.expressions` returns `wipe=(kind, seconds)` and `animation.wipe_progress` is the eased 0..1
  reveal expression both renderers use. The preview clips the layer to `preview.wipe_region` (a rect, or an ellipse
  path for the circle). The exporter applies `ffmpeg_tools._wipe_condition` with `geq`, but only to the clip's first
  `seconds` (`split` + `trim`, head and tail overlaid one after the other) because `geq` is slow. Wipes are In-only
  (`OUT_PRESETS` leaves them out) and are measured against the whole frame. `values_at` adds a `wipe` key only
  while one is set.
- **The hold.** The outgoing clip stays visible for `hold_after(c)` seconds past its end. `held_clips_at` /
  `visual_clips_at` / `video_clips_at(held=True)` include it in paint order (below the incoming clip); decoders and
  sound use `held=False`, and the preview shows its last real frame as a still. Export pads video with
  `tpad=stop_mode=clone` (photos just loop longer) and widens that clip's overlay `enable` window.
- **UI.** Transition section in `PropertiesPanel` (`transitionEdited`), `MainWindow._on_transition` (slider drags
  commit once; picking previews across the cut), bow-tie + duration bar in `TimelineCanvas._draw_clip`.
- **Tests.** `tests/test_transitions.py` renders red -> green and checks the dissolve is a real crossfade.

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

## Voiceover recording

- **Capture.** `ui/voiceover.py::VoiceRecorder` opens a `QAudioInput` (16-bit PCM, mono if the device allows) and
  polls it every 15 ms, emitting `chunk(bytes)`. Polling works on every audio backend. `core/voiceover.WavSink`
  appends the chunks to `Voiceover N.wav` with the stdlib `wave` module, so recording needs no codec and no FFmpeg,
  and `peak_level` feeds the level meter and the live trace on the timeline.
- **Where it lands.** `Project.voiceover_track(start)` picks the audio track *before* recording starts (the take's
  length is unknown, so the track must be empty from `start` onwards; locked and muted tracks are skipped), creating a
  new audio track if none qualifies. `MainWindow.record_voiceover` removes that track again if the take is cancelled,
  and otherwise adds the WAV as an `audio` `MediaItem` and `place_clip`s it, in a single `commit("Record Voiceover")`.
- **Timing.** The dialog is modal, so the timeline can't change underneath it. When "play the project" is on it starts
  the preview engine, waits until `PreviewEngine.is_starting()` is false (the clock only runs once the decoders are
  ready) and only then opens the microphone, taking `engine.t` at that instant as the take's start time. The preview's
  sound can be silenced with `PreviewEngine.set_muted` so speakers don't bleed into the microphone.
- **Live feedback.** `TimelineCanvas.set_recording(track, t0, t1, levels)` paints the growing take over the cached
  layer (it is never part of the cache, so it costs one repaint per tick).
- **Tests.** `VoiceoverDialog` takes a `recorder_factory`, so `tests/test_voiceover.py` drives whole takes with a fake
  microphone and engine; no audio hardware is needed.

## Project files

`.vvproj` is JSON (`Project.to_dict()`), versioned via `PROJECT_VERSION`. Media is referenced by
absolute path, not embedded. Bump `PROJECT_VERSION` and keep `from_dict` backward compatible when
changing the format.

## FFmpeg lookup order

1. `VENMA_FFMPEG` / `VENMA_FFPROBE` env vars
2. `<repo>/ffmpeg/` or `<repo>/ffmpeg/bin/`
3. `PATH`
