"""Draws title clips with Qt.

One painter function serves both the live preview (drawn over the video frame at paint time) and the
export (each title is rendered to a transparent, project-sized PNG that FFmpeg overlays). Using the
same code for both means what you see in the preview is what you get in the exported file, and the
export needs neither FFmpeg's ``drawtext`` filter nor font files on disk.

Sizes are defined for a 1080-pixel-high frame and scale with the frame height.
"""
import math
import os

from PyQt5.QtCore import Qt, QRect, QRectF
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter

REFERENCE_HEIGHT = 1080.0
_ALIGN = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}


def _font(title, px):
    f = QFont()
    if title.get("font"):
        f.setFamily(title["font"])
    f.setPixelSize(px)
    f.setBold(bool(title.get("bold")))
    f.setItalic(bool(title.get("italic")))
    return f


def layout(title, frame):
    """Work out where a title goes inside ``frame`` (QRectF).

    Returns ``(font, text_rect, box_rect, flags, pad, px)`` or ``None`` if there is nothing to draw.
    ``box_rect`` is the full background rectangle; ``text_rect`` is the area the text is drawn into.
    """
    text = title.get("text", "")
    if not text.strip():
        return None
    px = max(6, int(round(float(title.get("size", 96)) * frame.height() / REFERENCE_HEIGHT)))
    font = _font(title, px)
    fm = QFontMetrics(font)
    pad = int(px * 0.35) if title.get("box") else 0
    flags = int(Qt.TextWordWrap | _ALIGN.get(title.get("align", "center"), Qt.AlignHCenter))
    max_w = max(10, int(frame.width() * 0.92) - 2 * pad)
    br = fm.boundingRect(QRect(0, 0, max_w, 100000), flags, text)
    w, h = br.width() + 2 * pad + 2, br.height() + 2 * pad
    cx = frame.left() + float(title.get("pos_x", 0.5)) * frame.width()
    cy = frame.top() + float(title.get("pos_y", 0.5)) * frame.height()
    # keep the whole box inside the frame
    x = min(max(cx - w / 2.0, frame.left()), max(frame.left(), frame.right() - w))
    y = min(max(cy - h / 2.0, frame.top()), max(frame.top(), frame.bottom() - h))
    box = QRectF(x, y, w, h)
    text_rect = QRectF(x + pad, y + pad, w - 2 * pad, h - 2 * pad)
    return font, text_rect, box, flags, pad, px


def draw_title(p, frame, title, opacity=1.0):
    """Paint one title into ``frame`` (a QRectF in the painter's coordinates)."""
    lay = layout(title, frame)
    if lay is None:
        return
    font, text_rect, box, flags, pad, px = lay
    text = title["text"]
    p.save()
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    p.setOpacity(p.opacity() * max(0.0, min(1.0, opacity)))
    p.setFont(font)
    if title.get("box"):
        c = QColor(title.get("box_color", "#000000"))
        c.setAlphaF(max(0.0, min(1.0, float(title.get("box_opacity", 0.55)))))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(box, pad * 0.6, pad * 0.6)
    if title.get("stroke"):
        r = max(1.0, px * 0.05)
        p.setPen(QColor(0, 0, 0, 255))
        for k in range(16):                              # ring of offsets gives an even outline
            a = 2 * math.pi * k / 16
            p.drawText(text_rect.translated(r * math.cos(a), r * math.sin(a)), flags, text)
    p.setPen(QColor(title.get("color", "#FFFFFF")))
    p.drawText(text_rect, flags, text)
    p.restore()


def draw_titles(p, frame, clips):
    """Paint every title clip in ``clips`` (already ordered bottom to top)."""
    for c in clips:
        draw_title(p, frame, c.title, c.opacity)


def render_title_png(path, width, height, title):
    """Render a title to a transparent ``width`` x ``height`` PNG (opacity is applied later by ffmpeg)."""
    img = QImage(int(width), int(height), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    draw_title(p, QRectF(0, 0, width, height), title, 1.0)
    p.end()
    if not img.save(path, "PNG"):
        raise RuntimeError(f"Could not write {os.path.basename(path)}")
    return path


def render_project_titles(project, folder):
    """Render every title in the project into ``folder``. Returns {clip id: png path}."""
    out = {}
    for c in project.clips:
        if c.title is not None:
            out[c.id] = render_title_png(os.path.join(folder, f"title_{c.id}.png"),
                                         project.width - project.width % 2, project.height - project.height % 2,
                                         c.title)
    return out
