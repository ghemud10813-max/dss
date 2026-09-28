"""v3.1: magnetic targets, Eye Dock click types, precise zoom-click, dwell rules."""

import threading

import numpy as np

from gazer.core.calib_session import CalibrationSession
from gazer.core.gestures import BindingResolver
from gazer.core.targets import StaticProvider, Target, TargetSnapper
from gazer.core.zones import ZoneDetector
from gazer.config import ZoneSettings

from test_eyes_pipeline import SCREEN, Rig

BUTTON = Target(700, 400, 90, 36, "button", "Save")  # centre (745, 418)


def calibrated_rig(tmp_path, magnetic=True, targets=(BUTTON,)):
    rig = Rig(tmp_path)
    rig.calibrate("quick")
    s = rig.profile.settings
    s.pointer.magnetic = magnetic
    s.pointer.magnetic_radius_px = 45
    rig.core.snapper = TargetSnapper(StaticProvider(list(targets)), 45, synchronous=True)
    rig.core.reload_settings()
    rig.core.set_control(True)
    return rig


def look_at(rig, px, py, frames=45):
    rig.user.attend(SCREEN.px_to_norm(px, py))
    snap = None
    for _ in range(frames):
        snap = rig.step()
    return snap


def test_magnetic_lock_snaps_to_button_and_clicks_its_centre(tmp_path):
    rig = calibrated_rig(tmp_path)
    rig.profile.settings.dwell.enabled = False
    snap = look_at(rig, 745 + 30, 418 + 12)  # gaze lands beside the button
    assert snap.target == BUTTON
    assert snap.pointer == (745.0, 418.0)
    rig.profile.settings.dwell.enabled = True
    for _ in range(45):
        rig.step()
    clicks = rig.backend.clicks()
    assert clicks and clicks[-1][1] == (745, 418)


def test_locked_clicks_teach_the_gaze_model_in_pure_gaze_mode(tmp_path):
    rig = calibrated_rig(tmp_path)
    rig.profile.settings.dwell.enabled = False
    before = len(rig.profile.gaze.implicit)
    look_at(rig, 760, 425)
    rig.core.perform("left_click")
    assert len(rig.profile.gaze.implicit) == before + 1
    assert rig.profile.gaze.live_error is not None and rig.profile.gaze.live_error < 0.05


def test_no_lock_far_from_targets_and_targets_only_dwell_ignores_text(tmp_path):
    rig = calibrated_rig(tmp_path)
    s = rig.profile.settings
    s.dwell.targets_only = True
    snap = look_at(rig, 300, 800, frames=90)  # "reading" plain text for 3 s
    assert snap.target is None and rig.backend.clicks() == []
    look_at(rig, 745, 418, frames=60)
    assert rig.backend.clicks(), "dwell on a real button still clicks"


def test_lock_releases_when_looking_away(tmp_path):
    rig = calibrated_rig(tmp_path)
    rig.profile.settings.dwell.enabled = False
    assert look_at(rig, 745, 418).target == BUTTON
    snap = look_at(rig, 1500, 200)
    assert snap.target is None
    assert abs(snap.pointer[0] - 1500) < 120


def test_dock_click_type_is_one_shot(tmp_path):
    rig = calibrated_rig(tmp_path, magnetic=False)
    rig.core.set_dwell_next("right_click")
    look_at(rig, 500, 500, frames=45)
    clicks = rig.backend.clicks()
    assert clicks and clicks[0][0] == "right"
    assert rig.core.dwell_next is None
    look_at(rig, 1300, 700, frames=45)
    assert rig.backend.clicks()[-1][0] == "left"


def test_precise_zoom_click_two_step(tmp_path):
    rig = calibrated_rig(tmp_path, magnetic=False)
    rig.core.set_dwell_next("zoom_click")
    look_at(rig, 900, 500, frames=45)
    assert rig.core.zoom is not None and rig.core.dwell_next == "zoom_click"
    assert rig.backend.clicks() == []
    for _ in range(60):  # hold still inside the lens → precise click
        rig.step()
    assert rig.core.zoom is None and rig.core.dwell_next is None
    (button, (x, y)), = rig.backend.clicks()
    assert button == "left" and abs(x - 900) < 60 and abs(y - 500) < 60


def test_dwell_exclusion_rect(tmp_path):
    rig = calibrated_rig(tmp_path, magnetic=False)
    rig.core.dwell_exclusions = [(1700, 0, 220, 1080)]  # e.g. the Eye Dock
    look_at(rig, 1800, 540, frames=60)
    assert rig.backend.clicks() == []


