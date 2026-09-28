"""Gaze hot-zones: glance at (or just past) a screen edge or corner to act.

Zones live on the screen border and extend beyond it — webcam gaze estimates
keep working a little past the bezel — so looking *off-screen* is the trigger.
That keeps on-screen targets (tabs, menus, taskbar) clickable by gaze without
conflict. Scroll-type actions auto-repeat and accelerate while you keep
looking, which gives hands-free reading: glance below the screen to scroll
down, above it to scroll up.
"""

from __future__ import annotations

from gazer.config import ZoneSettings

REPEATABLE = {
    "scroll_up", "scroll_down", "key:pageup", "key:pagedown", "key:up", "key:down", "key:left", "key:right",
}
CORNERS = {"top_left", "top_right", "bottom_left", "bottom_right"}


def classify(nx: float, ny: float, size: float, aspect: float = 16 / 9) -> str | None:
    """Zone for a normalized point. `size` is the band as a fraction of the
    shorter screen side; corners use a band twice as large."""
    bx = size / aspect  # horizontal band in normalized x (screen is wider)
    by = size
    left, right = nx <= bx, nx >= 1 - bx
    top, bottom = ny <= by, ny >= 1 - by
    cleft, cright = nx <= 2 * bx, nx >= 1 - 2 * bx
    ctop, cbottom = ny <= 2 * by, ny >= 1 - 2 * by
    if (left or top) and cleft and ctop:
        return "top_left"
    if (right or top) and cright and ctop:
        return "top_right"
    if (left or bottom) and cleft and cbottom:
        return "bottom_left"
    if (right or bottom) and cright and cbottom:
        return "bottom_right"
    if top:
        return "top"
    if bottom:
        return "bottom"
    if left:
        return "left"
    if right:
        return "right"
    return None


class ZoneDetector:
    def __init__(self, settings: ZoneSettings, aspect: float = 16 / 9):
        self.s = settings
        self.aspect = aspect
        self.current: str | None = None
        self.since = 0.0
        self.armed = True
        self.progress = 0.0
        self._next_repeat = 0.0
        self._repeats = 0

    def reset(self) -> None:
        self.current = None
        self.armed = True
        self.progress = 0.0

    def update(self, t: float, p_norm: tuple[float, float] | None) -> list[str]:
        """Feed the gaze (or pointer) point; returns actions to perform now."""
        if p_norm is None or not self.s.enabled:
            self.reset()
            return []
        zone = classify(p_norm[0], p_norm[1], self.s.size, self.aspect)
        action = self.s.actions.get(zone, "none") if zone else "none"
        if zone is None or action == "none":
            self.reset()
            return []
        if zone != self.current:
            self.current = zone
            self.since = t
            self.armed = True
            self.progress = 0.0
            self._repeats = 0
            return []
        dwell = self.s.dwell_ms / 1000
        if not self.armed:
            if action in REPEATABLE and t >= self._next_repeat:
                self._repeats += 1
                # accelerate from ~4 to ~16 notches/s: fast enough to skim, slow enough to read
                self._next_repeat = t + max(0.06, 0.25 * 0.88 ** self._repeats)
                self.progress = 1.0
                return [action]
            return []
        self.progress = min((t - self.since) / dwell, 1.0)
        if self.progress >= 1.0:
            self.armed = False
            self._next_repeat = t + 0.3
            return [action]
        return []
