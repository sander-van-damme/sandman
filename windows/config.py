"""Configuration model and persistence for Sandman.

Config is stored as JSON at ``~/.sandman/config.json``. This module handles
loading, saving, and supplying sensible defaults so the rest of the app can
treat settings as a simple dict-backed object.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


CONFIG_DIR = Path(os.path.expanduser("~")) / ".sandman"
CONFIG_PATH = CONFIG_DIR / "config.json"


NUDGE_STYLES = ("gentle", "direct", "humor", "therapist")


DEFAULT_CONFIG: dict[str, Any] = {
    "openai_api_key": "",
    "model": "gpt-5-mini",
    "debug_logging": False,
    "schedule": {
        "active_from": "21:30",
        "active_until": "02:00",
        "active_days": [0, 1, 2, 3, 4, 5, 6],  # Mon..Sun
        "wake_time": "07:30",
    },
    "notifications": {
        "min_interval_seconds": 60,
        "escalation_enabled": True,
        "nudge_style": "gentle",
    },
    "start_with_windows": False,
    "state": {
        "paused_until": None,  # ISO timestamp string or None
        "total_nudges_sent": 0,
        "sessions": [],
    },
}


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Return a new dict with overrides merged over base (recursively)."""
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    """Dict-backed configuration with helpers for the fields we read a lot."""

    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_CONFIG))
    path: Path = CONFIG_PATH

    # ---- persistence ----------------------------------------------------

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            log.info("No config at %s, using defaults", path)
            return cls(data=copy.deepcopy(DEFAULT_CONFIG), path=path)
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Failed to read config at %s: %s — using defaults", path, exc)
            return cls(data=copy.deepcopy(DEFAULT_CONFIG), path=path)
        merged = _deep_merge(DEFAULT_CONFIG, raw)
        return cls(data=merged, path=path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        tmp.replace(self.path)
        log.debug("Saved config to %s", self.path)

    # ---- convenience accessors -----------------------------------------

    @property
    def api_key(self) -> str:
        return self.data.get("openai_api_key", "") or ""

    @property
    def model(self) -> str:
        return self.data.get("model", "gpt-5-mini")

    @property
    def schedule(self) -> dict[str, Any]:
        return self.data["schedule"]

    @property
    def notifications(self) -> dict[str, Any]:
        return self.data["notifications"]

    @property
    def state(self) -> dict[str, Any]:
        return self.data["state"]

    def is_configured(self) -> bool:
        return bool(self.api_key.strip())

    # ---- schedule helpers ----------------------------------------------

    def _parse_time(self, value: str) -> time:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))

    def active_from(self) -> time:
        return self._parse_time(self.schedule["active_from"])

    def active_until(self) -> time:
        return self._parse_time(self.schedule["active_until"])

    def wake_time(self) -> time:
        return self._parse_time(self.schedule["wake_time"])

    def active_days(self) -> set[int]:
        return set(self.schedule.get("active_days", list(range(7))))

    def is_within_active_window(self, now: datetime | None = None) -> bool:
        """Check whether ``now`` falls inside the configured active window.

        Handles midnight crossover: if ``active_until < active_from``, the
        window straddles midnight. Active days are evaluated against the
        weekday the window *started* on (so a Monday 21:30 – Tuesday 02:00
        window counts as "Monday").
        """
        now = now or datetime.now()
        start = self.active_from()
        end = self.active_until()
        now_t = now.time()
        days = self.active_days()

        if start <= end:
            # Same-day window (e.g., 21:00 – 23:30).
            if now.weekday() not in days:
                return False
            return start <= now_t <= end

        # Crosses midnight.
        if now_t >= start:
            return now.weekday() in days
        if now_t <= end:
            # "Started yesterday" — check yesterday's weekday.
            yesterday = (now.weekday() - 1) % 7
            return yesterday in days
        return False

    def minutes_past_bedtime(self, now: datetime | None = None) -> int:
        """Return minutes elapsed since ``active_from`` started.

        If we're in the post-midnight part of the window, counts from
        yesterday's ``active_from``.
        """
        now = now or datetime.now()
        start_t = self.active_from()
        start_dt = now.replace(
            hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0
        )
        if now < start_dt:
            # Must be post-midnight part of an overnight window.
            from datetime import timedelta

            start_dt -= timedelta(days=1)
        delta = now - start_dt
        return max(0, int(delta.total_seconds() // 60))