def test_zone_scroll_is_one_notch_per_repeat(tmp_path):
    rig = calibrated_rig(tmp_path, magnetic=False)
    rig.core.perform("scroll_down", "zone")
    rig.core.perform("scroll_down")
    scrolls = [e for e in rig.backend.events if e[0] == "scroll"]
    assert scrolls[0] == ("scroll", -120, 0) and scrolls[1] == ("scroll", -360, 0)


def test_zone_scroll_rate_is_readable():
    det = ZoneDetector(ZoneSettings(enabled=True, dwell_ms=500))
    fired, t = [], 0.0
    for _ in range(30 * 4):
        t += 1 / 30
        fired += det.update(t, (0.5, 1.02))
    per_s = fired.count("scroll_down") / 3.5
    assert 4 <= per_s <= 17


def test_engine_toggles_report_settings_changes(tmp_path):
    rig = calibrated_rig(tmp_path, magnetic=False)
    rig.core.perform("dwell_toggle")
    snap = rig.step()
    assert ("settings", "dwell.enabled") in snap.events


def test_reload_keeps_held_gesture_state(tmp_path):
    rig = calibrated_rig(tmp_path, magnetic=False)
    res = rig.core.bindings
    rig.profile.settings.gestures.gestures["smile"].threshold = 0.6
    rig.core.reload_settings()
    assert rig.core.bindings is res
    rig.profile.settings.gestures.bindings = list(rig.profile.settings.gestures.bindings)
    rig.core.reload_settings()
    assert rig.core.bindings is not res and isinstance(rig.core.bindings, BindingResolver)


def test_calibration_buttons_are_thread_safe(tmp_path):
    rig = Rig(tmp_path)
    sess = CalibrationSession("gaze", "quick", SCREEN, require_ready=False, intro_s=0.2,
                              async_training=False, auto_accept_s=0, seed=1)
    stop = threading.Event()

    def spam():
        while not stop.is_set():
            sess.view()
            sess.skip_intro()

    th = threading.Thread(target=spam)
    th.start()
    try:
        for _ in range(200):
            rig.user.attend(sess.target())
            sess.on_snapshot(rig.step())
    finally:
        stop.set()
        th.join()
    sess.cancel()
    assert sess.state == "cancelled"
    sess.retry()  # no resurrecting a finished session
    assert sess.state == "cancelled"


def test_snapper_thread_finds_nearby_target():
    snap = TargetSnapper(StaticProvider([BUTTON]), 45)
    snap.start()
    try:
        import time
        for _ in range(50):
            snap.request(745 + 40, 418)
            if snap.lock_for(745 + 40, 418):
                break
            time.sleep(0.02)
        assert snap.available and snap.lock_for(745 + 40, 418) == BUTTON
        assert snap.lock_for(100, 100) is None
    finally:
        snap.stop()
    assert np.isfinite(BUTTON.distance(0, 0))


def _face_frame(face_luma, bg_luma, left_right=(0, 0)):
    g = np.full((120, 160), bg_luma, dtype=np.uint8)
    g[20:100, 50:110] = face_luma
    g[20:100, 50:80] = np.clip(face_luma + left_right[0], 0, 255)
    g[20:100, 80:110] = np.clip(face_luma + left_right[1], 0, 255)
    pts = np.array([[x, y] for x in (50, 80, 110) for y in (20, 60, 100)] * 2, dtype=float)
    return g, pts


def test_lighting_advisor():
    from gazer.core.lighting import analyze

    assert analyze(*_face_frame(140, 90)).status == "good"
    assert analyze(*_face_frame(35, 30)).status == "dark"
    assert analyze(*_face_frame(80, 230)).status == "backlit"
    assert analyze(*_face_frame(150, 90, (-70, 70))).status == "uneven"
    assert analyze(*_face_frame(245, 120)).status == "bright"
    assert analyze(np.zeros((10, 10), np.uint8), None).status == "unknown"


def test_next_word_prediction_and_hindi_words():
    from gazer.core.wordpredict import WordPredictor

    p = WordPredictor()
    for sentence in (["see", "you", "tomorrow"], ["see", "you", "tomorrow"], ["see", "you", "soon"]):
        for w in sentence:
            p.learn(w)
        p.end_sentence()
    p.learn("see")
    p.learn("you")
    assert p.suggest("")[0] == "tomorrow"  # learned follower beats frequent words
    p.learn("नमस्ते")  # Devanagari with vowel signs is a real word
    assert "नमस्ते" in p.learned
    q = WordPredictor(p.export())
    assert q.pairs == p.pairs and "नमस्ते" in q.learned
    assert "kya" in q.suggest("ky", 8)  # Hinglish in the base list
