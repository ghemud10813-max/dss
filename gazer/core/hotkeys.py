"""System-wide hotkeys (pynput). Optional: silently disabled where unavailable
(e.g. Wayland sessions or headless machines)."""

from __future__ import annotations

import logging
from typing import Callable

from gazer.config import HotkeySettings

log = logging.getLogger("gazer.hotkeys")

HOTKEY_ACTIONS = {
    "pause": "pause_toggle",
    "recenter": "recenter",
    "keyboard": "keyboard_toggle",
    "mode": "mode_cycle",
    "calibrate": "calibrate",
    "stop": "control_toggle",
}


class Hotkeys:
    def __init__(self, settings: HotkeySettings, on_action: Callable[[str], None]):
        self.settings = settings
        self.on_action = on_action
        self._listener = None
        self.error = ""

    def start(self) -> bool:
        self.stop()
        if not self.settings.enabled:
            return False
        try:
            from pynput import keyboard
        except Exception as exc:  # noqa: BLE001
            self.error = f"pynput unavailable: {exc}"
            return False
        mapping = {}
        for field_name, action in HOTKEY_ACTIONS.items():
            combo = getattr(self.settings, field_name, "")
            if combo:
                mapping[combo] = (lambda a=action: self._fire(a))
        try:
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = f"Hotkeys unavailable: {exc}"
            log.info(self.error)
            self._listener = None
            return False

    def _fire(self, action: str) -> None:
        try:
            self.on_action(action)
        except Exception:
            log.exception("hotkey action")

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
