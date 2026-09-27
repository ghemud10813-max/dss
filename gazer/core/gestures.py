"""Facial gesture detection and gesture → action bindings.

Pipeline per frame:
  blendshapes + eye geometry → raw values (0..1)
  → neutral-face subtraction → hysteresis + minimum hold → start/end events
  → bindings (start / hold / tap / while_held) → actions

Natural blinks last ~100–300 ms, so blink-based gestures require one eye to
stay open (winks) or a long closure (long_blink) — no more accidental clicks
every time you blink, which plagued v1.
"""

from __future__ import annotations

from dataclasses import dataclass

from gazer.config import GESTURE_CATALOG, Binding, GestureSettings
from gazer.core.features import FaceFeatures
from gazer.core.filters import RollingPercentile

NO_NEUTRAL = {"wink_left", "wink_right", "long_blink", "tilt_left", "tilt_right"}
_PAIRS = [
    ("eyeBlinkLeft", "eyeBlinkRight"), ("mouthLeft", "mouthRight"),
    ("mouthSmileLeft", "mouthSmileRight"), ("browDownLeft", "browDownRight"),
    ("browOuterUpLeft", "browOuterUpRight"),
]


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


class EyeClosure:
    """Per-eye closure 0 (open) … 1 (closed) for the user's left/right eye.

    Combines the eye-aspect-ratio (relative to an adaptive per-user open
    baseline) with MediaPipe's eyeBlink blendshapes. Whether MediaPipe's
    "Left" means the user's left in our mirrored image is learned on the fly
    from moments where the geometry clearly shows one eye closed.
    """

    def __init__(self):
        self._open = [RollingPercentile(900, 85, default=0.28), RollingPercentile(900, 85, default=0.28)]
        self.votes_same = 0
        self.votes_swap = 0

    @property
    def swapped(self) -> bool | None:
        if self.votes_same + self.votes_swap < 6:
            return None
        return self.votes_swap > self.votes_same

    def update(self, f: FaceFeatures) -> tuple[float, float]:
        ce = []
        for i, eye in enumerate((f.left, f.right)):
            ref = max(self._open[i].push(eye.ear), 0.12)
            ce.append(_clamp01((ref - eye.ear) / (ref * 0.5)))
        bl, br = f.blend.get("eyeBlinkLeft"), f.blend.get("eyeBlinkRight")
        if bl is None or br is None:
            return ce[0], ce[1]
        if abs(ce[0] - ce[1]) > 0.45 and abs(bl - br) > 0.15:
            geo_left_closed = ce[0] > ce[1]
            if (bl > br) == geo_left_closed:
                self.votes_same += 1
            else:
                self.votes_swap += 1
        sw = self.swapped
        if sw is None:
            return ce[0], ce[1]
        bL, bR = (br, bl) if sw else (bl, br)
        return (0.5 * ce[0] + 0.5 * _clamp01(bL / 0.7), 0.5 * ce[1] + 0.5 * _clamp01(bR / 0.7))


def raw_gesture_values(f: FaceFeatures, closure: tuple[float, float], swapped: bool | None,
                       swap_setting: bool = False) -> dict[str, float]:
    b = dict(f.blend)
    if bool(swapped) != swap_setting:
        for l, r in _PAIRS:
            b[l], b[r] = b.get(r, 0.0), b.get(l, 0.0)
    g = b.get
    cl, cr = closure
    open_max = 0.35
    return {
        "smile": (g("mouthSmileLeft", 0) + g("mouthSmileRight", 0)) / 2,
        "mouth_open": g("jawOpen", 0),
        "pucker": g("mouthPucker", 0),
        "brow_raise": max(g("browInnerUp", 0), (g("browOuterUpLeft", 0) + g("browOuterUpRight", 0)) / 2),
        "brow_down": (g("browDownLeft", 0) + g("browDownRight", 0)) / 2,
        "mouth_left": g("mouthLeft", 0),
        "mouth_right": g("mouthRight", 0),
        "cheek_puff": g("cheekPuff", 0),
        "lips_in": (g("mouthRollLower", 0) + g("mouthRollUpper", 0)) / 2,
        "wink_left": cl if cr < open_max else 0.0,
        "wink_right": cr if cl < open_max else 0.0,
        "long_blink": min(cl, cr),
        "tilt_left": _clamp01((-f.roll - 8.0) / 12.0),
        "tilt_right": _clamp01((f.roll - 8.0) / 12.0),
    }


