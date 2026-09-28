"""Qt desktop layer smoke tests (offscreen): overlay painting, keyboard, hooks."""

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from gazer.config import AppConfig  # noqa: E402
from gazer.controller import Controller  # noqa: E402
from gazer.core.engine import Snapshot  # noqa: E402
from gazer.core.interaction import WheelView  # noqa: E402
from gazer.core.profiles import ProfileStore  # noqa: E402

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

class FakeHub:
    url = "http://127.0.0.1:1/?t=x"

def test_desktop_ui_overlay_and_keyboard(app, tmp_path):
    from gazer.ui import desktop

    ctl = Controller(AppConfig(), demo=True, store=ProfileStore(tmp_path / "p"), persist_config=False)
    ui = desktop.DesktopUI(app, ctl, FakeHub())
    ctl.ui = ui
    assert ui.open_calibration() is False  # no WebEngine → the browser page calibrates itself
    assert ctl.state()["app"]["native_calib"] is False

    cx, cy = ctl.core.screen.center
    snap = Snapshot(t=1.0, face=True, control=True, pointer=(cx, cy), gaze=(cx + 40, cy), dwell_enabled=True,
                    dwell_progress=0.6, zone=("bottom", 0.5), zones_enabled=True,
                    events=[("toast", "Hello pilot"), ("click", (cx, cy), "left"), ("zone", "top", "scroll_up")])
    ctl.profile.settings.overlay.show_gaze_dot = True
    ui._frame(snap)
    assert ui.overlay.isVisible()
    img = ui.overlay.grab().toImage()
    assert img.width() > 0

    # the action wheel paints too
    snap2 = Snapshot(t=1.1, face=True, control=True, pointer=(cx, cy),
                     wheel=WheelView((cx, cy), ["left_click", "right_click", "zoom", "keyboard_toggle"], 160, 48, 1, 0.5,
                                     (cx + 50, cy)))
    ui._frame(snap2)
    ui.overlay.grab()

    ui._toggle_keyboard()
    assert ui.keyboard is not None and ui.keyboard.isVisible()
    ui.keyboard._press("h")
    ui.keyboard._press("i")
    ui.keyboard._char(" ")
    assert ("text", "h") in ctl.backend.events and ("text", "i") in ctl.backend.events
    assert ctl.profile.stats.keys_typed >= 3
    ui._toggle_keyboard()
    assert not ui.keyboard.isVisible()

    ui._frame(Snapshot(t=2.0, face=False, control=False))
    assert not ui.overlay.isVisible() or ui.overlay.toast is not None


def test_keyboard_hindi_layout(app):
    from gazer.config import KeyboardSettings
    from gazer.core.input_backend import RecordingInput
    from gazer.core.wordpredict import WordPredictor
    from gazer.ui.keyboard import GazeKeyboard

    backend = RecordingInput()
    kb = GazeKeyboard(backend, WordPredictor(), KeyboardSettings())
    kb._toggle_lang()
    assert kb.s.layout == "hi"
    for ch in ("न", "म", "स", "्", "त", "े"):
        kb._press(ch)
    assert kb.word == "नमस्ते"
    kb._char(" ")
    assert "नमस्ते" in kb.predictor.learned
    assert ("text", "े") in backend.events


def test_eye_dock_hover_dwell_sets_click_type(app, tmp_path):
    from PyQt6.QtCore import QPointF

    from gazer.config import AppConfig as Cfg
    from gazer.controller import Controller as Ctl
    from gazer.core.engine import Snapshot as Snap
    from gazer.core.profiles import ProfileStore as Store
    from gazer.core.targets import Target
    from gazer.ui import desktop
    from gazer.ui.eyedock import ITEMS

    ctl = Ctl(Cfg(), demo=True, store=Store(tmp_path / "p"), persist_config=False)
    ui = desktop.DesktopUI(app, ctl, FakeHub())
    ctl.ui = ui
    ctl.profile.settings.dock.enabled = True
    ctl.core.control = True
    ui._frame(Snap(t=0.0, face=True, control=True, pointer=(10, 10)))
    dock = ui.dock
    assert dock is not None and dock.isVisible()
    posted = []
    ctl.runner.post = posted.append  # capture engine commands
    right = [k for k, *_ in ITEMS].index("right_click")
    r = dock.button_rects()[right]
    centre = QPointF(r.center().x(), r.center().y())
    dock.on_pointer(centre, 1.0)
    dock.on_pointer(centre, 1.0 + dock.s.dwell_ms / 1000 + 0.05)
    assert dock.current == "right_click" and posted, "hover-dwell chose RIGHT"
    posted[-1](ctl.core)
    assert ctl.core.dwell_next == "right_click"
    # the dock registered itself as a no-dwell zone
    ctl.set_dwell_exclusions([(0, 0, 10, 10)])
    posted[-1](ctl.core)
    assert ctl.core.dwell_exclusions == [(0, 0, 10, 10)]

    snap = Snap(t=2.0, face=True, control=True, pointer=(700, 400), target=Target(650, 380, 120, 40, "button", "Save"),
                dwell_next="right_click")
    ui._frame(snap)
    assert ui.overlay.grab().toImage().width() > 0  # brackets + badge paint
    dock.grab()
