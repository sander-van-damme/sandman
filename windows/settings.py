"""The Sandman settings window (tkinter).

Built as a standalone Toplevel so it can be opened from the tray menu or
automatically on first run. Values are validated on Save before being
written back to the ``Config`` object.
"""

from __future__ import annotations

import logging
import re
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from typing import Callable

from .activity_watch import ActivityWatchClient
from .config import NUDGE_STYLES, Config

log = logging.getLogger(__name__)


DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MODELS = ("gpt-5-nano", "gpt-4o-mini", "gpt-4o", "gpt-4.1-nano")
NUDGE_STYLE_LABELS = {
    "gentle": "Gentle and supportive",
    "direct": "Direct and firm",
    "humor": "Use humor",
    "therapist": "Coach me like a therapist",
}

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _valid_time(s: str) -> bool:
    if not _TIME_RE.match(s):
        return False
    hh, mm = s.split(":")
    return 0 <= int(hh) < 24 and 0 <= int(mm) < 60


class SettingsWindow:
    """Modal-ish settings window. Call ``open`` once per app."""

    def __init__(
        self,
        config: Config,
        *,
        aw_client: ActivityWatchClient | None = None,
        on_saved: Callable[[Config], None] | None = None,
        on_close: Callable[[], None] | None = None,
        parent: tk.Misc | None = None,
        ui_scale: float = 1.0,
    ) -> None:
        self.config = config
        self.aw_client = aw_client or ActivityWatchClient()
        self.on_saved = on_saved
        self.on_close = on_close
        self._parent = parent
        self._ui_scale = max(1.0, float(ui_scale))
        self._root: tk.Toplevel | tk.Tk | None = None
        self._owns_root = False
        self._aw_status_lock = threading.Lock()
        self._aw_status_pending = False
        self._aw_status_last: bool | None = None

        # Tk variables — created in ``open``.
        self.var_api_key: tk.StringVar | None = None
        self.var_model: tk.StringVar | None = None
        self.var_show_key: tk.BooleanVar | None = None
        self.var_active_from: tk.StringVar | None = None
        self.var_active_until: tk.StringVar | None = None
        self.var_wake_time: tk.StringVar | None = None
        self.var_days: list[tk.BooleanVar] = []
        self.var_min_interval: tk.IntVar | None = None
        self.var_escalation: tk.BooleanVar | None = None
        self.var_nudge_style: tk.StringVar | None = None
        self.var_autostart: tk.BooleanVar | None = None
        self.var_debug_logging: tk.BooleanVar | None = None

        self._entry_api_key: tk.Entry | None = None
        self._aw_status_label: ttk.Label | None = None

    # ---- window construction -------------------------------------------

    def open(self) -> None:
        if self._parent is None:
            self._root = tk.Tk()
            self._owns_root = True
        else:
            self._root = tk.Toplevel(self._parent)
            self._owns_root = False

        root = self._root
        root.title("Sandman — Settings")
        w = int(480 * self._ui_scale)
        h = int(640 * self._ui_scale)
        root.geometry(f"{w}x{h}")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._init_vars()

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        self._build_aw_tab(notebook)
        self._build_schedule_tab(notebook)
        self._build_ai_tab(notebook)
        self._build_notifications_tab(notebook)

        btn_frame = ttk.Frame(root)
        btn_frame.pack(fill="x", padx=12, pady=12)
        ttk.Button(btn_frame, text="Save", command=self._on_save).pack(
            side="right"
        )
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(
            side="right", padx=(0, 6)
        )

        # Poll AW status every 2 seconds so the user sees live feedback.
        self._refresh_aw_status()
        root.after(2000, self._schedule_aw_refresh)

        root.lift()
        root.focus_force()

        if self._owns_root:
            root.mainloop()

    # ---- tabs -----------------------------------------------------------

    def _init_vars(self) -> None:
        c = self.config
        self.var_api_key = tk.StringVar(value=c.api_key)
        self.var_model = tk.StringVar(value=c.model)
        self.var_show_key = tk.BooleanVar(value=False)
        self.var_active_from = tk.StringVar(value=c.schedule["active_from"])
        self.var_active_until = tk.StringVar(value=c.schedule["active_until"])
        self.var_wake_time = tk.StringVar(value=c.schedule["wake_time"])
        active_days = set(c.schedule.get("active_days", list(range(7))))
        self.var_days = [
            tk.BooleanVar(value=(i in active_days)) for i in range(7)
        ]
        self.var_min_interval = tk.IntVar(
            value=max(1, int(c.notifications.get("min_interval_seconds", 60) / 60))
        )
        self.var_escalation = tk.BooleanVar(
            value=bool(c.notifications.get("escalation_enabled", True))
        )
        self.var_nudge_style = tk.StringVar(
            value=NUDGE_STYLE_LABELS.get(
                c.notifications.get("nudge_style", "gentle"),
                NUDGE_STYLE_LABELS["gentle"],
            )
        )
        self.var_autostart = tk.BooleanVar(
            value=bool(c.data.get("start_with_windows", False))
        )
        self.var_debug_logging = tk.BooleanVar(
            value=bool(c.data.get("debug_logging", False))
        )

    def _build_aw_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="ActivityWatch")

        ttk.Label(
            frame,
            text="ActivityWatch must be installed and running\nfor Sandman to work.",
            justify="left",
        ).pack(anchor="w")

        self._aw_status_label = ttk.Label(frame, text="Checking...")
        self._aw_status_label.pack(anchor="w", pady=(10, 4))

        ttk.Button(
            frame,
            text="Download ActivityWatch",
            command=lambda: webbrowser.open("https://activitywatch.net/downloads/"),
        ).pack(anchor="w", pady=4)

        ttk.Label(
            frame,
            text=(
                "Sandman reads the aw-watcher-window and aw-watcher-afk\n"
                "buckets from http://localhost:5600."
            ),
            foreground="#666",
            justify="left",
        ).pack(anchor="w", pady=(12, 0))

    def _build_schedule_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="Schedule")

        def _row(label: str, var: tk.StringVar, row: int) -> None:
            ttk.Label(frame, text=label).grid(
                row=row, column=0, sticky="w", pady=4
            )
            ttk.Entry(frame, textvariable=var, width=10).grid(
                row=row, column=1, sticky="w", pady=4
            )

        assert self.var_active_from and self.var_active_until and self.var_wake_time
        _row("Active from (HH:MM)", self.var_active_from, 0)
        _row("Active until (HH:MM)", self.var_active_until, 1)
        _row("Wake-up time (HH:MM)", self.var_wake_time, 2)

        ttk.Label(frame, text="Active days:").grid(
            row=3, column=0, sticky="w", pady=(12, 4)
        )
        days_frame = ttk.Frame(frame)
        days_frame.grid(row=4, column=0, columnspan=2, sticky="w")
        for i, name in enumerate(DAY_NAMES):
            ttk.Checkbutton(days_frame, text=name, variable=self.var_days[i]).pack(
                side="left", padx=2
            )

        ttk.Label(
            frame,
            text=(
                "If 'Active until' is earlier than 'Active from',\n"
                "the window crosses midnight (e.g., 21:30 → 02:00)."
            ),
            foreground="#666",
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(16, 0))

    def _build_ai_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="AI")

        ttk.Label(frame, text="OpenAI API key:").grid(
            row=0, column=0, sticky="w", pady=4
        )
        assert self.var_api_key and self.var_show_key and self.var_model
        self._entry_api_key = tk.Entry(
            frame, textvariable=self.var_api_key, show="*", width=40
        )
        self._entry_api_key.grid(row=0, column=1, sticky="w", pady=4)

        def _toggle_show() -> None:
            assert self._entry_api_key is not None
            self._entry_api_key.configure(
                show="" if self.var_show_key.get() else "*"
            )

        ttk.Checkbutton(
            frame, text="Show", variable=self.var_show_key, command=_toggle_show
        ).grid(row=0, column=2, sticky="w", padx=(6, 0))

        ttk.Label(frame, text="Model:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(
            frame,
            textvariable=self.var_model,
            values=list(MODELS),
            state="readonly",
            width=20,
        ).grid(row=1, column=1, sticky="w", pady=4)

        assert self.var_debug_logging
        ttk.Checkbutton(
            frame,
            text="Enable debug logging (verbose diagnostics)",
            variable=self.var_debug_logging,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 4))

        ttk.Label(
            frame,
            text=(
                "Your API key is stored locally in ~/.sandman/config.json\n"
                "and never shared. At typical usage this costs < $0.10/month.\n\n"
                "Privacy: window titles and app names are sent to OpenAI\n"
                "to classify activity and generate nudge messages."
            ),
            foreground="#666",
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _build_notifications_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="Notifications")

        assert (
            self.var_min_interval
            and self.var_escalation
            and self.var_nudge_style
            and self.var_autostart
        )

        ttk.Label(frame, text="Minimum minutes between nudges:").grid(
            row=0, column=0, sticky="w", pady=4
        )
        value_label = ttk.Label(frame, text=str(self.var_min_interval.get()))
        value_label.grid(row=0, column=2, sticky="w", padx=(6, 0))

        def _slider_changed(v: str) -> None:
            try:
                value_label.configure(text=str(int(float(v))))
            except ValueError:
                pass

        ttk.Scale(
            frame,
            from_=1,
            to=10,
            orient="horizontal",
            variable=self.var_min_interval,
            command=_slider_changed,
            length=200,
        ).grid(row=0, column=1, sticky="w", pady=4)

        ttk.Checkbutton(
            frame,
            text="Increase urgency over time",
            variable=self.var_escalation,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 4))

        ttk.Label(frame, text="Nudge style:").grid(
            row=2, column=0, sticky="w", pady=4
        )
        ttk.Combobox(
            frame,
            textvariable=self.var_nudge_style,
            values=list(NUDGE_STYLE_LABELS.values()),
            state="readonly",
            width=28,
        ).grid(row=2, column=1, columnspan=2, sticky="w", pady=4)

        ttk.Checkbutton(
            frame,
            text="Start Sandman with Windows",
            variable=self.var_autostart,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(16, 4))

    # ---- actions --------------------------------------------------------

    def _refresh_aw_status(self) -> None:
        """Kick off an async availability check; UI updates on completion.

        We can't call ``is_available`` inline: it does a blocking HTTP
        request (up to 3s) on what is now the shared Tk thread, which
        would freeze every window. Instead we run the probe on a short-
        lived worker and post the result back through ``after``.
        """
        if self._aw_status_label is None:
            return
        # Show the last known result immediately (or a neutral state).
        if self._aw_status_last is not None:
            self._render_aw_status(self._aw_status_last)
        elif self._aw_status_label is not None:
            self._aw_status_label.configure(
                text="Status: Checking...", foreground="#666666"
            )

        with self._aw_status_lock:
            if self._aw_status_pending:
                return
            self._aw_status_pending = True

        def _probe() -> None:
            ok = False
            try:
                ok = self.aw_client.is_available()
            except Exception:
                ok = False
            root = self._root
            if root is None:
                with self._aw_status_lock:
                    self._aw_status_pending = False
                return
            try:
                root.after(0, lambda ok=ok: self._on_aw_status_result(ok))
            except tk.TclError:
                with self._aw_status_lock:
                    self._aw_status_pending = False

        threading.Thread(
            target=_probe, name="sandman-aw-probe", daemon=True
        ).start()

    def _on_aw_status_result(self, ok: bool) -> None:
        with self._aw_status_lock:
            self._aw_status_pending = False
        self._aw_status_last = ok
        self._render_aw_status(ok)

    def _render_aw_status(self, ok: bool) -> None:
        if self._aw_status_label is None:
            return
        text = "Connected ✓" if ok else "Not detected ✗"
        color = "#2a7a2a" if ok else "#a02020"
        try:
            self._aw_status_label.configure(
                text=f"Status: {text}", foreground=color
            )
        except tk.TclError:
            pass

    def _schedule_aw_refresh(self) -> None:
        if self._root is None:
            return
        self._refresh_aw_status()
        try:
            self._root.after(2000, self._schedule_aw_refresh)
        except tk.TclError:
            pass

    def _on_save(self) -> None:
        assert self._root is not None
        # Validate times.
        assert self.var_active_from and self.var_active_until and self.var_wake_time
        for label, var in (
            ("Active from", self.var_active_from),
            ("Active until", self.var_active_until),
            ("Wake-up time", self.var_wake_time),
        ):
            if not _valid_time(var.get()):
                messagebox.showerror(
                    "Invalid time",
                    f"{label} must be in HH:MM format (00:00–23:59).",
                )
                return

        # Validate at least one day selected.
        selected_days = [i for i, v in enumerate(self.var_days) if v.get()]
        if not selected_days:
            messagebox.showerror(
                "No days selected",
                "Pick at least one day of the week for Sandman to be active.",
            )
            return

        assert self.var_api_key and self.var_model
        api_key = self.var_api_key.get().strip()
        if not api_key:
            if not messagebox.askyesno(
                "No API key",
                "Without an OpenAI API key, Sandman can't generate nudges.\n"
                "Save anyway?",
            ):
                return

        # Map nudge style label back to key.
        assert self.var_nudge_style
        label_to_key = {v: k for k, v in NUDGE_STYLE_LABELS.items()}
        nudge_style_key = label_to_key.get(
            self.var_nudge_style.get(), "gentle"
        )
        if nudge_style_key not in NUDGE_STYLES:
            nudge_style_key = "gentle"

        assert (
            self.var_min_interval
            and self.var_escalation
            and self.var_autostart
            and self.var_debug_logging
        )
        c = self.config
        c.data["openai_api_key"] = api_key
        c.data["model"] = self.var_model.get()
        c.data["schedule"]["active_from"] = self.var_active_from.get()
        c.data["schedule"]["active_until"] = self.var_active_until.get()
        c.data["schedule"]["wake_time"] = self.var_wake_time.get()
        c.data["schedule"]["active_days"] = selected_days
        c.data["notifications"]["min_interval_seconds"] = int(
            self.var_min_interval.get() * 60
        )
        c.data["notifications"]["escalation_enabled"] = self.var_escalation.get()
        c.data["notifications"]["nudge_style"] = nudge_style_key
        c.data["start_with_windows"] = self.var_autostart.get()
        c.data["debug_logging"] = self.var_debug_logging.get()

        try:
            c.save()
        except OSError as exc:
            messagebox.showerror("Save failed", f"Could not write config: {exc}")
            return

        if self.on_saved:
            try:
                self.on_saved(c)
            except Exception:
                log.exception("on_saved callback failed")

        self._close()

    def _on_cancel(self) -> None:
        self._close()

    def _close(self) -> None:
        if self._root is None:
            return
        try:
            self._root.destroy()
        except tk.TclError:
            pass
        self._root = None
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                log.exception("on_close callback failed")
