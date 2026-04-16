"""The background monitor loop — the heart of Sandman.

The monitor polls ActivityWatch every ``poll_interval`` seconds, applies
rate limits and a bunch of "don't nag me right now" checks, asks the LLM
whether to nudge, and hands the result off to a notifier callback.

It's deliberately decoupled from the UI: the tray module wires up a
notifier callback (toast + reply window) and a state callback (to change
the tray icon). The monitor itself only touches configuration, the LLM
client, and the ActivityWatch client.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable

from .activity_watch import ActivityWatchClient, ActivityWatchError, WindowActivity
from .config import Config
from .llm import ConversationHistory, LLMClient, NudgeDecision

log = logging.getLogger(__name__)


class MonitorState(str, Enum):
    IDLE = "idle"  # outside active window / paused
    ACTIVE = "active"  # inside window, monitoring
    NUDGING = "nudging"  # recently sent a nudge
    ERROR = "error"  # AW unreachable, missing API key, etc.


@dataclass
class MonitorStatus:
    """Snapshot of the monitor's current state for the tray to display."""

    state: MonitorState = MonitorState.IDLE
    message: str = ""
    nudge_count: int = 0
    last_nudge_at: datetime | None = None
    aw_connected: bool = False


NudgeCallback = Callable[[NudgeDecision], None]
StatusCallback = Callable[[MonitorStatus], None]


