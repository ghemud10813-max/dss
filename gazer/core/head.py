"""Head-driven pointing: mouse-like, joystick and absolute modes.

The signal is the nose tip position (in face-widths from the frame centre) of
a mirrored image, so turning or moving the head right moves it right and
looking up moves it up. It captures rotation and translation together, like
the classic Camera Mouse, and is far more precise than webcam gaze.
"""

from __future__ import annotations

import math

import numpy as np

from gazer.config import HeadCalibration, PointerSettings
from gazer.core.filters import OneEuro2D


class HeadSignal:
    """Light smoothing of the raw head point."""

    def __init__(self):
        self._f = OneEuro2D(min_cutoff=2.0, beta=4.0)
        self.value: np.ndarray | None = None

    def reset(self) -> None:
        self._f.reset()
        self.value = None

    def __call__(self, hp: np.ndarray, t: float) -> np.ndarray:
        x, y = self._f(float(hp[0]), float(hp[1]), t)
        self.value = np.array([x, y])
        return self.value


def _signs(s: PointerSettings) -> np.ndarray:
    return np.array([-1.0 if s.invert_x else 1.0, -1.0 if s.invert_y else 1.0])


class HeadMouse:
    """Relative: head *movement* moves the cursor, with pointer acceleration."""

    V_REF = 0.35  # face-widths/s at which gain == base gain

    def __init__(self, settings: PointerSettings, screen_w: float):
        self.s = settings
        self.screen_w = screen_w
        self._prev: np.ndarray | None = None
        self._t = 0.0

    def reset(self) -> None:
        self._prev = None

    def update(self, hp: np.ndarray, t: float) -> np.ndarray:
        if self._prev is None:
            self._prev, self._t = hp.copy(), t
            return np.zeros(2)
        dt = max(t - self._t, 1e-3)
        delta = hp - self._prev
        self._prev, self._t = hp.copy(), t
        mag = float(np.hypot(*delta))
        dz = self.s.head_deadzone
        if mag <= dz:
            return np.zeros(2)
        delta = delta * ((mag - dz) / mag)
        speed = (mag - dz) / dt
        accel = (speed / self.V_REF) ** max(self.s.head_accel - 1.0, 0.0) if speed > 0 else 1.0
        accel = min(max(accel, 0.3), 4.0)
        base = self.screen_w * 2.0 * self.s.head_gain
        return delta * base * accel * _signs(self.s)


class HeadJoystick:
    """Rate control: displacement from a neutral pose sets cursor velocity."""

    def __init__(self, settings: PointerSettings, screen_w: float):
        self.s = settings
        self.screen_w = screen_w
        self.neutral: np.ndarray | None = None
        self._t: float | None = None

    def reset(self) -> None:
        self._t = None

    def recenter(self, hp: np.ndarray) -> None:
        self.neutral = hp.copy()

    def velocity(self, hp: np.ndarray) -> np.ndarray:
        if self.neutral is None:
            self.neutral = hp.copy()
        d = hp - self.neutral
        mag = float(np.hypot(*d))
        dz = self.s.joystick_deadzone
        if mag <= dz:
            return np.zeros(2)
        k = ((mag - dz) / 0.12) ** 1.6
        speed = min(k, 4.0) * self.s.joystick_speed * self.screen_w * 0.8  # px/s
        return d / mag * speed * _signs(self.s)

    def update(self, hp: np.ndarray, t: float) -> np.ndarray:
        dt = 0.0 if self._t is None else min(t - self._t, 0.1)
        self._t = t
        return self.velocity(hp) * dt


class HeadAbsolute:
    """Absolute: the nose points at a screen location (after range calibration)."""

    DEFAULT_RANGE = 0.30  # face-widths of nose travel across the full width

    def __init__(self, settings: PointerSettings, cal: HeadCalibration, screen_w: float, screen_h: float):
        self.s = settings
        self.cal = cal
        self.w = screen_w
        self.h = screen_h
        self.neutral: np.ndarray | None = None

    def recenter(self, hp: np.ndarray) -> None:
        self.neutral = hp.copy()

    def position(self, hp: np.ndarray) -> np.ndarray:
        """Offset from screen centre in px."""
        if self.cal.calibrated and self.neutral is None:
            neutral = np.array([self.cal.neutral_x, self.cal.neutral_y])
        else:
            if self.neutral is None:
                self.neutral = hp.copy()
            neutral = self.neutral
        if self.cal.calibrated:
            gx, gy = self.cal.gain_x, self.cal.gain_y
        else:
            gx = self.w / self.DEFAULT_RANGE
            gy = gx
        g = self.s.absolute_gain
        d = (hp - neutral) * _signs(self.s)
        return np.array([d[0] * gx * g, d[1] * gy * g])


def fit_head_calibration(samples: dict[str, list[np.ndarray]], screen_w: float, screen_h: float,
                         margin: float = 0.12) -> HeadCalibration | None:
    """Samples keyed centre/left/right/top/bottom, targets at `margin` from edges."""
    try:
        med = {k: np.median(np.array(v), axis=0) for k, v in samples.items() if len(v) >= 5}
        c, l, r, t, b = med["center"], med["left"], med["right"], med["top"], med["bottom"]
    except KeyError:
        return None
    span_x = r[0] - l[0]
    span_y = b[1] - t[1]
    if abs(span_x) < 0.02 or abs(span_y) < 0.015:
        return None
    # Signed: if the camera isn't mirrored the span is negative and so is the gain.
    gain_x = (1 - 2 * margin) * screen_w / span_x
    gain_y = (1 - 2 * margin) * screen_h / span_y
    if not (math.isfinite(gain_x) and math.isfinite(gain_y)):
        return None
    return HeadCalibration(True, float(c[0]), float(c[1]), float(gain_x), float(gain_y))
