"""Venma Video main window, CapCut-style: one slim top bar, a Media/Text panel, the player and a Details
panel across the top, and the timeline (with its own toolbar) underneath. Fixed layout, no docking."""
import os
import shutil
import tempfile

from PyQt5.QtCore import Qt, QSize, QTimer, QSettings, QStandardPaths
from PyQt5.QtGui import QKeySequence, QPixmap, QColor
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QAction, QActionGroup,
    QSplitter, QFileDialog, QMessageBox, QProgressDialog, QSizePolicy, QStackedWidget, QButtonGroup,
    QApplication, QToolButton, QMenu, QFrame, QDialog, QColorDialog,
)

from . import theme
from ..core import ffmpeg_tools as ft
from ..core import animation as anim_core
from ..core.project import TRANSITIONS
from ..core import voiceover as vo_core
from ..core.project import ASPECTS, Project, MediaItem, CLIP_DEFAULTS, TITLE_PRESETS, KEYFRAMED_PROPS, SPEED_PRESETS, MIN_CLIP
from .timeline import TimelineWidget, format_tc
from .preview import PreviewView, PreviewEngine, HAS_MULTIMEDIA
from .titles import render_project_titles
from .panels import fit_tile, MediaBin, TextPanel, PropertiesPanel, ProjectDialog, ExportDialog
from .voiceover import HAS_INPUT, VoiceRecorder, VoiceoverDialog
from .docks import vsep
from ..workers import MediaWorker, ExportWorker, WaveformWorker

