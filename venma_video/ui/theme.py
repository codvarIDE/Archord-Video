"""Venma Video design system (shared look with Venma): warm paper tones, violet accent, serif headings, line icons.

One place for every colour token, the global Qt stylesheet, and the icon set.
"""
import os
import weakref
from PyQt5.QtCore import Qt, QByteArray, QSettings, QRectF
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QPalette, QFont, QFontDatabase
from PyQt5.QtSvg import QSvgRenderer

LIGHT = dict(
    bg="#F3F4F6", surface="#FFFFFF", panel="#FFFFFF", sunken="#E9EBEF",
    border="#E1E4E9", border_strong="#CBD0D8", text="#16181D", muted="#6B7280",
    faint="#A3A9B4", accent="#7C3AED", accent_hover="#6D28D9", accent_soft="#EDE4FD",
    on_accent="#FFFFFF", canvas="#E4E6EB", checker1="#DDE0E6", checker2="#EEF0F3",
)
DARK = dict(
    bg="#0E0E10", surface="#1C1C20", panel="#161619", sunken="#111113",
    border="#26262B", border_strong="#34343A", text="#F4F4F5", muted="#9A9AA3",
    faint="#62626B", accent="#8B5CF6", accent_hover="#A78BFA", accent_soft="#2A1A4F",
    on_accent="#FFFFFF", canvas="#08080A", checker1="#1B1B1F", checker2="#232327",
)
T = dict(DARK)           # live tokens (mutated by set_theme)
NAME = "dark"
SERIF = '"Inter", "Segoe UI Variable", "Segoe UI", "Helvetica Neue", Arial, sans-serif'
MONO = '"JetBrains Mono", "SF Mono", Menlo, Consolas, monospace'

# ---- icons (24px line icons, Lucide-style) ---------------------------------
_P = {
 "open": '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>',
 "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
 "undo": '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>',
 "redo": '<path d="M21 7v6h-6"/><path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3l3 2.7"/>',
 "check": '<path d="M20 6 9 17l-5-5"/>',
 "zoom": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
 "zoom_in": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3M11 8v6M8 11h6"/>',
 "zoom_out": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3M8 11h6"/>',
 "fit": '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
 "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
 "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
 "image": '<rect width="18" height="18" x="3" y="3" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
 "spark": '<path d="M4.5 5 12 19.5 19.5 5"/>',  # "V" brand mark
 "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
 "pause": '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
 "skip_back": '<polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/>',
 "skip_fwd": '<polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/>',
 "step_back": '<polyline points="15 18 9 12 15 6"/>',
 "step_fwd": '<polyline points="9 18 15 12 9 6"/>',
 "razor": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/>',
 "film": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18M17 3v18M3 7.5h4M3 12h18M3 16.5h4M17 7.5h4M17 16.5h4"/>',
 "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
 "import": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
 "export": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
 "volume": '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
 "magnet": '<path d="m6 15-4-4 6.75-6.77a7.79 7.79 0 0 1 11 11L13 22l-4-4 6.39-6.36a2.14 2.14 0 0 0-3-3L6 15"/><path d="m5 8 4 4"/><path d="m12 15 4 4"/>',
 "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
 "move": '<polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/><polyline points="15 19 12 22 9 19"/><polyline points="19 9 22 12 19 15"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/>',
 "text": '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>',
 "hand": '<path d="M18 11V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0"/><path d="M14 10V4a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v2"/><path d="M10 10.5V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
 "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
 "layer_plus": '<path d="m12 2-10 5 10 5 10-5-10-5Z"/><path d="m2 12 10 5 10-5"/><path d="m2 17 10 5 10-5"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
 "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
 "duplicate": '<rect width="13" height="13" x="9" y="9" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
 "eye": '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
 "eye_off": '<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" y1="2" x2="22" y2="22"/>',
 "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
 "unlock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
 "volume_off": '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="22" x2="16" y1="9" y2="15"/><line x1="16" x2="22" y1="9" y2="15"/>',
 "arrow_up": '<polyline points="18 15 12 9 6 15"/>',
 "arrow_down": '<polyline points="6 9 12 15 18 9"/>',
 "swap": '<path d="m16 3 4 4-4 4"/><path d="M20 7H4"/><path d="m8 21-4-4 4-4"/><path d="M4 17h16"/>',
 "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
 "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
 "ripple": '<path d="M8 4v16"/><path d="M8 12h6"/><polyline points="11.5 9.5 14 12 11.5 14.5"/><rect x="17" y="7" width="4" height="10" rx="1"/>',
 "slip": '<rect x="3" y="6" width="18" height="12" rx="2"/><line x1="7" y1="12" x2="17" y2="12"/><polyline points="10 9.5 7.5 12 10 14.5"/><polyline points="14 9.5 16.5 12 14 14.5"/>',
 "keyframe": '<polygon points="12 3 21 12 12 21 3 12 12 3"/>',
}


