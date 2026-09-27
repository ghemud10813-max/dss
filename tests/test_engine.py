"""EngineCore end-to-end with synthetic features and a recording input backend."""

import numpy as np

from gazer.config import AppConfig, Binding, ProfileSettings, from_dict, to_dict
from gazer.core.engine import CursorOutput, EngineCore
from gazer.core.input_backend import RecordingInput, parse_combo
from gazer.core.profiles import ProfileStore
from gazer.core.screen import ScreenRect
from gazer.core.voice import parse_command
from gazer.core.wordpredict import WordPredictor

SCREEN = ScreenRect(0, 0, 1920, 1080)


def make_core(tmp_path, mode="head_mouse"):
    store = ProfileStore(tmp_path / "profiles")
    prof = store.create("tester")
    prof.settings.pointer.mode = mode
    backend = RecordingInput((960, 540))
    core = EngineCore(prof, SCREEN, backend, CursorOutput(backend, manual_override=False))
    core.set_control(True)
    return core, backend


def smile(f, v):
    f.blend["mouthSmileLeft"] = f.blend["mouthSmileRight"] = v
    return f


def test_smile_clicks_at_pointer(tmp_path, feats_factory):
    core, backend = make_core(tmp_path)
    t = 0.0
    for i in range(10):
        t = i / 30
        core.process(t, feats_factory(t=t))
    for i in range(10, 25):
        t = i / 30
        core.process(t, smile(feats_factory(t=t), 0.9))
    clicks = backend.clicks()
    assert len(clicks) == 1 and clicks[0][0] == "left"
    assert core.profile.stats.clicks == 1


def test_paused_ignores_clicks_but_long_blink_resumes(tmp_path, feats_factory):
    core, backend = make_core(tmp_path)
    core.set_paused(True)
    for i in range(20):
        core.process(i / 30, smile(feats_factory(t=i / 30), 0.9))
    assert backend.clicks() == []
    for i in range(100):  # establish open-eye baseline
        core.process(1 + i / 30, feats_factory(t=1 + i / 30))
    t0 = 5.0
    for i in range(30):  # ~1 s both eyes closed
        core.process(t0 + i / 30, feats_factory(t=t0 + i / 30, ear=(0.05, 0.05)))
    assert core.paused is False


def test_action_wheel_select_executes_at_anchor(tmp_path, feats_factory):
    core, backend = make_core(tmp_path)
    core.process(0, feats_factory())
    anchor = tuple(core.pointer.P)
    core.perform("action_wheel")
    assert core.wheel is not None
    core.wheel.items[1] = "right_click"  # right-hand sector
    hp = 0.0
    t = 0.0
    for i in range(1, 60):
        t = i / 30
        hp = min(hp + 0.006, 0.06)
        core.process(t, feats_factory(t=t, head_point=(hp, 0)))
        if core.wheel is None:
            break
    clicks = backend.clicks()
    assert clicks and clicks[-1][0] == "right"
    assert np.hypot(clicks[-1][1][0] - anchor[0], clicks[-1][1][1] - anchor[1]) < 2


def test_zoom_click_lands_on_real_target(tmp_path, feats_factory):
    core, backend = make_core(tmp_path)
    core.process(0, feats_factory())
    core.perform("zoom")
    assert core.zoom is not None
    start_real = core.zoom.to_real(core.zoom.pointer)
    core.zoom.pointer = core.zoom.pointer + np.array([30.0, 0.0])
    core.pointer.P = core.zoom.pointer.copy()
    core.perform("left_click")
    assert core.zoom is None
    (_, pos), = backend.clicks()
    assert abs(pos[0] - (start_real[0] + 10)) <= 1


def test_drag_released_when_face_lost(tmp_path, feats_factory):
    core, backend = make_core(tmp_path)
    core.process(0, feats_factory())
    core.perform("drag_toggle")
    assert core.dragging
    for i in range(1, 50):
        core.process(i / 30, None)
    assert not core.dragging
    assert ("button", "left", False) in [e[:3] for e in backend.events]


def test_while_held_drag_binding(tmp_path, feats_factory):
    core, backend = make_core(tmp_path)
    core.profile.settings.gestures.bindings = [Binding("pucker", "while_held", "drag_hold")]
    core.reload_settings()
    for i in range(40):
        f = feats_factory(t=i / 30)
        f.blend["mouthPucker"] = 0.9 if 5 <= i < 25 else 0.0
        core.process(i / 30, f)
    downs = [e for e in backend.events if e[0] == "button"]
    assert [d[2] for d in downs] == [True, False]


def test_cursor_output_glides_and_yields_to_manual_mouse():
    backend = RecordingInput((0, 0))
    seen = []
    out = CursorOutput(backend, smoothing_ms=30, manual_override=True, on_manual=seen.append)
    out.set_target((100, 0))
    for i in range(1, 60):
        out.step(i * 0.008, 0.008)
    assert abs(backend.position()[0] - 100) <= 1
    backend.move(500, 500)  # user grabs the physical mouse
    out.step(1.0, 0.008)
    assert seen and out.manual_until > 1.0
    out.step(1.1, 0.008)
    assert backend.position() == (500, 500)


def test_settings_roundtrip_tolerates_junk():
    s = ProfileSettings()
    s.pointer.mode = "gaze"
    s.gestures.bindings.append(Binding("tilt_left", "hold", "zoom", 900))
    d = to_dict(s)
    d["pointer"]["unknown_key"] = 5
    d["dwell"]["time_ms"] = "not a number"
    s2 = from_dict(ProfileSettings, d)
    assert s2.pointer.mode == "gaze"
    assert s2.gestures.bindings[-1].action == "zoom"
    assert s2.dwell.time_ms == 900  # fell back to default
    assert from_dict(AppConfig, {"camera": {"index": 2}}).camera.index == 2


def test_profile_store(tmp_path):
    store = ProfileStore(tmp_path)
    p = store.create("alice")
    p.stats.clicks = 7
    p.save_meta()
    assert store.names() == ["alice"]
    assert store.load("alice").stats.clicks == 7
    store.duplicate("alice", "bob")
    store.rename("bob", "carol")
    assert store.names() == ["alice", "carol"]
    store.delete("carol")
    assert store.names() == ["alice"]
    assert not store.valid_name("../evil")


def test_voice_and_input_parsing():
    assert parse_command("click") == "left_click"
    assert parse_command("please scroll down") == "scroll_down"
    assert parse_command("[unk]") is None
    assert parse_combo("ctrl+shift+t") == ["ctrl", "shift", "t"]


def test_word_predictor_learns():
    wp = WordPredictor()
    assert "the" in wp.suggest("th")
    wp.learn("gazertastic")
    wp.learn("gazertastic")
    assert wp.suggest("gaz")[0] == "gazertastic"
