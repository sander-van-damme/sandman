"""Tests for the monotonic-clock-based sleep detector."""

from __future__ import annotations

from sandman.sleep_detect import SleepDetector


def test_no_wake_on_normal_interval() -> None:
    d = SleepDetector(expected_interval=30, gap_threshold=60, grace_period=120)
    assert d.tick(now=1000.0) is False
    assert d.tick(now=1030.0) is False
    assert d.in_grace_period(now=1030.0) is False


def test_wake_detected_on_large_gap() -> None:
    d = SleepDetector(expected_interval=30, gap_threshold=60, grace_period=120)
    d.tick(now=1000.0)
    assert d.tick(now=2000.0) is True  # 1000s gap > threshold
    assert d.in_grace_period(now=2000.0) is True
    # Still in grace period 60s later.
    assert d.in_grace_period(now=2060.0) is True
    # Grace expires after 120s.
    assert d.in_grace_period(now=2121.0) is False


def test_reset_clears_state() -> None:
    d = SleepDetector()
    d.tick(now=0.0)
    d.tick(now=500.0)  # triggers wake
    assert d.in_grace_period(now=500.0) is True
    d.reset()
    assert d.in_grace_period(now=500.0) is False
