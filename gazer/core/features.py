"""Turn raw landmarks into stable, pose-aware measurements.

Conventions: the image is mirrored (selfie view) before tracking, so the eye
with the smaller x is the user's *left* eye and head movements match screen
directions. All eye measurements are expressed in a roll-corrected frame
anchored on the eye corners and scaled by eye width, which removes most of the
dependence on head roll and camera distance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from gazer.core.tracker import FaceObservation

FEATURE_VERSION = 2

_EYE_A = dict(c1=33, c2=133, up=159, lo=145, ear=(33, 160, 158, 133, 153, 144))
_EYE_B = dict(c1=362, c2=263, up=386, lo=374, ear=(362, 385, 387, 263, 373, 380))
_IRIS_1 = (468, 469, 470, 471, 472)
_IRIS_2 = (473, 474, 475, 476, 477)
_NOSE = (1, 4)
_CHEEK_L, _CHEEK_R = 234, 454
_FOREHEAD, _CHIN = 10, 152

GAZE_CORE = ["uL", "vL", "uR", "vR", "yaw", "pitch"]
GAZE_LINEAR = [
    "apL", "apR", "roll", "hx", "hy", "scale", "m_yaw", "m_pitch", "m_tz",
    "lookUpL", "lookDownL", "lookInL", "lookOutL", "lookUpR", "lookDownR", "lookInR", "lookOutR",
]
GAZE_FEATURES = GAZE_CORE + GAZE_LINEAR
GAZE_DIM = len(GAZE_FEATURES)
_LOOK = ["eyeLookUpLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft",
         "eyeLookUpRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight"]


@dataclass
class EyeState:
    center: np.ndarray  # (2,) px
    width: float  # px
    u: float  # iris offset along eye axis (eye widths)
    v: float  # iris offset perpendicular (eye widths, + = down)
    aperture: float  # lid gap / eye width
    ear: float  # eye aspect ratio


@dataclass
class FaceFeatures:
    t: float
    left: EyeState
    right: EyeState
    roll: float  # degrees, + = head tilted to the user's right
    yaw: float  # nose offset from face centre, face-widths*2
    pitch: float  # nose position between forehead (0) and chin (1)
    head_point: np.ndarray  # nose in face-widths from frame centre (pointer signal)
    face_center: np.ndarray  # normalized frame coords
    scale: float  # inter-ocular distance / frame width
    matrix_angles: tuple[float, float, float]
    matrix_tz: float
    blend: dict[str, float]
    gaze_vector: np.ndarray = field(repr=False)
    quality: float = 1.0


def _eye_state(P: np.ndarray, spec: dict, iris: np.ndarray) -> EyeState:
    a = P[spec["c1"], :2]
    b = P[spec["c2"], :2]
    if a[0] > b[0]:
        a, b = b, a
    axis = b - a
    width = float(np.hypot(*axis)) or 1e-6
    xh = axis / width
    yh = np.array([-xh[1], xh[0]])  # +90° in image coords → points down
    center = (a + b) / 2
    d = iris - center
    u = float(d @ xh) / width
    v = float(d @ yh) / width
    aperture = float((P[spec["lo"], :2] - P[spec["up"], :2]) @ yh) / width
    e = [P[i, :2] for i in spec["ear"]]
    horiz = float(np.hypot(*(e[0] - e[3]))) or 1e-6
    ear = (float(np.hypot(*(e[1] - e[5]))) + float(np.hypot(*(e[2] - e[4])))) / (2 * horiz)
    return EyeState(center, width, u, v, aperture, ear)


def _matrix_angles(m: np.ndarray | None) -> tuple[tuple[float, float, float], float]:
    if m is None:
        return (0.0, 0.0, 0.0), 0.0
    r = m[:3, :3]
    yaw = math.degrees(math.atan2(r[0, 2], r[2, 2]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -r[1, 2]))))
    roll = math.degrees(math.atan2(r[1, 0], r[1, 1]))
    return (yaw, pitch, roll), float(m[2, 3])


def extract_features(obs: FaceObservation) -> FaceFeatures:
    P = obs.points
    iris1 = P[list(_IRIS_1), :2].mean(axis=0)
    iris2 = P[list(_IRIS_2), :2].mean(axis=0)
    ca = (P[_EYE_A["c1"], :2] + P[_EYE_A["c2"], :2]) / 2
    # Pair each iris with the nearer eye (robust to model index conventions).
    if np.hypot(*(iris1 - ca)) <= np.hypot(*(iris2 - ca)):
        ia, ib = iris1, iris2
    else:
        ia, ib = iris2, iris1
    ea = _eye_state(P, _EYE_A, ia)
    eb = _eye_state(P, _EYE_B, ib)
    left, right = (ea, eb) if ea.center[0] <= eb.center[0] else (eb, ea)

    dx, dy = right.center - left.center
    roll_rad = math.atan2(dy, dx)
    mid = (left.center + right.center) / 2
    c, s = math.cos(-roll_rad), math.sin(-roll_rad)

    def derot(idx: int) -> np.ndarray:
        d = P[idx, :2] - mid
        return np.array([c * d[0] - s * d[1], s * d[0] + c * d[1]])

    nose = np.mean([derot(i) for i in _NOSE], axis=0)
    cl, cr = derot(_CHEEK_L), derot(_CHEEK_R)
    face_w = abs(cr[0] - cl[0]) or 1e-6
    yaw = (nose[0] - (cl[0] + cr[0]) / 2) / face_w * 2
    fh, ch = derot(_FOREHEAD), derot(_CHIN)
    span = (ch[1] - fh[1]) or 1e-6
    pitch = (nose[1] - fh[1]) / span

    nose_px = P[list(_NOSE), :2].mean(axis=0)
    head_point = (nose_px - np.array([obs.width / 2, obs.height / 2])) / face_w
    iod = float(np.hypot(dx, dy))
    scale = iod / max(obs.width, 1)
    face_center = np.array([mid[0] / obs.width, mid[1] / obs.height])
    angles, tz = _matrix_angles(obs.matrix)
    blend = obs.blendshapes

    vec = np.array(
        [
            left.u, left.v, right.u, right.v, yaw, pitch,
            left.aperture, right.aperture, math.degrees(roll_rad),
            head_point[0], head_point[1], scale,
            angles[0], angles[1], tz,
            *[blend.get(k, 0.0) for k in _LOOK],
        ],
        dtype=np.float64,
    )

    quality = 1.0
    if scale < 0.06:
        quality *= max(0.0, scale / 0.06)
    quality *= max(0.0, 1.0 - max(0.0, abs(yaw) - 0.5) * 2)
    if min(left.aperture, right.aperture) < 0.08:
        quality *= 0.2

    return FaceFeatures(
        t=obs.t, left=left, right=right, roll=math.degrees(roll_rad), yaw=float(yaw),
        pitch=float(pitch), head_point=head_point, face_center=face_center, scale=scale,
        matrix_angles=angles, matrix_tz=tz, blend=blend, gaze_vector=vec, quality=quality,
    )


# Landmark subsets for the camera preview.
PREVIEW_CONTOURS = {
    "eyes": [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246,
             362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398],
    "iris": list(_IRIS_1) + list(_IRIS_2),
    "face": [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378,
             400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21,
             54, 103, 67, 109],
    "nose": [1, 4],
    "lips": [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185],
}
