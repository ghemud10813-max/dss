"""A synthetic user: a full 478-landmark face driven by a model of human gaze.

Used for demo mode (no webcam needed) and for end-to-end tests. The face is
rendered from a canonical mesh, rotated/translated by a head pose, with irises
placed by an eyeball model looking at a point on a virtual monitor. Output is
a real `FaceObservation`, so everything downstream — feature extraction,
calibration, regression, pointer fusion, gestures — runs exactly as it would
on camera data.

Behaviour: saccades with human latency, fixational jitter, head that partly
follows the eyes, natural blinks (which the engine must ignore), and the odd
deliberate gesture. `attend()` lets calibration pin the eyes to a target.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass

import numpy as np

from gazer.core.tracker import FaceObservation
from gazer.paths import ASSETS_DIR

MESH_PATH = ASSETS_DIR / "face_mesh.json"

# Eye landmark groups (canonical mesh: x right, y up, z toward the viewer).
_UPPER_A = [246, 161, 160, 159, 158, 157, 173]
_LOWER_A = [7, 163, 144, 145, 153, 154, 155]
_UPPER_B = [466, 388, 387, 386, 385, 384, 398]
_LOWER_B = [249, 390, 373, 374, 380, 381, 382]
_BROW_A = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
_BROW_B = [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]
_IRIS_A = [468, 469, 470, 471, 472]
_IRIS_B = [473, 474, 475, 476, 477]

# Virtual desk geometry (metres): 24" 16:9 monitor, camera on top, 60 cm away.
SCREEN_W_M = 0.53
SCREEN_H_M = 0.30
VIEW_DIST_M = 0.60
EYE_LEVEL = 0.30  # normalized screen height at eye level


def load_mesh() -> tuple[np.ndarray, list[tuple[int, int]]]:
    data = json.loads(MESH_PATH.read_text(encoding="utf-8"))
    return np.asarray(data["points"], dtype=np.float64), [tuple(e) for e in data["edges"]]


def rotation(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Head rotation in a y-up frame. +yaw turns toward image right, +pitch
    looks down, +roll tilts toward the user's right shoulder (mirrored view)."""
    y, p, r = map(math.radians, (yaw_deg, pitch_deg, -roll_deg))
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    Rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


@dataclass
class _Expr:
    """A timed expression pulse (smile, brow raise, …)."""

    name: str
    start: float
    length: float

    def value(self, t: float) -> float:
        k = (t - self.start) / self.length
        if k < 0 or k > 1:
            return 0.0
        return math.sin(math.pi * min(1.0, k * 1.6)) if k < 0.3 else (1.0 if k < 0.8 else (1 - k) / 0.2)


