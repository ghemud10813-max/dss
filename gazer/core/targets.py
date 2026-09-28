"""Magnetic targets: find the button / link / field you are looking at.

Webcam gaze is accurate to a few tens of pixels, which is often just short of
a small button. The OS accessibility tree knows exactly where the clickable
things are, so when your gaze rests near one, the cursor locks onto its
centre. That makes small targets hittable by eye, lets dwell-click ignore
plain text (no accidental clicks while reading), and turns every locked click
into a precisely labelled training sample for the gaze model.

Providers
  * `UIAProvider`  — Windows UI Automation via comtypes (optional dependency).
  * `StaticProvider` — fixed rectangles; used by tests and the simulator.

Accessibility queries can take tens of milliseconds, so `TargetSnapper` runs
them on its own thread; the engine only ever reads the latest result.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("gazer.targets")

MAX_W, MAX_H = 480, 240  # bigger than this is a pane/document, not a target
MIN_SIDE = 6


@dataclass(frozen=True)
class Target:
    x: float
    y: float
    w: float
    h: float
    kind: str = "button"
    name: str = ""

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def distance(self, px: float, py: float) -> float:
        """0 inside the rectangle, else distance to its edge (px)."""
        dx = max(self.x - px, 0.0, px - (self.x + self.w))
        dy = max(self.y - py, 0.0, py - (self.y + self.h))
        return math.hypot(dx, dy)

    def contains(self, px: float, py: float, pad: float = 0.0) -> bool:
        return self.distance(px, py) <= pad

    @property
    def clickable_size(self) -> bool:
        return MIN_SIDE <= self.w <= MAX_W and MIN_SIDE <= self.h <= MAX_H


class TargetProvider:
    name = "none"

    def open(self) -> bool:
        """Called on the snapper thread before the first query."""
        return True

    def close(self) -> None:
        pass

    def query(self, x: float, y: float) -> Target | None:
        """The interactive element at physical screen point (x, y), if any."""
        raise NotImplementedError


class StaticProvider(TargetProvider):
    name = "static"

    def __init__(self, targets: list[Target]):
        self.targets = list(targets)

    def query(self, x, y):
        for t in self.targets:
            if t.contains(x, y):
                return t
        return None


# UI Automation control type ids that are worth clicking.
_UIA_TYPES = {
    50000: "button", 50002: "checkbox", 50003: "combobox", 50004: "field", 50005: "link",
    50007: "list item", 50011: "menu item", 50013: "radio", 50015: "slider",
    50019: "tab", 50024: "tree item", 50029: "cell", 50031: "split button", 50035: "header item",
}


class UIAProvider(TargetProvider):
    """Windows UI Automation (needs `pip install comtypes`)."""

    name = "uia"

    def __init__(self):
        self._uia = None
        self._mod = None

    def open(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()  # COM objects are per-thread
            self._mod = comtypes.client.GetModule("UIAutomationCore.dll")
            self._uia = comtypes.client.CreateObject(self._mod.CUIAutomation, interface=self._mod.IUIAutomation)
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("UI Automation unavailable: %s", exc)
            return False

    def close(self) -> None:
        self._uia = None
        try:
            import comtypes

            comtypes.CoUninitialize()
        except Exception:
            pass

    def query(self, x, y):
        if self._uia is None:
            return None
        el = self._uia.ElementFromPoint(self._mod.tagPOINT(int(x), int(y)))
        # Walk up a few levels: the hit element is often the text/icon inside a button.
        walker = self._uia.ControlViewWalker
        for _ in range(4):
            if el is None:
                return None
            kind = _UIA_TYPES.get(int(el.CurrentControlType))
            if kind is not None and not el.CurrentIsOffscreen:
                r = el.CurrentBoundingRectangle
                t = Target(float(r.left), float(r.top), float(r.right - r.left), float(r.bottom - r.top), kind,
                           str(el.CurrentName or "")[:60])
                return t if t.clickable_size else None
            el = walker.GetParentElement(el)
        return None


def default_provider() -> TargetProvider:
    return UIAProvider() if sys.platform == "win32" else TargetProvider()


class TargetSnapper:
    """Background lookups around the gaze point; the engine polls `lock_for`."""

    RING = 8

    def __init__(self, provider: TargetProvider, radius_px: float = 45.0, synchronous: bool = False):
        self.provider = provider
        self.radius = radius_px
        self.synchronous = synchronous  # tests: query inline, no thread
        self.available = synchronous and provider.open()
        self.status = "starting" if not synchronous else ("ready" if self.available else "unavailable")
        self._cond = threading.Condition()
        self._want: tuple[float, float] | None = None
        self._last_query: tuple[float, float, float] | None = None
        self._result: tuple[float, float, float, Target | None] | None = None  # qx, qy, t, target
        self._stop = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.synchronous or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="gazer-targets", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def request(self, x: float, y: float) -> None:
        """Ask for the target near (x, y). Cheap; coalesces to the latest point."""
        if self.synchronous:
            if self.available and self._needs_query(x, y, time.perf_counter()):
                self._do_query(x, y)
            return
        with self._cond:
            self._want = (x, y)
            self._cond.notify()

    def lock_for(self, x: float, y: float, max_age: float = 0.8) -> Target | None:
        r = self._result
        if r is None:
            return None
        qx, qy, t, target = r
        if target is None or time.perf_counter() - t > max_age:
            return None
        if math.hypot(x - qx, y - qy) > self.radius * 2:
            return None
        return target if target.distance(x, y) <= self.radius else None

    # ------------------------------------------------------------ internals

    def _needs_query(self, x: float, y: float, now: float) -> bool:
        lq = self._last_query
        return lq is None or math.hypot(x - lq[0], y - lq[1]) > 10 or now - lq[2] > 0.3

    def _do_query(self, x: float, y: float) -> None:
        now = time.perf_counter()
        self._last_query = (x, y, now)
        best: Target | None = None
        best_d = float("inf")
        points = [(x, y)] + [(x + math.cos(a) * self.radius * 0.8, y + math.sin(a) * self.radius * 0.8)
                             for a in (i * 2 * math.pi / self.RING for i in range(self.RING))]
        for i, (px, py) in enumerate(points):
            try:
                t = self.provider.query(px, py)
            except Exception:  # noqa: BLE001 — a flaky app must not kill the snapper
                t = None
            if t is not None:
                d = t.distance(x, y)
                if d < best_d:
                    best, best_d = t, d
                if i == 0 and d == 0:
                    break  # looking right at it
        self._result = (x, y, time.perf_counter(), best if best_d <= self.radius else None)

    def _run(self) -> None:
        ok = False
        try:
            ok = self.provider.open()
        except Exception:  # noqa: BLE001
            ok = False
        self.available = ok
        self.status = "ready" if ok else "unavailable"
        if not ok:
            return
        try:
            while True:
                with self._cond:
                    while not self._stop and self._want is None:
                        self._cond.wait(0.5)
                    if self._stop:
                        return
                    x, y = self._want
                    self._want = None
                if self._needs_query(x, y, time.perf_counter()):
                    self._do_query(x, y)
                    time.sleep(0.03)  # ≤ ~30 lookups/s
        finally:
            self.provider.close()
