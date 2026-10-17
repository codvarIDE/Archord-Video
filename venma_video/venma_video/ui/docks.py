"""Reusable panel pieces: a collapsible section for the Details panel and a thin vertical separator."""
from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtWidgets import QFrame, QToolButton, QVBoxLayout, QWidget, QSizePolicy

from . import theme


def vsep():
    """1px vertical divider used between button groups in the toolbars."""
    f = QFrame()
    f.setObjectName("vsep")
    f.setFixedSize(1, 18)
    return f


class CollapsibleSection(QFrame):
    """A titled, flat section whose body can be folded away."""

    toggled = pyqtSignal(bool)

    def __init__(self, title, icon_name=None, expanded=True, parent=None):
        super().__init__(parent)
        self.setObjectName("section")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.head = QToolButton()
        self.head.setObjectName("sectionHead")
        self.head.setText("  " + title)
        self.head.setCheckable(True)
        self.head.setChecked(expanded)
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.head.setIconSize(QSize(11, 11))
        self.head.clicked.connect(self._toggle)
        outer.addWidget(self.head)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(2, 0, 2, 12)
        self.body_lay.setSpacing(6)
        outer.addWidget(self.body)
        self.body.setVisible(expanded)
        self._sync_arrow()

    def _sync_arrow(self):
        self.head.setIcon(theme.icon("chevron_down" if self.head.isChecked() else "chevron_right", 11))

    def _toggle(self, on):
        self.body.setVisible(on)
        self._sync_arrow()
        self.toggled.emit(on)

    def add(self, widget):
        self.body_lay.addWidget(widget)
        return widget

    def add_layout(self, layout):
        self.body_lay.addLayout(layout)
        return layout

    def retint(self):
        self._sync_arrow()
