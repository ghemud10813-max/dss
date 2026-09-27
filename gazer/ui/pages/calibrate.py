from __future__ import annotations

import datetime as dt

from PyQt6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
)

from gazer.core.calibration import PRESETS
from gazer.ui import theme
from gazer.ui.widgets import Card, muted, page, toggle


class CalibratePage:
    def __init__(self, ctl):
        self.ctl = ctl
        self.widget, lay = page(
            "Calibration",
            "Gaze calibration teaches Gazer how your eyes map to the screen. Head calibration sets your "
            "comfortable range for the Head Pointer mode. Both are saved to this profile.")

        gaze = Card("Gaze calibration")
        self.gaze_info = QLabel()
        self.gaze_info.setWordWrap(True)
        gaze.add(self.gaze_info)
        row = QHBoxLayout()
        for key, (label, desc) in PRESETS.items():
            b = QPushButton(f"{label}\n{desc}")
            b.setMinimumHeight(56)
            if key == "standard":
                b.setObjectName("Primary")
            b.clicked.connect(lambda _=False, k=key: ctl.start_calibration("gaze", k, self.append))
            row.addWidget(b)
        gaze.add(layout=row)
        self.append = False
        gaze.add(toggle("Add to existing data instead of starting fresh", False,
                        lambda v: setattr(self, "append", v),
                        "Useful for covering a new seating position or lighting."))
        tips = muted("Tips: light your face from the front, keep the camera near the top-centre of the "
                     "screen, and sit at your usual distance. Accuracy is measured on points the model "
                     "never trained on, so the number is honest.")
        gaze.add(tips)
        row2 = QHBoxLayout()
        b = QPushButton("Forget learned click samples")
        b.clicked.connect(self._clear_implicit)
        row2.addWidget(b)
        b = QPushButton("Delete gaze model")
        b.setObjectName("Danger")
        b.clicked.connect(self._delete_gaze)
        row2.addWidget(b)
        row2.addStretch(1)
        gaze.add(layout=row2)
        lay.addWidget(gaze)

        head = Card("Head range")
        self.head_info = QLabel()
        self.head_info.setWordWrap(True)
        head.add(self.head_info)
        row3 = QHBoxLayout()
        b = QPushButton("Calibrate head range (12 s)")
        b.clicked.connect(lambda: ctl.start_calibration("head", ""))
        row3.addWidget(b)
        b = QPushButton("Reset")
        b.clicked.connect(self._reset_head)
        row3.addWidget(b)
        row3.addStretch(1)
        head.add(layout=row3)
        lay.addWidget(head)

        hist = Card("History")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["When", "Preset", "Avg error", "Grade"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(200)
        hist.add(self.table)
        lay.addWidget(hist)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        prof = self.ctl.profile
        g = prof.gaze
        if g.ready:
            rep = g.last_report
            extra = f" · model fit error {rep.rmse * 100:.1f}% of screen" if rep else ""
            self.gaze_info.setText(
                f"<span style='color:{theme.OK}'>Calibrated</span> — {g.n_samples} calibration samples, "
                f"{len(g.implicit)} learned from clicks{extra}")
        else:
            self.gaze_info.setText(f"<span style='color:{theme.WARN}'>Not calibrated yet.</span> "
                                   "Standard takes under a minute.")
        hc = prof.settings.head_cal
        self.head_info.setText(
            f"<span style='color:{theme.OK}'>Calibrated</span> (gain {abs(hc.gain_x):.0f} × {abs(hc.gain_y):.0f})"
            if hc.calibrated else "Using defaults. Calibrate if the Head Pointer feels too fast or slow.")
        self.table.setRowCount(0)
        for rec in reversed(prof.history):
            r = self.table.rowCount()
            self.table.insertRow(r)
            when = dt.datetime.fromtimestamp(rec.when).strftime("%d %b %H:%M")
            for c, text in enumerate([when, rec.preset, f"{rec.mean_px:.0f} px", rec.grade]):
                self.table.setItem(r, c, QTableWidgetItem(text))

    def _clear_implicit(self) -> None:
        self.ctl.profile.gaze.clear_implicit()
        self.ctl.save_gaze()
        self.refresh()

    def _delete_gaze(self) -> None:
        if QMessageBox.question(self.widget, "Delete gaze model",
                                "Delete this profile's gaze calibration and learned samples?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.ctl.profile.gaze.reset()
        self.ctl.save_gaze()
        self.refresh()

    def _reset_head(self) -> None:
        from gazer.config import HeadCalibration

        self.ctl.profile.settings.head_cal = HeadCalibration()
        self.ctl.settings_changed()
        self.refresh()
