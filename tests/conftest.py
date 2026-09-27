import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("GAZER_DATA_DIR", tempfile.mkdtemp(prefix="gazer-test-"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gazer.core.features import GAZE_DIM, EyeState, FaceFeatures  # noqa: E402


def make_feats(t=0.0, head_point=(0.0, 0.0), gaze_vector=None, blend=None, roll=0.0,
               ear=(0.3, 0.3), quality=1.0) -> FaceFeatures:
    eye = lambda e, x: EyeState(np.array([x, 100.0]), 30.0, 0.0, 0.0, 0.3, e)  # noqa: E731
    return FaceFeatures(
        t=t, left=eye(ear[0], 90.0), right=eye(ear[1], 150.0), roll=roll, yaw=0.0, pitch=0.5,
        head_point=np.asarray(head_point, dtype=float), face_center=np.array([0.5, 0.5]), scale=0.1,
        matrix_angles=(0.0, 0.0, 0.0), matrix_tz=-40.0, blend=dict(blend or {}),
        gaze_vector=np.zeros(GAZE_DIM) if gaze_vector is None else np.asarray(gaze_vector, dtype=float),
        quality=quality,
    )


@pytest.fixture
def feats_factory():
    return make_feats
