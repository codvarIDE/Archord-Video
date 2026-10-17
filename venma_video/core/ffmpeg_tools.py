
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import animation

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".gif"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus", ".wma"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".3gp"}
MEDIA_FILTER = ("Media files (" + " ".join("*" + e for e in sorted(IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS)) + ");;All files (*)")

_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}   # no console flash on Windows


def _find(name):
    env = os.environ.get("VENMA_" + name.upper())
    if env and os.path.isfile(env):
        return env
    # Repo root (…/venma_video/core/ffmpeg_tools.py -> …/), then the current working directory.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    exe = name + (".exe" if sys.platform == "win32" else "")
    for base in (root, os.getcwd()):
        for cand in (os.path.join(base, "ffmpeg", exe), os.path.join(base, "ffmpeg", "bin", exe)):
            if os.path.isfile(cand):
                return cand
    return shutil.which(name)


def ffmpeg_path():
    return _find("ffmpeg")


def ffprobe_path():
    return _find("ffprobe")


def have_ffmpeg():
    return bool(ffmpeg_path() and ffprobe_path())


def guess_kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    return "video"


def _rate(s):
    try:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else 0.0
    except Exception:
        return 0.0


def probe(path):
    """Return a dict(kind, duration, width, height, fps, has_audio) or raise RuntimeError."""
    probe_bin = ffprobe_path()
    if not probe_bin:
        raise RuntimeError("ffprobe not found")
    r = subprocess.run([probe_bin, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                       capture_output=True, text=True, **_NOWIN)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffprobe failed").strip().splitlines()[-1])
    info = json.loads(r.stdout or "{}")
    streams = info.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"
               and not (s.get("disposition") or {}).get("attached_pic")), None)
    aud = next((s for s in streams if s.get("codec_type") == "audio"), None)
    kind = guess_kind(path)
    if kind == "video" and vs is None:
        kind = "audio" if aud else "video"
    if kind == "audio" and aud is None and vs is not None:
        kind = "video"
    try:
        dur = float(info.get("format", {}).get("duration") or (vs or aud or {}).get("duration") or 0)
    except ValueError:
        dur = 0.0
    if kind == "image" or not dur:
        dur = 5.0 if kind == "image" else max(dur, 0.1)
    vw, vh = int((vs or {}).get("width") or 0), int((vs or {}).get("height") or 0)
    rot = (vs or {}).get("tags", {}).get("rotate")
    for sd in (vs or {}).get("side_data_list", []) or []:
        rot = sd.get("rotation", rot)
    try:
        if rot is not None and int(float(rot)) % 180 != 0:          # ffmpeg auto-rotates, so the picture is turned
            vw, vh = vh, vw
    except (TypeError, ValueError):
        pass
    return dict(kind=kind, duration=dur,
                width=vw, height=vh,
                fps=_rate((vs or {}).get("avg_frame_rate", "0/1")) if kind == "video" else 0.0,
                has_audio=bool(aud) and kind != "image")


def thumbnail_png(path, kind, at=0.0, height=90):
    """PNG bytes of a frame (or None). Audio has no frame."""
    exe = ffmpeg_path()
    if not exe or kind == "audio":
        return None
    cmd = [exe, "-v", "error"]
    if kind == "video" and at > 0:
        cmd += ["-ss", f"{at:.3f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", f"scale=-1:{int(height)}", "-f", "image2pipe", "-vcodec", "png", "-"]
    r = subprocess.run(cmd, capture_output=True, **_NOWIN)
    return r.stdout if r.returncode == 0 and r.stdout else None


# ── export ────────────────────────────────────────────────────────────────────
QUALITY = {"High": 18, "Medium": 23, "Low": 28}
FORMATS = {
    "MP4 (H.264 + AAC)": dict(ext=".mp4", v=["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"],
                              a=["-c:a", "aac", "-b:a", "192k"], extra=["-movflags", "+faststart"], crf=True),
    "WebM (VP9 + Opus)": dict(ext=".webm", v=["-c:v", "libvpx-vp9", "-b:v", "0", "-pix_fmt", "yuv420p"],
                              a=["-c:a", "libopus", "-b:a", "160k"], extra=[], crf=True),
}


