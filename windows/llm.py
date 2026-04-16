"""OpenAI integration: prompt construction, calls, and conversation memory."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """\
You are Sandman, a bedtime coach built on behavioral psychology principles \
(BJ Fogg's Behavior Model, habit stacking, commitment devices, implementation \
intentions). The user has asked you to help them get to bed on time.

Current context:
- It is {current_time}
- The user's bedtime goal is {bedtime} (they need to wake at {wake_time})
- They have been active past their wind-down time for {minutes_past} minutes
- Current application: {app_name}
- Current window title: {window_title}
- Number of nudges sent this session: {nudge_count}
- Nudge style preference: {nudge_style}

Your job:
1. CLASSIFY what the user is doing (programming, social media, entertainment, \
communication, productive work, etc.)
2. DECIDE if this activity warrants a nudge. Some activities are legitimate \
late-night needs (e.g., booking an Uber, responding to an urgent message, \
setting an alarm). Use good judgment.
3. If nudging, generate a SHORT message (2-3 sentences max) that:
   - Acknowledges what they're doing specifically
   - Uses a behavioral psychology technique (e.g., "just one tiny step: close \
the laptop lid", "your future self will thank you", "you've been coding for \
2 hours — diminishing returns have kicked in")
   - Gets more urgent as nudge_count increases
   - Matches the user's preferred nudge style
4. If the user has replied to a previous nudge, respond to their reply \
conversationally while still guiding them toward bed.

Respond in JSON format:
{{
  "activity_type": "programming|social_media|entertainment|communication|productive|utility|other",
  "should_nudge": true/false,
  "reason": "brief explanation of why or why not",
  "message": "the nudge message to show (only if should_nudge is true)",
  "follow_up_question": "optional question to engage the user, e.g. 'What's keeping you going right now?'"
}}
"""


FALLBACK_MESSAGES = [
    "Hey, it's getting late. Consider wrapping up.",
    "Time's slipping away — your future self will thank you for stopping now.",
    "Small step: close the laptop lid. Future you is cheering.",
    "You've earned rest. Wrap up the current thought and call it a night.",
]


@dataclass
class NudgeDecision:
    """Structured result of an LLM call."""

    activity_type: str
    should_nudge: bool
    reason: str
    message: str
    follow_up_question: str | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def fallback(cls, nudge_count: int, reason: str = "LLM unavailable") -> "NudgeDecision":
        log.warning("Using fallback nudge (nudge_count=%d, reason=%s)", nudge_count, reason)
        msg = FALLBACK_MESSAGES[min(nudge_count, len(FALLBACK_MESSAGES) - 1)]
        return cls(
            activity_type="other",
            should_nudge=True,
            reason=reason,
            message=msg,
            follow_up_question=None,
        )


@dataclass
class ConversationHistory:
    """Session-scoped chat history for the nudge LLM.

    Stored in memory only — cleared when a new active window session starts.
    ``messages`` is a list of ``{"role": ..., "content": ...}`` dicts, matching
    the OpenAI Chat Completions schema.
    """

    messages: list[dict[str, str]] = field(default_factory=list)
    max_messages: int = 20
    session_started_at: datetime | None = None

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > self.max_messages:
            # Keep the most recent messages.
            self.messages = self.messages[-self.max_messages :]

    def clear(self) -> None:
        self.messages = []
        self.session_started_at = None

    def start_session(self, when: datetime | None = None) -> None:
        self.clear()
        self.session_started_at = when or datetime.now()


class LLMClient:
    """Wrapper around the OpenAI Chat Completions API.

    Kept as a simple class so tests can substitute a fake ``openai_client``.
    Lazy-imports ``openai`` so the rest of the app works without it (useful
    for headless tests).
    """

    def __init__(self, api_key: str, model: str = "gpt-5-nano") -> None:
        self.api_key = api_key
        self.model = model
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI  # type: ignore

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    # ---- prompt building ------------------------------------------------

    @staticmethod
    def build_system_prompt(
        *,
        now: datetime,
        bedtime: str,
        wake_time: str,
        minutes_past: int,
        app_name: str,
        window_title: str,
        nudge_count: int,
        nudge_style: str,
    ) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(
            current_time=now.strftime("%H:%M"),
            bedtime=bedtime,
            wake_time=wake_time,
            minutes_past=minutes_past,
            app_name=app_name or "unknown",
            window_title=window_title or "",
            nudge_count=nudge_count,
            nudge_style=nudge_style,
        )

    # ---- API calls ------------------------------------------------------

    def classify_and_nudge(
        self,
        *,
        system_prompt: str,
        history: ConversationHistory,
        user_message: str | None = None,
        nudge_count: int = 0,
    ) -> NudgeDecision:
        """Call the LLM and return a ``NudgeDecision``.

        On any error returns a fallback decision — callers never need to
        handle exceptions from this method.
        """
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history.messages)
        if user_message:
            messages.append({"role": "user", "content": user_message})

        try:
            client = self._ensure_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                max_completion_tokens=300,
            )
            content = response.choices[0].message.content or "{}"
        except Exception as exc:  # pragma: no cover - network/SDK errors
            log.warning("OpenAI call failed: %s", exc)
            return NudgeDecision.fallback(nudge_count, reason=f"api_error: {exc}")

        return self._parse_decision(content, nudge_count)

    @staticmethod
    def _parse_decision(content: str, nudge_count: int) -> NudgeDecision:
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            log.warning("LLM returned non-JSON content: %s", exc)
            return NudgeDecision.fallback(nudge_count, reason="invalid_json")

        return NudgeDecision(
            activity_type=str(raw.get("activity_type", "other")),
            should_nudge=bool(raw.get("should_nudge", False)),
            reason=str(raw.get("reason", "")),
            message=str(raw.get("message", "") or ""),
            follow_up_question=raw.get("follow_up_question") or None,
            raw=raw,
        )
