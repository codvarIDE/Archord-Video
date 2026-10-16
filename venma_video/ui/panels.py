"""Side panels and dialogs: Media bin, Clip properties, History, New project, Export."""
import os

from PyQt5.QtCore import Qt, QSize, QMimeData, pyqtSignal, QByteArray
from PyQt5.QtGui import QPixmap, QPainter, QColor, QIcon
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton, QListWidget, QListWidgetItem,
    QAbstractItemView, QComboBox, QSpinBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QFileDialog, QFrame, QGridLayout,
)

from . import theme
from ..core import ffmpeg_tools as ft
from ..core.project import PRESETS
from .timeline import MIME_MEDIA, format_tc

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
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setSpacing(4)

    def mimeData(self, items):
        md = QMimeData()
        if items:
            md.setData(MIME_MEDIA, QByteArray(items[0].data(Qt.UserRole).encode("utf-8")))
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
        lay.setContentsMargins(10, 10, 10, 10)
        row = QHBoxLayout()
        self.b_import = QPushButton(" Import")
        self.b_import.setIcon(theme.icon("import", 16))
        self.b_import.clicked.connect(self.importRequested.emit)
        self.b_add = QPushButton(" Add to timeline")
        self.b_add.setObjectName("primary")
        self.b_add.clicked.connect(self._add)
        self.b_del = QPushButton()
        self.b_del.setIcon(theme.icon("trash", 16))
        self.b_del.setToolTip("Remove from project")
        self.b_del.clicked.connect(self._remove)
        for b in (self.b_import, self.b_add, self.b_del):
            row.addWidget(b)
        lay.addLayout(row)
        self.list = MediaList()
        self.list.itemDoubleClicked.connect(lambda it: self.addRequested.emit(it.data(Qt.UserRole)))
        lay.addWidget(self.list, 1)
        self.hint = QLabel("Import clips, then drag them onto the timeline\n(or double-click to append at the playhead).")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.T['muted']}; font-size: 9pt;")
        lay.addWidget(self.hint)

    def _sel(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _add(self):
        if self._sel():
            self.addRequested.emit(self._sel())

    def _remove(self):
        if self._sel():
            self.removeRequested.emit(self._sel())

    def clear(self):
        self.list.clear()

    def add_item(self, media, pix=None):
        it = QListWidgetItem(QIcon(pix or placeholder_tile(media.kind)), f"{media.name}\n{_fmt_dur(media.duration) if media.kind != 'image' else 'image'}")
        it.setData(Qt.UserRole, media.id)
        it.setToolTip(f"{media.path}\n{media.width}×{media.height}" if media.width else media.path)
        it.setTextAlignment(Qt.AlignHCenter)
        self.list.addItem(it)
        self.hint.setVisible(self.list.count() == 0)

    def set_thumb(self, media_id, pix):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == media_id:
                it.setIcon(QIcon(fit_tile(pix)))

    def populate(self, project, thumbs):
        self.clear()
        for m in project.media.values():
            th = thumbs.get(m.id)
            self.add_item(m, fit_tile(th) if th is not None else None)
        self.hint.setVisible(self.list.count() == 0)


# ── Clip properties ──────────────────────────────────────────────────────────
class _SliderRow(QWidget):
    changed = pyqtSignal(float)

    def __init__(self, label, lo, hi, default, unit="%", parent=None):
        super().__init__(parent)
        self.default, self.unit = default, unit
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        self.name = QLabel(label)
        self.val = QLabel()
        self.val.setAlignment(Qt.AlignRight)
        self.val.setStyleSheet(f"color: {theme.T['muted']};")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        lay.addWidget(self.name, 0, 0)
        lay.addWidget(self.val, 0, 1)
        lay.addWidget(self.slider, 1, 0, 1, 2)
        self.slider.valueChanged.connect(self._moved)
        self.slider.mouseDoubleClickEvent = lambda e: self.slider.setValue(self.default)

    def _moved(self, v):
        self.val.setText(f"{v}{self.unit}")
        self.changed.emit(float(v))

    def set_value(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v)))
        self.slider.blockSignals(False)
        self.val.setText(f"{int(round(v))}{self.unit}")


