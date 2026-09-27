"""Calibration as a UI-agnostic state machine.

The display (a fullscreen web page) only renders `view()`; all timing,
sample selection, training and validation happen here, driven by engine
snapshots. The timeline runs on snapshot timestamps and only advances while a
face is visible, so samples line up exactly with the target that was shown.

States: waiting → intro → run → training → validate → results → done
(or cancelled at any point). Head-range calibration skips training/validation.

Results can be confirmed hands-free: the freshly trained model drives a gaze
dot, and dwelling on SAVE / RETRY / CANCEL picks it — an eyes-only user can
finish calibration without touching anything.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from gazer.core.calibration import (
    HEAD_PLAN, CalibPoint, SampleSet, ValidationReport, accept_sample, build_plan, evaluate,
)
from gazer.core.gaze import GazeEstimator
from gazer.core.head import fit_head_calibration
from gazer.core.screen import ScreenRect

# Normalized result-screen buttons: x, y, w, h
BUTTONS = {
    "retry": (0.16, 0.74, 0.2, 0.14),
    "accept": (0.40, 0.74, 0.2, 0.14),
    "cancel": (0.64, 0.74, 0.2, 0.14),
}
BUTTON_DWELL_S = 1.3


@dataclass
class CalibrationResult:
    kind: str  # "gaze" | "head"
    payload: object


class CalibrationSession:
    def __init__(self, kind: str, preset: str, screen: ScreenRect, append: bool = False,
                 intro_s: float = 3.5, require_ready: bool = True, auto_accept_s: float = 12.0,
                 seed: int | None = None, async_training: bool = True):
        self.kind = kind
        self.preset = preset
        self.screen = screen
        self.append = append
        self.intro_s = intro_s
        self.auto_accept_s = auto_accept_s
        self.async_training = async_training
        self.seed = seed
        self.state = "waiting" if require_ready else "intro"
        self.now = 0.0
        self._intro_end: float | None = None
        self.elapsed = 0.0
        self._last_t: float | None = None
        self.face = False
        self.result: CalibrationResult | None = None
        self._lock = threading.Lock()
        self._reset_data()

    # -------------------------------------------------------------- setup

    def _reset_data(self) -> None:
        if self.kind == "gaze":
            self.plan = build_plan(self.preset, self.seed)
            self.points = self.plan.points
            self.validation = self.plan.validation
        else:
            self.plan = None
            self.points = [CalibPoint(x, y, 2.0, name) for name, x, y in HEAD_PLAN]
            self.validation = []
        self.samples = SampleSet()
        self.point_counts: dict[int, int] = {}
        self.val_preds: list[np.ndarray] = []
        self.val_targets: list[tuple[float, float]] = []
        self.val_ids: list[int] = []
        self.head_samples: dict[str, list[np.ndarray]] = {}
        self.temp: GazeEstimator | None = None
        self.report: ValidationReport | None = None
        self.head_result = None
        self.error = ""
        self.results_since = 0.0
        self.live_gaze: np.ndarray | None = None
        self.hover: str | None = None
        self.hover_since = 0.0

    # ----------------------------------------------------------- controls

    @property
    def finished(self) -> bool:
        return self.state in ("done", "cancelled")

    def ready(self) -> None:
        """The display is up (fullscreen) — start the intro countdown."""
        if self.state == "waiting":
            self.state = "intro"
            self._intro_end = None

    def skip_intro(self) -> None:
        if self.state in ("waiting", "intro"):
            self._begin("run")

    def cancel(self) -> None:
        self.state = "cancelled"

    def retry(self) -> None:
        self._reset_data()
        self.state = "intro"
        self._intro_end = None

    def accept(self) -> CalibrationResult | None:
        if self.state != "results":
            return None
        if self.kind == "head":
            self.result = CalibrationResult("head", self.head_result) if self.head_result else None
        elif self.temp is not None:
            X, Y = self.samples.arrays()
            self.result = CalibrationResult("gaze", {"X": X, "Y": Y, "report": self.report,
                                                     "preset": self.preset, "append": self.append})
        self.state = "done" if self.result else "cancelled"
        return self.result

    def _begin(self, state: str) -> None:
        self.state = state
        self.elapsed = 0.0

    # ------------------------------------------------------------ timeline

    def sequence(self) -> list[CalibPoint]:
        return self.validation if self.state == "validate" else self.points

    def current(self, elapsed: float | None = None) -> tuple[int | None, CalibPoint | None, float]:
        el = self.elapsed if elapsed is None else elapsed
        acc = 0.0
        for i, p in enumerate(self.sequence()):
            if el < acc + p.duration:
                return i, p, el - acc
            acc += p.duration
        return None, None, 0.0

    def target(self) -> tuple[float, float] | None:
        """Where the user should be looking right now (for the simulator)."""
        if self.state in ("run", "validate"):
            _, p, _ = self.current()
            return None if p is None else (p.nx, p.ny)
        return None

    def on_snapshot(self, snap) -> None:
        """Feed every engine snapshot (engine thread)."""
        with self._lock:
            t = snap.t
            dt = 0.0 if self._last_t is None else min(max(t - self._last_t, 0.0), 0.25)
            self._last_t = t
            self.now = t
            self.face = snap.face
            if self.state == "intro":
                if self._intro_end is None:
                    self._intro_end = t + self.intro_s
                elif t >= self._intro_end:
                    self._begin("run")
                return
            if self.state in ("run", "validate"):
                if self.face:
                    self.elapsed += dt
                self._collect(snap)
                i, _, _ = self.current()
                if i is None:
                    if self.state == "run":
                        self._after_run()
                    else:
                        self._after_validation()
                return
            if self.state == "results":
                self._results_gaze(snap, t)

    def _collect(self, snap) -> None:
        f = snap.features
        if f is None:
            return
        i, point, t_in = self.current()
        if point is None:
            return
        if self.kind == "head":
            if 0.7 <= t_in <= point.duration - 0.1:
                self.head_samples.setdefault(point.hint, []).append(np.asarray(f.head_point).copy())
                self.point_counts[i] = self.point_counts.get(i, 0) + 1
            return
        if not accept_sample(self.plan, point, t_in, f, snap.closure):
            return
        if self.state == "run":
            self.samples.add(f.gaze_vector, (point.nx, point.ny), i)
            self.point_counts[i] = self.point_counts.get(i, 0) + 1
        elif self.temp is not None:
            pred = self.temp.predict(f.gaze_vector)
            if pred is not None:
                self.val_preds.append(np.array(pred))
                self.val_targets.append((point.nx, point.ny))
                self.val_ids.append(i)
                self.point_counts[1000 + i] = self.point_counts.get(1000 + i, 0) + 1

    def _after_run(self) -> None:
        if self.kind == "head":
            self.head_result = fit_head_calibration(self.head_samples, self.screen.width, self.screen.height)
            if self.head_result is None:
                self.error = "Not enough head movement — turn a little further toward each dot."
            self._to_results()
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
                self._begin("validate")
            except Exception as exc:  # noqa: BLE001 — shown to the user
                self.error = str(exc)
                self._to_results()

        if self.async_training:
            threading.Thread(target=work, name="gazer-calib-fit", daemon=True).start()
        else:
            work()

    def _after_validation(self) -> None:
        self.report = evaluate(np.array(self.val_preds), np.array(self.val_targets), self.val_ids,
                               self.screen.width, self.screen.height)
        if self.report is None:
            self.error = "No validation samples were collected."
        self._to_results()

    def _to_results(self) -> None:
        self.state = "results"
        self.results_since = self.now
        self.live_gaze = None
        self.hover = None

    @property
    def good(self) -> bool:
        if self.kind == "head":
            return self.head_result is not None
        return self.report is not None and self.report.grade != "Poor"

    def _results_gaze(self, snap, t: float) -> None:
        # Hands-free confirmation with the model we just trained.
        if self.good and self.auto_accept_s and t - self.results_since >= self.auto_accept_s:
            self.accept()
            return
        f = snap.features
        if self.temp is None or f is None or max(snap.closure) > 0.5:
            return
        pred = self.temp.predict(f.gaze_vector)
        if pred is None:
            return
        p = np.array(pred)
        self.live_gaze = p if self.live_gaze is None else self.live_gaze + (p - self.live_gaze) * 0.35
        hit = None
        for name, (x, y, w, h) in BUTTONS.items():
            pad = 0.03
            if x - pad <= self.live_gaze[0] <= x + w + pad and y - pad <= self.live_gaze[1] <= y + h + pad:
                hit = name
        if hit != self.hover:
            self.hover, self.hover_since = hit, t
            return
        if hit and t - self.hover_since >= BUTTON_DWELL_S and t - self.results_since > 1.0:
            if hit == "accept" and self.good:
                self.accept()
            elif hit == "retry":
                self.retry()
            elif hit == "cancel":
                self.cancel()

    # ---------------------------------------------------------------- view

    def view(self) -> dict:
        v: dict = {"kind": self.kind, "preset": self.preset, "state": self.state, "face": self.face,
                   "error": self.error}
        if self.state == "intro":
            v["countdown"] = max(0.0, (self._intro_end or self.now + self.intro_s) - self.now)
        if self.state in ("run", "validate"):
            seq = self.sequence()
            i, p, t_in = self.current()
            total_dur = sum(q.duration for q in seq) or 1.0
            done = sum(q.duration for q in seq[: i or 0]) + t_in
            v.update(
                index=i, total=len(seq), t_in=t_in, progress=done / total_dur,
                settle=self.plan.settle_s if self.plan else 0.7,
                duration=p.duration if p else 0, hint=p.hint if p else "",
                target=[p.nx, p.ny] if p else None,
                captured=self.point_counts.get((1000 if self.state == "validate" else 0) + (i or 0), 0),
                points=[[q.nx, q.ny] for q in seq],
            )
        if self.state == "results":
            v["good"] = self.good
            v["accept_in"] = (max(0.0, self.auto_accept_s - (self.now - self.results_since))
                              if self.good and self.auto_accept_s else None)
            v["gaze"] = None if self.live_gaze is None else [float(self.live_gaze[0]), float(self.live_gaze[1])]
            v["buttons"] = {name: {"rect": list(r),
                                   "progress": (min(1.0, (self.now - self.hover_since) / BUTTON_DWELL_S)
                                                if self.hover == name else 0.0),
                                   "enabled": name != "accept" or self.good}
                            for name, r in BUTTONS.items()}
            v["samples"] = len(self.samples)
            if self.report is not None:
                r = self.report
                W, H = self.screen.width, self.screen.height
                v["report"] = {
                    "grade": r.grade, "mean_px": r.mean_px, "median_px": r.median_px, "p90_px": r.p90_px,
                    "percent": r.percent_of_width,
                    "points": [{"target": [pp.target[0] / W, pp.target[1] / H],
                                "pred": [pp.mean_pred[0] / W, pp.mean_pred[1] / H], "error": pp.error_px}
                               for pp in r.per_point],
                }
            if self.kind == "head":
                v["head_ok"] = self.head_result is not None
        return v
