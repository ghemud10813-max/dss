"""Threaded webcam capture that always hands out the freshest frame.

A background thread reads continuously so the processing loop never sees
stale buffered frames. On Windows, Media Foundation (MSMF) is tried first: on
typical laptop webcams it delivers ~30 fps at 720p where DirectShow manages
~17 fps.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from gazer.config import CameraSettings

_BACKENDS = {
    "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY),
    "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
    "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
    "avfoundation": getattr(cv2, "CAP_AVFOUNDATION", cv2.CAP_ANY),
    "any": cv2.CAP_ANY,
}


def backend_order(name: str) -> list[str]:
    if name != "auto" and name in _BACKENDS:
        return [name, "any"] if name != "any" else ["any"]
    if sys.platform == "win32":
        return ["msmf", "dshow", "any"]
    if sys.platform == "darwin":
        return ["avfoundation", "any"]
    return ["v4l2", "any"]


@dataclass
class Frame:
    seq: int
    t: float  # time.perf_counter() at capture
    image: np.ndarray  # BGR


class CameraStream:
    def __init__(self, settings: CameraSettings):
        self.settings = settings
        self._cap: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._latest: Frame | None = None
        self._seq = 0
        self.backend_used = ""
        self.size = (0, 0)
        self.fps = 0.0
        self.error = ""

    # ---------------------------------------------------------------- open

    def open(self) -> bool:
        s = self.settings
        for name in backend_order(s.backend):
            cap = cv2.VideoCapture(s.index, _BACKENDS[name])
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, s.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, s.height)
            cap.set(cv2.CAP_PROP_FPS, s.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            deadline = time.perf_counter() + 4.0
            ok, img = False, None
            while time.perf_counter() < deadline:
                ok, img = cap.read()
                if ok and img is not None:
                    break
            if ok and img is not None:
                self._cap = cap
                self.backend_used = name
                self.size = (img.shape[1], img.shape[0])
                self.error = ""
                return True
            cap.release()
        self.error = f"Could not open camera #{s.index}"
        return False

    def start(self) -> bool:
        if self._cap is None and not self.open():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gazer-camera", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---------------------------------------------------------------- loop

    def _run(self) -> None:
        window: list[float] = []
        failures = 0
        while not self._stop.is_set():
            cap = self._cap
            if cap is None:
                break
            ok, img = cap.read()
            now = time.perf_counter()
            if not ok or img is None:
                failures += 1
                if failures > 60:
                    self.error = "Camera stopped delivering frames"
                    time.sleep(0.2)
                else:
                    time.sleep(0.01)
                continue
            failures = 0
            window.append(now)
            while window and now - window[0] > 1.0:
                window.pop(0)
            self.fps = float(len(window))
            with self._cond:
                self._seq += 1
                self._latest = Frame(self._seq, now, img)
                self._cond.notify_all()

    def read(self, after_seq: int = 0, timeout: float = 0.5) -> Frame | None:
        """Block until a frame newer than `after_seq` exists (or timeout)."""
        deadline = time.perf_counter() + timeout
        with self._cond:
            while not self._stop.is_set():
                if self._latest is not None and self._latest.seq > after_seq:
                    return self._latest
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
        return None


def probe_cameras(max_index: int = 4) -> list[int]:
    """Indices of cameras that open. Slow-ish (≈0.3 s per index with MSMF)."""
    found = []
    backend = _BACKENDS[backend_order("auto")[0]]
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend)
        if cap.isOpened():
            found.append(i)
        cap.release()
    return found
