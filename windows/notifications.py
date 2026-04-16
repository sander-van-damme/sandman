"""Windows toast notifications and the tkinter reply window.

``notify`` wraps ``win11toast`` so the rest of the app doesn't have to care
about which Windows toast library is available. The reply window
(``ReplyWindow``) is a small always-on-top tkinter chat UI where the user
can have a back-and-forth with Sandman.
"""

from __future__ import annotations

import logging
import platform
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Callable

log = logging.getLogger(__name__)


# ---- toast notifications --------------------------------------------------


# Button identifiers returned by the toast. These strings double as the
# "arguments" attached to each action button, which is what win11toast hands
# us back through its callback.
ACTION_BED = "sandman:bed"
ACTION_SNOOZE = "sandman:snooze5"


@dataclass
class ToastAction:
    """Represents a click on one of the toast action buttons."""

    action: str
    user_input: str | None = None


ToastCallback = Callable[[ToastAction], None]


def show_nudge_toast(
    message: str,
    *,
    title: str = "Sandman",
    on_action: ToastCallback | None = None,
) -> None:
    """Display a persistent toast with Bed / Snooze buttons.

    On non-Windows platforms this is a no-op that just logs the message,
    which keeps development and tests portable.
    """
    if platform.system() != "Windows":
        log.info("[toast stub] %s: %s", title, message)
        return

    try:
        from win11toast import toast  # type: ignore
    except Exception as exc:  # pragma: no cover - import-time
        log.warning("win11toast unavailable: %s — falling back to log", exc)
        log.info("[toast] %s: %s", title, message)
        return

    buttons = [
        {"activationType": "background", "content": "I'm going to bed", "arguments": ACTION_BED},
        {"activationType": "background", "content": "5 more minutes", "arguments": ACTION_SNOOZE},
    ]

    def _callback(args: dict[str, Any]) -> None:
        if on_action is None:
            return
        action = str(args.get("arguments", "") or "")
        user_input = None
        inputs = args.get("user_input") or {}
        if isinstance(inputs, dict) and inputs:
            # Grab the first free-form input value, if any.
            user_input = next(iter(inputs.values()), None)
        try:
            on_action(ToastAction(action=action, user_input=user_input))
        except Exception:  # pragma: no cover - user callback
            log.exception("Toast action callback failed")

    try:
        log.info("Sending win11toast: %r", message[:120] if message else "")
        toast(
            title,
            message,
            buttons=buttons,
            on_click=_callback,
            duration="long",  # keeps it in Action Center
        )
        log.info("Toast displayed successfully")
    except Exception as exc:  # pragma: no cover - toast runtime
        log.warning("Failed to show toast: %s", exc)


# ---- reply window ---------------------------------------------------------


