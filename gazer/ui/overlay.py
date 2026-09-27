"""Always-on-top, click-through HUD over the real desktop.

Draws the gaze reticle, dwell arc, gaze dot, hot-zone edge glow, action
wheel, zoom lens, status tags, click ripples and typewriter toasts — the
cinematic Command Deck look, carried onto whatever app you're using.
"""

from __future__ import annotations

import math
import sys
import time
from typing import Callable

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QRegion,
)
from PyQt6.QtWidgets import QWidget

from gazer.config import ACTIONS, OverlaySettings
from gazer.core.engine import Snapshot
from gazer.ui import theme
from gazer.ui.bridge import ScreenMapper

WHEEL_LABELS = {
    "left_click": "CLICK", "double_click": "DOUBLE", "right_click": "RIGHT", "middle_click": "MIDDLE",
    "drag_toggle": "DRAG", "scroll_mode": "SCROLL", "zoom": "ZOOM", "keyboard_toggle": "KEYS",
    "recenter": "CENTER", "precision_toggle": "PRECISE", "pause_toggle": "PAUSE", "mode_cycle": "MODE",
    "hotkey:alt+tab": "SWITCH", "hotkey:ctrl+c": "COPY", "hotkey:ctrl+v": "PASTE", "hotkey:alt+left": "BACK",
    "key:enter": "ENTER", "key:esc": "ESC",
}
ZONE_EDGE = 14  # px thickness of the zone glow


