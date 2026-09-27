"""Gestures, dwell, wheel, zoom, calibration, pointer modes."""

import numpy as np

from gazer.config import Binding, GestureSettings, HeadCalibration, PointerSettings, ScrollSettings
from gazer.core.calibration import accept_sample, build_plan, evaluate
from gazer.core.gestures import BindingResolver, EyeClosure, GestureDetector, raw_gesture_values
from gazer.core.head import fit_head_calibration
from gazer.core.interaction import ActionWheel, DwellDetector, Rect, ZoomLens
from gazer.core.pointer import PointerEngine
from gazer.core.screen import ScreenRect

SCREEN = ScreenRect(0, 0, 1920, 1080)


def run_detector(det, seq, dt=1 / 30, name="smile"):
    events = []
    for i, v in enumerate(seq):
        events += det.update(i * dt, {name: v})
    return events


def test_gesture_needs_min_hold_and_has_hysteresis():
    det = GestureDetector(GestureSettings())
    # 2 frames above threshold (<150 ms) → nothing
    assert run_detector(det, [0, 0.9, 0.9, 0, 0, 0]) == []
    det = GestureDetector(GestureSettings())
    ev = run_detector(det, [0] + [0.9] * 8 + [0.5] * 3 + [0.1] * 3)
    kinds = [e.kind for e in ev]
    assert kinds == ["start", "end"]  # 0.5 is above off-threshold, no flicker


def test_neutral_baseline_is_subtracted():
    s = GestureSettings()
    s.neutral["smile"] = 0.4
    det = GestureDetector(s)
    assert run_detector(det, [0.6] * 10) == []  # (0.6-0.4)/0.6 = 0.33 < 0.55


def test_binding_triggers():
    b = [Binding("smile", "start", "left_click"), Binding("smile", "hold", "right_click", 500),
         Binding("pucker", "while_held", "drag_hold"), Binding("brow_raise", "tap", "zoom", 400)]
    det = GestureDetector(GestureSettings())
    res = BindingResolver(b)
    out = []
    for i in range(40):
        t = i / 30
        vals = {"smile": 0.9 if i < 30 else 0.0, "pucker": 0.9 if 5 <= i < 20 else 0.0,
                "brow_raise": 0.9 if 30 <= i < 38 else 0.0}
        out += [(r.action, r.phase) for r in res.process(t, det.update(t, vals))]
    assert ("left_click", "fire") in out
    assert out.count(("right_click", "fire")) == 1
    assert ("drag_hold", "down") in out and ("drag_hold", "up") in out
    assert ("zoom", "fire") in out


def test_wink_vs_natural_blink(feats_factory):
    ec = EyeClosure()
    for _ in range(100):
        ec.update(feats_factory(ear=(0.3, 0.3)))
    cl, cr = ec.update(feats_factory(ear=(0.08, 0.3)))
    assert cl > 0.8 and cr < 0.2
    vals = raw_gesture_values(feats_factory(), (cl, cr), None)
    assert vals["wink_left"] > 0.8 and vals["wink_right"] == 0
    both = raw_gesture_values(feats_factory(), (0.9, 0.9), None)
    assert both["wink_left"] == 0 and both["long_blink"] == 0.9
    det = GestureDetector(GestureSettings())
    # a 250 ms natural blink must not trigger the 700 ms long_blink
    assert run_detector(det, [0.9] * 8 + [0] * 5, name="long_blink") == []


def test_blendshape_side_mapping_is_learned(feats_factory):
    ec = EyeClosure()
    for _ in range(100):
        ec.update(feats_factory(ear=(0.3, 0.3)))
    for _ in range(8):  # geometric LEFT closed but MediaPipe says "Right"
        ec.update(feats_factory(ear=(0.05, 0.3), blend={"eyeBlinkLeft": 0.0, "eyeBlinkRight": 0.8}))
    assert ec.swapped is True


def test_dwell():
    d = DwellDetector(radius_px=30, dwell_s=0.5, cooldown_s=0.3)
    fired = [d.update(i / 30, (100 + (i % 3), 100)) for i in range(30)]
    assert fired.count(True) == 1  # fires once, then must move away
    d.update(1.2, (400, 400))
    fired = [d.update(1.2 + i / 30, (400, 400)) for i in range(1, 30)]
    assert fired.count(True) == 1


def test_wheel_sectors_and_selection():
    w = ActionWheel((500, 500), ["a", "b", "c", "d"], 100, select_dwell_s=0.3)
    assert w.sector_at((500, 400)) == 0  # top
    assert w.sector_at((600, 500)) == 1  # right
    assert w.sector_at((500, 600)) == 2  # bottom
    assert w.sector_at((400, 500)) == 3  # left
    assert w.sector_at((505, 505)) is None  # centre = cancel
    res = [w.update(i / 30, (600, 500)) for i in range(15)]
    assert ("select", 1) in res


