"""Colours and the application stylesheet."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

# Shared with the web Command Deck (gazer/web/css/app.css).
BG = "#03060a"
SURFACE = "#08111a"
SURFACE_2 = "#0c1a24"
SURFACE_3 = "#12283a"
BORDER = "#1b3a44"
TEXT = "#dcf5f2"
MUTED = "#6f8796"
ACCENT = "#3ee8d8"
ACCENT_2 = "#9b7cff"
OK = "#4dffa6"
WARN = "#ffb547"
DANGER = "#ff4d6a"

STYLESHEET = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog, #Page {{ background: {BG}; }}
QWidget#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QListWidget#Nav {{ background: transparent; border: none; outline: none; padding: 6px; }}
QListWidget#Nav::item {{ padding: 10px 14px; margin: 2px 0; border-radius: 8px; color: {MUTED}; }}
QListWidget#Nav::item:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#Nav::item:selected {{ background: {SURFACE_3}; color: {ACCENT}; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#CardTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#PageTitle {{ font-size: 22px; font-weight: 700; }}
QLabel#Muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#Big {{ font-size: 26px; font-weight: 700; }}
QLabel#Brand {{ font-size: 18px; font-weight: 800; letter-spacing: 3px; color: {ACCENT}; }}
QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: {SURFACE_3}; border-color: #3a4352; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #5a6272; }}
QPushButton#Primary {{ background: {ACCENT}; color: #06201d; border: none; font-weight: 700; }}
QPushButton#Primary:hover {{ background: #5fe3d6; }}
QPushButton#Danger {{ background: transparent; color: {DANGER}; border: 1px solid #5a2a31; }}
QPushButton#Danger:hover {{ background: #2a1519; }}
QPushButton#Seg {{ border-radius: 0; padding: 7px 12px; }}
QPushButton#Seg:checked {{ background: {SURFACE_3}; color: {ACCENT}; border-color: {ACCENT}; }}
QPushButton#Choice {{ text-align: left; padding: 12px 14px; }}
QPushButton#Choice:checked {{ border: 1px solid {ACCENT}; background: #15302e; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {SURFACE_2}; border: 1px solid {BORDER};
    border-radius: 7px; padding: 5px 8px; selection-background-color: {ACCENT}; selection-color: #06201d; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: #3a4352; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {SURFACE_3}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 34px; height: 18px; border-radius: 9px; background: {SURFACE_3}; border: 1px solid {BORDER}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QTableWidget {{ background: {SURFACE}; border: none; gridline-color: {BORDER}; }}
QHeaderView::section {{ background: {SURFACE_2}; color: {MUTED}; border: none; padding: 6px; }}
QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 4px; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMessageBox {{ background: {BG}; }}
"""


def app_icon(size: int = 64, color: str = ACCENT) -> QIcon:
    """An eye glyph painted at runtime (no binary assets needed)."""
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(color)
        path = QPainterPath()
        m = s * 0.08
        path.moveTo(m, s / 2)
        path.quadTo(s / 2, -s * 0.12, s - m, s / 2)
        path.quadTo(s / 2, s * 1.12, m, s / 2)
        p.setPen(QPen(c, max(1.5, s * 0.07)))
        p.setBrush(QColor(BG))
        p.drawPath(path)
        g = QRadialGradient(QPointF(s / 2, s / 2), s * 0.2)
        g.setColorAt(0, QColor(ACCENT_2))
        g.setColorAt(1, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QRectF(s / 2 - s * 0.19, s / 2 - s * 0.19, s * 0.38, s * 0.38))
        p.setBrush(QColor(BG))
        p.drawEllipse(QRectF(s / 2 - s * 0.07, s / 2 - s * 0.07, s * 0.14, s * 0.14))
        p.end()
        icon.addPixmap(pm)
    return icon
