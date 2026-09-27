"""Dwell detection, the radial action wheel and the zoom-click lens.

These solve the "Midas touch" problem of gaze interfaces — looking at
something shouldn't click it. Dwell can open the action wheel instead of
clicking directly, so you choose *what* to do at a spot before it happens,
and the zoom lens magnifies small targets for a precise second pick.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


class DwellDetector:
    def __init__(self, radius_px: float = 45, dwell_s: float = 0.9, cooldown_s: float = 0.7):
        self.radius = radius_px
        self.dwell_s = dwell_s
        self.cooldown_s = cooldown_s
        self.reset()

    def reset(self) -> None:
        self.anchor: np.ndarray | None = None
        self.start = 0.0
        self.armed = True
        self.cooldown_until = 0.0
        self.progress = 0.0

    def update(self, t: float, pos) -> bool:
        """Returns True when a dwell completes."""
        p = np.asarray(pos, dtype=np.float64)
        if self.anchor is None or float(np.hypot(*(p - self.anchor))) > self.radius:
            self.anchor = p
            self.start = t
            self.armed = True
            self.progress = 0.0
            return False
        if not self.armed or t < self.cooldown_until:
            self.progress = 0.0
            if t < self.cooldown_until:
                self.start = t
            return False
        self.progress = min((t - self.start) / self.dwell_s, 1.0)
        if self.progress >= 1.0:
            self.armed = False  # must move away before the next dwell
            self.cooldown_until = t + self.cooldown_s
            self.progress = 0.0
            return True
        return False


@dataclass
class WheelView:
    center: tuple[float, float]
    items: list[str]
    radius: float
    inner: float
    hover: int | None
    progress: float
    pointer: tuple[float, float]


class ActionWheel:
    """Radial menu; item 0 at the top, clockwise. Centre = cancel."""

    def __init__(self, center, items: list[str], radius: float, select_dwell_s: float = 0.45,
                 timeout_s: float = 8.0, t0: float = 0.0):
        self.center = np.asarray(center, dtype=np.float64)
        self.items = items
        self.radius = radius
        self.inner = radius * 0.3
        self.select_dwell_s = select_dwell_s
        self.timeout_s = timeout_s
        self.opened = t0
        self.hover: int | None = None
        self._hover_since = t0
        self.progress = 0.0
        self.pointer = self.center.copy()

    def sector_at(self, p) -> int | None:
        d = np.asarray(p, dtype=np.float64) - self.center
        dist = float(np.hypot(*d))
        if dist < self.inner or dist > self.radius * 2.2 or not self.items:
            return None
        n = len(self.items)
        ang = (math.degrees(math.atan2(d[1], d[0])) + 90.0 + 180.0 / n) % 360.0
        return int(ang // (360.0 / n)) % n

    def update(self, t: float, p) -> tuple[str, int | None]:
        """Returns ("select", idx) | ("cancel", None) | ("open", None)."""
        self.pointer = np.asarray(p, dtype=np.float64)
        if t - self.opened > self.timeout_s:
            return "cancel", None
        idx = self.sector_at(p)
        if idx != self.hover:
            self.hover = idx
            self._hover_since = t
            self.progress = 0.0
            return "open", None
        if idx is None:
            self.progress = 0.0
            return "open", None
        self.progress = min((t - self._hover_since) / self.select_dwell_s, 1.0)
        if self.progress >= 1.0:
            return "select", idx
        return "open", None

    def view(self) -> WheelView:
        return WheelView((float(self.center[0]), float(self.center[1])), list(self.items), self.radius,
                         self.inner, self.hover, self.progress,
                         (float(self.pointer[0]), float(self.pointer[1])))


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.w, self.h


def _fit_rect(cx, cy, w, h, bounds: Rect) -> Rect:
    w, h = min(w, bounds.w), min(h, bounds.h)
    x = min(max(cx - w / 2, bounds.x), bounds.x + bounds.w - w)
    y = min(max(cy - h / 2, bounds.y), bounds.y + bounds.h - h)
    return Rect(x, y, w, h)


@dataclass
class ZoomView:
    id: int
    region: tuple[float, float, float, float]  # physical px, what was captured
    lens: tuple[float, float, float, float]  # physical px, where it is drawn
    pointer: tuple[float, float]  # virtual pointer (on the lens)
    target: tuple[float, float]  # real screen point under the lens pointer


class ZoomLens:
    """Magnifies the area around `anchor`. Pointer moves over the enlarged
    image; clicks go to the corresponding real location (factor× precision)."""

    _next_id = 1

    def __init__(self, anchor, factor: float, region_w: float, region_h: float, bounds: Rect,
                 t0: float = 0.0, timeout_s: float = 12.0):
        ax, ay = float(anchor[0]), float(anchor[1])
        self.factor = max(factor, 1.5)
        self.region = _fit_rect(ax, ay, region_w, region_h, bounds)
        self.lens = _fit_rect(ax, ay, self.region.w * self.factor, self.region.h * self.factor, bounds)
        # Recompute effective factor if the lens was clipped by the screen.
        self.fx = self.lens.w / self.region.w
        self.fy = self.lens.h / self.region.h
        self.opened = t0
        self.timeout_s = timeout_s
        self.id = ZoomLens._next_id
        ZoomLens._next_id += 1
        self.pointer = self.to_lens((ax, ay))

    def to_lens(self, real) -> np.ndarray:
        return np.array([self.lens.x + (real[0] - self.region.x) * self.fx,
                         self.lens.y + (real[1] - self.region.y) * self.fy])

    def to_real(self, lens_pt) -> np.ndarray:
        x = self.region.x + (lens_pt[0] - self.lens.x) / self.fx
        y = self.region.y + (lens_pt[1] - self.lens.y) / self.fy
        return np.array([min(max(x, self.region.x), self.region.x + self.region.w - 1),
                         min(max(y, self.region.y), self.region.y + self.region.h - 1)])

    def clamp_to_lens(self, p) -> np.ndarray:
        return np.array([min(max(p[0], self.lens.x), self.lens.x + self.lens.w - 1),
                         min(max(p[1], self.lens.y), self.lens.y + self.lens.h - 1)])

    def expired(self, t: float) -> bool:
        return t - self.opened > self.timeout_s

    def view(self) -> ZoomView:
        tgt = self.to_real(self.pointer)
        return ZoomView(self.id, self.region.as_tuple(), self.lens.as_tuple(),
                        (float(self.pointer[0]), float(self.pointer[1])), (float(tgt[0]), float(tgt[1])))
