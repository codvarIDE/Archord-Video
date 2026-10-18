
import atexit
import json
import math
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
    env = os.environ.get("ARCHORD_" + name.upper())
    if env and os.path.isfile(env):
        return env
    # Repo root (…/archord_video/core/ffmpeg_tools.py -> …/), then the current working directory.
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


_FILTERS = None
MISSING_FILTERS = set()          # filters an export reported as "No such filter" (learned at run time)
FILTER_FALLBACKS = {"adelay"}    # filters the exporter can work around when this FFmpeg lacks them


def has_filter(name):
    """True when this FFmpeg build has filter ``name`` (stripped-down builds lack some, e.g. ``adelay``).
    If the build's filter list can't be read, say no, so the portable path is used."""
    global _FILTERS
    if name in MISSING_FILTERS:
        return False
    exe = ffmpeg_path()
    if not exe:                                   # no FFmpeg here (tests): keep the normal graph
        return True
    if _FILTERS is None:
        found = set()
        try:
            out = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True,
                                 errors="replace", timeout=60, **_NOWIN).stdout
            for line in out.splitlines():          # e.g. " TSC adelay   A->A   Delay one or more audio channels."
                parts = line.split()
                if len(parts) >= 3 and len(parts[0]) == 3 and all(ch in "TSC." for ch in parts[0]):
                    found.add(parts[1])
        except Exception:
            found = set()
        _FILTERS = found
    return name in _FILTERS


def ffmpeg_too_old():
    """True when the FFmpeg in use is a version known to lack filters / options the editor needs (older than 4.4)."""
    exe = ffmpeg_path()
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, errors="replace", timeout=15, **_NOWIN).stdout
        tok = out.split()[2]
    except Exception:
        return False
    try:
        if tok.startswith(("N-", "n-")):                           # a git build: "N-55702-g920046a"
            return int(tok.split("-")[1]) < 100000                 # 4.4 branched around N-100000
        nums = tok.lstrip("nN").split("-")[0].split(".")
        major, minor = int(nums[0]), int(nums[1]) if len(nums) > 1 else 0
        return (major, minor) < (4, 4)
    except Exception:
        return False


def ffmpeg_info():
    """Which FFmpeg is in use and its version line, for error messages."""
    exe = ffmpeg_path()
    if not exe:
        return "FFmpeg: not found"
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, errors="replace", timeout=15, **_NOWIN).stdout
        line = out.splitlines()[0] if out else ""
    except Exception as e:
        line = f"(could not run: {e})"
    return f"FFmpeg: {exe}\n{line}"


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


# ── GPU (hardware) H.264 encoding ────────────────────────────────────────────────────────────────
# name -> (label, function(crf) -> video args). Only MP4 can use these (WebM / VP9 stays on the CPU).
GPU_ENCODERS = {
    "h264_nvenc": ("NVIDIA NVENC", lambda q: ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(q),
                                              "-b:v", "0", "-pix_fmt", "yuv420p"]),
    "h264_qsv": ("Intel Quick Sync", lambda q: ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", str(q),
                                                "-pix_fmt", "nv12"]),
    "h264_amf": ("AMD AMF", lambda q: ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", str(q),
                                       "-qp_p", str(q), "-qp_b", str(q), "-pix_fmt", "yuv420p"]),
    "h264_videotoolbox": ("Apple VideoToolbox", lambda q: ["-c:v", "h264_videotoolbox",
                                                           "-q:v", str(max(30, min(90, int(110 - q * 2.5)))),
                                                           "-pix_fmt", "yuv420p"]),
}
_GPU_CACHE = {}


