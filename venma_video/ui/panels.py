"""Side panels and dialogs: Media bin, Clip properties, History, New project, Export."""
import math
import os

from PyQt5.QtCore import Qt, QSize, QMimeData, pyqtSignal, QByteArray
from PyQt5.QtGui import QPixmap, QPainter, QColor, QIcon, QFont
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton, QListWidget, QListWidgetItem,
    QAbstractItemView, QComboBox, QSpinBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QFileDialog, QFrame, QGridLayout, QPlainTextEdit, QFontComboBox, QToolButton, QCheckBox,
    QColorDialog, QScrollArea, QMenu, QSizePolicy, QDoubleSpinBox,
)

from . import theme
from ..core import animation as anim
from ..core import ffmpeg_tools as ft
from ..core.project import (
    PRESETS, ASPECTS, EXPORT_PRESETS, canvas_size, KEYFRAMED_PROPS, MIN_SPEED, MAX_SPEED, SPEED_PRESETS, TRANSITIONS, MIN_TRANSITION, MAX_TRANSITION,
    DEFAULT_TRANSITION,
)
from ..core.project import normalize_color
from .timeline import MIME_MEDIA, format_tc
from .docks import CollapsibleSection

TILE = QSize(104, 58)


def _fmt_dur(sec):
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def placeholder_tile(kind):
    pm = QPixmap(TILE)
    pm.fill(QColor(theme.T["sunken"]))
    p = QPainter(pm)
    ic = theme.pixmap("music" if kind == "audio" else "film", theme.T["muted"], 24)
    p.drawPixmap((TILE.width() - 24) // 2, (TILE.height() - 24) // 2, ic)
    p.end()
    return pm


def fit_tile(img_pix):
    pm = QPixmap(TILE)
    pm.fill(QColor("black"))
    s = img_pix.scaled(TILE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    p = QPainter(pm)
    p.drawPixmap((TILE.width() - s.width()) // 2, (TILE.height() - s.height()) // 2, s)
    p.end()
    return pm


# ── Media bin ────────────────────────────────────────────────────────────────
class MediaList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(TILE)
        self.setGridSize(QSize(TILE.width() + 22, TILE.height() + 44))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)     # Ctrl/Shift-click or drag a box
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setSpacing(4)

    def mimeData(self, items):
        md = QMimeData()
        if items:
            ids = "\n".join(it.data(Qt.UserRole) for it in sorted(items, key=self.row))
            md.setData(MIME_MEDIA, QByteArray(ids.encode("utf-8")))
        return md

    def mimeTypes(self):
        return [MIME_MEDIA]


class MediaBin(QWidget):
    importRequested = pyqtSignal()
    addRequested = pyqtSignal(str)
    removeRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        self.b_import = QPushButton(" Import")
        self.b_import.setObjectName("primary")
        theme.set_icon(self.b_import, "import", 14, "on_accent")
        self.b_import.setToolTip("Import media (Ctrl+I) - or just drag files onto the window")
        self.b_import.clicked.connect(self.importRequested.emit)
        lay.addWidget(self.b_import)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search media")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.b_view = QToolButton()
        self.b_view.setCheckable(True)
        self.b_view.setToolTip("Switch between thumbnails and list")
        theme.set_icon(self.b_view, "list", 15)
        self.b_view.toggled.connect(self._set_list_mode)
        row.addWidget(self.search, 1)
        row.addWidget(self.b_view)
        lay.addLayout(row)

        self.list = MediaList()
        self.list.itemDoubleClicked.connect(lambda it: self.addRequested.emit(self._sel() or it.data(Qt.UserRole)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)

        self.empty = QFrame()                              # one calm empty state instead of text + hint
        self.empty.setObjectName("dropzone")
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(16, 28, 16, 28)
        el.setSpacing(8)
        ic = QLabel()
        ic.setAlignment(Qt.AlignCenter)
        theme.set_icon(ic, "import", 28)
        t1 = QLabel("Drop media here")
        t1.setAlignment(Qt.AlignCenter)
        t1.setStyleSheet("font-weight: 600;")
        t2 = QLabel("Video, audio or images. Drag them down to the timeline.")
        t2.setObjectName("muted")
        t2.setAlignment(Qt.AlignCenter)
        t2.setWordWrap(True)
        for w in (ic, t1, t2):
            el.addWidget(w)
        lay.addWidget(self.empty)
        self._sync_empty()

    def _menu(self, pos):
        if not self._sel():
            return
        m = QMenu(self)
        m.addAction("Add to timeline", self._add)
        m.addAction("Remove from project", self._remove)
        m.exec_(self.list.viewport().mapToGlobal(pos))

    # -- selection helpers
    def _sel(self):
        """Selected media ids in bin order, joined by newlines (None if nothing is selected)."""
        items = sorted(self.list.selectedItems(), key=self.list.row)
        return "\n".join(it.data(Qt.UserRole) for it in items) or None

    def _add(self):
        if self._sel():
            self.addRequested.emit(self._sel())

    def _remove(self):
        if self._sel():
            self.removeRequested.emit(self._sel())

    # -- view modes / filter
    def _set_list_mode(self, on):
        L = self.list
        if on:
            L.setViewMode(QListWidget.ListMode)
            L.setGridSize(QSize())
            L.setIconSize(QSize(64, 36))
            L.setSpacing(2)
        else:
            L.setViewMode(QListWidget.IconMode)
            L.setIconSize(TILE)
            L.setGridSize(QSize(TILE.width() + 22, TILE.height() + 44))
            L.setSpacing(4)
        L.setMovement(QListWidget.Static)
        theme.set_icon(self.b_view, "grid" if on else "list", 15)
        self.b_view.setToolTip("Show thumbnails" if on else "Show as list")

    def _filter(self, text):
        q = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in (it.data(Qt.UserRole + 1) or "").lower())

    def _sync_empty(self):
        none = self.list.count() == 0
        self.empty.setVisible(none)
        self.list.setVisible(not none)

    # -- content
    def clear(self):
        self.list.clear()

    def add_item(self, media, pix=None):
        it = QListWidgetItem(QIcon(pix or placeholder_tile(media.kind)),
                             f"{media.name}\n{_fmt_dur(media.duration) if media.kind != 'image' else 'image'}")
        it.setData(Qt.UserRole, media.id)
        it.setData(Qt.UserRole + 1, media.name)
        it.setToolTip(f"{media.path}\n{media.width}×{media.height}" if media.width else media.path)
        it.setTextAlignment(Qt.AlignHCenter)
        self.list.addItem(it)
        self._sync_empty()

    def set_thumb(self, media_id, pix):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == media_id:
                it.setIcon(QIcon(fit_tile(pix)))

    def populate(self, project, thumbs):
        self.clear()
        for m in project.library():
            th = thumbs.get(m.id)
            self.add_item(m, fit_tile(th) if th is not None else None)
        self._filter(self.search.text())
        self._sync_empty()


# ── Clip properties ──────────────────────────────────────────────────────────
class _SliderRow(QWidget):
    changed = pyqtSignal(float)
    keyframeToggled = pyqtSignal()

    def __init__(self, label, lo, hi, default, unit="%", has_kf=False, parent=None, div=1):
        super().__init__(parent)
        self.default, self.unit, self.div = default, unit, div      # div: slider units per shown unit (10 = tenths)
        self.has_kf = has_kf
        self._kf_active = False
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(4)
        
        # Name label
        self.name = QLabel(label)
        lay.addWidget(self.name, 0, 0)
        
        # Right header layout (Value + Keyframe icon button)
        right_box = QHBoxLayout()
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(6)
        right_box.addStretch(1)
        
        self.val = QLabel()
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.val.setStyleSheet(f"color: {theme.T['muted']};")
        right_box.addWidget(self.val)
        
        if has_kf:
            self.kf_btn = QToolButton()
            self.kf_btn.setFixedSize(18, 18)
            self.kf_btn.setCursor(Qt.PointingHandCursor)
            self.kf_btn.setToolTip("Add / Remove Keyframe at Playhead")
            self.kf_btn.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0px; }")
            self._update_kf_icon(False)
            self.kf_btn.clicked.connect(self.keyframeToggled.emit)
            right_box.addWidget(self.kf_btn)
        else:
            self.kf_btn = None
            
        lay.addLayout(right_box, 0, 1)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        lay.addWidget(self.slider, 1, 0, 1, 2)
        self.slider.valueChanged.connect(self._moved)
        self.slider.mouseDoubleClickEvent = lambda e: self.slider.setValue(self.default)

    def _update_kf_icon(self, active):
        self._kf_active = active
        if self.kf_btn:
            color = theme.T["accent"] if active else theme.T["faint"]
            theme.set_icon(self.kf_btn, "keyframe", 13, "accent" if active else "faint")

    def set_keyframe_active(self, active):
        if self.kf_btn:
            self._update_kf_icon(bool(active))

    def _text(self, v):
        return f"{v}{self.unit}" if self.div == 1 else f"{v / self.div:.1f}{self.unit}"

    def _moved(self, v):
        self.val.setText(self._text(v))
        self.changed.emit(float(v))

    def set_value(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v)))
        self.slider.blockSignals(False)
        self.val.setText(self._text(int(round(v))))

    def set_maximum(self, hi):
        """Change the slider's top end without emitting (the value is re-set by the caller)."""
        self.slider.blockSignals(True)
        self.slider.setMaximum(max(1, int(hi)))
        self.slider.blockSignals(False)