def _svg(name, color, sw=1.75):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{_P[name]}</svg>').encode()


def _svg_pixmap(name, color, size=20, sw=1.75):
    s = size * 2
    pm = QPixmap(s, s); pm.fill(Qt.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(_svg(name, color, sw))).render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


def get_app_icon_path():
    """Returns absolute path to venma_video/assets/venma.ico if it exists, else None."""
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for base in (pkg, os.getcwd()):
        cand = os.path.join(base, "assets", "venma.ico")
        if os.path.isfile(cand):
            return cand
    return None


def app_icon():
    """Window/taskbar icon: uses assets/venma.ico if present, otherwise generated violet rounded tile."""
    ico_path = get_app_icon_path()
    if ico_path:
        return QIcon(ico_path)

    ic = QIcon()
    for s in (16, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen); p.setBrush(QColor(LIGHT["accent"]))
        p.drawRoundedRect(0, 0, s, s, s * 0.22, s * 0.22)
        m = int(s * 0.2)
        QSvgRenderer(QByteArray(_svg("spark", "#FFFFFF", 2.6))).render(p, QRectF(m, m, s - 2 * m, s - 2 * m))
        p.end()
        ic.addPixmap(pm)
    return ic


def app_pixmap(size=64):
    """Returns a QPixmap of the application icon at the requested size."""
    ico = app_icon()
    return ico.pixmap(size, size)



try:
    import qtawesome as qta
except Exception:          # optional: without it every icon falls back to the built-in SVG line set
    qta = None

# ---- semantic icon names -> (QtAwesome glyph, built-in SVG fallback) --------------------------------------
# Font Awesome 5 solid ("fa5s") is bundled with qtawesome. A glyph that a given qtawesome version does not
# know simply falls back to the SVG, so a typo can never crash the app.
_ICONS = {
    # file
    "new": ("fa5s.file", "open"), "open": ("fa5s.folder-open", "open"), "save": ("fa5s.save", "save"),
    "saveas": ("fa5s.file-signature", "save"), "import": ("fa5s.file-import", "import"),
    "export": ("fa5s.file-export", "export"), "settings": ("fa5s.cog", "sliders"), "quit": ("fa5s.power-off", None),
    # edit
    "undo": ("fa5s.undo-alt", "undo"), "redo": ("fa5s.redo-alt", "redo"), "split": ("fa5s.cut", "razor"),
    "razor": ("fa5s.cut", "razor"), "trash": ("fa5s.trash-alt", "trash"), "ripple_delete": ("fa5s.backspace", "trash"),
    "duplicate": ("fa5s.clone", "duplicate"), "reset": ("fa5s.sync-alt", "undo"), "check": ("fa5s.check", "check"),
    # insert
    "text": ("fa5s.font", "text"), "title": ("fa5s.heading", "text"), "lower_third": ("fa5s.tag", "text"),
    "subtitle": ("fa5s.closed-captioning", "text"), "layer_plus": ("fa5s.plus-square", "layer_plus"),
    "audio_plus": ("fa5s.music", "music"), "prune": ("fa5s.broom", "trash"), "plus": ("fa5s.plus", "plus"),
    # transport
    "play": ("fa5s.play", "play"), "pause": ("fa5s.pause", "pause"),
    "skip_back": ("fa5s.fast-backward", "skip_back"), "skip_fwd": ("fa5s.fast-forward", "skip_fwd"),
    "step_back": ("fa5s.step-backward", "step_back"), "step_fwd": ("fa5s.step-forward", "step_fwd"),
    # view
    "zoom_in": ("fa5s.search-plus", "zoom_in"), "zoom_out": ("fa5s.search-minus", "zoom_out"),
    "fit": ("fa5s.expand-arrows-alt", "fit"), "magnet": ("fa5s.magnet", "magnet"),
    "moon": ("fa5s.moon", "moon"), "sun": ("fa5s.sun", "sun"),
    # tools
    "move": ("fa5s.mouse-pointer", "move"), "ripple": ("fa5s.angle-double-right", "ripple"),
    "slip": ("fa5s.arrows-alt-h", "slip"), "hand": ("fa5s.hand-paper", "hand"), "zoom": ("fa5s.search", "zoom"),
    # media / layers
    "film": ("fa5s.film", "film"), "music": ("fa5s.music", "music"), "image": ("fa5s.image", "image"),
    "layers": ("fa5s.layer-group", "layers"), "eye": ("fa5s.eye", "eye"), "eye_off": ("fa5s.eye-slash", "eye_off"),
    "lock": ("fa5s.lock", "lock"), "unlock": ("fa5s.lock-open", "unlock"), "volume_off": ("fa5s.volume-mute", "volume_off"), "volume": ("fa5s.volume-up", "volume"), "arrow_up": ("fa5s.arrow-up", "arrow_up"),
    "arrow_down": ("fa5s.arrow-down", "arrow_down"),
    # panels & UI chrome
    "panel_media": ("fa5s.photo-video", "film"), "panel_props": ("fa5s.sliders-h", "sliders"),
    "panel_history": ("fa5s.history", "history"), "panel_timeline": ("fa5s.stream", "layers"),
    "sliders": ("fa5s.sliders-h", "sliders"), "history": ("fa5s.history", "history"),
    "panels": ("fa5s.th-large", "layers"), "workspace": ("fa5s.columns", "layers"),
    "float": ("fa5s.window-restore", "swap"), "close": ("fa5s.times", "plus"), "search": ("fa5s.search", "zoom"),
    "grid": ("fa5s.th", "layers"), "list": ("fa5s.list", "layers"), "chevron_right": ("fa5s.chevron-right", "step_fwd"),
    "chevron_down": ("fa5s.chevron-down", "arrow_down"), "palette": ("fa5s.palette", None),
    "adjust": ("fa5s.adjust", None), "bold": ("fa5s.bold", None), "italic": ("fa5s.italic", None),
    "align_left": ("fa5s.align-left", None), "align_center": ("fa5s.align-center", None),
    "align_right": ("fa5s.align-right", None), "keyboard": ("fa5s.keyboard", None), "info": ("fa5s.info-circle", None),
    "spark": ("fa5s.video", "spark"),
    "keyframe": ("fa5s.gem", "keyframe"),
}


