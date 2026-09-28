"""The engine: camera → tracking → pointer/gestures → OS input.

`EngineCore` is pure logic (feed it features, it drives an InputBackend) and
is fully testable without a camera. `CursorOutput` glides the real cursor at
125 Hz toward the pointer target, so motion is smooth even though the camera
runs at 30 fps; it also yields to the physical mouse. `EngineRunner` owns the
camera and tracker threads and publishes snapshots via callbacks (the Qt
layer turns those into signals).
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from gazer.config import AppConfig, CameraSettings
from gazer.core.features import FaceFeatures, extract_features
from gazer.core.gestures import BindingResolver, EyeClosure, GestureDetector, raw_gesture_values
from gazer.core.input_backend import InputBackend
from gazer.core.lighting import analyze as analyze_lighting
from gazer.core.interaction import ActionWheel, DwellDetector, Rect, WheelView, ZoomLens, ZoomView
from gazer.core.pointer import GAZE_MODES, MODE_ORDER, PointerEngine
from gazer.core.profiles import Profile
from gazer.core.screen import ScreenRect
from gazer.core.sources import CameraSource, FrameSource
from gazer.core.targets import Target, TargetSnapper
from gazer.core.zones import ZoneDetector

log = logging.getLogger("gazer.engine")

CLICK_ACTIONS = {"left_click": ("left", 1), "right_click": ("right", 1), "double_click": ("left", 2),
                 "middle_click": ("middle", 1)}
ALWAYS_ALLOWED = {"pause_toggle", "resume", "stop"}
# Dwell click types that stay armed until their two-step action completes.
TWO_STEP = {"drag_toggle", "zoom_click"}
LEARN_MAX_TARGET_PX = 160  # only small targets are precise enough to learn from


@dataclass
class Snapshot:
    t: float = 0.0
    face: bool = False
    control: bool = False
    paused: bool = False
    calibrating: bool = False
    mode: str = "hybrid"
    mode_effective: str = "hybrid"
    pointer: tuple[float, float] | None = None
    gaze: tuple[float, float] | None = None
    gaze_ready: bool = False
    dwell_enabled: bool = False
    dwell_progress: float = 0.0
    gesture_values: dict[str, float] = field(default_factory=dict)
    gesture_active: list[str] = field(default_factory=list)
    closure: tuple[float, float] = (0.0, 0.0)
    head: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scrolling: bool = False
    scroll_rate: tuple[float, float] = (0.0, 0.0)
    dragging: bool = False
    precision: bool = False
    wheel: WheelView | None = None
    zoom: ZoomView | None = None
    features: FaceFeatures | None = None
    quality: float = 0.0
    zone: tuple[str, float] | None = None  # (zone name, dwell progress)
    target: Target | None = None  # magnetic lock (physical px)
    dwell_next: str | None = None  # one-shot click type chosen in the Eye Dock
    zones_enabled: bool = False
    blinked: bool = False
    events: list[tuple] = field(default_factory=list)
    # filled by the runner
    fps: float = 0.0
    cam_fps: float = 0.0
    latency_ms: float = 0.0
    points: np.ndarray | None = None
    preview: np.ndarray | None = None
    mesh: np.ndarray | None = None  # (478, 3) face-centred, y up, ~unit face width
    source: str = ""
    lighting: dict | None = None  # periodic lighting report from the runner


class CursorOutput:
    def __init__(self, backend: InputBackend, smoothing_ms: float = 35, hz: float = 125,
                 manual_override: bool = True, on_manual: Callable[[tuple[int, int]], None] | None = None):
        self.backend = backend
        self.smoothing_ms = smoothing_ms
        self.manual_override = manual_override
        self.on_manual = on_manual
        self._period = 1.0 / hz
        self._lock = threading.RLock()
        self._target: np.ndarray | None = None
        self._cur: np.ndarray | None = None
        self._last_set: tuple[int, int] | None = None
        self.manual_until = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gazer-cursor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None

    def set_target(self, p) -> None:
        with self._lock:
            self._target = None if p is None else np.asarray(p, dtype=np.float64)

    def move_now(self, p) -> None:
        with self._lock:
            self._target = np.asarray(p, dtype=np.float64)
            self._cur = self._target.copy()
            self.backend.move(*self._cur)
            self._last_set = self.backend.position()

    @property
    def manual_active(self) -> bool:
        return time.perf_counter() < self.manual_until

    def step(self, now: float, dt: float) -> None:
        with self._lock:
            actual = self.backend.position()
            if self.manual_override and self._last_set is not None:
                if abs(actual[0] - self._last_set[0]) > 4 or abs(actual[1] - self._last_set[1]) > 4:
                    self.manual_until = now + 1.2
                    self._last_set = actual
                    self._cur = np.array(actual, dtype=np.float64)
                    if self.on_manual:
                        self.on_manual(actual)
                    return
            if now < self.manual_until:
                self._last_set = actual
                return
            if self._target is None:
                self._last_set = None
                self._cur = None
                return
            if self._cur is None:
                self._cur = np.array(actual, dtype=np.float64)
            tau = max(self.smoothing_ms, 1.0) / 1000.0
            a = 1.0 - math.exp(-dt / tau)
            self._cur = self._cur + (self._target - self._cur) * a
            if float(np.hypot(*(self._target - self._cur))) < 0.5:
                self._cur = self._target.copy()
            self.backend.move(*self._cur)
            self._last_set = self.backend.position()

    def _run(self) -> None:
        last = time.perf_counter()
        while not self._stop.is_set():
            now = time.perf_counter()
            try:
                self.step(now, now - last)
            except Exception:
                log.exception("cursor output")
            last = now
            time.sleep(max(0.0, self._period - (time.perf_counter() - now)))


class EngineCore:
    def __init__(self, profile: Profile, screen: ScreenRect, backend: InputBackend,
                 output: CursorOutput | None = None):
        self.profile = profile
        self.screen = screen
        self.backend = backend
        self.output = output or CursorOutput(backend, manual_override=False)
        self.output.on_manual = self._on_manual
        self.control = False
        self.paused = False
        self.calibrating = False
        self.dragging = False
        self.wheel: ActionWheel | None = None
        self.zoom: ZoomLens | None = None
        self.requests: list[tuple[str, object]] = []
        self._events: list[tuple] = []
        self._last_feats: FaceFeatures | None = None
        self._last_closure: tuple[float, float] = (1.0, 1.0)
        self._face_lost_since: float | None = None
        self._scroll_started = 0.0
        self._t = 0.0
        self._active_since: float | None = None
        self.snapper: TargetSnapper | None = None
        self.target: Target | None = None
        self._target_seen = 0.0
        self.dwell_next: str | None = None
        self.dwell_exclusions: list[tuple[float, float, float, float]] = []  # physical x, y, w, h
        self.bindings: BindingResolver | None = None
        self.bind_profile(profile)

    # ------------------------------------------------------------ profile

    def bind_profile(self, profile: Profile) -> None:
        self.profile = profile
        s = profile.settings
        self.pointer = PointerEngine(s.pointer, s.scroll, s.head_cal, self.screen)
        self.closure = EyeClosure()
        self.gestures = GestureDetector(s.gestures)
        self.bindings = BindingResolver(s.gestures.bindings)
        self.dwell = DwellDetector()
        self.target = None
        self.dwell_next = None
        self.zones = ZoneDetector(s.zones, self.screen.width / max(self.screen.height, 1))
        self.reload_settings()

    def reload_settings(self) -> None:
        s = self.profile.settings
        self.pointer.s = s.pointer
        self.pointer.scroll_s = s.scroll
        self.pointer.set_calibration(s.head_cal)
        self.pointer.apply_settings()
        self.gestures.s = s.gestures
        if self.bindings is None or self.bindings.bindings is not s.gestures.bindings:
            # only rebuild when the list changed, so a gesture held right now isn't dropped
            self.bindings = BindingResolver(s.gestures.bindings)
        if self.snapper is not None:
            self.snapper.radius = s.pointer.magnetic_radius_px
        self.dwell.radius = s.dwell.radius_px
        self.dwell.dwell_s = s.dwell.time_ms / 1000
        self.dwell.cooldown_s = s.dwell.cooldown_ms / 1000
        self.output.smoothing_ms = s.pointer.output_smoothing_ms
        self.output.manual_override = s.pointer.manual_override
        self.zones.s = s.zones

    @property
    def effective_mode(self) -> str:
        """Gaze modes need a calibrated model; until then fall back to head mouse
        so the cursor never just freezes."""
        mode = self.profile.settings.pointer.mode
        if mode in GAZE_MODES and not self.profile.gaze.ready:
            return "head_mouse"
        return mode

    # ------------------------------------------------------------ control

    @property
    def active(self) -> bool:
        return self.control and not self.paused and not self.calibrating

    def set_control(self, on: bool) -> None:
        if on == self.control:
            return
        self.control = on
        self.paused = False
        if on:
            self.pointer.sync(self.backend.position())
            self.profile.stats.sessions += 1
            self._active_since = self._t
        else:
            self._stop_everything()
            self.output.set_target(None)
            self._flush_active_time()
        self._toast("Control on" if on else "Control off")

    def set_paused(self, paused: bool) -> None:
        if paused == self.paused:
            return
        self.paused = paused
        if paused:
            self._stop_everything()
            self.output.set_target(None)
            self._flush_active_time()
        else:
            self.pointer.sync(self.backend.position())
            self._active_since = self._t
        self._toast("Paused — long blink to resume" if paused else "Resumed")

    def set_calibrating(self, on: bool) -> None:
        self.calibrating = on
        if on:
            self._stop_everything()
            self.output.set_target(None)
        else:
            self.pointer.sync(self.backend.position())

    def _flush_active_time(self) -> None:
        if self._active_since is not None:
            self.profile.stats.seconds_active += max(0.0, self._t - self._active_since)
            self._active_since = None

    def _stop_everything(self) -> None:
        if self.dragging:
            self.backend.button("left", False)
            self.dragging = False
        if self.pointer.scrolling:
            self.pointer.stop_scroll()
        self.wheel = None
        self.zoom = None
        self.dwell.reset()
        self.zones.reset()
        self.target = None
        self.set_dwell_next(None)

    def set_dwell_next(self, action: str | None) -> None:
        """Choose what the next dwell does (Eye Dock). None = the profile default."""
        if action == self.dwell_next:
            return
        self.dwell_next = action
        self._events.append(("dwell_next", action))

    def magnetic_on(self, mode: str | None = None) -> bool:
        s = self.profile.settings.pointer
        return (s.magnetic and self.snapper is not None and self.snapper.available
                and (mode or self.effective_mode) == "gaze")

    def _excluded(self, p) -> bool:
        return any(x <= p[0] < x + w and y <= p[1] < y + h for x, y, w, h in self.dwell_exclusions)

    def _on_manual(self, pos) -> None:
        self.pointer.sync(pos)
        self.dwell.reset()

    def _toast(self, text: str) -> None:
        self._events.append(("toast", text))

    # -------------------------------------------------------------- frame

    def process(self, t: float, feats: FaceFeatures | None) -> Snapshot:
        self._t = t
        s = self.profile.settings
        if feats is not None:
            self._last_feats = feats
            self._face_lost_since = None
            closure = self.closure.update(feats)
            self._last_closure = closure
            raw = raw_gesture_values(feats, closure, self.closure.swapped, s.gestures.swap_sides)
            events = self.gestures.update(t, raw)
        else:
            closure = (0.0, 0.0)
            if self._face_lost_since is None:
                self._face_lost_since = t
            events = self.gestures.release_all(t)
            if self.dragging and t - self._face_lost_since > 1.0:
                self.perform("drag_toggle")  # safety: never leave the button stuck down

        gaze_n = None
        if feats is not None and self.profile.gaze.ready and max(closure) < 0.5:
            gaze_n = self.profile.gaze.predict(feats.gaze_vector)

        mode = self.effective_mode
        out = self.pointer.update(t, feats, gaze_n, mode)

        for req in self.bindings.process(t, events):
            if self.active or (self.control and req.action in ALWAYS_ALLOWED):
                self.profile.stats.gestures += 1
                self.perform(req.action, req.phase)

        self._update_zones(t, out, mode)

        if self.active and not self.output.manual_active:
            self._drive(t, feats, out)
        elif not self.active:
            self.dwell.reset()

        return Snapshot(
            t=t, face=feats is not None, control=self.control, paused=self.paused,
            calibrating=self.calibrating, mode=s.pointer.mode, mode_effective=mode,
            pointer=None if self.pointer.P is None else (float(self.pointer.P[0]), float(self.pointer.P[1])),
            gaze=None if out.gaze is None else (float(out.gaze[0]), float(out.gaze[1])),
            gaze_ready=self.profile.gaze.ready, dwell_enabled=s.dwell.enabled,
            dwell_progress=self.dwell.progress if self.active else 0.0,
            gesture_values=dict(self.gestures.values), gesture_active=list(self.gestures.active),
            closure=closure,
            head=(feats.yaw, feats.pitch, feats.roll) if feats else (0.0, 0.0, 0.0),
            scrolling=self.pointer.scrolling,
            scroll_rate=(float(self.pointer.scroll_rate[0]), float(self.pointer.scroll_rate[1])),
            dragging=self.dragging, precision=self.pointer.precision,
            wheel=self.wheel.view() if self.wheel else None,
            zoom=self.zoom.view() if self.zoom else None,
            features=feats, quality=feats.quality if feats else 0.0,
            zone=(self.zones.current, self.zones.progress) if self.zones.current else None,
            zones_enabled=s.zones.enabled, blinked=self.gestures.blinked,
            target=self.target if self.active else None, dwell_next=self.dwell_next,
            events=self._drain_events(),
        )

    def _update_zones(self, t: float, out, mode: str) -> None:
        if not self.control or self.calibrating or self.wheel is not None or self.zoom is not None \
                or self.pointer.scrolling or self.output.manual_active:
            self.zones.reset()
            return
        p = None
        if out.gaze is not None and mode in GAZE_MODES:
            p = self.screen.px_to_norm(*out.gaze)
        elif self.pointer.P is not None and out.pos is not None:
            p = self.screen.px_to_norm(*self.pointer.P)
        for action in self.zones.update(t, p):
            if self.active or action in ALWAYS_ALLOWED:
                self._events.append(("zone", self.zones.current, action))
                self.perform(action, "zone")

    def _drain_events(self) -> list[tuple]:
        ev, self._events = self._events, []
        return ev

    def _drive(self, t: float, feats: FaceFeatures | None, out) -> None:
        s = self.profile.settings
        if out.scroll != (0, 0):
            self.backend.scroll(out.scroll[0], out.scroll[1])
        if self.pointer.scrolling:
            if t - self._scroll_started > s.scroll.timeout_s:
                self.pointer.stop_scroll()
                self._toast("Scroll mode off")
            return
        if out.pos is None:
            return
        if self.wheel is not None:
            state, idx = self.wheel.update(t, out.pos)
            if state == "select" and idx is not None:
                self._wheel_select(idx)
            elif state == "cancel":
                self._close_wheel()
            return
        if self.zoom is not None:
            self.target = None
            lens_p = self.zoom.clamp_to_lens(out.pos)
            self.pointer.P = lens_p.copy()
            self.zoom.pointer = lens_p
            self.output.set_target(self.zoom.to_real(lens_p))
            if self.zoom.expired(t):
                self._close_zoom()
                return
        else:
            self._magnetize(t, out.pos)
            self.output.set_target(self.pointer.P)
        dwell_ok = s.dwell.enabled or self.dwell_next is not None
        if s.dwell.targets_only and self.zoom is None and self.target is None and self.magnetic_on():
            dwell_ok = False  # reading plain text never clicks
        if dwell_ok and feats is not None and self.zones.current is None and not self._excluded(self.pointer.P):
            if self.dwell.update(t, self.pointer.P):
                default = s.dwell.action if s.dwell.action != "none" else "left_click"
                action = self.dwell_next or default
                self.perform(action)
                two_step_pending = (action == "drag_toggle" and self.dragging) or \
                                   (action == "zoom_click" and self.zoom is not None)
                if self.dwell_next is not None and not two_step_pending:
                    self.set_dwell_next(None)
        else:
            self.dwell.reset()

    def _magnetize(self, t: float, gaze_pos) -> None:
        """Lock the cursor onto the interactive element nearest the gaze."""
        if not self.magnetic_on():
            self.target = None
            return
        x, y = float(gaze_pos[0]), float(gaze_pos[1])
        self.snapper.request(x, y)
        lock = self.snapper.lock_for(x, y)
        prev = self.target
        if lock is None and prev is not None and t - self._target_seen < 0.6 \
                and prev.distance(x, y) <= self.snapper.radius * 1.4:
            lock = prev  # hysteresis: don't flicker off between lookups
        elif lock is not None:
            self._target_seen = t
        if lock is not None and lock != prev:
            self.dwell.reset()
        self.target = lock
        if lock is not None:
            self.pointer.P = np.array(lock.center, dtype=np.float64)

    # ------------------------------------------------------------ actions

    def perform(self, action: str, phase: str = "fire") -> None:
        if action == "drag_hold" and phase == "up":
            if self.dragging:
                self._drag(False)
            return
        if not self.control and action not in ("calibrate", "keyboard_toggle", "voice_toggle", "dock_toggle",
                                               "trainer"):
            return
        if self.paused and action not in ALWAYS_ALLOWED:
            return
        if action == "drag_hold":
            if phase == "down" and not self.dragging:
                self._drag(True)
            elif phase in ("up", "fire") and self.dragging:
                self._drag(False)
            elif phase == "fire":
                self._drag(True)
            return
        if phase == "up":
            return
        self._events.append(("action", action))

        if action in CLICK_ACTIONS:
            if self.wheel is not None:
                if self.wheel.hover is not None:
                    self._wheel_select(self.wheel.hover)
                else:
                    self._close_wheel()
                return
            button, count = CLICK_ACTIONS[action]
            self._click(button, count)
        elif action == "drag_toggle":
            self._drag(not self.dragging)
        elif action == "scroll_mode":
            if self.pointer.scrolling:
                self.pointer.stop_scroll()
                self._toast("Scroll mode off")
            else:
                self.pointer.start_scroll()
                self._scroll_started = self._t
                self._toast("Scroll mode — move head up/down")
        elif action in ("scroll_up", "scroll_down"):
            step = 120 if phase == "zone" else 360  # zones repeat fast, so one notch each
            self.backend.scroll(step if action == "scroll_up" else -step)
        elif action == "action_wheel":
            if self.wheel is not None:
                self._close_wheel()
            else:
                self._open_wheel()
        elif action == "zoom":
            if self.zoom is not None:
                self._close_zoom()
            else:
                self._open_zoom()
        elif action == "zoom_click":
            # two-step precise click: first magnify, then click inside the lens
            if self.zoom is None:
                self._open_zoom()
            else:
                self._click("left", 1)
        elif action == "pause_toggle":
            self.set_paused(not self.paused)
        elif action == "pause":
            self.set_paused(True)
        elif action == "resume":
            self.set_paused(False)
        elif action == "stop":
            self._stop_everything()
            self._toast("Stopped")
        elif action == "recenter":
            self._recenter()
        elif action == "precision_toggle":
            self.pointer.set_precision(not self.pointer.precision)
            self._toast("Precision on" if self.pointer.precision else "Precision off")
        elif action == "mode_cycle":
            i = MODE_ORDER.index(self.profile.settings.pointer.mode) if \
                self.profile.settings.pointer.mode in MODE_ORDER else -1
            self.set_mode(MODE_ORDER[(i + 1) % len(MODE_ORDER)])
        elif action.startswith("mode:"):
            self.set_mode(action[5:])
        elif action == "zones_toggle":
            z = self.profile.settings.zones
            z.enabled = not z.enabled
            self._toast("Gaze zones on" if z.enabled else "Gaze zones off")
            self._events.append(("settings", "zones.enabled"))
        elif action == "dwell_toggle":
            d = self.profile.settings.dwell
            d.enabled = not d.enabled
            self._toast("Dwell click on" if d.enabled else "Dwell click off")
            self._events.append(("settings", "dwell.enabled"))
        elif action in ("keyboard_toggle", "voice_toggle", "calibrate", "dock_toggle", "trainer"):
            self.requests.append((action, None))
        elif action.startswith("key:"):
            self.backend.tap(action[4:])
        elif action.startswith("hotkey:"):
            self.backend.hotkey(action[7:])
        elif action.startswith("type:"):
            text = action[5:]
            self.backend.type_text(text)
            self.profile.stats.keys_typed += len(text)

    def set_mode(self, mode: str) -> None:
        if mode not in MODE_ORDER:
            return
        self.profile.settings.pointer.mode = mode
        self.pointer.sync(self.pointer.P)
        self.pointer.stab.reset()
        if mode in GAZE_MODES and not self.profile.gaze.ready:
            self._toast("Mode: " + mode + " — calibrate gaze for best results")
        else:
            self._toast("Mode: " + mode.replace("_", " "))
        self._events.append(("mode", mode))

    def _click(self, button: str, count: int) -> None:
        if self.zoom is not None:
            target = self.zoom.to_real(self.zoom.pointer)
            self._close_zoom(sync_to=target)
            learn = True
        elif self.target is not None and max(self.target.w, self.target.h) <= LEARN_MAX_TARGET_PX:
            # Magnetic lock: the element centre is a better label than the gaze estimate itself.
            target = np.array(self.target.center)
            learn = True
        else:
            target = np.array(self.pointer.P)
            learn = self.profile.settings.pointer.mode != "gaze"
        self.output.move_now(target)
        self.backend.click(button, count)
        self.profile.stats.clicks += 1
        self.dwell.reset()
        self._events.append(("click", (float(target[0]), float(target[1])), button))
        if (learn and self.profile.settings.pointer.adaptive_learning and self._last_feats is not None
                and self.profile.gaze.ready and max(self._last_closure) < 0.5):
            if self.profile.gaze.learn_from_click(self._last_feats.gaze_vector, self.screen.px_to_norm(*target)):
                self.profile.stats.implicit_samples += 1

    def _drag(self, down: bool) -> None:
        if down == self.dragging:
            return
        if down:
            self.output.move_now(self.pointer.P)
        self.backend.button("left", down)
        self.dragging = down
        self._toast("Dragging — repeat to drop" if down else "Dropped")

    def _open_wheel(self) -> None:
        w = self.profile.settings.wheel
        anchor = np.array(self.pointer.P)
        self.output.move_now(anchor)
        self.output.set_target(anchor)
        self.wheel = ActionWheel(anchor, list(w.items), w.radius_px, w.select_dwell_ms / 1000, w.timeout_s,
                                 t0=self._t)
        self.dwell.reset()

    def _close_wheel(self) -> None:
        if self.wheel is not None:
            self.pointer.sync(self.wheel.center)
        self.wheel = None
        self.dwell.reset()

    def _wheel_select(self, idx: int) -> None:
        assert self.wheel is not None
        action = self.wheel.items[idx]
        self._close_wheel()
        self.perform(action)

    def _open_zoom(self) -> None:
        z = self.profile.settings.zoom
        sc = self.screen
        self.zoom = ZoomLens(self.pointer.P, z.factor, z.region_w, z.region_h,
                             Rect(sc.x, sc.y, sc.width, sc.height), t0=self._t, timeout_s=z.timeout_s)
        self.pointer.sync(self.zoom.pointer)
        self.output.move_now(self.zoom.to_real(self.zoom.pointer))
        self.dwell.reset()

    def _close_zoom(self, sync_to=None) -> None:
        if self.zoom is not None:
            self.pointer.sync(sync_to if sync_to is not None else self.zoom.to_real(self.zoom.pointer))
        self.zoom = None
        self.dwell.reset()

    def _recenter(self) -> None:
        self.pointer.recenter()
        f = self._last_feats
        gz = self.profile.gaze
        if f is not None and gz.ready and self.profile.settings.pointer.mode != "gaze":
            pred = gz.predict(f.gaze_vector)
            if pred is not None:
                gz.nudge(pred, self.screen.px_to_norm(*self.pointer.P))
        self.pointer.stab.reset()
        self._toast("Recentered")


def normalized_mesh(points: np.ndarray) -> np.ndarray:
    """Landmarks → face-centred coordinates for the 3D hologram (y up, z toward
    the viewer, 2 units ≈ face width). Head pose stays baked in."""
    fw = float(np.hypot(*(points[454, :2] - points[234, :2]))) or 1.0
    c = points[:468].mean(axis=0)
    m = (points - c) / (fw / 2)
    m[:, 1] *= -1
    m[:, 2] *= -1
    return m.astype(np.float32)


class EngineRunner:
    """Frame source + tracker loop in a background thread."""

    def __init__(self, config: AppConfig, core: EngineCore,
                 on_snapshot: Callable[[Snapshot], None],
                 on_status: Callable[[str], None] | None = None,
                 source_factory: Callable[[AppConfig], FrameSource] | None = None):
        self.config = config
        self.core = core
        self.on_snapshot = on_snapshot
        self.on_status = on_status or (lambda s: None)
        self.source_factory = source_factory or (lambda cfg: CameraSource(cfg.camera))
        self.source: FrameSource | None = None
        self.want_preview = False
        self.want_mesh = False
        self.preview_width = 480
        self.status = "Starting…"
        self._cmds: queue.Queue[Callable[[EngineCore], None]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._restart = True
        self._last_light = 0.0
        self.camera_ok = False

    def post(self, fn: Callable[[EngineCore], None]) -> None:
        self._cmds.put(fn)

    def start(self) -> None:
        self._stop.clear()
        self.core.output.start()
        self._thread = threading.Thread(target=self._run, name="gazer-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self.core.output.stop()
        self.core.set_control(False)

    def restart_camera(self, settings: CameraSettings) -> None:
        self.config.camera = settings
        self.post(lambda core: setattr(self, "_restart", True))

    def _set_status(self, text: str) -> None:
        self.status = text
        self.on_status(text)

    def _open_source(self) -> bool:
        if self.source is not None:
            self.source.close()
        self._set_status("Opening camera…")
        self.source = self.source_factory(self.config)
        ok = self.source.open()
        self.camera_ok = ok
        self._set_status(self.source.description if ok else self.source.error)
        return ok

    def _drain_commands(self) -> None:
        while True:
            try:
                fn = self._cmds.get_nowait()
            except queue.Empty:
                return
            try:
                fn(self.core)
            except Exception:
                log.exception("engine command")

    def _run(self) -> None:
        times: list[float] = []
        while not self._stop.is_set():
            self._drain_commands()
            if self._restart:
                self._restart = False
                if not self._open_source():
                    # keep the UI alive (face lost, status visible) while retrying
                    for _ in range(8):
                        snap = self.core.process(time.perf_counter(), None)
                        snap.source = "offline"
                        self._emit(snap)
                        self._sleep_or_stop(0.25)
                        self._drain_commands()
                    self._restart = True
                    continue
            assert self.source is not None
            try:
                frame = self.source.next(self.want_preview, timeout=0.5)
            except Exception:
                log.exception("frame source")
                frame = None
                self._sleep_or_stop(0.05)
            if frame is None:
                snap = self.core.process(time.perf_counter(), None)
                snap.cam_fps = self.source.fps
                snap.source = self.source.name
                self._emit(snap)
                continue
            obs = frame.obs
            try:
                feats = extract_features(obs) if obs is not None else None
            except Exception:
                log.exception("features")
                feats = None
            try:
                snap = self.core.process(frame.t, feats)
            except Exception:
                log.exception("engine process")
                continue
            now = time.perf_counter()
            times.append(now)
            while times and now - times[0] > 1.0:
                times.pop(0)
            snap.fps = float(len(times))
            snap.cam_fps = self.source.fps
            snap.latency_ms = (now - frame.t) * 1000
            snap.source = self.source.name
            if obs is not None and self.want_mesh:
                snap.mesh = normalized_mesh(obs.points)
            if frame.image is not None and obs is not None and now - self._last_light > 1.0:
                self._last_light = now
                try:
                    small = cv2.resize(frame.image, (160, int(160 * frame.image.shape[0] / frame.image.shape[1])),
                                       interpolation=cv2.INTER_AREA)
                    k = 160 / frame.image.shape[1]
                    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                    snap.lighting = analyze_lighting(gray, obs.points[:, :2] * k).as_dict()
                except Exception:
                    log.exception("lighting")
            if self.want_preview and frame.image is not None:
                img = frame.image
                h, w = img.shape[:2]
                scale = self.preview_width / w
                snap.preview = cv2.resize(img, (self.preview_width, int(h * scale)), interpolation=cv2.INTER_AREA)
                if obs is not None:
                    snap.points = obs.points[:, :2] * scale
            self._emit(snap)
        if self.source is not None:
            self.source.close()

    def _emit(self, snap: Snapshot) -> None:
        req, self.core.requests = self.core.requests, []
        for kind, data in req:
            snap.events.append(("request", kind, data))
        try:
            self.on_snapshot(snap)
        except Exception:
            log.exception("snapshot callback")

    def _sleep_or_stop(self, seconds: float) -> None:
        self._stop.wait(seconds)
