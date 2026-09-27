"""Signal filters for gaze and head tracking."""

from __future__ import annotations

import math
from collections import deque

import numpy as np


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / max(dt, 1e-6))


class OneEuro:
    """One Euro filter (Casiez et al. 2012): smooth when slow, responsive when fast."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007, d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self) -> None:
        self._x: float | None = None
        self._dx = 0.0
        self._t = 0.0

    def __call__(self, x: float, t: float) -> float:
        if self._x is None:
            self._x, self._dx, self._t = x, 0.0, t
            return x
        dt = max(t - self._t, 1e-4)
        dx = (x - self._x) / dt
        self._dx = self._dx + _alpha(self.d_cutoff, dt) * (dx - self._dx)
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        self._x = self._x + _alpha(cutoff, dt) * (x - self._x)
        self._t = t
        return self._x

    @property
    def speed(self) -> float:
        return abs(self._dx)


class OneEuro2D:
    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007, d_cutoff: float = 1.0):
        self.fx = OneEuro(min_cutoff, beta, d_cutoff)
        self.fy = OneEuro(min_cutoff, beta, d_cutoff)

    def reset(self) -> None:
        self.fx.reset()
        self.fy.reset()

    def set_params(self, min_cutoff: float, beta: float) -> None:
        for f in (self.fx, self.fy):
            f.min_cutoff, f.beta = min_cutoff, beta

    def __call__(self, x: float, y: float, t: float) -> tuple[float, float]:
        return self.fx(x, t), self.fy(y, t)


class GazeStabilizer:
    """Median-of-3 outlier rejection → One Euro → soft deadband.

    The deadband keeps the point perfectly still while you fixate (eye
    trackers jitter by tens of pixels) yet lets real movement through
    immediately, because displacement beyond the radius passes unattenuated.
    """

    def __init__(self, smoothing: float = 0.6, px_scale: float = 1.0):
        self._hist: deque[tuple[float, float]] = deque(maxlen=3)
        self._euro = OneEuro2D()
        self._held: np.ndarray | None = None
        self.px_scale = px_scale
        self.set_smoothing(smoothing)

    def set_smoothing(self, s: float) -> None:
        s = min(max(s, 0.0), 1.0)
        self.smoothing = s
        self._euro.set_params(min_cutoff=3.0 - 2.6 * s, beta=0.02 - 0.015 * s)
        self.deadband = (8 + 42 * s) * self.px_scale

    def reset(self) -> None:
        self._hist.clear()
        self._euro.reset()
        self._held = None

    def __call__(self, x: float, y: float, t: float) -> tuple[float, float]:
        self._hist.append((x, y))
        if len(self._hist) == 3:
            xs, ys = zip(*self._hist)
            x, y = float(np.median(xs)), float(np.median(ys))
        fx, fy = self._euro(x, y, t)
        p = np.array([fx, fy])
        if self._held is None:
            self._held = p
        else:
            d = p - self._held
            dist = float(np.hypot(*d))
            r = self.deadband
            if dist > 0:
                w = 1.0 if dist >= r else (dist / r) ** 2
                self._held = self._held + d * w
        return float(self._held[0]), float(self._held[1])


class RollingPercentile:
    """Percentile over a sliding window, recomputed every `every` samples."""

    def __init__(self, size: int = 600, q: float = 85.0, every: int = 15, default: float = 0.0):
        self._buf: deque[float] = deque(maxlen=size)
        self.q = q
        self.every = every
        self._n = 0
        self.value = default

    def __len__(self) -> int:
        return len(self._buf)

    def push(self, x: float) -> float:
        self._buf.append(x)
        self._n += 1
        if self._n % self.every == 0 or len(self._buf) < self.every:
            self.value = float(np.percentile(self._buf, self.q))
        return self.value
