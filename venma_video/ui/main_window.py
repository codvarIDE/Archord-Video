"""Venma Video main window: same layout language as Venma (tool palette, options bar, tabbed side
panels), with a preview monitor and a multi-track timeline in the centre."""
import os

from PyQt5.QtCore import Qt, QSize, QTimer, QSettings
from PyQt5.QtGui import QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolBar, QAction, QActionGroup,
    QDockWidget, QTabWidget, QSplitter, QFileDialog, QMessageBox, QCheckBox, QProgressDialog, QSizePolicy,
    QApplication,
)

from . import theme
from ..core import ffmpeg_tools as ft
from ..core.project import Project, MediaItem, CLIP_DEFAULTS
from .timeline import TimelineWidget, format_tc
from .preview import PreviewView, PreviewEngine, HAS_MULTIMEDIA
from .panels import MediaBin, PropertiesPanel, HistoryPanel, ProjectDialog, ExportDialog
from ..workers import MediaWorker, ExportWorker

MAX_HISTORY = 100
PROJECT_EXT = ".vvproj"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.resize(1480, 900)
        self.project = Project()
        self.path = None
        self.dirty = False
        self.thumbs = {}                 # media id -> QPixmap
        self.history, self.labels, self.index = [], [], -1
        self._workers = []
        self._first_import = True
        self._edit_timer = QTimer(self, singleShot=True, interval=500)
        self._edit_timer.timeout.connect(lambda: self.commit("Edit Clip"))

        self.view = PreviewView()
        self.engine = PreviewEngine(self.view, self)
        self.engine.thumbs = self.thumbs
        QApplication.instance().aboutToQuit.connect(self.engine.shutdown)
        self.timeline = TimelineWidget()
        self.media_bin = MediaBin()
        self.props = PropertiesPanel()
        self.history_panel = HistoryPanel()

        self._build_actions()
        self._build_menus()
        self._build_main_toolbar()
        self._build_tools_bar()
        self._build_options_bar()
        self._build_center()
        self._build_dock()
        self._wire()
        self.statusBar().setSizeGripEnabled(False)

        self._reset_history("New Project")
        self._refresh_all()
        QTimer.singleShot(300, self._check_environment)

    # ── actions / menus ──────────────────────────────────────────────────────
    def _act(self, text, slot, shortcut=None, icon=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if icon:
            a.setIcon(theme.icon(icon, 20))
        a.setCheckable(checkable)
        a.triggered.connect(lambda _=False: slot())
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("&New Project…", self.new_project, "Ctrl+N")
        self.a_open = A("&Open Project…", self.open_project, "Ctrl+O", "open")
        self.a_save = A("&Save", self.save_project, "Ctrl+S", "save")
        self.a_saveas = A("Save &As…", lambda: self.save_project(True), "Ctrl+Shift+S")
        self.a_import = A("&Import Media…", self.import_dialog, "Ctrl+I", "import")
        self.a_export = A("&Export Video…", self.export_video, "Ctrl+M", "export")
        self.a_settings = A("Project &Settings…", self.project_settings, "Ctrl+Alt+P")
        self.a_quit = A("&Quit", self.close, "Ctrl+Q")
        self.a_undo = A("&Undo", self.undo, "Ctrl+Z", "undo")
        self.a_redo = A("&Redo", self.redo, "Ctrl+Y", "redo")
        self.a_split = A("&Split at Playhead", self.split_at_playhead, "S", "razor")
        self.a_split2 = A("Split at Playhead (alt)", self.split_at_playhead, "Ctrl+K")
        self.a_delete = A("&Delete Clip", lambda: self.delete_selected(False), "Delete")
        self.a_ripple = A("&Ripple Delete", lambda: self.delete_selected(True), "Shift+Delete")
        self.a_play = A("&Play / Pause", self.engine.toggle, "Space", "play")
        self.a_start = A("Go to &Start", lambda: self.engine.seek(0), "Home", "skip_back")
        self.a_end = A("Go to &End", lambda: self.engine.seek(self.project.duration()), "End", "skip_fwd")
        self.a_prev = A("Previous Frame", lambda: self.step(-1), "Left", "step_back")
        self.a_next = A("Next Frame", lambda: self.step(1), "Right", "step_fwd")
        self.a_zin = A("Zoom Timeline &In", lambda: self.timeline.canvas.zoom_by(1.25), "=", "zoom_in")
        self.a_zout = A("Zoom Timeline &Out", lambda: self.timeline.canvas.zoom_by(0.8), "-", "zoom_out")
        self.a_fit = A("&Fit Timeline", self.timeline.canvas.fit, "\\", "fit")
        self.a_snap = A("&Snapping", self._toggle_snap, "N", "magnet", checkable=True)
        self.a_snap.setChecked(True)
        self.a_theme = A("Toggle &Dark Mode", self.toggle_theme, "Ctrl+Shift+D", "moon")

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_saveas, None, self.a_import, self.a_export, None,
                  self.a_settings, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, None, self.a_split, self.a_delete, self.a_ripple):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Playback")
        for a in (self.a_play, None, self.a_start, self.a_end, self.a_prev, self.a_next):
            m.addSeparator() if a is None else m.addAction(a)
        self.menu_view = mb.addMenu("&View")
        for a in (self.a_zin, self.a_zout, self.a_fit, self.a_snap, None, self.a_theme):
            self.menu_view.addSeparator() if a is None else self.menu_view.addAction(a)
        self.menu_window = mb.addMenu("&Window")

    def _build_main_toolbar(self):
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(Qt.TopToolBarArea, tb)
        tb.addActions([self.a_open, self.a_save])
        tb.addSeparator()
        tb.addActions([self.a_import])
        tb.addSeparator()
        tb.addActions([self.a_undo, self.a_redo])
        tb.addSeparator()
        tb.addActions([self.a_zin, self.a_zout, self.a_fit])
        sp = QWidget(); sp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(sp)
        self.b_export = QPushButton(" Export")
        self.b_export.setObjectName("primary")
        self.b_export.setIcon(theme.icon("export", 16))
        self.b_export.clicked.connect(self.export_video)
        tb.addWidget(self.b_export)
        tb.addAction(self.a_theme)
        self.main_toolbar = tb

    def _build_tools_bar(self):
        tb = QToolBar("Tools")
        tb.setObjectName("toolbox")
        tb.setOrientation(Qt.Vertical)
        tb.setMovable(False)
        tb.setIconSize(QSize(22, 22))
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(Qt.LeftToolBarArea, tb)
        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for tid, title, key, icon in (("select", "Selection Tool (V)", "V", "move"),
                                      ("razor", "Razor Tool (C) — click a clip to cut", "C", "razor"),
                                      ("hand", "Hand Tool (H) — pan the timeline", "H", "hand"),
                                      ("zoom", "Zoom Tool (Z) — click to zoom, Alt-click to zoom out", "Z", "zoom")):
            a = QAction(self)
            a.setCheckable(True)
            a.setIcon(theme.icon(icon, 20))
            a.setToolTip(title)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(lambda _=False, t=tid: self.select_tool(t))
            self.tool_group.addAction(a)
            tb.addAction(a)
            self.tool_actions[tid] = a
        self.tool_actions["select"].setChecked(True)
        self.tools_bar = tb

    def _build_options_bar(self):
        self.options_bar = QToolBar("Tool Options")
        self.options_bar.setObjectName("options_bar")
        self.options_bar.setMovable(False)
        self.options_bar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBarBreak(Qt.TopToolBarArea)       # options bar on its own row, like Venma
        self.addToolBar(Qt.TopToolBarArea, self.options_bar)
        self._tool_options("select")

    def _tool_options(self, tool):
        ob = self.options_bar
        ob.clear()
        hints = {
            "select": "Select: drag clips to move · drag clip edges to trim · drag from the Media tab to add",
            "razor": "Razor: click a clip to cut it at that point  (S cuts at the playhead)",
            "hand": "Hand: drag to pan the timeline  (or middle-mouse drag)",
            "zoom": "Zoom: click to zoom in · Alt+click to zoom out  (Ctrl+wheel anywhere)",
        }
        lbl = QLabel(hints[tool])
        lbl.setStyleSheet(f"color: {theme.T['muted']}; font-size: 9pt;")
        ob.addWidget(lbl)
        ob.addSeparator()
        cb = QCheckBox("Snapping")
        cb.setChecked(self.a_snap.isChecked())
        cb.toggled.connect(lambda on: (self.a_snap.setChecked(on), self._toggle_snap()))
        ob.addWidget(cb)

    def _build_center(self):
        # preview + transport
        top = QWidget()
        lay = QVBoxLayout(top)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.view, 1)
        bar = QWidget()
        bar.setStyleSheet(f"background: {theme.T['bg']}; border-top: 1px solid {theme.T['border']};")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 6, 14, 6)
        self.tc_label = QLabel("00:00:00:00")
        self.tc_label.setStyleSheet(f"font-family: {theme.MONO}; font-size: 13pt; border: none;")
        self.dur_label = QLabel("/ 00:00:00:00")
        self.dur_label.setStyleSheet(f"font-family: {theme.MONO}; color: {theme.T['muted']}; border: none;")
        row.addWidget(self.tc_label)
        row.addWidget(self.dur_label)
        row.addStretch(1)
        from PyQt5.QtWidgets import QToolButton
        for a in (self.a_start, self.a_prev, self.a_play, self.a_next, self.a_end):
            b = QToolButton()
            b.setDefaultAction(a)
            b.setIconSize(QSize(20, 20))
            b.setStyleSheet("border: none;")
            if a is self.a_play:
                self.play_btn = b
            row.addWidget(b)
        row.addStretch(1)
        self.fmt_label = QLabel("")
        self.fmt_label.setStyleSheet(f"color: {theme.T['muted']}; border: none;")
        row.addWidget(self.fmt_label)
        lay.addWidget(bar)

        split = QSplitter(Qt.Vertical)
        split.addWidget(top)
        split.addWidget(self.timeline)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([520, 300])
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)

    def _build_dock(self):
        self.dock = QDockWidget("Panels", self)
        self.dock.setObjectName("dock_panels")
        self.dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        tabs = QTabWidget()
        tabs.addTab(self.media_bin, theme.icon("film", 16), "Media")
        tabs.addTab(self.props, theme.icon("sliders", 16), "Properties")
        tabs.addTab(self.history_panel, theme.icon("history", 16), "History")
        self.tabs = tabs
        self.dock.setWidget(tabs)
        self.dock.setMinimumWidth(320)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.menu_window.addAction(self.dock.toggleViewAction())
        self.menu_window.addAction(self.tools_bar.toggleViewAction())
        self.menu_window.addAction(self.options_bar.toggleViewAction())

    def _wire(self):
        c = self.timeline.canvas
        c.playheadChanged.connect(self.engine.seek)
        c.clipSelected.connect(self._on_selected)
        c.clipMoved.connect(self._on_clip_moved)
        c.clipTrimmed.connect(self._on_clip_trimmed)
        c.splitRequested.connect(self._on_split_requested)
        c.mediaDropped.connect(self._on_media_dropped)
        self.engine.timeChanged.connect(self._on_time)
        self.engine.playingChanged.connect(self._on_playing)
        self.engine.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        self.media_bin.importRequested.connect(self.import_dialog)
        self.media_bin.addRequested.connect(lambda mid: self.add_to_timeline(mid))
        self.media_bin.removeRequested.connect(self.remove_media)
        self.props.propertyEdited.connect(self._on_property)
        self.props.resetRequested.connect(self._on_reset_props)
        self.history_panel.jumpRequested.connect(self._jump)

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
        self.history_panel.set_states(self.labels, self.index)

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

    def _jump(self, row):
        if 0 <= row < len(self.history) and row != self.index:
            self._restore(row)

    # ── refresh ──────────────────────────────────────────────────────────────
    def _refresh_all(self, rebuild_bin=False):
        self.timeline.canvas.thumbs = self.thumbs
        self.engine.thumbs = self.thumbs
        self.timeline.canvas.set_project(self.project)
        self.engine.set_project(self.project, reset=rebuild_bin)
        self.timeline.canvas.set_playhead(self.engine.t)
        if rebuild_bin or self.media_bin.list.count() != len(self.project.media):
            self.media_bin.populate(self.project, self.thumbs)
        self.history_panel.set_states(self.labels, self.index)
        self.a_undo.setEnabled(self.index > 0)
        self.a_redo.setEnabled(self.index < len(self.history) - 1)
        self._update_clip_panel()
        self._update_labels()
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.setWindowTitle(f"{name}{'*' if self.dirty else ''} — Venma Video")

    def _update_labels(self):
        p = self.project
        self.dur_label.setText("/ " + format_tc(p.duration(), p.fps))
        self.tc_label.setText(format_tc(self.engine.t, p.fps))
        self.fmt_label.setText(f"{p.width}×{p.height} · {p.fps} fps")
        self.statusBar().showMessage(f"{len(p.clips)} clip(s) · {len(p.media)} media file(s)", 4000)

    def _update_clip_panel(self):
        sid = self.timeline.canvas.selected
        clip = self.project.clip(sid) if sid else None
        self.props.set_clip(clip, self.project.media.get(clip.media_id) if clip else None, self.project.fps)

    # ── timeline / engine callbacks ──────────────────────────────────────────
    def _on_time(self, t):
        self.timeline.canvas.set_playhead(t, follow=self.engine.playing)
        self.tc_label.setText(format_tc(t, self.project.fps))

    def _on_playing(self, playing):
        self.a_play.setIcon(theme.icon("pause" if playing else "play", 20))

    def step(self, frames):
        self.engine.pause()
        self.engine.seek(max(0.0, self.engine.t + frames / self.project.fps))

    def _on_selected(self, cid):
        self._update_clip_panel()
        if cid:
            self.tabs.setCurrentWidget(self.props)

    def _on_clip_moved(self, cid, track, start):
        self.project.move(cid, track, start)
        self.commit("Move Clip")

    def _on_clip_trimmed(self, cid, side, edge):
        (self.project.trim_left if side == "left" else self.project.trim_right)(cid, edge)
        self.commit("Trim Clip")

    def _on_split_requested(self, cid, t):
        if self.project.split(cid, t):
            self.commit("Split Clip")

    def _on_media_dropped(self, mid, track, t):
        self.add_to_timeline(mid, track if track >= 0 else None, t)

    def _toggle_snap(self):
        self.timeline.canvas.snap_on = self.a_snap.isChecked()

    def select_tool(self, tool):
        self.timeline.canvas.set_tool(tool)
        self._tool_options(tool)
        self.tool_actions[tool].setChecked(True)

    # ── editing commands ─────────────────────────────────────────────────────
    def add_to_timeline(self, media_id, track=None, t=None):
        m = self.project.media.get(media_id)
        if not m:
            return
        if track is None or not self.project.track_accepts(track, m.kind):
            track = self.project.first_track_for(m.kind)
        if t is None:
            t = self.engine.t
        c = self.project.add_clip(media_id, track, t)
        self.commit("Add Clip")
        self.timeline.canvas.select(c.id)
        # Seek to clip start so preview immediately displays the new video
        self.engine.seek(c.start)

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
        if not sid:
            return
        self.timeline.canvas.select(None)
        self.project.delete([sid], ripple=ripple)
        self.commit("Ripple Delete" if ripple else "Delete Clip")

    def _on_property(self, attr, value):
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c is None:
            return
        setattr(c, attr, value)
        self.dirty = True
        self.engine.refresh()
        self._edit_timer.start()

    def _on_reset_props(self):
        sid = self.timeline.canvas.selected
        c = self.project.clip(sid) if sid else None
        if c:
            for k, v in CLIP_DEFAULTS.items():
                setattr(c, k, v)
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
        w.ready.connect(self._on_media_ready)
        w.finished.connect(lambda w=w: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_media_ready(self, path, info, img, err):
        if info is None:
            QMessageBox.warning(self, "Import failed", f"{os.path.basename(path)}\n\n{err or 'Unsupported file.'}")
            return
        p = self.project
        first = not p.media and not p.clips
        item = MediaItem(p.new_media_id(), path, **info)
        p.add_media(item)
        if img is not None:
            self.thumbs[item.id] = QPixmap.fromImage(img)
        if first and item.kind == "video" and item.width and item.height:
            p.width, p.height = item.width - item.width % 2, item.height - item.height % 2
            p.fps = min((24, 25, 30, 50, 60), key=lambda f: abs(f - (item.fps or 30)))
            self.statusBar().showMessage(f"Project set to match first clip: {p.width}×{p.height} @ {p.fps} fps", 6000)
        self.commit("Import " + item.name)
        self.media_bin.populate(p, self.thumbs)
        self.tabs.setCurrentWidget(self.media_bin)

    # ── project files ────────────────────────────────────────────────────────
    def _confirm_discard(self):
        if not self.dirty or not self.project.clips and not self.project.media:
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
        self.project = Project(*d.values())
        self.path, self.dirty = None, False
        self.thumbs.clear()
        self.engine.t = 0.0
        self._reset_history("New Project")
        self._refresh_all(rebuild_bin=True)

    def project_settings(self):
        p = self.project
        d = ProjectDialog(self, p.width, p.height, p.fps, "Project Settings")
        if d.exec_() == ProjectDialog.Accepted:
            p.width, p.height, p.fps = d.values()
            self.commit("Project Settings")

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
        missing = [m.name for m in proj.media.values() if not os.path.isfile(m.path)]
        if missing:
            QMessageBox.warning(self, "Missing media", "These files could not be found:\n\n" + "\n".join(missing[:15]))
        jobs = [dict(path=m.path, probe=False, kind=m.kind, duration=m.duration)
                for m in proj.media.values() if os.path.isfile(m.path) and m.kind != "audio"]
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
        missing = [p.media[c.media_id].name for c in p.clips if not os.path.isfile(p.media[c.media_id].path)]
        if missing:
            QMessageBox.warning(self, "Missing media", "Can't export — missing files:\n\n" + "\n".join(sorted(set(missing))[:15]))
            return
        try:
            cmd = ft.build_export_command(p, out, fmt, quality)
        except Exception as e:
            QMessageBox.critical(self, "Export", str(e))
            return
        dlg = QProgressDialog("Rendering…", "Cancel", 0, 1000, self)
        dlg.setWindowTitle("Exporting")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        w = ExportWorker(cmd, p.duration(), self)
        w.progress.connect(lambda f: dlg.setValue(int(f * 1000)))
        dlg.canceled.connect(w.cancel)

        def done(ok, msg):
            dlg.close()
            if ok:
                QMessageBox.information(self, "Export", f"Saved to\n{out}")
            elif "cancel" not in msg.lower():
                QMessageBox.critical(self, "Export failed", msg)
        w.finished_ok.connect(done)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()
        dlg.show()

    # ── misc ─────────────────────────────────────────────────────────────────
    def toggle_theme(self):
        theme.set_theme(QApplication.instance(), "dark" if theme.NAME == "light" else "light")
        self.a_theme.setIcon(theme.icon("sun" if theme.NAME == "dark" else "moon", 20))
        for tid, icon in (("select", "move"), ("razor", "razor"), ("hand", "hand"), ("zoom", "zoom")):
            self.tool_actions[tid].setIcon(theme.icon(icon, 20))
        for a, ic in ((self.a_open, "open"), (self.a_save, "save"), (self.a_import, "import"), (self.a_undo, "undo"),
                      (self.a_redo, "redo"), (self.a_zin, "zoom_in"), (self.a_zout, "zoom_out"), (self.a_fit, "fit"),
                      (self.a_start, "skip_back"), (self.a_prev, "step_back"), (self.a_next, "step_fwd"),
                      (self.a_end, "skip_fwd")):
            a.setIcon(theme.icon(ic, 20))
        self._on_playing(self.engine.playing)
        self.b_export.setIcon(theme.icon("export", 16))
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
        self.engine.shutdown()
        for w in list(self._workers):
            if isinstance(w, ExportWorker):
                w.cancel()
            w.wait(2000)
        e.accept()
