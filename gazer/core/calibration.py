"""Calibration planning, clean sample collection and accuracy reporting.

v1 labelled every frame with wherever a moving dot happened to be, including
while the eyes were still catching up (~200 ms saccade latency) or blinking.
Here targets are fixations: samples are only taken after the eyes settle,
blinks and poor-quality frames are dropped, and accuracy is measured on
held-out validation points the model never trained on.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np

from gazer.core.features import FaceFeatures


@dataclass
class CalibPoint:
    nx: float
    ny: float
    duration: float = 1.5
    hint: str = ""


@dataclass
class CalibrationPlan:
    name: str
    points: list[CalibPoint]
    validation: list[CalibPoint]
    settle_s: float = 0.45
    tail_s: float = 0.08

    @property
    def total_seconds(self) -> float:
        return sum(p.duration for p in self.points) + sum(p.duration for p in self.validation)


PRESETS = {
    "quick": ("Quick", "9 points · ~20 s"),
    "standard": ("Standard", "16 points + head motion · ~50 s"),
    "deep": ("Deep", "25 points + head motion · ~85 s"),
}

HEAD_MOVE_HINT = "Keep looking at the dot and gently move your head around"


def grid(rows: int, cols: int, margin: float) -> list[tuple[float, float]]:
    return [
        (margin + (1 - 2 * margin) * c / max(cols - 1, 1), margin + (1 - 2 * margin) * r / max(rows - 1, 1))
        for r in range(rows) for c in range(cols)
    ]


def _order(points: list[tuple[float, float]], rng: random.Random) -> list[tuple[float, float]]:
    """Random but avoid consecutive points that are very close (they don't
    trigger a clean saccade)."""
    pts = points[:]
    rng.shuffle(pts)
    for i in range(1, len(pts)):
        if np.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) < 0.15:
            for j in range(i + 1, len(pts)):
                if np.hypot(pts[j][0] - pts[i - 1][0], pts[j][1] - pts[i - 1][1]) >= 0.15:
                    pts[i], pts[j] = pts[j], pts[i]
                    break
    return pts


def build_plan(preset: str = "standard", seed: int | None = None) -> CalibrationPlan:
    rng = random.Random(seed)
    val = [CalibPoint(x, y, 1.6) for x, y in _order(
        [(0.3, 0.25), (0.7, 0.3), (0.5, 0.55), (0.25, 0.75), (0.75, 0.72), (0.15, 0.45), (0.85, 0.5)], rng)]
    if preset == "quick":
        pts = [CalibPoint(x, y, 1.5) for x, y in _order(grid(3, 3, 0.08), rng)]
        return CalibrationPlan("quick", pts, val[:5])
    if preset == "deep":
        pts = [CalibPoint(x, y, 1.5) for x, y in _order(grid(5, 5, 0.05), rng)]
        pts += [CalibPoint(x, y, 2.4, HEAD_MOVE_HINT) for x, y in _order(grid(3, 3, 0.15), rng)]
        pts += [CalibPoint(rng.uniform(0.06, 0.94), rng.uniform(0.06, 0.94), 1.5) for _ in range(6)]
        return CalibrationPlan("deep", pts, val)
    pts = [CalibPoint(x, y, 1.5) for x, y in _order(grid(4, 4, 0.06), rng)]
    pts += [CalibPoint(x, y, 2.4, HEAD_MOVE_HINT) for x, y in _order(grid(3, 3, 0.15), rng)]
    return CalibrationPlan("standard", pts, val[:6])


def accept_sample(plan: CalibrationPlan, point: CalibPoint, t_in_point: float, f: FaceFeatures | None,
                  closure: tuple[float, float] | None = None) -> bool:
    if f is None:
        return False
    if t_in_point < plan.settle_s or t_in_point > point.duration - plan.tail_s:
        return False
    if f.quality < 0.3:
        return False
    if closure is not None and max(closure) > 0.45:
        return False
    return True


@dataclass
class SampleSet:
    X: list[np.ndarray] = field(default_factory=list)
    Y: list[tuple[float, float]] = field(default_factory=list)
    point_ids: list[int] = field(default_factory=list)

    def add(self, vec: np.ndarray, target: tuple[float, float], pid: int) -> None:
        self.X.append(np.asarray(vec, dtype=np.float64).copy())
        self.Y.append(target)
        self.point_ids.append(pid)

    def __len__(self) -> int:
        return len(self.X)

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.X:
            return np.zeros((0, 0)), np.zeros((0, 2))
        return np.vstack(self.X), np.asarray(self.Y, dtype=np.float64)


@dataclass
class PointResult:
    target: tuple[float, float]  # px
    mean_pred: tuple[float, float]  # px
    error_px: float
    n: int


@dataclass
class ValidationReport:
    mean_px: float
    median_px: float
    p90_px: float
    per_point: list[PointResult]
    grade: str
    screen_w: int

    @property
    def percent_of_width(self) -> float:
        return 100.0 * self.mean_px / max(self.screen_w, 1)


def grade_for(mean_px: float, screen_w: int) -> str:
    frac = mean_px / max(screen_w, 1)
    if frac < 0.03:
        return "Excellent"
    if frac < 0.05:
        return "Good"
    if frac < 0.08:
        return "Fair"
    return "Poor"


def evaluate(preds_n: np.ndarray, targets_n: np.ndarray, point_ids: list[int], screen_w: int,
             screen_h: int) -> ValidationReport | None:
    if len(preds_n) == 0:
        return None
    scale = np.array([screen_w, screen_h], dtype=np.float64)
    P = np.asarray(preds_n) * scale
    T = np.asarray(targets_n) * scale
    errs = np.linalg.norm(P - T, axis=1)
    per = []
    ids = np.asarray(point_ids)
    for pid in sorted(set(point_ids)):
        m = ids == pid
        mp = np.median(P[m], axis=0)
        tg = T[m][0]
        per.append(PointResult((float(tg[0]), float(tg[1])), (float(mp[0]), float(mp[1])),
                               float(np.hypot(*(mp - tg))), int(m.sum())))
    mean = float(np.mean(errs))
    return ValidationReport(mean, float(np.median(errs)), float(np.percentile(errs, 90)), per,
                            grade_for(mean, screen_w), screen_w)


HEAD_PLAN = [("center", 0.5, 0.5), ("left", 0.12, 0.5), ("right", 0.88, 0.5), ("top", 0.5, 0.12),
             ("bottom", 0.5, 0.88), ("center", 0.5, 0.5)]