def _ffmpeg_major():
    try:
        out = subprocess.run([ffmpeg_path(), "-version"], capture_output=True, text=True, **_NOWIN).stdout
        tok = out.split()[2]                        # "ffmpeg version 6.1.1-3ubuntu5 ..."
        return int(tok.lstrip("nN").split(".")[0].split("-")[0])
    except Exception:
        return 0


def _graph_args(graph, limit=6000):
    """Arguments that hand ffmpeg the filter graph. A project with many layers makes a graph too long for
    the OS command line (Windows caps it near 32 k characters), so long graphs go through a temp file."""
    if len(graph) <= limit:
        return ["-filter_complex", graph]
    f = tempfile.NamedTemporaryFile("w", suffix=".ffgraph", delete=False, encoding="utf-8")
    f.write(graph)
    f.close()
    atexit.register(lambda p=f.name: os.path.exists(p) and os.remove(p))
    return ["-/filter_complex" if _ffmpeg_major() >= 7 else "-filter_complex_script", f.name]


def _num(x):
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


def _n6(x):
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


def has_keyframes(clip):
    return clip is not None and bool(getattr(clip, "keyframes", None))


def _kf_expr(clip, prop, mul=1.0, var="t"):
    """FFmpeg expression of ``t`` (clip-local seconds) for a keyframed property, or ``None`` when no keyframe sets it.

    Linear between the keyframes and *held* before the first / after the last one, exactly like
    ``Clip.interpolate`` does for the preview. ``mul`` converts units (e.g. a fraction of the frame to pixels);
    ``var`` is the time variable of the filter that evaluates it (``t`` for most, ``T`` for ``geq``)."""
    ks = [k for k in getattr(clip, "keyframes", None) or [] if prop in k.values]
    if not ks:
        return None
    vals = [k.values[prop] * mul for k in ks]
    if len(ks) == 1 or all(abs(v - vals[0]) < 1e-9 for v in vals):
        return _n6(vals[0])
    expr = _n6(vals[-1])
    for j in range(len(ks) - 2, -1, -1):
        t0, t1 = ks[j].t, ks[j + 1].t
        v0, v1 = vals[j], vals[j + 1]
        seg = f"({_n6(v0)}+({_n6(v1 - v0)})*({var}-{_n6(t0)})/{_n6(max(1e-4, t1 - t0))})"
        expr = f"if(lte({var},{_n6(t1)}),{seg},{expr})"
    return f"if(lte({var},{_n6(ks[0].t)}),{_n6(vals[0])},{expr})"


def _is_const(expr):
    try:
        float(expr)
        return True
    except (TypeError, ValueError):
        return False


def _max_kf_scale(clip):
    vals = [k.values["scale"] for k in getattr(clip, "keyframes", None) or [] if "scale" in k.values]
    return max(vals) if vals else clip.scale


def _kf_opacity_filter(clip):
    """Filter for the clip's opacity: a plain ``colorchannelmixer`` when it is constant, ``geq`` (per frame)
    when opacity is keyframed. ``None`` when the clip is fully opaque."""
    ex = _kf_expr(clip, "opacity")
    if ex is None:
        return f"colorchannelmixer=aa={_num(clip.opacity)}" if clip.opacity < 0.999 else None
    if _is_const(ex):
        return f"colorchannelmixer=aa={_num(min(1.0, max(0.0, float(ex))))}" if float(ex) < 0.999 else None
    g = _kf_expr(clip, "opacity", var="T")
    return f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*min(1,max(0,{g}))'"


def has_animation(clip):
    """True when the clip has an In / Out / Combo animation (it is then always drawn through the placed path)."""
    return clip is not None and bool(getattr(clip, "animation", None)) and clip.has_animation()


def has_transform(clip):
    """True when a clip has been moved, resized or rotated away from "fitted, centred", or is animated."""
    if clip is None:
        return False
    if has_animation(clip):
        return True
    if has_keyframes(clip) and getattr(clip, "title", None) is None:
        return True
    return (getattr(clip, "title", None) is None
            and (abs(clip.scale - 1) > 1e-3 or abs(clip.pos_x - 0.5) > 1e-3 or abs(clip.pos_y - 0.5) > 1e-3
                 or abs(getattr(clip, "rotation", 0.0)) > 1e-3))