@dataclass
class Monitor:
    """Owns the background polling thread and decision logic."""

    config: Config
    aw_client: ActivityWatchClient
    llm_client: LLMClient
    on_nudge: NudgeCallback
    on_status: StatusCallback | None = None
    is_alert_open: Callable[[], bool] | None = None
    poll_interval: float = 30.0

    status: MonitorStatus = field(default_factory=MonitorStatus)
    history: ConversationHistory = field(default_factory=ConversationHistory)
    _pending_notification_responses: list[str] = field(default_factory=list)
    _pending_lock: threading.Lock = field(default_factory=threading.Lock)

    _last_activity_key: tuple[str, str] | None = None
    _last_nudge_activity_key: tuple[str, str] | None = None
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _session_date: str | None = None  # YYYY-MM-DD for which history is valid
    _paused_until: datetime | None = None

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="sandman-monitor", daemon=True
        )
        self._thread.start()
        log.info("Monitor started")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        log.info("Monitor stopped")

    # ---- public control -------------------------------------------------

    def pause_for(self, minutes: int) -> None:
        self._paused_until = datetime.now() + timedelta(minutes=minutes)
        log.info("Paused until %s", self._paused_until)
        self._emit_status(MonitorState.IDLE, f"Paused for {minutes} minutes")

    def pause_until_tomorrow(self) -> None:
        tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow += timedelta(days=1)
        # Pause until the next day's active_from time.
        active_from = self.config.active_from()
        tomorrow = tomorrow.replace(hour=active_from.hour, minute=active_from.minute)
        self._paused_until = tomorrow
        log.info("Paused until %s", self._paused_until)
        self._emit_status(MonitorState.IDLE, "Paused until tomorrow")

    def resume(self) -> None:
        self._paused_until = None
        self._emit_status(MonitorState.IDLE, "Resumed")

    def is_paused(self, now: datetime | None = None) -> bool:
        if self._paused_until is None:
            return False
        now = now or datetime.now()
        if now >= self._paused_until:
            self._paused_until = None
            return False
        return True

    def handle_user_reply(self, text: str) -> NudgeDecision:
        """Process a free-form reply from the user and return Sandman's response.

        Called from the reply window on the tkinter thread. We synchronously
        call the LLM with the reply appended to history and append both
        sides to the history on success.
        """
        activity = self._safe_current_window() or WindowActivity(
            app="unknown", title="", duration=0, timestamp=""
        )
        log.info("Handling user reply, current app=%s", activity.app)
        self._consume_pending_notification_responses()
        system_prompt = LLMClient.build_system_prompt()
        turn_context = self._build_turn_context(activity, user_reply=text)
        decision = self.llm_client.classify_and_nudge(
            system_prompt=system_prompt,
            history=self.history,
            user_message=turn_context,
            nudge_count=self.status.nudge_count,
        )
        log.info(
            "Reply decision: should_nudge=%s, message=%r",
            decision.should_nudge,
            decision.message[:120] if decision.message else "",
        )
        self.history.add("user", text)
        if decision.message:
            self.history.add("assistant", decision.message)
        else:
            log.warning("LLM returned empty message for user reply")
        self._apply_extension_if_any(decision)
        return decision

    def record_notification_response(self, response_text: str) -> None:
        """Capture quick-action toast choices for the next LLM request."""
        response = response_text.strip()
        if not response:
            return
        log.info("Recorded notification response: %s", response)
        with self._pending_lock:
            self._pending_notification_responses.append(response)

    # ---- main loop ------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("Monitor tick failed")
                self._emit_status(MonitorState.ERROR, "Internal error")
            # Sleep in small increments so shutdown is responsive.
            slept = 0.0
            while slept < self.poll_interval and not self._stop_event.is_set():
                time.sleep(min(0.5, self.poll_interval - slept))
                slept += 0.5

    def _tick(self) -> None:
        now = datetime.now()

        # 1) Configuration sanity checks.
        if not self.config.is_configured():
            self._emit_status(MonitorState.ERROR, "OpenAI API key not set")
            return

        # 2) ActivityWatch connectivity.
        if not self.aw_client.is_available():
            self.status.aw_connected = False
            self._emit_status(MonitorState.ERROR, "ActivityWatch not reachable")
            return
        self.status.aw_connected = True

        # 3) Active window / pause checks.
        if self.is_paused(now):
            self._emit_status(MonitorState.IDLE, "Paused")
            return
        if not self.config.is_within_active_window(now):
            # Leaving the active window resets session state next time we enter.
            self._maybe_end_session()
            self._emit_status(MonitorState.IDLE, "Outside active hours")
            return

        # 4) Start a new session if this is our first tick today.
        self._maybe_start_session(now)

        # 5) AFK check — don't nudge someone who isn't at their desk.
        afk = None
        try:
            afk = self.aw_client.current_afk()
        except ActivityWatchError as exc:
            log.debug("AFK fetch failed: %s", exc)
        if afk and afk.afk and afk.duration >= 120:
            # Long AFK likely means they got up — reset nudge counter so
            # they don't come back to an escalated scolding.
            if afk.duration >= 300:
                self.status.nudge_count = 0
            self._emit_status(MonitorState.ACTIVE, "User is AFK")
            return

        # 6) If an alert is already open, don't trigger additional nudges.
        if self.is_alert_open is not None and self.is_alert_open():
            self._emit_status(MonitorState.NUDGING, "Nudge popup open")
            return

        # 7) Rate limit: min interval between nudges.
        min_interval = int(
            self.config.notifications.get("min_interval_seconds", 60)
        )
        if self.status.last_nudge_at is not None:
            elapsed = (now - self.status.last_nudge_at).total_seconds()
            if elapsed < min_interval:
                self._emit_status(MonitorState.NUDGING, "Rate limited")
                return

        # 8) Fetch current window activity.
        try:
            activity = self.aw_client.current_window()
        except ActivityWatchError as exc:
            log.warning("Failed to fetch window activity: %s", exc)
            self._emit_status(MonitorState.ERROR, "ActivityWatch query failed")
            return
        if activity is None:
            self._emit_status(MonitorState.ACTIVE, "No window data yet")
            return

        activity_key = activity.key()

        # 9) Activity-level deduplication: if the exact same activity as
        #     the last nudge, wait 3× the minimum interval before re-nudging.
        if (
            self._last_nudge_activity_key == activity_key
            and self.status.last_nudge_at is not None
        ):
            elapsed = (now - self.status.last_nudge_at).total_seconds()
            if elapsed < 3 * min_interval:
                self._emit_status(
                    MonitorState.ACTIVE, "Same activity, waiting longer"
                )
                return

        self._last_activity_key = activity_key

        # 10) Ask the LLM what to do.
        self._consume_pending_notification_responses()
        system_prompt = LLMClient.build_system_prompt()
        turn_context = self._build_turn_context(activity)
        decision = self.llm_client.classify_and_nudge(
            system_prompt=system_prompt,
            history=self.history,
            user_message=turn_context,
            nudge_count=self.status.nudge_count,
        )

        if not decision.should_nudge or not decision.message:
            log.info(
                "LLM declined to nudge: should_nudge=%s, message_empty=%s, reason=%s",
                decision.should_nudge,
                not decision.message,
                decision.reason,
            )
            self._emit_status(
                MonitorState.ACTIVE,
                f"Watching ({decision.activity_type})",
            )
            return

        # 11) Fire the nudge.
        log.info(
            "Firing nudge #%d: %r",
            self.status.nudge_count + 1,
            decision.message[:120],
        )
        self.status.nudge_count += 1
        self.status.last_nudge_at = now
        self._last_nudge_activity_key = activity_key
        self.history.add("assistant", decision.message)
        self.config.state["total_nudges_sent"] = (
            self.config.state.get("total_nudges_sent", 0) + 1
        )
        try:
            self.config.save()
        except OSError as exc:
            log.warning("Failed to persist nudge count: %s", exc)

        self._emit_status(
            MonitorState.NUDGING,
            f"Nudge #{self.status.nudge_count}",
        )
        try:
            self.on_nudge(decision)
        except Exception:
            log.exception("on_nudge callback failed")

    # ---- helpers --------------------------------------------------------

    def _safe_current_window(self) -> WindowActivity | None:
        try:
            return self.aw_client.current_window()
        except ActivityWatchError:
            return None

    def _build_turn_context(self, activity: WindowActivity, user_reply: str | None = None) -> str:
        now = datetime.now()
        return LLMClient.build_turn_context_message(
            now=now,
            bedtime=self.config.schedule["active_from"],
            wake_time=self.config.schedule["wake_time"],
            minutes_past=self.config.minutes_past_bedtime(now),
            app_name=activity.app,
            window_title=activity.title,
            nudge_count=self.status.nudge_count,
            nudge_style=self.config.notifications.get("nudge_style", "gentle"),
            user_reply=user_reply,
        )

    def _maybe_start_session(self, now: datetime) -> None:
        date_key = now.strftime("%Y-%m-%d")
        # For overnight windows, a session that started yesterday at 21:30
        # should stay valid through 02:00 today. Track by "session start date".
        start_t = self.config.active_from()
        if now.time() < start_t:
            # We're in the post-midnight part — session started yesterday.
            date_key = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        if self._session_date != date_key:
            log.info("Starting new nudge session for %s", date_key)
            self._session_date = date_key
            self.status.nudge_count = 0
            self.status.last_nudge_at = None
            self._last_nudge_activity_key = None
            self.history.start_session(now)

    def _maybe_end_session(self) -> None:
        if self._session_date is not None:
            log.info("Ending nudge session %s", self._session_date)
        self._session_date = None
        self.status.nudge_count = 0
        self.status.last_nudge_at = None
        self._last_nudge_activity_key = None
        with self._pending_lock:
            self._pending_notification_responses.clear()
        self.history.clear()

    def _consume_pending_notification_responses(self) -> None:
        with self._pending_lock:
            pending = list(self._pending_notification_responses)
            self._pending_notification_responses.clear()
        for response in pending:
            self.history.add(
                "user",
                (
                    "Quick notification response: "
                    f"{response}. (User clicked this from the popup.)"
                ),
            )

    def _apply_extension_if_any(self, decision: NudgeDecision) -> None:
        if decision.extension_minutes is None or decision.extension_minutes <= 0:
            return
        now = datetime.now()
        requested_until = now + timedelta(minutes=decision.extension_minutes)
        capped_until = min(requested_until, self._current_active_window_end(now))
        if capped_until <= now:
            return
        self._paused_until = capped_until
        granted_minutes = int((capped_until - now).total_seconds() // 60)
        self._emit_status(MonitorState.IDLE, f"Extension granted for {granted_minutes} min")

    def _current_active_window_end(self, now: datetime) -> datetime:
        start_t = self.config.active_from()
        end_t = self.config.active_until()
        if start_t <= end_t:
            return now.replace(
                hour=end_t.hour,
                minute=end_t.minute,
                second=0,
                microsecond=0,
            )
        if now.time() >= start_t:
            end_date = now + timedelta(days=1)
        else:
            end_date = now
        return end_date.replace(
            hour=end_t.hour,
            minute=end_t.minute,
            second=0,
            microsecond=0,
        )

    def _emit_status(self, state: MonitorState, message: str) -> None:
        self.status.state = state
        self.status.message = message
        if self.on_status is not None:
            try:
                self.on_status(self.status)
            except Exception:
                log.exception("on_status callback failed")
