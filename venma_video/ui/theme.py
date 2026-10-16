"""Venma Video design system (shared look with Venma): warm paper tones, clay accent, serif headings, line icons.

One place for every colour token, the global Qt stylesheet, and the icon set.
"""
import os
from PyQt5.QtCore import Qt, QByteArray, QSettings, QRectF
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QPalette, QFont, QFontDatabase
from PyQt5.QtSvg import QSvgRenderer

LIGHT = dict(
    bg="#FAF9F5", surface="#FFFFFF", panel="#F5F4ED", sunken="#EFEDE4",
    border="#E6E3D8", border_strong="#D3CFC0", text="#141413", muted="#73726C",
    faint="#A8A69C", accent="#D97757", accent_hover="#C6613F", accent_soft="#F7E6DD",
    on_accent="#FFFFFF", canvas="#EDEBE2", checker1="#E3E0D5", checker2="#F2F0E8",
)
DARK = dict(
    bg="#1F1E1D", surface="#2B2A28", panel="#262524", sunken="#1A1918",
    border="#3A3835", border_strong="#4B4944", text="#F0EEE6", muted="#A3A198",
    faint="#6F6D66", accent="#D97757", accent_hover="#E78F70", accent_soft="#3F2D26",
    on_accent="#FFFFFF", canvas="#161615", checker1="#242321", checker2="#2D2C2A",
)
T = dict(LIGHT)          # live tokens (mutated by set_theme)
NAME = "light"
SERIF = '"Tiempos Headline", "Iowan Old Style", "Palatino Linotype", Georgia, serif'
MONO = '"JetBrains Mono", "SF Mono", Menlo, Consolas, monospace'

# ---- icons (24px line icons, Lucide-style) ---------------------------------
_P = {
 "open": '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>',
 "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
 "undo": '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>',
 "redo": '<path d="M21 7v6h-6"/><path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3l3 2.7"/>',
 "rot_l": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
 "rot_r": '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
 "flip_h": '<path d="M8 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3"/><path d="M16 3h3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-3"/><path d="M12 20v2M12 14v2M12 8v2M12 2v2"/>',
 "flip_v": '<path d="M21 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v3"/><path d="M21 16v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3"/><path d="M4 12H2M10 12H8M16 12h-2M22 12h-2"/>',
 "crop": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
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
 "marquee": '<rect x="3" y="3" width="18" height="18" rx="2" stroke-dasharray="3 3"/>',
 "brush": '<path d="m9 12 8-8a2.8 2.8 0 1 1 4 4l-8 8"/><path d="M7 15c-1.7 0-3 1.3-3 3 0 1.3-2.5 1.5-2 2 1.1 1.1 2.5 2 4 2 2.2 0 4-1.8 4-4a3 3 0 0 0-3-3z"/>',
 "eraser": '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/>',
 "bucket": '<path d="m19 11-8-8-8.6 8.6a2 2 0 0 0 0 2.8l5.2 5.2c.8.8 2 .8 2.8 0L19 11Z"/><path d="m5 2 5 5"/><path d="M2 13h15"/><path d="M22 20a2 2 0 1 1-4 0c0-1.6 1.7-2.4 2-4 .3 1.6 2 2.4 2 4Z"/>',
 "pipette": '<path d="m2 22 1-1h3l9-9"/><path d="M19 8a2.8 2.8 0 0 0 0-4l-1-1a2.8 2.8 0 0 0-4 0l-2 2 5 5 2-2Z"/><path d="m5 18-3 3"/>',
 "shape": '<path d="M8.3 10a.7.7 0 0 1-.626-1.079L11.4 3a.7.7 0 0 1 1.198-.043L16.3 8.9a.7.7 0 0 1-.572 1.1Z"/><rect x="3" y="14" width="7" height="7" rx="1"/><circle cx="17.5" cy="17.5" r="3.5"/>',
 "pen": '<path d="m12 19 7-7 3 3-7 7-3-3z"/><path d="m18 13-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="m2 2 7.586 7.586"/><circle cx="11" cy="11" r="2"/>',
 "text": '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>',
 "hand": '<path d="M18 11V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0"/><path d="M14 10V4a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v2"/><path d="M10 10.5V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
 "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
 "layer_plus": '<path d="m12 2-10 5 10 5 10-5-10-5Z"/><path d="m2 12 10 5 10-5"/><path d="m2 17 10 5 10-5"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
 "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
 "duplicate": '<rect width="13" height="13" x="9" y="9" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
 "layer_merge": '<path d="M12 3v12"/><polyline points="8 11 12 15 16 11"/><path d="M4 21h16"/>',
 "eye": '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
 "eye_off": '<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" y1="2" x2="22" y2="22"/>',
 "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
 "arrow_up": '<polyline points="18 15 12 9 6 15"/>',
 "arrow_down": '<polyline points="6 9 12 15 18 9"/>',
 "swap": '<path d="m16 3 4 4-4 4"/><path d="M20 7H4"/><path d="m8 21-4-4 4-4"/><path d="M4 17h16"/>',
 "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
 "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
 "filter": '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
 "chart": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
 "tools": '<path d="m14 7 3-3a2.8 2.8 0 1 1 4 4l-3 3"/><path d="m2 22 7-7"/><path d="M5 19 2 22"/><path d="m19 15 2 2-7 7-2-2Z"/>',
 "chevrons_right": '<polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/>',
 "chevrons_left": '<polyline points="11 17 6 12 11 7"/><polyline points="18 17 13 12 18 7"/>',
 "split": '<rect width="18" height="18" x="3" y="3" rx="2"/><line x1="3" y1="12" x2="21" y2="12"/>',
 "transform": '<rect x="5" y="5" width="14" height="14" stroke-dasharray="3 2"/><rect x="2.5" y="2.5" width="3" height="3"/><rect x="10.5" y="2.5" width="3" height="3"/><rect x="18.5" y="2.5" width="3" height="3"/><rect x="2.5" y="10.5" width="3" height="3"/><rect x="18.5" y="10.5" width="3" height="3"/><rect x="2.5" y="18.5" width="3" height="3"/><rect x="10.5" y="18.5" width="3" height="3"/><rect x="18.5" y="18.5" width="3" height="3"/>',
}