def _exclude_from_capture(widget: QWidget) -> None:
    """Keep the HUD out of screenshots / screen shares (Windows 10 2004+)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11)
    except Exception:
        pass


def _font(size: float, bold: bool = True, family: str = "Segoe UI") -> QFont:
    f = QFont(family)
    f.setPointSizeF(size)
    f.setBold(bold)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
    return f


class Overlay(QWidget):
    def __init__(self, mapper: ScreenMapper, settings: Callable[[], OverlaySettings], follow_os_cursor: bool = True):
        super().__init__(None)
        self.mapper = mapper
        self.settings = settings
        self.follow_os_cursor = follow_os_cursor
        self.snap: Snapshot | None = None
        self.ripples: list[tuple[QPointF, float]] = []
        self.toast: tuple[str, float] | None = None
        self.zoom_pixmap: QPixmap | None = None
        self.zoom_id = -1
        self._zone_flash: tuple[str, float] | None = None
        self._dirty_prev = QRect()
        self._t0 = time.perf_counter()
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

    @property
    def s(self) -> OverlaySettings:
        return self.settings()

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
        now = time.perf_counter()
        for ev in snap.events:
            if ev[0] == "click" and self.s.click_ripple:
                self.ripples.append((self.mapper.to_local(*ev[1]), now))
            elif ev[0] == "toast" and self.s.show_toasts:
                self.toast = (str(ev[1]), now)
            elif ev[0] == "zone":
                self._zone_flash = (ev[1], now)
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

    # ------------------------------------------------------------ geometry

    def _cursor_local(self) -> QPointF | None:
        if self.follow_os_cursor:
            g = QCursor.pos()
            return QPointF(g.x() - self.mapper.geo.x(), g.y() - self.mapper.geo.y())
        if self.snap is not None and self.snap.pointer is not None:
            return self.mapper.to_local(*self.snap.pointer)
        return None

    def _zone_rect(self, zone: str) -> QRectF:
        w, h, e = self.width(), self.height(), ZONE_EDGE
        c = min(w, h) * 0.12
        return {
            "top": QRectF(w * 0.2, 0, w * 0.6, e), "bottom": QRectF(w * 0.2, h - e, w * 0.6, e),
            "left": QRectF(0, h * 0.2, e, h * 0.6), "right": QRectF(w - e, h * 0.2, e, h * 0.6),
            "top_left": QRectF(0, 0, c, c), "top_right": QRectF(w - c, 0, c, c),
            "bottom_left": QRectF(0, h - c, c, c), "bottom_right": QRectF(w - c, h - c, c, c),
        }.get(zone, QRectF())

    def _items_rect(self) -> QRect:
        rects: list[QRectF] = []
        snap = self.snap
        if snap and snap.control:
            c = self._cursor_local()
            if c is not None:
                rects.append(QRectF(c.x() - 48, c.y() - 48, 96, 96))
            if snap.gaze is not None and self.s.show_gaze_dot:
                g = self.mapper.to_local(*snap.gaze)
                rects.append(QRectF(g.x() - 26, g.y() - 26, 52, 52))
            if snap.wheel is not None:
                w = snap.wheel
                cc = self.mapper.to_local(*w.center)
                rr = self.mapper.len_to_local(w.radius) + 40
                rects.append(QRectF(cc.x() - rr, cc.y() - rr, 2 * rr, 2 * rr))
            if snap.zoom is not None:
                rects.append(self.mapper.rect_to_local(snap.zoom.lens).adjusted(-24, -24, 24, 24))
            if snap.scrolling and c is not None:
                rects.append(QRectF(c.x() - 90, c.y() - 90, 180, 180))
            if snap.zone:
                rects.append(self._zone_rect(snap.zone[0]).adjusted(-40, -40, 40, 40))
        if self._zone_flash:
            rects.append(self._zone_rect(self._zone_flash[0]).adjusted(-40, -40, 40, 40))
        for pos, _ in self.ripples:
            rects.append(QRectF(pos.x() - 60, pos.y() - 60, 120, 120))
        rects.append(self._badge_rect())
        if self.toast:
            rects.append(self._toast_rect())
        out = QRect()
        for r in rects:
            out = out.united(r.toAlignedRect())
        return out

    def _badge_rect(self) -> QRectF:
        return QRectF(self.width() - 420, 8, 412, 44)

    def _toast_rect(self) -> QRectF:
        return QRectF(self.width() / 2 - 330, self.height() - 130, 660, 70)

    def _tick(self) -> None:
        now = time.perf_counter()
        self.ripples = [(p, t0) for p, t0 in self.ripples if now - t0 < 0.6]
        if self.toast and now - self.toast[1] > 2.8:
            self.toast = None
        if self._zone_flash and now - self._zone_flash[1] > 0.7:
            self._zone_flash = None
        r = self._items_rect()
        dirty = r.united(self._dirty_prev)
        self._dirty_prev = r
        if not dirty.isEmpty():
            self.update(QRegion(dirty))

    # ------------------------------------------------------------ painting

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        snap = self.snap
        now = time.perf_counter()
        t = now - self._t0
        accent = QColor(self.s.accent)
        if snap and snap.control and not snap.calibrating:
            if snap.zone:
                self._paint_zone(p, snap.zone[0], snap.zone[1], accent)
            if snap.zoom is not None:
                self._paint_zoom(p, snap)
            c = self._cursor_local()
            if snap.gaze is not None and self.s.show_gaze_dot:
                g = self.mapper.to_local(*snap.gaze)
                grad = QRadialGradient(g, 20)
                grad.setColorAt(0, QColor(196, 180, 255, 190))
                grad.setColorAt(1, QColor(155, 124, 255, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(grad)
                p.drawEllipse(g, 20, 20)
            if c is not None and snap.wheel is None and not snap.paused:
                if self.s.show_cursor_ring:
                    self._paint_reticle(p, c, t, snap, accent)
                if snap.dwell_enabled and snap.dwell_progress > 0.02 and self.s.show_dwell_ring:
                    self._paint_dwell(p, c, snap.dwell_progress)
            if snap.scrolling and c is not None:
                self._paint_scroll(p, c, snap)
            if snap.wheel is not None:
                self._paint_wheel(p, snap, t)
        if self._zone_flash:
            k = (now - self._zone_flash[1]) / 0.7
            self._paint_zone(p, self._zone_flash[0], 1.0, QColor(255, 255, 255), alpha=1 - k)
        for pos, t0 in self.ripples:
            k = (now - t0) / 0.6
            for i, (r0, w) in enumerate(((10, 3.0), (4, 1.5))):
                col = QColor(accent)
                col.setAlphaF(max(0.0, 0.9 * (1 - k)) * (1 if i == 0 else 0.6))
                p.setPen(QPen(col, w))
                p.setBrush(Qt.BrushStyle.NoBrush)
                rr = r0 + 44 * k * (1 if i == 0 else 0.6)
                p.drawEllipse(pos, rr, rr)
        self._paint_badges(p, snap)
        if self.toast:
            self._paint_toast(p, self.toast[0], now - self.toast[1], accent)
        p.end()

    def _paint_reticle(self, p: QPainter, c: QPointF, t: float, snap: Snapshot, accent: QColor) -> None:
        col = QColor(theme.WARN) if snap.precision else QColor(accent)
        r = 13 if snap.precision else 19
        # soft halo
        halo = QRadialGradient(c, r + 14)
        hc = QColor(col)
        hc.setAlpha(46)
        halo.setColorAt(0.55, hc)
        hc.setAlpha(0)
        halo.setColorAt(1, hc)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, r + 14, r + 14)
        # rotating segmented ring
        col.setAlpha(220)
        p.setPen(QPen(col, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        base = int((t * 90) % 360 * 16)
        for i in range(4):
            p.drawArc(rect, base + i * 90 * 16 + 12 * 16, 66 * 16)
        # ticks
        col.setAlpha(150)
        p.setPen(QPen(col, 1.6))
        for i in range(4):
            a = math.radians(i * 90 + 45)
            p.drawLine(QPointF(c.x() + math.cos(a) * (r + 5), c.y() + math.sin(a) * (r + 5)),
                       QPointF(c.x() + math.cos(a) * (r + 10), c.y() + math.sin(a) * (r + 10)))
        if snap.dragging:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 181, 71, 110))
            p.drawEllipse(c, r * 0.55, r * 0.55)

    def _paint_dwell(self, p: QPainter, c: QPointF, progress: float) -> None:
        rect = QRectF(c.x() - 27, c.y() - 27, 54, 54)
        span = -int(360 * 16 * progress)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(62, 232, 216, 70), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, span)
        p.setPen(QPen(QColor(240, 255, 253), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, span)

    def _paint_zone(self, p: QPainter, zone: str, progress: float, color: QColor, alpha: float = 1.0) -> None:
        r = self._zone_rect(zone)
        if r.isEmpty():
            return
        vertical = r.height() > r.width() * 2
        horizontal = r.width() > r.height() * 2
        if horizontal:
            g = QLinearGradient(r.center().x(), 0 if r.y() < 5 else self.height(), r.center().x(),
                                r.height() * 3 if r.y() < 5 else self.height() - r.height() * 3)
        elif vertical:
            g = QLinearGradient(0 if r.x() < 5 else self.width(), 0,
                                r.width() * 3 if r.x() < 5 else self.width() - r.width() * 3, 0)
        else:
            corner = QPointF(0 if r.x() < 5 else self.width(), 0 if r.y() < 5 else self.height())
            g = QRadialGradient(corner, r.width() * 1.6)
        c1 = QColor(color)
        c1.setAlphaF(min(1.0, (0.25 + 0.6 * progress) * alpha))
        c2 = QColor(color)
        c2.setAlphaF(0)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        if horizontal:
            p.drawRect(QRectF(r.x(), 0 if r.y() < 5 else self.height() - r.height() * 3, r.width(), r.height() * 3))
        elif vertical:
            p.drawRect(QRectF(0 if r.x() < 5 else self.width() - r.width() * 3, r.y(), r.width() * 3, r.height()))
        else:
            p.drawRect(r.adjusted(-r.width() * 0.6 if r.x() > 5 else 0, -r.height() * 0.6 if r.y() > 5 else 0,
                                  r.width() * 0.6 if r.x() < 5 else 0, r.height() * 0.6 if r.y() < 5 else 0))

    def _paint_scroll(self, p: QPainter, c: QPointF, snap: Snapshot) -> None:
        p.setPen(QPen(QColor(theme.ACCENT_2), 2))
        p.setBrush(QColor(8, 14, 22, 170))
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

    def _paint_wheel(self, p: QPainter, snap: Snapshot, t: float) -> None:
        w = snap.wheel
        c = self.mapper.to_local(*w.center)
        R = self.mapper.len_to_local(w.radius)
        r0 = self.mapper.len_to_local(w.inner)
        n = max(len(w.items), 1)
        span = 360.0 / n
        # outer rotating ring
        p.setPen(QPen(QColor(62, 232, 216, 110), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, R + 10, R + 10)
        p.setFont(_font(9.5))
        for i, item in enumerate(w.items):
            start = 90 + span / 2 - i * span
            path = QPainterPath()
            outer = QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R)
            inner = QRectF(c.x() - r0, c.y() - r0, 2 * r0, 2 * r0)
            path.arcMoveTo(outer, start - 1.5)
            path.arcTo(outer, start - 1.5, -span + 3)
            path.arcTo(inner, start - span + 1.5, span - 3)
            path.closeSubpath()
            hover = w.hover == i
            p.setPen(QPen(QColor(62, 232, 216, 230 if hover else 90), 1.5))
            p.setBrush(QColor(62, 214, 198, 190) if hover else QColor(8, 16, 24, 225))
            p.drawPath(path)
            if hover and w.progress > 0:
                prog = QPainterPath()
                pr = r0 + (R - r0) * w.progress
                rr = QRectF(c.x() - pr, c.y() - pr, 2 * pr, 2 * pr)
                prog.arcMoveTo(inner, start - 1.5)
                prog.arcTo(rr, start - 1.5, -span + 3)
                prog.arcTo(inner, start - span + 1.5, span - 3)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 70))
                p.drawPath(prog)
            mid = math.radians(start - span / 2)
            lr = (R + r0) / 2
            lp = QPointF(c.x() + lr * math.cos(mid), c.y() - lr * math.sin(mid))
            p.setPen(QColor("#021a18") if hover else QColor(theme.TEXT))
            label = WHEEL_LABELS.get(item, ACTIONS.get(item, item)[:9].upper())
            p.drawText(QRectF(lp.x() - 48, lp.y() - 12, 96, 24), Qt.AlignmentFlag.AlignCenter, label)
        p.setPen(QPen(QColor(62, 232, 216, 120), 1.5))
        p.setBrush(QColor(4, 10, 16, 235))
        p.drawEllipse(c, r0 - 4, r0 - 4)
        p.setPen(QColor(theme.MUTED))
        p.setFont(_font(8))
        p.drawText(QRectF(c.x() - r0, c.y() - 10, 2 * r0, 20), Qt.AlignmentFlag.AlignCenter, "CANCEL")
        ptr = self.mapper.to_local(*w.pointer)
        p.setPen(QPen(QColor(theme.WARN), 2))
        p.setBrush(QColor(255, 181, 71, 120))
        p.drawEllipse(ptr, 7, 7)

    def _paint_zoom(self, p: QPainter, snap: Snapshot) -> None:
        z = snap.zoom
        lens = self.mapper.rect_to_local(z.lens)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawRoundedRect(lens.adjusted(-6, -6, 6, 6), 6, 6)
        if self.zoom_pixmap is not None:
            p.drawPixmap(lens.toRect(), self.zoom_pixmap)
        p.setPen(QPen(QColor(62, 232, 216, 140), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(lens)
        # HUD brackets
        p.setPen(QPen(QColor(theme.ACCENT), 3))
        b = 22
        for sx, sy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            x = lens.left() if sx == 0 else lens.right()
            y = lens.top() if sy == 0 else lens.bottom()
            dx = b if sx == 0 else -b
            dy = b if sy == 0 else -b
            p.drawLine(QPointF(x - (4 if sx == 0 else -4), y - (4 if sy == 0 else -4)), QPointF(x + dx, y - (4 if sy == 0 else -4)))
            p.drawLine(QPointF(x - (4 if sx == 0 else -4), y - (4 if sy == 0 else -4)), QPointF(x - (4 if sx == 0 else -4), y + dy))
        p.setFont(_font(8))
        p.setPen(QColor(theme.ACCENT))
        p.drawText(QRectF(lens.x(), lens.y() - 22, lens.width(), 16), Qt.AlignmentFlag.AlignLeft,
                   f"ZOOM ×{lens.width() / max(1.0, self.mapper.rect_to_local(z.region).width()):.1f}")
        ptr = self.mapper.to_local(*z.pointer)
        p.setPen(QPen(QColor(theme.DANGER), 2))
        for a, b2 in (((-18, 0), (-5, 0)), ((5, 0), (18, 0)), ((0, -18), (0, -5)), ((0, 5), (0, 18))):
            p.drawLine(QPointF(ptr.x() + a[0], ptr.y() + a[1]), QPointF(ptr.x() + b2[0], ptr.y() + b2[1]))

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
        if not tags:
            return
        p.setFont(_font(8.5))
        x = self.width() - 16
        for text, color in tags:
            wdt = p.fontMetrics().horizontalAdvance(text) + 30
            r = QRectF(x - wdt, 16, wdt, 26)
            p.setPen(QPen(QColor(color), 1.2))
            p.setBrush(QColor(4, 10, 16, 225))
            p.drawRect(r)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRect(QRectF(r.x(), r.y(), 3, r.height()))
            p.setPen(QColor(color))
            p.drawText(r.adjusted(6, 0, 0, 0), Qt.AlignmentFlag.AlignCenter, text)
            x -= wdt + 8

    def _paint_toast(self, p: QPainter, text: str, age: float, accent: QColor) -> None:
        alpha = 1.0 if age < 2.3 else max(0.0, 1 - (age - 2.3) / 0.5)
        shown = text.upper()[: max(1, int(age * 90))]
        p.setFont(_font(10.5))
        wdt = min(p.fontMetrics().horizontalAdvance(text.upper()) + 60, 640)
        r = QRectF(self.width() / 2 - wdt / 2, self.height() - 118, wdt, 42)
        bg = QColor(4, 12, 18)
        bg.setAlphaF(0.9 * alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRect(r)
        edge = QColor(accent)
        edge.setAlphaF(0.5 * alpha)
        p.setPen(QPen(edge, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        bar = QColor(accent)
        bar.setAlphaF(alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bar)
        p.drawRect(QRectF(r.x(), r.y(), 3, r.height()))
        fg = QColor(theme.TEXT)
        fg.setAlphaF(alpha)
        p.setPen(fg)
        p.drawText(r.adjusted(16, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, shown)
