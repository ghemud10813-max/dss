"""The application controller: owns the engine, profiles, calibration, voice,
hotkeys and insights, and exposes a small command API.

It is UI-agnostic. The web Command Deck talks to it through the hub
(`gazer.server`), the Qt layer plugs in via `UIHooks` (overlay, focus-free
keyboard, fullscreen calibration window, tray). Engine state is only mutated
on the engine thread (`runner.post`); controller state is guarded by a lock.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from dataclasses import asdict
from typing import Any, Callable, Protocol

import numpy as np

from gazer import __version__
from gazer.config import (
    ACTIONS, CONTROL_STYLES, GESTURE_CATALOG, POINTER_MODES, TRIGGERS, ZONE_NAMES, AppConfig, Binding,
    ProfileSettings, apply_control_style, config_to_patchable, from_dict, load_json, save_json, set_path,
    to_dict,
)
from gazer.core import assets, voice
from gazer.core.calib_session import BUTTONS, CalibrationSession
from gazer.core.calibration import PRESETS
from gazer.core.engine import CursorOutput, EngineCore, EngineRunner, Snapshot
from gazer.core.gestures import NO_NEUTRAL, raw_gesture_values
from gazer.core.hotkeys import Hotkeys
from gazer.core.input_backend import InputBackend, RecordingInput, create_backend
from gazer.core.insights import Insights
from gazer.core.pointer import MODE_ORDER
from gazer.core.profiles import Profile, ProfileStore
from gazer.core.screen import list_monitors, pick_monitor
from gazer.core.wordpredict import WordPredictor
from gazer.paths import config_path

log = logging.getLogger("gazer.controller")

HOST_ACTIONS = {"keyboard_toggle", "voice_toggle", "calibrate", "control_toggle"}


class UIHooks(Protocol):
    """Implemented by the Qt layer. All methods may be called from any thread."""

    def toggle_keyboard(self) -> None: ...
    def open_calibration(self) -> bool: ...
    def close_calibration(self) -> None: ...
    def screen_changed(self) -> None: ...
    def quit(self) -> None: ...


class Listener(Protocol):
    def on_frame(self, snap: Snapshot, extra: dict) -> None: ...
    def on_state(self) -> None: ...


class Controller:
    def __init__(self, config: AppConfig | None = None, *, demo: bool = False, virtual_input: bool | None = None,
                 store: ProfileStore | None = None, source_factory=None, backend: InputBackend | None = None,
                 persist_config: bool = True):
        self.config = config or load_json(AppConfig, config_path())
        self.persist_config = persist_config
        self.demo = demo
        self.virtual_input = demo if virtual_input is None else virtual_input
        self.store = store or ProfileStore()
        self.profile = self._initial_profile()
        self.monitors = list_monitors()
        self.monitor = pick_monitor(self.config.screen_index)
        screen = self.monitor.rect
        if backend is None:
            cx, cy = screen.center
            backend = RecordingInput((int(cx), int(cy))) if self.virtual_input else create_backend()
        self.backend = backend
        self.core = EngineCore(self.profile, screen, backend,
                               CursorOutput(backend, manual_override=not self.virtual_input))
        if source_factory is None and demo:
            from gazer.core.sources import SimulatedSource

            source_factory = lambda cfg: SimulatedSource()  # noqa: E731
        self.runner = EngineRunner(self.config, self.core, self._on_snapshot, self._on_status, source_factory)
        self.insights = Insights(screen, self.profile.settings.wellness)
        self.predictor = WordPredictor(self.profile.words)
        self.calib: CalibrationSession | None = None
        self.last_calib: dict | None = None
        self.ui: UIHooks | None = None
        self.listeners: list[Listener] = []
        self.camera_status = "Starting…"
        self.voice_listener: voice.VoiceListener | None = None
        self.voice_status = "Off"
        self.voice_heard = ""
        self.voice_download: float | None = None
        self.hotkeys = Hotkeys(self.config.hotkeys, self.perform)
        self._lock = threading.RLock()
        self._dirty = False
        self._neutral: dict | None = None
        self._last_insights = 0.0
        self._insights_payload: dict | None = None
        self._stop = threading.Event()
        self._saver: threading.Thread | None = None
        self.started = time.time()

    # ------------------------------------------------------------ lifecycle

    def _initial_profile(self) -> Profile:
        names = self.store.names()
        name = self.config.last_profile if self.config.last_profile in names else (names[0] if names else "")
        if name:
            return self.store.load(name)
        settings = ProfileSettings()
        apply_control_style(settings, "eyes")
        prof = self.store.create("Default", settings)
        self.config.last_profile = prof.name
        return prof

    def start(self) -> None:
        if self.demo and not self.profile.gaze.ready:
            self._demo_pretrain()
        self.runner.start()
        if self.config.hotkeys.enabled and not self.demo:
            self.hotkeys.start()
        if self.profile.settings.voice.enabled:
            self.set_voice(True)
        self._saver = threading.Thread(target=self._autosave, name="gazer-save", daemon=True)
        self._saver.start()

    def _demo_pretrain(self) -> None:
        """Demo mode starts with eyes already calibrated, so gaze control and
        the 3D gaze rays are live immediately (calibration can still be run)."""
        from gazer.core.simulator import synthetic_calibration

        X, Y = synthetic_calibration()
        rep = self.profile.gaze.fit(X, Y)
        self.profile.add_calibration("demo", len(X), rep.rmse * self.core.screen.width, "Excellent")
        self.mark_dirty()

    def stop(self) -> None:
        self._stop.set()
        self.hotkeys.stop()
        if self.voice_listener:
            self.voice_listener.stop()
        self.runner.stop()
        self.save()

    def save(self) -> None:
        with self._lock:
            self.profile.words = dict(self.predictor.learned)
            try:
                self.profile.save_all()
                if self.persist_config:
                    self.config.last_profile = self.profile.name
                    save_json(self.config, config_path())
            except OSError:
                log.exception("save")
            self._dirty = False

    def _autosave(self) -> None:
        last_meta = time.time()
        while not self._stop.wait(2.0):
            if self._dirty:
                self.save()
                last_meta = time.time()
            elif time.time() - last_meta > 60:
                try:
                    self.profile.save_meta()
                except OSError:
                    pass
                last_meta = time.time()

    def mark_dirty(self) -> None:
        self._dirty = True
        self.notify()

    def notify(self) -> None:
        for lst in list(self.listeners):
            try:
                lst.on_state()
            except Exception:
                log.exception("state listener")

    # -------------------------------------------------------------- engine

    def _on_status(self, text: str) -> None:
        self.camera_status = text
        self.notify()

    @property
    def sim_user(self):
        src = self.runner.source
        return getattr(src, "user", None)

    def _on_snapshot(self, snap: Snapshot) -> None:
        extra: dict[str, Any] = {}
        calib = self.calib
        if calib is not None:
            calib.on_snapshot(snap)
            user = self.sim_user
            if user is not None:
                target = calib.target()
                if target is None and calib.state == "results" and calib.good:
                    x, y, w, h = BUTTONS["accept"]
                    target = (x + w / 2, y + h / 2)  # the demo user confirms by gaze, hands-free
                user.attend(target)
            extra["calib"] = calib.view()
            if calib.finished:
                self._finish_calibration(calib)
        elif self.sim_user is not None:
            self.sim_user.attend(None)

        for ev in self.insights.update(snap):
            snap.events.append(("toast", ev[2]))
            snap.events.append(ev)
        if snap.t - self._last_insights >= 1.0:
            self._last_insights = snap.t
            self._insights_payload = self.insights.summary(snap.t)
            extra["insights"] = self._insights_payload

        if self._neutral is not None:
            self._collect_neutral(snap)

        for ev in snap.events:
            if ev[0] == "request":
                self._handle_request(ev[1])
            elif ev[0] == "mode":
                self.mark_dirty()

        for lst in list(self.listeners):
            try:
                lst.on_frame(snap, extra)
            except Exception:
                log.exception("frame listener")

    def _handle_request(self, kind: str) -> None:
        if kind == "keyboard_toggle":
            self.toggle_keyboard()
        elif kind == "voice_toggle":
            self.set_voice(not self.profile.settings.voice.enabled)
        elif kind == "calibrate":
            self.start_calibration("gaze", "standard")

    # ------------------------------------------------------------- actions

    def perform(self, action: str) -> None:
        """Run any action from anywhere (UI, hotkey, voice)."""
        if action == "control_toggle":
            self.set_control(not self.core.control)
        elif action == "keyboard_toggle":
            self.toggle_keyboard()
        elif action == "voice_toggle":
            self.set_voice(not self.profile.settings.voice.enabled)
        elif action == "calibrate":
            self.start_calibration("gaze", "standard")
        else:
            self.runner.post(lambda core: core.perform(action))
            if action in ("dwell_toggle", "zones_toggle", "mode_cycle") or action.startswith("mode:"):
                self.mark_dirty()

    def set_control(self, on: bool) -> None:
        self.runner.post(lambda core: core.set_control(on))
        if on and self.profile.settings.pointer.mode in ("gaze", "hybrid") and not self.profile.gaze.ready:
            self.runner.post(lambda core: core._toast("Eyes not calibrated yet — head control until you calibrate"))
        self.notify()

    def toggle_keyboard(self) -> None:
        if self.ui is not None:
            self.ui.toggle_keyboard()
        else:
            self.runner.post(lambda core: core._toast("The gaze keyboard needs the desktop app"))

    # -------------------------------------------------------- calibration

    def start_calibration(self, kind: str = "gaze", preset: str = "standard", append: bool = False) -> dict:
        with self._lock:
            if self.calib is not None and not self.calib.finished:
                return {"active": True}
            self.calib = CalibrationSession(kind, preset if kind == "gaze" else "head", self.core.screen, append)
            self.last_calib = None
        self.runner.post(lambda core: core.set_calibrating(True))
        opened = self.ui.open_calibration() if self.ui is not None else False
        self.notify()
        return {"active": True, "native_window": opened}

    def calibration_command(self, what: str) -> None:
        c = self.calib
        if c is None:
            return
        with self._lock:
            if what == "ready":
                c.ready()
            elif what == "skip":
                c.skip_intro()
            elif what == "accept":
                c.accept()
            elif what == "retry":
                c.retry()
            elif what == "cancel":
                c.cancel()
        if c.finished:
            self._finish_calibration(c)

    def _finish_calibration(self, c: CalibrationSession) -> None:
        with self._lock:
            if self.calib is not c:
                return
            self.calib = None
        res = c.result
        summary: dict[str, Any] = {"kind": c.kind, "saved": False}
        if res is not None and res.kind == "gaze":
            data = res.payload
            rep = data["report"]

            def fit():
                self.profile.gaze.fit(data["X"], data["Y"], append=data["append"])
                if rep is not None:
                    self.profile.add_calibration(data["preset"], len(data["X"]), rep.mean_px, rep.grade)
                self.mark_dirty()

            threading.Thread(target=fit, name="gazer-fit", daemon=True).start()
            summary.update(saved=True, grade=rep.grade if rep else "", mean_px=rep.mean_px if rep else 0)
            msg = f"Eyes calibrated · {rep.grade} · {rep.mean_px:.0f} px" if rep else "Eyes calibrated"
        elif res is not None and res.kind == "head":
            self.profile.settings.head_cal = res.payload
            summary["saved"] = True
            msg = "Head range calibrated"
        else:
            msg = "Calibration cancelled"

        def done(core: EngineCore):
            core.set_calibrating(False)
            core.reload_settings()
            core._toast(msg)

        self.runner.post(done)
        self.last_calib = summary
        if self.ui is not None:
            self.ui.close_calibration()
        self.mark_dirty()

    # --------------------------------------------------------------- voice

    def set_voice(self, on: bool) -> None:
        s = self.profile.settings.voice
        if self.voice_listener is not None:
            self.voice_listener.stop()
            self.voice_listener = None
        s.enabled = on
        if on:
            model = s.model_dir or str(assets.default_voice_model_dir())
            if not voice.available():
                self.voice_status = "Install vosk + sounddevice to use voice"
                s.enabled = False
            elif not assets.voice_model_ready(assets.Path(model)):
                self.voice_status = "Voice model not downloaded"
                s.enabled = False
            else:
                self.voice_listener = voice.VoiceListener(
                    model, on_action=self._voice_action, on_text=self._voice_text,
                    on_status=self._voice_status, device=s.device)
                self.voice_listener.start()
        else:
            self.voice_status = "Off"
        self.mark_dirty()

    def _voice_action(self, action: str) -> None:
        if action.startswith("type:"):
            self.runner.post(lambda core: core.perform(action))
        else:
            self.perform(action)

    def _voice_text(self, text: str) -> None:
        self.voice_heard = text
        self.notify()

    def _voice_status(self, text: str) -> None:
        self.voice_status = text
        self.notify()

    def download_voice_model(self) -> None:
        if self.voice_download is not None:
            return
        self.voice_download = 0.0
        self.notify()

        def work():
            try:
                def prog(done, total):
                    self.voice_download = done / total if total else 0.0
                assets.download_voice_model(prog)
                self.voice_status = "Model ready"
            except Exception as exc:  # noqa: BLE001
                self.voice_status = f"Download failed: {exc}"
            self.voice_download = None
            self.notify()

        threading.Thread(target=work, name="gazer-voice-dl", daemon=True).start()

    # ------------------------------------------------------------ gestures

    def capture_neutral(self, seconds: float = 2.5) -> None:
        self._neutral = {"until": None, "seconds": seconds, "acc": []}
        self.runner.post(lambda core: core._toast("Relax your face — capturing neutral…"))

    def _collect_neutral(self, snap: Snapshot) -> None:
        n = self._neutral
        if n is None:
            return
        if n["until"] is None:
            n["until"] = snap.t + n["seconds"]
        f = snap.features
        if f is not None:
            g = self.profile.settings.gestures
            n["acc"].append(raw_gesture_values(f, snap.closure, self.core.closure.swapped, g.swap_sides))
        if snap.t >= n["until"]:
            self._neutral = None
            acc = n["acc"]
            if len(acc) < 10:
                self.runner.post(lambda core: core._toast("No face — neutral not captured"))
                return
            neutral = {k: float(np.median([a[k] for a in acc])) for k in acc[0] if k not in NO_NEUTRAL}
            self.profile.settings.gestures.neutral = neutral
            self.runner.post(lambda core: core._toast("Neutral face captured"))
            self.mark_dirty()

    # ------------------------------------------------------------ profiles

    def switch_profile(self, name: str) -> None:
        self.save()
        prof = self.store.load(name)
        self._bind(prof)

    def _bind(self, prof: Profile) -> None:
        with self._lock:
            self.profile = prof
            self.config.last_profile = prof.name
            self.predictor = WordPredictor(prof.words)
            self.insights.s = prof.settings.wellness
        self.runner.post(lambda core: core.bind_profile(prof))
        self.mark_dirty()

    def create_profile(self, name: str, style: str = "eyes") -> None:
        settings = ProfileSettings()
        apply_control_style(settings, style)
        settings.style = style
        prof = self.store.create(name, settings)
        self.save()
        self._bind(prof)

    def delete_profile(self, name: str) -> None:
        if name == self.profile.name:
            raise ValueError("Switch to another profile before deleting this one.")
        self.store.delete(name)
        self.notify()

    def set_screen(self, index: int) -> None:
        self.config.screen_index = index
        self.monitor = pick_monitor(index)
        rect = self.monitor.rect

        def apply(core: EngineCore):
            core.screen = rect
            core.bind_profile(core.profile)

        self.runner.post(apply)
        self.insights.screen = rect
        if self.ui is not None:
            self.ui.screen_changed()
        self.mark_dirty()

    # ------------------------------------------------------------ commands

    def command(self, cmd: str, args: dict | None = None) -> Any:
        """Entry point for the web UI. Raises ValueError for bad input."""
        a = args or {}
        s = self.profile.settings
        if cmd == "control":
            self.set_control(bool(a.get("on", not self.core.control)))
        elif cmd == "pause":
            on = a.get("on")
            self.perform("pause_toggle" if on is None else ("pause" if on else "resume"))
        elif cmd == "perform":
            action = str(a["action"])
            if action not in ACTIONS and not action.startswith(("mode:", "key:", "hotkey:", "type:")) \
                    and action not in ("pause", "resume", "stop", "control_toggle"):
                raise ValueError(f"Unknown action {action}")
            self.perform(action)
        elif cmd == "mode":
            mode = str(a["mode"])
            if mode not in MODE_ORDER:
                raise ValueError("Unknown mode")
            self.runner.post(lambda core: core.set_mode(mode))
            s.pointer.mode = mode
            self.mark_dirty()
        elif cmd == "style":
            style = str(a["style"])
            if style not in CONTROL_STYLES:
                raise ValueError("Unknown style")
            apply_control_style(s, style)
            s.style = style
            self._reload()
        elif cmd == "settings":
            set_path(s, str(a["path"]), a["value"])
            self._reload()
        elif cmd == "app_settings":
            path = str(a["path"])
            set_path(self.config, path, a["value"])
            if path.startswith("camera."):
                self.runner.restart_camera(self.config.camera)
            elif path == "screen_index":
                self.set_screen(int(a["value"]))
            elif path.startswith("hotkeys."):
                self.hotkeys.settings = self.config.hotkeys
                self.hotkeys.start()
            self.mark_dirty()
        elif cmd == "bindings":
            s.gestures.bindings = [from_dict(Binding, b) for b in a["bindings"]]
            self._reload()
        elif cmd == "neutral":
            self.capture_neutral()
        elif cmd == "calibrate":
            return self.start_calibration(str(a.get("kind", "gaze")), str(a.get("preset", "standard")),
                                          bool(a.get("append", False)))
        elif cmd == "calib":
            self.calibration_command(str(a["what"]))
        elif cmd == "profile_create":
            self.create_profile(str(a["name"]), str(a.get("style", "eyes")))
        elif cmd == "profile_switch":
            self.switch_profile(str(a["name"]))
        elif cmd == "profile_delete":
            self.delete_profile(str(a["name"]))
        elif cmd == "profile_duplicate":
            self.store.duplicate(str(a["src"]), str(a["dst"]))
            self.notify()
        elif cmd == "forget_implicit":
            threading.Thread(target=self.profile.gaze.clear_implicit, daemon=True).start()
            self.mark_dirty()
        elif cmd == "delete_gaze":
            self.profile.gaze.reset()
            self.profile.history.clear()
            self.mark_dirty()
        elif cmd == "reset_heatmap":
            self.insights.reset()
        elif cmd == "keyboard":
            self.toggle_keyboard()
        elif cmd == "voice":
            self.set_voice(bool(a.get("on", not s.voice.enabled)))
        elif cmd == "voice_download":
            self.download_voice_model()
        elif cmd == "camera_restart":
            self.runner.restart_camera(self.config.camera)
        elif cmd == "demo_gesture":
            user = self.sim_user
            if user is None:
                raise ValueError("Only available in demo mode")
            user.trigger(str(a["name"]), float(a.get("length", 0.7)))
        elif cmd == "quit":
            if self.ui is not None:
                self.ui.quit()
        else:
            raise ValueError(f"Unknown command {cmd}")
        return None

    def _reload(self) -> None:
        self.insights.s = self.profile.settings.wellness
        self.runner.post(lambda core: core.reload_settings())
        self.mark_dirty()

    # ---------------------------------------------------------------- state

    def state(self) -> dict:
        prof = self.profile
        g = prof.gaze
        rep = g.last_report
        s = self.core.screen
        return {
            "app": {
                "version": __version__, "demo": self.demo, "virtual_input": self.virtual_input,
                "platform": platform.system(), "camera": self.camera_status,
                "source": getattr(self.runner.source, "name", ""), "native_ui": self.ui is not None,
                "uptime": time.time() - self.started,
            },
            "config": config_to_patchable(self.config),
            "profile": {
                "name": prof.name,
                "settings": to_dict(prof.settings),
                "gaze": {"ready": g.ready, "samples": g.n_samples, "implicit": len(g.implicit),
                         "rmse": rep.rmse if rep else None},
                "history": [asdict(h) for h in prof.history[-20:]],
                "stats": asdict(prof.stats),
            },
            "profiles": self.store.names(),
            "monitors": [{"name": m.name, "w": m.rect.width, "h": m.rect.height, "primary": m.primary}
                         for m in sorted(self.monitors, key=lambda m: not m.primary)],
            "screen": {"w": s.width, "h": s.height},
            "control": self.core.control, "paused": self.core.paused,
            "calibrating": self.calib is not None, "last_calib": self.last_calib,
            "voice": {"available": voice.available(), "model_ready": assets.voice_model_ready(),
                      "status": self.voice_status, "heard": self.voice_heard,
                      "download": self.voice_download,
                      "commands": sorted(voice.COMMANDS)},
            "hotkeys": {"error": self.hotkeys.error},
            "insights": self._insights_payload,
        }

    @staticmethod
    def catalog() -> dict:
        return {
            "modes": POINTER_MODES, "mode_order": MODE_ORDER,
            "gestures": {k: {"label": v[0], "threshold": v[1], "hold": v[2], "desc": v[3]}
                         for k, v in GESTURE_CATALOG.items()},
            "triggers": TRIGGERS, "actions": ACTIONS, "presets": PRESETS, "zones": ZONE_NAMES,
            "styles": {k: {"label": v[0], "desc": v[1]} for k, v in CONTROL_STYLES.items()},
        }


def run_headless(ctl: Controller, stop: Callable[[], bool]) -> None:
    ctl.start()
    try:
        while not stop():
            time.sleep(0.2)
    finally:
        ctl.stop()
