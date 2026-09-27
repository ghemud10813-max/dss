"""Downloadable model assets (face landmarker, optional voice model)."""

from __future__ import annotations

import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from gazer.paths import ASSETS_DIR, models_dir

FACE_LANDMARKER_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
FACE_LANDMARKER_PATH = ASSETS_DIR / "face_landmarker.task"

VOICE_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOICE_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOICE_MODEL_NAME}.zip"

ProgressFn = Callable[[int, int], None]


def _download(url: str, dest: Path, progress: ProgressFn | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=30) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    tmp.replace(dest)


def ensure_face_landmarker(progress: ProgressFn | None = None) -> Path:
    if FACE_LANDMARKER_PATH.exists() and FACE_LANDMARKER_PATH.stat().st_size > 1_000_000:
        return FACE_LANDMARKER_PATH
    _download(FACE_LANDMARKER_URL, FACE_LANDMARKER_PATH, progress)
    return FACE_LANDMARKER_PATH


def default_voice_model_dir() -> Path:
    return models_dir() / VOICE_MODEL_NAME


def voice_model_ready(path: Path | None = None) -> bool:
    p = path or default_voice_model_dir()
    return p.is_dir() and (p / "conf").is_dir() and (p / "am").is_dir()


def download_voice_model(progress: ProgressFn | None = None) -> Path:
    target = default_voice_model_dir()
    if voice_model_ready(target):
        return target
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "model.zip"
        _download(VOICE_MODEL_URL, zpath, progress)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tmp)
        extracted = Path(tmp) / VOICE_MODEL_NAME
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(extracted), str(target))
    return target
