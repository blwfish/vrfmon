"""Tests for vrfmon/notify.py — fmt_duration and connectivity_warning throttle."""
import pytest

from vrfmon.notify import Notifier, fmt_duration


# --- fmt_duration ---

def test_fmt_seconds_zero():
    assert fmt_duration(0) == "0s"


def test_fmt_seconds_below_minute():
    assert fmt_duration(59) == "59s"


def test_fmt_minute_boundary_at():
    # at exactly 60s, switches to m/s format
    assert fmt_duration(60) == "1m 00s"


def test_fmt_minute_boundary_below():
    assert fmt_duration(59) == "59s"


def test_fmt_minute_boundary_above():
    assert fmt_duration(61) == "1m 01s"


def test_fmt_below_hour():
    assert fmt_duration(3599) == "59m 59s"


def test_fmt_hour_boundary_at():
    # at exactly 3600s, switches to h/m format (seconds not shown)
    assert fmt_duration(3600) == "1h 00m"


def test_fmt_hour_boundary_below():
    assert fmt_duration(3599) == "59m 59s"


def test_fmt_hour_boundary_above():
    assert fmt_duration(3601) == "1h 00m"


def test_fmt_hour_with_minutes():
    assert fmt_duration(3660) == "1h 01m"


def test_fmt_rounds_fractional_seconds():
    assert fmt_duration(0.4) == "0s"
    # Python 3 banker's rounding: round(0.5) == 0, round(1.5) == 2
    assert fmt_duration(0.5) == "0s"
    assert fmt_duration(1.5) == "2s"


# --- connectivity_warning throttle ---

def test_connectivity_warning_fires_on_first_call(monkeypatch):
    notifier = Notifier()
    delivered = []
    monkeypatch.setattr(notifier, "_console", lambda text: delivered.append(text))
    monkeypatch.setattr("vrfmon.notify.time.time", lambda: 1000.0)

    notifier.connectivity_warning(4, 5)
    assert len(delivered) == 1


def test_connectivity_warning_suppressed_before_interval(monkeypatch):
    notifier = Notifier()
    delivered = []
    monkeypatch.setattr(notifier, "_console", lambda text: delivered.append(text))

    # First call at t=1000
    monkeypatch.setattr("vrfmon.notify.time.time", lambda: 1000.0)
    notifier.connectivity_warning(4, 5)

    # Second call at t=1599: 599 < 600 -> suppressed
    monkeypatch.setattr("vrfmon.notify.time.time", lambda: 1599.0)
    notifier.connectivity_warning(4, 5)
    assert len(delivered) == 1


def test_connectivity_warning_fires_at_interval_boundary(monkeypatch):
    notifier = Notifier()
    delivered = []
    monkeypatch.setattr(notifier, "_console", lambda text: delivered.append(text))

    monkeypatch.setattr("vrfmon.notify.time.time", lambda: 1000.0)
    notifier.connectivity_warning(4, 5)

    # At exactly 600s elapsed: 1600 - 1000 = 600, not < 600 -> fires
    monkeypatch.setattr("vrfmon.notify.time.time", lambda: 1600.0)
    notifier.connectivity_warning(4, 5)
    assert len(delivered) == 2
