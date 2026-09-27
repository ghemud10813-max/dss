"""Offline voice commands with Vosk (optional dependency).

Two modes:
  * command mode — a fixed grammar, which makes recognition fast and robust;
  * dictation mode — free speech typed into the focused app
    ("start dictation" / "stop dictation").
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Callable

COMMANDS: dict[str, str] = {
    "click": "left_click", "left click": "left_click", "right click": "right_click",
    "double click": "double_click", "middle click": "middle_click",
    "drag": "drag_toggle", "drop": "drag_toggle", "grab": "drag_toggle",
    "scroll": "scroll_mode", "scroll mode": "scroll_mode", "stop": "stop",
    "scroll up": "scroll_up", "scroll down": "scroll_down",
    "page up": "key:pageup", "page down": "key:pagedown",
    "menu": "action_wheel", "wheel": "action_wheel", "zoom": "zoom",
    "keyboard": "keyboard_toggle", "recenter": "recenter", "center": "recenter",
    "precision": "precision_toggle", "pause": "pause", "sleep": "pause",
    "resume": "resume", "wake up": "resume", "dwell": "dwell_toggle",
    "calibrate": "calibrate",
    "enter": "key:enter", "press enter": "key:enter", "escape": "key:esc", "tab": "key:tab",
    "backspace": "key:backspace", "delete": "key:delete", "space": "key:space",
    "up": "key:up", "down": "key:down", "left": "key:left", "right": "key:right",
    "home": "key:home", "end": "key:end",
    "copy": "hotkey:ctrl+c", "paste": "hotkey:ctrl+v", "cut": "hotkey:ctrl+x",
    "undo": "hotkey:ctrl+z", "redo": "hotkey:ctrl+y", "select all": "hotkey:ctrl+a",
    "save": "hotkey:ctrl+s", "new tab": "hotkey:ctrl+t", "close tab": "hotkey:ctrl+w",
    "go back": "hotkey:alt+left", "go forward": "hotkey:alt+right", "refresh": "key:f5",
    "switch window": "hotkey:alt+tab", "start menu": "hotkey:win", "show desktop": "hotkey:win+d",
    "volume up": "key:volumeup", "volume down": "key:volumedown", "mute": "key:volumemute",
    "play": "key:playpause", "next track": "key:nexttrack",
    "mode hybrid": "mode:hybrid", "mode gaze": "mode:gaze", "mode head": "mode:head_mouse",
    "mode joystick": "mode:head_joystick", "mode pointer": "mode:head_absolute",
    "start dictation": "dictation_on", "stop dictation": "dictation_off",
}


def parse_command(text: str) -> str | None:
    t = " ".join(text.lower().split())
    if not t or t == "[unk]":
        return None
    if t in COMMANDS:
        return COMMANDS[t]
    # Tolerate a leading filler ("please click", "ok scroll down").
    words = t.split()
    for i in range(1, len(words)):
        tail = " ".join(words[i:])
        if tail in COMMANDS:
            return COMMANDS[tail]
    return None


def grammar() -> str:
    vocab = sorted(set(COMMANDS))
    return json.dumps(vocab + ["[unk]"])


def available() -> bool:
    try:
        import sounddevice  # noqa: F401
        import vosk  # noqa: F401
        return True
    except Exception:
        return False


class VoiceListener:
    def __init__(self, model_dir: Path, on_action: Callable[[str], None],
                 on_text: Callable[[str], None], on_status: Callable[[str], None], device: int = -1):
        self.model_dir = Path(model_dir)
        self.on_action = on_action
        self.on_text = on_text
        self.on_status = on_status
        self.device = None if device < 0 else device
        self.dictation = False
        self._q: queue.Queue[bytes] = queue.Queue(maxsize=50)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gazer-voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        try:
            import sounddevice as sd
            import vosk

            vosk.SetLogLevel(-1)
            self.on_status("Loading voice model…")
            model = vosk.Model(str(self.model_dir))
            cmd_rec = vosk.KaldiRecognizer(model, 16000, grammar())
            free_rec = vosk.KaldiRecognizer(model, 16000)
        except Exception as exc:
            self.on_status(f"Voice unavailable: {exc}")
            return

        def callback(indata, frames, time_info, status):
            try:
                self._q.put_nowait(bytes(indata))
            except queue.Full:
                pass

        try:
            with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype="int16", channels=1,
                                   callback=callback, device=self.device):
                self.on_status("Listening")
                while not self._stop.is_set():
                    try:
                        data = self._q.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    rec = free_rec if self.dictation else cmd_rec
                    if rec.AcceptWaveform(data):
                        text = json.loads(rec.Result()).get("text", "").strip()
                        if text:
                            self._handle(text)
        except Exception as exc:
            self.on_status(f"Microphone error: {exc}")
            return
        self.on_status("Stopped")

    def _handle(self, text: str) -> None:
        self.on_text(text)
        if self.dictation:
            if text.lower().strip() in ("stop dictation", "stop typing"):
                self.dictation = False
                self.on_status("Listening")
                return
            self.on_action("type:" + text + " ")
            return
        action = parse_command(text)
        if action == "dictation_on":
            self.dictation = True
            self.on_status("Dictation — say “stop dictation” to finish")
        elif action == "dictation_off":
            self.dictation = False
        elif action:
            self.on_action(action)