def _svg(name, color, sw=1.75):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{_P[name]}</svg>').encode()


def pixmap(name, color, size=20, sw=1.75):
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
    """Window/taskbar icon: uses assets/venma.ico if present, otherwise generated clay rounded tile."""
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
except Exception:          # optional: only used for a few legacy icon names
    qta = None


def qta_icon(name, color=None, color_active=None, color_disabled=None):
    """Optional QtAwesome icon styled to the theme tokens (empty icon if qtawesome isn't installed)."""
    if qta is None:
        return QIcon()
    c = color or T["text"]
    ac = color_active or T["accent"]
    dc = color_disabled or T["faint"]
    return qta.icon(name, color=c, color_active=ac, color_selected=ac, color_disabled=dc)


def icon(name, size=20):
    if "." in name:
        return qta_icon(name)
    if name in _P:
        ic = QIcon()
        ic.addPixmap(pixmap(name, T["text"], size), QIcon.Normal, QIcon.Off)
        ic.addPixmap(pixmap(name, T["accent"], size), QIcon.Normal, QIcon.On)
        ic.addPixmap(pixmap(name, T["faint"], size), QIcon.Disabled, QIcon.Off)
        return ic
    return qta_icon(name)


# ---- global stylesheet -----------------------------------------------------
def qss(t):
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog, QMessageBox {{ background: {t['bg']}; }}
QWidget {{ color: {t['text']}; }}
QToolTip {{ background: #141413; color: #FAF9F5; border: none; padding: 5px 9px; border-radius: 6px; }}

QMenuBar {{ background: {t['bg']}; padding: 4px 8px 0 8px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {t['sunken']}; }}
QMenu {{ background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
QMenu::item:disabled {{ color: {t['faint']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}

QToolBar {{ background: {t['bg']}; border: none; border-bottom: 1px solid {t['border']}; spacing: 2px; padding: 8px 14px; }}
QToolBar::separator {{ width: 1px; background: {t['border']}; margin: 6px 8px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 9px; padding: 6px; }}
QToolButton:hover {{ background: {t['sunken']}; }}
QToolButton:pressed {{ background: {t['border']}; }}
QToolButton:checked {{ background: {t['accent_soft']}; border-color: {t['accent']}; }}
QToolButton:disabled {{ background: transparent; }}
QWidget#brand {{ background: transparent; }}
QLabel#brandName {{ font-family: {SERIF}; font-size: 16pt; font-weight: 600; letter-spacing: 0.3px; }}

QStatusBar {{ background: {t['bg']}; color: {t['muted']}; border-top: 1px solid {t['border']}; padding: 2px 10px; }}
QStatusBar::item {{ border: none; }}
QLabel#statusInfo {{ color: {t['muted']}; font-family: {MONO}; font-size: 9pt; padding: 3px 10px;
                    background: {t['sunken']}; border-radius: 10px; }}

QDockWidget {{ color: {t['text']}; font-family: {SERIF}; font-size: 12pt; }}
QDockWidget::title {{ background: {t['bg']}; padding: 12px 16px 6px 16px; border: none; text-align: left; }}
QDockWidget > QWidget {{ background: {t['bg']}; }}
QMainWindow::separator {{ background: {t['border']}; width: 1px; height: 1px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea#stage, QWidget#stageVp {{ background: {t['canvas']}; }}
QWidget#panel {{ background: {t['bg']}; }}

QGroupBox {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 14px;
            margin-top: 24px; padding: 14px 12px 12px 12px; font-size: 9.5pt; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 4px; top: 2px;
                   color: {t['muted']}; font-weight: 600; }}
QLabel {{ background: transparent; }}
QGroupBox QLabel {{ font-size: 9.5pt; }}
QLabel#val {{ color: {t['muted']}; font-family: {MONO}; font-size: 9pt; }}

QPushButton {{ background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 9px;
              padding: 7px 14px; font-weight: 500; }}
QPushButton:hover {{ background: {t['sunken']}; }}
QPushButton:pressed {{ background: {t['border']}; }}
QPushButton:disabled {{ color: {t['faint']}; border-color: {t['border']}; }}
QPushButton#primary {{ background: {t['accent']}; border: 1px solid {t['accent']}; color: {t['on_accent']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {t['sunken']}; border-color: {t['border']}; color: {t['faint']}; }}
QPushButton#chip {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 14px;
                   padding: 5px 8px; font-size: 9pt; font-weight: 500; }}
QPushButton#chip:hover {{ background: {t['accent_soft']}; border-color: {t['accent']}; }}
QPushButton#chip:disabled {{ background: transparent; }}

QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit, QComboBox {{
    background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 9px; padding: 6px 10px;
    selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border_strong']};
    selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1.5px solid {t['border_strong']}; background: {t['surface']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
QDialogButtonBox {{ padding-top: 6px; }}

QListWidget {{ background: transparent; border: none; padding: 2px 8px 8px 8px; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 8px; color: {t['muted']}; }}
QListWidget::item:hover {{ background: {t['sunken']}; }}
QListWidget::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}

QScrollBar:vertical {{ width: 12px; background: transparent; margin: 2px; }}
QScrollBar:horizontal {{ height: 12px; background: transparent; margin: 2px; }}
QScrollBar::handle {{ background: {t['border_strong']}; border-radius: 4px; min-height: 32px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {t['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QFrame#dropzone {{ background: {t['surface']}; border: 1.5px dashed {t['border_strong']}; border-radius: 24px; }}
QLabel#h1 {{ font-family: {SERIF}; font-size: 25pt; }}
QLabel#sub {{ color: {t['muted']}; font-size: 11pt; }}
QLabel#kbd {{ background: {t['sunken']}; border: 1px solid {t['border']}; border-radius: 7px; padding: 3px 9px;
             color: {t['muted']}; font-family: {MONO}; font-size: 9pt; }}

QTabWidget::pane {{ border: 1px solid {t['border']}; background: {t['bg']}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: {t['surface']}; color: {t['muted']}; border: 1px solid {t['border']};
               padding: 6px 14px; border-top-left-radius: 7px; border-top-right-radius: 7px;
               margin-right: 3px; font-weight: 500; font-size: 9pt; }}
QTabBar::tab:selected {{ background: {t['bg']}; color: {t['text']}; border-bottom-color: {t['bg']}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ background: {t['sunken']}; color: {t['text']}; }}

QToolBar#toolbox {{ background: {t['surface']}; border-right: 1px solid {t['border']}; padding: 6px 4px; spacing: 4px; }}
QToolBar#toolbox QToolButton {{ width: 28px; height: 28px; border-radius: 6px; padding: 3px; }}
QToolBar#toolbox QToolButton:hover {{ background: {t['sunken']}; }}
QToolBar#toolbox QToolButton:checked {{ background: {t['accent_soft']}; border: 1px solid {t['accent']}; }}

QToolBar#optionsBar {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; padding: 3px 10px; spacing: 8px; min-height: 36px; }}
QToolBar#optionsBar QLabel {{ font-size: 9pt; color: {t['muted']}; }}

QListWidget#layerList {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 4px; }}
QListWidget#layerList::item {{ padding: 3px 6px; border-radius: 6px; margin-bottom: 2px; }}
QListWidget#layerList::item:hover {{ background: {t['sunken']}; }}
QListWidget#layerList::item:selected {{ background: {t['accent_soft']}; border: 1px solid {t['accent']}; color: {t['text']}; }}

/* Photoshop-style Vertical Icon Sub-bar (Right Dock) */
QFrame#subBar, QWidget#subBar, QWidget#sideSubBar {{
    background: {t['surface']};
    border-left: 1px solid {t['border']};
    min-width: 38px;
    max-width: 38px;
}}
QToolButton#sideBarBtn, QPushButton#sideBarBtn {{
    width: 30px;
    height: 30px;
    border-radius: 6px;
    padding: 3px;
    border: none;
    background: transparent;
}}
QToolButton#sideBarBtn:hover, QPushButton#sideBarBtn:hover {{
    background: {t['sunken']};
}}
QToolButton#sideBarBtn:checked, QPushButton#sideBarBtn:checked {{
    background: {t['accent_soft']};
    border-left: 3px solid {t['accent']};
    border-radius: 2px 6px 6px 2px;
}}
QToolButton#sideBarToggle, QPushButton#sideBarToggle {{
    width: 28px;
    height: 28px;
    border-radius: 6px;
    border: none;
    background: transparent;
}}
QToolButton#sideBarToggle:hover, QPushButton#sideBarToggle:hover {{
    background: {t['sunken']};
}}

