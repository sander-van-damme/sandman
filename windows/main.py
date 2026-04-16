"""Sandman entry point — wires the tray, monitor, settings and LLM together.

The tray (pystray) runs on the main thread, the monitor polls on a
background thread, and a dedicated UI thread owns a persistent hidden
tkinter root and drives the event loop for every window (settings,
chat, overlays). Other threads schedule work on the UI thread via the
``_ui_call`` queue which is drained from within ``mainloop``.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import queue
import sys
import threading
from datetime import datetime
from typing import Any, Callable

from .activity_watch import ActivityWatchClient
from .config import Config
from .llm import LLMClient, NudgeDecision
from .monitor import Monitor, MonitorStatus
from .notifications import (
    ReplyWindow,
)
from .settings import SettingsWindow
from .tray import SandmanTray

log = logging.getLogger(__name__)


def _set_log_level(debug_enabled: bool) -> None:
    level = logging.DEBUG if debug_enabled else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)


def _setup_logging(*, debug_enabled: bool) -> None:
    from pathlib import Path

    log_dir = Path.home() / ".sandman"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "sandman.log"

    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=1 * 1024 * 1024,  # 1 MB
                backupCount=2,
                encoding="utf-8",
            ),
        ],
    )
    _set_log_level(debug_enabled)


class SandmanApp:
    """Glues all the pieces together. Lives for the duration of the process."""

    def __init__(self) -> None:
        self.config = Config.load()
        self.aw_client = ActivityWatchClient()
        self.llm_client = LLMClient(
            api_key=self.config.api_key, model=self.config.model
        )

        self.monitor = Monitor(
            config=self.config,
            aw_client=self.aw_client,
            llm_client=self.llm_client,
            on_nudge=self._on_nudge,
            on_status=self._on_status,
            is_alert_open=lambda: self._reply_window is not None and self._reply_window._root is not None,
        )

        self.tray = SandmanTray(
            on_open_settings=lambda: self._ui_call(self._open_settings),
            on_quit=self._on_quit,
        )

        # Tasks that must execute on the Tk UI thread.
        self._ui_queue: queue.Queue[Callable[[], Any]] = queue.Queue()
        self._reply_window: ReplyWindow | None = None
        self._settings_window: SettingsWindow | None = None
        self._tk_root: Any = None  # tk.Tk, lazily created on the UI thread
        self._ui_ready = threading.Event()
        self._ui_scale: float = 1.0
        self._stopping = False

    # ---- UI thread task pump -------------------------------------------

    def _ui_call(self, func: Callable[[], Any]) -> None:
        """Schedule ``func`` to run on the Tk UI thread."""
        self._ui_queue.put(func)

    def _ui_pump(self) -> None:
        """Own a persistent hidden Tk root and run its mainloop.

        All windows (settings, chat, escalation overlay) are Toplevels
        parented to this hidden root so a single event loop drives the
        whole UI. Work scheduled via ``_ui_call`` from other threads is
        drained from within the event loop via ``after`` polling, which
        means tkinter calls always happen on this one thread.
        """
        import tkinter as tk

        # Per-monitor DPI awareness so tkinter renders crisp on high-DPI
        # displays. Must be set before any Tk window is created. No-op
        # off Windows.
        if platform.system() == "Windows":
            try:
                import ctypes

                try:
                    # PROCESS_PER_MONITOR_DPI_AWARE (Win 8.1+)
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)
                except Exception:
                    ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                log.debug("Could not enable DPI awareness", exc_info=True)

        try:
            root = tk.Tk()
        except Exception:
            log.exception("Failed to create Tk root — UI disabled")
            self._ui_ready.set()
            return

        root.withdraw()

        # Scale Tk (fonts, ttk widgets) to display DPI. ``winfo_fpixels``
        # returns pixels per inch — Tk's native scaling unit is 1/72".
        try:
            dpi = float(root.winfo_fpixels("1i"))
            if dpi > 0:
                root.tk.call("tk", "scaling", dpi / 72.0)
                self._ui_scale = max(1.0, dpi / 96.0)
        except Exception:
            self._ui_scale = 1.0

        # Prefer the native Windows theme for ttk widgets; it's faster
        # and matches the rest of the OS.
        try:
            from tkinter import ttk

            style = ttk.Style(root)
            for theme in ("vista", "xpnative", "winnative", "clam"):
                if theme in style.theme_names():
                    style.theme_use(theme)
                    break
        except Exception:
            log.debug("Could not set ttk theme", exc_info=True)

        self._tk_root = root
        self._ui_ready.set()

        def _drain() -> None:
            if self._stopping:
                try:
                    root.quit()
                except Exception:
                    pass
                return
            try:
                while True:
                    task = self._ui_queue.get_nowait()
                    try:
                        task()
                    except Exception:
                        log.exception("UI task failed")
            except queue.Empty:
                pass
            try:
                root.after(50, _drain)
            except tk.TclError:
                pass

        root.after(50, _drain)

        try:
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:
                pass
            self._tk_root = None

    # ---- monitor callbacks ---------------------------------------------

    def _on_status(self, status: MonitorStatus) -> None:
        self.tray.update_state(status)

    def _on_nudge(self, decision: NudgeDecision) -> None:
        log.info(
            "on_nudge called: activity=%s, message=%r, reply_window_open=%s",
            decision.activity_type,
            decision.message[:120] if decision.message else "",
            self._reply_window is not None,
        )
        # Mirror the nudge into the reply window if it's open.
        if self._reply_window is not None:
            self._reply_window.queue_sandman_message(decision.message)
            log.info("Opening center popup notification")
            # Don't pass the message here — it's already queued above and _drain_queue
            # will display it. Passing it would cause open() to _append it directly,
            # resulting in the message appearing twice.
            self._ui_call(lambda: self._open_chat())
        else:
            log.info("Opening center popup notification")
            self._ui_call(lambda: self._open_chat(decision.message))

        # Escalation overlay at 7+ nudges.
        if (
            self.config.notifications.get("escalation_enabled", True)
            and self.monitor.status.nudge_count >= 7
        ):
            log.info("Showing escalation overlay (nudge_count=%d)", self.monitor.status.nudge_count)
            self._ui_call(lambda: self._show_escalation_overlay(decision.message))

    # ---- UI openers -----------------------------------------------------

    def _open_settings(self) -> None:
        # If a settings window is already open, just bring it to the front.
        if self._settings_window is not None and self._settings_window._root is not None:
            try:
                self._settings_window._root.lift()
                self._settings_window._root.focus_force()
                return
            except Exception:
                self._settings_window = None

        def _on_saved(cfg: Config) -> None:
            # Rebuild LLM client if key/model changed.
            self.llm_client.api_key = cfg.api_key
            self.llm_client.model = cfg.model
            self.llm_client._client = None  # type: ignore[attr-defined]
            _set_log_level(bool(cfg.data.get("debug_logging", False)))
            # Reset bucket cache in case hostname shifted.
            self.aw_client.reset_bucket_cache()
            self._settings_window = None
            log.info("Settings saved — applied to live config")

        def _on_close() -> None:
            self._settings_window = None

        window = SettingsWindow(
            self.config,
            aw_client=self.aw_client,
            on_saved=_on_saved,
            on_close=_on_close,
            parent=self._tk_root,
            ui_scale=self._ui_scale,
        )
        self._settings_window = window
        window.open()

    def _open_chat(self, initial_message: str | None = None) -> None:
        if self._reply_window is None:
            self._reply_window = ReplyWindow(
                on_user_reply=self._handle_chat_reply,
                parent=self._tk_root,
                ui_scale=self._ui_scale,
            )
        self._reply_window.open(
            initial_sandman_message=(
                initial_message
                or (
                    "Hey — I'm here. Tell me what's keeping you up."
                    if not self._reply_window._incoming.qsize()
                    else None
                )
            )
        )

    def _handle_chat_reply(self, text: str) -> None:
        log.info("User reply: %s", text)
        decision = self.monitor.handle_user_reply(text)
        if self._reply_window is not None and decision.message:
            log.info("Queuing Sandman response to chat: %r", decision.message[:120])
            self._reply_window.queue_sandman_message(decision.message)
        elif not decision.message:
            log.warning("No message in LLM response to user reply — nothing to display")

    def _show_escalation_overlay(self, message: str) -> None:
        """Full-screen-ish overlay for high nudge counts."""
        import tkinter as tk

        if self._tk_root is None:
            return
        try:
            overlay = tk.Toplevel(self._tk_root)
            overlay.attributes("-topmost", True)
            overlay.attributes("-alpha", 0.85)
            overlay.overrideredirect(True)
            sw = overlay.winfo_screenwidth()
            sh = overlay.winfo_screenheight()
            w = int(sw * 0.5)
            h = int(sh * 0.3)
            x = (sw - w) // 2
            y = (sh - h) // 2
            overlay.geometry(f"{w}x{h}+{x}+{y}")
            overlay.configure(bg="#101018")

            label = tk.Label(
                overlay,
                text=message,
                wraplength=w - 40,
                fg="#ffffff",
                bg="#101018",
                font=("Segoe UI", 18, "bold"),
                justify="center",
            )
            label.pack(expand=True, fill="both", padx=20, pady=20)

            btn = tk.Button(
                overlay,
                text="OK, I'll go to bed",
                command=overlay.destroy,
                bg="#2a2a3a",
                fg="#ffffff",
                bd=0,
                padx=20,
                pady=8,
            )
            btn.pack(pady=(0, 20))
        except Exception:
            log.exception("Failed to show escalation overlay")

    # ---- lifecycle ------------------------------------------------------

    def run(self) -> int:
        _setup_logging(debug_enabled=bool(self.config.data.get("debug_logging", False)))
        log.info("Starting Sandman")

        # Start UI thread first so the Tk root exists before anything
        # tries to open a window on it.
        ui_thread = threading.Thread(
            target=self._ui_pump, name="sandman-ui", daemon=True
        )
        ui_thread.start()
        # Wait briefly for the Tk root to come up so the first UI call
        # doesn't race ahead of it.
        self._ui_ready.wait(timeout=5.0)

        # First-run: open settings if no API key yet.
        if not self.config.is_configured():
            log.info("No API key configured — opening settings on first run")
            self._ui_call(self._open_settings)

        # Start monitor thread.
        self.monitor.start()

        # Tray takes over the main thread until Quit.
        try:
            self.tray.run()
        finally:
            self._stopping = True
            self.monitor.stop()
            # Give the UI thread a chance to tear down its Tk root.
            ui_thread.join(timeout=2.0)
            log.info("Sandman exited")
        return 0

    def _on_quit(self) -> None:
        self._stopping = True
        self.monitor.stop()


def main() -> int:
    app = SandmanApp()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
