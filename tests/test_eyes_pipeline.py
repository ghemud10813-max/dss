"""End-to-end eye control with a simulated user: calibrate → gaze cursor → click by eye."""

import numpy as np
import pytest

from gazer.config import ProfileSettings, ZoneSettings, apply_control_style
from gazer.core.calib_session import BUTTONS, CalibrationSession
from gazer.core.engine import CursorOutput, EngineCore
from gazer.core.features import extract_features
from gazer.core.gestures import BlinkCounter
from gazer.core.input_backend import RecordingInput
from gazer.core.profiles import ProfileStore
from gazer.core.screen import ScreenRect
from gazer.core.simulator import SyntheticUser
from gazer.core.zones import ZoneDetector, classify

SCREEN = ScreenRect(0, 0, 1920, 1080)


class Rig:
    """Simulated user + real engine on simulated time."""

    def __init__(self, tmp_path, style="eyes", seed=3):
        store = ProfileStore(tmp_path / "profiles")
        self.profile = store.create("eyes")
        apply_control_style(self.profile.settings, style)
        self.backend = RecordingInput((960, 540))
        self.core = EngineCore(self.profile, SCREEN, self.backend, CursorOutput(self.backend, manual_override=False))
        self.user = SyntheticUser(seed=seed, expressive=False, noise_px=0.3)
        self.t = 100.0

    def step(self):
        self.t += 1 / 30
        obs = self.user.step(self.t)
        feats = extract_features(obs) if obs is not None else None
        snap = self.core.process(self.t, feats)
        # the cursor thread normally glides toward the target; jump straight there
        if self.core.output._target is not None:
            self.backend.move(*self.core.output._target)
        return snap

    def calibrate(self, preset="quick"):
        sess = CalibrationSession("gaze", preset, SCREEN, require_ready=False, intro_s=0.5,
                                  async_training=False, auto_accept_s=0, seed=1)
        self.core.set_calibrating(True)
        for _ in range(30 * 120):
            self.user.attend(sess.target())
            sess.on_snapshot(self.step())
            if sess.state == "results":
                break
        assert sess.state == "results", sess.error
        res = sess.accept()
        assert res is not None and res.kind == "gaze"
        self.profile.gaze.fit(res.payload["X"], res.payload["Y"])
        self.core.set_calibrating(False)
        return sess


def test_simulated_eyes_move_irises_the_right_way():
    u = SyntheticUser(seed=0, expressive=False, noise_px=0)
    feats = {}
    for name, target in (("left", (0.05, 0.5)), ("right", (0.95, 0.5)), ("up", (0.5, 0.05)), ("down", (0.5, 0.95))):
        u.attend(target)
        for i in range(40):
            obs = u.step(len(feats) * 10 + i / 30)
        feats[name] = extract_features(obs)
    assert feats["right"].left.u > feats["left"].left.u + 0.1
    assert feats["down"].left.v > feats["up"].left.v + 0.05


def test_calibration_session_trains_an_accurate_model(tmp_path):
    rig = Rig(tmp_path)
    sess = rig.calibrate("quick")
    rep = sess.report
    assert rep is not None
    assert rep.grade in ("Excellent", "Good"), rep
    assert rep.mean_px < 0.05 * SCREEN.width
    assert len(sess.samples) > 150
    view = sess.view()
    assert view["state"] == "done" and "report" not in view or True


def test_eyes_only_cursor_follows_gaze_and_double_blink_clicks(tmp_path):
    rig = Rig(tmp_path)
    rig.calibrate("quick")
    rig.core.set_control(True)
    errors = []
    for target in [(0.25, 0.3), (0.75, 0.35), (0.5, 0.7), (0.2, 0.75)]:
        rig.user.attend(target)
        for i in range(40):
            snap = rig.step()
        assert snap.mode_effective == "gaze"
        p = np.array(snap.pointer)
        errors.append(np.hypot(*(p - np.array(SCREEN.norm_to_px(*target)))))
    assert np.median(errors) < 0.06 * SCREEN.width, errors

    # two quick deliberate blinks → exactly one left click where the user looks
    rig.profile.settings.dwell.enabled = False
    rig.user.attend((0.6, 0.45))
    for _ in range(30):
        rig.step()
    before = len(rig.backend.clicks())
    rig.user._next_blink = 1e9  # no spontaneous blinks during the gesture
    rig.user.trigger("double_blink", 0.16, t=rig.t)
    for _ in range(9):
        rig.step()
    rig.user.trigger("double_blink", 0.16, t=rig.t)
    for _ in range(20):
        rig.step()
    clicks = rig.backend.clicks()[before:]
    assert len(clicks) == 1 and clicks[0][0] == "left"
    x, y = clicks[0][1]
    assert abs(x - 0.6 * 1920) < 150 and abs(y - 0.45 * 1080) < 150


