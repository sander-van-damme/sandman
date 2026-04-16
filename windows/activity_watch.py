"""ActivityWatch REST API client.

Sandman never implements its own window tracking — it relies on a running
ActivityWatch server (usually on ``http://localhost:5600``) and its
``aw-watcher-window`` and ``aw-watcher-afk`` buckets.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)


DEFAULT_BASE_URL = "http://localhost:5600/api/0"
REQUEST_TIMEOUT = 3.0  # seconds


@dataclass
class WindowActivity:
    """A snapshot of what the user currently has focused."""

    app: str
    title: str
    duration: float  # seconds
    timestamp: str  # ISO timestamp from AW

    def key(self) -> tuple[str, str]:
        """Stable identity for deduplication purposes."""
        return (self.app, self.title)


@dataclass
class AfkStatus:
    """Most recent entry from the AFK watcher."""

    afk: bool
    duration: float  # seconds the user has been in this state


class ActivityWatchError(Exception):
    """Raised when we can't talk to ActivityWatch."""


class ActivityWatchClient:
    """Thin wrapper over the ActivityWatch REST API.

    Only the endpoints Sandman actually uses are implemented. Bucket names
    are discovered lazily and cached.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._window_bucket: str | None = None
        self._afk_bucket: str | None = None
        self._hostname = socket.gethostname()

    # ---- connectivity ---------------------------------------------------

    def is_available(self) -> bool:
        """Return True iff we can reach the ActivityWatch info endpoint."""
        try:
            r = requests.get(f"{self.base_url}/info", timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return True
        except requests.RequestException as exc:
            log.debug("ActivityWatch not reachable: %s", exc)
            return False

    def info(self) -> dict[str, Any]:
        r = requests.get(f"{self.base_url}/info", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ---- bucket discovery ----------------------------------------------

    def _list_buckets(self) -> dict[str, Any]:
        r = requests.get(f"{self.base_url}/buckets", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _find_bucket(self, prefix: str) -> str | None:
        """Find the first bucket whose ID starts with ``prefix``.

        We prefer a bucket matching the local hostname when available to
        avoid picking up stale buckets from other machines.
        """
        try:
            buckets = self._list_buckets()
        except requests.RequestException as exc:
            raise ActivityWatchError(f"Failed to list buckets: {exc}") from exc

        candidates = [bid for bid in buckets if bid.startswith(prefix)]
        if not candidates:
            return None
        # Prefer one that matches our hostname.
        for bid in candidates:
            if self._hostname in bid:
                return bid
        return candidates[0]

    def window_bucket(self) -> str | None:
        if self._window_bucket is None:
            self._window_bucket = self._find_bucket("aw-watcher-window_")
        return self._window_bucket

    def afk_bucket(self) -> str | None:
        if self._afk_bucket is None:
            self._afk_bucket = self._find_bucket("aw-watcher-afk_")
        return self._afk_bucket

    def reset_bucket_cache(self) -> None:
        self._window_bucket = None
        self._afk_bucket = None

    # ---- activity polling ----------------------------------------------

    def _latest_event(self, bucket_id: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/buckets/{bucket_id}/events"
        try:
            r = requests.get(url, params={"limit": 1}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise ActivityWatchError(
                f"Failed to fetch events from {bucket_id}: {exc}"
            ) from exc
        events = r.json()
        if not events:
            return None
        return events[0]

    def current_window(self) -> WindowActivity | None:
        """Return the most recent window event, or None if unavailable."""
        bucket = self.window_bucket()
        if not bucket:
            return None
        event = self._latest_event(bucket)
        if not event:
            return None
        data = event.get("data", {}) or {}
        return WindowActivity(
            app=str(data.get("app", "") or "unknown"),
            title=str(data.get("title", "") or ""),
            duration=float(event.get("duration", 0.0) or 0.0),
            timestamp=str(event.get("timestamp", "")),
        )

    def current_afk(self) -> AfkStatus | None:
        """Return the most recent AFK status, or None if unavailable."""
        bucket = self.afk_bucket()
        if not bucket:
            return None
        event = self._latest_event(bucket)
        if not event:
            return None
        data = event.get("data", {}) or {}
        status = str(data.get("status", "not-afk")).lower()
        return AfkStatus(
            afk=(status == "afk"),
            duration=float(event.get("duration", 0.0) or 0.0),
        )
