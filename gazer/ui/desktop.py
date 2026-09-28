"""The desktop layer: Qt shell window, desktop HUD overlay, focus-free gaze
keyboard, fullscreen calibration window and tray icon.

The Command Deck itself is the web UI (served by the hub). With PyQt6-WebEngine
installed it is embedded in a native window; otherwise it opens in your
browser and this layer keeps running the overlay, keyboard and tray.
"""

from __future__ import annotations

import logging
import signal
import sys
import webbrowser

from PyQt6.QtCore import QObject, QPointF, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QGuiApplication
from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon

from gazer import APP_NAME
from gazer.core.engine import Snapshot
from gazer.ui import theme
from gazer.ui.bridge import ScreenMapper, pick_qscreen
from gazer.ui.eyedock import EyeDock
from gazer.ui.keyboard import GazeKeyboard
from gazer.ui.overlay import Overlay

log = logging.getLogger("gazer.desktop")


def _webengine():
    try:
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: F401
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        return QWebEngineView
    except Exception as exc:  # noqa: BLE001
        log.info("QtWebEngine unavailable (%s) — using the system browser", exc)
        return None


class DeckWindow(QMainWindow):
    def __init__(self, view_cls, url: str, on_close):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Command Deck")
        self.setWindowIcon(theme.app_icon())
        self.resize(1600, 960)
        self.setMinimumSize(1100, 700)
        self.view = view_cls(self)
        self.view.page().setBackgroundColor(theme_color(theme.BG))
        self.view.setUrl(QUrl(url))
        self.setCentralWidget(self.view)
        self._on_close = on_close
        # let the deck go fullscreen (Gaze Trainer, browser-style calibration)
        self.view.page().fullScreenRequested.connect(self._fullscreen)

    def _fullscreen(self, request) -> None:
        request.accept()
        self.showFullScreen() if request.toggleOn() else self.showNormal()

    def closeEvent(self, e):
        # Closing the deck hides it; the tray keeps Gazer running.
        e.ignore()
        self.hide()
        self._on_close()


class CalibrationWindow(QMainWindow):
    """Fullscreen deck page in calibration mode on the controlled monitor."""

    def __init__(self, view_cls, url: str, screen):
        super().__init__()
        from PyQt6.QtCore import Qt

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.view = view_cls(self)
        self.view.page().setBackgroundColor(theme_color("#05080c"))
        self.view.setUrl(QUrl(url))
        self.setCentralWidget(self.view)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())

    def show_on_screen(self) -> None:
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.view.setFocus()


def theme_color(hex_color: str):
    from PyQt6.QtGui import QColor

    return QColor(hex_color)


