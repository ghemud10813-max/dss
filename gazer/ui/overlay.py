"""Always-on-top, click-through overlay: cursor ring, dwell, wheel, zoom, toasts."""

from __future__ import annotations

import math
import sys
import time

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PyQt6.QtWidgets import QWidget

from gazer.config import ACTIONS, OverlaySettings
from gazer.core.engine import Snapshot
from gazer.ui import theme
from gazer.ui.bridge import ScreenMapper

WHEEL_ICONS = {
    "left_click": "Click", "double_click": "Double", "right_click": "Right", "middle_click": "Middle",
    "drag_toggle": "Drag", "scroll_mode": "Scroll", "zoom": "Zoom", "keyboard_toggle": "Keys",
    "recenter": "Center", "precision_toggle": "Precise", "pause_toggle": "Pause", "mode_cycle": "Mode",
}


def _exclude_from_capture(widget: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        WDA_EXCLUDEFROMCAPTURE = 0x11
        ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass


class Overlay(QWidget):
    def __init__(self, mapper: ScreenMapper, settings: OverlaySettings):
        super().__init__(None)
        self.mapper = mapper
        self.s = settings
        self.snap: Snapshot | None = None
        self.ripples: list[tuple[QPointF, float]] = []
        self.toast: tuple[str, float] | None = None
        self.zoom_pixmap: QPixmap | None = None
        self.zoom_id = -1
        self._dirty_prev: QRect = QRect()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(mapper.geo)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, e):
        super().showEvent(e)
        _exclude_from_capture(self)
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def set_mapper(self, mapper: ScreenMapper) -> None:
        self.mapper = mapper
        self.setGeometry(mapper.geo)

    # ------------------------------------------------------------ inputs

    def on_snapshot(self, snap: Snapshot) -> None:
        self.snap = snap
        for ev in snap.events:
            if ev[0] == "click" and self.s.click_ripple:
                self.ripples.append((self.mapper.to_local(*ev[1]), time.perf_counter()))
            elif ev[0] == "toast" and self.s.show_toasts:
                self.toast = (ev[1], time.perf_counter())
        if snap.zoom is not None and snap.zoom.id != self.zoom_id:
            self.zoom_id = snap.zoom.id
            self._grab_zoom(snap.zoom.region)
        elif snap.zoom is None:
            self.zoom_pixmap = None
            self.zoom_id = -1

    def _grab_zoom(self, region) -> None:
        r = self.mapper.rect_to_local(region)
        self.zoom_pixmap = self.mapper.qscreen.grabWindow(
            0, int(r.x()), int(r.y()), max(1, int(r.width())), max(1, int(r.height())))

    # ------------------------------------------------------------ drawing

    def _cursor_local(self) -> QPointF:
        g = QCursor.pos()
        return QPointF(g.x() - self.mapper.geo.x(), g.y() - self.mapper.geo.y())

    def _items_rect(self) -> QRect:
        """Bounding box of everything drawn this frame (for partial repaint)."""
        rects: list[QRectF] = []
        snap = self.snap
        if snap and snap.control:
            c = self._cursor_local()
            rects.append(QRectF(c.x() - 40, c.y() - 40, 80, 80))
            if snap.gaze is not None and self.s.show_gaze_dot:
                g = self.mapper.to_local(*snap.gaze)
                rects.append(QRectF(g.x() - 20, g.y() - 20, 40, 40))
            if snap.pointer is not None:
                pp = self.mapper.to_local(*snap.pointer)
                rects.append(QRectF(pp.x() - 30, pp.y() - 30, 60, 60))
            if snap.wheel is not None:
                w = snap.wheel
                cc = self.mapper.to_local(*w.center)
                rr = self.mapper.len_to_local(w.radius) + 30
                rects.append(QRectF(cc.x() - rr, cc.y() - rr, 2 * rr, 2 * rr))
            if snap.zoom is not None:
                rects.append(self.mapper.rect_to_local(snap.zoom.lens).adjusted(-6, -6, 6, 6))
            if snap.scrolling:
                rects.append(QRectF(c.x() - 90, c.y() - 90, 180, 180))
        for pos, _ in self.ripples:
            rects.append(QRectF(pos.x() - 50, pos.y() - 50, 100, 100))
        rects.append(self._badge_rect())
        if self.toast:
            rects.append(self._toast_rect())
        out = QRect()
        for r in rects:
            out = out.united(r.toAlignedRect())
        return out

    def _badge_rect(self) -> QRectF:
        return QRectF(self.width() - 320, 10, 310, 40)

    def _toast_rect(self) -> QRectF:
        return QRectF(self.width() / 2 - 260, self.height() - 120, 520, 60)

    def _tick(self) -> None:
        now = time.perf_counter()
        self.ripples = [(p, t0) for p, t0 in self.ripples if now - t0 < 0.45]
        if self.toast and now - self.toast[1] > 2.2:
            self.toast = None
        r = self._items_rect()
        dirty = r.united(self._dirty_prev)
        self._dirty_prev = r
        if not dirty.isEmpty():
            self.update(QRegion(dirty))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        snap = self.snap
        accent = QColor(self.s.accent)
        if snap and snap.control and not snap.calibrating:
            if snap.zoom is not None:
                self._paint_zoom(p, snap)
            c = self._cursor_local()
            if snap.gaze is not None and self.s.show_gaze_dot:
                g = self.mapper.to_local(*snap.gaze)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(139, 123, 255, 110))
                p.drawEllipse(g, 9, 9)
            if snap.wheel is None and not snap.paused:
                if self.s.show_cursor_ring:
                    col = QColor(theme.WARN) if snap.precision else QColor(accent)
                    col.setAlpha(170)
                    p.setPen(QPen(col, 2.2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(c, 18, 18)
                    if snap.dragging:
                        p.setBrush(QColor(255, 180, 84, 90))
                        p.drawEllipse(c, 10, 10)
                if snap.dwell_enabled and snap.dwell_progress > 0.02 and self.s.show_dwell_ring:
                    p.setPen(QPen(QColor(theme.TEXT), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    rect = QRectF(c.x() - 24, c.y() - 24, 48, 48)
                    p.drawArc(rect, 90 * 16, -int(360 * 16 * snap.dwell_progress))
            if snap.scrolling:
                self._paint_scroll(p, c, snap)
            if snap.wheel is not None:
                self._paint_wheel(p, snap)
        now = time.perf_counter()
        for pos, t0 in self.ripples:
            k = (now - t0) / 0.45
            col = QColor(accent)
            col.setAlphaF(max(0.0, 0.8 * (1 - k)))
            p.setPen(QPen(col, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(pos, 10 + 36 * k, 10 + 36 * k)
        self._paint_badges(p, snap)
        if self.toast:
            self._paint_toast(p, self.toast[0], now - self.toast[1])
        p.end()

    def _paint_scroll(self, p: QPainter, c: QPointF, snap: Snapshot) -> None:
        p.setPen(QPen(QColor(theme.ACCENT_2), 2))
        p.setBrush(QColor(20, 24, 32, 150))
        p.drawEllipse(c, 34, 34)
        vy, vx = snap.scroll_rate
        mag = math.hypot(vx, vy)
        p.setPen(QPen(QColor(theme.TEXT), 3, cap=Qt.PenCapStyle.RoundCap))
        if mag > 1:
            dx, dy = vx / mag, -vy / mag
            L = 12 + min(mag / 60, 60)
            end = QPointF(c.x() + dx * L, c.y() + dy * L)
            p.drawLine(c, end)
            p.setBrush(QColor(theme.TEXT))
            p.drawEllipse(end, 4, 4)
        else:
            for sgn in (-1, 1):
                p.drawLine(QPointF(c.x() - 7, c.y() + sgn * 12), QPointF(c.x(), c.y() + sgn * 19))
                p.drawLine(QPointF(c.x() + 7, c.y() + sgn * 12), QPointF(c.x(), c.y() + sgn * 19))

    def _paint_wheel(self, p: QPainter, snap: Snapshot) -> None:
        w = snap.wheel
        c = self.mapper.to_local(*w.center)
        R = self.mapper.len_to_local(w.radius)
        r0 = self.mapper.len_to_local(w.inner)
        n = max(len(w.items), 1)
        span = 360.0 / n
        font = QFont(p.font())
        font.setPointSize(10)
        font.setBold(True)
        p.setFont(font)
        for i, item in enumerate(w.items):
            start = 90 + span / 2 - i * span  # Qt: 0° = 3 o'clock, CCW positive
            path = QPainterPath()
            outer = QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R)
            inner = QRectF(c.x() - r0, c.y() - r0, 2 * r0, 2 * r0)
            path.arcMoveTo(outer, start)
            path.arcTo(outer, start, -span)
            path.arcTo(inner, start - span, span)
            path.closeSubpath()
            hover = w.hover == i
            p.setPen(QPen(QColor(theme.BORDER), 1.5))
            p.setBrush(QColor(61, 214, 198, 200) if hover else QColor(22, 26, 33, 225))
            p.drawPath(path)
            if hover and w.progress > 0:
                prog = QPainterPath()
                pr = r0 + (R - r0) * w.progress
                rr = QRectF(c.x() - pr, c.y() - pr, 2 * pr, 2 * pr)
                prog.arcMoveTo(inner, start)
                prog.arcTo(rr, start, -span)
                prog.arcTo(inner, start - span, span)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 60))
                p.drawPath(prog)
            mid = math.radians(start - span / 2)
            lr = (R + r0) / 2
            lp = QPointF(c.x() + lr * math.cos(mid), c.y() - lr * math.sin(mid))
            p.setPen(QColor("#06201d") if hover else QColor(theme.TEXT))
            label = WHEEL_ICONS.get(item, ACTIONS.get(item, item)[:8])
            p.drawText(QRectF(lp.x() - 45, lp.y() - 12, 90, 24), Qt.AlignmentFlag.AlignCenter, label)
        p.setPen(QPen(QColor(theme.BORDER), 1.5))
        p.setBrush(QColor(14, 17, 22, 230))
        p.drawEllipse(c, r0 - 3, r0 - 3)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(c.x() - r0, c.y() - 10, 2 * r0, 20), Qt.AlignmentFlag.AlignCenter, "cancel")
        ptr = self.mapper.to_local(*w.pointer)
        p.setPen(QPen(QColor(theme.WARN), 2))
        p.setBrush(QColor(255, 180, 84, 120))
        p.drawEllipse(ptr, 7, 7)

    def _paint_zoom(self, p: QPainter, snap: Snapshot) -> None:
        z = snap.zoom
        lens = self.mapper.rect_to_local(z.lens)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawRoundedRect(lens.adjusted(-5, -5, 5, 5), 10, 10)
        if self.zoom_pixmap is not None:
            p.drawPixmap(lens.toRect(), self.zoom_pixmap)
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(lens, 8, 8)
        ptr = self.mapper.to_local(*z.pointer)
        p.setPen(QPen(QColor(theme.DANGER), 2))
        p.drawLine(QPointF(ptr.x() - 16, ptr.y()), QPointF(ptr.x() - 4, ptr.y()))
        p.drawLine(QPointF(ptr.x() + 4, ptr.y()), QPointF(ptr.x() + 16, ptr.y()))
        p.drawLine(QPointF(ptr.x(), ptr.y() - 16), QPointF(ptr.x(), ptr.y() - 4))
        p.drawLine(QPointF(ptr.x(), ptr.y() + 4), QPointF(ptr.x(), ptr.y() + 16))

    def _paint_badges(self, p: QPainter, snap: Snapshot | None) -> None:
        if snap is None or not snap.control:
            return
        tags = []
        if snap.paused:
            tags.append(("PAUSED", theme.WARN))
        if not snap.face:
            tags.append(("NO FACE", theme.DANGER))
        if snap.scrolling:
            tags.append(("SCROLL", theme.ACCENT_2))
        if snap.dragging:
            tags.append(("DRAG", theme.WARN))
        if snap.precision:
            tags.append(("PRECISION", theme.WARN))
        if snap.zoom is not None:
            tags.append(("ZOOM", theme.ACCENT))
        font = QFont(p.font())
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)
        x = self.width() - 16
        for text, color in tags:
            wdt = p.fontMetrics().horizontalAdvance(text) + 22
            r = QRectF(x - wdt, 16, wdt, 26)
            p.setPen(QPen(QColor(color), 1.5))
            p.setBrush(QColor(14, 17, 22, 220))
            p.drawRoundedRect(r, 13, 13)
            p.setPen(QColor(color))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            x -= wdt + 8

    def _paint_toast(self, p: QPainter, text: str, age: float) -> None:
        alpha = 1.0 if age < 1.8 else max(0.0, 1 - (age - 1.8) / 0.4)
        font = QFont(p.font())
        font.setPointSize(12)
        p.setFont(font)
        wdt = min(p.fontMetrics().horizontalAdvance(text) + 40, 500)
        r = QRectF(self.width() / 2 - wdt / 2, self.height() - 110, wdt, 40)
        p.setPen(Qt.PenStyle.NoPen)
        bg = QColor(22, 26, 33)
        bg.setAlphaF(0.92 * alpha)
        p.setBrush(bg)
        p.drawRoundedRect(r, 20, 20)
        fg = QColor(theme.TEXT)
        fg.setAlphaF(alpha)
        p.setPen(fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
