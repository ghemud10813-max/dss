"""Typed settings with forgiving JSON round-tripping.

Every setting lives in a dataclass. Loading tolerates missing keys (defaults
are used) and unknown keys (ignored), so old config files keep working as the
app grows.
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar, Union

T = TypeVar("T")

POINTER_MODES: dict[str, str] = {
    "hybrid": "Hybrid — eyes jump, head fine-tunes",
    "gaze": "Gaze — cursor follows your eyes",
    "head_mouse": "Head mouse — head moves the cursor like a mouse",
    "head_joystick": "Head joystick — tilt away from centre to glide",
    "head_absolute": "Head pointer — nose points at the screen",
}

# name: (label, default threshold, default min hold ms, description)
GESTURE_CATALOG: dict[str, tuple[str, float, int, str]] = {
    "smile": ("Smile", 0.55, 150, "Both mouth corners up"),
    "mouth_open": ("Open mouth", 0.40, 200, "Drop your jaw"),
    "pucker": ("Pucker / kiss", 0.65, 150, "Push lips forward"),
    "brow_raise": ("Raise eyebrows", 0.55, 150, "Lift both eyebrows"),
    "brow_down": ("Frown", 0.50, 150, "Pull eyebrows down"),
    "mouth_left": ("Mouth left", 0.45, 150, "Pull mouth to the left"),
    "mouth_right": ("Mouth right", 0.45, 150, "Pull mouth to the right"),
    "cheek_puff": ("Puff cheeks", 0.35, 200, "Fill cheeks with air"),
    "lips_in": ("Lips in", 0.45, 200, "Roll both lips inward"),
    "wink_left": ("Wink left", 0.55, 150, "Close only your left eye"),
    "wink_right": ("Wink right", 0.55, 150, "Close only your right eye"),
    "long_blink": ("Long blink", 0.55, 700, "Close both eyes ~1 second"),
    "tilt_left": ("Tilt head left", 0.50, 250, "Ear toward left shoulder"),
    "tilt_right": ("Tilt head right", 0.50, 250, "Ear toward right shoulder"),
}

TRIGGERS: dict[str, str] = {
    "start": "When gesture starts",
    "hold": "After holding",
    "tap": "Quick tap (release before hold time)",
    "while_held": "While held (press → release)",
}

ACTIONS: dict[str, str] = {
    "none": "— nothing —",
    "left_click": "Left click",
    "right_click": "Right click",
    "double_click": "Double click",
    "middle_click": "Middle click",
    "drag_toggle": "Drag: grab / drop",
    "drag_hold": "Drag while held",
    "scroll_mode": "Scroll mode on/off",
    "scroll_up": "Scroll up",
    "scroll_down": "Scroll down",
    "action_wheel": "Open action wheel",
    "zoom": "Zoom-click lens",
    "keyboard_toggle": "Show/hide keyboard",
    "pause_toggle": "Pause / resume",
    "recenter": "Recenter / fix drift",
    "precision_toggle": "Precision mode on/off",
    "mode_cycle": "Next pointer mode",
    "dwell_toggle": "Dwell click on/off",
    "voice_toggle": "Voice commands on/off",
    "calibrate": "Start calibration",
    "key:enter": "Key: Enter",
    "key:esc": "Key: Escape",
    "key:backspace": "Key: Backspace",
    "key:tab": "Key: Tab",
    "key:space": "Key: Space",
    "key:pageup": "Key: Page Up",
    "key:pagedown": "Key: Page Down",
    "hotkey:alt+left": "Browser back",
    "hotkey:alt+right": "Browser forward",
    "hotkey:alt+tab": "Switch window",
    "hotkey:win": "Start menu",
    "hotkey:ctrl+c": "Copy",
    "hotkey:ctrl+v": "Paste",
    "hotkey:ctrl+z": "Undo",
    "hotkey:win+d": "Show desktop",
}

DEFAULT_WHEEL_ITEMS = [
    "left_click",
    "double_click",
    "right_click",
    "drag_toggle",
    "scroll_mode",
    "zoom",
    "keyboard_toggle",
    "recenter",
]


@dataclass
class CameraSettings:
    index: int = 0
    backend: str = "auto"  # auto | msmf | dshow | v4l2 | any
    width: int = 1280
    height: int = 720
    fps: int = 30
    mirror: bool = True


@dataclass
class PointerSettings:
    mode: str = "hybrid"
    gaze_smoothing: float = 0.6  # 0 = raw & jumpy, 1 = heavy & stable
    head_gain: float = 1.0
    head_accel: float = 1.6  # 1 = linear, higher = more acceleration
    head_deadzone: float = 0.0012  # face-widths per frame
    joystick_speed: float = 1.0
    joystick_deadzone: float = 0.025
    absolute_gain: float = 1.0
    hybrid_fine_gain: float = 0.6
    warp_threshold: float = 0.13  # fraction of screen width
    warp_delay_ms: int = 140
    output_smoothing_ms: int = 35
    precision_factor: float = 0.35
    manual_override: bool = True
    adaptive_learning: bool = True
    invert_x: bool = False
    invert_y: bool = False


@dataclass
class DwellSettings:
    enabled: bool = False
    time_ms: int = 900
    radius_px: int = 45
    action: str = "left_click"
    cooldown_ms: int = 700


@dataclass
class WheelSettings:
    items: list[str] = field(default_factory=lambda: list(DEFAULT_WHEEL_ITEMS))
    select_dwell_ms: int = 450
    radius_px: int = 160
    timeout_s: float = 8.0


@dataclass
class ZoomSettings:
    factor: float = 3.0
    region_w: int = 260
    region_h: int = 160
    timeout_s: float = 12.0


@dataclass
class ScrollSettings:
    speed: float = 1.0
    deadzone: float = 0.03
    invert: bool = False
    horizontal: bool = True
    timeout_s: float = 20.0


@dataclass
class GestureSetting:
    enabled: bool = True
    threshold: float = 0.5
    min_hold_ms: int = 150


@dataclass
class Binding:
    gesture: str = ""
    trigger: str = "start"
    action: str = "none"
    hold_ms: int = 600


def default_gesture_table() -> dict[str, GestureSetting]:
    return {
        name: GestureSetting(True, thr, hold) for name, (_, thr, hold, _) in GESTURE_CATALOG.items()
    }


def default_bindings() -> list[Binding]:
    return [
        Binding("smile", "start", "left_click"),
        Binding("wink_left", "start", "left_click"),
        Binding("wink_right", "start", "right_click"),
        Binding("pucker", "start", "right_click"),
        Binding("brow_raise", "start", "scroll_mode"),
        Binding("mouth_open", "hold", "action_wheel", 600),
        Binding("long_blink", "start", "pause_toggle"),
    ]


@dataclass
class GestureSettings:
    gestures: dict[str, GestureSetting] = field(default_factory=default_gesture_table)
    bindings: list[Binding] = field(default_factory=default_bindings)
    neutral: dict[str, float] = field(default_factory=dict)
    swap_sides: bool = False
    refractory_ms: int = 250


@dataclass
class OverlaySettings:
    show_cursor_ring: bool = True
    show_gaze_dot: bool = False
    show_dwell_ring: bool = True
    show_toasts: bool = True
    click_ripple: bool = True
    accent: str = "#3dd6c6"


@dataclass
class VoiceSettings:
    enabled: bool = False
    model_dir: str = ""
    device: int = -1  # -1 = system default


@dataclass
class KeyboardSettings:
    dock: str = "bottom"  # bottom | top
    height_frac: float = 0.40
    predictions: bool = True


@dataclass
class HeadCalibration:
    calibrated: bool = False
    neutral_x: float = 0.0
    neutral_y: float = 0.0
    gain_x: float = 0.0  # screen px per face-width unit
    gain_y: float = 0.0


@dataclass
class ProfileSettings:
    pointer: PointerSettings = field(default_factory=PointerSettings)
    dwell: DwellSettings = field(default_factory=DwellSettings)
    wheel: WheelSettings = field(default_factory=WheelSettings)
    zoom: ZoomSettings = field(default_factory=ZoomSettings)
    scroll: ScrollSettings = field(default_factory=ScrollSettings)
    gestures: GestureSettings = field(default_factory=GestureSettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    keyboard: KeyboardSettings = field(default_factory=KeyboardSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    head_cal: HeadCalibration = field(default_factory=HeadCalibration)


@dataclass
class HotkeySettings:
    enabled: bool = True
    pause: str = "<ctrl>+<alt>+p"
    recenter: str = "<ctrl>+<alt>+r"
    keyboard: str = "<ctrl>+<alt>+k"
    mode: str = "<ctrl>+<alt>+m"
    calibrate: str = "<ctrl>+<alt>+c"
    stop: str = "<ctrl>+<alt>+q"


@dataclass
class AppConfig:
    last_profile: str = ""
    camera: CameraSettings = field(default_factory=CameraSettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    screen_index: int = 0
    start_minimized: bool = False
    first_run_done: bool = False
    preview_landmarks: bool = True


# --------------------------------------------------------------------- serde


def to_dict(obj: Any) -> Any:
    return dataclasses.asdict(obj)


def from_dict(cls: type[T], data: Any) -> T:
    """Build `cls` from a (possibly partial or stale) dict."""
    if not isinstance(data, dict):
        return cls()
    hints = typing.get_type_hints(cls)
    kwargs = {}
    for f in fields(cls):
        if f.name in data:
            try:
                kwargs[f.name] = _coerce(hints[f.name], data[f.name])
            except (TypeError, ValueError):
                pass
    try:
        return cls(**kwargs)
    except TypeError:
        return cls()


def _coerce(tp: Any, value: Any) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if is_dataclass(tp):
        return from_dict(tp, value)
    if origin is list:
        inner = args[0] if args else Any
        if not isinstance(value, list):
            raise TypeError("expected list")
        return [_coerce(inner, v) for v in value]
    if origin is dict:
        _, inner = args if args else (str, Any)
        if not isinstance(value, dict):
            raise TypeError("expected dict")
        return {str(k): _coerce(inner, v) for k, v in value.items()}
    if origin in (Union, types.UnionType):
        if value is None:
            return None
        non_none = [a for a in args if a is not type(None)]
        return _coerce(non_none[0], value)
    if tp is Any:
        return value
    if tp is bool:
        if not isinstance(value, bool):
            raise TypeError("expected bool")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("expected number")
        return float(value)
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("expected number")
        return int(value)
    if tp is str:
        if not isinstance(value, str):
            raise TypeError("expected str")
        return value
    return value


def load_json(cls: type[T], path: Path) -> T:
    try:
        return from_dict(cls, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return cls()


def save_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(to_dict(obj), indent=2), encoding="utf-8")
    tmp.replace(path)
