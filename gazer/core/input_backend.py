"""OS input injection: mouse, wheel, keys and text.

Windows uses SendInput / SetCursorPos directly (fast, no hooks). Other
platforms fall back to pynput. `RecordingInput` is an in-memory backend for
tests and dry runs.
"""

from __future__ import annotations

import sys
import threading
import time

# Named keys → Windows virtual-key codes. Extended keys need a flag.
VK: dict[str, int] = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
    "tab": 0x09, "space": 0x20, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "delete": 0x2E,
    "insert": 0x2D, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
    "win": 0x5B, "cmd": 0x5B, "capslock": 0x14, "printscreen": 0x2C, "menu": 0x5D,
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF, "playpause": 0xB3,
    "nexttrack": 0xB0, "prevtrack": 0xB1,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
_EXTENDED = {0x25, 0x26, 0x27, 0x28, 0x24, 0x23, 0x21, 0x22, 0x2E, 0x2D, 0x5B, 0x5D, 0xAD, 0xAE, 0xAF, 0xB3, 0xB0, 0xB1}
MODIFIERS = ("ctrl", "control", "alt", "shift", "win", "cmd")
WHEEL_DELTA = 120


def parse_combo(combo: str) -> list[str]:
    return [p.strip().lower() for p in combo.replace("-", "+").split("+") if p.strip()]


class InputBackend:
    """Interface. Coordinates are physical screen pixels."""

    def move(self, x: float, y: float) -> None: ...
    def position(self) -> tuple[int, int]: return (0, 0)
    def button(self, button: str, down: bool) -> None: ...
    def scroll(self, dy: int, dx: int = 0) -> None: ...
    def key(self, name: str, down: bool) -> None: ...
    def type_text(self, text: str) -> None: ...

    # -- conveniences built on the primitives
    def click(self, button: str = "left", count: int = 1) -> None:
        for i in range(count):
            self.button(button, True)
            self.button(button, False)
            if i + 1 < count:
                time.sleep(0.03)

    def tap(self, name: str) -> None:
        self.key(name, True)
        self.key(name, False)

    def hotkey(self, combo: str) -> None:
        keys = parse_combo(combo)
        mods = [k for k in keys if k in MODIFIERS]
        rest = [k for k in keys if k not in MODIFIERS]
        for m in mods:
            self.key(m, True)
        if rest:
            for k in rest:
                self.tap(k)
        else:
            time.sleep(0.02)
        for m in reversed(mods):
            self.key(m, False)


class RecordingInput(InputBackend):
    def __init__(self, pos: tuple[int, int] = (0, 0)):
        self.events: list[tuple] = []
        self._pos = (int(pos[0]), int(pos[1]))
        self._lock = threading.Lock()

    def move(self, x, y):
        with self._lock:
            self._pos = (int(round(x)), int(round(y)))

    def position(self):
        with self._lock:
            return self._pos

    def button(self, button, down):
        self.events.append(("button", button, down, self.position()))

    def scroll(self, dy, dx=0):
        self.events.append(("scroll", int(dy), int(dx)))

    def key(self, name, down):
        self.events.append(("key", name, down))

    def type_text(self, text):
        self.events.append(("text", text))

    def clicks(self) -> list[tuple[str, tuple[int, int]]]:
        return [(e[1], e[3]) for e in self.events if e[0] == "button" and e[2]]