MAX_HISTORY = 100
PROJECT_EXT = ".vvproj"
LAYOUT_KEY = "layout2"          # splitter sizes; new key so layouts saved by older versions are ignored


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.resize(1480, 900)
        self.project = Project(typed=True)
        self.path = None
        self.dirty = False
        self.thumbs = {}                 # media id -> QPixmap
        self.waves = {}                  # media path -> core.waveform.Waveform (None: no sound to draw)
        self._wave_queue = []            # paths still to decode, one at a time
        self._wave_worker = None
        self.history, self.labels, self.index = [], [], -1
        self._workers = []
        self._first_import = True
        self._live_timer = QTimer(self, singleShot=True, interval=35)
        self._live_timer.timeout.connect(lambda: self.engine.refresh())
        self._edit_timer = QTimer(self, singleShot=True, interval=500)
        self._edit_label = "Edit Clip"                           # undo label the pending edit will get (e.g. "Change Speed")
        self._edit_timer.timeout.connect(lambda: self.commit(self._edit_label))
        self._anim_stop = QTimer(self, singleShot=True)           # ends the short preview played after picking an animation
        self._anim_stop.timeout.connect(self._stop_animation_preview)

        self.view = PreviewView()
        self.engine = PreviewEngine(self.view, self)
        self.engine.thumbs = self.thumbs
        QApplication.instance().aboutToQuit.connect(self.engine.shutdown)
        self.timeline = TimelineWidget()
        self.media_bin = MediaBin()
        self.props = PropertiesPanel()
        self.text_panel = TextPanel()

        self._build_actions()
        self._build_workspace()
        self._wire()
        self.statusBar().setSizeGripEnabled(False)
        self._restore_layout()

        self._reset_history("New Project")
        self._refresh_all()
        self._env_timer = QTimer(self, singleShot=True, interval=300)   # parented: dies with the window instead of firing late
        self._env_timer.timeout.connect(self._check_environment)
        self._env_timer.start()

    # ── actions ──────────────────────────────────────────────────────────────
    def _act(self, text, slot, shortcut=None, icon=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if icon:
            theme.set_icon(a, icon, 20)
        a.setCheckable(checkable)
        a.triggered.connect(lambda _=False: slot())
        a.setToolTip(text.replace("&", "").replace("…", "") + (f"  ({shortcut})" if shortcut else ""))
        self.addAction(a)                                # window-level, so every shortcut works without a menu bar
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("&New Project…", self.new_project, "Ctrl+N", "new")
        self.a_open = A("&Open Project…", self.open_project, "Ctrl+O", "open")
        self.a_save = A("&Save", self.save_project, "Ctrl+S", "save")
        self.a_saveas = A("Save &As…", lambda: self.save_project(True), "Ctrl+Shift+S", "saveas")
        self.a_import = A("&Import Media…", self.import_dialog, "Ctrl+I", "import")
        self.a_title = A("Add &Title", lambda: self.add_title("Title"), "Ctrl+T", "title")
        self.a_lower = A("Add &Lower Third", lambda: self.add_title("Lower third"), "Ctrl+Shift+T", "lower_third")
        self.a_subtitle = A("Add &Subtitle", lambda: self.add_title("Subtitle"), "Ctrl+Alt+T", "subtitle")
        self.a_layer = A("Add Video &Layer", self.add_video_layer, "Ctrl+Shift+L", "layer_plus")
        self.a_atrack = A("Add &Audio Track", self.add_audio_track, "Ctrl+Shift+A", "audio_plus")
        self.a_record = A("Record &Voiceover…", self.record_voiceover, "Ctrl+R", "mic")
        self.a_prune = A("&Remove Empty Layers", self.remove_empty_layers, None, "prune")
        self.a_export = A("&Export Video…", self.export_video, "Ctrl+M", "export")
        self.a_settings = A("Project &Settings…", self.project_settings, "Ctrl+Alt+P", "settings")
        self.a_aspects = [(A(label, lambda aw=aw, ah=ah: self.switch_canvas(aw, ah)), aw, ah) for label, aw, ah in ASPECTS]
        self.a_bg_color = A("Background: &Color Fill", lambda: self.set_canvas_background("color"))
        self.a_bg_blur = A("Background: &Blurred Picture", lambda: self.set_canvas_background("blur"))
        self.a_bg_pick = A("Background Color…", self.pick_canvas_color)
        self.a_quit = A("&Quit", self.close, "Ctrl+Q", "quit")
        self.a_undo = A("&Undo", self.undo, "Ctrl+Z", "undo")
        self.a_redo = A("&Redo", self.redo, "Ctrl+Y", "redo")
        self.a_split = A("&Split at Playhead", self.split_at_playhead, "S", "split")
        self.a_split2 = A("Split at Playhead (alt)", self.split_at_playhead, "Ctrl+K")
        self.a_delete = A("&Delete Clip", lambda: self.delete_selected(False), "Delete", "trash")
        self.a_ripple = A("&Ripple Delete", lambda: self.delete_selected(True), "Shift+Delete", "ripple_delete")
        self.a_reverse = A("&Reverse Clip", self.reverse_selected, "Alt+R")
        self.a_speed = {sp: A(f"{sp:g}×" + ("  (normal)" if sp == 1.0 else ""), lambda sp=sp: self._apply_speed(sp, live=False))
                        for sp in SPEED_PRESETS}
        self.a_keyframe = A("Add / Remove &Keyframe", self.add_keyframe_at_playhead, "Alt+K", "keyframe")
        self.a_kf_prev = A("Previous Keyframe", lambda: self._goto_keyframe(-1), "Alt+Left")
        self.a_kf_next = A("Next Keyframe", lambda: self._goto_keyframe(1), "Alt+Right")
        self.a_play = A("&Play / Pause", self.engine.toggle, "Space", "play")
        self.a_start = A("Go to &Start", lambda: self.engine.seek(0), "Home", "skip_back")
        self.a_end = A("Go to &End", lambda: self.engine.seek(self.project.duration()), "End", "skip_fwd")
        self.a_prev = A("Previous Frame", lambda: self.step(-1), "Left", "step_back")
        self.a_next = A("Next Frame", lambda: self.step(1), "Right", "step_fwd")
        self.a_zin = A("Zoom In", lambda: self.timeline.canvas.zoom_by(1.25), "=", "zoom_in")
        self.a_zout = A("Zoom Out", lambda: self.timeline.canvas.zoom_by(0.8), "-", "zoom_out")
        self.a_fit = A("Fit Timeline", self.timeline.canvas.fit, "\\", "fit")
        self.a_snap = A("Snapping", self._toggle_snap, "N", "magnet", checkable=True)
        self.a_snap.setChecked(True)
        self.a_theme = A("Toggle Dark / Light", self.toggle_theme, "Ctrl+Shift+D", "sun" if theme.NAME == "dark" else "moon")
        # editing tools (exclusive): shown as a segmented group in the timeline toolbar
        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for tid, title, key, icon in (("select", "Select", "V", "move"),
                                      ("ripple", "Ripple edit: trim an edge and later clips follow", "B", "ripple"),
                                      ("slip", "Slip: change which part of the source plays", "Y", "slip"),
                                      ("razor", "Razor: click a clip to cut it", "C", "razor")):
            a = QAction(self)
            a.setCheckable(True)
            theme.set_icon(a, icon, 20)
            a.setToolTip(f"{title}  ({key})")
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(lambda _=False, t=tid: self.select_tool(t))
            self.tool_group.addAction(a)
            self.addAction(a)
            self.tool_actions[tid] = a
        self.tool_actions["select"].setChecked(True)

    TOOL_HINTS = {
        "select": "Select: drag clips to move, drag clip edges to trim.",
        "ripple": "Ripple edit: drag a clip edge; later clips follow so no gap is left.",
        "slip": "Slip: drag inside a clip to change which part of the source plays.",
        "razor": "Razor: click a clip to cut it (S cuts at the playhead).",
    }

    # ── layout ───────────────────────────────────────────────────────────────
    def _build_workspace(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())

        self.hsplit = QSplitter(Qt.Horizontal)
        self.hsplit.setChildrenCollapsible(False)
        self.hsplit.addWidget(self._build_left_panel())
        self.hsplit.addWidget(self._build_player_panel())
        self.hsplit.addWidget(self._build_details_panel())
        self.hsplit.setStretchFactor(0, 0)
        self.hsplit.setStretchFactor(1, 1)
        self.hsplit.setStretchFactor(2, 0)

        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.addWidget(self.hsplit)
        self.vsplit.addWidget(self._build_timeline_panel())
        self.vsplit.setStretchFactor(0, 1)
        self.vsplit.setStretchFactor(1, 0)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(6, 6, 6, 4)
        bl.addWidget(self.vsplit)
        root.addWidget(body, 1)
        self.setCentralWidget(central)

    @staticmethod
    def _frame(head_widget, content, min_w=0):
        """A bordered panel: a 38px header row on top of ``content``."""
        f = QFrame()
        f.setObjectName("panelFrame")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QFrame()
        head.setObjectName("panelHead")
        head.setFixedHeight(38)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 0, 12, 0)
        hl.setSpacing(6)
        head_widget(hl)
        lay.addWidget(head)
        lay.addWidget(content, 1)
        if min_w:
            f.setMinimumWidth(min_w)
        return f

    def _icon_button(self, action, name="tlBtn", size=16):
        b = QToolButton()
        b.setObjectName(name)
        b.setDefaultAction(action)
        b.setIconSize(QSize(size, size))
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _build_top_bar(self):
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(48)
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 0, 12, 0)
        row.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(theme.app_pixmap(22))
        brand = QLabel("Venma Video")
        brand.setObjectName("brandName")
        row.addWidget(logo)
        row.addWidget(brand)
        row.addSpacing(10)

        self.b_menu = QToolButton()                       # File / Edit / Insert live here (no menu bar)
        self.b_menu.setObjectName("menuBtn")
        self.b_menu.setText("Menu")
        self.b_menu.setPopupMode(QToolButton.InstantPopup)
        self.b_menu.setCursor(Qt.PointingHandCursor)
        menu = QMenu(self.b_menu)
        for title, items in (
                ("File", (self.a_new, self.a_open, self.a_save, self.a_saveas, None, self.a_import, self.a_settings,
                          None, self.a_quit)),
                ("Edit", (self.a_undo, self.a_redo, None, self.a_split, self.a_delete, self.a_ripple, None, self.a_keyframe, self.a_kf_prev, self.a_kf_next)),
                ("Insert", (self.a_title, self.a_lower, self.a_subtitle, None, self.a_record, None, self.a_layer,
                            self.a_atrack, self.a_prune))):
            sub = menu.addMenu(title)
            for a in items:
                sub.addSeparator() if a is None else sub.addAction(a)
        canvas_menu = menu.addMenu("Canvas")
        for a, _, _ in self.a_aspects:
            canvas_menu.addAction(a)
        canvas_menu.addSeparator()
        for a in (self.a_bg_color, self.a_bg_blur, self.a_bg_pick):
            canvas_menu.addAction(a)
        clip_menu = menu.addMenu("Clip")
        clip_menu.addAction(self.a_reverse)
        speed_menu = clip_menu.addMenu("Speed")
        for a in self.a_speed.values():
            speed_menu.addAction(a)
        self.b_menu.setMenu(menu)
        row.addWidget(self.b_menu)

        row.addStretch(1)
        self.project_label = QLabel("Untitled")
        self.project_label.setObjectName("projectName")
        row.addWidget(self.project_label)
        row.addStretch(1)

        self.b_theme = self._icon_button(self.a_theme, "tlBtn", 18)
        row.addWidget(self.b_theme)
        self.b_export = QPushButton(" Export")
        self.b_export.setObjectName("primary")
        self.b_export.setToolTip("Export video (Ctrl+M)")
        self.b_export.setCursor(Qt.PointingHandCursor)
        theme.set_icon(self.b_export, "export", 14, "on_accent")
        self.b_export.clicked.connect(self.export_video)
        row.addWidget(self.b_export)
        return bar

    def _build_left_panel(self):
        """Media | Text category tabs (icon over label) switching the panel body."""
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self.media_bin)
        self.left_stack.addWidget(self.text_panel)
        self.cat_group = QButtonGroup(self)
        self.cat_group.setExclusive(True)

        def head(hl):
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(0)
            for i, (label, icon) in enumerate((("Media", "panel_media"), ("Text", "text"))):
                b = QToolButton()
                b.setObjectName("catTab")
                b.setText(label)
                b.setCheckable(True)
                b.setChecked(i == 0)
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                b.setIconSize(QSize(16, 16))
                b.setCursor(Qt.PointingHandCursor)
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                theme.set_icon(b, icon, 16)
                self.cat_group.addButton(b, i)
                hl.addWidget(b)
        self.cat_group.buttonClicked[int].connect(self.left_stack.setCurrentIndex)
        f = self._frame(head, self.left_stack, min_w=260)
        f.findChild(QFrame, "panelHead").setFixedHeight(54)
        return f

    def _set_left_tab(self, i):
        self.cat_group.button(i).setChecked(True)
        self.left_stack.setCurrentIndex(i)

    def _build_player_panel(self):
        top = QWidget()
        top.setObjectName("monitor")
        lay = QVBoxLayout(top)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.view, 1)
        bar = QFrame()
        bar.setObjectName("transport")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(4)
        self.tc_label = QLabel("00:00:00:00")
        self.tc_label.setObjectName("tcMain")
        self.dur_label = QLabel("/ 00:00:00:00")
        self.dur_label.setObjectName("tcDur")
        row.addWidget(self.tc_label)
        row.addSpacing(6)
        row.addWidget(self.dur_label)
        row.addStretch(1)
        row.addWidget(self._transport_button(self.a_prev))
        self.play_btn = QToolButton()                     # round white button, dark glyph
        self.play_btn.setObjectName("playBtn")
        self.play_btn.setIconSize(QSize(16, 16))
        self.play_btn.setFixedSize(36, 36)
        self.play_btn.setToolTip("Play / Pause (Space)")
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.clicked.connect(lambda _=False: self.engine.toggle())
        theme.set_icon(self.play_btn, "play", 16, "on_accent")
        row.addSpacing(4)
        row.addWidget(self.play_btn)
        row.addSpacing(4)
        row.addWidget(self._transport_button(self.a_next))
        row.addStretch(1)
        spacer = QWidget()                                # balances the timecode so the play button is centred
        spacer.setFixedWidth(self.tc_label.sizeHint().width() + self.dur_label.sizeHint().width() + 6)
        row.addWidget(spacer)
        lay.addWidget(bar)
        top.setMinimumWidth(360)

        self.fmt_label = QLabel("")
        self.fmt_label.setObjectName("fmtLabel")

        def head(hl):
            t = QLabel("Player")
            t.setObjectName("panelTitle")
            hl.addWidget(t)
            hl.addStretch(1)
            hl.addWidget(self.fmt_label)
        return self._frame(head, top)

    def _transport_button(self, action):
        b = QToolButton()
        b.setObjectName("transportBtn")
        b.setDefaultAction(action)
        b.setIconSize(QSize(18, 18))
        return b

    def _build_details_panel(self):
        def head(hl):
            t = QLabel("Details")
            t.setObjectName("panelTitle")
            hl.addWidget(t)
        return self._frame(head, self.props, min_w=290)

    def _build_timeline_panel(self):
        """Toolbar (undo / tools / split / layers / snap / zoom) above the timeline, like CapCut."""
        wrap = QFrame()
        wrap.setObjectName("panelFrame")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("tlBar")
        bar.setFixedHeight(40)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(2)
        for a in (self.a_undo, self.a_redo):
            row.addWidget(self._icon_button(a))
        row.addSpacing(6); row.addWidget(vsep()); row.addSpacing(6)
        for tid in ("select", "ripple", "slip", "razor"):
            row.addWidget(self._icon_button(self.tool_actions[tid]))
        row.addSpacing(6); row.addWidget(vsep()); row.addSpacing(6)
        for a in (self.a_split, self.a_delete, self.a_keyframe):
            row.addWidget(self._icon_button(a))
        row.addStretch(1)
        for text, icon, slot, tip in (
                ("Record", "mic", self.record_voiceover, "Record a voiceover from the microphone onto an audio track (Ctrl+R)"),
                ("Layer", "layer_plus", self.add_video_layer, "Add a video layer (Ctrl+Shift+L)"),
                ("Audio", "audio_plus", self.add_audio_track, "Add an audio track (Ctrl+Shift+A)")):
            b = QToolButton()
            b.setObjectName("tlText")
            b.setText(" " + text)
            b.setToolTip(tip)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setIconSize(QSize(14, 14))
            b.setCursor(Qt.PointingHandCursor)
            theme.set_icon(b, icon, 14)
            b.clicked.connect(lambda _=False, f=slot: f())
            row.addWidget(b)
        row.addSpacing(6); row.addWidget(vsep()); row.addSpacing(6)
        row.addWidget(self._icon_button(self.a_snap))
        row.addSpacing(6); row.addWidget(vsep()); row.addSpacing(6)
        for a in (self.a_zout, self.a_zin, self.a_fit):
            row.addWidget(self._icon_button(a))
        lay.addWidget(bar)
        lay.addWidget(self.timeline, 1)
        self.timeline.setMinimumHeight(190)
        return wrap

    # ── layout persistence (just the splitter sizes) ─────────────────────────
    def _restore_layout(self):
        st = QSettings("Venma", "VideoEditor")
        h, v = st.value(f"{LAYOUT_KEY}/h"), st.value(f"{LAYOUT_KEY}/v")
        ok = h is not None and v is not None and self.hsplit.restoreState(h) and self.vsplit.restoreState(v)
        if not ok:
            self.hsplit.setSizes([320, 760, 330])
            self.vsplit.setSizes([560, 320])

    def _save_layout(self):
        st = QSettings("Venma", "VideoEditor")
        st.setValue(f"{LAYOUT_KEY}/h", self.hsplit.saveState())
        st.setValue(f"{LAYOUT_KEY}/v", self.vsplit.saveState())

    def _wire(self):
        c = self.timeline.canvas
        c.playheadChanged.connect(self.engine.seek)
        c.clipSelected.connect(self._on_selected)
        c.clipMoved.connect(self._on_clip_moved)
        c.clipTrimmed.connect(self._on_clip_trimmed)
        c.clipRippled.connect(self._on_clip_rippled)
        c.clipSlipped.connect(self._on_clip_slipped)
        self.props.titleEdited.connect(self._on_title_edit)
        self.view.selected_provider = lambda: self.timeline.canvas.selected
        self.view.editable_check = lambda clip: not self.project.track_locked(clip.track)   # locked layers can't be grabbed
        self.view.titlePicked.connect(self._on_title_picked)           # click / drag text on the preview
        self.view.titleDragged.connect(self._on_title_dragged)
        self.view.clipDragged.connect(self._on_clip_dragged)               # move / resize an image or video
        self.view.titleGestureDone.connect(lambda _cid, label: self.commit(label))
        c.statusMessage.connect(lambda msg: self.statusBar().showMessage(msg, 4000))
        c.splitRequested.connect(self._on_split_requested)
        c.mediaDropped.connect(self._on_media_dropped)
        c.trackAddRequested.connect(self._on_track_add)
        c.trackRemoveRequested.connect(self._on_track_remove)
        c.trackMoveRequested.connect(self._on_track_move)
        c.trackFlagToggled.connect(self._on_track_flag)
        c.trackReordered.connect(self._on_track_reorder)
        self.engine.timeChanged.connect(self._on_time)
        self.engine.playingChanged.connect(self._on_playing)
        self.engine.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        self.media_bin.importRequested.connect(self.import_dialog)
        self.media_bin.addRequested.connect(lambda ids: self.add_media_list(ids.split("\n")))
        self.media_bin.removeRequested.connect(lambda ids: [self.remove_media(i) for i in ids.split("\n")])
        self.text_panel.addRequested.connect(self.add_title)
        self.props.propertyEdited.connect(self._on_property)
        self.props.resetRequested.connect(self._on_reset_props)
        self.props.keyframeAdded.connect(self._on_keyframe_add)
        self.props.keyframeRemoved.connect(self._on_keyframe_remove)
        self.props.keyframesCleared.connect(self._on_keyframes_clear)
        self.props.keyframeToggleRequested.connect(self._on_keyframe_toggle)
        self.props.keyframeSeek.connect(self._seek_to_keyframe)
        c.keyframeSelected.connect(lambda cid, t: self._seek_to_keyframe(t, cid))
        c.keyframeMoved.connect(self._on_keyframe_moved)
        c.keyframeDeleteRequested.connect(self._on_keyframe_delete)
        self.props.animationEdited.connect(self._on_animation)
        self.props.transitionEdited.connect(self._on_transition)
        self.props.speedEdited.connect(lambda v: self._apply_speed(v, live=True))
        self.props.reverseEdited.connect(self._on_reverse)
        self.props.fadeEdited.connect(self._on_fade)

    # ── environment ──────────────────────────────────────────────────────────
    def _check_environment(self):
        if not ft.have_ffmpeg():
            QMessageBox.warning(
                self, "FFmpeg not found",
                "Venma Video needs FFmpeg (ffmpeg + ffprobe) to import media and export video.\n\n"
                "Install it and make sure it is on your PATH:\n"
                "  • Windows: winget install Gyan.FFmpeg\n  • macOS: brew install ffmpeg\n  • Linux: sudo apt install ffmpeg\n\n"
                "Or place ffmpeg(.exe) and ffprobe(.exe) in an 'ffmpeg' folder next to the app.")
        elif not HAS_MULTIMEDIA:
            self.statusBar().showMessage("QtMultimedia not available: the preview plays silently (video and export still work).", 10000)

    # ── history ──────────────────────────────────────────────────────────────
    def _reset_history(self, label):
        self.history = [self.project.snapshot()]
        self.labels = [label]
        self.index = 0

    def commit(self, label):
        self._edit_timer.stop()
        del self.history[self.index + 1:]
        del self.labels[self.index + 1:]
        self.history.append(self.project.snapshot())
        self.labels.append(label)
        if len(self.history) > MAX_HISTORY:
            del self.history[1]
            del self.labels[1]
        self.index = len(self.history) - 1
        self.dirty = True
        self._edit_label = "Edit Clip"
        self._refresh_all()

    def _restore(self, index):
        self._edit_timer.stop()
        self.index = index
        self.project = Project.from_snapshot(self.history[index])
        self.dirty = True
        self._refresh_all(rebuild_bin=True)

    def undo(self):
        if self.index > 0:
            self._restore(self.index - 1)

    def redo(self):
        if self.index < len(self.history) - 1:
            self._restore(self.index + 1)

    # ── refresh ──────────────────────────────────────────────────────────────
    def _refresh_all(self, rebuild_bin=False):
        self.timeline.canvas.thumbs = self.thumbs
        self.timeline.canvas.waves = self.waves
        self.engine.thumbs = self.thumbs
        self.timeline.canvas.set_project(self.project)
        self.engine.set_project(self.project, reset=rebuild_bin)
        self.timeline.canvas.set_playhead(self.engine.t)
        if rebuild_bin or self.media_bin.list.count() != len(self.project.library()):
            self.media_bin.populate(self.project, self.thumbs)
        self.a_undo.setEnabled(self.index > 0)
        self.a_redo.setEnabled(self.index < len(self.history) - 1)
        self._update_clip_panel()
        self._update_labels()
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.setWindowTitle(f"{name}{'*' if self.dirty else ''} — Venma Video")
        self.project_label.setText(f"{name}{'*' if self.dirty else ''}")

    def _update_labels(self):
        p = self.project
        self.dur_label.setText("/ " + format_tc(p.duration(), p.fps))
        self.tc_label.setText(format_tc(self.engine.t, p.fps))
        self.fmt_label.setText(f"{p.width}×{p.height} · {p.fps} fps")
        self.statusBar().showMessage(f"{len(p.clips)} clip(s) · {len(p.library())} media file(s)", 4000)

    def _update_clip_panel(self):
        sid = self.timeline.canvas.selected
        clip = self.project.clip(sid) if sid else None
        self.props.set_clip(clip, self.project.media.get(clip.media_id) if clip else None, self.project.fps)
        if clip is not None:
            self.props.load_transition(clip, self.project.transition_prev(clip) is not None)

    # ── timeline / engine callbacks ──────────────────────────────────────────
    def _on_time(self, t):
        self.timeline.canvas.set_playhead(t, follow=self.engine.playing)
        self.tc_label.setText(format_tc(t, self.project.fps))
        # Keep the Properties panel aware of the current clip-local time
        sid = self.timeline.canvas.selected
        clip = self.project.clip(sid) if sid else None
        if clip is not None:
            t_clip = max(0.0, t - clip.start)
            self.props.set_playhead_clip_time(t_clip)
            # Refresh keyframe list on every tick when playing (cheap list update)
            if self.engine.playing and clip.keyframes:
                self.props.set_keyframes(clip.keyframes)

    def _on_playing(self, playing):
        name = "pause" if playing else "play"
        theme.set_icon(self.a_play, name, 20)
        theme.set_icon(self.play_btn, name, 16, "on_accent")

    def step(self, frames):
        self.engine.pause()
        self.engine.seek(max(0.0, self.engine.t + frames / self.project.fps))

    def _on_selected(self, cid):
        self._update_clip_panel()
        self.view.update()                              # selection box on the preview
        # Sync the clip-local time whenever selection changes
        if cid:
            clip = self.project.clip(cid)
            if clip is not None:
                t_clip = max(0.0, self.engine.t - clip.start)
                self.props.set_playhead_clip_time(t_clip)

    def _refuse_locked(self, cid):
        """True (with a status message) if clip ``cid`` is on a locked layer, so the edit must not happen."""
        c = self.project.clip(cid) if cid else None
        if c is not None and self.project.track_locked(c.track):
            self.statusBar().showMessage(f"{self.project.tracks[c.track]['name']} is locked. Click its lock to edit.", 4000)
            return True
        return False

    def _on_clip_moved(self, cid, track, start):
        if self._refuse_locked(cid):
            return
        self.project.move(cid, track, start)
        self.commit("Move Clip")

    def _on_clip_trimmed(self, cid, side, edge):
        if self._refuse_locked(cid):
            return
        (self.project.trim_left if side == "left" else self.project.trim_right)(cid, edge)
        self.commit("Trim Clip")

    def _on_clip_rippled(self, cid, side, edge):
        if not self._refuse_locked(cid) and self.project.ripple_trim(cid, side, edge):
            self.commit("Ripple Trim")

    def _on_clip_slipped(self, cid, in_point):
        if not self._refuse_locked(cid) and self.project.slip(cid, in_point):
            self.commit("Slip Clip")

    def _on_split_requested(self, cid, t):
        if not self._refuse_locked(cid) and self.project.split(cid, t):
            self.commit("Split Clip")

    def _on_media_dropped(self, ids, track, t):
        self.add_media_list(ids.split("\n"), track if track != -1 else None, t)

    # ── layers ───────────────────────────────────────────────────────────────
    def _on_track_add(self, kind, index):
        i = self.project.insert_track(index, kind)
        self.commit("Add Video Layer" if kind == "video" else "Add Audio Track")
        self.timeline.canvas.ensure_track_visible(i)

    def add_video_layer(self):
        self._on_track_add("video", 0)

    def add_audio_track(self):
        self._on_track_add("audio", len(self.project.tracks))

    # ── voiceover ────────────────────────────────────────────────────────────
    def _voiceover_dir(self):
        """Next to the saved project, else in the app's data folder (the project keeps the full path)."""
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or tempfile.gettempdir()
        return vo_core.recordings_dir(self.path, os.path.join(base, vo_core.FOLDER_NAME))

    def record_voiceover(self):
        """Record from the microphone at the playhead; the take lands on an audio track as a new clip."""
        if not HAS_INPUT or not VoiceRecorder.devices():
            QMessageBox.warning(
                self, "No microphone",
                "Venma Video couldn't find a microphone.\n\nConnect one (and, on macOS or Windows, allow this app "
                "to use it in the system privacy settings), then try again.")
            return
        p = self.project
        self.engine.pause()
        start = self.engine.t
        track, created = p.voiceover_track(start)        # the audio layer the take will land on (made now if needed)
        canvas = self.timeline.canvas
        canvas.set_project(p)
        canvas.ensure_track_visible(track)
        dlg = VoiceoverDialog(self.engine, p, canvas, track, start, self._voiceover_dir(), self)
        ok = dlg.exec_() == QDialog.Accepted
        canvas.clear_recording()
        if not ok:
            if created and p.track_is_empty(track):
                p.remove_track(track)                    # nothing was recorded: don't leave a stray empty track
            self._refresh_all()
            return
        r = dlg.result_info
        item = MediaItem(p.new_media_id(), r["path"], "audio", r["duration"], has_audio=True)
        p.add_media(item)
        c = p.place_clip(item.id, r["track"], r["start"], r["duration"])
        if created and c.track != track and p.track_is_empty(track):
            p.remove_track(track)                        # the take had to go elsewhere; drop the unused new track
        self.commit("Record Voiceover")
        self._request_waves([item])                      # the take gets a real waveform on its clip
        canvas.select(c.id)
        canvas.ensure_track_visible(c.track)
        self.engine.seek(c.end)
        name = p.tracks[c.track]["name"]
        self.statusBar().showMessage(f"Voiceover added to {name}: {os.path.basename(r['path'])}", 6000)

    def _on_track_remove(self, index):
        if not self.project.can_remove_track(index):
            self.statusBar().showMessage("Keep at least one video and one audio track.", 4000)
            return
        name = self.project.tracks[index]["name"]
        n = sum(1 for c in self.project.clips if c.track == index)
        if n and QMessageBox.question(self, "Delete track", f"Delete {name} and its {n} clip(s)?") != QMessageBox.Yes:
            return
        self.project.remove_track(index)
        self.commit(f"Delete {name}")

    def _on_track_move(self, index, delta):
        if self.project.move_track(index, delta):
            self.commit("Reorder Layers")

    FLAG_LABELS = {"hidden": ("Hide", "Show"), "locked": ("Lock", "Unlock"), "muted": ("Mute", "Unmute")}

    def _on_track_flag(self, index, key):
        """Hide / lock / mute switch of a layer, clicked in the timeline header."""
        if not self.project.toggle_track_flag(index, key):
            return
        track = self.project.tracks[index]
        verb = self.FLAG_LABELS[key][0 if track.get(key) else 1]       # switched on -> "Hide" / "Lock" / "Mute"
        self.commit(f"{verb} {track['name']}")

    def _on_track_reorder(self, src, dst):
        """A layer was dragged to a new place in the stack."""
        if self.project.reorder_track(src, dst):
            self.commit("Reorder Layers")

    def remove_empty_layers(self):
        n = self.project.remove_empty_tracks()
        if n:
            self.commit(f"Remove {n} Empty Track(s)")
        else:
            self.statusBar().showMessage("No empty layers to remove.", 3000)

    def _toggle_snap(self):
        self.timeline.canvas.snap_on = self.a_snap.isChecked()

    def select_tool(self, tool):
        self.timeline.canvas.set_tool(tool)
        self.tool_actions[tool].setChecked(True)
        self.statusBar().showMessage(self.TOOL_HINTS[tool], 5000)

    # ── editing commands ─────────────────────────────────────────────────────
    def add_to_timeline(self, media_id, track=None, t=None):
        self.add_media_list([media_id], track, t)

    def add_media_list(self, media_ids, track=None, t=None):
        """Add any number of media items at ``t`` (default: the playhead), one after another. A clip that
        would overlap something goes on a free layer, or a brand-new layer, so nothing is ever refused."""
        ids = [i for i in media_ids if i in self.project.media]
        if not ids:
            return
        t = self.engine.t if t is None else t
        first = last = None
        for mid in ids:
            c = self.project.place_clip(mid, track, t)
            track, t = c.track, c.end              # the next one follows on the same layer (or stacks if busy)
            first = first or c
            last = c
        self.commit("Add Clip" if len(ids) == 1 else f"Add {len(ids)} Clips")
        self.timeline.canvas.select(last.id)
        self.timeline.canvas.ensure_track_visible(last.track)
        self.engine.seek(first.start)

    def add_title(self, preset="Title"):
        """Add a title clip at the playhead (in front of everything added before it) using a style preset."""
        c = self.project.add_title(self.engine.t, **TITLE_PRESETS.get(preset, {}))
        self.commit(f"Add {preset}")
        self.timeline.canvas.select(c.id)
        self.timeline.canvas.ensure_track_visible(c.track)
        self.engine.seek(c.start)

    def _on_title_picked(self, cid):
        if self.timeline.canvas.selected != cid:
            self.timeline.canvas.select(cid)

    def _on_title_dragged(self, cid, vals):
        """Live update while a title is dragged / resized on the preview (committed on mouse release)."""
        if not self.project.set_title(cid, **vals):
            return
        c = self.project.clip(cid)
        self.props._load_title(c.title)                 # keep X / Y / size controls in sync
        self.view.update()
        self.timeline.canvas.invalidate()

    def _on_clip_dragged(self, cid, vals):
        """Live update while an image / video is dragged or resized on the preview (committed on release)."""
        c = self.project.clip(cid)
        if c is None or self.project.track_locked(c.track):
            return
        if self.engine.playing:
            self.engine.pause()                         # editing a picture is done on a still frame
        t_clip = max(0.0, self.engine.t - c.start)
        # CapCut behaviour: once a clip has keyframes, moving / resizing it on a frame that has no keyframe
        # creates one right there, and on a frame that has one it updates that keyframe. The picture can
        # therefore be placed anywhere between two keyframes and the motion bends through that new point.
        had = len(c.keyframes)
        c.edit_at(t_clip, **vals)
        if c.keyframes:
            self.props.set_playhead_clip_time(t_clip)
            self.props.set_keyframes(c.keyframes)
            if len(c.keyframes) != had:
                self.timeline.canvas.invalidate()        # a new diamond appeared
        self.dirty = True
        self.props.set_clip(c, self.project.media.get(c.media_id), self.project.fps)   # keep the sliders in sync
        self.view.update()                              # the selection box follows the mouse at once
        if not self._live_timer.isActive():
            self._live_timer.start()                    # ...the re-rendered picture follows a moment later

    def _on_title_edit(self, key, value):
        sid = self.timeline.canvas.selected
        if sid and self._refuse_locked(sid):
            self._update_clip_panel()
            return
        if sid and self.project.set_title(sid, **{key: value}):
            self.dirty = True
            self.engine.refresh()
            self.timeline.canvas.invalidate()           # the clip shows the title text
            self._edit_timer.start()

    def remove_media(self, media_id):
        used = sum(1 for c in self.project.clips if c.media_id == media_id)
        if used and QMessageBox.question(self, "Remove media",
                                         f"This media is used by {used} clip(s) on the timeline. Remove it and those clips?") != QMessageBox.Yes:
            return
        self.project.remove_media(media_id)
        self.thumbs.pop(media_id, None)
        self.commit("Remove Media")
        self.media_bin.populate(self.project, self.thumbs)

    def split_at_playhead(self):
        t = self.engine.t
        sel = self.project.clip(self.timeline.canvas.selected) if self.timeline.canvas.selected else None
        targets = [sel] if sel and sel.start < t < sel.end else [c for c in self.project.clips if c.start < t < c.end]
        n = sum(1 for c in targets if self.project.split(c.id, t))
        if n:
            self.commit("Split Clip")
        else:
            self.statusBar().showMessage("Nothing to split at the playhead.", 3000)

    def delete_selected(self, ripple):
        sid = self.timeline.canvas.selected
        kf = self.timeline.canvas.sel_kf
        if sid and kf is not None and kf[0] == sid and not ripple:      # a selected keyframe diamond goes first
            self._on_keyframe_delete(sid, kf[1])
            return
        if not sid or self._refuse_locked(sid):
            return
        self.timeline.canvas.select(None)
        self.project.delete([sid], ripple=ripple)
        self.commit("Ripple Delete" if ripple else "Delete Clip")

    def _on_property(self, attr, value):
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c is None:
            return
        if self._refuse_locked(sid):
            self._update_clip_panel()                    # put the sliders back
            return
        if c.keyframes and attr in KEYFRAMED_PROPS:
            # CapCut behaviour: with keyframes on the clip, a slider change at the playhead updates the keyframe
            # there, or creates one if the playhead is between keyframes.
            t_clip = max(0.0, self.engine.t - c.start)
            had = len(c.keyframes)
            c.edit_at(t_clip, **{attr: value})
            self.props.set_keyframes(c.keyframes)
            if len(c.keyframes) != had:
                self.timeline.canvas.invalidate()
        else:
            setattr(c, attr, value)
        self.dirty = True
        self.engine.refresh()
        self._edit_timer.start()

    def add_keyframe_at_playhead(self):
        """Action handler for adding/toggling a keyframe at the current playhead for the selected clip."""
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if not c:
            self.statusBar().showMessage("Select a clip to add a keyframe.", 3000)
            return
        t_clip = max(0.0, self.engine.t - c.start)
        self._on_keyframe_add(t_clip)

    def _on_keyframe_add(self, t_clip):
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if not c or self._refuse_locked(sid):
            return
        # Record current position, scale, rotation and opacity
        cur_props = c.props_at(t_clip)
        vals = {p: cur_props.get(p, getattr(c, p)) for p in KEYFRAMED_PROPS}
        c.set_keyframe(t_clip, **vals)
        self.props.set_keyframes(c.keyframes)
        self.timeline.canvas.invalidate()
        self.commit(f"Add Keyframe @ {t_clip:.2f}s")

    def _on_keyframe_remove(self, t_clip):
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if not c or self._refuse_locked(sid):
            return
        if c.remove_keyframe(t_clip, tol=0.05):
            self.props.set_keyframes(c.keyframes)
            self.timeline.canvas.invalidate()
            self.commit(f"Remove Keyframe @ {t_clip:.2f}s")
        else:
            self.statusBar().showMessage("No keyframe near the playhead to remove.", 3000)

    def _on_keyframes_clear(self):
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if not c or self._refuse_locked(sid):
            return
        if not c.keyframes:
            return
        c.clear_keyframes()
        self.props.set_keyframes(c.keyframes)
        self.timeline.canvas.invalidate()
        self.commit("Clear Keyframes")

    def _on_keyframe_toggle(self, t_clip):
        """The diamond next to a slider / Alt+K: remove the keyframe under the playhead, or add one."""
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if not c or self._refuse_locked(sid):
            return
        t_clip = min(max(0.0, t_clip), c.duration)
        what = c.toggle_keyframe(t_clip)
        self.timeline.canvas.sel_kf = None
        self.props.set_keyframes(c.keyframes)
        self.timeline.canvas.invalidate()
        self.commit(f"{'Add' if what == 'added' else 'Remove'} Keyframe @ {t_clip:.2f}s")

    def _on_keyframe_moved(self, cid, old_t, new_t):
        """A keyframe diamond was dragged along its clip in the timeline."""
        c = self.project.clip(cid)
        if c is None or self.project.track_locked(c.track):
            return
        kf = c.keyframe_at(old_t, 1e-3)
        if kf is None or not c.move_keyframe(kf, new_t):
            return
        self.timeline.canvas.sel_kf = (cid, kf.t)
        self.props.set_keyframes(c.keyframes)
        self.commit(f"Move Keyframe to {kf.t:.2f}s")
        self.timeline.canvas.sel_kf = (cid, kf.t)         # (commit rebuilds the clips; keep the diamond selected)
        self.timeline.canvas.invalidate()

    def _on_keyframe_delete(self, cid, t):
        c = self.project.clip(cid)
        if c is None or self._refuse_locked(cid):
            return
        if c.remove_keyframe(t, tol=1e-3):
            self.timeline.canvas.sel_kf = None
            self.props.set_keyframes(c.keyframes)
            self.commit(f"Delete Keyframe @ {t:.2f}s")

    def _seek_to_keyframe(self, t_clip, cid=None):
        """Put the playhead on a keyframe of the selected clip (or of clip ``cid``)."""
        sid = cid or self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c is None:
            return
        self.engine.pause()
        self.engine.seek(c.start + min(max(0.0, t_clip), c.duration))

    def _goto_keyframe(self, direction):
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c is None or not c.keyframes:
            self.statusBar().showMessage("Select a clip with keyframes first.", 3000)
            return
        t_clip = max(0.0, self.engine.t - c.start)
        kf = c.next_keyframe(t_clip) if direction > 0 else c.prev_keyframe(t_clip)
        if kf is not None:
            self._seek_to_keyframe(kf.t, sid)

    # ── transitions ──────────────────────────────────────────────────────────
    def _on_transition(self, name, seconds):
        """The Transition panel picked a style (or "" = none) or moved the duration slider."""
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c is None:
            return
        if self._refuse_locked(sid):
            self._update_clip_panel()
            return
        name = name or None
        only_duration = name is not None and c.transition.get("type") == name
        if not self.project.set_transition(sid, name, seconds):
            self._update_clip_panel()
            return
        if only_duration:                                # dragging the duration slider: commit once it settles
            self.dirty = True
            self.timeline.canvas.invalidate()
            self.engine.refresh()
            self._edit_label = "Change Transition"
            self._edit_timer.start()
            return
        label = dict(TRANSITIONS).get(name, "")
        self.commit(f"Transition: {label}" if name else "Remove Transition")
        if name:
            self._preview_transition(self.project.clip(sid))

    def _preview_transition(self, c):
        """Play across the cut so the transition that was just picked can be seen, then stop on its own."""
        tr = self.project.active_transition(c) if c is not None else None
        if tr is None:
            self.statusBar().showMessage("Transition set. It shows once another clip on the same layer ends exactly "
                                         "where this one starts.", 6000)
            return
        t0 = max(0.0, c.start - 0.8)
        self.engine.pause()
        self.engine.seek(t0)
        self.engine.play()
        self._anim_stop.start(int((c.start - t0 + tr[1] + 0.4) * 1000))

    # ── speed / reverse ──────────────────────────────────────────────────────
    def _retime_target(self):
        """The selected clip if its speed / direction may change, else None (with a message saying why)."""
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c is None:
            self.statusBar().showMessage("Select a clip first.", 3000)
            return None
        if self._refuse_locked(sid):
            self._update_clip_panel()                    # put the controls back
            return None
        if not self.project.can_retime(sid):
            self.statusBar().showMessage("Photos and titles have no source to speed up or reverse: "
                                         "just trim them to the length you want.", 4000)
            return None
        return c

    def _on_fade(self, which, seconds):
        """The Fade in / Fade out slider moved: change the selected clip's audio fade. Like the speed slider, the
        result shows (and plays) at once and the undo step is recorded when the drag settles."""
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c is None:
            return
        if self._refuse_locked(sid):
            self._update_clip_panel()                    # put the sliders back
            return
        changed = self.project.set_fade(sid, which, seconds)
        self.props.refresh_fades(c)                      # shows what was really set (the fades can't overlap)
        if not changed:
            return
        self.dirty = True
        self.timeline.canvas.invalidate()
        self._update_labels()
        self.engine.refresh()
        self._edit_label = "Change Fade"
        self._edit_timer.start()

    # ── waveforms ────────────────────────────────────────────────────────────
    def _request_waves(self, items):
        """Decode the waveform of every audio file / video with sound in ``items`` that has none yet."""
        for m in items:
            if (m.kind == "audio" or (m.kind == "video" and m.has_audio)) and m.path not in self.waves \
                    and m.path not in self._wave_queue and os.path.isfile(m.path):
                self._wave_queue.append(m.path)
        self._next_wave()

    def _next_wave(self):
        if self._wave_worker is not None or not self._wave_queue or not ft.have_ffmpeg():
            return
        w = WaveformWorker(self._wave_queue.pop(0), self)
        w.ready.connect(self._on_wave_ready)
        w.finished.connect(self._wave_done)
        self._wave_worker = w
        w.start()

    def _on_wave_ready(self, path, wf):
        self.waves[path] = wf
        self.timeline.canvas.invalidate()

    def _wave_done(self):
        w, self._wave_worker = self._wave_worker, None
        if w is not None:
            w.deleteLater()
        self._next_wave()

    def _apply_speed(self, value, live):
        """Change the selected clip's speed. ``live`` is the slider being dragged: the result shows at once and the
        undo step is recorded when the drag settles; otherwise (menu, chip) it is one undo step right away."""
        c = self._retime_target()
        if c is None:
            return
        top = self.project.max_speed(c.id)
        changed = self.project.set_speed(c.id, value)
        clamped = value > top + 1e-6
        if changed and live:
            self.dirty = True
            self.timeline.canvas.set_project(self.project)
            self._update_labels()
            self.engine.refresh()
            self.props.refresh_speed_info(c)
            self._edit_label = "Change Speed"
            self._edit_timer.start()
        elif changed:
            self.commit("Change Speed")
        if clamped:
            self.statusBar().showMessage(f"This clip can't go faster than {top:g}×: it would be shorter than "
                                         f"{MIN_CLIP:g} s.", 5000)
        if clamped or not changed:
            self._update_clip_panel()                    # show the speed the clip really has

    def reverse_selected(self):
        c = self._retime_target()
        if c is not None:
            self._on_reverse(not c.reverse)

    def _on_reverse(self, flag):
        c = self._retime_target()
        if c is None:
            return
        if self.project.set_reverse(c.id, flag):
            self.commit("Reverse Clip" if flag else "Play Clip Forward")
        else:
            self._update_clip_panel()

    # ── In / Out / Combo animations ──────────────────────────────────────────
    def _on_animation(self, slot, name, seconds):
        """The Animation panel picked a preset (or "" = none) or moved the duration slider."""
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c is None:
            return
        if self._refuse_locked(sid):
            self._update_clip_panel()
            return
        name = name or None
        only_duration = name is not None and c.animation.get(slot) == name
        if not self.project.set_animation(sid, slot, name, seconds):
            self._update_clip_panel()                    # nothing changed: put the chips back as they were
            return
        if only_duration:                                # dragging the duration slider: commit once it settles
            self.dirty = True
            self.timeline.canvas.invalidate()
            self.view.update()
            self._edit_timer.start()
            return
        what = anim_core.preset_label(slot, name) if name else "None"
        self.commit(f"Animation: {what}" if name else f"Remove {slot.title()} Animation")
        if name:
            self._preview_animation(self.project.clip(sid), slot)

    def _preview_animation(self, c, slot):
        """Play the animation that was just picked, the way CapCut does, then stop on its own."""
        if c is None or not c.animation.get(slot):
            return
        d_in, d_out = anim_core.durations(c.animation, c.duration)
        if slot == "in":
            t0, span = c.start, min(c.duration, d_in + 0.7)
        elif slot == "out":
            t0 = max(c.start, c.end - d_out - 0.5)
            span = c.end - t0
        else:
            t0, span = c.start, min(c.duration, 3.0)
        self.engine.pause()
        self.engine.seek(t0)
        self.engine.play()
        self._anim_stop.start(int((span + 0.1) * 1000))

    def _stop_animation_preview(self):
        if self.engine.playing:
            self.engine.pause()

    def _on_reset_props(self):
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c and not self._refuse_locked(sid):
            for k, v in CLIP_DEFAULTS.items():
                setattr(c, k, v)
            c.animation = {}
            c.transition = {}
            c.clear_keyframes()
            self.props.set_keyframes(c.keyframes)
            self.timeline.canvas.invalidate()
            self.commit("Reset Clip Effects")

    # ── media import ─────────────────────────────────────────────────────────
    def import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import media", "", ft.MEDIA_FILTER)
        self.import_files(paths)

    def import_files(self, paths):
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return
        if not ft.have_ffmpeg():
            self._check_environment()
            return
        existing = {m.path for m in self.project.media.values()}
        jobs = [dict(path=p, probe=True) for p in paths if p not in existing]
        if not jobs:
            self.statusBar().showMessage("Already imported.", 3000)
            return
        self.statusBar().showMessage(f"Importing {len(jobs)} file(s)…")
        w = MediaWorker(jobs, self)
        w.total, w.done, w.added = len(jobs), 0, 0
        w.ready.connect(self._on_media_ready)
        w.finished.connect(lambda w=w: self._finish_import(w))
        self._workers.append(w)
        w.start()

    def _finish_import(self, w):
        """One history entry per import batch, however many files it held (hundreds are fine)."""
        if w in self._workers:
            self._workers.remove(w)
        if w.added:
            self.commit("Import " + (self.project.media[self._last_import].name if w.added == 1
                                     else f"{w.added} files"))
            self.media_bin.populate(self.project, self.thumbs)
            self._set_left_tab(0)
        if w.total > 1 and getattr(w, "failed", 0):
            QMessageBox.warning(self, "Import", f"{w.failed} files could not be imported.")

    def _on_media_ready(self, path, info, img, err):
        w = self.sender()
        w.done += 1
        if info is None:
            w.failed = getattr(w, "failed", 0) + 1
            if w.total == 1:
                QMessageBox.warning(self, "Import failed", f"{os.path.basename(path)}\n\n{err or 'Unsupported file.'}")
            return
        self.statusBar().showMessage(f"Importing… {w.done}/{w.total}")
        p = self.project
        first = not p.library() and not p.clips
        item = MediaItem(p.new_media_id(), path, **info)
        p.add_media(item)
        if img is not None:
            self.thumbs[item.id] = QPixmap.fromImage(img)
        if first and item.kind == "video" and item.width and item.height:
            p.width, p.height = item.width - item.width % 2, item.height - item.height % 2
            p.fps = min((24, 25, 30, 50, 60), key=lambda f: abs(f - (item.fps or 30)))
            self.statusBar().showMessage(f"Project set to match first clip: {p.width}×{p.height} @ {p.fps} fps", 6000)
        w.added += 1
        self._last_import = item.id
        self._request_waves([item])
        self.media_bin.add_item(item, fit_tile(self.thumbs[item.id]) if item.id in self.thumbs else None)

    # ── project files ────────────────────────────────────────────────────────
    def _confirm_discard(self):
        if not self.dirty or not self.project.clips and not self.project.library():
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes to this project first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def new_project(self):
        if not self._confirm_discard():
            return
        d = ProjectDialog(self)
        if d.exec_() != ProjectDialog.Accepted:
            return
        self.engine.pause()
        self.project = Project(*d.values(), typed=True)
        self.project.set_background(*d.background())
        self.path, self.dirty = None, False
        self.thumbs.clear()
        self.engine.t = 0.0
        self._reset_history("New Project")
        self._refresh_all(rebuild_bin=True)

    def project_settings(self):
        p = self.project
        d = ProjectDialog(self, p.width, p.height, p.fps, "Project Settings", p.bg_mode, p.bg_color)
        if d.exec_() == ProjectDialog.Accepted:
            p.width, p.height, p.fps = d.values()
            p.set_background(*d.background())
            self.commit("Project Settings")

    def switch_canvas(self, aw, ah):
        """Canvas aspect-ratio switch (9:16, 1:1, 16:9, 4:5): pictures re-fit inside the new frame."""
        p = self.project
        self.engine.pause()
        p.set_canvas(aw, ah)
        self.commit(f"Canvas {aw}:{ah}")

    def set_canvas_background(self, mode):
        self.project.set_background(mode)
        self.commit("Canvas Background")

    def pick_canvas_color(self):
        c = QColorDialog.getColor(QColor(self.project.bg_color), self, "Canvas color")
        if c.isValid():
            self.project.set_background("color", c.name())
            self.commit("Canvas Background")

    def open_project(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", f"Venma Video project (*{PROJECT_EXT})")
        if not path:
            return
        try:
            proj = Project.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return
        self.engine.pause()
        self.project, self.path, self.dirty = proj, path, False
        self.thumbs.clear()
        self.engine.t = 0.0
        self._reset_history("Open Project")
        self._refresh_all(rebuild_bin=True)
        missing = [m.name for m in proj.library() if not os.path.isfile(m.path)]
        if missing:
            QMessageBox.warning(self, "Missing media", "These files could not be found:\n\n" + "\n".join(missing[:15]))
        self.waves.clear()
        self._request_waves(proj.library())
        jobs = [dict(path=m.path, probe=False, kind=m.kind, duration=m.duration)
                for m in proj.library() if os.path.isfile(m.path) and m.kind != "audio"]
        if jobs and ft.have_ffmpeg():
            w = MediaWorker(jobs, self)
            w.ready.connect(self._on_thumb_ready)
            w.finished.connect(lambda w=w: self._workers.remove(w) if w in self._workers else None)
            self._workers.append(w)
            w.start()

    def _on_thumb_ready(self, path, _info, img, _err):
        if img is None:
            return
        for m in self.project.media.values():
            if m.path == path:
                self.thumbs[m.id] = QPixmap.fromImage(img)
                self.media_bin.set_thumb(m.id, self.thumbs[m.id])
        self.timeline.canvas.invalidate()

    def save_project(self, save_as=False):
        path = self.path
        if save_as or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save project", self.path or "Untitled" + PROJECT_EXT,
                                                  f"Venma Video project (*{PROJECT_EXT})")
            if not path:
                return False
            if not path.endswith(PROJECT_EXT):
                path += PROJECT_EXT
        try:
            self.project.save(path)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False
        self.path, self.dirty = path, False
        self._refresh_all()
        self.statusBar().showMessage("Project saved.", 3000)
        return True

    # ── export ───────────────────────────────────────────────────────────────
    def export_video(self):
        p = self.project
        if not p.clips:
            QMessageBox.information(self, "Export", "The timeline is empty — add some clips first.")
            return
        if not ft.have_ffmpeg():
            self._check_environment()
            return
        self.engine.pause()
        base = os.path.splitext(self.path)[0] if self.path else os.path.join(os.path.expanduser("~"), "Untitled")
        d = ExportDialog(p, base + ".mp4", self)
        if d.exec_() != ExportDialog.Accepted:
            return
        out, fmt, quality = d.values()
        if not out:
            return
        missing = [p.media[c.media_id].name for c in p.clips
                   if c.title is None and not os.path.isfile(p.media[c.media_id].path)]
        if missing:
            QMessageBox.warning(self, "Missing media", "Can't export — missing files:\n\n" + "\n".join(sorted(set(missing))[:15]))
            return
        ep = p
        size = d.size()
        if size and tuple(size) != (p.width, p.height):
            # A social preset is a temporary canvas switch: the export is laid out on a copy of the project at that
            # size (clips keep their place as fractions of the frame; pictures are re-fitted, backdrop behind them).
            ep = Project.from_dict(p.to_dict())
            ep.width, ep.height = size
        self._run_export(ep, out, fmt, quality, d.gpu_encoder())

    def _run_export(self, p, out, fmt, quality, gpu):
        """Render ``p`` to ``out``. With a GPU encoder, a failed export is retried once on the CPU."""
        title_dir = None
        try:
            text_images = {}
            if any(c.title is not None for c in p.clips):
                title_dir = tempfile.mkdtemp(prefix="venma_titles_")       # one transparent PNG per title
                text_images = render_project_titles(p, title_dir)
            rev_dir, prepare = None, None
            if ft.reversed_clips(p):
                # FFmpeg's reverse filter holds a whole clip in memory, so reversed clips are rendered backwards in
                # short pieces first (inside the worker, with progress), and the export command is built after that.
                rev_dir = tempfile.mkdtemp(prefix="venma_rev_")

                def prepare(progress, cancelled, p=p, out=out, fmt=fmt, quality=quality, ti=text_images, d=rev_dir, g=gpu):
                    rev = ft.render_reversed_media(p, d, progress=progress, cancelled=cancelled)
                    return ft.build_export_command(p, out, fmt, quality, text_images=ti, reversed_media=rev, gpu=g)
                cmd = None
            else:
                cmd = ft.build_export_command(p, out, fmt, quality, text_images=text_images, gpu=gpu)
        except Exception as e:
            if title_dir:
                shutil.rmtree(title_dir, ignore_errors=True)
            QMessageBox.critical(self, "Export", str(e))
            return
        dlg = QProgressDialog("Rendering…", "Cancel", 0, 1000, self)
        dlg.setWindowTitle("Exporting")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        w = ExportWorker(cmd, p.duration(), self, prepare=prepare)
        w.progress.connect(lambda f: dlg.setValue(int(f * 1000)))
        w.status.connect(dlg.setLabelText)
        dlg.canceled.connect(w.cancel)

        def done(ok, msg):
            dlg.close()
            if title_dir:
                shutil.rmtree(title_dir, ignore_errors=True)
            if rev_dir:
                shutil.rmtree(rev_dir, ignore_errors=True)
            if ok:
                QMessageBox.information(self, "Export", f"Saved to\n{out}"
                                        + (f"\n\nEncoded on the GPU ({ft.gpu_label(gpu)})." if gpu else ""))
            elif gpu and "cancel" not in msg.lower():
                self.statusBar().showMessage("GPU encoder failed, retrying on the CPU…", 8000)
                QTimer.singleShot(0, lambda: self._run_export(p, out, fmt, quality, None))
            elif "cancel" not in msg.lower():
                QMessageBox.critical(self, "Export failed", msg)
        w.finished_ok.connect(done)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()
        dlg.show()

    # ── misc ─────────────────────────────────────────────────────────────────
    def toggle_theme(self):
        theme.set_theme(QApplication.instance(), "dark" if theme.NAME == "light" else "light")   # re-tints bound icons
        theme.set_icon(self.a_theme, "sun" if theme.NAME == "dark" else "moon", 20)
        self.props.retint()
        self.timeline.canvas.invalidate()
        self.view.update()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.import_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self._save_layout()
        self.engine.shutdown()
        self._wave_queue.clear()
        if self._wave_worker is not None:
            self._wave_worker.stop()
            self._wave_worker.wait(2000)
        for w in list(self._workers):
            if isinstance(w, ExportWorker):
                w.cancel()
            w.wait(2000)
        e.accept()