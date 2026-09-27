"""MediaPipe Face Landmarker wrapper: 478 landmarks, 52 blendshapes, head matrix."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from gazer.core.assets import ensure_face_landmarker


@dataclass
class FaceObservation:
    t: float
    width: int
    height: int
    points: np.ndarray  # (478, 3) in pixels; z scaled like x
    blendshapes: dict[str, float]
    matrix: np.ndarray | None  # 4x4 canonical-face → camera transform


class FaceTracker:
    def __init__(self, min_confidence: float = 0.5):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_tasks
        from mediapipe.tasks.python import vision

        options = vision.FaceLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=str(ensure_face_landmarker())),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=min_confidence,
            min_face_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._Image = mp.Image
        self._fmt = mp.ImageFormat.SRGB
        self._last_ts = -1

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:
            pass

    def process(self, rgb: np.ndarray, t: float) -> FaceObservation | None:
        """`rgb` must be contiguous HxWx3 uint8; `t` is perf_counter seconds."""
        h, w = rgb.shape[:2]
        ts = int(t * 1000)
        if ts <= self._last_ts:  # VIDEO mode needs strictly increasing timestamps
            ts = self._last_ts + 1
        self._last_ts = ts
        result = self._landmarker.detect_for_video(self._Image(image_format=self._fmt, data=rgb), ts)
        if not result.face_landmarks:
            return None
        lms = result.face_landmarks[0]
        if len(lms) < 478:
            return None
        pts = np.array([(p.x, p.y, p.z) for p in lms], dtype=np.float64)
        pts[:, 0] *= w
        pts[:, 1] *= h
        pts[:, 2] *= w
        blend = {}
        if result.face_blendshapes:
            blend = {c.category_name: float(c.score) for c in result.face_blendshapes[0]}
        matrix = None
        if result.facial_transformation_matrixes:
            matrix = np.asarray(result.facial_transformation_matrixes[0], dtype=np.float64)
        return FaceObservation(t, w, h, pts, blend, matrix)
