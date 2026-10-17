
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

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


def has_transform(clip):
    """True when a picture clip has been moved or resized away from "fitted, centred"."""
    return (clip is not None and getattr(clip, "title", None) is None
            and (abs(clip.scale - 1) > 1e-3 or abs(clip.pos_x - 0.5) > 1e-3 or abs(clip.pos_y - 0.5) > 1e-3))


def _fit(W, H, clip, flags=""):
    """Filters that scale a picture to fit the frame, times the clip's scale (no padding when transformed)."""
    s = clip.scale if has_transform(clip) else 1.0
    bw, bh = max(2, int(round(W * s))), max(2, int(round(H * s)))
    return [f"scale={bw}:{bh}:force_original_aspect_ratio=decrease{flags}", "setsar=1", "format=rgba"]


def _place(src, dst, W, H, clip, fps, tag, bg="black@0"):
    """Graph lines that put the scaled picture ``src`` onto a W x H canvas, centred on the clip's position."""
    px, py = _num(W * clip.pos_x), _num(H * clip.pos_y)
    return [f"color=c={bg}:s={W}x{H}:r={fps},format=rgba[bg{tag}]",
            f"[bg{tag}][{src}]overlay=x='{px}-w/2':y='{py}-h/2':format=auto:shortest=1[{dst}]"]


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
        is_video_track = project.tracks[c.track]["kind"] == "video"
        if is_video_track and m.kind in ("video", "image", "text"):
            moved = has_transform(c)
            chain = [f"fps={fps}"] + _fit(W, H, c)
            if not moved:
                chain.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0")
            if abs(c.brightness) > 1e-3 or abs(c.contrast - 1) > 1e-3 or abs(c.saturation - 1) > 1e-3:
                chain.append(f"eq=brightness={_num(c.brightness)}:contrast={_num(c.contrast)}:saturation={_num(c.saturation)}")
            if c.opacity < 0.999:
                chain.append(f"colorchannelmixer=aa={_num(c.opacity)}")
            if moved:
                filters.append(f"[{i}:v]{','.join(chain)}[t{i}]")
                filters += _place(f"t{i}", f"p{i}", W, H, c, fps, f"e{i}")
                filters.append(f"[p{i}]setpts=PTS-STARTPTS+{_num(c.start)}/TB[v{i}]")
            else:
                chain.append(f"setpts=PTS-STARTPTS+{_num(c.start)}/TB")
                filters.append(f"[{i}:v]{','.join(chain)}[v{i}]")
            vparts.append((i, c))
        if m.has_audio and m.kind != "image":
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