"""Detect device sleep/wake transitions based on polling gaps.

Sandman doesn't get an OS-level suspend/resume event — we just notice that
the monotonic clock jumped forward by much more than the poll interval, and
treat that as "laptop was asleep". After a wake, we impose a grace period
before nudges resume.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class SleepDetector:
    """Track inter-poll gaps to spot device suspend/resume events.

    ``expected_interval`` is the nominal poll period in seconds.
    ``gap_threshold`` is how many seconds over expected counts as a "wake".
    ``grace_period`` is how long to wait after a wake before resuming nudges.
    """

    expected_interval: float = 30.0
    gap_threshold: float = 60.0
    grace_period: float = 120.0

    _last_poll: float | None = None
    _wake_at: float | None = None

    def tick(self, now: float | None = None) -> bool:
        """Record a poll. Returns True if we just detected a wake event."""
        now = now if now is not None else time.monotonic()
        woke = False
        if self._last_poll is not None:
            gap = now - self._last_poll
            if gap > self.gap_threshold:
                log.info(
                    "Detected sleep/wake: %.1fs gap between polls (expected %.1fs)",
                    gap,
                    self.expected_interval,
                )
                self._wake_at = now
                woke = True
        self._last_poll = now
        return woke

    def in_grace_period(self, now: float | None = None) -> bool:
        """Return True if we should suppress nudges because we recently woke."""
        if self._wake_at is None:
            return False
        now = now if now is not None else time.monotonic()
        if now - self._wake_at < self.grace_period:
            return True
        # Grace period elapsed — clear the marker.
        self._wake_at = None
        return False

    def reset(self) -> None:
        self._last_poll = None
        self._wake_at = None