class _ColorButton(QPushButton):
    """A swatch button that opens the colour dialog."""
    colorChanged = pyqtSignal(str)

    def __init__(self, color="#FFFFFF", title="Colour", parent=None):
        super().__init__(parent)
        self._title = title
        self.setFixedSize(44, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._pick)
        self.set_color(color)

    def color(self):
        return self._color

    def set_color(self, color):
        self._color = QColor(color).name().upper()
        self.setStyleSheet(f"QPushButton {{ background: {self._color}; border: 1px solid {theme.T['border_strong']};"
                           f" border-radius: 5px; }}")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, self._title)
        if c.isValid():
            self.set_color(c.name())
            self.colorChanged.emit(self._color)


class PropertiesPanel(QWidget):
    propertyEdited = pyqtSignal(str, float)    # clip attribute, value
    titleEdited = pyqtSignal(str, object)      # title style key, value (title clips only)
    resetRequested = pyqtSignal()
    keyframeAdded = pyqtSignal(float)          # clip-local time: record current props as a keyframe here
    keyframeRemoved = pyqtSignal(float)        # clip-local time: remove keyframe near this time
    keyframesCleared = pyqtSignal()            # clear all keyframes from the selected clip
    keyframeToggleRequested = pyqtSignal(float)    # clip-local time: the diamond next to a slider (add here, or remove the one here)
    keyframeSeek = pyqtSignal(float)           # clip-local time: jump the playhead to a keyframe (prev / next / list click)
    animationEdited = pyqtSignal(str, str, float)   # slot ("in" | "out" | "combo"), preset name ("" = none), seconds
    speedEdited = pyqtSignal(float)            # playback speed multiplier chosen in the Speed section (live while dragging)
    transitionEdited = pyqtSignal(str, float)  # transition name ("" = none), seconds
    reverseEdited = pyqtSignal(bool)           # "Play in reverse" switched on / off
    fadeEdited = pyqtSignal(str, float)        # audio fade: "in" | "out", seconds (live while dragging)

    # attribute -> (label, lo, hi, default, scale between slider value and attribute)
    SPEC = [
        ("volume",     "Volume",     0,    200, 100, 100.0),
        ("opacity",    "Opacity",    0,    100, 100, 100.0),
        ("brightness", "Brightness", -100, 100,   0, 100.0),
        ("contrast",   "Contrast",   0,    200, 100, 100.0),
        ("saturation", "Saturation", 0,    300, 100, 100.0),
        ("scale",      "Scale",      5,    400, 100, 100.0),
        ("pos_x",      "Position X", -50,  150,  50, 100.0),
        ("pos_y",      "Position Y", -50,  150,  50, 100.0),
        ("rotation",   "Rotation",  -180,  180,   0,   1.0),
    ]
    XFORM = ("scale", "pos_x", "pos_y", "rotation")

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        self._loading = False
        self._t_clip = 0.0          # current clip-local time (set by main window)
        self.title = QLabel("No clip selected")
        self.title.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.title.setWordWrap(True)
        self.info = QLabel("")
        self.info.setStyleSheet(f"color: {theme.T['muted']}; font-size: 9pt;")
        self.info.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.info)
        self._build_text_section(lay)
        self._build_speed_section(lay)
        self.rows = {}
        self.sec_audio = CollapsibleSection("Audio")
        self.sec_visual = CollapsibleSection("Opacity & colour")
        self.sec_xform = CollapsibleSection("Position & size")
        for attr, label, lo, hi, default, scale in self.SPEC:
            has_kf = attr in self.XFORM or attr == "opacity"
            r = _SliderRow(label, lo, hi, default, has_kf=has_kf)
            r.changed.connect(lambda v, a=attr, s=scale: self.propertyEdited.emit(a, v / s))
            if has_kf:
                r.keyframeToggled.connect(lambda a=attr: self._toggle_property_kf(a))
            self.rows[attr] = (r, scale)
            (self.sec_audio if attr == "volume" else self.sec_xform if attr in self.XFORM else self.sec_visual).add(r)
        self.fade_rows = {}                                # audio fades: sliders in tenths of a second
        for which, label in (("in", "Fade in"), ("out", "Fade out")):
            r = _SliderRow(label, 0, 100, 0, unit=" s", div=10)
            r.slider.setToolTip(f"{label}: the sound ramps {'up from' if which == 'in' else 'down to'} silence over "
                                f"this many seconds (double-click to switch off)")
            r.changed.connect(lambda v, w=which: None if self._loading else self.fadeEdited.emit(w, v / 10.0))
            self.fade_rows[which] = r
            self.sec_audio.add(r)
        lay.addWidget(self.sec_audio)
        lay.addWidget(self.sec_xform)
        lay.addWidget(self.sec_visual)
        self.b_reset = QPushButton(" Reset effects")
        theme.set_icon(self.b_reset, "reset", 13)
        self.b_reset.clicked.connect(self.resetRequested.emit)
        lay.addWidget(self.b_reset)
        self._build_transition_section(lay)
        self._build_animation_section(lay)

        # ── Keyframes section ──────────────────────────────────────────────
        self.sec_kf = CollapsibleSection("Keyframes")
        kflay = self.sec_kf.body_lay

        nav_row = QHBoxLayout()
        self.b_kf_prev = QPushButton("◀")
        self.b_kf_prev.setFixedWidth(34)
        self.b_kf_prev.setToolTip("Jump to the previous keyframe (Alt+Left)")
        self.b_kf_prev.clicked.connect(lambda: self._seek_keyframe(-1))
        self.b_kf_add = QPushButton(" Add Keyframe")
        theme.set_icon(self.b_kf_add, "keyframe", 13)
        self.b_kf_add.setToolTip("Record the current position, scale, rotation and opacity as a keyframe at the playhead.\n"
                                 "After that, moving / resizing / rotating the picture on any other frame adds a keyframe there by itself.")
        self.b_kf_add.clicked.connect(lambda: self.keyframeAdded.emit(self._t_clip))
        self.b_kf_next = QPushButton("▶")
        self.b_kf_next.setFixedWidth(34)
        self.b_kf_next.setToolTip("Jump to the next keyframe (Alt+Right)")
        self.b_kf_next.clicked.connect(lambda: self._seek_keyframe(1))
        nav_row.addWidget(self.b_kf_prev)
        nav_row.addWidget(self.b_kf_add, 1)
        nav_row.addWidget(self.b_kf_next)
        kflay.addLayout(nav_row)

        kf_btn_row = QHBoxLayout()
        self.b_kf_remove = QPushButton("Remove")
        self.b_kf_remove.setToolTip("Remove the keyframe under the playhead")
        self.b_kf_remove.clicked.connect(lambda: self.keyframeRemoved.emit(self._t_clip))
        self.b_kf_clear = QPushButton("Clear All")
        self.b_kf_clear.setToolTip("Remove all keyframes from this clip")
        self.b_kf_clear.clicked.connect(self.keyframesCleared.emit)
        for b in (self.b_kf_remove, self.b_kf_clear):
            kf_btn_row.addWidget(b)
        kflay.addLayout(kf_btn_row)

        self.kf_list = QListWidget()
        self.kf_list.setFixedHeight(90)
        self.kf_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.kf_list.setFocusPolicy(Qt.NoFocus)
        self.kf_list.itemClicked.connect(self._kf_item_clicked)
        kflay.addWidget(self.kf_list)
        lay.addWidget(self.sec_kf)

        lay.addStretch(1)
        self._current_clip = None
        self.set_clip(None, None, 30)

    def _toggle_property_kf(self, attr):
        """The diamond next to a slider works like CapCut's: a keyframe under the playhead is removed, otherwise one is added."""
        self.keyframeToggleRequested.emit(self._t_clip)

    def _seek_keyframe(self, direction):
        """Jump to the previous (-1) / next (+1) keyframe of the selected clip."""
        c = self._current_clip
        if c is None or not c.keyframes:
            return
        kf = c.next_keyframe(self._t_clip) if direction > 0 else c.prev_keyframe(self._t_clip)
        if kf is not None:
            self.keyframeSeek.emit(kf.t)

    def _kf_item_clicked(self, item):
        t = item.data(Qt.UserRole)
        if t is not None:
            self.keyframeSeek.emit(float(t))

    def retint(self):
        for sec in (self.text_box, self.sec_speed, self.sec_trans, self.sec_audio, self.sec_xform, self.sec_visual, self.sec_anim,
                    self.sec_kf):
            sec.retint()
        theme.set_icon(self.b_reset, "reset", 13)
        theme.set_icon(self.b_kf_add, "keyframe", 13)
        self._sync_kf_slider_icons()

    def _sync_kf_slider_icons(self):
        """Highlights keyframe icons on sliders if a keyframe exists at the current clip time."""
        c = self._current_clip
        if not c or not getattr(c, "keyframes", None):
            for attr in self.rows:
                r, _ = self.rows[attr]
                r.set_keyframe_active(False)
            return
        kf = c.keyframe_at(self._t_clip)
        for attr in self.rows:
            r, _ = self.rows[attr]
            if r.has_kf:
                r.set_keyframe_active(bool(kf and attr in kf.values))

    # ── speed / reverse ─────────────────────────────────────────────────────
    SPEED_DECADE = 100        # slider steps per factor of ten: a log scale, so 0.5x and 2x sit the same distance from 1x

    def _build_speed_section(self, lay):
        self.sec_speed = CollapsibleSection("Speed")
        v = self.sec_speed.body_lay

        row = QHBoxLayout()
        row.setSpacing(8)
        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setRange(int(round(math.log10(MIN_SPEED) * self.SPEED_DECADE)),
                                    int(round(math.log10(MAX_SPEED) * self.SPEED_DECADE)))
        self._speed_slider.setToolTip("Playback speed (double-click to reset to 1×)")
        self._speed_slider.mouseDoubleClickEvent = lambda e: self._speed_chosen(1.0)
        self._speed_slider.valueChanged.connect(
            lambda sv: self._speed_chosen(round(10 ** (sv / self.SPEED_DECADE), 2), from_slider=True))
        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(MIN_SPEED, MAX_SPEED)
        self._speed_spin.setDecimals(2)
        self._speed_spin.setSingleStep(0.25)
        self._speed_spin.setSuffix(" ×")
        self._speed_spin.setKeyboardTracking(False)          # apply on Enter / arrows, not on every typed digit
        self._speed_spin.setFixedWidth(84)
        self._speed_spin.valueChanged.connect(lambda x: self._speed_chosen(x, from_spin=True))
        row.addWidget(self._speed_slider, 1)
        row.addWidget(self._speed_spin)
        v.addLayout(row)

        chips = QHBoxLayout()
        chips.setSpacing(4)
        self._speed_chips = {}
        for sp in SPEED_PRESETS:
            b = QToolButton()
            b.setText(f"{sp:g}×")
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(28)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, x=sp: self._speed_chosen(x))
            chips.addWidget(b)
            self._speed_chips[sp] = b
        v.addLayout(chips)

        self._reverse_box = QCheckBox("Play in reverse")
        self._reverse_box.setToolTip("Play this clip backwards, picture and sound (Alt+R)")
        self._reverse_box.toggled.connect(lambda on: None if self._loading else self.reverseEdited.emit(bool(on)))
        v.addWidget(self._reverse_box)

        self._speed_info = QLabel("")
        self._speed_info.setWordWrap(True)
        self._speed_info.setStyleSheet(f"color: {theme.T['muted']}; font-size: 9pt;")
        v.addWidget(self._speed_info)
        lay.addWidget(self.sec_speed)

    def _speed_chosen(self, speed, from_slider=False, from_spin=False):
        """A speed was picked by the slider, the box or a chip: mirror it in the other controls, then announce it."""
        if self._loading:
            return
        speed = min(MAX_SPEED, max(MIN_SPEED, float(speed)))
        self._set_speed_controls(speed, skip_slider=from_slider, skip_spin=from_spin)
        self.speedEdited.emit(speed)

    def _set_speed_controls(self, speed, skip_slider=False, skip_spin=False):
        if not skip_slider:
            self._speed_slider.blockSignals(True)
            self._speed_slider.setValue(int(round(math.log10(speed) * self.SPEED_DECADE)))
            self._speed_slider.blockSignals(False)
        if not skip_spin:
            self._speed_spin.blockSignals(True)
            self._speed_spin.setValue(speed)
            self._speed_spin.blockSignals(False)
        for sp, b in self._speed_chips.items():
            b.setChecked(abs(sp - speed) < 1e-3)

    def load_speed(self, clip):
        """Show the clip's speed / reverse in the Speed section without triggering an edit."""
        self._loading = True
        try:
            sp = clip.speed if clip is not None else 1.0
            self._set_speed_controls(sp)
            self._reverse_box.setChecked(bool(clip is not None and clip.reverse))
        finally:
            self._loading = False
        self.refresh_speed_info(clip)

    def refresh_speed_info(self, clip):
        """Update only the read-out (the controls keep their place while the user is dragging them)."""
        if clip is None:
            self._speed_info.setText("")
            return
        self._speed_info.setText(f"Plays {clip.span:.2f} s of the source in {clip.duration:.2f} s"
                                 + (" · backwards" if clip.reverse else ""))

    # ── transition into the clip ────────────────────────────────────────────
    def _build_transition_section(self, lay):
        self.sec_trans = CollapsibleSection("Transition")
        v = self.sec_trans.body_lay
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setSpacing(5)
        self._trans_btns = {}
        for i, (name, label) in enumerate([("", "None")] + list(TRANSITIONS)):
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(30)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, n=name: self._pick_transition(n))
            grid.addWidget(b, i // 3, i % 3)
            self._trans_btns[name] = b
        for col in range(3):
            grid.setColumnStretch(col, 1)
        v.addWidget(host)
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        self._trans_dur = QSlider(Qt.Horizontal)
        self._trans_dur.setRange(int(MIN_TRANSITION * 10), int(MAX_TRANSITION * 10))     # tenths of a second
        self._trans_dur.setValue(int(DEFAULT_TRANSITION * 10))
        self._trans_dur_val = QLabel("0.5 s")
        self._trans_dur_val.setStyleSheet(f"color: {theme.T['muted']};")
        self._trans_dur_val.setMinimumWidth(36)
        self._trans_dur.valueChanged.connect(self._trans_duration_moved)
        row.addWidget(QLabel("Duration"))
        row.addWidget(self._trans_dur, 1)
        row.addWidget(self._trans_dur_val)
        self._trans_dur_row = QWidget()
        self._trans_dur_row.setLayout(row)
        v.addWidget(self._trans_dur_row)
        self._trans_hint = QLabel("")
        self._trans_hint.setWordWrap(True)
        self._trans_hint.setStyleSheet(f"color: {theme.T['muted']}; font-size: 9pt;")
        v.addWidget(self._trans_hint)
        lay.addWidget(self.sec_trans)

    def _pick_transition(self, name):
        if self._loading:
            return
        self.transitionEdited.emit(name, self._trans_dur.value() / 10.0)
        self.load_transition(self._current_clip, self._trans_has_prev)      # re-sync the chips after a re-click

    def _trans_duration_moved(self, v):
        self._trans_dur_val.setText(f"{v / 10.0:.1f} s")
        if self._loading:
            return
        c = self._current_clip
        name = (c.transition.get("type") if c is not None else None) or ""
        if name:
            self.transitionEdited.emit(name, v / 10.0)

    _trans_has_prev = False

    def load_transition(self, clip, has_prev):
        """Show the clip's transition. ``has_prev``: a clip on the same layer ends where this one starts (so it shows)."""
        self._trans_has_prev = bool(has_prev)
        self._loading = True
        try:
            t = clip.transition if clip is not None else {}
            cur = t.get("type", "")
            for name, b in self._trans_btns.items():
                b.setChecked(name == cur)
            self._trans_dur_row.setEnabled(bool(cur))
            if cur:
                self._trans_dur.setValue(int(round(t.get("dur", DEFAULT_TRANSITION) * 10)))
            self._trans_dur_val.setText(f"{self._trans_dur.value() / 10.0:.1f} s")
        finally:
            self._loading = False
        if has_prev:
            hint = ""
        elif clip is not None and clip.transition:
            hint = ("Not showing yet: another clip on the same layer must end exactly where this one starts. "
                    "Its last frame is then held under the transition.")
        else:
            hint = "Needs another clip on the same layer that ends exactly where this one starts."
        self._trans_hint.setText(hint)

    # ── animation (In / Out / Combo, CapCut style) ──────────────────────────
    ANIM_TABS = (("in", "In"), ("out", "Out"), ("combo", "Combo"))

    def _build_animation_section(self, lay):
        self.sec_anim = CollapsibleSection("Animation")
        v = self.sec_anim.body_lay
        self._anim_slot = "in"
        self._anim_btns = {}                  # preset name ("" = None) -> button of the current tab

        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self._anim_tabs = {}
        for slot, label in self.ANIM_TABS:
            b = QToolButton()
            b.setObjectName("catTab")
            b.setText(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, sl=slot: self._set_anim_tab(sl))
            tabs.addWidget(b)
            self._anim_tabs[slot] = b
        v.addLayout(tabs)

        self._anim_grid_host = QWidget()
        self._anim_grid = QGridLayout(self._anim_grid_host)
        self._anim_grid.setContentsMargins(0, 4, 0, 0)
        self._anim_grid.setSpacing(5)
        v.addWidget(self._anim_grid_host)

        row = QHBoxLayout()
        self._anim_dur_label = QLabel("Duration")
        self._anim_dur = QSlider(Qt.Horizontal)
        self._anim_dur.setRange(int(anim.MIN_DURATION * 10), 40)          # tenths of a second: 0.1 - 4.0 s
        self._anim_dur.setValue(int(anim.DEFAULT_DURATION * 10))
        self._anim_dur_val = QLabel("0.5 s")
        self._anim_dur_val.setStyleSheet(f"color: {theme.T['muted']};")
        self._anim_dur_val.setMinimumWidth(36)
        self._anim_dur.valueChanged.connect(self._anim_duration_moved)
        row.addWidget(self._anim_dur_label)
        row.addWidget(self._anim_dur, 1)
        row.addWidget(self._anim_dur_val)
        self._anim_dur_row = QWidget()
        self._anim_dur_row.setLayout(row)
        row.setContentsMargins(0, 2, 0, 0)
        v.addWidget(self._anim_dur_row)

        self._anim_hint = QLabel("")
        self._anim_hint.setWordWrap(True)
        self._anim_hint.setStyleSheet(f"color: {theme.T['muted']}; font-size: 9pt;")
        v.addWidget(self._anim_hint)
        lay.addWidget(self.sec_anim)
        self._rebuild_anim_presets()

    def _set_anim_tab(self, slot):
        self._anim_slot = slot
        self._rebuild_anim_presets()
        self._load_animation(self._current_clip)

    def _rebuild_anim_presets(self):
        """(Re)create the preset buttons of the current tab: a "None" chip followed by every preset."""
        while self._anim_grid.count():
            w = self._anim_grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._anim_btns = {}
        entries = [("", "None")] + anim.presets(self._anim_slot)
        for i, (name, label) in enumerate(entries):
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(30)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, n=name: self._pick_animation(n))
            self._anim_grid.addWidget(b, i // 3, i % 3)
            self._anim_btns[name] = b
        for col in range(3):
            self._anim_grid.setColumnStretch(col, 1)

    def _anim_seconds(self):
        return self._anim_dur.value() / 10.0

    def _pick_animation(self, name):
        if self._loading:
            return
        self.animationEdited.emit(self._anim_slot, name, self._anim_seconds())
        self._load_animation(self._current_clip)         # re-sync the chips (a re-click must not leave one unchecked)

    def _anim_duration_moved(self, v):
        self._anim_dur_val.setText(f"{v / 10.0:.1f} s")
        if self._loading:
            return
        c = self._current_clip
        name = (c.animation.get(self._anim_slot) if c is not None else None) or ""
        if name:
            self.animationEdited.emit(self._anim_slot, name, v / 10.0)

    def _load_animation(self, clip):
        """Show the clip's animation in the tabs / chips / duration slider without triggering edits."""
        self._loading = True
        try:
            a = clip.animation if clip is not None else {}
            for slot, b in self._anim_tabs.items():
                b.setChecked(slot == self._anim_slot)
                active = bool(a.get(slot))
                b.setText(dict(self.ANIM_TABS)[slot] + (" •" if active else ""))
            cur = a.get(self._anim_slot) or ""
            for name, b in self._anim_btns.items():
                b.setChecked(name == cur)
            has_dur = self._anim_slot != "combo"
            self._anim_dur_row.setVisible(has_dur)
            self._anim_dur_row.setEnabled(bool(cur))
            if has_dur:
                d = a.get(self._anim_slot + "_dur", anim.DEFAULT_DURATION)
                self._anim_dur.setValue(int(round(float(d) * 10)))
                self._anim_dur_val.setText(f"{self._anim_dur.value() / 10.0:.1f} s")
            self._anim_hint.setText(
                "Combo animations loop for the whole clip and replace In / Out." if self._anim_slot == "combo"
                else "Picking an In or Out animation replaces any Combo." if a.get("combo") else "")
        finally:
            self._loading = False

    # ── title (text) controls ────────────────────────────────────────────────
    def _emit_title(self, key, value):
        if not self._loading:
            self.titleEdited.emit(key, value)

    def _build_text_section(self, lay):
        self.text_box = CollapsibleSection("Text")
        v = self.text_box.body_lay

        self.t_text = QPlainTextEdit()
        self.t_text.setPlaceholderText("Type your title…")
        self.t_text.setFixedHeight(78)
        self.t_text.textChanged.connect(lambda: self._emit_title("text", self.t_text.toPlainText()))
        v.addWidget(self.t_text)

        self.t_font = QFontComboBox()
        self.t_font.currentFontChanged.connect(lambda f: self._emit_title("font", f.family()))
        v.addWidget(self.t_font)

        row = QHBoxLayout()
        self.t_size = QSpinBox(); self.t_size.setRange(8, 600); self.t_size.setSuffix(" px")
        self.t_size.valueChanged.connect(lambda n: self._emit_title("size", n))
        self.t_bold = QToolButton(); self.t_bold.setCheckable(True); self.t_bold.setToolTip("Bold")
        theme.set_icon(self.t_bold, "bold", 13)
        self.t_bold.toggled.connect(lambda on: self._emit_title("bold", on))
        self.t_italic = QToolButton(); self.t_italic.setCheckable(True); self.t_italic.setToolTip("Italic")
        theme.set_icon(self.t_italic, "italic", 13)
        self.t_italic.toggled.connect(lambda on: self._emit_title("italic", on))
        self.t_align = QComboBox()
        for label, key in (("Left", "left"), ("Center", "center"), ("Right", "right")):
            self.t_align.addItem(label, key)
        self.t_align.currentIndexChanged.connect(lambda _: self._emit_title("align", self.t_align.currentData()))
        for w in (self.t_size, self.t_bold, self.t_italic, self.t_align):
            row.addWidget(w)
        v.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Colour"))
        self.t_color = _ColorButton("#FFFFFF", "Text colour")
        self.t_color.colorChanged.connect(lambda c: self._emit_title("color", c))
        row.addWidget(self.t_color)
        row.addSpacing(10)
        self.t_stroke = QCheckBox("Outline")
        self.t_stroke.toggled.connect(lambda on: self._emit_title("stroke", on))
        row.addWidget(self.t_stroke)
        row.addStretch(1)
        v.addLayout(row)

        row = QHBoxLayout()
        self.t_box = QCheckBox("Background box")
        self.t_box.toggled.connect(lambda on: self._emit_title("box", on))
        self.t_box_color = _ColorButton("#000000", "Background colour")
        self.t_box_color.colorChanged.connect(lambda c: self._emit_title("box_color", c))
        row.addWidget(self.t_box)
        row.addWidget(self.t_box_color)
        row.addStretch(1)
        v.addLayout(row)

        self.t_box_opacity = _SliderRow("Box opacity", 0, 100, 55)
        self.t_box_opacity.changed.connect(lambda n: self._emit_title("box_opacity", n / 100.0))
        self.t_x = _SliderRow("Position X", 0, 100, 50)
        self.t_x.changed.connect(lambda n: self._emit_title("pos_x", n / 100.0))
        self.t_y = _SliderRow("Position Y", 0, 100, 50)
        self.t_y.changed.connect(lambda n: self._emit_title("pos_y", n / 100.0))
        for w in (self.t_box_opacity, self.t_x, self.t_y):
            v.addWidget(w)
        lay.addWidget(self.text_box)

    def _load_title(self, t):
        """Show a title's style in the controls without triggering edits."""
        self._loading = True
        try:
            if self.t_text.toPlainText() != t["text"]:        # don't reset the caret while typing
                self.t_text.setPlainText(t["text"])
            self.t_font.setCurrentFont(QFont(t["font"]) if t["font"] else QFont())
            self.t_size.setValue(int(t["size"]))
            self.t_bold.setChecked(bool(t["bold"]))
            self.t_italic.setChecked(bool(t["italic"]))
            self.t_align.setCurrentIndex(max(0, self.t_align.findData(t["align"])))
            self.t_color.set_color(t["color"])
            self.t_stroke.setChecked(bool(t["stroke"]))
            self.t_box.setChecked(bool(t["box"]))
            self.t_box_color.set_color(t["box_color"])
            self.t_box_opacity.set_value(t["box_opacity"] * 100)
            self.t_x.set_value(t["pos_x"] * 100)
            self.t_y.set_value(t["pos_y"] * 100)
        finally:
            self._loading = False

    # ── selection ────────────────────────────────────────────────────────────
    def set_clip(self, clip, media, fps):
        self._current_clip = clip
        on = clip is not None
        is_title = on and clip.title is not None
        self.text_box.setVisible(is_title)
        speed_on = on and not is_title and media is not None and media.kind in ("video", "audio")
        self.sec_speed.setVisible(speed_on)              # photos and titles have no source to speed up or reverse
        if speed_on:
            self.load_speed(clip)
        self.sec_trans.setVisible(on and not is_title and media is not None and media.kind in ("video", "image"))
        self.sec_audio.setVisible(not is_title)
        self.sec_xform.setVisible(on and not is_title and media is not None and media.kind in ("video", "image"))
        # Keyframe section: only for visual, non-title clips
        kf_on = on and not is_title and media is not None and media.kind in ("video", "image")
        self.sec_kf.setVisible(kf_on)
        self.sec_anim.setVisible(on and (is_title or (media is not None and media.kind in ("video", "image"))))
        self._load_animation(clip)
        for attr, (r, scale) in self.rows.items():
            r.setEnabled(on)
            r.setVisible(not is_title or attr == "opacity")     # titles only have opacity (their X / Y are in Text)
            if on:
                shown = clip.props_at(self._t_clip).get(attr) if clip.keyframes and attr in KEYFRAMED_PROPS else None
                r.set_value((getattr(clip, attr) if shown is None else shown) * scale)
        self.b_reset.setEnabled(on)
        sounds = on and not is_title and media is not None and (media.kind == "audio" or media.has_audio)
        for r in self.fade_rows.values():
            r.setVisible(not is_title)
            r.setEnabled(bool(sounds))
        if sounds:
            self.refresh_fades(clip)
        if not on:
            self.title.setText("No clip selected")
            self.info.setText("Click a clip in the timeline to edit its properties.")
            self.kf_list.clear()
            self._sync_kf_slider_icons()
            return
        if is_title:
            self._load_title(clip.title)
            self.title.setText("Title")
            self.info.setText(f"Start  {format_tc(clip.start, fps)}\nLength  {format_tc(clip.duration, fps)}")
            self._sync_kf_slider_icons()
            return
        self.title.setText(media.name)
        self.info.setText(f"Start  {format_tc(clip.start, fps)}\nLength  {format_tc(clip.duration, fps)}\n"
                          f"Source in  {format_tc(clip.in_point, fps)}")
        has_video = media.kind in ("video", "image")
        for attr in ("opacity", "brightness", "contrast", "saturation") + self.XFORM:
            self.rows[attr][0].setEnabled(has_video)
        self.rows["volume"][0].setEnabled(media.kind == "audio" or media.has_audio)
        if kf_on:
            self.set_keyframes(clip.keyframes)
        self._sync_kf_slider_icons()

    def refresh_fades(self, clip):
        """Show the clip's fades (and what is possible for its length) without emitting any edit."""
        tenths = max(1, int(clip.duration * 10))
        for which, r in self.fade_rows.items():
            r.set_maximum(tenths)
            r.set_value((clip.fade_in if which == "in" else clip.fade_out) * 10)

    def set_keyframes(self, keyframes):
        """Refresh the keyframe list widget."""
        self.kf_list.clear()
        if not keyframes:
            self.kf_list.addItem("(no keyframes)")
            self._sync_kf_slider_icons()
            return
        for i, kf in enumerate(keyframes):
            parts = ", ".join(f"{k}: {v:.3f}" for k, v in sorted(kf.values.items()))
            self.kf_list.addItem(f"{kf.t:.2f}s — {parts}")
            self.kf_list.item(i).setData(Qt.UserRole, kf.t)
        self._sync_kf_slider_icons()

    def set_playhead_clip_time(self, t_clip):
        """Called by the main window to update which clip-local time the panel refers to."""
        self._t_clip = max(0.0, t_clip)
        self._sync_kf_slider_icons()
        # CapCut style: update the slider positions to reflect animated interpolated values as playhead moves
        c = self._current_clip
        if c and getattr(c, "keyframes", None) and not self._loading:
            props = c.props_at(self._t_clip)
            for attr in KEYFRAMED_PROPS:
                if attr in self.rows and attr in props:
                    r, scale = self.rows[attr]
                    r.set_value(props[attr] * scale)


class TextPanel(QWidget):
    """The Text tab: click a style to add it at the playhead."""
    addRequested = pyqtSignal(str)        # preset name understood by MainWindow.add_title

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        note = QLabel("Click a style to add it at the playhead.")
        note.setObjectName("muted")
        lay.addWidget(note)
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, (label, preset) in enumerate((("Title", "Title"), ("Lower third", "Lower third"),
                                             ("Subtitle", "Subtitle"))):
            b = QPushButton(label)
            b.setObjectName("card")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=preset: self.addRequested.emit(p))
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        lay.addStretch(1)


