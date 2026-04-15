"""Tests for the ActivityWatch client — network calls are mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from sandman.activity_watch import (
    ActivityWatchClient,
    ActivityWatchError,
    WindowActivity,
)


def _fake_response(payload, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_is_available_true() -> None:
    client = ActivityWatchClient()
    with patch("sandman.activity_watch.requests.get") as mock_get:
        mock_get.return_value = _fake_response({"hostname": "test"})
        assert client.is_available() is True


def test_is_available_false_on_connection_error() -> None:
    client = ActivityWatchClient()
    with patch("sandman.activity_watch.requests.get") as mock_get:
        mock_get.side_effect = requests.ConnectionError("nope")
        assert client.is_available() is False


def test_bucket_discovery_prefers_hostname() -> None:
    client = ActivityWatchClient()
    client._hostname = "my-laptop"
    buckets = {
        "aw-watcher-window_other-host": {},
        "aw-watcher-window_my-laptop": {},
    }
    with patch.object(client, "_list_buckets", return_value=buckets):
        assert client.window_bucket() == "aw-watcher-window_my-laptop"


def test_bucket_discovery_returns_none_when_missing() -> None:
    client = ActivityWatchClient()
    with patch.object(client, "_list_buckets", return_value={}):
        assert client.window_bucket() is None


def test_current_window_parses_event() -> None:
    client = ActivityWatchClient()
    client._window_bucket = "aw-watcher-window_host"
    event = {
        "timestamp": "2026-04-15T22:30:00Z",
        "duration": 42.5,
        "data": {"app": "Code.exe", "title": "main.py — sandman"},
    }
    with patch("sandman.activity_watch.requests.get") as mock_get:
        mock_get.return_value = _fake_response([event])
        activity = client.current_window()
    assert isinstance(activity, WindowActivity)
    assert activity.app == "Code.exe"
    assert activity.title == "main.py — sandman"
    assert activity.duration == 42.5
    assert activity.key() == ("Code.exe", "main.py — sandman")


def test_current_window_returns_none_on_empty() -> None:
    client = ActivityWatchClient()
    client._window_bucket = "aw-watcher-window_host"
    with patch("sandman.activity_watch.requests.get") as mock_get:
        mock_get.return_value = _fake_response([])
        assert client.current_window() is None


def test_current_afk_parses_status() -> None:
    client = ActivityWatchClient()
    client._afk_bucket = "aw-watcher-afk_host"
    with patch("sandman.activity_watch.requests.get") as mock_get:
        mock_get.return_value = _fake_response(
            [{"timestamp": "x", "duration": 300, "data": {"status": "afk"}}]
        )
        afk = client.current_afk()
    assert afk is not None
    assert afk.afk is True
    assert afk.duration == 300


def test_latest_event_raises_on_http_error() -> None:
    client = ActivityWatchClient()
    with patch("sandman.activity_watch.requests.get") as mock_get:
        mock_get.side_effect = requests.ConnectionError("boom")
        with pytest.raises(ActivityWatchError):
            client._latest_event("aw-watcher-window_host")
