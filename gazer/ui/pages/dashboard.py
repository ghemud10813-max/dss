from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gazer.config import GESTURE_CATALOG
from gazer.core.engine import Snapshot
from gazer.ui import theme
from gazer.ui.widgets import CameraPreview, Card, Meter, Segmented, muted, page, toggle

MODE_SHORT = {"hybrid": "Hybrid", "gaze": "Gaze", "head_mouse": "Head", "head_joystick": "Joystick",
              "head_absolute": "Pointer"}


def _stat(title: str) -> tuple[QWidget, QLabel]:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    val = QLabel("—")
    val.setObjectName("Big")
    v.addWidget(val)
    v.addWidget(muted(title))
    return w, val


class DashboardPage:
    def __init__(self, ctl):
        self.ctl = ctl
        self._btn_state = None
        self.widget, lay = page("Dashboard", "Live view of what Gazer sees and how it's controlling your PC.")
        top = QHBoxLayout()
        top.setSpacing(14)
        lay.addLayout(top)

        left = QVBoxLayout()
        cam_card = Card("Camera")
        self.preview = CameraPreview()
        self.preview.setMinimumHeight(330)
        cam_card.add(self.preview)
        self.cam_info = muted("")
        cam_card.add(self.cam_info)
        left.addWidget(cam_card)
        stats = Card("Tracking")
        grid = QGridLayout()
        self.stat_widgets = {}
        for i, (key, title) in enumerate([("fps", "tracking fps"), ("lat", "latency ms"),
                                          ("quality", "face quality"), ("head", "head yaw / pitch")]):
            w, val = _stat(title)
            self.stat_widgets[key] = val
            grid.addWidget(w, 0, i)
        stats.add(layout=grid)
        left.addWidget(stats)
        top.addLayout(left, 3)

        right = QVBoxLayout()
        ctrl = Card("Control")
        row = QHBoxLayout()
        self.start_btn = QPushButton("Start control")
        self.start_btn.setObjectName("Primary")
        self.start_btn.setMinimumHeight(42)
        self.start_btn.clicked.connect(lambda: ctl.set_control(not ctl.control_on))
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setMinimumHeight(42)
        self.pause_btn.clicked.connect(ctl.toggle_pause)
        row.addWidget(self.start_btn, 2)
        row.addWidget(self.pause_btn, 1)
        ctrl.add(layout=row)
        self.mode_seg = Segmented(MODE_SHORT, ctl.profile.settings.pointer.mode)
        self.mode_seg.changed.connect(ctl.set_mode)
        ctrl.add(self.mode_seg)
        self.mode_desc = muted("")
        ctrl.add(self.mode_desc)
        self.gaze_status = QLabel("")
        self.gaze_status.setWordWrap(True)
        ctrl.add(self.gaze_status)
        cal_row = QHBoxLayout()
        b = QPushButton("Calibrate gaze")
        b.clicked.connect(lambda: ctl.start_calibration("gaze", "standard"))
        cal_row.addWidget(b)
        b2 = QPushButton("Recenter")
        b2.setToolTip("Resets the head neutral pose and fixes gaze drift (you look at the cursor).")
        b2.clicked.connect(lambda: ctl.perform("recenter"))
        cal_row.addWidget(b2)
        b3 = QPushButton("Keyboard")
        b3.clicked.connect(ctl.toggle_keyboard)
        cal_row.addWidget(b3)
        ctrl.add(layout=cal_row)
        right.addWidget(ctrl)

        quick = Card("Quick toggles")
        s = ctl.profile.settings
        self.dwell_cb = toggle("Dwell click (hold still to click)", s.dwell.enabled,
                               lambda v: self._set(lambda: setattr(s.dwell, "enabled", v)))
        quick.add(self.dwell_cb)
        quick.add(toggle("Voice commands", s.voice.enabled, ctl.set_voice))
        quick.add(toggle("Show gaze dot", s.overlay.show_gaze_dot,
                         lambda v: self._set(lambda: setattr(s.overlay, "show_gaze_dot", v))))
        quick.add(toggle("Learn from my clicks", s.pointer.adaptive_learning,
                         lambda v: self._set(lambda: setattr(s.pointer, "adaptive_learning", v))))
        right.addWidget(quick)

        ges = Card("Live gestures", "Bound gestures light up green when triggered.")
        self.meters: dict[str, Meter] = {}
        g = QGridLayout()
        bound = []
        for b in s.gestures.bindings:
            if b.action != "none" and b.gesture not in bound:
                bound.append(b.gesture)
        for i, name in enumerate(bound):
            lbl = QLabel(GESTURE_CATALOG.get(name, (name,))[0])
            m = Meter(s.gestures.gestures[name].threshold if name in s.gestures.gestures else 0.5)
            self.meters[name] = m
            g.addWidget(lbl, i, 0)
            g.addWidget(m, i, 1)
        ges.add(layout=g)
        right.addWidget(ges)

        sess = Card("This profile")
        self.session_lbl = QLabel("")
        self.session_lbl.setWordWrap(True)
        sess.add(self.session_lbl)
        right.addWidget(sess)
        right.addStretch(1)
        top.addLayout(right, 2)
        lay.addStretch(1)
        self.refresh()

    def _set(self, fn) -> None:
        fn()
        self.ctl.settings_changed()

    def refresh(self) -> None:
        s = self.ctl.profile.settings
        self.mode_seg.set_current(s.pointer.mode)
        from gazer.config import POINTER_MODES

        self.mode_desc.setText(POINTER_MODES.get(s.pointer.mode, ""))
        self.dwell_cb.blockSignals(True)
        self.dwell_cb.setChecked(s.dwell.enabled)
        self.dwell_cb.blockSignals(False)
        prof = self.ctl.profile
        g = prof.gaze
        if g.ready:
            last = prof.history[-1] if prof.history else None
            acc = f" · last accuracy {last.mean_px:.0f} px ({last.grade})" if last else ""
            self.gaze_status.setText(f"<span style='color:{theme.OK}'>Gaze model ready</span>"
                                     f" — {g.n_samples} samples{acc}")
        else:
            self.gaze_status.setText(f"<span style='color:{theme.WARN}'>Gaze not calibrated.</span> "
                                     "Head modes work right away; calibrate to use Hybrid/Gaze.")
        st = prof.stats
        self.session_lbl.setText(
            f"{st.clicks} clicks · {st.gestures} gestures · {st.keys_typed} keys typed<br>"
            f"{st.seconds_active / 60:.0f} min of control · {st.sessions} sessions<br>"
            f"<span style='color:{theme.ACCENT}'>{len(g.implicit)} samples learned from your clicks</span>")

    def on_snapshot(self, snap: Snapshot) -> None:
        self.preview.set_frame(snap.preview, snap.points, snap.face, snap.head)
        self.stat_widgets["fps"].setText(f"{snap.fps:.0f}")
        self.stat_widgets["lat"].setText(f"{snap.latency_ms:.0f}")
        self.stat_widgets["quality"].setText(f"{snap.quality * 100:.0f}%" if snap.face else "—")
        self.stat_widgets["head"].setText(f"{snap.head[0]:+.2f} / {snap.head[1]:.2f}" if snap.face else "—")
        for name, m in self.meters.items():
            m.set(snap.gesture_values.get(name, 0.0), name in snap.gesture_active)
        state = (snap.control, snap.paused)
        if state != self._btn_state:
            self._btn_state = state
            self.start_btn.setText("Stop control" if snap.control else "Start control")
            self.start_btn.setObjectName("Danger" if snap.control else "Primary")
            self.start_btn.style().unpolish(self.start_btn)
            self.start_btn.style().polish(self.start_btn)
            self.pause_btn.setText("Resume" if snap.paused else "Pause")
            self.pause_btn.setEnabled(snap.control)
        btn = self.mode_seg.buttons.get(snap.mode)
        if btn is not None and not btn.isChecked():
            self.refresh()
        cam = self.ctl.camera_status
        self.cam_info.setText(cam)
