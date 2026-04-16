"""Tests for the Monitor's decision logic.

We don't actually run the background thread — we poke ``_tick`` directly
after wiring up fake ActivityWatch and LLM clients.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from windows.activity_watch import AfkStatus, WindowActivity
from windows.config import Config
from windows.llm import NudgeDecision
from windows.monitor import Monitor, MonitorState


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config.load(tmp_path / "c.json")
    cfg.data["openai_api_key"] = "sk-test"
    cfg.data["schedule"]["active_from"] = "21:30"
    cfg.data["schedule"]["active_until"] = "02:00"
    cfg.data["schedule"]["active_days"] = list(range(7))
    cfg.data["notifications"]["min_interval_seconds"] = 60
    return cfg


def _make_monitor(
    cfg: Config,
    *,
    activity: WindowActivity | None = None,
    afk: AfkStatus | None = None,
    decision: NudgeDecision | None = None,
) -> tuple[Monitor, MagicMock, MagicMock]:
    aw = MagicMock()
    aw.is_available.return_value = True
    aw.current_window.return_value = activity or WindowActivity(
        app="Code.exe", title="main.py", duration=10, timestamp="t"
    )
    aw.current_afk.return_value = afk or AfkStatus(afk=False, duration=0)

    llm = MagicMock()
    llm.classify_and_nudge.return_value = decision or NudgeDecision(
        activity_type="programming",
        should_nudge=True,
        reason="late coding",
        message="Go to bed!",
    )

    nudge_cb = MagicMock()
    status_cb = MagicMock()
    monitor = Monitor(
        config=cfg,
        aw_client=aw,
        llm_client=llm,
        on_nudge=nudge_cb,
        on_status=status_cb,
    )
    return monitor, nudge_cb, status_cb


_ACTIVE_NOW = datetime(2026, 4, 15, 22, 30)  # Wed 22:30 — inside window


def test_skips_when_not_configured(config: Config, tmp_path: Path) -> None:
    config.data["openai_api_key"] = ""
    m, nudge_cb, _ = _make_monitor(config)
    with patch("windows.monitor.datetime") as mdt:
        mdt.now.return_value = _ACTIVE_NOW
        mdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m._tick()
    nudge_cb.assert_not_called()
    assert m.status.state == MonitorState.ERROR


def test_skips_outside_active_window(config: Config) -> None:
    m, nudge_cb, _ = _make_monitor(config)
    outside = datetime(2026, 4, 15, 10, 0)  # 10am
    with patch("windows.monitor.datetime") as mdt:
        mdt.now.return_value = outside
        mdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m._tick()
    nudge_cb.assert_not_called()
    assert m.status.state == MonitorState.IDLE


def test_nudges_when_conditions_met(config: Config) -> None:
    m, nudge_cb, _ = _make_monitor(config)
    with patch("windows.monitor.datetime") as mdt:
        mdt.now.return_value = _ACTIVE_NOW
        mdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m._tick()
    nudge_cb.assert_called_once()
    assert m.status.nudge_count == 1
    assert m.status.state == MonitorState.NUDGING


def test_rate_limits_second_nudge(config: Config) -> None:
    m, nudge_cb, _ = _make_monitor(config)
    # Pre-seed the session so _maybe_start_session doesn't wipe last_nudge_at.
    m._session_date = _ACTIVE_NOW.strftime("%Y-%m-%d")
    m.status.last_nudge_at = _ACTIVE_NOW - timedelta(seconds=30)
    with patch("windows.monitor.datetime") as mdt:
        mdt.now.return_value = _ACTIVE_NOW
        mdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m._tick()
    nudge_cb.assert_not_called()


def test_dedupes_same_activity_within_triple_interval(config: Config) -> None:
    m, nudge_cb, _ = _make_monitor(config)
    m._session_date = _ACTIVE_NOW.strftime("%Y-%m-%d")
    m.status.last_nudge_at = _ACTIVE_NOW - timedelta(seconds=90)  # > 60 but < 180
    m._last_nudge_activity_key = ("Code.exe", "main.py")
    with patch("windows.monitor.datetime") as mdt:
        mdt.now.return_value = _ACTIVE_NOW
        mdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m._tick()
    nudge_cb.assert_not_called()


def test_skips_when_afk(config: Config) -> None:
    m, nudge_cb, _ = _make_monitor(
        config, afk=AfkStatus(afk=True, duration=600)
    )
    m.status.nudge_count = 4
    with patch("windows.monitor.datetime") as mdt:
        mdt.now.return_value = _ACTIVE_NOW
        mdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m._tick()
    nudge_cb.assert_not_called()
    # Long AFK (>=300) resets the count so they don't come back to escalation.
    assert m.status.nudge_count == 0


def test_llm_can_decline_to_nudge(config: Config) -> None:
    decision = NudgeDecision(
        activity_type="utility",
        should_nudge=False,
        reason="booking an Uber is legit",
        message="",
    )
    m, nudge_cb, _ = _make_monitor(config, decision=decision)
    with patch("windows.monitor.datetime") as mdt:
        mdt.now.return_value = _ACTIVE_NOW
        mdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m._tick()
    nudge_cb.assert_not_called()
    assert m.status.state == MonitorState.ACTIVE


def test_pause_blocks_ticks(config: Config) -> None:
    m, nudge_cb, _ = _make_monitor(config)
    with patch("windows.monitor.datetime") as mdt:
        mdt.now.return_value = _ACTIVE_NOW
        mdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m.pause_for(30)
        m._tick()
    nudge_cb.assert_not_called()
    assert m.status.state == MonitorState.IDLE


def test_aw_unreachable_sets_error_state(config: Config) -> None:
    m, nudge_cb, _ = _make_monitor(config)
    m.aw_client.is_available.return_value = False
    with patch("windows.monitor.datetime") as mdt:
        mdt.now.return_value = _ACTIVE_NOW
        mdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        m._tick()
    assert m.status.state == MonitorState.ERROR
    assert m.status.aw_connected is False
    nudge_cb.assert_not_called()
