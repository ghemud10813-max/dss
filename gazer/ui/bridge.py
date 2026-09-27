"""Physical (OS input) ↔ logical (Qt) screen mapping."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QGuiApplication, QScreen

from gazer.core.screen import ScreenRect, list_monitors


class ScreenMapper:
    """Maps physical pixels (engine/OS input) to a QScreen's logical coordinates."""

    def __init__(self, qscreen: QScreen):
        self.qscreen = qscreen
        self.geo = qscreen.geometry()
        self.dpr = qscreen.devicePixelRatio()
        self.physical = self._physical_rect()

    def _physical_rect(self) -> ScreenRect:
        name = self.qscreen.name()
        w = round(self.geo.width() * self.dpr)
        h = round(self.geo.height() * self.dpr)
        mons = list_monitors()
        for m in mons:
            if m.name and (m.name == name or m.name.endswith(name) or name.endswith(m.name)):
                return m.rect
        for m in mons:
            if m.rect.width == w and m.rect.height == h and (m.primary == (self.qscreen == QGuiApplication.primaryScreen())):
                return m.rect
        return ScreenRect(round(self.geo.x() * self.dpr), round(self.geo.y() * self.dpr), w, h)

    def to_local(self, px: float, py: float) -> QPointF:
        """Physical px → coordinates inside a widget that covers this screen."""
        return QPointF((px - self.physical.x) / self.dpr, (py - self.physical.y) / self.dpr)

    def rect_to_local(self, r) -> QRectF:
        x, y, w, h = r
        tl = self.to_local(x, y)
        return QRectF(tl.x(), tl.y(), w / self.dpr, h / self.dpr)

    def to_global_logical(self, px: float, py: float) -> QPointF:
        loc = self.to_local(px, py)
        return QPointF(self.geo.x() + loc.x(), self.geo.y() + loc.y())

    def len_to_local(self, v: float) -> float:
        return v / self.dpr


def pick_qscreen(index: int) -> QScreen:
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    ordered = [primary] + [s for s in screens if s is not primary]
    return ordered[index] if 0 <= index < len(ordered) else primary