def _anim_filters(clip):
    """FFmpeg filters for the clip's animation, in three groups that the caller must apply in this order:

    ``rotate``  turn the fitted picture (the canvas is made large enough for any angle),
    ``fade``    In / Out opacity fades,
    ``scale``   the animated zoom. It MUST come last: a time-varying ``scale`` changes the frame size
                per frame, and downstream filters (``rotate``, ``eq`` ...) are configured for the first size.

    All are expressions of ``t`` (seconds since the clip started)."""
    ex = animation.expressions(getattr(clip, "animation", None), clip.duration) if has_animation(clip) else None
    out = dict(rotate=[], fade=[], scale=[])
    kf_scale = _kf_expr(clip, "scale")          # keyframed zoom: the picture is fitted at its biggest scale, shrunk per frame
    if kf_scale is not None and not _is_const(kf_scale):
        r = f"({kf_scale})/{_n6(max(1e-3, _max_kf_scale(clip)))}"
        S = f"({r})*({ex['scale']})" if ex is not None and ex["scale"] != "1" else r
        out["scale"].append(f"scale=w='max(2,trunc(iw*({S})/2)*2)':h='max(2,trunc(ih*({S})/2)*2)'"
                            f":eval=frame:flags=bilinear")
        if ex is not None:
            ex = dict(ex, scale="1")            # already folded into S
    if ex is None:
        return out
    if ex["rotation"] != "0":
        a = f"({ex['rotation']})*PI/180"
        out["rotate"].append(f"rotate='{a}':ow='hypot(iw,ih)':oh='hypot(iw,ih)':c=black@0")
    if ex["fade_in"] > 0:
        out["fade"].append(f"fade=t=in:st=0:d={_num(ex['fade_in'])}:alpha=1")
    if ex["fade_out"] > 0:
        out["fade"].append(f"fade=t=out:st={_num(max(0.0, clip.duration - ex['fade_out']))}"
                           f":d={_num(ex['fade_out'])}:alpha=1")
    if ex["scale"] != "1":
        S = ex["scale"]
        out["scale"].append(f"scale=w='max(2,trunc(iw*({S})/2)*2)':h='max(2,trunc(ih*({S})/2)*2)'"
                            f":eval=frame:flags=bilinear")
    return out


def _fit(W, H, clip, flags=""):
    """Filters that scale a picture to fit the frame, times the clip's scale (no padding when transformed)."""
    s = clip.scale if has_transform(clip) else 1.0
    kf_scale = _kf_expr(clip, "scale")
    if kf_scale is not None:
        s = _max_kf_scale(clip) if not _is_const(kf_scale) else float(kf_scale)
    bw, bh = max(2, int(round(W * s))), max(2, int(round(H * s)))
    chain = [f"scale={bw}:{bh}:force_original_aspect_ratio=decrease{flags}", "setsar=1", "format=rgba"]
    rot = getattr(clip, "rotation", 0.0)
    rot_kf = _kf_expr(clip, "rotation", mul=3.14159265 / 180.0)
    if rot_kf is not None:
        if _is_const(rot_kf):
            rad = float(rot_kf)
            if abs(rad) > 1e-5:
                chain.append(f"rotate='{_n6(rad)}':ow='rotw({_n6(rad)})':oh='roth({_n6(rad)})':c=black@0")
        else:
            # the angle changes every frame, so the canvas is made big enough for any angle
            chain.append(f"rotate='{rot_kf}':ow='hypot(iw,ih)':oh='hypot(iw,ih)':c=black@0")
    elif abs(rot) > 1e-3:
        rad = rot * 3.14159265 / 180.0
        chain.append(f"rotate='{rad}':ow='rotw({rad})':oh='roth({rad})':c=black@0")
    return chain


