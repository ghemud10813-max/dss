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
    "double_blink": ("Double blink", 0.55, 0, "Two deliberate blinks within half a second"),
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
    "zoom_click": "Precise click (zoom, then click)",
    "dock_toggle": "Show/hide Eye Dock",
    "trainer": "Open Gaze Trainer",
    "keyboard_toggle": "Show/hide keyboard",
    "pause_toggle": "Pause / resume",
    "recenter": "Recenter / fix drift",
    "precision_toggle": "Precision mode on/off",
    "mode_cycle": "Next pointer mode",
    "dwell_toggle": "Dwell click on/off",
    "voice_toggle": "Voice commands on/off",
    "calibrate": "Start calibration",
    "zones_toggle": "Gaze zones on/off",
    "key:up": "Key: Arrow up",
    "key:down": "Key: Arrow down",
    "hotkey:ctrl+w": "Close tab",
    "hotkey:ctrl+t": "New tab",
    "hotkey:alt+f4": "Close window",
    "hotkey:ctrl+s": "Save",
    "key:volumeup": "Volume up",
    "key:volumedown": "Volume down",
    "key:playpause": "Play / pause media",
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
    # Magnetic targets: lock the gaze cursor onto the button/link you look at
    # (OS accessibility API; Windows UI Automation).
    magnetic: bool = False
    magnetic_radius_px: int = 45


@dataclass
class DwellSettings:
    enabled: bool = False
    time_ms: int = 900
    radius_px: int = 45
    action: str = "left_click"
    cooldown_ms: int = 700
    targets_only: bool = False  # only dwell-click on buttons/links (needs magnetic targets)


@dataclass
class DockSettings:
    """Eye Dock: a hover-dwell toolbar choosing what the next dwell does."""

    enabled: bool = False
    side: str = "right"  # right | left
    button_px: int = 86
    dwell_ms: int = 700


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
        Binding("double_blink", "start", "left_click"),
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
    accent: str = "#3ee8d8"


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
    layout: str = "en"  # en | hi (Devanagari)


ZONE_NAMES: dict[str, str] = {
    "top_left": "Top-left corner",
    "top": "Top edge",
    "top_right": "Top-right corner",
    "right": "Right edge",
    "bottom_right": "Bottom-right corner",
    "bottom": "Bottom edge",
    "bottom_left": "Bottom-left corner",
    "left": "Left edge",
}


def default_zone_actions() -> dict[str, str]:
    return {
        "top_left": "action_wheel",
        "top": "scroll_up",
        "top_right": "pause_toggle",
        "right": "hotkey:alt+right",
        "bottom_right": "keyboard_toggle",
        "bottom": "scroll_down",
        "bottom_left": "zoom",
        "left": "hotkey:alt+left",
    }


@dataclass
class ZoneSettings:
    """Gaze hot-zones: look at a screen corner/edge for a moment to fire an action."""

    enabled: bool = False
    dwell_ms: int = 700
    size: float = 0.03  # edge band, fraction of the shorter screen side (corners: 2x)
    actions: dict[str, str] = field(default_factory=default_zone_actions)


@dataclass
class WellnessSettings:
    """Eye-health assistant: blink-rate monitor and 20-20-20 breaks."""

    enabled: bool = True
    break_interval_min: float = 20.0
    break_length_s: float = 20.0
    low_blink_per_min: float = 8.0


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
    zones: ZoneSettings = field(default_factory=ZoneSettings)
    wellness: WellnessSettings = field(default_factory=WellnessSettings)
    dock: DockSettings = field(default_factory=DockSettings)
    style: str = "eyes"


@dataclass
class UISettings:
    sound: bool = True
    boot_sequence: bool = True
    reduced_motion: bool = False
    bloom: float = 1.0  # 0 = off
    quality: str = "high"  # high | low


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
    port: int = 8765
    ui: UISettings = field(default_factory=UISettings)


CONTROL_STYLES: dict[str, tuple[str, str]] = {
    "eyes": ("Eyes only", "Cursor follows your gaze. Click by dwelling, winking or blinking twice. "
                          "Glance past the screen edges to scroll, go back/forward, pause or open the keyboard."),
    "eyes_head": ("Eyes + head", "Eyes jump the cursor across the screen, small head moves place it "
                                 "exactly. Smile or blink twice to click."),
    "head": ("Head only", "Your head moves the cursor like a mouse. No calibration needed. "
                          "Smile to click, raise brows to scroll."),
}


def apply_control_style(s: "ProfileSettings", style: str) -> None:
    """Configure a profile for a control style (keeps unrelated settings)."""
    g = s.gestures
    if style == "eyes":
        s.pointer.mode = "gaze"
        s.pointer.gaze_smoothing = 0.7
        s.dwell.enabled = True
        s.dwell.time_ms = 1000
        s.dwell.radius_px = 60
        s.dwell.action = "left_click"
        s.zones.enabled = True
        s.dock.enabled = True
        g.bindings = [
            Binding("double_blink", "start", "left_click"),
            Binding("wink_left", "start", "left_click"),
            Binding("wink_right", "start", "right_click"),
            Binding("long_blink", "start", "pause_toggle"),
            Binding("brow_raise", "hold", "action_wheel", 500),
            Binding("smile", "start", "left_click"),
        ]
    elif style == "eyes_head":
        s.pointer.mode = "hybrid"
        s.dwell.enabled = False
        s.dock.enabled = False
        s.zones.enabled = True
        g.bindings = default_bindings()
    elif style == "head":
        s.pointer.mode = "head_mouse"
        s.dwell.enabled = False
        s.dock.enabled = False
        s.zones.enabled = False
        g.bindings = default_bindings()


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


def config_to_patchable(obj: Any) -> Any:
    return to_dict(obj)


def set_path(root: Any, path: str, value: Any) -> None:
    """Set a nested setting by dotted path with type checking, e.g.
    ``pointer.gaze_smoothing`` or ``gestures.gestures.smile.threshold`` or
    ``zones.actions.top``. Raises ValueError on unknown paths / bad types."""
    parts = [p for p in path.split(".") if p]
    if not parts:
        raise ValueError("empty path")
    obj = root
    for i, key in enumerate(parts):
        last = i == len(parts) - 1
        if is_dataclass(obj):
            hints = typing.get_type_hints(type(obj))
            if key not in hints or key.startswith("_"):
                raise ValueError(f"unknown setting {'.'.join(parts[:i + 1])}")
            if last:
                try:
                    setattr(obj, key, _coerce(hints[key], value))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"bad value for {path}: {exc}") from exc
                return
            obj = getattr(obj, key)
        elif isinstance(obj, dict):
            if key not in obj:
                raise ValueError(f"unknown key {'.'.join(parts[:i + 1])}")
            if last:
                cur = obj[key]
                if is_dataclass(cur):
                    obj[key] = from_dict(type(cur), value)
                elif isinstance(cur, bool) or isinstance(value, bool):
                    if not isinstance(value, bool) or not isinstance(cur, bool):
                        raise ValueError(f"bad value for {path}")
                    obj[key] = value
                elif isinstance(cur, (int, float)) and isinstance(value, (int, float)):
                    obj[key] = type(cur)(value)
                elif isinstance(cur, str) and isinstance(value, str):
                    obj[key] = value
                else:
                    raise ValueError(f"bad value for {path}")
                return
            obj = obj[key]
        else:
            raise ValueError(f"cannot descend into {'.'.join(parts[:i])}")
