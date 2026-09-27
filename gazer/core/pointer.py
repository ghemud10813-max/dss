"""Pointer engine: fuses gaze and head signals into one cursor position.

Hybrid mode (the default) follows the MAGIC-pointing idea: webcam gaze is
only accurate to a few degrees, so it is used for *big* jumps (the cursor
warps to where you look once your eyes settle far away), while small, precise
adjustments come from head motion, which webcams track very accurately.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from gazer.config import HeadCalibration, PointerSettings, ScrollSettings
from gazer.core.features import FaceFeatures
from gazer.core.filters import GazeStabilizer
from gazer.core.head import HeadAbsolute, HeadJoystick, HeadMouse, HeadSignal
from gazer.core.screen import ScreenRect

MODE_ORDER = ["hybrid", "gaze", "head_mouse", "head_joystick", "head_absolute"]
GAZE_MODES = {"hybrid", "gaze"}


@dataclass
class PointerOutput:
    pos: np.ndarray | None  # desired pointer position (px); None = don't move
    gaze: np.ndarray | None  # stabilised gaze point (px)
    scroll: tuple[int, int] = (0, 0)  # wheel units (vertical, horizontal)
    warped: bool = False


class PointerEngine:
    def __init__(self, settings: PointerSettings, scroll: ScrollSettings, cal: HeadCalibration,
                 screen: ScreenRect):
        self.s = settings
        self.scroll_s = scroll
        self.screen = screen
        self.P = np.array(screen.center, dtype=np.float64)
        self.head = HeadSignal()
        self.mouse = HeadMouse(settings, screen.width)
        self.joy = HeadJoystick(settings, screen.width)
        self.absolute = HeadAbsolute(settings, cal, screen.width, screen.height)
        self.stab = GazeStabilizer(settings.gaze_smoothing, px_scale=screen.width / 1920)
        self.precision = False
        self._prec_anchor: np.ndarray | None = None
        self._prec_ref: np.ndarray | None = None
        self.scrolling = False
        self._scroll_anchor: np.ndarray | None = None
        self._scroll_acc = np.zeros(2)
        self._scroll_t: float | None = None
        self.scroll_rate = np.zeros(2)
        self._far_since: float | None = None
        self._suppress_until = 0.0
        self._gaze_hist: deque[tuple[float, np.ndarray]] = deque(maxlen=12)
        self._last_mode = settings.mode

    # ------------------------------------------------------------ control

    def apply_settings(self) -> None:
        self.stab.set_smoothing(self.s.gaze_smoothing)

    def set_calibration(self, cal: HeadCalibration) -> None:
        self.absolute.cal = cal
        self.absolute.neutral = None

    def sync(self, pos) -> None:
        self.P = np.array(pos, dtype=np.float64)
        self.mouse.reset()

    def recenter(self) -> None:
        hp = self.head.value
        if hp is not None:
            self.joy.recenter(hp)
            self.absolute.recenter(hp)
        if self._last_mode in ("head_absolute", "head_joystick"):
            self.P = np.array(self.screen.center)
        self.mouse.reset()

    def set_precision(self, on: bool) -> None:
        self.precision = on
        self._prec_anchor = self.P.copy() if on else None
        self._prec_ref = None

    def start_scroll(self) -> None:
        self.scrolling = True
        self._scroll_anchor = None if self.head.value is None else self.head.value.copy()
        self._scroll_acc = np.zeros(2)
        self._scroll_t = None
        self.scroll_rate = np.zeros(2)

    def stop_scroll(self) -> None:
        self.scrolling = False
        self.scroll_rate = np.zeros(2)
        self.mouse.reset()

    # ------------------------------------------------------------- update

    def update(self, t: float, feats: FaceFeatures | None, gaze_n: tuple[float, float] | None,
               mode: str | None = None) -> PointerOutput:
        mode = mode or self.s.mode
        if mode != self._last_mode:
            self._last_mode = mode
            self.mouse.reset()
            self.joy.reset()
            self._far_since = None

        gaze_px = None
        if gaze_n is not None:
            rx, ry = self.screen.norm_to_px(*gaze_n)
            gaze_px = np.array(self.stab(rx, ry, t))
            self._gaze_hist.append((t, gaze_px))

        if feats is None:
            self.mouse.reset()
            self.joy.reset()
            self._far_since = None
            return PointerOutput(None, gaze_px)

        hp = self.head(feats.head_point, t)

        if self.scrolling:
            return PointerOutput(None, gaze_px, self._scroll_update(hp, t))

        factor = self.s.precision_factor if self.precision else 1.0
        warped = False

        if mode == "gaze":
            if gaze_px is None:
                return PointerOutput(None, None)
            self.P = self.P + (gaze_px - self.P) * (0.12 if self.precision else 1.0)
        elif mode == "head_mouse":
            self.P = self.P + self.mouse.update(hp, t) * factor
        elif mode == "head_joystick":
            self.P = self.P + self.joy.update(hp, t) * factor
        elif mode == "head_absolute":
            target = np.array(self.screen.center) + self.absolute.position(hp)
            if self.precision and self._prec_anchor is not None:
                if self._prec_ref is None:
                    self._prec_ref = target.copy()
                target = self._prec_anchor + (target - self._prec_ref) * factor
            self.P = target
        else:  # hybrid
            d = self.mouse.update(hp, t) * self.s.hybrid_fine_gain * factor
            if t >= self._suppress_until:
                self.P = self.P + d
            if gaze_px is not None and not self.precision:
                warped = self._maybe_warp(t, gaze_px)

        self.P = np.array(self.screen.clamp(*self.P))
        return PointerOutput(self.P.copy(), gaze_px, warped=warped)

    def _maybe_warp(self, t: float, gaze_px: np.ndarray) -> bool:
        warp_px = self.s.warp_threshold * self.screen.width
        if float(np.hypot(*(gaze_px - self.P))) <= warp_px:
            self._far_since = None
            return False
        if self._far_since is None:
            self._far_since = t
            return False
        if (t - self._far_since) * 1000 < self.s.warp_delay_ms:
            return False
        recent = [p for (ts, p) in self._gaze_hist if t - ts <= 0.15]
        if len(recent) >= 2:
            spread = float(np.max(np.linalg.norm(np.array(recent) - gaze_px, axis=1)))
            if spread > warp_px * 0.5:
                return False  # eyes still travelling
        self.P = gaze_px.copy()
        self._far_since = None
        self._suppress_until = t + 0.15  # head often moves along with a big gaze shift
        self.mouse.reset()
        return True

    def _scroll_update(self, hp: np.ndarray, t: float) -> tuple[int, int]:
        if self._scroll_anchor is None:
            self._scroll_anchor = hp.copy()
        dt = 0.0 if self._scroll_t is None else min(t - self._scroll_t, 0.1)
        self._scroll_t = t
        d = hp - self._scroll_anchor
        dz = self.scroll_s.deadzone

        def axis(v: float) -> float:
            m = abs(v) - dz
            return 0.0 if m <= 0 else math.copysign(min((m / 0.08) ** 1.5, 6.0), v)

        rate = np.array([-axis(d[1]), axis(d[0]) if self.scroll_s.horizontal else 0.0])
        rate *= self.scroll_s.speed * 1200.0  # wheel units per second
        if self.scroll_s.invert:
            rate = -rate
        self.scroll_rate = rate
        self._scroll_acc += rate * dt
        out = np.trunc(self._scroll_acc)
        self._scroll_acc -= out
        return int(out[0]), int(out[1])