def _place(src, dst, W, H, clip, fps, tag, bg="black@0", animate=False):
    """Graph lines that put the scaled picture ``src`` onto a W x H canvas, centred on the clip's position.

    With ``animate`` the clip's In / Out / Combo animation offset is added to that position (per frame)."""
    ex = animation.expressions(clip.animation, clip.duration) if animate and has_animation(clip) else None
    off_x = f"+{W}*({ex['dx']})" if ex and ex["dx"] != "0" else ""
    off_y = f"+{H}*({ex['dy']})" if ex and ex["dy"] != "0" else ""
    kx, ky = _kf_expr(clip, "pos_x", mul=W), _kf_expr(clip, "pos_y", mul=H)
    if kx is not None or ky is not None:
        px_expr = kx if kx is not None else _num(W * clip.pos_x)
        py_expr = ky if ky is not None else _num(H * clip.pos_y)
        return [f"color=c={bg}:s={W}x{H}:r={fps},format=rgba[bg{tag}]",
                f"[bg{tag}][{src}]overlay=x='({px_expr}){off_x}-w/2':y='({py_expr}){off_y}-h/2':format=auto:shortest=1[{dst}]"]
    px, py = _num(W * clip.pos_x), _num(H * clip.pos_y)
    return [f"color=c={bg}:s={W}x{H}:r={fps},format=rgba[bg{tag}]",
            f"[bg{tag}][{src}]overlay=x='{px}{off_x}-w/2':y='{py}{off_y}-h/2':format=auto:shortest=1[{dst}]"]


def build_export_command(project, out_path, fmt="MP4 (H.264 + AAC)", quality="Medium", text_images=None):
    """Build the ffmpeg argv that renders the whole timeline. Raises ValueError for an empty timeline.

    ``text_images`` maps a title clip's id to a transparent, project-sized PNG of that title (the UI
    renders these with Qt so the export matches the preview exactly and needs no ffmpeg fonts).
    """
    text_images = text_images or {}
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    total = project.duration()
    if total <= 0 or not project.clips:
        raise ValueError("The timeline is empty.")
    W, H, fps = project.width - project.width % 2, project.height - project.height % 2, project.fps
    spec = FORMATS[fmt]

    cmd = [exe, "-y", "-v", "error", "-nostdin"]
    vparts, aparts = [], []      # (z-order, label) / label
    filters = []
    n = 0
    for c in sorted(project.clips, key=lambda c: (-c.track, c.start)):      # lower tracks first
        m = project.media[c.media_id]
        is_video_track = project.tracks[c.track]["kind"] == "video"
        draw = is_video_track and m.kind in ("video", "image", "text") and not project.track_hidden(c.track)
        sound = m.has_audio and m.kind != "image" and not project.track_muted(c.track)
        if not draw and not sound:
            continue                                  # hidden picture layer / muted track: nothing to render
        src = m.path
        if m.kind == "text":
            src = text_images.get(c.id)
            if not src:
                raise ValueError("A title has not been rendered to an image for export.")
        if m.kind in ("image", "text"):
            cmd += ["-loop", "1", "-framerate", str(fps), "-t", _num(c.duration), "-i", src]
        else:
            cmd += ["-ss", _num(c.in_point), "-t", _num(c.duration), "-i", m.path]
        i = n
        n += 1
        if draw:
            moved = has_transform(c)
            chain = [f"fps={fps}"] + _fit(W, H, c)
            if not moved:
                chain.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0")
            anim = _anim_filters(c)
            chain += anim["rotate"]
            if abs(c.brightness) > 1e-3 or abs(c.contrast - 1) > 1e-3 or abs(c.saturation - 1) > 1e-3:
                chain.append(f"eq=brightness={_num(c.brightness)}:contrast={_num(c.contrast)}:saturation={_num(c.saturation)}")
            op = _kf_opacity_filter(c)
            if op:
                chain.append(op)
            chain += anim["fade"] + anim["scale"]
            if moved:
                filters.append(f"[{i}:v]{','.join(chain)}[t{i}]")
                filters += _place(f"t{i}", f"p{i}", W, H, c, fps, f"e{i}", animate=True)
                filters.append(f"[p{i}]setpts=PTS-STARTPTS+{_num(c.start)}/TB[v{i}]")
            else:
                chain.append(f"setpts=PTS-STARTPTS+{_num(c.start)}/TB")
                filters.append(f"[{i}:v]{','.join(chain)}[v{i}]")
            vparts.append((i, c))
        if sound:
            delay = int(round(c.start * 1000))
            filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={_num(c.volume)},"
                           f"adelay={delay}|{delay}[a{i}]")
            aparts.append(i)

    filters.insert(0, f"color=c=black:s={W}x{H}:r={fps}:d={_num(total)},format=rgba[base]")
    last = "base"
    for k, (i, c) in enumerate(vparts):
        out = f"o{k}"
        filters.append(f"[{last}][v{i}]overlay=enable='between(t,{_num(c.start)},{_num(c.end)})'[{out}]")
        last = out
    filters.append(f"[{last}]format=yuv420p[vout]")
    maps = ["-map", "[vout]"]
    if aparts:
        if len(aparts) == 1:
            filters.append(f"[a{aparts[0]}]anull[aout]")
        else:
            ins = "".join(f"[a{i}]" for i in aparts)
            filters.append(f"{ins}amix=inputs={len(aparts)}:normalize=0:duration=longest[aout]")
        maps += ["-map", "[aout]"]

    cmd += _graph_args(";".join(filters)) + maps
    cmd += spec["v"]
    if spec["crf"]:
        cmd += ["-crf", str(QUALITY[quality])]
    if aparts:
        cmd += spec["a"]
    cmd += spec["extra"] + ["-r", str(fps), "-t", _num(total), "-progress", "pipe:1", "-nostats", out_path]
    return cmd