class WinInput(InputBackend):
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ct = ctypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        ulong_ptr = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self._INPUT = INPUT
        self._POINT = wintypes.POINT
        self._user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._user32.SendInput.restype = wintypes.UINT
        self._user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self._user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)

    _BTN = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}

    def _send(self, inputs: list) -> None:
        arr = (self._INPUT * len(inputs))(*inputs)
        self._user32.SendInput(len(inputs), arr, self._ct.sizeof(self._INPUT))

    def _mouse(self, flags: int, data: int = 0):
        inp = self._INPUT(type=0)
        inp.mi.dwFlags = flags
        inp.mi.mouseData = data & 0xFFFFFFFF
        return inp

    def _kbd(self, vk: int = 0, scan: int = 0, flags: int = 0):
        inp = self._INPUT(type=1)
        inp.ki.wVk = vk
        inp.ki.wScan = scan
        inp.ki.dwFlags = flags
        return inp

    def move(self, x, y):
        self._user32.SetCursorPos(int(round(x)), int(round(y)))

    def position(self):
        pt = self._POINT()
        self._user32.GetCursorPos(self._ct.byref(pt))
        return pt.x, pt.y

    def button(self, button, down):
        d, u = self._BTN.get(button, self._BTN["left"])
        self._send([self._mouse(d if down else u)])

    def click(self, button="left", count=1):
        d, u = self._BTN.get(button, self._BTN["left"])
        seq = []
        for _ in range(count):
            seq += [self._mouse(d), self._mouse(u)]
        self._send(seq)

    def scroll(self, dy, dx=0):
        seq = []
        if dy:
            seq.append(self._mouse(0x0800, int(dy)))
        if dx:
            seq.append(self._mouse(0x1000, int(dx)))
        if seq:
            self._send(seq)

    def _vk_for(self, name: str) -> int | None:
        if name in VK:
            return VK[name]
        if len(name) == 1:
            res = self._user32.VkKeyScanW(ord(name))
            if res != -1 and res != 0xFFFF:
                return res & 0xFF
        return None

    def key(self, name, down):
        name = name.lower()
        vk = self._vk_for(name)
        if vk is None:
            if down and len(name) == 1:
                self.type_text(name)
            return
        flags = (0 if down else 0x0002) | (0x0001 if vk in _EXTENDED else 0)
        self._send([self._kbd(vk=vk, flags=flags)])

    def type_text(self, text):
        seq = []
        for ch in text:
            if ch == "\n":
                seq += [self._kbd(vk=0x0D), self._kbd(vk=0x0D, flags=0x0002)]
                continue
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i:i + 2], "little")
                seq += [self._kbd(scan=unit, flags=0x0004), self._kbd(scan=unit, flags=0x0004 | 0x0002)]
        if seq:
            self._send(seq)


class PynputInput(InputBackend):
    def __init__(self):
        from pynput import keyboard, mouse

        self._m = mouse.Controller()
        self._k = keyboard.Controller()
        self._Button = mouse.Button
        self._Key = keyboard.Key

    def move(self, x, y):
        self._m.position = (int(round(x)), int(round(y)))

    def position(self):
        x, y = self._m.position
        return int(x), int(y)

    def button(self, button, down):
        b = getattr(self._Button, button, self._Button.left)
        (self._m.press if down else self._m.release)(b)

    def scroll(self, dy, dx=0):
        self._m.scroll(dx / WHEEL_DELTA, dy / WHEEL_DELTA)

    def _key_obj(self, name):
        alias = {"esc": "esc", "escape": "esc", "return": "enter", "control": "ctrl", "win": "cmd",
                 "pageup": "page_up", "pagedown": "page_down", "capslock": "caps_lock",
                 "printscreen": "print_screen", "volumemute": "media_volume_mute",
                 "volumeup": "media_volume_up", "volumedown": "media_volume_down",
                 "playpause": "media_play_pause", "nexttrack": "media_next", "prevtrack": "media_previous"}
        n = alias.get(name, name)
        if hasattr(self._Key, n):
            return getattr(self._Key, n)
        return name if len(name) == 1 else None

    def key(self, name, down):
        k = self._key_obj(name.lower())
        if k is None:
            return
        (self._k.press if down else self._k.release)(k)

    def type_text(self, text):
        self._k.type(text)


def create_backend() -> InputBackend:
    if sys.platform == "win32":
        try:
            return WinInput()
        except Exception:
            pass
    try:
        return PynputInput()
    except Exception:
        return RecordingInput()
