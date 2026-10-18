"""CapCut-style clip animations: **In**, **Out** and **Combo** (pure Python, no Qt).

A clip carries an ``animation`` dict, e.g. ``{"in": "pop", "in_dur": 0.6, "out": "fade", "out_dur": 0.4}``
or ``{"combo": "pulse"}``. Like CapCut, a Combo animation loops over the whole clip and replaces In / Out.

Every preset is defined once, as small arithmetic expressions in the *FFmpeg expression language*
(``t`` = seconds since the clip started). The preview evaluates those very same strings in Python
(:func:`values_at`) and the exporter hands them to FFmpeg (:func:`expressions`), so what you see in the
preview is what gets rendered. Expressions only use ``+ - * /``, ``sin cos abs min max clip`` and ``PI``,
which mean the same in both languages.

An animation never touches the clip's own position / scale / rotation / opacity (or its keyframes); it is
layered on top of them:

* ``dx`` / ``dy``  extra offset, as a fraction of the frame width / height
* ``scale``        extra scale factor (multiplies the clip's scale)
* ``rotation``     extra rotation in degrees (added)
* ``opacity``      extra opacity factor (multiplies)
"""
import math
import re

SLOTS = ("in", "out", "combo")
DEFAULT_DURATION = 0.5
MIN_DURATION = 0.1
MIN_SCALE = 0.02                      # an animated picture never collapses to nothing (FFmpeg dislikes 0-size frames)
_C1 = 1.70158                         # "back" easing constants (a little overshoot / anticipation)
_C3 = _C1 + 1.0

# ── In / Out presets ──────────────────────────────────────────────────────────────────────────────────
# ``mx`` / ``my``: direction the picture TRAVELS, as a fraction of the frame (In starts the opposite way
#                  off its rest position, Out ends that way).  ``rot``: degrees it turns while travelling.
# ``s1``: scale at the "away" end (start of In, end of Out).  ``fade``: fades opacity 0 <-> 1.
# ``bounce``: ease the scale with overshoot (In) / anticipation (Out) instead of a smooth curve.
_EDGE = {
    "fade":        dict(label=("Fade In", "Fade Out"), fade=True),
    "grow":        dict(label=("Zoom In", "Zoom Out"), s1=0.4, fade=True),
    "swell":       dict(label=("Zoom Out", "Zoom In"), s1=1.7, fade=True),
    "pop":         dict(label=("Pop", "Pop Out"), s1=0.0, bounce=True),
    "slide_left":  dict(label=("Slide Left", "Slide Left"), mx=-1.0),
    "slide_right": dict(label=("Slide Right", "Slide Right"), mx=1.0),
    "slide_up":    dict(label=("Slide Up", "Slide Up"), my=-1.0),
    "slide_down":  dict(label=("Slide Down", "Slide Down"), my=1.0),
    "rise":        dict(label=("Rise", "Rise"), my=-0.2, fade=True),
    "fall":        dict(label=("Fall", "Fall"), my=0.2, fade=True),
    "spin":        dict(label=("Spin", "Spin"), rot=180.0, s1=0.3, fade=True),
    "swing":       dict(label=("Swing", "Swing"), rot=25.0, s1=0.7, fade=True),
    # Wipes reveal the picture through a moving edge instead of moving it. ``wipe`` = the direction the edge
    # TRAVELS (like the slides: "left" starts at the right edge and sweeps left); "circle" grows from the centre.
    # The reveal is measured against the whole frame. In only, there is no Out wipe.
    "wipe_left":   dict(label=("Wipe Left", "Wipe Left"), wipe="left"),
    "wipe_right":  dict(label=("Wipe Right", "Wipe Right"), wipe="right"),
    "wipe_up":     dict(label=("Wipe Up", "Wipe Up"), wipe="up"),
    "wipe_down":   dict(label=("Wipe Down", "Wipe Down"), wipe="down"),
    "wipe_circle": dict(label=("Wipe Circle", "Wipe Circle"), wipe="circle"),
}

# ── Combo presets: they loop while the clip plays.  ``P`` = progress through the whole clip (0..1),
# ``D`` = clip length in seconds. ────────────────────────────────────────────────────────────────────────
_COMBO = {
    "pulse":      dict(label="Pulse", scale="1+0.06*sin(2*PI*t/0.8)"),
    "heartbeat":  dict(label="Heartbeat", scale="1+0.10*max(0,sin(2*PI*t/0.9))"),
    "swing":      dict(label="Swing", rotation="8*sin(2*PI*t/1.6)"),
    "float":      dict(label="Float", dy="0.025*sin(2*PI*t/2.2)"),
    "bounce":     dict(label="Bounce", dy="-0.06*abs(sin(PI*t/0.7))"),
    "shake":      dict(label="Shake", dx="0.012*sin(2*PI*t*7)", dy="0.008*sin(2*PI*t*9+1)"),
    "spin":       dict(label="Spin", rotation="360*t/4"),
    "zoom_in":    dict(label="Slow Zoom In", scale="1+0.25*P"),
    "zoom_out":   dict(label="Slow Zoom Out", scale="1.25-0.25*P"),
    "pan_left":   dict(label="Pan Left", scale="1.15", dx="0.07*(1-2*P)"),
    "pan_right":  dict(label="Pan Right", scale="1.15", dx="-0.07*(1-2*P)"),
}