def test_zoom_lens_maps_back_precisely():
    z = ZoomLens((960, 540), 3.0, 200, 120, Rect(0, 0, 1920, 1080))
    assert np.allclose(z.to_real(z.pointer), (960, 540))
    moved = z.pointer + np.array([30, 0])
    assert np.allclose(z.to_real(moved), (970, 540))  # 30 px on lens = 10 px real
    edge = ZoomLens((5, 5), 3.0, 200, 120, Rect(0, 0, 1920, 1080))
    assert edge.lens.x >= 0 and edge.lens.y >= 0
    assert np.allclose(edge.to_real(edge.pointer), (5, 5))


def test_calibration_plan_and_acceptance(feats_factory):
    plan = build_plan("standard", seed=1)
    assert len(plan.points) == 25 and len(plan.validation) == 6
    pt = plan.points[0]
    f = feats_factory()
    assert not accept_sample(plan, pt, 0.2, f)  # eyes still moving
    assert accept_sample(plan, pt, 0.8, f)
    assert not accept_sample(plan, pt, 0.8, f, closure=(0.9, 0.9))  # blinking
    assert not accept_sample(plan, pt, 0.8, feats_factory(quality=0.1))
    rep = evaluate(np.array([[0.51, 0.5], [0.49, 0.5]]), np.array([[0.5, 0.5], [0.5, 0.5]]), [0, 0], 1920, 1080)
    assert rep.mean_px < 25 and rep.grade == "Excellent"


def test_head_calibration_fit():
    samples = {"center": [np.array([0.0, 0.0])] * 6, "left": [np.array([-0.1, 0.0])] * 6,
               "right": [np.array([0.1, 0.0])] * 6, "top": [np.array([0.0, -0.08])] * 6,
               "bottom": [np.array([0.0, 0.08])] * 6}
    cal = fit_head_calibration(samples, 1920, 1080)
    assert cal.calibrated and cal.gain_x > 0 and cal.gain_y > 0
    assert fit_head_calibration({"center": [np.zeros(2)] * 6}, 1920, 1080) is None


def _pe(mode):
    s = PointerSettings(mode=mode)
    return PointerEngine(s, ScrollSettings(), HeadCalibration(), SCREEN), s


def test_head_mouse_moves_in_head_direction(feats_factory):
    pe, _ = _pe("head_mouse")
    start = pe.P.copy()
    for i in range(20):
        pe.update(i / 30, feats_factory(head_point=(i * 0.01, 0.0)), None)
    assert pe.P[0] > start[0] + 50 and abs(pe.P[1] - start[1]) < 5
    for i in range(20, 35):  # hold still so the filter settles
        pe.update(i / 30, feats_factory(head_point=(0.19, 0.0)), None)
    # tiny jitter under the deadzone does not move the cursor
    p = pe.P.copy()
    for i in range(35, 60):
        pe.update(i / 30, feats_factory(head_point=(0.19 + (i % 2) * 0.0005, 0.0)), None)
    assert np.hypot(*(pe.P - p)) < 3


def test_joystick_and_absolute(feats_factory):
    pe, _ = _pe("head_joystick")
    pe.update(0, feats_factory(head_point=(0, 0)), None)
    pe.recenter()
    for i in range(1, 30):
        pe.update(i / 30, feats_factory(head_point=(0, -0.1)), None)
    assert pe.P[1] < 540 - 50  # looking up glides up
    pe, _ = _pe("head_absolute")
    pe.update(0, feats_factory(head_point=(0, 0)), None)
    for i in range(1, 20):
        out = pe.update(i / 30, feats_factory(head_point=(0.1, 0)), None)
    assert out.pos[0] > 960 + 300


def test_hybrid_warps_to_gaze_then_head_fine_tunes(feats_factory):
    pe, s = _pe("hybrid")
    pe.sync((200, 200))
    warped = False
    for i in range(20):
        out = pe.update(i / 30, feats_factory(head_point=(0, 0)), (0.75, 0.6))
        warped |= out.warped
    assert warped
    assert np.hypot(pe.P[0] - 1440, pe.P[1] - 648) < 60
    before = pe.P.copy()
    for i in range(20, 30):
        pe.update(i / 30, feats_factory(head_point=((i - 20) * 0.003, 0)), (0.75, 0.6))
    assert pe.P[0] > before[0] + 5  # head nudged it, gaze didn't snap it back


def test_scroll_mode_emits_wheel(feats_factory):
    pe, _ = _pe("hybrid")
    pe.update(0, feats_factory(head_point=(0, 0)), None)
    pe.start_scroll()
    total = 0
    for i in range(1, 31):
        out = pe.update(i / 30, feats_factory(head_point=(0, 0.12)), None)
        total += out.scroll[0]
        assert out.pos is None
    assert total < -200  # head down → scroll down (negative wheel)
