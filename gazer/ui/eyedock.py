"""Eye Dock: a focus-free toolbar on the screen edge for eyes-only clicking.

Rest your gaze on a button to choose what your *next* dwell does — right
click, double click, drag, precise zoom-click — or to toggle the keyboard,
dwell itself, or pause. The dock runs its own hover-dwell from the gaze
pointer, so it keeps working even while dwell-click is switched off, and the
engine never dwell-clicks on top of it.
"""

from __future__ import annotations

import sys
import time

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from gazer.config import DockSettings
from gazer.ui import theme

# key, label, glyph, kind ("type" = sets next dwell, "act" = immediate action)
ITEMS = [
    ("left_click", "CLICK", "●", "type"),
    ("double_click", "DOUBLE", "●●", "type"),
    ("right_click", "RIGHT", "◐", "type"),
    ("drag_toggle", "DRAG", "✥", "type"),
    ("zoom_click", "PRECISE", "⊕", "type"),
    ("keyboard_toggle", "KEYS", "⌨", "act"),
    ("dwell_toggle", "DWELL", "◎", "act"),
    ("pause_toggle", "PAUSE", "❚❚", "act"),
]


def _no_activate(widget: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        u = ctypes.windll.user32
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        hwnd = int(widget.winId())
        style = u.GetWindowLongPtrW(hwnd, -20)
        u.SetWindowLongPtrW(hwnd, -20, ctypes.c_ssize_t(style | 0x08000000 | 0x00000008))  # NOACTIVATE|TOPMOST
    except Exception:
        pass


class EyeDock(QWidget):
    chosen = pyqtSignal(str, str)  # action, kind

    PAD = 10
    GAP = 8

    def __init__(self, settings: DockSettings):
        super().__init__(None)
        self.s = settings
        self.setWindowTitle("Gazer Eye Dock")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.current = "left_click"  # active click type
        self.dwell_on = True
        self.paused = False
        self.hover: int | None = None
        self._hover_since = 0.0
        self._armed = True
        self._flash: tuple[int, float] | None = None
        self.progress = 0.0

    # ------------------------------------------------------------ geometry

    def button_rects(self) -> list[QRectF]:
        b = self.s.button_px
        return [QRectF(self.PAD, self.PAD + i * (b + self.GAP), b, b) for i in range(len(ITEMS))]

    def place(self, geo: QRect) -> None:
        b = self.s.button_px
        w = b + 2 * self.PAD
        h = len(ITEMS) * (b + self.GAP) - self.GAP + 2 * self.PAD
        x = geo.x() + (geo.width() - w - 6 if self.s.side == "right" else 6)
        y = geo.y() + (geo.height() - h) // 2
        self.setGeometry(x, y, w, h)

    def showEvent(self, e):
        super().showEvent(e)
        _no_activate(self)

    # --------------------------------------------------------------- input

    def set_state(self, dwell_next: str | None, dwell_on: bool, paused: bool) -> None:
        cur = dwell_next or "left_click"
        if (cur, dwell_on, paused) != (self.current, self.dwell_on, self.paused):
            self.current, self.dwell_on, self.paused = cur, dwell_on, paused
            self.update()

    def on_pointer(self, local: QPointF | None, t: float | None = None) -> None:
        """Gaze pointer in this widget's coordinates (None = elsewhere)."""
        t = time.perf_counter() if t is None else t
        idx = None
        if local is not None:
            for i, r in enumerate(self.button_rects()):
                if r.adjusted(-4, -4, 4, 4).contains(local):
                    idx = i
        if idx != self.hover:
            self.hover = idx
            self._hover_since = t
            self._armed = True
            self.progress = 0.0
            self.update()
            return
        if idx is None or not self._armed:
            return
        self.progress = min(1.0, (t - self._hover_since) / (self.s.dwell_ms / 1000))
        if self.progress >= 1.0:
            self._armed = False  # look away and back to choose again
            self.progress = 0.0
            self._activate(idx, t)
        self.update()

    def mousePressEvent(self, e) -> None:  # physical mouse / any Gazer click
        for i, r in enumerate(self.button_rects()):
            if r.contains(e.position()):
                self._activate(i, time.perf_counter())
                return

    def _activate(self, idx: int, t: float) -> None:
        key, _, _, kind = ITEMS[idx]
        self._flash = (idx, t)
        if kind == "type":
            self.current = key
        self.chosen.emit(key, kind)
        self.update()

    # ------------------------------------------------------------- painting

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # glass slab
        slab = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(62, 232, 216, 90), 1))
        p.setBrush(QColor(3, 8, 13, 215))
        p.drawRoundedRect(slab, 6, 6)
        now = time.perf_counter()
        for i, (r, (key, label, glyph, kind)) in enumerate(zip(self.button_rects(), ITEMS)):
            active = kind == "type" and key == self.current
            if key == "dwell_toggle":
                active = self.dwell_on
            if key == "pause_toggle":
                active = self.paused
            hover = self.hover == i
            accent = QColor(theme.WARN) if key == "pause_toggle" and self.paused else QColor(theme.ACCENT)
            bg = QColor(accent)
            bg.setAlpha(70 if active else (40 if hover else 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(8, 17, 26, 235))
            p.drawRoundedRect(r, 4, 4)
            p.setBrush(bg)
            p.drawRoundedRect(r, 4, 4)
            edge = QColor(accent)
            edge.setAlpha(255 if (active or hover) else 70)
            p.setPen(QPen(edge, 2 if active else 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 4, 4)
            if hover and self.progress > 0:
                # dwell progress: a bright bar sweeping up the button's left edge + outline trace
                fill = QRectF(r.x() + 2, r.bottom() - 2 - (r.height() - 4) * self.progress, 4, (r.height() - 4) * self.progress)
                p.fillRect(fill, QColor(240, 255, 253))
            if self._flash and self._flash[0] == i and now - self._flash[1] < 0.4:
                k = (now - self._flash[1]) / 0.4
                f = QColor(accent)
                f.setAlphaF(0.6 * (1 - k))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(f)
                p.drawRoundedRect(r, 4, 4)
                self.update()
            txt = QColor(theme.TEXT) if (active or hover) else QColor(theme.MUTED)
            p.setPen(txt)
            gf = QFont("Segoe UI Symbol")
            gf.setPointSizeF(self.s.button_px * 0.2)
            p.setFont(gf)
            p.drawText(QRectF(r.x(), r.y() + r.height() * 0.12, r.width(), r.height() * 0.5),
                       Qt.AlignmentFlag.AlignCenter, glyph)
            lf = QFont("Segoe UI")
            lf.setPointSizeF(max(7.0, self.s.button_px * 0.095))
            lf.setBold(True)
            lf.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 115)
            p.setFont(lf)
            text = label if key != "dwell_toggle" else ("DWELL ON" if self.dwell_on else "DWELL OFF")
            if key == "pause_toggle" and self.paused:
                text = "RESUME"
            p.drawText(QRectF(r.x(), r.y() + r.height() * 0.6, r.width(), r.height() * 0.3),
                       Qt.AlignmentFlag.AlignCenter, text)
        p.end()
