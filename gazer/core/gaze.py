"""Gaze estimator: calibrated regression + drift correction + learning from use.

Implicit calibration: whenever the user clicks, they were almost certainly
looking at the click point. If the raw gaze prediction was reasonably close
(so they weren't looking elsewhere), that (features → click position) pair is
a high-quality training sample. The estimator applies an immediate bias
correction and periodically refits with these samples, so accuracy improves
the more Gazer is used.
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path

import numpy as np

from gazer.core.features import FEATURE_VERSION, GAZE_CORE, GAZE_DIM
from gazer.core.regression import FitReport, PolyRidge

IMPLICIT_MAX = 1500
IMPLICIT_WEIGHT = 2.0
IMPLICIT_ACCEPT_RADIUS = 0.22  # normalized; farther means the user looked elsewhere
REFIT_EVERY = 20


class GazeEstimator:
    def __init__(self):
        self.model: PolyRidge | None = None
        self.X = np.zeros((0, GAZE_DIM))
        self.Y = np.zeros((0, 2))
        self.implicit: deque[tuple[np.ndarray, np.ndarray]] = deque(maxlen=IMPLICIT_MAX)
        self.bias = np.zeros(2)
        self.last_report: FitReport | None = None
        self._since_refit = 0
        self._refitting = False
        self._lock = threading.Lock()
        self.live_error: float | None = None  # EMA of |prediction - true target| (normalized)
        self.live_count = 0

    @property
    def ready(self) -> bool:
        return self.model is not None

    @property
    def n_samples(self) -> int:
        return len(self.X)

    # ----------------------------------------------------------- training

    def fit(self, X: np.ndarray, Y: np.ndarray, append: bool = False) -> FitReport:
        X = np.asarray(X, dtype=np.float64).reshape(-1, GAZE_DIM)
        Y = np.asarray(Y, dtype=np.float64).reshape(-1, 2)
        if append and len(self.X):
            X = np.vstack([self.X, X])
            Y = np.vstack([self.Y, Y])
        report = self._fit_all(X, Y)
        self.X, self.Y = X, Y
        return report

    def _fit_all(self, X: np.ndarray, Y: np.ndarray) -> FitReport:
        with self._lock:
            imp = list(self.implicit)
        w = np.ones(len(X))
        if imp:
            X = np.vstack([X, np.array([a for a, _ in imp])])
            Y = np.vstack([Y, np.array([b for _, b in imp])])
            w = np.concatenate([w, np.full(len(imp), IMPLICIT_WEIGHT)])
        model = PolyRidge(len(GAZE_CORE))
        report = model.fit(X, Y, w)
        self.model = model  # atomic swap
        self.bias = np.zeros(2)
        self.last_report = report
        return report

    # --------------------------------------------------------- prediction

    def predict(self, vector: np.ndarray) -> tuple[float, float] | None:
        model = self.model
        if model is None:
            return None
        p = model.predict(vector)[0] + self.bias
        return float(np.clip(p[0], -0.05, 1.05)), float(np.clip(p[1], -0.05, 1.05))

    def predict_raw(self, vector: np.ndarray) -> np.ndarray | None:
        model = self.model
        return None if model is None else model.predict(vector)[0]

    # ---------------------------------------------------- implicit learning

    def learn_from_click(self, vector: np.ndarray, target_n: tuple[float, float]) -> bool:
        """Record that the user was looking at `target_n` (normalized)."""
        raw = self.predict_raw(vector)
        if raw is None:
            return False
        target = np.asarray(target_n, dtype=np.float64)
        pred = raw + self.bias
        err = float(np.hypot(*(pred - target)))
        if err > IMPLICIT_ACCEPT_RADIUS:
            return False
        self.live_error = err if self.live_error is None else 0.85 * self.live_error + 0.15 * err
        self.live_count += 1
        with self._lock:
            self.implicit.append((np.asarray(vector, dtype=np.float64).copy(), target))
        # Fast path: nudge a global offset right away.
        self.bias = 0.8 * self.bias + 0.2 * (target - raw)
        self._since_refit += 1
        if self._since_refit >= REFIT_EVERY and len(self.X) >= 8:
            self._since_refit = 0
            self._refit_async()
        return True

    def _refit_async(self) -> None:
        if self._refitting:
            return
        self._refitting = True

        def work():
            try:
                self._fit_all(self.X, self.Y)
            except Exception:
                pass
            finally:
                self._refitting = False

        threading.Thread(target=work, name="gazer-refit", daemon=True).start()

    def nudge(self, pred_n: tuple[float, float], target_n: tuple[float, float]) -> None:
        """One-shot drift fix: shift so `pred_n` maps onto `target_n`."""
        self.bias = self.bias + (np.asarray(target_n) - np.asarray(pred_n))

    def clear_implicit(self) -> None:
        with self._lock:
            self.implicit.clear()
        self.bias = np.zeros(2)
        if len(self.X) >= 8:
            self._fit_all(self.X, self.Y)

    def reset(self) -> None:
        self.model = None
        self.X = np.zeros((0, GAZE_DIM))
        self.Y = np.zeros((0, 2))
        with self._lock:
            self.implicit.clear()
        self.bias = np.zeros(2)
        self.last_report = None

    # --------------------------------------------------------- persistence

    def save(self, path: Path) -> None:
        with self._lock:
            imp = list(self.implicit)
        data = {
            "version": np.array(FEATURE_VERSION),
            "X": self.X, "Y": self.Y,
            "IX": np.array([a for a, _ in imp]) if imp else np.zeros((0, GAZE_DIM)),
            "IY": np.array([b for _, b in imp]) if imp else np.zeros((0, 2)),
            "bias": self.bias,
        }
        if self.model is not None:
            data.update(self.model.to_arrays("m_"))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.stem + ".tmp.npz")
        np.savez_compressed(tmp, **data)
        tmp.replace(path)

    def load(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            with np.load(path) as data:
                if int(data["version"]) != FEATURE_VERSION:
                    return False
                self.X = np.asarray(data["X"]).reshape(-1, GAZE_DIM)
                self.Y = np.asarray(data["Y"]).reshape(-1, 2)
                with self._lock:
                    self.implicit.clear()
                    for a, b in zip(np.asarray(data["IX"]), np.asarray(data["IY"])):
                        self.implicit.append((a, b))
                self.bias = np.asarray(data["bias"])
                self.model = PolyRidge.from_arrays(data, "m_")
            return True
        except Exception:
            return False
