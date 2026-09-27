from __future__ import annotations

from PyQt6.QtWidgets import QButtonGroup, QGridLayout, QPushButton

from gazer.config import POINTER_MODES
from gazer.ui.widgets import Card, SliderRow, muted, page, toggle

MODE_DETAIL = {
    "hybrid": "Recommended. Look somewhere far away and the cursor jumps there; small head "
              "movements then place it exactly. Needs gaze calibration.",
    "gaze": "Cursor follows your eyes directly. Fast but only as precise as webcam gaze "
            "(~2–4% of the screen). Pair with zoom-click for small targets.",
    "head_mouse": "Move your head like you'd move a mouse — faster movements go further. "
                  "Very precise; no calibration needed.",
    "head_joystick": "Lean your head away from centre to glide the cursor; return to centre to stop. "
                     "Good for limited range of motion.",
    "head_absolute": "Your nose points at the screen: each head pose maps to one screen position. "
                     "Run head range calibration for best results.",
}


class PointerPage:
    def __init__(self, ctl):
        self.ctl = ctl
        s = ctl.profile.settings.pointer
        self.widget, lay = page("Pointer", "How the cursor is driven, and how it feels.")

        modes = Card("Mode")
        grid = QGridLayout()
        self.group = QButtonGroup(self.widget)
        for i, (key, label) in enumerate(POINTER_MODES.items()):
            title, _, _ = label.partition(" — ")
            b = QPushButton(f"{title}\n{MODE_DETAIL[key]}")
            b.setObjectName("Choice")
            b.setCheckable(True)
            b.setChecked(s.mode == key)
            b.setMinimumHeight(74)
            b.setStyleSheet("QPushButton { white-space: normal; }")
            b.clicked.connect(lambda _=False, k=key: ctl.set_mode(k))
            self.group.addButton(b)
            grid.addWidget(b, i // 2, i % 2)
        modes.add(layout=grid)
        lay.addWidget(modes)

        def slider(card, label, attr, lo, hi, step, fmt="{:.2f}", tip=""):
            card.add(SliderRow(label, lo, hi, getattr(s, attr), step, fmt, tip,
                               lambda v, a=attr: self._set(a, v)))

        c = Card("Gaze")
        slider(c, "Smoothing", "gaze_smoothing", 0.0, 1.0, 0.05, "{:.2f}",
               "Higher = steadier cursor while you fixate, slightly slower to follow.")
        lay.addWidget(c)

        c = Card("Head mouse & Hybrid fine control")
        slider(c, "Speed", "head_gain", 0.2, 3.0, 0.05)
        slider(c, "Acceleration", "head_accel", 1.0, 2.5, 0.05, "{:.2f}",
               "1 = linear. Higher = slow moves are extra precise, fast moves travel far.")
        slider(c, "Jitter filter", "head_deadzone", 0.0, 0.005, 0.0002, "{:.4f}",
               "Ignore head movements smaller than this per frame.")
        slider(c, "Hybrid fine gain", "hybrid_fine_gain", 0.1, 1.5, 0.05)
        slider(c, "Hybrid jump distance", "warp_threshold", 0.05, 0.35, 0.01, "{:.2f}",
               "How far (fraction of screen width) you must look before the cursor jumps.")
        slider(c, "Hybrid jump delay (ms)", "warp_delay_ms", 40, 500, 10, "{:.0f}")
        lay.addWidget(c)

        c = Card("Joystick & Head pointer")
        slider(c, "Joystick speed", "joystick_speed", 0.2, 3.0, 0.05)
        slider(c, "Joystick centre zone", "joystick_deadzone", 0.0, 0.1, 0.005, "{:.3f}")
        slider(c, "Head pointer gain", "absolute_gain", 0.3, 3.0, 0.05)
        lay.addWidget(c)

        c = Card("Feel")
        slider(c, "Cursor glide (ms)", "output_smoothing_ms", 0, 150, 5, "{:.0f}",
               "The cursor glides toward its target at 125 Hz. 0 = instant.")
        slider(c, "Precision mode factor", "precision_factor", 0.1, 0.8, 0.05, "{:.2f}",
               "Speed multiplier while Precision mode is on.")
        c.add(toggle("Pause when I use the real mouse", s.manual_override,
                     lambda v: self._set("manual_override", v)))
        c.add(toggle("Learn from my clicks (gets more accurate with use)", s.adaptive_learning,
                     lambda v: self._set("adaptive_learning", v)))
        c.add(toggle("Invert horizontal", s.invert_x, lambda v: self._set("invert_x", v)))
        c.add(toggle("Invert vertical", s.invert_y, lambda v: self._set("invert_y", v)))
        c.add(muted("Recenter anytime: Ctrl+Alt+R, the action wheel, or say “recenter”."))
        lay.addWidget(c)
        lay.addStretch(1)

    def _set(self, attr, v) -> None:
        cur = getattr(self.ctl.profile.settings.pointer, attr)
        setattr(self.ctl.profile.settings.pointer, attr, type(cur)(v) if not isinstance(cur, bool) else bool(v))
        self.ctl.settings_changed()

    def refresh(self) -> None:
        mode = self.ctl.profile.settings.pointer.mode
        for b, key in zip(self.group.buttons(), POINTER_MODES):
            b.setChecked(key == mode)
