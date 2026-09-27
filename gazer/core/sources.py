"""Frame sources: where face observations come from.

`CameraSource` is the real thing (webcam → MediaPipe). `SimulatedSource`
drives a `SyntheticUser`, so the full app — calibration included — works with
no webcam at all (demo mode, tests, screenshots).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from gazer.config import CameraSettings
from gazer.core.camera import CameraStream
from gazer.core.tracker import FaceObservation


@dataclass
class SourceFrame:
    t: float
    image: np.ndarray | None  # BGR, already mirrored; None if not requested
    obs: FaceObservation | None


class FrameSource:
    name = "source"
    error = ""
    fps = 0.0
    description = ""

    def open(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def next(self, want_image: bool, timeout: float = 0.5) -> SourceFrame | None:
        raise NotImplementedError


class CameraSource(FrameSource):
    name = "camera"

    def __init__(self, settings: CameraSettings):
        self.settings = settings
        self.stream: CameraStream | None = None
        self.tracker = None
        self._seq = 0

    def open(self) -> bool:
        if self.tracker is None:
            from gazer.core.tracker import FaceTracker

            try:
                self.tracker = FaceTracker()
            except Exception as exc:
                self.error = f"Face tracker failed: {exc}"
                return False
        self.stream = CameraStream(self.settings)
        ok = self.stream.start()
        self.error = "" if ok else self.stream.error
        if ok:
            w, h = self.stream.size
            self.description = f"Camera #{self.settings.index} · {w}×{h} via {self.stream.backend_used}"
        self._seq = 0
        return ok

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        if self.tracker is not None:
            self.tracker.close()
            self.tracker = None

    def next(self, want_image: bool, timeout: float = 0.5) -> SourceFrame | None:
        assert self.stream is not None
        frame = self.stream.read(self._seq, timeout=timeout)
        self.fps = self.stream.fps
        if frame is None:
            return None
        self._seq = frame.seq
        img = cv2.flip(frame.image, 1) if self.settings.mirror else frame.image
        rgb = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        obs = self.tracker.process(rgb, frame.t)
        return SourceFrame(frame.t, img, obs)


class SimulatedSource(FrameSource):
    name = "simulator"

    def __init__(self, fps: float = 30.0, seed: int | None = None, realtime: bool = True):
        from gazer.core.simulator import SyntheticUser

        self.user = SyntheticUser(seed=seed)
        self.period = 1.0 / fps
        self.realtime = realtime
        self._next_t: float | None = None
        self._window: list[float] = []
        self.description = "Simulated user · synthetic 478-point face"

    def next(self, want_image: bool, timeout: float = 0.5) -> SourceFrame | None:
        now = time.perf_counter()
        if self._next_t is None:
            self._next_t = now
        if self.realtime and now < self._next_t:
            time.sleep(self._next_t - now)
        t = time.perf_counter() if self.realtime else self._next_t
        self._next_t = max(self._next_t + self.period, t - self.period)
        obs = self.user.step(t)
        self._window.append(t)
        while self._window and t - self._window[0] > 1.0:
            self._window.pop(0)
        self.fps = float(len(self._window))
        img = self.user.render_preview(obs) if want_image else None
        return SourceFrame(t, img, obs)
