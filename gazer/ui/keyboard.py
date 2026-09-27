"""On-screen keyboard that never takes focus, with word prediction.

Keys are pressed with any Gazer click (gesture, dwell, voice), so the window
must not activate: otherwise typed text would go to the keyboard itself
instead of the app you're writing in.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from gazer.config import KeyboardSettings
from gazer.core.input_backend import InputBackend
from gazer.core.wordpredict import WordPredictor
from gazer.ui import theme

ROWS_ALPHA = [
    list("1234567890") + ["⌫"],
    list("qwertyuiop") + ["'"],
    list("asdfghjkl") + ["↵"],
    ["⇧"] + list("zxcvbnm") + [",", ".", "?"],
]
ROWS_SYM = [
    list("!@#$%^&*()") + ["⌫"],
    list("-_=+[]{};:") + ['"'],
    list("/\\|<>~`€£") + ["↵"],
    ["⇧"] + list("¿¡°•…") + [",", ".", "?", "!"],
]
SPECIAL = {"⌫": "backspace", "↵": "enter"}


def _no_activate(widget: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOPMOST = 0x00000008
        hwnd = int(widget.winId())
        u = ctypes.windll.user32
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        style = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ctypes.c_ssize_t(style | WS_EX_NOACTIVATE | WS_EX_TOPMOST))
    except Exception:
        pass


class GazeKeyboard(QWidget):
    typed = pyqtSignal(int)  # characters typed (for stats)

    def __init__(self, backend: InputBackend, predictor: WordPredictor, settings: KeyboardSettings):
        super().__init__(None)
        self.backend = backend
        self.predictor = predictor
        self.s = settings
        self.shift = False
        self.symbols = False
        self.word = ""
        self.setWindowTitle("Gazer Keyboard")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(f"""
            QWidget#KB {{ background: {theme.BG}; border-top: 2px solid {theme.ACCENT}; }}
            QPushButton {{ font-family: "Segoe UI", sans-serif; font-size: 22px; font-weight: 600; color: {theme.TEXT};
                           border-radius: 2px; background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; }}
            QPushButton:hover {{ background: {theme.SURFACE_3}; border: 1px solid {theme.ACCENT}; color: #ffffff; }}
            QPushButton:pressed {{ background: {theme.ACCENT}; color: #021a18; }}
            QPushButton#Pred {{ font-size: 19px; color: {theme.ACCENT}; background: #050d14; border: 1px solid #16424a; }}
            QPushButton#Mod:checked {{ background: {theme.ACCENT}; color: #021a18; }}
            QPushButton#Hide {{ color: {theme.DANGER}; border-color: #5a2a31; }}
        """)
        self.setObjectName("KB")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)
        self.pred_row = QHBoxLayout()
        self.pred_buttons = []
        for _ in range(4):
            b = self._btn("", None, "Pred")
            b.clicked.connect(lambda _=False, btn=b: self._on_prediction(btn))
            self.pred_buttons.append(b)
            self.pred_row.addWidget(b)
        root.addLayout(self.pred_row, 2)
        self.grid = QGridLayout()
        self.grid.setSpacing(6)
        root.addLayout(self.grid, 8)
        bottom = QHBoxLayout()
        self.sym_btn = self._btn("123", self._toggle_symbols, "Mod", checkable=True)
        bottom.addWidget(self.sym_btn, 2)
        bottom.addWidget(self._btn("◀", lambda: self._key("left")), 1)
        bottom.addWidget(self._btn("space", lambda: self._char(" ")), 7)
        bottom.addWidget(self._btn("▶", lambda: self._key("right")), 1)
        bottom.addWidget(self._btn("Esc", lambda: self._key("esc")), 1)
        bottom.addWidget(self._btn("Tab", lambda: self._key("tab")), 1)
        bottom.addWidget(self._btn("✕ Hide", self.hide, "Hide"), 2)
        root.addLayout(bottom, 2)
        self._build_keys()
        self._update_predictions()

    def _btn(self, text, fn, name="", checkable=False) -> QPushButton:
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if name:
            b.setObjectName(name)
        b.setCheckable(checkable)
        if fn is not None:
            b.clicked.connect(lambda _=False: fn())
        return b

    def _build_keys(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows = ROWS_SYM if self.symbols else ROWS_ALPHA
        for r, row in enumerate(rows):
            col = 0
            for key in row:
                span = 2 if key in ("⌫", "↵", "⇧") else 1
                if key == "⇧":
                    b = self._btn(key, self._toggle_shift, "Mod", checkable=True)
                    b.setChecked(self.shift)
                else:
                    label = key.upper() if (self.shift and len(key) == 1) else key
                    b = self._btn(label, lambda k=key: self._press(k))
                self.grid.addWidget(b, r, col, 1, span)
                col += span

    def place(self, geo) -> None:
        h = int(geo.height() * self.s.height_frac)
        y = geo.y() if self.s.dock == "top" else geo.y() + geo.height() - h
        self.setGeometry(geo.x(), y, geo.width(), h)

    def showEvent(self, e):
        super().showEvent(e)
        _no_activate(self)

    # ------------------------------------------------------------- typing

    def _press(self, key: str) -> None:
        if key in SPECIAL:
            self._key(SPECIAL[key])
            return
        ch = key.upper() if self.shift else key
        self._char(ch)
        if self.shift:
            self.shift = False
            self._build_keys()

    def _char(self, ch: str) -> None:
        self.backend.type_text(ch)
        self.typed.emit(1)
        if ch.isalpha() or ch == "'":
            self.word += ch
        else:
            if self.word:
                self.predictor.learn(self.word)
            self.word = ""
        self._update_predictions()

    def _key(self, name: str) -> None:
        self.backend.tap(name)
        if name == "backspace":
            self.word = self.word[:-1]
        elif name in ("enter", "tab", "esc", "left", "right"):
            if self.word and name in ("enter", "tab"):
                self.predictor.learn(self.word)
            self.word = ""
        self._update_predictions()

    def _on_prediction(self, b: QPushButton) -> None:
        word = b.text()
        if not word:
            return
        rest = word[len(self.word):] if word.lower().startswith(self.word.lower()) else None
        if rest is None:
            for _ in self.word:
                self.backend.tap("backspace")
            rest = word
        self.backend.type_text(rest + " ")
        self.typed.emit(len(rest) + 1)
        self.predictor.learn(word)
        self.word = ""
        self._update_predictions()

    def _update_predictions(self) -> None:
        if not self.s.predictions:
            for b in self.pred_buttons:
                b.setText("")
            return
        sugg = self.predictor.suggest(self.word, 4)
        for b, w in zip(self.pred_buttons, sugg + [""] * 4):
            if self.word and self.word[0].isupper():
                w = w.capitalize()
            b.setText(w)

    def _toggle_shift(self) -> None:
        self.shift = not self.shift
        self._build_keys()

    def _toggle_symbols(self) -> None:
        self.symbols = not self.symbols
        self.sym_btn.setText("abc" if self.symbols else "123")
        self._build_keys()