IN_PRESETS = list(_EDGE)
OUT_PRESETS = [n for n, spec in _EDGE.items() if not spec.get("wipe")]
COMBO_PRESETS = list(_COMBO)


def preset_label(slot, name):
    """Human name of a preset in a slot (``"None"`` for no animation)."""
    if not name:
        return "None"
    if slot == "combo":
        return _COMBO[name]["label"]
    return _EDGE[name]["label"][0 if slot == "in" else 1]


def presets(slot):
    """[(name, label)] for a slot, in display order."""
    names = COMBO_PRESETS if slot == "combo" else IN_PRESETS
    return [(n, preset_label(slot, n)) for n in names]


# ── the data on a clip ────────────────────────────────────────────────────────────────────────────────
def clean(anim):
    """Normalise a clip's animation dict: drop unknown names, clamp durations, enforce combo-vs-in/out."""
    if not anim:
        return {}
    out = {}
    combo = anim.get("combo")
    if combo in _COMBO:
        out["combo"] = combo
        return out
    for slot in ("in", "out"):
        name = anim.get(slot)
        if name in _EDGE and not (slot == "out" and _EDGE[name].get("wipe")):
            out[slot] = name
            d = anim.get(slot + "_dur")
            if d is not None:
                try:
                    out[slot + "_dur"] = max(MIN_DURATION, float(d))
                except (TypeError, ValueError):
                    pass
    return out


def is_active(anim):
    return bool(anim) and any(anim.get(s) for s in SLOTS)


def set_slot(anim, slot, name, duration=None):
    """Return a new animation dict with ``slot`` set to preset ``name`` (None / "" clears it).

    Picking a Combo drops In / Out, and picking an In / Out drops the Combo (as CapCut does)."""
    a = dict(anim or {})
    if slot == "combo":
        a = {"combo": name} if name else {k: v for k, v in a.items() if k != "combo"}
    else:
        a.pop("combo", None)
        if name:
            a[slot] = name
            if duration is not None:
                a[slot + "_dur"] = float(duration)
        else:
            a.pop(slot, None)
            a.pop(slot + "_dur", None)
    return clean(a)


def durations(anim, clip_duration):
    """(in_seconds, out_seconds) actually used: 0 for an unused slot, shrunk so both fit inside the clip."""
    a = anim or {}
    if a.get("combo"):
        return 0.0, 0.0
    d_in = float(a.get("in_dur", DEFAULT_DURATION)) if a.get("in") else 0.0
    d_out = float(a.get("out_dur", DEFAULT_DURATION)) if a.get("out") else 0.0
    room = max(0.0, float(clip_duration))
    d_in, d_out = min(d_in, room), min(d_out, room)
    if d_in + d_out > room > 0:
        k = room / (d_in + d_out)
        d_in, d_out = d_in * k, d_out * k
    return d_in, d_out


def split_animation(anim, left_duration, right_duration):
    """Animation dicts for the two halves of a split clip: the first keeps In, the second keeps Out
    (a Combo continues on both)."""
    a = clean(anim)
    if a.get("combo"):
        return dict(a), dict(a)
    left = {k: v for k, v in a.items() if k.startswith("in")}
    right = {k: v for k, v in a.items() if k.startswith("out")}
    return left, right


# ── expression builder ────────────────────────────────────────────────────────────────────────────────
def _num(x):
    s = f"{x:.5f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _u(start, dur):
    """Linear 0..1 progress through the window [start, start + dur]."""
    a = "t" if abs(start) < 1e-9 else f"(t-{_num(start)})"
    return f"clip({a}/{_num(dur)},0,1)"


def _edge_terms(spec, slot, start, dur):
    """dx, dy, scale, rotation expressions for one In / Out window (neutral outside it)."""
    u = _u(start, dur)
    if slot == "in":            # K: 1 at the start of the window, 0 at rest (smooth)
        k = f"(1-{u}*{u}*(3-2*{u}))"
        v = f"({u}-1)"
        kb = f"(0-({_num(_C3)}*{v}*{v}*{v}+{_num(_C1)}*{v}*{v}))"
        sign = -1.0
    else:                       # K: 0 at rest, 1 at the end of the window
        k = f"({u}*{u}*(3-2*{u}))"
        kb = f"({_num(_C3)}*{u}*{u}*{u}-{_num(_C1)}*{u}*{u})"
        sign = 1.0
    dx = dy = rot = "0"
    scale = "1"
    if spec.get("mx"):
        dx = f"({_num(sign * spec['mx'])}*{k})"
    if spec.get("my"):
        dy = f"({_num(sign * spec['my'])}*{k})"
    if spec.get("rot"):
        rot = f"({_num(sign * spec['rot'])}*{k})"
    if "s1" in spec:
        kk = kb if spec.get("bounce") else k
        scale = f"(1+({_num(spec['s1'] - 1.0)})*{kk})"
    return dx, dy, scale, rot


