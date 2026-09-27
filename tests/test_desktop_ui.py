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