# ── Dialogs ──────────────────────────────────────────────────────────────────
class ProjectDialog(QDialog):
    def __init__(self, parent=None, width=1920, height=1080, fps=30, title="New Project",
                 bg_mode="color", bg_color="#000000"):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.preset = QComboBox()
        for label, w, h in PRESETS:
            self.preset.addItem(label, (w, h))
        self.preset.addItem("Custom", None)
        self.w = QSpinBox(); self.w.setRange(64, 7680); self.w.setValue(width)
        self.h = QSpinBox(); self.h.setRange(64, 7680); self.h.setValue(height)
        self.fps = QComboBox()
        for f in (24, 25, 30, 50, 60):
            self.fps.addItem(f"{f} fps", f)
        self.fps.setCurrentIndex(max(0, self.fps.findData(fps)))
        idx = next((i for i, (_, pw, ph) in enumerate(PRESETS) if (pw, ph) == (width, height)), len(PRESETS))
        self.preset.setCurrentIndex(idx)
        self.preset.currentIndexChanged.connect(self._preset)
        form.addRow("Preset", self.preset)
        self.aspect = QComboBox()                       # canvas aspect switch: keeps the short side
        self.aspect.addItem("Keep current", None)
        for label, aw, ah in ASPECTS:
            self.aspect.addItem(label, (aw, ah))
        self.aspect.currentIndexChanged.connect(self._aspect)
        form.addRow("Aspect ratio", self.aspect)
        row = QHBoxLayout(); row.addWidget(self.w); row.addWidget(QLabel("×")); row.addWidget(self.h)
        form.addRow("Size", row)
        form.addRow("Frame rate", self.fps)
        self.bg_mode = QComboBox()
        self.bg_mode.addItem("Color fill", "color")
        self.bg_mode.addItem("Blurred picture", "blur")
        self.bg_mode.setCurrentIndex(max(0, self.bg_mode.findData(bg_mode)))
        self._bg_color = normalize_color(bg_color)
        self.bg_btn = QPushButton()
        self.bg_btn.setCursor(Qt.PointingHandCursor)
        self.bg_btn.clicked.connect(self._pick_color)
        self._paint_swatch()
        brow = QHBoxLayout(); brow.addWidget(self.bg_mode, 1); brow.addWidget(self.bg_btn)
        form.addRow("Background", brow)
        self.bg_mode.currentIndexChanged.connect(self._sync_bg)
        self._sync_bg()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)
        theme.tidy(self)

    def _preset(self, i):
        d = self.preset.itemData(i)
        if d:
            self.w.setValue(d[0]); self.h.setValue(d[1])

    def _aspect(self, i):
        d = self.aspect.itemData(i)
        if d:
            w, h = canvas_size(self.w.value(), self.h.value(), *d)
            self.w.setValue(w); self.h.setValue(h)

    def _paint_swatch(self):
        self.bg_btn.setText(self._bg_color.upper())
        self.bg_btn.setStyleSheet(f"QPushButton {{ background: {self._bg_color}; "
                                  f"color: {'#000' if QColor(self._bg_color).lightness() > 128 else '#fff'}; "
                                  f"border: 1px solid #888; padding: 3px 10px; }}")

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._bg_color), self, "Canvas color")
        if c.isValid():
            self._bg_color = normalize_color(c.name())
            self._paint_swatch()

    def _sync_bg(self):
        self.bg_btn.setEnabled(self.bg_mode.currentData() == "color")
        self.bg_btn.setToolTip("Color shown around pictures that don't fill the canvas")

    def values(self):
        return self.w.value(), self.h.value(), self.fps.currentData()

    def background(self):
        return self.bg_mode.currentData(), self._bg_color


