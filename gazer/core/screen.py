"""Monitor geometry in physical pixels (the coordinate space of OS input)."""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenRect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    @property
    def diag(self) -> float:
        return (self.width**2 + self.height**2) ** 0.5

    def norm_to_px(self, nx: float, ny: float) -> tuple[float, float]:
        return self.x + nx * self.width, self.y + ny * self.height

    def px_to_norm(self, px: float, py: float) -> tuple[float, float]:
        return (px - self.x) / self.width, (py - self.y) / self.height

    def clamp(self, px: float, py: float) -> tuple[float, float]:
        return (
            min(max(px, self.x), self.right - 1),
            min(max(py, self.y), self.bottom - 1),
        )

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom


@dataclass(frozen=True)
class MonitorInfo:
    name: str
    rect: ScreenRect
    primary: bool


def list_monitors() -> list[MonitorInfo]:
    """Physical monitor rectangles. Requires a DPI-aware process on Windows
    (QApplication makes the process per-monitor aware)."""
    if sys.platform == "win32":
        try:
            return _win_monitors()
        except Exception:
            pass
    try:
        from screeninfo import get_monitors

        return [
            MonitorInfo(m.name or f"Display {i + 1}", ScreenRect(m.x, m.y, m.width, m.height), bool(m.is_primary))
            for i, m in enumerate(get_monitors())
        ]
    except Exception:
        return [MonitorInfo("Display 1", ScreenRect(0, 0, 1920, 1080), True)]


def _win_monitors() -> list[MonitorInfo]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    monitors: list[MonitorInfo] = []
    proc_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def _cb(hmon, _hdc, _rect, _lp):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append(
                MonitorInfo(
                    info.szDevice,
                    ScreenRect(r.left, r.top, r.right - r.left, r.bottom - r.top),
                    bool(info.dwFlags & 1),
                )
            )
        return 1

    user32.EnumDisplayMonitors(None, None, proc_type(_cb), 0)
    if not monitors:
        raise RuntimeError("no monitors")
    return monitors


def pick_monitor(index: int = 0) -> MonitorInfo:
    """index 0 = primary, then the remaining monitors in OS order."""
    mons = list_monitors()
    ordered = sorted(mons, key=lambda m: not m.primary)
    return ordered[index] if 0 <= index < len(ordered) else ordered[0]