class PropertiesPanel(QWidget):
    propertyEdited = pyqtSignal(str, float)    # clip attribute, value
    resetRequested = pyqtSignal()

    # attribute -> (label, lo, hi, default, scale between slider value and attribute)
    SPEC = [
        ("volume", "Volume", 0, 200, 100, 100.0),
        ("opacity", "Opacity", 0, 100, 100, 100.0),
        ("brightness", "Brightness", -100, 100, 0, 100.0),
        ("contrast", "Contrast", 0, 200, 100, 100.0),
        ("saturation", "Saturation", 0, 300, 100, 100.0),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.title = QLabel("No clip selected")
        self.title.setStyleSheet(f"font-family: {theme.SERIF}; font-size: 13pt;")
        self.title.setWordWrap(True)
        self.info = QLabel("")
        self.info.setStyleSheet(f"color: {theme.T['muted']}; font-size: 9pt;")
        self.info.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.info)
        line = QFrame(); line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {theme.T['border']};")
        lay.addWidget(line)
        self.rows = {}
        for attr, label, lo, hi, default, scale in self.SPEC:
            r = _SliderRow(label, lo, hi, default)
            r.changed.connect(lambda v, a=attr, s=scale: self.propertyEdited.emit(a, v / s))
            self.rows[attr] = (r, scale)
            lay.addWidget(r)
        self.b_reset = QPushButton("Reset effects")
        self.b_reset.clicked.connect(self.resetRequested.emit)
        lay.addWidget(self.b_reset)
        lay.addStretch(1)
        self.set_clip(None, None, 30)

    def set_clip(self, clip, media, fps):
        on = clip is not None
        for attr, (r, scale) in self.rows.items():
            r.setEnabled(on)
            if on:
                r.set_value(getattr(clip, attr) * scale)
        self.b_reset.setEnabled(on)
        if not on:
            self.title.setText("No clip selected")
            self.info.setText("Click a clip in the timeline to edit its properties.")
            return
        self.title.setText(media.name)
        self.info.setText(f"Start  {format_tc(clip.start, fps)}\nLength  {format_tc(clip.duration, fps)}\n"
                          f"Source in  {format_tc(clip.in_point, fps)}")
        has_video = media.kind in ("video", "image")
        for attr in ("opacity", "brightness", "contrast", "saturation"):
            self.rows[attr][0].setEnabled(has_video)
        self.rows["volume"][0].setEnabled(media.kind == "audio" or media.has_audio)


class HistoryPanel(QWidget):
    jumpRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._row)
        lay.addWidget(self.list)
        self._block = False

    def _row(self, r):
        if not self._block and r >= 0:
            self.jumpRequested.emit(r)

    def set_states(self, labels, index):
        self._block = True
        self.list.clear()
        for i, l in enumerate(labels):
            self.list.addItem(f"{i}. {l}")
        self.list.setCurrentRow(index)
        self._block = False


# ── Dialogs ──────────────────────────────────────────────────────────────────
class ProjectDialog(QDialog):
    def __init__(self, parent=None, width=1920, height=1080, fps=30, title="New Project"):
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
        row = QHBoxLayout(); row.addWidget(self.w); row.addWidget(QLabel("×")); row.addWidget(self.h)
        form.addRow("Size", row)
        form.addRow("Frame rate", self.fps)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)
        theme.tidy(self)

    def _preset(self, i):
        d = self.preset.itemData(i)
        if d:
            self.w.setValue(d[0]); self.h.setValue(d[1])

    def values(self):
        return self.w.value(), self.h.value(), self.fps.currentData()


class ExportDialog(QDialog):
    def __init__(self, project, default_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Video")
        form = QFormLayout(self)
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
        form.addRow("Format", self.fmt)
        form.addRow("Quality", self.quality)
        form.addRow("Save to", row)
        info = QLabel(f"{project.width} × {project.height}  ·  {project.fps} fps  ·  "
                      f"{format_tc(project.duration(), project.fps)}")
        info.setStyleSheet(f"color: {theme.T['muted']};")
        form.addRow("Project", info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.fmt.currentTextChanged.connect(self._fix_ext)
        theme.tidy(self)
        self.setMinimumWidth(460)

    def _fix_ext(self):
        base, _ = os.path.splitext(self.path.text())
        self.path.setText(base + ft.FORMATS[self.fmt.currentText()]["ext"])

    def _browse(self):
        ext = ft.FORMATS[self.fmt.currentText()]["ext"]
        p, _ = QFileDialog.getSaveFileName(self, "Export to", self.path.text(), f"Video (*{ext})")
        if p:
            self.path.setText(p if p.lower().endswith(ext) else p + ext)

    def values(self):
        return self.path.text().strip(), self.fmt.currentText(), self.quality.currentText()