def gpu_encoder(refresh=False):
    """Name of the first hardware H.264 encoder that really works on this machine, or None.

    Being listed by ``ffmpeg -encoders`` isn't enough (a build can list NVENC on a PC without an NVIDIA card), so
    each candidate is test-encoded for a few frames. The answer is cached."""
    if "enc" in _GPU_CACHE and not refresh:
        return _GPU_CACHE["enc"]
    found = None
    exe = ffmpeg_path()
    if exe:
        try:
            listed = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                    timeout=15, **_NOWIN).stdout
        except Exception:
            listed = ""
        for name in GPU_ENCODERS:
            if name not in listed:
                continue
            try:
                r = subprocess.run([exe, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                                    "color=c=black:s=256x256:r=30:d=0.2", *GPU_ENCODERS[name][1](23),
                                    "-f", "null", "-"], capture_output=True, timeout=20, **_NOWIN)
            except Exception:
                continue
            if r.returncode == 0:
                found = name
                break
    _GPU_CACHE["enc"] = found
    return found


def gpu_label(name=None):
    name = name or gpu_encoder()
    return GPU_ENCODERS[name][0] if name in GPU_ENCODERS else ""


def _video_args(spec, quality, gpu):
    """(ffmpeg video-codec args, whether the quality ``-crf`` flag is still needed)."""
    if gpu and gpu in GPU_ENCODERS and spec["ext"] == ".mp4":
        return GPU_ENCODERS[gpu][1](QUALITY[quality]), False
    return spec["v"], spec["crf"]


# ── canvas background ────────────────────────────────────────────────────────────────────────────
def bg_color_arg(project):
    """The project's fill colour as an FFmpeg colour (``0xRRGGBB``)."""
    return "0x" + getattr(project, "bg_color", "#000000").lstrip("#")


def wants_backdrop(project, clip):
    """True when ``clip`` gets a blurred copy of itself behind it: blur mode, an untouched (fitted, centred) picture
    on the bottom-most picture layer whose shape doesn't already fill the canvas. The preview uses the same rule."""
    if getattr(project, "bg_mode", "color") != "blur" or clip.title is not None or has_transform(clip):
        return False
    m = project.media.get(clip.media_id)
    if m is None or m.kind not in ("video", "image") or clip.track != project.main_picture_track():
        return False
    if m.width > 0 and m.height > 0 and abs(m.width / m.height - project.width / project.height) < 0.01:
        return False
    return True


