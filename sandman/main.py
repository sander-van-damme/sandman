"""Sandman entry point — wires the tray, monitor, settings and LLM together.

The tray (pystray) runs on the main thread, the monitor polls on a
background thread, and any tkinter UI (settings, reply chat) is created
ad-hoc by scheduling work back onto the main thread via a small task
queue that the tray polls while running.
"""

from __future__ import annotations

import logging
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
    ACTION_BED,
    ACTION_REPLY,
    ACTION_SNOOZE,
    ReplyWindow,
    ToastAction,
    show_nudge_toast,
)
from .settings import SettingsWindow
from .tray import SandmanTray

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


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
        )

        self.tray = SandmanTray(
            on_open_settings=lambda: self._ui_call(self._open_settings),
            on_open_chat=lambda: self._ui_call(self._open_chat),
            on_pause_30=lambda: self.monitor.pause_for(30),
            on_pause_tomorrow=self.monitor.pause_until_tomorrow,
            on_resume=self.monitor.resume,
            on_quit=self._on_quit,
        )

        # Tasks that must execute on the tkinter/main thread.
        self._ui_queue: queue.Queue[Callable[[], Any]] = queue.Queue()
        self._reply_window: ReplyWindow | None = None
        self._stopping = False

    # ---- main-thread task pump -----------------------------------------

    def _ui_call(self, func: Callable[[], Any]) -> None:
        """Schedule ``func`` to run on the main thread."""
        self._ui_queue.put(func)

    def _ui_pump(self) -> None:
        """Called periodically from a tiny helper thread to drain the UI queue.

        We can't easily inject into pystray's event loop, so instead we run
        a lightweight dispatcher thread that sleeps on the queue and then
        spawns the Tk work on *itself* — tkinter will create a new thread-
        local root as needed. For longer-lived windows (settings/chat) this
        is acceptable since tkinter tolerates being driven from a single
        consistent worker thread.
        """
        import time

        while not self._stopping:
            try:
                task = self._ui_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                task()
            except Exception:
                log.exception("UI task failed")
            time.sleep(0.05)

    # ---- monitor callbacks ---------------------------------------------

    def _on_status(self, status: MonitorStatus) -> None:
        self.tray.update_state(status)

    def _on_nudge(self, decision: NudgeDecision) -> None:
        # Mirror the nudge into the reply window if it's open.
        if self._reply_window is not None:
            self._reply_window.queue_sandman_message(decision.message)

        show_nudge_toast(
            decision.message,
            title="Sandman",
            on_action=self._on_toast_action,
        )

        # Escalation overlay at 7+ nudges.
        if (
            self.config.notifications.get("escalation_enabled", True)
            and self.monitor.status.nudge_count >= 7
        ):
            self._ui_call(lambda: self._show_escalation_overlay(decision.message))

    def _on_toast_action(self, action: ToastAction) -> None:
        log.info("Toast action: %s", action.action)
        if action.action == ACTION_BED:
            self.monitor.pause_until_tomorrow()
        elif action.action == ACTION_SNOOZE:
            self.monitor.pause_for(5)
        elif action.action == ACTION_REPLY:
            self._ui_call(self._open_chat)
            if action.user_input:
                self._ui_call(
                    lambda text=action.user_input: self._handle_chat_reply(text)
                )

    # ---- UI openers -----------------------------------------------------

    def _open_settings(self) -> None:
        def _on_saved(cfg: Config) -> None:
            # Rebuild LLM client if key/model changed.
            self.llm_client.api_key = cfg.api_key
            self.llm_client.model = cfg.model
            self.llm_client._client = None  # type: ignore[attr-defined]
            # Reset bucket cache in case hostname shifted.
            self.aw_client.reset_bucket_cache()
            log.info("Settings saved — applied to live config")

        window = SettingsWindow(
            self.config, aw_client=self.aw_client, on_saved=_on_saved
        )
        window.open()

    def _open_chat(self) -> None:
        if self._reply_window is None:
            self._reply_window = ReplyWindow(on_user_reply=self._handle_chat_reply)
        self._reply_window.open(
            initial_sandman_message=(
                "Hey — I'm here. Tell me what's keeping you up."
                if not self._reply_window._incoming.qsize()
                else None
            )
        )

    def _handle_chat_reply(self, text: str) -> None:
        log.info("User reply: %s", text)
        decision = self.monitor.handle_user_reply(text)
        if self._reply_window is not None and decision.message:
            self._reply_window.queue_sandman_message(decision.message)

    def _show_escalation_overlay(self, message: str) -> None:
        """Full-screen-ish overlay for high nudge counts."""
        import tkinter as tk

        try:
            root = tk.Tk()
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.85)
            root.overrideredirect(True)
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            w = int(sw * 0.5)
            h = int(sh * 0.3)
            x = (sw - w) // 2
            y = (sh - h) // 2
            root.geometry(f"{w}x{h}+{x}+{y}")
            root.configure(bg="#101018")

            label = tk.Label(
                root,
                text=message,
                wraplength=w - 40,
                fg="#ffffff",
                bg="#101018",
                font=("Segoe UI", 18, "bold"),
                justify="center",
            )
            label.pack(expand=True, fill="both", padx=20, pady=20)

            btn = tk.Button(
                root,
                text="OK, I'll go to bed",
                command=root.destroy,
                bg="#2a2a3a",
                fg="#ffffff",
                bd=0,
                padx=20,
                pady=8,
            )
            btn.pack(pady=(0, 20))
            root.mainloop()
        except Exception:
            log.exception("Failed to show escalation overlay")

    # ---- lifecycle ------------------------------------------------------

    def run(self) -> int:
        _setup_logging()
        log.info("Starting Sandman")

        # First-run: open settings if no API key yet.
        if not self.config.is_configured():
            log.info("No API key configured — opening settings on first run")
            self._ui_call(self._open_settings)

        # Start monitor thread.
        self.monitor.start()

        # Start UI task pump thread.
        ui_thread = threading.Thread(
            target=self._ui_pump, name="sandman-ui", daemon=True
        )
        ui_thread.start()

        # Tray takes over the main thread until Quit.
        try:
            self.tray.run()
        finally:
            self._stopping = True
            self.monitor.stop()
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