/* Photoshop-style Panel Header */
QFrame#sidebarHeader, QFrame#panelHeader {{
    background: {t['panel']};
    border-bottom: 1px solid {t['border']};
    padding: 6px 8px;
    min-height: 28px;
}}
QFrame#sidebarHeader QLabel, QFrame#panelHeader QLabel#panelTitle {{
    font-size: 8.5pt;
    font-weight: 700;
    color: {t['text']};
    letter-spacing: 1px;
}}
QFrame#panelHeader QToolButton {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px;
}}
QFrame#panelHeader QToolButton:hover {{
    background: {t['sunken']};
}}

/* Layer Action Buttons at bottom of Layers Panel */
QToolButton#layerActionBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 3px;
    min-width: 24px;
    max-width: 26px;
    min-height: 24px;
    max-height: 26px;
}}
QToolButton#layerActionBtn:hover {{
    background: {t['sunken']};
    border-color: {t['border']};
}}
QToolButton#layerActionBtn:pressed {{
    background: {t['border']};
}}
QToolButton#layerFilterBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 2px;
    min-width: 22px;
    max-width: 24px;
    min-height: 22px;
    max-height: 24px;
}}
QToolButton#layerFilterBtn:hover {{
    background: {t['sunken']};
}}
QToolButton#layerFilterBtn:checked {{
    background: {t['accent_soft']};
    border: 1px solid {t['accent']};
}}
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
    QSettings("Venma", "VideoEditor").setValue("theme", name)


def init(app):
    fams = set(QFontDatabase().families())
    for f in ("Inter", "SF Pro Text", "Segoe UI Variable", "Segoe UI", "Helvetica Neue", "Ubuntu", "Arial"):
        if f in fams:
            font = QFont(f, 10); break
    else:
        font = app.font()
    app.setFont(font)
    set_theme(app, QSettings("Venma", "VideoEditor").value("theme", "light"))


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