def backdrop_filters(W, H):
    """Filter chain: the picture scaled to *cover* the canvas, cropped and blurred (done small, then scaled up)."""
    sw, sh = max(2, W // 8 // 2 * 2), max(2, H // 8 // 2 * 2)
    return [f"scale={sw}:{sh}:force_original_aspect_ratio=increase", f"crop={sw}:{sh}", "boxblur=luma_radius=3:luma_power=2",
            f"scale={W}:{H}:flags=bilinear", "setsar=1", "format=rgba"]


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


def _atempo_chain(speed):
    """``atempo`` filters that change the audio speed by ``speed`` without changing its pitch.

    One ``atempo`` only takes 0.5..2 on older FFmpeg builds, so bigger / smaller factors are chained."""
    out, s = [], float(speed)
    while s > 2.0 + 1e-9:
        out.append("atempo=2")
        s /= 2.0
    while s < 0.5 - 1e-9:
        out.append("atempo=0.5")
        s *= 2.0
    if abs(s - 1.0) > 1e-6:
        out.append(f"atempo={_n6(s)}")
    return out


def fade_filters(fade_in, fade_out, duration, offset=0.0):
    """``afade`` filters for a clip's audio fades (linear), for sound that starts ``offset`` seconds into the clip.

    ``fade_in`` / ``fade_out`` are the clip's *effective* fades (``Clip.fades()``) and ``duration`` its timeline
    length. Apply them after speed / reverse, i.e. to sound already running in timeline time. ``offset`` is 0 for an
    export; the preview starts mid-clip (a seek, or a later reversed piece) and so must start mid-ramp: the fade-in
    begins at the gain it would have reached there (``silence``) and a fade-out already under way begins at its
    current gain (``unity``) and finishes with the clip, so what you hear is the same curve either way.
    """
    out = []
    off = max(0.0, float(offset))
    if fade_in > 1e-3 and off < fade_in - 1e-3:
        f = f"afade=t=in:st=0:d={_n6(fade_in - off)}"
        if off > 1e-3:
            f += f":silence={_n6(off / fade_in)}"
        out.append(f)
    if fade_out > 1e-3 and off < duration - 1e-3:
        start = duration - fade_out                   # clip time at which the fade-out begins
        if off >= start:
            left = duration - off
            out.append(f"afade=t=out:st=0:d={_n6(left)}:unity={_n6(min(1.0, left / fade_out))}")
        else:
            out.append(f"afade=t=out:st={_n6(start - off)}:d={_n6(fade_out)}")
    return out


def waveform_cmd(path):
    """ffmpeg argv that decodes ``path``'s sound to mono 16-bit PCM (4 kHz is plenty for peaks) on stdout."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    return [exe, "-v", "error", "-nostdin", "-i", path, "-vn", "-sn", "-dn", "-ac", "1", "-ar", "4000",
            "-f", "s16le", "-"]


def _is_retimed(clip):
    return clip is not None and (getattr(clip, "reverse", False) or abs(getattr(clip, "speed", 1.0) - 1.0) > 1e-9)


REVERSE_CHUNK = 4.0          # export: source seconds reversed per FFmpeg run (``reverse`` buffers a whole run in RAM)
PREVIEW_REVERSE_CHUNK = 1.0  # preview: timeline seconds decoded (and reversed) at a time


def reverse_chunks(clip, s0=0.0, chunk=PREVIEW_REVERSE_CHUNK, fps=None):
    """How to play a reversed clip from ``s0`` (seconds into the clip) to its end in short pieces.

    FFmpeg's ``reverse`` filter holds everything it is given in memory, so a long clip is cut into ``chunk``
    timeline-second pieces, last piece of the source first. Yields ``(src_lo, src_hi, frames)``: the source range
    to decode and, when ``fps`` is given, the exact number of output frames that piece must produce (rounded
    cumulatively, so the pieces never drift against the clock). """
    s = min(max(0.0, float(s0)), clip.duration)
    s_first = s
    top = clip.in_point + clip.span
    while s < clip.duration - 1e-6:
        e = min(clip.duration, s + chunk)
        hi = top - s * clip.speed
        lo = top - e * clip.speed
        n = None
        if fps:
            n = int(round((e - s_first) * fps)) - int(round((s - s_first) * fps))
        yield max(0.0, lo), max(0.0, hi), n
        s = e


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
                f"[bg{tag}][{src}]overlay=x='({px_expr}){off_x}-w/2':y='({py_expr}){off_y}-h/2':format=rgb:shortest=1[{dst}]"]
    px, py = _num(W * clip.pos_x), _num(H * clip.pos_y)
    return [f"color=c={bg}:s={W}x{H}:r={fps},format=rgba[bg{tag}]",
            f"[bg{tag}][{src}]overlay=x='{px}{off_x}-w/2':y='{py}{off_y}-h/2':format=rgb:shortest=1[{dst}]"]


def _wipe_condition(kind, W, H, p):
    """geq condition (pixel ``X``, ``Y``) that is true where a wipe of ``kind`` has revealed the picture, when ``p`` is
    the expression for how much (0..1). Pixel centres are used so 0 reveals nothing and 1 reveals everything.
    The edge travels in the direction of the name: ``left`` starts at the right edge and sweeps left."""
    x, y = "(X+0.5)", "(Y+0.5)"
    if kind == "left":
        return f"gt({x},{_n6(W)}*(1-{p}))"
    if kind == "right":
        return f"lt({x},{_n6(W)}*{p})"
    if kind == "up":
        return f"gt({y},{_n6(H)}*(1-{p}))"
    if kind == "down":
        return f"lt({y},{_n6(H)}*{p})"
    r = math.hypot(W / 2.0, H / 2.0)                  # circle: reaches the corners at 1
    return f"lt(hypot({x}-{_n6(W / 2.0)},{y}-{_n6(H / 2.0)}),{_n6(r)}*{p})"


def _wipe_filters(i, c, kind, seconds, W, H, fps, end):
    """Graph lines that apply a wipe-in to the finished, frame-sized stream ``[v<i>]``.

    ``geq`` is slow, so only the first ``seconds`` of the clip (the head) go through it: the stream is split in two,
    and the caller overlays head and tail one after the other. When the wipe lasts as long as the clip (``end`` is
    where its picture stops, hold included) there is no tail and the whole stream is masked.
    Returns (lines, [(label, first second, last second)])."""
    s0 = c.start
    s1 = c.start + seconds
    p = animation.wipe_progress(seconds, f"T-{_num(s0)}")
    cond = _wipe_condition(kind, W, H, p)
    mask = f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*if({cond},1,0)'"
    if s1 + 1.5 / fps >= end:
        return [f"[v{i}]{mask}[vh{i}]"], [(f"vh{i}", s0, end)]
    lines = [f"[v{i}]split=2[wh{i}][wt{i}]",
             f"[wh{i}]trim=start={_num(s0)}:end={_num(s1)},{mask}[vh{i}]",
             f"[wt{i}]trim=start={_num(s1)}[vt{i}]"]
    return lines, [(f"vh{i}", s0, s1), (f"vt{i}", s1, end)]


def _roles(project, c):
    """(draws a picture, makes sound) for clip ``c``: what the exporter has to render for it."""
    m = project.media[c.media_id]
    is_video_track = project.tracks[c.track]["kind"] == "video"
    draw = is_video_track and m.kind in ("video", "image", "text") and not project.track_hidden(c.track)
    sound = m.has_audio and m.kind != "image" and not project.track_muted(c.track)
    return draw, sound


def reversed_clips(project):
    """Clips the export has to play backwards (hidden / muted ones that render nothing are left out)."""
    return [c for c in project.clips if c.reverse and project.media[c.media_id].kind in ("video", "audio")
            and any(_roles(project, c))]


def render_reversed_media(project, out_dir, chunk=REVERSE_CHUNK, progress=None, cancelled=None):
    """Pre-render every reversed clip's source window, played backwards, into ``out_dir``.

    FFmpeg's ``reverse`` / ``areverse`` filters keep everything they are given in memory (a minute of 1080p would
    need many gigabytes), so the window is reversed ``chunk`` source-seconds at a time and the pieces are joined in
    reverse order without re-encoding. Returns ``{clip id: path}`` for :func:`build_export_command`'s
    ``reversed_media``. ``progress(fraction)`` and ``cancelled()`` are optional callbacks.
    """
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    jobs = reversed_clips(project)
    plan = {c.id: [(max(c.in_point, c.in_point + c.span - (k + 1) * chunk), c.in_point + c.span - k * chunk)
                   for k in range(max(1, int(-(-c.span // chunk))))] for c in jobs}
    total = sum(len(v) + 1 for v in plan.values()) or 1
    done = 0

    def step():
        nonlocal done
        done += 1
        if progress:
            progress(done / total)
        if cancelled and cancelled():
            raise RuntimeError("Export cancelled.")

    def run(argv):
        r = subprocess.run(argv, capture_output=True, text=True, **_NOWIN)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "ffmpeg failed").strip().splitlines()[-1])

    out = {}
    for c in jobs:
        m = project.media[c.media_id]
        parts = []
        for k, (a, b) in enumerate(plan[c.id]):
            part = os.path.join(out_dir, f"{c.id}_{k:04d}.mkv")
            argv = [exe, "-y", "-v", "error", "-nostdin", "-ss", _n6(a), "-t", _n6(max(0.05, b - a)), "-i", m.path, "-sn"]
            if m.kind == "video":
                argv += ["-vf", "reverse", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p"]
            else:
                argv += ["-vn"]
            argv += ["-af", "areverse", "-c:a", "pcm_s16le"] if m.has_audio else ["-an"]
            run(argv + [part])
            parts.append(part)
            step()
        final = os.path.join(out_dir, f"{c.id}_reversed.mkv")
        lst = os.path.join(out_dir, f"{c.id}_parts.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for part in parts:                                  # last piece of the source first
                f.write("file '" + part.replace("\\", "/").replace("'", "'\\''") + "'\n")
        run([exe, "-y", "-v", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", final])
        for part in parts + [lst]:
            try:
                os.remove(part)
            except OSError:
                pass
        out[c.id] = final
        step()
    return out


def build_export_command(project, out_path, fmt="MP4 (H.264 + AAC)", quality="Medium", text_images=None,
                         reversed_media=None, gpu=None):
    """Build the ffmpeg argv that renders the whole timeline. Raises ValueError for an empty timeline.

    ``text_images`` maps a title clip's id to a transparent, project-sized PNG of that title (the UI
    renders these with Qt so the export matches the preview exactly and needs no ffmpeg fonts).

    ``reversed_media`` maps a reversed clip's id to its source window already played backwards
    (:func:`render_reversed_media`). A reversed clip without an entry is reversed inline with the ``reverse``
    filter, which is fine for short clips but needs the whole clip in memory.

    ``gpu`` is a hardware encoder name from :data:`GPU_ENCODERS` (see :func:`gpu_encoder`); MP4 only, WebM ignores it.
    """
    text_images = text_images or {}
    reversed_media = reversed_media or {}
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    total = project.duration()
    if total <= 0 or not project.clips:
        raise ValueError("The timeline is empty.")
    W, H, fps = project.width - project.width % 2, project.height - project.height % 2, project.fps
    spec = FORMATS[fmt]

    cmd = [exe, "-y", "-v", "error", "-nostdin"]
    vparts, aparts = [], []      # (label, first second, last second) of each picture overlay / audio label
    filters = []
    n = 0
    for c in sorted(project.clips, key=lambda c: (-c.track, c.start)):      # lower tracks first
        m = project.media[c.media_id]
        draw, sound = _roles(project, c)
        if not draw and not sound:
            continue                                  # hidden picture layer / muted track: nothing to render
        hold = project.hold_after(c) if draw and m.kind in ("video", "image") else 0.0
        c = project.render_clip(c)                    # a transition is an In animation on the incoming clip
        src = m.path
        if m.kind == "text":
            src = text_images.get(c.id)
            if not src:
                raise ValueError("A title has not been rendered to an image for export.")
        if m.kind in ("image", "text"):
            cmd += ["-loop", "1", "-framerate", str(fps), "-t", _num(c.duration + hold), "-i", src]
        elif c.reverse and c.id in reversed_media:        # already backwards: just read it from the start
            cmd += ["-t", _num(c.span), "-i", reversed_media[c.id]]
        else:
            cmd += ["-ss", _num(c.in_point), "-t", _num(c.span), "-i", m.path]
        inline_reverse = c.reverse and c.id not in reversed_media and m.kind not in ("image", "text")
        i = n
        n += 1
        if draw:
            moved = has_transform(c)
            lead = ["reverse"] if inline_reverse else []
            if abs(c.speed - 1.0) > 1e-9 and m.kind not in ("image", "text"):
                lead.append(f"setpts=(PTS-STARTPTS)/{_n6(c.speed)}")
            keep = [f"tpad=stop_mode=clone:stop_duration={_n6(hold)}"] if hold > 0 and m.kind == "video" else []
            backdrop = wants_backdrop(project, c)
            head = lead + [f"fps={fps}"] + keep
            if backdrop:                      # one decode feeds both the blurred backdrop and the fitted picture
                filters.append(f"[{i}:v]{','.join(head)},split[s{i}a][s{i}b]")
                chain = _fit(W, H, c)
            else:
                chain = head + _fit(W, H, c)
            if not moved:
                chain.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0")
            anim = _anim_filters(c)
            chain += anim["rotate"]
            eq = None
            if abs(c.brightness) > 1e-3 or abs(c.contrast - 1) > 1e-3 or abs(c.saturation - 1) > 1e-3:
                eq = f"eq=brightness={_num(c.brightness)}:contrast={_num(c.contrast)}:saturation={_num(c.saturation)}"
                chain.append(eq)
            if backdrop:
                bgc = backdrop_filters(W, H) + ([eq] if eq else []) + [f"setpts=PTS-STARTPTS+{_num(c.start)}/TB"]
                filters.append(f"[s{i}b]{','.join(bgc)}[b{i}]")
                vparts.append((f"b{i}", c.start, c.end + hold))
            op = _kf_opacity_filter(c)
            if op:
                chain.append(op)
            chain += anim["fade"] + anim["scale"]
            src_pad = f"s{i}a" if backdrop else f"{i}:v"
            if moved:
                filters.append(f"[{i}:v]{','.join(chain)}[t{i}]")
                filters += _place(f"t{i}", f"p{i}", W, H, c, fps, f"e{i}", animate=True)
                filters.append(f"[p{i}]setpts=PTS-STARTPTS+{_num(c.start)}/TB[v{i}]")
            else:
                chain.append(f"setpts=PTS-STARTPTS+{_num(c.start)}/TB")
                filters.append(f"[{src_pad}]{','.join(chain)}[v{i}]")
            end = c.end + hold
            wipe = (animation.expressions(c.animation, c.duration) or {}).get("wipe")
            if wipe:                                                # a wipe-in (In animation or transition): a mask
                lines, parts = _wipe_filters(i, c, wipe[0], wipe[1], W, H, fps, end)
                filters += lines
                vparts += parts
            else:
                vparts.append((f"v{i}", c.start, end))
        if sound:
            delay = int(round(c.start * 1000))
            alead = (["areverse"] if inline_reverse else []) + _atempo_chain(c.speed)
            fades = fade_filters(*c.fades(), c.duration)           # after speed / reverse: the clip's own timeline
            base = alead + ['aresample=48000', 'aformat=channel_layouts=stereo']
            if has_filter("adelay"):
                tail = [f"volume={_num(c.volume)}"] + fades + [f"adelay={delay}|{delay}"]
                filters.append(f"[{i}:a]{','.join(base + tail)}[a{i}]")
            elif delay <= 0:                                        # starts at 0 and there is no adelay: nothing to delay
                filters.append(f"[{i}:a]{','.join(base + [f'volume={_num(c.volume)}'] + fades)}[a{i}]")
            else:
                # this FFmpeg has no adelay: put the clip after that much silence instead
                tail = [f"volume={_num(c.volume)}"] + fades + ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"]
                filters.append(f"[{i}:a]{','.join(base + tail)}[ar{i}]")
                filters.append(f"anullsrc=r=48000:cl=stereo:d={_num(delay / 1000.0)},"
                               f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[as{i}]")
                filters.append(f"[as{i}][ar{i}]concat=n=2:v=0:a=1[a{i}]")
            aparts.append(i)

    filters.insert(0, f"color=c={bg_color_arg(project)}:s={W}x{H}:r={fps}:d={_num(total)},format=rgba[base]")
    last = "base"
    for k, (lab, t0, t1) in enumerate(vparts):
        out = f"o{k}"
        filters.append(f"[{last}][{lab}]overlay=enable='between(t,{_num(t0)},{_num(t1)})'[{out}]")
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
    vargs, use_crf = _video_args(spec, quality, gpu)
    cmd += vargs
    if use_crf:
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


def audio_stream_cmd(path, src_start, duration, volume=1.0, rate=48000, channels=2, speed=1.0, reverse=False,
                     fade=None):
    """ffmpeg argv that writes raw signed 16-bit little-endian PCM to stdout.

    ``src_start`` / ``duration`` are SOURCE seconds; ``speed`` changes the tempo (pitch is kept) and ``reverse``
    plays that range backwards. ``fade`` is ``(fade_in, fade_out, clip_duration, offset)`` (see
    :func:`fade_filters`) when the clip has audio fades; ``offset`` is where in the clip this stream starts."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    af = (["areverse"] if reverse else []) + _atempo_chain(speed) + [f"volume={_num(volume)}"]
    if fade:
        af += fade_filters(*fade)
    return [exe, "-v", "error", "-nostdin", "-ss", f"{max(0.0, src_start):.3f}", "-t", f"{max(0.05, duration):.3f}",
            "-i", path, "-vn", "-sn", "-af", ",".join(af),
            "-ac", str(channels), "-ar", str(rate), "-f", "s16le", "-"]