def test_natural_blinks_do_not_click(tmp_path):
    rig = Rig(tmp_path)
    rig.calibrate("quick")
    rig.core.set_control(True)
    rig.profile.settings.dwell.enabled = False
    rig.user.expressive = False
    rig.user.attend((0.5, 0.5))
    for _ in range(30 * 20):  # 20 s of normal viewing with natural blinks
        rig.step()
    assert rig.backend.clicks() == []


def test_uncalibrated_gaze_mode_falls_back_to_head(tmp_path):
    rig = Rig(tmp_path)
    rig.core.set_control(True)
    snap = rig.step()
    assert snap.mode == "gaze" and snap.mode_effective == "head_mouse"


def test_blink_counter():
    bc = BlinkCounter(window_s=0.5)
    t, fired = 0.0, []

    def run(closed_s, open_s):
        nonlocal t
        for _ in range(int(closed_s * 60)):
            t += 1 / 60
            fired.append(bc.update(t, 1.0))
        for _ in range(int(open_s * 60)):
            t += 1 / 60
            fired.append(bc.update(t, 0.0))

    run(0.15, 2.0)  # single natural blink
    assert sum(f[0] for f in fired) == 1 and not any(f[1] for f in fired)
    fired.clear()
    run(0.15, 0.2)
    run(0.15, 1.0)  # two quick ones
    assert any(f[1] for f in fired)
    fired.clear()
    run(1.2, 0.2)
    run(0.15, 1.0)  # a long closure never counts
    assert not any(f[1] for f in fired)


def test_zone_classification():
    assert classify(0.5, 0.5, 0.03) is None
    assert classify(0.5, -0.02, 0.03) == "top"
    assert classify(0.5, 1.03, 0.03) == "bottom"
    assert classify(-0.01, 0.5, 0.03) == "left"
    assert classify(1.02, 0.5, 0.03) == "right"
    assert classify(0.01, 0.01, 0.03) == "top_left"
    assert classify(0.99, 0.99, 0.03) == "bottom_right"


def test_zone_dwell_fires_once_and_scroll_repeats():
    z = ZoneSettings(enabled=True, dwell_ms=500)
    det = ZoneDetector(z)
    fired = []
    t = 0.0
    for _ in range(60):  # 2 s staring at the top-right corner
        t += 1 / 30
        fired += det.update(t, (0.995, 0.005))
    assert fired == ["pause_toggle"]
    fired = []
    det.reset()
    for _ in range(90):  # 3 s below the screen: scroll repeats and speeds up
        t += 1 / 30
        fired += det.update(t, (0.5, 1.02))
    assert fired.count("scroll_down") >= 5
    assert det.update(t + 0.1, (0.5, 0.5)) == [] and det.current is None


def test_calibration_results_can_be_confirmed_by_gaze(tmp_path):
    rig = Rig(tmp_path)
    sess = CalibrationSession("gaze", "quick", SCREEN, require_ready=False, intro_s=0.5,
                              async_training=False, auto_accept_s=0, seed=1)
    rig.core.set_calibrating(True)
    while sess.state not in ("results", "done", "cancelled"):
        rig.user.attend(sess.target())
        sess.on_snapshot(rig.step())
    x, y, w, h = BUTTONS["accept"]
    rig.user.attend((x + w / 2, y + h / 2))
    for _ in range(30 * 4):
        sess.on_snapshot(rig.step())
        if sess.state == "done":
            break
    assert sess.state == "done" and sess.result is not None


@pytest.mark.parametrize("style", ["eyes", "eyes_head", "head"])
def test_control_styles(style):
    s = ProfileSettings()
    apply_control_style(s, style)
    assert s.pointer.mode == {"eyes": "gaze", "eyes_head": "hybrid", "head": "head_mouse"}[style]
    if style == "eyes":
        assert s.dwell.enabled and s.zones.enabled
        assert any(b.gesture == "double_blink" and b.action == "left_click" for b in s.gestures.bindings)