class ReplyWindow:
    """Small always-on-top tkinter chat window for replying to Sandman.

    Designed to run on the main thread (tkinter's requirement). To send a
    reply from another thread, push a message onto the queue via
    ``queue_sandman_message``.

    The caller supplies ``on_user_reply``, which is invoked synchronously on
    the tkinter thread with the user's text. The callback is expected to
    produce Sandman's response and push it back via
    ``queue_sandman_message``.
    """

    def __init__(
        self,
        *,
        on_user_reply: Callable[[str], None],
        parent: tk.Misc | None = None,
        ui_scale: float = 1.0,
    ) -> None:
        self._on_user_reply = on_user_reply
        self._parent = parent
        self._ui_scale = max(1.0, float(ui_scale))
        self._root: tk.Toplevel | tk.Tk | None = None
        self._transcript: tk.Text | None = None
        self._entry: ttk.Entry | None = None
        self._incoming: queue.Queue[tuple[str, str]] = queue.Queue()
        self._lock = threading.Lock()

    # ---- lifecycle ------------------------------------------------------

    def open(self, initial_sandman_message: str | None = None) -> None:
        """Create and show the window. Safe to call more than once."""
        if self._root is not None:
            try:
                self._root.deiconify()
                self._root.lift()
                self._root.focus_force()
                if initial_sandman_message:
                    self._append("sandman", initial_sandman_message)
                return
            except tk.TclError:
                # Window was destroyed — rebuild it below.
                self._root = None

        if self._parent is None:
            root: tk.Tk | tk.Toplevel = tk.Tk()
            root.withdraw()  # hide the default root; we'll use a Toplevel
            top = tk.Toplevel(root)
            self._owns_root = True
            self._hidden_root = root
        else:
            top = tk.Toplevel(self._parent)
            self._owns_root = False
            self._hidden_root = None  # type: ignore[assignment]

        w = int(320 * self._ui_scale)
        h = int(440 * self._ui_scale)

        top.title("Sandman")
        top.geometry(f"{w}x{h}")
        top.minsize(int(260 * self._ui_scale), int(320 * self._ui_scale))
        top.attributes("-topmost", True)
        top.protocol("WM_DELETE_WINDOW", self.close)

        # Position bottom-right of the primary screen.
        top.update_idletasks()
        sw = top.winfo_screenwidth()
        sh = top.winfo_screenheight()
        x = max(0, sw - w - 20)
        y = max(0, sh - h - 60)
        top.geometry(f"{w}x{h}+{x}+{y}")

        # Container so we can rely on grid for a clean bottom-entry layout.
        container = ttk.Frame(top)
        container.pack(fill="both", expand=True)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        transcript_frame = ttk.Frame(container)
        transcript_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 0))
        transcript_frame.rowconfigure(0, weight=1)
        transcript_frame.columnconfigure(0, weight=1)

        transcript = tk.Text(
            transcript_frame,
            wrap="word",
            state="disabled",
            bg="#1e1e2f",
            fg="#e6e6f0",
            padx=8,
            pady=8,
            bd=0,
            highlightthickness=0,
            font=("Segoe UI", 10),
        )
        transcript.tag_configure(
            "sandman",
            justify="left",
            foreground="#a0c4ff",
            spacing3=4,
        )
        transcript.tag_configure(
            "user",
            justify="right",
            foreground="#ffd6a5",
            spacing3=4,
        )
        transcript.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            transcript_frame, orient="vertical", command=transcript.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        transcript.configure(yscrollcommand=scrollbar.set)

        entry_frame = ttk.Frame(container)
        entry_frame.grid(row=1, column=0, sticky="ew", padx=6, pady=6)
        entry_frame.columnconfigure(0, weight=1)
        entry = ttk.Entry(entry_frame)
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _e: self._send_from_entry())
        send_btn = ttk.Button(
            entry_frame, text="Send", command=self._send_from_entry
        )
        send_btn.grid(row=0, column=1, padx=(6, 0))
        entry.focus_set()

        self._root = top
        self._transcript = transcript
        self._entry = entry

        if initial_sandman_message:
            self._append("sandman", initial_sandman_message)

        # Start pumping the cross-thread message queue.
        top.after(100, self._drain_queue)

    def close(self) -> None:
        if self._root is None:
            return
        try:
            self._root.destroy()
        except tk.TclError:
            pass
        self._root = None
        self._transcript = None
        self._entry = None
        if getattr(self, "_hidden_root", None) is not None:
            try:
                self._hidden_root.destroy()  # type: ignore[union-attr]
            except tk.TclError:
                pass
            self._hidden_root = None  # type: ignore[assignment]

    # ---- thread-safe ingestion -----------------------------------------

    def queue_sandman_message(self, message: str) -> None:
        """Enqueue a message from Sandman. Safe to call from any thread."""
        log.info("Queuing sandman message for chat window: %r", message[:120] if message else "")
        self._incoming.put(("sandman", message))

    def queue_user_message(self, message: str) -> None:
        self._incoming.put(("user", message))

    def _drain_queue(self) -> None:
        if self._root is None:
            return
        try:
            while True:
                role, msg = self._incoming.get_nowait()
                self._append(role, msg)
        except queue.Empty:
            pass
        try:
            self._root.after(150, self._drain_queue)
        except tk.TclError:
            pass

    # ---- internal helpers ----------------------------------------------

    def _send_from_entry(self) -> None:
        if self._entry is None:
            return
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, tk.END)
        self._append("user", text)
        try:
            self._on_user_reply(text)
        except Exception:
            log.exception("on_user_reply handler failed")

    def _append(self, role: str, message: str) -> None:
        if self._transcript is None:
            return
        prefix = "Sandman:" if role == "sandman" else "You:"
        self._transcript.configure(state="normal")
        self._transcript.insert(tk.END, f"{prefix} {message}\n\n", role)
        self._transcript.see(tk.END)
        self._transcript.configure(state="disabled")
