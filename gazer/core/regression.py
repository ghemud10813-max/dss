"""Polynomial ridge regression with CV-chosen regularisation and outlier rejection.

Webcam gaze mapping is a small-data, noisy-label problem: a few thousand
samples, labels that are wrong whenever the user glanced away. Closed-form
ridge on quadratic features handles this better than a neural net — it
trains in milliseconds, can't overfit wildly, and is trivially refit online.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ALPHAS = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0)


@dataclass
class FitReport:
    n_samples: int
    n_used: int
    alpha: float
    rmse: float  # in target units (normalized screen)


class PolyRidge:
    def __init__(self, n_core: int):
        self.n_core = n_core
        self.mean: np.ndarray | None = None
        self.std: np.ndarray | None = None
        self.coef: np.ndarray | None = None
        self.intercept: np.ndarray | None = None
        self.alpha = 1.0

    @property
    def fitted(self) -> bool:
        return self.coef is not None

    def _expand(self, Xs: np.ndarray) -> np.ndarray:
        core = Xs[:, : self.n_core]
        i, j = np.triu_indices(self.n_core)
        return np.hstack([Xs, core[:, i] * core[:, j]])

    @staticmethod
    def _solve(Phi, Y, w, alpha):
        sw = w / w.sum()
        pm = sw @ Phi
        ym = sw @ Y
        Pc = Phi - pm
        Yc = Y - ym
        A = (Pc * w[:, None]).T @ Pc
        A[np.diag_indices_from(A)] += alpha * w.sum() / len(w)
        B = (Pc * w[:, None]).T @ Yc
        coef = np.linalg.solve(A, B)
        return coef, ym - pm @ coef

    def _choose_alpha(self, Phi, Y, w, folds: int = 5) -> float:
        n = len(Phi)
        if n < 40:
            return 1.0
        rng = np.random.default_rng(0)
        idx = rng.permutation(n)
        parts = np.array_split(idx, folds)
        best, best_err = 1.0, np.inf
        for a in ALPHAS:
            err = 0.0
            for k in range(folds):
                test = parts[k]
                train = np.concatenate([parts[m] for m in range(folds) if m != k])
                coef, b = self._solve(Phi[train], Y[train], w[train], a)
                r = Phi[test] @ coef + b - Y[test]
                err += float(np.sum(w[test] * np.sum(r * r, axis=1)))
            if err < best_err:
                best, best_err = a, err
        return best

    def fit(self, X: np.ndarray, Y: np.ndarray, w: np.ndarray | None = None) -> FitReport:
        X = np.asarray(X, dtype=np.float64)
        Y = np.asarray(Y, dtype=np.float64)
        w = np.ones(len(X)) if w is None else np.asarray(w, dtype=np.float64)
        if len(X) < 8:
            raise ValueError("need at least 8 samples")
        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0)
        self.std[self.std < 1e-8] = 1.0
        Phi = self._expand((X - self.mean) / self.std)

        keep = np.ones(len(X), dtype=bool)
        self.alpha = self._choose_alpha(Phi, Y, w)
        self.coef, self.intercept = self._solve(Phi, Y, w, self.alpha)
        for _ in range(2):
            r = np.linalg.norm(Phi @ self.coef + self.intercept - Y, axis=1)
            med = np.median(r)
            mad = np.median(np.abs(r - med)) * 1.4826 + 1e-9
            new_keep = r <= med + 3.0 * mad
            if new_keep.sum() < 0.7 * len(X) or np.array_equal(new_keep, keep):
                break
            keep = new_keep
            self.coef, self.intercept = self._solve(Phi[keep], Y[keep], w[keep], self.alpha)
        r = Phi[keep] @ self.coef + self.intercept - Y[keep]
        rmse = float(np.sqrt(np.mean(np.sum(r * r, axis=1))))
        return FitReport(len(X), int(keep.sum()), self.alpha, rmse)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("model not fitted")
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        return self._expand((X - self.mean) / self.std) @ self.coef + self.intercept

    def to_arrays(self, prefix: str = "") -> dict[str, np.ndarray]:
        if not self.fitted:
            return {}
        return {
            f"{prefix}n_core": np.array(self.n_core), f"{prefix}mean": self.mean,
            f"{prefix}std": self.std, f"{prefix}coef": self.coef,
            f"{prefix}intercept": self.intercept, f"{prefix}alpha": np.array(self.alpha),
        }

    @classmethod
    def from_arrays(cls, data, prefix: str = "") -> "PolyRidge | None":
        if f"{prefix}coef" not in data:
            return None
        m = cls(int(data[f"{prefix}n_core"]))
        m.mean = np.asarray(data[f"{prefix}mean"])
        m.std = np.asarray(data[f"{prefix}std"])
        m.coef = np.asarray(data[f"{prefix}coef"])
        m.intercept = np.asarray(data[f"{prefix}intercept"])
        m.alpha = float(data[f"{prefix}alpha"])
        return m
