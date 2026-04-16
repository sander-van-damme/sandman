"""System tray icon and menu for Sandman.

Uses ``pystray``. Runs on the main thread because that's where pystray
wants to live on Windows; the monitor loop runs on a background thread
and pushes status updates into this module via ``update_state``.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from .monitor import MonitorState, MonitorStatus

log = logging.getLogger(__name__)


ASSETS_DIR = Path(__file__).parent / "assets"


class IconState(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    NUDGE = "nudge"
    ERROR = "error"


def _state_from_monitor(status: MonitorStatus) -> IconState:
    if status.state == MonitorState.ERROR:
        return IconState.ERROR
    if status.state == MonitorState.NUDGING:
        return IconState.NUDGE
    if status.state == MonitorState.ACTIVE:
        return IconState.ACTIVE
    return IconState.IDLE


# ---- icon generation ------------------------------------------------------

# On first run — or when packaged assets are missing — we draw the moon
# icons programmatically with Pillow. They're simple crescent shapes with
# state-dependent colors.

_ICON_COLORS = {
    IconState.IDLE: (160, 160, 170, 255),
    IconState.ACTIVE: (90, 150, 240, 255),
    IconState.NUDGE: (240, 150, 60, 255),
    IconState.ERROR: (220, 60, 60, 255),
}


def _draw_moon(color: tuple[int, int, int, int], size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Full circle for the moon's body.
    pad = 6
    draw.ellipse((pad, pad, size - pad, size - pad), fill=color)
    # Offset circle carved out to form the crescent.
    off = size // 4
    draw.ellipse(
        (pad + off, pad - 2, size - pad + off, size - pad - 2),
        fill=(0, 0, 0, 0),
    )
    return img


def load_icon(state: IconState) -> Image.Image:
    """Return a PIL image for the given state, preferring packaged assets."""
    asset_path = ASSETS_DIR / f"icon_{state.value}.ico"
    if asset_path.exists():
        try:
            return Image.open(asset_path)
        except Exception as exc:
            log.warning("Failed to load %s: %s", asset_path, exc)
    return _draw_moon(_ICON_COLORS[state])


# ---- tray app --------------------------------------------------------------


class SandmanTray:
    """Owns the pystray Icon and wires menu callbacks back to the app."""

    def __init__(
        self,
        *,
        on_open_settings: Callable[[], None],
        on_pause_30: Callable[[], None],
        on_pause_tomorrow: Callable[[], None],
        on_resume: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._on_open_settings = on_open_settings
        self._on_pause_30 = on_pause_30
        self._on_pause_tomorrow = on_pause_tomorrow
        self._on_resume = on_resume
        self._on_quit = on_quit

        self._icon = None  # type: ignore[assignment]
        self._current_status: MonitorStatus = MonitorStatus()

    # ---- pystray wiring -------------------------------------------------

    def _build_icon(self):
        import pystray

        def _status_title(_item):
            s = self._current_status
            if not s.aw_connected and s.state == MonitorState.ERROR:
                return f"Status: {s.message or 'ActivityWatch not connected'}"
            return f"Status: {s.message or s.state.value}"

        menu = pystray.Menu(
            pystray.MenuItem(_status_title, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings...", lambda _i, _it: self._on_open_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause for 30 min", lambda _i, _it: self._on_pause_30()),
            pystray.MenuItem(
                "Pause until tomorrow", lambda _i, _it: self._on_pause_tomorrow()
            ),
            pystray.MenuItem("Resume", lambda _i, _it: self._on_resume()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda _i, _it: self._quit()),
        )
        icon = pystray.Icon(
            "sandman",
            icon=load_icon(IconState.IDLE),
            title="Sandman",
            menu=menu,
        )
        return icon

    def run(self) -> None:
        """Blocks on the main thread — pystray owns the event loop."""
        self._icon = self._build_icon()
        self._icon.run()

    def _quit(self) -> None:
        try:
            self._on_quit()
        finally:
            if self._icon is not None:
                self._icon.stop()

    # ---- external updates ----------------------------------------------

    def update_state(self, status: MonitorStatus) -> None:
        """Called from the monitor thread to refresh icon + tooltip."""
        self._current_status = status
        if self._icon is None:
            return
        icon_state = _state_from_monitor(status)
        try:
            self._icon.icon = load_icon(icon_state)
            tooltip = f"Sandman — {status.message or status.state.value}"
            self._icon.title = tooltip
            self._icon.update_menu()
        except Exception:
            log.exception("Failed to update tray state")