@dataclass
class GestureEvent:
    name: str
    kind: str  # "start" | "end"
    t: float
    duration: float = 0.0


class GestureDetector:
    def __init__(self, settings: GestureSettings):
        self.s = settings
        self.values: dict[str, float] = {}
        self.active: dict[str, float] = {}  # name → start time
        self._candidate: dict[str, float] = {}
        self._ended: dict[str, float] = {}

    def reset(self) -> None:
        self.active.clear()
        self._candidate.clear()

    def normalize(self, raw: dict[str, float]) -> dict[str, float]:
        out = {}
        for name, v in raw.items():
            n = 0.0 if name in NO_NEUTRAL else self.s.neutral.get(name, 0.0)
            out[name] = _clamp01((v - n) / max(1.0 - n, 1e-3))
        return out

    def update(self, t: float, raw: dict[str, float]) -> list[GestureEvent]:
        self.values = self.normalize(raw)
        events: list[GestureEvent] = []
        for name, v in self.values.items():
            cfg = self.s.gestures.get(name)
            if cfg is None or not cfg.enabled:
                if name in self.active:
                    events.append(GestureEvent(name, "end", t, t - self.active.pop(name)))
                self._candidate.pop(name, None)
                continue
            on = cfg.threshold
            off = on * 0.75
            if name in self.active:
                if v < off:
                    start = self.active.pop(name)
                    self._ended[name] = t
                    events.append(GestureEvent(name, "end", t, t - start))
                continue
            if v >= on:
                if t - self._ended.get(name, -1e9) < self.s.refractory_ms / 1000:
                    continue
                since = self._candidate.setdefault(name, t)
                if (t - since) * 1000 >= cfg.min_hold_ms:
                    self._candidate.pop(name, None)
                    self.active[name] = since
                    events.append(GestureEvent(name, "start", t, t - since))
            elif v < off:
                self._candidate.pop(name, None)
        return events

    def release_all(self, t: float) -> list[GestureEvent]:
        """Face lost: end everything that is active."""
        events = [GestureEvent(n, "end", t, t - s) for n, s in self.active.items()]
        self.active.clear()
        self._candidate.clear()
        return events


@dataclass
class ActionRequest:
    action: str
    phase: str  # "fire" | "down" | "up"
    gesture: str


class BindingResolver:
    def __init__(self, bindings: list[Binding]):
        self.bindings = bindings
        self._hold_fired: set[tuple[int, float]] = set()
        self._starts: dict[str, float] = {}

    def process(self, t: float, events: list[GestureEvent]) -> list[ActionRequest]:
        out: list[ActionRequest] = []
        for ev in events:
            if ev.kind == "start":
                self._starts[ev.name] = ev.t - ev.duration
            for i, b in enumerate(self.bindings):
                if b.gesture != ev.name or b.action == "none":
                    continue
                if ev.kind == "start":
                    if b.trigger == "start":
                        out.append(ActionRequest(b.action, "fire", ev.name))
                    elif b.trigger == "while_held":
                        out.append(ActionRequest(b.action, "down", ev.name))
                else:
                    if b.trigger == "while_held":
                        out.append(ActionRequest(b.action, "up", ev.name))
                    elif b.trigger == "tap" and ev.duration * 1000 < b.hold_ms:
                        out.append(ActionRequest(b.action, "fire", ev.name))
            if ev.kind == "end":
                start = self._starts.pop(ev.name, None)
                self._hold_fired = {k for k in self._hold_fired if k[1] != start}
        for i, b in enumerate(self.bindings):
            if b.trigger != "hold" or b.action == "none":
                continue
            start = self._starts.get(b.gesture)
            if start is None or (i, start) in self._hold_fired:
                continue
            if (t - start) * 1000 >= b.hold_ms:
                self._hold_fired.add((i, start))
                out.append(ActionRequest(b.action, "fire", b.gesture))
        return out


def gesture_label(name: str) -> str:
    return GESTURE_CATALOG.get(name, (name,))[0]
