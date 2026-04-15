"""Tests for config loading/saving and the active-window logic."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from sandman.config import DEFAULT_CONFIG, Config


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path / "nope.json")
    assert cfg.data == DEFAULT_CONFIG
    assert not cfg.is_configured()


def test_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    cfg = Config.load(p)
    cfg.data["openai_api_key"] = "sk-test"
    cfg.data["schedule"]["active_from"] = "22:00"
    cfg.save()

    reloaded = Config.load(p)
    assert reloaded.api_key == "sk-test"
    assert reloaded.schedule["active_from"] == "22:00"
    assert reloaded.is_configured()


def test_deep_merge_preserves_new_defaults(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    # Write a minimal legacy config missing some keys.
    p.write_text(json.dumps({"openai_api_key": "sk-legacy"}))
    cfg = Config.load(p)
    assert cfg.api_key == "sk-legacy"
    # Defaults fill in missing sections.
    assert cfg.schedule["active_from"] == DEFAULT_CONFIG["schedule"]["active_from"]
    assert "notifications" in cfg.data


def test_within_same_day_window(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path / "c.json")
    cfg.data["schedule"]["active_from"] = "22:00"
    cfg.data["schedule"]["active_until"] = "23:30"
    cfg.data["schedule"]["active_days"] = list(range(7))

    # Wednesday 22:30 — inside.
    assert cfg.is_within_active_window(datetime(2026, 4, 15, 22, 30))
    # Wednesday 21:00 — outside.
    assert not cfg.is_within_active_window(datetime(2026, 4, 15, 21, 0))
    # Wednesday 23:45 — outside.
    assert not cfg.is_within_active_window(datetime(2026, 4, 15, 23, 45))


def test_within_overnight_window(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path / "c.json")
    cfg.data["schedule"]["active_from"] = "21:30"
    cfg.data["schedule"]["active_until"] = "02:00"
    # Only weekdays (Mon=0..Fri=4).
    cfg.data["schedule"]["active_days"] = [0, 1, 2, 3, 4]

    # Wed 23:00 — inside (started Wed).
    assert cfg.is_within_active_window(datetime(2026, 4, 15, 23, 0))
    # Thu 01:30 — inside (started Wed, still active pre-02:00).
    assert cfg.is_within_active_window(datetime(2026, 4, 16, 1, 30))
    # Thu 02:30 — outside (window ended).
    assert not cfg.is_within_active_window(datetime(2026, 4, 16, 2, 30))
    # Sat 23:00 — outside (weekends disabled).
    assert not cfg.is_within_active_window(datetime(2026, 4, 18, 23, 0))
    # Sun 01:30 — outside (started Sat, which is disabled).
    assert not cfg.is_within_active_window(datetime(2026, 4, 19, 1, 30))


def test_minutes_past_bedtime(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path / "c.json")
    cfg.data["schedule"]["active_from"] = "21:30"
    cfg.data["schedule"]["active_until"] = "02:00"

    # 22:30 same day → 60 minutes past.
    assert cfg.minutes_past_bedtime(datetime(2026, 4, 15, 22, 30)) == 60
    # 01:00 next day → 3h30m past = 210 minutes.
    assert cfg.minutes_past_bedtime(datetime(2026, 4, 16, 1, 0)) == 210


def test_active_days_set(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path / "c.json")
    cfg.data["schedule"]["active_days"] = [0, 2, 4]
    assert cfg.active_days() == {0, 2, 4}
