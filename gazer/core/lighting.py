"""Lighting advisor: is the face lit well enough for accurate gaze tracking?

Iris landmarks get noisy long before the face tracker fails, so bad light
quietly costs accuracy. This compares the face's brightness with the
background and between the two halves of the face, and says what to fix:
too dark, backlit (a window behind you), lit from one side, or overexposed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

ADVICE = {
    "good": "Lighting is good.",
    "dark": "Too dark — turn on a light in front of you.",
    "backlit": "Backlit — a bright window or lamp is behind you. Face the light or close the blind.",
    "uneven": "Light comes from one side — add light on the darker side of your face.",
    "bright": "Overexposed — dim the light shining straight into your face.",
    "unknown": "No face in view.",
}


@dataclass
class LightingReport:
    status: str
    face: float  # mean luma of the face (0..255)
    background: float
    balance: float  # |left - right| / face luma
    advice: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["face"] = round(self.face, 1)
        d["background"] = round(self.background, 1)
        d["balance"] = round(self.balance, 3)
        return d


def analyze(gray: np.ndarray, points: np.ndarray | None) -> LightingReport:
    """`gray` is an (H, W) uint8 frame, `points` the (N, 2+) landmarks in its pixels."""
    if points is None or len(points) < 10:
        return LightingReport("unknown", 0.0, float(gray.mean()), 0.0, ADVICE["unknown"])
    h, w = gray.shape[:2]
    xs, ys = points[:, 0], points[:, 1]
    x0, x1 = int(max(xs.min(), 0)), int(min(xs.max(), w - 1))
    y0, y1 = int(max(ys.min(), 0)), int(min(ys.max(), h - 1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return LightingReport("unknown", 0.0, float(gray.mean()), 0.0, ADVICE["unknown"])
    # inner face box (skip hair/background at the edges of the landmark hull)
    mx, my = (x1 - x0) // 6, (y1 - y0) // 8
    face = gray[y0 + my:y1 - my, x0 + mx:x1 - mx].astype(np.float64)
    fl = float(face.mean())
    half = face.shape[1] // 2
    left, right = float(face[:, :half].mean()), float(face[:, half:].mean())
    mask = np.ones(gray.shape, dtype=bool)
    mask[y0:y1, x0:x1] = False
    bg = float(gray[mask].mean()) if mask.any() else fl
    balance = abs(left - right) / max(fl, 1.0)
    if fl < 55:
        status = "dark"
    elif bg - fl > 45 and bg > 120:
        status = "backlit"
    elif fl > 225:
        status = "bright"
    elif balance > 0.35:
        status = "uneven"
    else:
        status = "good"
    return LightingReport(status, fl, bg, balance, ADVICE[status])