def _qta_icon(glyph, color, **extra):
    """A qtawesome icon, or None when qtawesome / that glyph is unavailable."""
    if qta is None or not glyph:
        return None
    try:
        return qta.icon(glyph, color=color, **extra)
    except Exception:
        return None


def pixmap(name, color, size=20, sw=1.75):
    """A device-pixel-ratio-aware pixmap of icon ``name`` in ``color`` (used by painted widgets)."""
    glyph, fallback = _ICONS.get(name, (None, name))
    ic = _qta_icon(glyph, color)
    if ic is not None:
        pm = ic.pixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2)
        return pm
    if fallback in _P:
        return _svg_pixmap(fallback, color, size, sw)
    pm = QPixmap(size * 2, size * 2); pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(2)
    return pm


def qta_icon(name, color=None, color_active=None, color_disabled=None):
    """Raw QtAwesome icon (``"fa5s.play"`` style names) styled to the theme tokens."""
    ic = _qta_icon(name, color or T["text"], color_active=color_active or T["accent"],
                   color_selected=color_active or T["accent"], color_disabled=color_disabled or T["faint"])
    return ic if ic is not None else QIcon()


def icon(name, size=20, color=None):
    """Themed QIcon for a semantic name (``"play"``) or a raw qtawesome name (``"fa5s.play"``).
    Normal = text colour, hover/checked = accent, disabled = faint. ``color`` pins one colour for every state."""
    if "." in name:
        return qta_icon(name, color)
    glyph, fallback = _ICONS.get(name, (None, name))
    base = color or T["text"]
    on = color or T["accent"]
    ic = _qta_icon(glyph, base, color_on=on, color_active=on, color_selected=on, color_disabled=T["faint"])
    if ic is not None:
        return ic
    if fallback in _P:
        out = QIcon()
        out.addPixmap(_svg_pixmap(fallback, base, size), QIcon.Normal, QIcon.Off)
        out.addPixmap(_svg_pixmap(fallback, on, size), QIcon.Normal, QIcon.On)
        out.addPixmap(_svg_pixmap(fallback, T["faint"], size), QIcon.Disabled, QIcon.Off)
        return out
    return QIcon()


# ---- live icon binding: every icon re-tints itself when the theme flips ------------------------------------
_BOUND = []          # [(weakref, name, size, colour-token or None)]


def set_icon(obj, name, size=20, color_token=None):
    """Set an icon on an action / button / label and remember it so ``refresh_icons`` can re-tint it."""
    _apply(obj, name, size, color_token)
    _BOUND[:] = [e for e in _BOUND if e[0]() is not obj]      # one binding per object (re-binding replaces it)
    _BOUND.append((weakref.ref(obj), name, size, color_token))
    return obj


