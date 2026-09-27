"""Fullscreen calibration: gaze (fixation targets + validation) and head range."""

from __future__ import annotations

import math
import threading
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from gazer.core.calibration import (
    HEAD_PLAN, PRESETS, CalibPoint, SampleSet, accept_sample, build_plan, evaluate,
)
from gazer.core.engine import Snapshot
from gazer.core.gaze import GazeEstimator
from gazer.core.head import fit_head_calibration
from gazer.ui import theme
from gazer.ui.bridge import ScreenMapper

BG = QColor("#1b1f26")  # mid-dark: keeps pupils at a normal size


class CalibrationWindow(QWidget):
    # kind ("gaze"|"head"), payload
    finished = pyqtSignal(str, object)
    _trained = pyqtSignal()

    def __init__(self, mapper: ScreenMapper, kind: str = "gaze", preset: str = "standard"):
        super().__init__(None)
        self.mapper = mapper
        self.kind = kind
        self.preset = preset
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setGeometry(mapper.geo)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.state = "intro"
        self.countdown_end = time.perf_counter() + 4.0
        self.face = False
        self.last_tick = time.perf_counter()
        self.elapsed = 0.0
        self.samples = SampleSet()
        self.val_preds: list[np.ndarray] = []
        self.val_targets: list[tuple[float, float]] = []
        self.val_ids: list[int] = []
        self.report = None
        self.fit_error = ""
        self.temp: GazeEstimator | None = None
        self.head_samples: dict[str, list[np.ndarray]] = {}
        self.head_result = None
        self.accept_deadline = 0.0
        if kind == "gaze":
            self.plan = build_plan(preset)
            self.points = self.plan.points
        else:
            self.plan = None
            self.points = [CalibPoint(x, y, 2.0, name) for name, x, y in HEAD_PLAN]
        self._trained.connect(self._after_training)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # -------------------------------------------------------------- timing

    def _schedule(self):
        seq = self.points if self.state == "run" else (self.plan.validation if self.plan else [])
        return seq

    def _current(self, elapsed: float):
        acc = 0.0
        seq = self._schedule()
        for i, p in enumerate(seq):
            if elapsed < acc + p.duration:
                return i, p, elapsed - acc
            acc += p.duration
        return None, None, 0.0

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = now - self.last_tick
        self.last_tick = now
        if self.state == "intro" and now >= self.countdown_end:
            self._begin("run")
        elif self.state in ("run", "validate"):
            if self.face:
                self.elapsed += dt
            i, _, _ = self._current(self.elapsed)
            if i is None:
                if self.state == "run":
                    self._after_run()
                else:
                    self._after_validation()
        elif self.state == "results" and self.accept_deadline and now >= self.accept_deadline:
            self._accept()
        self.update()

    def _begin(self, state: str) -> None:
        self.state = state
        self.elapsed = 0.0
        self.last_tick = time.perf_counter()

    # ------------------------------------------------------------ samples

    def on_snapshot(self, snap: Snapshot) -> None:
        self.face = snap.face
        if self.state not in ("run", "validate") or snap.features is None:
            return
        # Map the frame's capture time onto our (face-gated) timeline.
        el = self.elapsed - max(0.0, time.perf_counter() - snap.t)
        i, point, t_in = self._current(max(el, 0.0))
        if point is None:
            return
        f = snap.features
        if self.kind == "head":
            if t_in >= 0.7 and t_in <= point.duration - 0.1:
                self.head_samples.setdefault(point.hint, []).append(f.head_point.copy())
            return
        if not accept_sample(self.plan, point, t_in, f, snap.closure):
            return
        if self.state == "run":
            self.samples.add(f.gaze_vector, (point.nx, point.ny), i)
        elif self.temp is not None:
            pred = self.temp.predict(f.gaze_vector)
            if pred is not None:
                self.val_preds.append(np.array(pred))
                self.val_targets.append((point.nx, point.ny))
                self.val_ids.append(i)

    def _after_run(self) -> None:
        if self.kind == "head":
            cal = fit_head_calibration(self.head_samples, self.mapper.physical.width, self.mapper.physical.height)
            self.head_result = cal
            self.state = "results"
            self.accept_deadline = time.perf_counter() + (2.5 if cal else 6.0)
            return
        self.state = "training"
        X, Y = self.samples.arrays()

        def work():
            try:
                if len(X) < 30:
                    raise ValueError(f"Only {len(X)} usable samples — keep your face visible and eyes open.")
                est = GazeEstimator()
                est.fit(X, Y)
                self.temp = est
            except Exception as exc:
                self.fit_error = str(exc)
            self._trained.emit()  # queued onto the UI thread

        threading.Thread(target=work, daemon=True).start()

    def _after_training(self) -> None:
        if self.temp is None:
            self.state = "results"
            self.accept_deadline = 0.0
            return
        self._begin("validate")

    def _after_validation(self) -> None:
        pw, ph = self.mapper.physical.width, self.mapper.physical.height
        self.report = evaluate(np.array(self.val_preds), np.array(self.val_targets), self.val_ids, pw, ph)
        self.state = "results"
        good = self.report is not None and self.report.grade in ("Excellent", "Good", "Fair")
        self.accept_deadline = time.perf_counter() + 10.0 if good else 0.0

    # ------------------------------------------------------------ results

    def _accept(self) -> None:
        self.accept_deadline = 0.0
        if self.kind == "head":
            if self.head_result is not None:
                self.finished.emit("head", self.head_result)
            else:
                self.finished.emit("cancel", None)
            self.close()
            return
        if self.temp is None:
            self.finished.emit("cancel", None)
            self.close()
            return
        X, Y = self.samples.arrays()
        self.finished.emit("gaze", {"X": X, "Y": Y, "report": self.report, "preset": self.preset})
        self.close()

    def _retry(self) -> None:
        self.samples = SampleSet()
        self.val_preds, self.val_targets, self.val_ids = [], [], []
        self.report, self.temp, self.fit_error = None, None, ""
        self.head_samples, self.head_result = {}, None
        self.accept_deadline = 0.0
        if self.plan is not None:
            self.plan = build_plan(self.preset)
            self.points = self.plan.points
        self.state = "intro"
        self.countdown_end = time.perf_counter() + 3.0

    def keyPressEvent(self, e: QKeyEvent) -> None:
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.finished.emit("cancel", None)
            self.close()
        elif k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.state == "intro":
                self._begin("run")
            elif self.state == "results":
                self._accept()
        elif k == Qt.Key.Key_R and self.state == "results":
            self._retry()

    def mousePressEvent(self, e) -> None:
        if self.state == "intro":
            self._begin("run")
        elif self.state == "results":
            self._accept()

    # ------------------------------------------------------------ painting

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), BG)
        W, H = self.width(), self.height()
        if self.state == "intro":
            self._paint_intro(p, W, H)
        elif self.state in ("run", "validate"):
            self._paint_target(p, W, H)
        elif self.state == "training":
            self._center_text(p, W, H, "Training your gaze model…", "This takes a second")
        elif self.state == "results":
            self._paint_results(p, W, H)
        p.end()

    def _text(self, p, rect: QRectF, text: str, size: int = 13, bold: bool = False, color=theme.TEXT,
              align=Qt.AlignmentFlag.AlignCenter):
        f = QFont("Segoe UI", size)
        f.setBold(bold)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(rect, int(align) | int(Qt.TextFlag.TextWordWrap), text)

    def _center_text(self, p, W, H, title, sub=""):
        self._text(p, QRectF(0, H / 2 - 60, W, 50), title, 24, True)
        if sub:
            self._text(p, QRectF(W * 0.2, H / 2, W * 0.6, 80), sub, 13, color=theme.MUTED)

    def _paint_intro(self, p, W, H):
        left = max(0.0, self.countdown_end - time.perf_counter())
        if self.kind == "gaze":
            label, desc = PRESETS.get(self.preset, ("Calibration", ""))
            title = f"Gaze calibration — {label}"
            body = ("Sit how you normally sit and keep your face in view.\n"
                    "Look straight at each dot until it moves. Blink normally — blinks are filtered out.\n"
                    "When asked, keep looking at the dot while gently moving your head.")
        else:
            title = "Head range calibration"
            desc = "6 targets · ~12 s"
            body = "Turn your head so your NOSE points at each dot.\nMove comfortably — this sets how far you need to move."
        self._text(p, QRectF(0, H * 0.28, W, 50), title, 26, True)
        self._text(p, QRectF(0, H * 0.28 + 50, W, 30), desc, 13, color=theme.MUTED)
        self._text(p, QRectF(W * 0.2, H * 0.40, W * 0.6, 120), body, 14)
        state = "Face detected ✓" if self.face else "No face detected — look at the camera"
        self._text(p, QRectF(0, H * 0.58, W, 30), state, 13, color=theme.OK if self.face else theme.WARN)
        self._text(p, QRectF(0, H * 0.66, W, 60), f"Starting in {math.ceil(left)}", 30, True, theme.ACCENT)
        self._text(p, QRectF(0, H * 0.76, W, 30), "Space = start now   ·   Esc = cancel", 12, color=theme.MUTED)

    def _paint_target(self, p, W, H):
        i, point, t_in = self._current(self.elapsed)
        if point is None:
            return
        x, y = point.nx * W, point.ny * H
        seq = self._schedule()
        settle = self.plan.settle_s if self.plan else 0.7
        k = min(t_in / settle, 1.0)
        # Shrinking ring pulls the eyes onto the exact centre.
        ring = 42 - 30 * (1 - (1 - k) ** 3)
        color = QColor(theme.ACCENT_2) if self.state == "validate" else QColor(theme.ACCENT)
        p.setPen(QPen(color, 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(x, y), ring, ring)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawEllipse(QPointF(x, y), 5, 5)
        p.setBrush(QColor("#000000"))
        p.drawEllipse(QPointF(x, y), 1.8, 1.8)
        if k >= 1.0:
            prog = (t_in - settle) / max(point.duration - settle, 1e-3)
            p.setPen(QPen(QColor(theme.TEXT), 2))
            p.drawArc(QRectF(x - 18, y - 18, 36, 36), 90 * 16, -int(360 * 16 * min(prog, 1.0)))
        total = len(seq)
        phase = "Validating" if self.state == "validate" else ("Point your nose" if self.kind == "head" else "Calibrating")
        self._text(p, QRectF(0, 18, W, 24), f"{phase} · {i + 1}/{total}", 11, color=theme.MUTED)
        if point.hint and self.kind == "gaze":
            self._text(p, QRectF(0, H - 70, W, 30), point.hint, 14, True, theme.WARN)
        if not self.face:
            self._text(p, QRectF(0, H / 2 + 80, W, 30), "Paused — no face detected", 14, True, theme.WARN)
        bw = W * 0.3
        done = (sum(q.duration for q in seq[:i]) + t_in) / max(sum(q.duration for q in seq), 1e-3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SURFACE_3))
        p.drawRoundedRect(QRectF(W / 2 - bw / 2, 48, bw, 4), 2, 2)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(W / 2 - bw / 2, 48, bw * done, 4), 2, 2)

    def _paint_results(self, p, W, H):
        now = time.perf_counter()
        hint = "Enter = save   ·   R = retry   ·   Esc = cancel"
        if self.accept_deadline:
            hint = f"Saving automatically in {math.ceil(self.accept_deadline - now)} s   ·   " + hint
        if self.kind == "head":
            if self.head_result:
                self._center_text(p, W, H, "Head range calibrated ✓",
                                  "Head pointer mode now maps your comfortable range to the whole screen.")
            else:
                self._center_text(p, W, H, "Not enough head movement",
                                  "Move your head a bit further toward each dot. Press R to retry.")
            self._text(p, QRectF(0, H - 60, W, 30), hint, 12, color=theme.MUTED)
            return
        if self.report is None:
            msg = self.fit_error or "No validation samples were collected."
            self._center_text(p, W, H, "Calibration failed", msg + "\nPress R to retry.")
            self._text(p, QRectF(0, H - 60, W, 30), "R = retry   ·   Esc = cancel", 12, color=theme.MUTED)
            return
        rep = self.report
        sx = W / self.mapper.physical.width
        sy = H / self.mapper.physical.height
        for pr in rep.per_point:
            t = QPointF(pr.target[0] * sx, pr.target[1] * sy)
            m = QPointF(pr.mean_pred[0] * sx, pr.mean_pred[1] * sy)
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = rep.mean_px * sx
            p.drawEllipse(t, r, r)
            p.setPen(QPen(QColor(theme.WARN), 2))
            p.drawLine(t, m)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.TEXT))
            p.drawEllipse(t, 5, 5)
            p.setBrush(QColor(theme.WARN))
            p.drawEllipse(m, 4, 4)
        colors = {"Excellent": theme.OK, "Good": theme.ACCENT, "Fair": theme.WARN, "Poor": theme.DANGER}
        box = QRectF(W / 2 - 260, H / 2 - 110, 520, 220)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(22, 26, 33, 235))
        p.drawRoundedRect(box, 16, 16)
        self._text(p, QRectF(box.x(), box.y() + 18, box.width(), 40), rep.grade, 28, True, colors.get(rep.grade, theme.TEXT))
        self._text(p, QRectF(box.x(), box.y() + 70, box.width(), 30),
                   f"Average error {rep.mean_px:.0f} px ({rep.percent_of_width:.1f}% of screen width)", 14)
        self._text(p, QRectF(box.x(), box.y() + 100, box.width(), 30),
                   f"median {rep.median_px:.0f} px · 90th pct {rep.p90_px:.0f} px · {len(self.samples)} training samples",
                   11, color=theme.MUTED)
        tip = ("Hybrid mode will fine-tune the rest with your head." if rep.grade != "Poor"
               else "Try better lighting (light on your face, not behind you) and retry.")
        self._text(p, QRectF(box.x() + 20, box.y() + 140, box.width() - 40, 50), tip, 12, color=theme.MUTED)
        self._text(p, QRectF(0, H - 60, W, 30), hint, 12, color=theme.MUTED)