def _combine(parts, op):
    parts = [p for p in parts if p not in ("0", "1")]
    if not parts:
        return "0" if op == "+" else "1"
    return "(" + op.join(parts) + ")"


def expressions(anim, clip_duration):
    """FFmpeg-language expressions for an animation, or ``None`` when the clip isn't animated.

    Returns ``dict(dx, dy, scale, rotation, fade_in, fade_out, wipe)``: the first four are expression strings in
    ``t`` (clip-local seconds); ``fade_in`` / ``fade_out`` are fade durations in seconds (0 = none); ``wipe`` is
    ``None`` or ``(kind, seconds)``: the In reveal that starts at the clip's first frame (see :func:`wipe_progress`)."""
    a = clean(anim)
    if not is_active(a):
        return None
    D = max(1e-3, float(clip_duration))
    dxs, dys, scs, rts = [], [], [], []
    fade_in = fade_out = 0.0
    wipe = None
    if a.get("combo"):
        spec = _COMBO[a["combo"]]
        sub = {"P": f"clip(t/{_num(D)},0,1)", "D": _num(D)}
        for key, bucket in (("dx", dxs), ("dy", dys), ("scale", scs), ("rotation", rts)):
            if key in spec:
                bucket.append("(" + re.sub(r"\b(P|D)\b", lambda m: sub[m.group(1)], spec[key]) + ")")
    else:
        d_in, d_out = durations(a, D)
        for slot, d, start in (("in", d_in, 0.0), ("out", d_out, D - d_out)):
            if d <= 1e-6:
                continue
            spec = _EDGE[a[slot]]
            if spec.get("wipe"):
                wipe = (spec["wipe"], d)             # a mask, not a motion: the renderers handle it themselves
                continue
            dx, dy, sc, rt = _edge_terms(spec, slot, start, d)
            dxs.append(dx), dys.append(dy), scs.append(sc), rts.append(rt)
            if spec.get("fade"):
                if slot == "in":
                    fade_in = d
                else:
                    fade_out = d
    scale = _combine(scs, "*")
    return dict(dx=_combine(dxs, "+"), dy=_combine(dys, "+"), rotation=_combine(rts, "+"),
                scale=scale if scale == "1" else f"max({_num(MIN_SCALE)},{scale})",
                fade_in=fade_in, fade_out=fade_out, wipe=wipe)


def wipe_progress(seconds, var="t"):
    """Expression (FFmpeg language) for how much of the picture a wipe has revealed: 0 -> 1, eased, over
    ``seconds`` of the variable ``var`` (clip-local time; the exporter passes an offset ``T`` for ``geq``)."""
    u = f"clip(({var})/{_num(max(1e-3, seconds))},0,1)"
    return f"({u}*{u}*(3-2*{u}))"


# ── evaluation (preview) ──────────────────────────────────────────────────────────────────────────────
_FUNCS = {"sin": math.sin, "cos": math.cos, "abs": abs, "min": min, "max": max, "PI": math.pi,
          "clip": lambda x, lo, hi: min(max(x, lo), hi)}
_code_cache = {}


def _eval(expr, t):
    if expr in ("0", "1"):
        return float(expr)
    code = _code_cache.get(expr)
    if code is None:
        code = _code_cache[expr] = compile(expr, "<animation>", "eval")
    return float(eval(code, {"__builtins__": {}}, dict(_FUNCS, t=t)))


NEUTRAL = dict(dx=0.0, dy=0.0, scale=1.0, rotation=0.0, opacity=1.0)


def values_at(anim, clip_duration, t_clip):
    """The animation's effect ``t_clip`` seconds into a clip of ``clip_duration`` seconds
    (``NEUTRAL`` when the clip has no animation). A wipe-in adds a ``wipe`` key: ``(kind, revealed 0..1)``."""
    ex = expressions(anim, clip_duration)
    if ex is None:
        return dict(NEUTRAL)
    t = max(0.0, min(float(t_clip), float(clip_duration)))
    opacity = 1.0
    if ex["fade_in"] > 0:
        opacity *= min(1.0, t / ex["fade_in"])
    if ex["fade_out"] > 0:
        opacity *= min(1.0, max(0.0, (float(clip_duration) - t) / ex["fade_out"]))
    out = dict(dx=_eval(ex["dx"], t), dy=_eval(ex["dy"], t), scale=_eval(ex["scale"], t),
               rotation=_eval(ex["rotation"], t), opacity=opacity)
    if ex["wipe"] is not None:                    # only present while a wipe-in is set: (kind, how much is revealed 0..1)
        out["wipe"] = (ex["wipe"][0], _eval(wipe_progress(ex["wipe"][1]), t))
    return out