# ── live preview (decoded by FFmpeg, shown by the app – no QtMultimedia video needed) ────────────
PREVIEW_LIMIT = 960          # longest preview side in pixels


def preview_size(width, height, limit=PREVIEW_LIMIT):
    """Even-sized preview frame that keeps the project's aspect ratio."""
    s = min(1.0, limit / max(1, width, height))
    return max(2, int(width * s) // 2 * 2), max(2, int(height * s) // 2 * 2)


def _preview_filter_args(w, h, fps, clip):
    """ffmpeg arguments that fit one picture into a w x h RGB frame (moved / resized when the clip says so)."""
    moved = has_transform(clip)
    chain = []
    if fps:
        chain.append(f"fps={fps}")
    if moved:
        chain += _fit(w, h, clip, ":flags=fast_bilinear")
    else:
        chain += [f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=fast_bilinear",
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"]
    if clip is not None and (abs(clip.brightness) > 1e-3 or abs(clip.contrast - 1) > 1e-3
                             or abs(clip.saturation - 1) > 1e-3):
        chain.append(f"eq=brightness={_num(clip.brightness)}:contrast={_num(clip.contrast)}"
                     f":saturation={_num(clip.saturation)}")
    if not moved:
        chain.append("format=rgb24")
        return ["-vf", ",".join(chain)]
    graph = [f"[0:v]{','.join(chain)}[t]"] + _place("t", "p", w, h, clip, fps or 30, "pv", bg="black")
    graph.append("[p]format=rgb24[out]")
    return ["-filter_complex", ";".join(graph), "-map", "[out]"]


def video_stream_cmd(path, src_start, duration, w, h, fps, clip=None):
    """ffmpeg argv that writes raw RGB24 frames (w*h*3 bytes each) at ``fps`` to stdout."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    return [exe, "-v", "error", "-nostdin", "-ss", f"{max(0.0, src_start):.3f}", "-t", f"{max(0.05, duration):.3f}",
            "-i", path, "-an", "-sn"] + _preview_filter_args(w, h, fps, clip) + [
            "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]


def audio_stream_cmd(path, src_start, duration, volume=1.0, rate=48000, channels=2):
    """ffmpeg argv that writes raw signed 16-bit little-endian PCM to stdout."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    return [exe, "-v", "error", "-nostdin", "-ss", f"{max(0.0, src_start):.3f}", "-t", f"{max(0.05, duration):.3f}",
            "-i", path, "-vn", "-sn", "-af", f"volume={_num(volume)}",
            "-ac", str(channels), "-ar", str(rate), "-f", "s16le", "-"]


def composite_still_cmd(project, t, w, h):
    """ffmpeg argv that writes ONE raw RGB24 frame of every video layer visible at ``t`` stacked in layer
    order (opacity and colour correction applied), so the preview can show layers on top of each other."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    cmd = [exe, "-v", "error", "-nostdin"]
    filters = [f"color=c=black:s={w}x{h}:d=1,format=rgba[base]"]
    last = "base"
    for n, c in enumerate(project.video_clips_at(t)):            # bottom layer first
        m = project.media[c.media_id]
        if m.kind == "image":
            cmd += ["-i", m.path]
        else:
            src = c.in_point + (t - c.start)
            src = min(max(0.0, src), max(0.0, m.duration - 1.0 / max(1, project.fps)))
            cmd += ["-ss", f"{src:.3f}", "-i", m.path]
        moved = has_transform(c)
        chain = _fit(w, h, c, ":flags=fast_bilinear")
        if not moved:
            chain.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0")
        if abs(c.brightness) > 1e-3 or abs(c.contrast - 1) > 1e-3 or abs(c.saturation - 1) > 1e-3:
            chain.append(f"eq=brightness={_num(c.brightness)}:contrast={_num(c.contrast)}:saturation={_num(c.saturation)}")
        if c.opacity < 0.999:
            chain.append(f"colorchannelmixer=aa={_num(c.opacity)}")
        if moved:
            filters.append(f"[{n}:v]{','.join(chain)}[t{n}]")
            filters += _place(f"t{n}", f"l{n}", w, h, c, 30, f"c{n}")
        else:
            filters.append(f"[{n}:v]{','.join(chain)}[l{n}]")
        filters.append(f"[{last}][l{n}]overlay=shortest=0[o{n}]")
        last = f"o{n}"
    filters.append(f"[{last}]format=rgb24[out]")
    return cmd + _graph_args(";".join(filters)) + ["-map", "[out]", "-frames:v", "1", "-pix_fmt", "rgb24",
                                                      "-f", "rawvideo", "-"]


def still_cmd(path, kind, src, w, h, clip=None):
    """ffmpeg argv that writes ONE raw RGB24 frame (w*h*3 bytes) of a video/image at ``src`` seconds."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    cmd = [exe, "-v", "error", "-nostdin"]
    if kind == "video":
        cmd += ["-ss", f"{max(0.0, src):.3f}"]
    cmd += ["-i", path, "-an", "-sn", "-frames:v", "1"] + _preview_filter_args(w, h, 0, clip) + [
            "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    return cmd


# ── per-layer preview (each layer is decoded alone at its fitted size and painted by Qt in stacking order) ──
def layer_size(media_w, media_h, pw, ph, scale=1.0):
    """Pixel size (even numbers) a picture is decoded at for the preview: the picture fitted into a pw x ph frame,
    doubled while the clip is enlarged past 1.0 (so zooming in stays sharp), never bigger than the source."""
    if media_w <= 0 or media_h <= 0:
        media_w, media_h, cap = pw, ph, False
    else:
        cap = True
    f = min(pw / media_w, ph / media_h) * (2.0 if scale > 1.001 else 1.0)
    if cap:
        f = min(f, 1.0)
    return max(2, int(media_w * f) // 2 * 2), max(2, int(media_h * f) // 2 * 2)


def _layer_vf(w, h, fps, clip, alpha):
    chain = [f"fps={fps}"] if fps else []
    chain += [f"scale={w}:{h}:flags=fast_bilinear", "setsar=1"]
    if clip is not None and (abs(clip.brightness) > 1e-3 or abs(clip.contrast - 1) > 1e-3
                             or abs(clip.saturation - 1) > 1e-3):
        chain.append(f"eq=brightness={_num(clip.brightness)}:contrast={_num(clip.contrast)}"
                     f":saturation={_num(clip.saturation)}")
    chain.append("format=rgba" if alpha else "format=rgb24")
    return ",".join(chain)


def layer_stream_cmd(path, src_start, duration, w, h, fps, clip=None):
    """Raw RGB24 frames (w*h*3 bytes each) of ONE video layer at its own fitted size, no padding."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    return [exe, "-v", "error", "-nostdin", "-ss", f"{max(0.0, src_start):.3f}", "-t", f"{max(0.05, duration):.3f}",
            "-i", path, "-an", "-sn", "-vf", _layer_vf(w, h, fps, clip, False),
            "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]


def layer_still_cmd(path, kind, src, w, h, clip=None):
    """ONE raw frame of a video / image layer at its fitted size: RGB24 for video, RGBA for images (keeps transparency)."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    alpha = kind == "image"
    cmd = [exe, "-v", "error", "-nostdin"]
    if kind == "video":
        cmd += ["-ss", f"{max(0.0, src):.3f}"]
    return cmd + ["-i", path, "-an", "-sn", "-frames:v", "1", "-vf", _layer_vf(w, h, 0, clip, alpha),
                  "-pix_fmt", "rgba" if alpha else "rgb24", "-f", "rawvideo", "-"]
