"""Tkinter-based nudge popup and reply chat UI."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

log = logging.getLogger(__name__)


# ---- nudge popup ----------------------------------------------------------


class ReplyWindow:
    """Elegant always-on-top popup with chat.

    Designed to run on the main thread (tkinter's requirement). To send a
    reply from another thread, push a message onto the queue via
    ``queue_sandman_message``.

    The caller supplies ``on_user_reply``, which is invoked on a background
    thread with the user's text. The callback is expected to
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
        self._send_btn: ttk.Button | None = None
        self._incoming: queue.Queue[tuple[str, str]] = queue.Queue()
        self._lock = threading.Lock()
        self._awaiting_response = False
        self._typing_dots = 1
        self._typing_job_id: str | None = None
        self._typing_index: str | None = None

    def _apply_styles(self, top: tk.Toplevel) -> ttk.Style:
        """Apply a modern dark theme to the popup."""
        style = ttk.Style(top)
        try:
            style.theme_use("clam")
        except tk.TclError:
            # Fallback to whichever theme is available.
            pass

        base = max(10, int(10 * self._ui_scale))
        title = max(14, int(16 * self._ui_scale))

        font_body = ("Segoe UI", base)
        font_title = ("Segoe UI Semibold", title)

        colors = {
            "bg": "#0f172a",
            "surface": "#111827",
            "surface_alt": "#1f2937",
            "fg": "#e5e7eb",
            "muted": "#9ca3af",
            "accent": "#7c3aed",
            "accent_active": "#6d28d9",
            "accent_text": "#f5f3ff",
            "border": "#334155",
        }

        top.configure(bg=colors["bg"])

        style.configure(
            "Nudge.Root.TFrame",
            background=colors["bg"],
        )
        style.configure(
            "Nudge.Card.TFrame",
            background=colors["surface"],
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Nudge.Header.TFrame",
            background=colors["surface"],
        )
        style.configure(
            "Nudge.Compose.TFrame",
            background=colors["surface_alt"],
        )
        style.configure(
            "Nudge.Title.TLabel",
            background=colors["surface"],
            foreground=colors["fg"],
            font=font_title,
        )
        style.configure(
            "Nudge.Subtitle.TLabel",
            background=colors["surface"],
            foreground=colors["muted"],
            font=font_body,
        )
        style.configure(
            "Nudge.TButton",
            font=font_body,
            padding=(12, 7),
            background=colors["accent"],
            foreground=colors["accent_text"],
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Nudge.TButton",
            background=[
                ("active", colors["accent_active"]),
                ("pressed", colors["accent_active"]),
                ("disabled", colors["surface_alt"]),
            ],
            foreground=[("disabled", colors["muted"])],
        )
        style.configure(
            "Nudge.Secondary.TButton",
            font=font_body,
            padding=(10, 6),
            background=colors["surface_alt"],
            foreground=colors["fg"],
            borderwidth=1,
            relief="solid",
        )
        style.map(
            "Nudge.Secondary.TButton",
            background=[("active", "#273549"), ("pressed", "#273549")],
            bordercolor=[("!disabled", colors["border"])],
            foreground=[("disabled", colors["muted"])],
        )
        style.configure(
            "Nudge.TEntry",
            fieldbackground=colors["surface"],
            foreground=colors["fg"],
            insertcolor=colors["fg"],
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
            padding=(10, 7),
            relief="flat",
        )
        style.map(
            "Nudge.TEntry",
            bordercolor=[("focus", colors["accent"])],
            lightcolor=[("focus", colors["accent"])],
            darkcolor=[("focus", colors["accent"])],
        )
        return style

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

        w = int(560 * self._ui_scale)
        h = int(500 * self._ui_scale)

        top.title("Sandman")
        top.geometry(f"{w}x{h}")
        top.minsize(int(260 * self._ui_scale), int(320 * self._ui_scale))
        top.attributes("-topmost", True)
        top.protocol("WM_DELETE_WINDOW", self.close)
        self._apply_styles(top)

        # Position center-screen.
        top.update_idletasks()
        sw = top.winfo_screenwidth()
        sh = top.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        top.geometry(f"{w}x{h}+{x}+{y}")

        # Container so we can rely on grid for a clean bottom-entry layout.
        container = ttk.Frame(top, style="Nudge.Root.TFrame")
        container.pack(fill="both", expand=True)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        card = ttk.Frame(container, style="Nudge.Card.TFrame", padding=14)
        card.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)

        header = ttk.Frame(card, style="Nudge.Header.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 10))
        header.columnconfigure(0, weight=1)
        title = ttk.Label(
            header,
            text="It's wind-down time 🌙",
            style="Nudge.Title.TLabel",
        )
        title.grid(row=0, column=0, sticky="w")
        subtitle = ttk.Label(
            header,
            text="Take a breath. A quick check-in before bedtime.",
            style="Nudge.Subtitle.TLabel",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))
        transcript_frame = ttk.Frame(card, style="Nudge.Compose.TFrame")
        transcript_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        transcript_frame.rowconfigure(0, weight=1)
        transcript_frame.columnconfigure(0, weight=1)

        transcript = tk.Text(
            transcript_frame,
            wrap="word",
            state="disabled",
            bg="#0b1220",
            fg="#e5e7eb",
            padx=10,
            pady=10,
            bd=0,
            highlightthickness=0,
            font=("Segoe UI", max(10, int(10 * self._ui_scale))),
        )
        transcript.tag_configure(
            "sandman",
            justify="left",
            foreground="#bfdbfe",
            spacing3=4,
        )
        transcript.tag_configure(
            "user",
            justify="right",
            foreground="#fbcfe8",
            spacing3=4,
        )
        transcript.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            transcript_frame, orient="vertical", command=transcript.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        transcript.configure(yscrollcommand=scrollbar.set)

        entry_frame = ttk.Frame(card, style="Nudge.Compose.TFrame", padding=8)
        entry_frame.grid(row=2, column=0, sticky="ew")
        entry_frame.columnconfigure(0, weight=1)
        entry = ttk.Entry(entry_frame, style="Nudge.TEntry")
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _e: self._send_from_entry())
        send_btn = ttk.Button(
            entry_frame,
            text="Send",
            command=self._send_from_entry,
            style="Nudge.Secondary.TButton",
        )
        send_btn.grid(row=0, column=1, padx=(6, 0))
        entry.focus_set()

        self._root = top
        self._transcript = transcript
        self._entry = entry
        self._send_btn = send_btn

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
        self._send_btn = None
        self._awaiting_response = False
        self._typing_dots = 1
        self._typing_index = None
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
                if role == "sandman":
                    self._stop_waiting_for_response()
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
        self._start_waiting_for_response()

        def _send() -> None:
            try:
                self._on_user_reply(text)
            except Exception:
                log.exception("on_user_reply handler failed")
                if self._root is not None:
                    try:
                        self._root.after(0, self._stop_waiting_for_response)
                    except tk.TclError:
                        pass

        threading.Thread(target=_send, daemon=True).start()

    def _append(self, role: str, message: str) -> None:
        if self._transcript is None:
            return
        prefix = "Sandman:" if role == "sandman" else "You:"
        self._transcript.configure(state="normal")
        self._transcript.insert(tk.END, f"{prefix} {message}\n\n", role)
        self._transcript.see(tk.END)
        self._transcript.configure(state="disabled")

    def _start_waiting_for_response(self) -> None:
        if self._awaiting_response:
            return
        self._awaiting_response = True
        self._typing_dots = 1
        if self._entry is not None:
            self._entry.configure(state="disabled")
        if self._send_btn is not None:
            self._send_btn.configure(text="Sending...", state="disabled")
        self._show_typing_indicator()

    def _stop_waiting_for_response(self) -> None:
        if not self._awaiting_response:
            return
        self._awaiting_response = False
        if self._entry is not None:
            self._entry.configure(state="normal")
            self._entry.focus_set()
        if self._send_btn is not None:
            self._send_btn.configure(text="Send", state="normal")
        self._remove_typing_indicator()

    def _show_typing_indicator(self) -> None:
        if self._transcript is None or self._root is None:
            return
        self._transcript.configure(state="normal")
        if self._typing_index is None:
            self._typing_index = self._transcript.index("end-1c")
            self._transcript.insert("end-1c", "Sandman is typing.\n\n", "sandman")
        self._transcript.see(tk.END)
        self._transcript.configure(state="disabled")
        self._typing_job_id = self._root.after(350, self._animate_typing_indicator)

    def _animate_typing_indicator(self) -> None:
        if (
            self._transcript is None
            or self._root is None
            or self._typing_index is None
            or not self._awaiting_response
        ):
            return
        dots = "." * self._typing_dots
        self._typing_dots = (self._typing_dots % 3) + 1
        self._transcript.configure(state="normal")
        self._transcript.delete(self._typing_index, f"{self._typing_index} lineend")
        self._transcript.insert(self._typing_index, f"Sandman is typing{dots}", "sandman")
        self._transcript.see(tk.END)
        self._transcript.configure(state="disabled")
        self._typing_job_id = self._root.after(350, self._animate_typing_indicator)

    def _remove_typing_indicator(self) -> None:
        if self._root is not None and self._typing_job_id is not None:
            try:
                self._root.after_cancel(self._typing_job_id)
            except tk.TclError:
                pass
        self._typing_job_id = None
        if self._transcript is not None and self._typing_index is not None:
            self._transcript.configure(state="normal")
            self._transcript.delete(self._typing_index, f"{self._typing_index}+2l")
            self._transcript.configure(state="disabled")
        self._typing_index = None