class DesktopUI(QObject):
    """Implements `UIHooks` + frame listener; every call is marshalled onto the Qt thread."""

    sig_frame = pyqtSignal(object)
    sig_keyboard = pyqtSignal()
    sig_dock = pyqtSignal()
    sig_calib_open = pyqtSignal()
    sig_calib_close = pyqtSignal()
    sig_screen = pyqtSignal()
    sig_quit = pyqtSignal()
    sig_state = pyqtSignal()

    def __init__(self, app: QApplication, ctl, hub, view_cls=None):
        super().__init__()
        self.app = app
        self.ctl = ctl
        self.hub = hub
        self.view_cls = view_cls
        self.deck: DeckWindow | None = None
        self.calib_win: CalibrationWindow | None = None
        self.keyboard: GazeKeyboard | None = None
        self.dock: EyeDock | None = None
        qs = pick_qscreen(ctl.config.screen_index)
        self.mapper = ScreenMapper(qs)
        self.overlay = Overlay(self.mapper, lambda: self.ctl.profile.settings.overlay,
                               follow_os_cursor=not ctl.virtual_input)
        self.sig_frame.connect(self._frame)
        self.sig_keyboard.connect(self._toggle_keyboard)
        self.sig_dock.connect(self._sync_dock)
        self.sig_calib_open.connect(self._open_calibration)
        self.sig_calib_close.connect(self._close_calibration)
        self.sig_screen.connect(self._screen_changed)
        self.sig_quit.connect(self._quit)
        self.sig_state.connect(self._refresh_tray)
        self.tray = self._make_tray()

    # ------------------------------------------------------------ hooks

    def toggle_keyboard(self) -> None:
        self.sig_keyboard.emit()

    def toggle_dock(self) -> None:
        self.sig_dock.emit()

    def open_calibration(self) -> bool:
        if self.view_cls is None:
            return False  # the browser page runs calibration fullscreen itself
        self.sig_calib_open.emit()
        return True

    def close_calibration(self) -> None:
        self.sig_calib_close.emit()

    def screen_changed(self) -> None:
        self.sig_screen.emit()

    def quit(self) -> None:
        self.sig_quit.emit()

    def on_frame(self, snap: Snapshot, extra: dict) -> None:
        self.sig_frame.emit(snap)

    def on_state(self) -> None:
        self.sig_state.emit()

    # ------------------------------------------------------------- slots

    def show_deck(self) -> None:
        if self.view_cls is None:
            webbrowser.open(self.hub.url)
            return
        if self.deck is None:
            self.deck = DeckWindow(self.view_cls, self.hub.url, on_close=self._deck_closed)
        self.deck.show()
        self.deck.raise_()
        self.deck.activateWindow()

    def _deck_closed(self) -> None:
        if self.tray is not None and self.tray.isVisible():
            self.tray.showMessage(APP_NAME, "Still running in the tray. Double-click the eye to reopen.",
                                  QSystemTrayIcon.MessageIcon.Information, 2500)
        else:
            self._quit()

    def _frame(self, snap: Snapshot) -> None:
        if snap.control and not self.overlay.isVisible():
            self.overlay.show()
        elif not snap.control and self.overlay.isVisible() and not self.overlay.toast:
            self.overlay.hide()
        self.overlay.on_snapshot(snap)
        self._dock_frame(snap)

    # ------------------------------------------------------------ Eye Dock

    def _sync_dock(self, want: bool | None = None) -> None:
        s = self.ctl.profile.settings.dock
        show = s.enabled and self.ctl.core.control if want is None else want
        if show:
            if self.dock is None:
                self.dock = EyeDock(s)
                self.dock.chosen.connect(self._dock_chosen)
            self.dock.s = s
            self.dock.place(self.mapper.qscreen.availableGeometry())
            if not self.dock.isVisible():
                self.dock.show()
            self.ctl.set_dwell_exclusions([self.mapper.global_rect_to_physical(self.dock.geometry())])
        elif self.dock is not None and self.dock.isVisible():
            self.dock.hide()
            self.ctl.set_dwell_exclusions([])

    def _dock_frame(self, snap: Snapshot) -> None:
        want = self.ctl.profile.settings.dock.enabled and snap.control
        visible = self.dock is not None and self.dock.isVisible()
        if want != visible:
            self._sync_dock(want)
        if self.dock is None or not self.dock.isVisible():
            return
        self.dock.set_state(snap.dwell_next, self.ctl.profile.settings.dwell.enabled, snap.paused)
        local = None
        if snap.pointer is not None:
            g = self.mapper.to_global_logical(*snap.pointer)
            local = QPointF(g.x() - self.dock.x(), g.y() - self.dock.y())
        self.dock.on_pointer(local, snap.t)

    def _dock_chosen(self, key: str, kind: str) -> None:
        if kind == "type":
            self.ctl.set_dwell_next(None if key == "left_click" else key)
        else:
            self.ctl.perform(key)

    def _toggle_keyboard(self) -> None:
        if self.keyboard is None:
            self.keyboard = GazeKeyboard(self.ctl.backend, self.ctl.predictor, self.ctl.profile.settings.keyboard)
            self.keyboard.typed.connect(self._typed)
            self.keyboard.layout_changed.connect(lambda _l: self.ctl.mark_dirty())
        if self.keyboard.isVisible():
            self.keyboard.hide()
        else:
            self.keyboard.predictor = self.ctl.predictor
            self.keyboard.s = self.ctl.profile.settings.keyboard
            self.keyboard.place(self.mapper.qscreen.availableGeometry())
            self.keyboard.show()

    def _typed(self, n: int) -> None:
        self.ctl.profile.stats.keys_typed += n

    def _open_calibration(self) -> None:
        self._close_calibration()
        self.overlay.hide()
        self.calib_win = CalibrationWindow(self.view_cls, self.hub.url.replace("/?", "/?calib=1&") + "#calib",
                                           self.mapper.qscreen)
        self.calib_win.show_on_screen()

    def _close_calibration(self) -> None:
        if self.calib_win is not None:
            self.calib_win.close()
            self.calib_win.deleteLater()
            self.calib_win = None

    def _screen_changed(self) -> None:
        self.mapper = ScreenMapper(pick_qscreen(self.ctl.config.screen_index))
        self.overlay.set_mapper(self.mapper)
        if self.dock is not None and self.dock.isVisible():
            self._sync_dock(True)

    def _quit(self) -> None:
        self.app.quit()

    # -------------------------------------------------------------- tray

    def _make_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(theme.app_icon(), self)
        tray.setToolTip(f"{APP_NAME} — eye control")
        menu = QMenu()
        menu.setStyleSheet(theme.STYLESHEET)
        self._act_deck = QAction("Open Command Deck", menu, triggered=self.show_deck)
        self._act_engage = QAction("Engage eye control", menu, triggered=lambda: self.ctl.perform("control_toggle"))
        self._act_pause = QAction("Pause / resume", menu, triggered=lambda: self.ctl.perform("pause_toggle"))
        self._act_cal = QAction("Calibrate eyes", menu, triggered=lambda: self.ctl.start_calibration("gaze", "standard"))
        self._act_kb = QAction("Gaze keyboard", menu, triggered=self._toggle_keyboard)
        self._act_dock = QAction("Eye Dock", menu, triggered=self.ctl.toggle_dock)
        self._act_quit = QAction("Quit Gazer", menu, triggered=self._quit)
        for a in (self._act_deck, self._act_engage, self._act_pause, self._act_cal, self._act_kb, self._act_dock):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self._act_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: self.show_deck()
                               if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        tray.show()
        self._menu = menu
        return tray

    def _refresh_tray(self) -> None:
        if self.tray is None:
            return
        on = self.ctl.core.control
        self._act_engage.setText("Disengage eye control" if on else "Engage eye control")


def run(args, build) -> int:
    from PyQt6.QtCore import QCoreApplication, Qt

    QGuiApplication.setApplicationName(APP_NAME)
    # QtWebEngine must be imported (and GL contexts shared) before the app exists.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    view_cls = _webengine()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setWindowIcon(theme.app_icon())
    app.setQuitOnLastWindowClosed(False)
    ctl, hub = build(args)
    if not hub.start():
        log.error(hub.error)
        return 1
    ui = DesktopUI(app, ctl, hub, view_cls)
    ctl.ui = ui
    ctl.listeners.append(ui)
    ctl.start()
    ui.show_deck()
    print(f"\n  GAZER Command Deck → {hub.url}\n", flush=True)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer()
    tick.start(250)  # let Python handle Ctrl+C while Qt's loop runs
    tick.timeout.connect(lambda: None)
    try:
        code = app.exec()
    finally:
        ctl.stop()
        hub.stop()
    return code