def composite_still_cmd(project, t, w, h):
    """ffmpeg argv that writes ONE raw RGB24 frame of every video layer visible at ``t`` stacked in layer
    order (opacity and colour correction applied), so the preview can show layers on top of each other."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    cmd = [exe, "-v", "error", "-nostdin"]
    filters = [f"color=c={bg_color_arg(project)}:s={w}x{h}:d=1,format=rgba[base]"]
    last = "base"
    for n, c in enumerate(project.video_clips_at(t)):            # bottom layer first
        m = project.media[c.media_id]
        if m.kind == "image":
            cmd += ["-i", m.path]
        else:
            src = c.source_time(t - c.start)
            src = min(max(0.0, src), max(0.0, m.duration - 1.0 / max(1, project.fps)))
            cmd += ["-ss", f"{src:.3f}", "-i", m.path]
        moved = has_transform(c)
        src_pad = f"{n}:v"
        if wants_backdrop(project, c):
            filters.append(f"[{n}:v]split[s{n}a][s{n}b]")
            filters.append(f"[s{n}b]{','.join(backdrop_filters(w, h))}[b{n}]")
            filters.append(f"[{last}][b{n}]overlay=shortest=0[bo{n}]")
            last, src_pad = f"bo{n}", f"s{n}a"
        chain = _fit(w, h, c, ":flags=fast_bilinear")
        if not moved:
            chain.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0")
        if abs(c.brightness) > 1e-3 or abs(c.contrast - 1) > 1e-3 or abs(c.saturation - 1) > 1e-3:
            chain.append(f"eq=brightness={_num(c.brightness)}:contrast={_num(c.contrast)}:saturation={_num(c.saturation)}")
        if c.opacity < 0.999:
            chain.append(f"colorchannelmixer=aa={_num(c.opacity)}")
        if moved:
            filters.append(f"[{src_pad}]{','.join(chain)}[t{n}]")
            filters += _place(f"t{n}", f"l{n}", w, h, c, 30, f"c{n}")
        else:
            filters.append(f"[{src_pad}]{','.join(chain)}[l{n}]")
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
    retime = bool(fps) and _is_retimed(clip)
    speed = clip.speed if retime else 1.0
    # a retimed clip is sampled at fps / speed per SOURCE second, then re-stamped at fps per timeline second
    chain = [f"fps={_n6(fps / speed) if retime else fps}"] if fps else []
    chain += [f"scale={w}:{h}:flags=fast_bilinear", "setsar=1"]
    if clip is not None and (abs(clip.brightness) > 1e-3 or abs(clip.contrast - 1) > 1e-3
                             or abs(clip.saturation - 1) > 1e-3):
        chain.append(f"eq=brightness={_num(clip.brightness)}:contrast={_num(clip.contrast)}"
                     f":saturation={_num(clip.saturation)}")
    if retime:
        if clip.reverse:
            chain += ["reverse", "tpad=stop_mode=clone:stop=4"]
        # Re-stamp the frames at exactly fps per timeline second. The time base must be set first: setpts stores an
        # integer in it, and after the sampling fps filter above it is far too coarse (timestamps would collapse).
        # The closing fps filter makes the stream's frame rate match, or ffmpeg would drop / duplicate frames.
        chain += [f"settb=1/{fps}", "setpts=N", f"fps={fps}"]
    chain.append("format=rgba" if alpha else "format=rgb24")
    return ",".join(chain)


def layer_stream_cmd(path, src_start, duration, w, h, fps, clip=None, frames=None):
    """Raw RGB24 frames (w*h*3 bytes each) of ONE video layer at its own fitted size, no padding.

    ``src_start`` / ``duration`` are SOURCE seconds. The clip's speed and reverse are applied; ``frames`` caps the
    output at exactly that many frames (used for the pieces of a reversed clip, see :func:`reverse_chunks`)."""
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    cmd = [exe, "-v", "error", "-nostdin", "-ss", f"{max(0.0, src_start):.3f}", "-t", f"{max(0.05, duration):.3f}",
           "-i", path, "-an", "-sn", "-vf", _layer_vf(w, h, fps, clip, False)]
    if frames:
        cmd += ["-frames:v", str(int(frames))]
    return cmd + ["-pix_fmt", "rgb24", "-f", "rawvideo", "-"]


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
