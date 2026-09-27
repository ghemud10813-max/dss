"""Gaze analytics and eye-health: heatmap, fixations, reading, blinks, breaks.

* Heatmap — where your eyes (or cursor, in head modes) spent time.
* Fixations — dispersion-threshold (I-DT) detection; fixation rate and mean
  duration are classic measures of visual effort.
* Reading — runs of small rightward saccades mark reading time.
* Blink rate — people blink ~15–20×/min normally but only ~5–7×/min while
  staring at screens, which dries the eyes. Gazer nudges you when it drops.
* 20-20-20 — every 20 minutes of screen time, look 20 ft away for 20 s.
  Looking away (face out of view) for the break length counts as a break.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from gazer.config import WellnessSettings
from gazer.core.screen import ScreenRect

GRID_W, GRID_H = 48, 27


class Insights:
    def __init__(self, screen: ScreenRect, settings: WellnessSettings):
        self.screen = screen
        self.s = settings
        self.heat = np.zeros((GRID_H, GRID_W), dtype=np.float64)
        self._last_t: float | None = None
        # fixations
        self._win: deque[tuple[float, float, float]] = deque()
        self._fix_start: float | None = None
        self._fix_center: np.ndarray | None = None
        self.fixations: deque[tuple[float, float]] = deque(maxlen=400)  # (end time, duration)
        self.saccades: deque[tuple[float, float, float]] = deque(maxlen=400)  # (t, dx, dy) normalized
        self.reading_s = 0.0
        # blinks & breaks
        self.blinks: deque[float] = deque(maxlen=200)
        self.face_s_window: deque[tuple[float, float]] = deque()
        self.screen_s = 0.0  # continuous screen time since last break
        self.away_s = 0.0
        self.total_face_s = 0.0
        self._break_nudged_at = -1e9
        self._blink_nudged_at = -1e9
        self.started: float | None = None

    def reset(self) -> None:
        self.heat[:] = 0
        self.fixations.clear()
        self.saccades.clear()
        self.reading_s = 0.0

    # --------------------------------------------------------------- update

    def update(self, snap) -> list[tuple]:
        """Feed a snapshot; returns wellness events to surface as toasts."""
        t = snap.t
        if self.started is None:
            self.started = t
        dt = 0.0 if self._last_t is None else min(max(t - self._last_t, 0.0), 0.25)
        self._last_t = t
        events: list[tuple] = []

        looking = snap.face and abs(snap.head[0]) < 0.6
        if looking:
            self.total_face_s += dt
            self.screen_s += dt
            self.away_s = 0.0
        else:
            self.away_s += dt
            if self.away_s >= self.s.break_length_s and self.screen_s > 0:
                if self.screen_s >= 60:
                    events.append(("wellness", "break_taken", "Nice break — eyes rested"))
                self.screen_s = 0.0
        self.face_s_window.append((t, dt if looking else 0.0))
        while self.face_s_window and t - self.face_s_window[0][0] > 60:
            self.face_s_window.popleft()
        if snap.blinked:
            self.blinks.append(t)

        p = self._point(snap)
        if p is not None and snap.face:
            gx = min(GRID_W - 1, max(0, int(p[0] * GRID_W)))
            gy = min(GRID_H - 1, max(0, int(p[1] * GRID_H)))
            self.heat[gy, gx] += dt
            self._fixation(t, p)

        if self.s.enabled:
            interval = self.s.break_interval_min * 60
            if self.screen_s >= interval and t - self._break_nudged_at > 300:
                self._break_nudged_at = t
                events.append(("wellness", "break",
                               f"20-20-20: look at something ~6 m away for {self.s.break_length_s:.0f} s"))
            rate = self.blink_rate(t)
            if (rate is not None and rate < self.s.low_blink_per_min and t - self._blink_nudged_at > 180
                    and t - self.started > 90):
                self._blink_nudged_at = t
                events.append(("wellness", "blink", f"Only {rate:.0f} blinks/min — blink slowly a few times"))
        return events

    def _point(self, snap) -> tuple[float, float] | None:
        src = snap.gaze if snap.gaze is not None else snap.pointer
        if src is None:
            return None
        nx, ny = self.screen.px_to_norm(*src)
        return min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0)

    def _fixation(self, t: float, p: tuple[float, float]) -> None:
        self._win.append((t, p[0], p[1]))
        while self._win and t - self._win[0][0] > 0.1:
            self._win.popleft()
        pts = np.array([(x, y) for _, x, y in self._win])
        disp = float(np.ptp(pts[:, 0]) + np.ptp(pts[:, 1])) if len(pts) > 1 else 0.0
        if disp <= 0.025:
            if self._fix_start is None:
                self._fix_start = self._win[0][0]
                c = pts.mean(axis=0)
                if self._fix_center is not None:
                    d = c - self._fix_center
                    self.saccades.append((t, float(d[0]), float(d[1])))
                    if 0.01 < d[0] < 0.08 and abs(d[1]) < 0.02:
                        recent = [s for s in self.saccades if t - s[0] < 3.0 and 0.01 < s[1] < 0.08
                                  and abs(s[2]) < 0.02]
                        if len(recent) >= 3:
                            self.reading_s += min(t - recent[-2][0], 1.0)
                self._fix_center = c
        elif self._fix_start is not None:
            dur = t - self._fix_start
            if dur >= 0.1:
                self.fixations.append((t, dur))
            self._fix_start = None

    # -------------------------------------------------------------- reports

    def blink_rate(self, t: float) -> float | None:
        face = sum(d for _, d in self.face_s_window)
        if face < 30:
            return None
        n = sum(1 for b in self.blinks if t - b <= 60)
        return n * 60.0 / face

    def summary(self, t: float) -> dict:
        recent = [d for (e, d) in self.fixations if t - e <= 60]
        h = self.heat
        mx = float(h.max()) or 1.0
        heat = (np.sqrt(h / mx) * 255).astype(np.uint8)
        return {
            "heat": {"w": GRID_W, "h": GRID_H, "data": heat.flatten().tolist()},
            "fix_per_min": float(len(recent)),
            "fix_mean_ms": float(np.mean(recent) * 1000) if recent else 0.0,
            "saccades_per_min": float(sum(1 for s in self.saccades if t - s[0] <= 60)),
            "reading_s": self.reading_s,
            "blink_rate": self.blink_rate(t),
            "blinks": len(self.blinks),
            "screen_s": self.screen_s,
            "break_interval_s": self.s.break_interval_min * 60,
            "face_s": self.total_face_s,
        }