class SyntheticUser:
    def __init__(self, seed: int | None = 7, frame_size: tuple[int, int] = (1280, 720),
                 noise_px: float = 0.25, expressive: bool = True):
        self.rng = np.random.default_rng(seed)
        self.base, self.edges = load_mesh()
        self.w, self.h = frame_size
        self.noise_px = noise_px
        self.expressive = expressive
        self.px_per_unit = 135.0  # canonical half-face-width → pixels
        # eyeball geometry derived from the canonical eye corners
        self._eye_c = {
            "A": (self.base[33] + self.base[133]) / 2,
            "B": (self.base[362] + self.base[263]) / 2,
        }
        eye_w = float(np.linalg.norm(self.base[133] - self.base[33]))
        self.eye_radius = 0.42 * eye_w
        self.iris_radius = 0.19 * eye_w
        # state
        self.t0: float | None = None
        self.gaze = np.array([0.5, 0.45])  # where the eyes point now (normalized screen)
        self.focus = self.gaze.copy()  # where attention is
        self._sacc_from = self.gaze.copy()
        self._sacc_to = self.gaze.copy()
        self._sacc_start = -1.0
        self._sacc_len = 0.04
        self._pending: tuple[float, np.ndarray] | None = None
        self._next_fix = 0.0
        self._reading: list[np.ndarray] = []
        self._attend: np.ndarray | None = None
        self.head_motion = 1.0
        self.head = np.zeros(3)  # yaw, pitch, roll (deg)
        self.head_pos = np.zeros(2)  # metres
        self._next_blink = 1.5
        self._blink_start = -10.0
        self._exprs: list[_Expr] = []
        self._next_expr = 6.0
        self.present = True
        self.phase = self.rng.uniform(0, 100, size=8)

    # ------------------------------------------------------------ control

    def attend(self, target: tuple[float, float] | None) -> None:
        """Pin attention to a normalized screen point (None = free viewing)."""
        self._attend = None if target is None else np.asarray(target, dtype=np.float64)

    def trigger(self, name: str, length: float = 0.6, t: float | None = None) -> None:
        """Perform an expression now: smile, brow_raise, mouth_open, pucker, wink_left, wink_right."""
        self._exprs.append(_Expr(name, self._now(t), length))

    def _now(self, t: float | None) -> float:
        if t is None:
            t = time.perf_counter()
        if self.t0 is None:
            self.t0 = t
        return t - self.t0

    # ------------------------------------------------------------ behaviour

    def _choose_focus(self, t: float) -> None:
        if self._attend is not None:
            if np.hypot(*(self._attend - self.focus)) > 1e-6:
                self._set_focus(self._attend, t)
            return
        if t < self._next_fix:
            return
        rng = self.rng
        if self._reading:
            nxt = self._reading.pop(0)
            dur = rng.uniform(0.18, 0.3)
        elif rng.random() < 0.25:
            y = rng.uniform(0.2, 0.8)
            x0 = rng.uniform(0.1, 0.4)
            self._reading = [np.array([x0 + 0.055 * i, y]) for i in range(1, rng.integers(6, 11))]
            nxt = np.array([x0, y])
            dur = 0.25
        else:
            nxt = np.clip(rng.normal([0.5, 0.48], [0.22, 0.2]), 0.03, 0.97)
            dur = float(np.clip(rng.lognormal(math.log(0.45), 0.4), 0.15, 1.6))
        self._set_focus(nxt, t)
        self._next_fix = t + dur

    def _set_focus(self, target: np.ndarray, t: float) -> None:
        self.focus = np.asarray(target, dtype=np.float64).copy()
        latency = float(np.clip(self.rng.normal(0.19, 0.03), 0.12, 0.3))
        self._pending = (t + latency, self.focus.copy())

    def _update_eyes(self, t: float) -> None:
        if self._pending and t >= self._pending[0]:
            self._sacc_from = self.gaze.copy()
            self._sacc_to = self._pending[1]
            amp = float(np.hypot(*(self._sacc_to - self._sacc_from)))
            self._sacc_len = 0.025 + 0.06 * amp
            self._sacc_start = t
            self._pending = None
        k = (t - self._sacc_start) / self._sacc_len
        if 0 <= k < 1:
            s = 0.5 - 0.5 * math.cos(math.pi * k)
            self.gaze = self._sacc_from + (self._sacc_to - self._sacc_from) * s
        elif k >= 1:
            # fixation: tiny drift + tremor around the saccade landing point
            self.gaze = self._sacc_to + self.rng.normal(0, 0.0025, 2)

    def _update_head(self, t: float, dt: float) -> None:
        ph = self.phase
        m = self.head_motion
        sway = np.array([
            2.2 * math.sin(0.31 * t + ph[0]) + 1.1 * math.sin(0.83 * t + ph[1]),
            1.6 * math.sin(0.27 * t + ph[2]) + 0.8 * math.sin(0.71 * t + ph[3]),
            1.2 * math.sin(0.19 * t + ph[4]),
        ]) * m
        ax, ay = self._angles(self.gaze)
        follow = np.array([math.degrees(ax) * 0.28, math.degrees(ay) * 0.22, 0.0])
        target = follow + sway
        a = 1 - math.exp(-dt / 0.35)
        self.head = self.head + (target - self.head) * a
        self.head_pos = 0.012 * m * np.array([math.sin(0.23 * t + ph[5]), math.sin(0.17 * t + ph[6])])

    def _angles(self, g: np.ndarray) -> tuple[float, float]:
        X = (g[0] - 0.5) * SCREEN_W_M - self.head_pos[0]
        Y = (g[1] - EYE_LEVEL) * SCREEN_H_M - self.head_pos[1]
        return math.atan2(X, VIEW_DIST_M), math.atan2(Y, VIEW_DIST_M)

    def _closure(self, t: float) -> tuple[float, float]:
        if t >= self._next_blink:
            self._blink_start = t
            self._next_blink = t + float(np.clip(self.rng.exponential(3.2), 0.8, 9.0))
        k = (t - self._blink_start) / 0.16
        c = math.sin(math.pi * k) ** 0.6 if 0 <= k <= 1 else 0.0
        cl = cr = c
        for e in self._exprs:
            v = e.value(t)
            if e.name == "wink_left":
                cl = max(cl, v)
            elif e.name == "wink_right":
                cr = max(cr, v)
            elif e.name in ("long_blink", "double_blink"):
                cl = max(cl, v)
                cr = max(cr, v)
        return cl, cr

    def _expressions(self, t: float) -> dict[str, float]:
        if self.expressive and self._attend is None and t >= self._next_expr:
            name = str(self.rng.choice(["smile", "smile", "brow_raise", "mouth_open"]))
            self.trigger(name, float(self.rng.uniform(0.5, 1.0)), t=self.t0 + t)
            self._next_expr = t + float(self.rng.uniform(8, 16))
        self._exprs = [e for e in self._exprs if t - e.start < e.length + 0.1]
        vals: dict[str, float] = {}
        for e in self._exprs:
            vals[e.name] = max(vals.get(e.name, 0.0), e.value(t))
        return vals

    # --------------------------------------------------------------- render

    def step(self, t: float | None = None) -> FaceObservation | None:
        tt = self._now(t)
        dt = 1 / 30 if not hasattr(self, "_last") else max(1e-3, min(tt - self._last, 0.1))
        self._last = tt
        self._choose_focus(tt)
        self._update_eyes(tt)
        self._update_head(tt, dt)
        if not self.present:
            return None
        cl, cr = self._closure(tt)
        ex = self._expressions(tt)
        pts, blend, M = self._render(cl, cr, ex)
        abs_t = tt + (self.t0 or 0.0)
        return FaceObservation(abs_t, self.w, self.h, pts, blend, M)

    def _render(self, cl: float, cr: float, ex: dict[str, float]):
        P = self.base.copy()
        smile = ex.get("smile", 0.0)
        jaw = ex.get("mouth_open", 0.0)
        brow = ex.get("brow_raise", 0.0)
        pucker = ex.get("pucker", 0.0)

        # eyelids
        for upper, lower, c in ((_UPPER_A, _LOWER_A, cl), (_UPPER_B, _LOWER_B, cr)):
            if c > 0:
                low_y = P[lower, 1].mean()
                P[upper, 1] += (low_y + 0.012 - P[upper, 1]) * c
        # brows
        for idx in (_BROW_A, _BROW_B):
            P[idx, 1] += 0.09 * brow
        # jaw: everything below the upper lip drops, fading toward the cheeks
        if jaw > 0:
            y13, y152 = P[13, 1], P[152, 1]
            below = P[:, 1] < y13 - 0.02
            w = np.clip((y13 - P[:, 1]) / (y13 - y152), 0, 1) ** 0.5
            w *= np.clip(1 - (np.abs(P[:, 0]) / 0.95) ** 2, 0, 1)
            P[below, 1] -= 0.30 * jaw * w[below]
        # smile: mouth corners up and out
        if smile > 0:
            for corner, sx in ((61, -1), (291, 1)):
                d2 = np.sum((P[:, :2] - self.base[corner, :2]) ** 2, axis=1)
                w = np.exp(-d2 / 0.03)
                P[:, 0] += sx * 0.07 * smile * w
                P[:, 1] += 0.08 * smile * w
        if pucker > 0:
            mouth = (self.base[61, :2] + self.base[291, :2]) / 2
            d2 = np.sum((P[:, :2] - mouth) ** 2, axis=1)
            w = np.exp(-d2 / 0.05)
            P[:, 0] -= (P[:, 0] - mouth[0]) * 0.35 * pucker * w
            P[:, 2] += 0.12 * pucker * w

        # irises: eyeball rotation = direction to the target minus head rotation
        ax, ay = self._angles(self.gaze)
        e_yaw = ax - math.radians(self.head[0])
        e_pitch = ay - math.radians(self.head[1])
        for key, idx in (("A", _IRIS_A), ("B", _IRIS_B)):
            c = self._eye_c[key]
            center = c + np.array([
                self.eye_radius * math.sin(e_yaw),
                -self.eye_radius * math.sin(e_pitch),
                0.05 + self.eye_radius * (math.cos(e_yaw) * math.cos(e_pitch) - 1),
            ])
            r = self.iris_radius
            P[idx[0]] = center
            P[idx[1]] = center + [r, 0, 0]
            P[idx[2]] = center + [0, r, 0]
            P[idx[3]] = center + [-r, 0, 0]
            P[idx[4]] = center + [0, -r, 0]

        R = rotation(*self.head)
        Q = P @ R.T
        s = self.px_per_unit * (VIEW_DIST_M / (VIEW_DIST_M + 0.0))
        cx = self.w / 2 + self.head_pos[0] * 2600
        cy = self.h * 0.46 - self.head_pos[1] * 2600
        pts = np.empty_like(Q)
        pts[:, 0] = cx + s * Q[:, 0]
        pts[:, 1] = cy - s * Q[:, 1]
        pts[:, 2] = -s * Q[:, 2]
        if self.noise_px > 0:
            pts[:, :2] += self.rng.normal(0, self.noise_px, (len(pts), 2))

        look_x = math.sin(e_yaw) / math.sin(math.radians(30))
        look_y = math.sin(e_pitch) / math.sin(math.radians(25))
        n = lambda: float(abs(self.rng.normal(0, 0.015)))  # noqa: E731
        blend = {
            "eyeBlinkLeft": min(1.0, cl * 0.9 + n()), "eyeBlinkRight": min(1.0, cr * 0.9 + n()),
            "mouthSmileLeft": min(1.0, smile * 0.85 + n()), "mouthSmileRight": min(1.0, smile * 0.85 + n()),
            "jawOpen": min(1.0, jaw * 0.8 + n()), "mouthPucker": min(1.0, pucker * 0.85 + n()),
            "browInnerUp": min(1.0, brow * 0.8 + n()),
            "browOuterUpLeft": min(1.0, brow * 0.7 + n()), "browOuterUpRight": min(1.0, brow * 0.7 + n()),
            "browDownLeft": n(), "browDownRight": n(), "mouthLeft": n(), "mouthRight": n(),
            "cheekPuff": n(), "mouthRollLower": n(), "mouthRollUpper": n(),
            "eyeLookOutLeft": max(0.0, -look_x), "eyeLookInLeft": max(0.0, look_x),
            "eyeLookInRight": max(0.0, -look_x), "eyeLookOutRight": max(0.0, look_x),
            "eyeLookUpLeft": max(0.0, -look_y), "eyeLookUpRight": max(0.0, -look_y),
            "eyeLookDownLeft": max(0.0, look_y), "eyeLookDownRight": max(0.0, look_y),
        }
        M = np.eye(4)
        M[:3, :3] = R
        M[:3, 3] = [self.head_pos[0] * 100, self.head_pos[1] * 100, -VIEW_DIST_M * 100]
        return pts, blend, M

    # ------------------------------------------------------------- preview

    def render_preview(self, obs: FaceObservation | None) -> np.ndarray:
        """A stylised 'infrared' camera frame for the optical-feed panel."""
        import cv2

        img = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        yy = np.linspace(0, 1, self.h)[:, None]
        img[:, :, 0] = (28 + 22 * yy).astype(np.uint8)
        img[:, :, 1] = (18 + 10 * yy).astype(np.uint8)
        img[:, :, 2] = 12
        if obs is not None:
            p = obs.points[:, :2].astype(np.int32)
            hull = cv2.convexHull(p[:468])
            cv2.fillConvexPoly(img, hull, (70, 92, 128), cv2.LINE_AA)
            for a, b in self.edges:
                cv2.line(img, tuple(p[a]), tuple(p[b]), (120, 95, 40), 1, cv2.LINE_AA)
            for i in _IRIS_A[:1] + _IRIS_B[:1]:
                cv2.circle(img, tuple(p[i]), 6, (200, 230, 60), 1, cv2.LINE_AA)
        return img


def synthetic_calibration(seed: int = 11, rows: int = 5, cols: int = 5, fps: float = 30.0):
    """Calibration data from a simulated user looking at a grid (demo mode).

    Returns (X, Y) gaze vectors and normalized targets, collected the same way
    a real calibration does (settle time, blink rejection)."""
    from gazer.core.calibration import grid
    from gazer.core.features import extract_features

    user = SyntheticUser(seed=seed, expressive=False)
    t = 0.0
    X, Y = [], []
    for target in grid(rows, cols, 0.05) * 2:
        user.attend(target)
        for k in range(int(1.4 * fps)):
            t += 1.0 / fps
            obs = user.step(t)
            if obs is None or k < int(0.45 * fps):
                continue
            f = extract_features(obs)
            if min(f.left.aperture, f.right.aperture) > 0.12:
                X.append(f.gaze_vector)
                Y.append(target)
    return np.asarray(X), np.asarray(Y, dtype=np.float64)
