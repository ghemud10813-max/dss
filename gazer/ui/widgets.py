"""Reusable widgets: cards, labelled sliders, meters, camera preview."""

from __future__ import annotations

from typing import Callable

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QVBoxLayout, QWidget,
)

from gazer.core.features import PREVIEW_CONTOURS
from gazer.ui import theme


class Card(QFrame):
    def __init__(self, title: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(10)
        if title:
            t = QLabel(title)
            t.setObjectName("CardTitle")
            self.body.addWidget(t)
        if subtitle:
            s = muted(subtitle)
            s.setWordWrap(True)
            self.body.addWidget(s)

    def add(self, w: QWidget | None = None, layout=None) -> None:
        if w is not None:
            self.body.addWidget(w)
        if layout is not None:
            self.body.addLayout(layout)


def muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setWordWrap(True)
    return lbl


def page(title: str, subtitle: str = "") -> tuple[QScrollArea, QVBoxLayout]:
    """A scrollable page with a heading; returns (widget, content layout)."""
    inner = QWidget()
    inner.setObjectName("Page")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(28, 24, 28, 28)
    lay.setSpacing(14)
    h = QLabel(title)
    h.setObjectName("PageTitle")
    lay.addWidget(h)
    if subtitle:
        lay.addWidget(muted(subtitle))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    return scroll, lay


class SliderRow(QWidget):
    """Label · slider · value, mapping an int slider onto a float range."""

    changed = pyqtSignal(float)

    def __init__(self, label: str, lo: float, hi: float, value: float, step: float = 0.01,
                 fmt: str = "{:.2f}", tip: str = "", on_change: Callable[[float], None] | None = None):
        super().__init__()
        self.lo, self.step, self.fmt = lo, step, fmt
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(label)
        self.label.setMinimumWidth(170)
        if tip:
            self.label.setToolTip(tip)
            self.setToolTip(tip)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, int(round((hi - lo) / step)))
        self.value_lbl = QLabel()
        self.value_lbl.setMinimumWidth(64)
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.label)
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_lbl)
        self.set_value(value)
        self.slider.valueChanged.connect(self._on)
        if on_change:
            self.changed.connect(on_change)

    def value(self) -> float:
        return self.lo + self.slider.value() * self.step

    def set_value(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((v - self.lo) / self.step)))
        self.slider.blockSignals(False)
        self.value_lbl.setText(self.fmt.format(self.value()))

    def _on(self, _):
        v = self.value()
        self.value_lbl.setText(self.fmt.format(v))
        self.changed.emit(v)


def toggle(text: str, checked: bool, on_change: Callable[[bool], None], tip: str = "") -> QCheckBox:
    cb = QCheckBox(text)
    cb.setChecked(checked)
    cb.toggled.connect(on_change)
    if tip:
        cb.setToolTip(tip)
    return cb


class Segmented(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, options: dict[str, str], current: str):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, label in options.items():
            b = QPushButton(label)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            row.addWidget(b)
            self.buttons[key] = b
        self.set_current(current)

    def set_current(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)


class Meter(QWidget):
    """Horizontal bar showing a live value with a threshold marker."""

    def __init__(self, threshold: float = 0.5):
        super().__init__()
        self.value = 0.0
        self.threshold = threshold
        self.active = False
        self.setMinimumHeight(14)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: float, active: bool = False) -> None:
        if abs(value - self.value) > 0.005 or active != self.active:
            self.value, self.active = value, active
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 3, self.width(), self.height() - 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SURFACE_3))
        p.drawRoundedRect(r, 4, 4)
        fill = QRectF(r.x(), r.y(), r.width() * max(0.0, min(self.value, 1.0)), r.height())
        p.setBrush(QColor(theme.OK if self.active else theme.ACCENT_2))
        p.drawRoundedRect(fill, 4, 4)
        x = r.x() + r.width() * self.threshold
        p.setPen(QPen(QColor(theme.TEXT), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.end()


class StatusPill(QLabel):
    def __init__(self, text: str = ""):
        super().__init__(text)
        self.set_state(text, "muted")

    def set_state(self, text: str, state: str) -> None:
        color = {"ok": theme.OK, "warn": theme.WARN, "bad": theme.DANGER, "accent": theme.ACCENT}.get(state, theme.MUTED)
        self.setText(f"● {text}")
        self.setStyleSheet(
            f"QLabel {{ color: {color}; background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER};"
            f" border-radius: 11px; padding: 3px 10px; font-size: 12px; }}")


class CameraPreview(QWidget):
    """Mirrored webcam view with face mesh, iris and head-direction overlay."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 240)
        self._img: QImage | None = None
        self._pts: np.ndarray | None = None
        self._face = False
        self._head = (0.0, 0.0, 0.0)
        self.show_mesh = True
        self.message = "Starting camera…"

    def set_frame(self, bgr: np.ndarray | None, pts: np.ndarray | None, face: bool, head) -> None:
        if bgr is not None:
            h, w = bgr.shape[:2]
            rgb = np.ascontiguousarray(bgr[:, :, ::-1])
            self._img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            self.message = ""
        self._pts, self._face, self._head = pts, face, head
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#07090c"))
        if self._img is None:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            p.end()
            return
        iw, ih = self._img.width(), self._img.height()
        s = min(self.width() / iw, self.height() / ih)
        ox, oy = (self.width() - iw * s) / 2, (self.height() - ih * s) / 2
        p.drawImage(QRectF(ox, oy, iw * s, ih * s), self._img)
        if self._pts is not None and self.show_mesh:
            pts = self._pts * s + np.array([ox, oy])
            colors = {"face": QColor(255, 255, 255, 70), "eyes": QColor(theme.ACCENT),
                      "lips": QColor(theme.ACCENT_2), "iris": QColor(theme.OK), "nose": QColor(theme.WARN)}
            for group, idx in PREVIEW_CONTOURS.items():
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(colors[group])
                rad = 2.6 if group in ("iris", "nose") else 1.6
                for i in idx:
                    if i < len(pts):
                        p.drawEllipse(QPointF(*pts[i]), rad, rad)
            nose = pts[1]
            yaw, pitch, _ = self._head
            end = nose + np.array([yaw * 120 * s, (pitch - 0.5) * 160 * s])
            p.setPen(QPen(QColor(theme.WARN), 2))
            p.drawLine(QPointF(*nose), QPointF(*end))
        if not self._face:
            p.setPen(QColor(theme.WARN))
            f = QFont(p.font())
            f.setPointSize(12)
            f.setBold(True)
            p.setFont(f)
            p.drawText(self.rect().adjusted(0, 0, 0, -12), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                       "No face — look at the camera")
        p.end()


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {theme.BORDER};")
    return f