class ExportDialog(QDialog):
    def __init__(self, project, default_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Video")
        form = QFormLayout(self)
        self.project = project
        self.preset = QComboBox()
        for label, w, h in EXPORT_PRESETS:
            self.preset.addItem(label, (w, h) if w else None)
        self.fmt = QComboBox()
        for k in ft.FORMATS:
            self.fmt.addItem(k)
        self.quality = QComboBox()
        for k in ft.QUALITY:
            self.quality.addItem(k)
        self.quality.setCurrentText("Medium")
        self.path = QLineEdit(default_name)
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.path, 1); row.addWidget(b)
        self.gpu_enc = ft.gpu_encoder()
        self.gpu = QCheckBox(f"Use GPU encoding ({ft.gpu_label(self.gpu_enc)})" if self.gpu_enc
                             else "GPU encoding (no supported GPU found)")
        self.gpu.setEnabled(bool(self.gpu_enc))
        self.gpu.setChecked(bool(self.gpu_enc))
        self.gpu.setToolTip("Much faster export on MP4. If the GPU encoder fails, the export is retried on the CPU.")
        form.addRow("Social preset", self.preset)
        form.addRow("Format", self.fmt)
        form.addRow("Quality", self.quality)
        form.addRow("", self.gpu)
        form.addRow("Save to", row)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet(f"color: {theme.T['muted']};")
        form.addRow("Output", self.info)
        self.preset.currentIndexChanged.connect(self._update_info)
        self._update_info()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.fmt.currentTextChanged.connect(self._fix_ext)
        theme.tidy(self)
        self.setMinimumWidth(460)

    def _update_info(self):
        p = self.project
        w, h = self.size() or (p.width, p.height)
        txt = f"{w} × {h}  ·  {p.fps} fps  ·  {format_tc(p.duration(), p.fps)}"
        if (w, h) != (p.width, p.height) and abs(w / h - p.width / p.height) > 0.01:
            fill = "a blurred copy of the picture" if p.bg_mode == "blur" else "the canvas color"
            txt += f"\nDifferent shape from the project: pictures are fitted inside the frame, with {fill} around them."
        self.info.setText(txt)

    def size(self):
        """(width, height) the export is rendered at, or None for the project's own size."""
        return self.preset.currentData()

    def _fix_ext(self):
        base, _ = os.path.splitext(self.path.text())
        self.path.setText(base + ft.FORMATS[self.fmt.currentText()]["ext"])
        self.gpu.setEnabled(bool(self.gpu_enc) and ft.FORMATS[self.fmt.currentText()]["ext"] == ".mp4")

    def _browse(self):
        ext = ft.FORMATS[self.fmt.currentText()]["ext"]
        p, _ = QFileDialog.getSaveFileName(self, "Export to", self.path.text(), f"Video (*{ext})")
        if p:
            self.path.setText(p if p.lower().endswith(ext) else p + ext)

    def values(self):
        return self.path.text().strip(), self.fmt.currentText(), self.quality.currentText()

    def gpu_encoder(self):
        """The hardware encoder to use, or None (unchecked, unsupported or a format that can't use it)."""
        return self.gpu_enc if self.gpu.isEnabled() and self.gpu.isChecked() else None