def _apply(obj, name, size, color_token):
    col = T[color_token] if color_token else None
    if hasattr(obj, "setIcon"):
        obj.setIcon(icon(name, size, col))
    elif hasattr(obj, "setPixmap"):                           # QLabel: a static pixmap
        obj.setPixmap(pixmap(name, col or T["muted"], size))


def refresh_icons():
    alive = []
    for ref, name, size, tok in _BOUND:
        o = ref()
        if o is None:
            continue
        try:
            _apply(o, name, size, tok)
        except RuntimeError:                                  # underlying C++ object already deleted
            continue
        alive.append((ref, name, size, tok))
    _BOUND[:] = alive


# ---- global stylesheet -----------------------------------------------------
def qss(t):
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog, QMessageBox {{ background: {t['bg']}; }}
QWidget {{ color: {t['text']}; }}
QToolTip {{ background: #26262B; color: #F4F4F5; border: 1px solid #34343A; padding: 5px 9px; border-radius: 6px; }}

/* menus (the single "Menu" button) */
QMenu {{ background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 30px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
QMenu::item:disabled {{ color: {t['faint']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}

/* top bar */
QFrame#topBar {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; }}
QLabel#brandName {{ font-size: 12pt; font-weight: 700; letter-spacing: 0.2px; }}
QLabel#projectName {{ color: {t['muted']}; font-size: 9.5pt; font-weight: 500; }}
QToolButton#menuBtn {{ background: transparent; border: none; border-radius: 8px; padding: 6px 10px; font-weight: 500; }}
QToolButton#menuBtn:hover {{ background: {t['surface']}; }}
QToolButton#menuBtn::menu-indicator {{ image: none; width: 0; }}

QStatusBar {{ background: {t['bg']}; color: {t['muted']}; border-top: 1px solid {t['border']}; padding: 0 10px; font-size: 8.5pt; }}
QStatusBar::item {{ border: none; }}

/* generic buttons */
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }}
QToolButton:hover {{ background: {t['surface']}; }}
QToolButton:pressed {{ background: {t['border']}; }}
QToolButton:checked {{ background: {t['accent_soft']}; color: {t['accent']}; }}
QToolButton:disabled {{ background: transparent; }}
QPushButton {{ background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 8px;
              padding: 7px 14px; font-weight: 500; }}
QPushButton:hover {{ background: {t['border']}; }}
QPushButton:pressed {{ background: {t['border_strong']}; }}
QPushButton:disabled {{ color: {t['faint']}; border-color: {t['border']}; }}
QPushButton#primary {{ background: {t['accent']}; border: 1px solid {t['accent']}; color: {t['on_accent']}; font-weight: 700; }}
QPushButton#primary:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {t['sunken']}; border-color: {t['border']}; color: {t['faint']}; }}

/* panels */
QWidget#panel, QFrame#panel {{ background: {t['panel']}; }}
QFrame#panelFrame {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 0; }}
QLabel#panelTitle {{ font-size: 9pt; font-weight: 600; color: {t['muted']}; }}
QFrame#panelHead {{ background: transparent; border: none; border-bottom: 1px solid {t['border']}; }}
QSplitter::handle {{ background: {t['bg']}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:hover {{ background: {t['accent_soft']}; }}

/* media / text category tabs (big icon over label, CapCut style) */
QToolButton#catTab {{ background: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0;
    padding: 9px 4px 7px 4px; color: {t['muted']}; font-size: 8.5pt; font-weight: 600; }}
QToolButton#catTab:hover {{ color: {t['text']}; background: transparent; }}
QToolButton#catTab:checked {{ color: {t['text']}; border-bottom: 2px solid {t['accent']}; background: transparent; }}

/* Details sections: flat, divided, no boxes */
QFrame#section {{ background: transparent; border: none; border-bottom: 1px solid {t['border']}; border-radius: 0; }}
QToolButton#sectionHead {{ background: transparent; border: none; border-radius: 6px; padding: 9px 2px;
    font-weight: 600; font-size: 9.5pt; text-align: left; }}
QToolButton#sectionHead:hover {{ background: transparent; color: {t['accent']}; }}
QLabel#sectionNote {{ color: {t['muted']}; font-size: 9pt; }}
QLabel#val {{ color: {t['muted']}; font-size: 9pt; }}
QLabel#muted {{ color: {t['muted']}; font-size: 9pt; }}
QLabel {{ background: transparent; }}

/* inputs */
QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit, QComboBox {{
    background: {t['sunken']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
QLineEdit#search {{ border-radius: 16px; padding: 6px 14px; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border_strong']};
    selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1.5px solid {t['border_strong']}; background: {t['sunken']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
QDialogButtonBox {{ padding-top: 6px; }}

/* sliders: thin violet track, round handle */
QSlider::groove:horizontal {{ height: 4px; background: {t['border_strong']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['text']}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: {t['accent']}; }}

/* lists (media bin) */
QListWidget {{ background: transparent; border: none; padding: 2px; }}
QListWidget::item {{ padding: 6px; border-radius: 8px; color: {t['muted']}; }}
QListWidget::item:hover {{ background: {t['surface']}; }}
QListWidget::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; border: 1px solid {t['accent']}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 2px; }}
QScrollBar::handle {{ background: {t['border_strong']}; border-radius: 3px; min-height: 32px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {t['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* empty-state / text preset cards */
QFrame#dropzone {{ background: {t['sunken']}; border: 1.5px dashed {t['border_strong']}; border-radius: 12px; }}
QPushButton#card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px;
    padding: 16px 10px; font-weight: 600; text-align: center; }}
QPushButton#card:hover {{ border-color: {t['accent']}; background: {t['accent_soft']}; }}

/* player */
QWidget#monitor {{ background: {t['canvas']}; }}
QFrame#transport {{ background: {t['panel']}; border: none; border-top: 1px solid {t['border']}; }}
QLabel#tcMain {{ font-family: {MONO}; font-size: 11pt; font-weight: 700; color: {t['accent']}; }}
QLabel#tcDur {{ font-family: {MONO}; font-size: 10pt; color: {t['muted']}; }}
QLabel#fmtLabel {{ color: {t['muted']}; font-size: 8.5pt; padding: 2px 4px; }}
QToolButton#transportBtn {{ border-radius: 8px; padding: 6px; }}
QToolButton#playBtn {{ background: {t['accent']}; border: none; border-radius: 18px; padding: 8px; }}
QToolButton#playBtn:hover {{ background: {t['accent_hover']}; }}

/* timeline toolbar */
QFrame#tlBar {{ background: {t['panel']}; border: none; border-bottom: 1px solid {t['border']}; }}
QFrame#tlBar QFrame#vsep {{ background: {t['border']}; max-width: 1px; min-width: 1px; }}
QToolButton#tlBtn {{ border-radius: 6px; padding: 5px; }}
QToolButton#tlBtn:checked {{ background: {t['accent_soft']}; border: 1px solid transparent; }}
QToolButton#tlText {{ border-radius: 6px; padding: 5px 9px; font-size: 9pt; font-weight: 500; }}
"""


def _palette(t):
    p = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "surface"), (QPalette.AlternateBase, "panel"),
                      (QPalette.Button, "surface"), (QPalette.Text, "text"), (QPalette.WindowText, "text"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
                      (QPalette.HighlightedText, "on_accent"), (QPalette.PlaceholderText, "faint")):
        p.setColor(role, QColor(t[key]))
    return p


def set_theme(app, name):
    global NAME
    NAME = name
    T.clear(); T.update(DARK if name == "dark" else LIGHT)
    app.setPalette(_palette(T))
    app.setStyleSheet(qss(T))
    refresh_icons()
    QSettings("Venma", "VideoEditor").setValue("theme_v2", name)


def init(app):
    fams = set(QFontDatabase().families())
    for f in ("Inter", "SF Pro Text", "Segoe UI Variable", "Segoe UI", "Helvetica Neue", "Ubuntu", "Arial"):
        if f in fams:
            font = QFont(f, 10); break
    else:
        font = app.font()
    app.setFont(font)
    set_theme(app, QSettings("Venma", "VideoEditor").value("theme_v2", "dark"))


def tidy(dlg):
    """Final touches for dialogs: clean spin boxes, primary OK button, sensible width."""
    from PyQt5.QtWidgets import QAbstractSpinBox, QDialogButtonBox
    for sb in dlg.findChildren(QAbstractSpinBox):
        sb.setButtonSymbols(QAbstractSpinBox.NoButtons)
    for bb in dlg.findChildren(QDialogButtonBox):
        ok = bb.button(QDialogButtonBox.Ok)
        if ok: ok.setObjectName("primary"); ok.setDefault(True)
    dlg.setMinimumWidth(360)
    dlg.layout().setContentsMargins(22, 20, 22, 18)
    dlg.layout().setVerticalSpacing(12) if hasattr(dlg.layout(), "setVerticalSpacing") else None