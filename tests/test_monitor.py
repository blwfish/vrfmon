"""Tests for vrfmon/monitor.py — _safe, debounce state machine, and run_cycle."""
import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from vrfmon.capture import CaptureResult, CaptureStatus
from vrfmon.classify import FeedState
from vrfmon.monitor import _safe, _update_camera, run_cycle


# --- _safe ---

def test_safe_plain_spaces():
    assert _safe("Plant City FL") == "Plant_City_FL"


def test_safe_strips_special_chars():
    assert _safe("Foo/Bar!Baz") == "Foo_Bar_Baz"


def test_safe_fallback_on_all_special():
    assert _safe("!!!") == "cam"


def test_safe_strips_trailing_leading_underscores():
    assert _safe(" foo ") == "foo"


# --- helpers ---

def _cam(name="Cam1", flaky=False):
    return {"name": name, "url": "https://example.com", "flaky": flaky}


def _cfg(tmp_path, down=2, up=2):
    return {
        "down_debounce_cycles": down,
        "up_debounce_cycles": up,
        "incident_log": str(tmp_path / "incidents.jsonl"),
    }


def _now():
    return datetime.datetime.now().astimezone()


class MockNotifier:
    def __init__(self):
        self.down_calls = []
        self.recovered_calls = []
        self.conn_warn_calls = []

    def down(self, cam, kind, since):
        self.down_calls.append((cam, kind, since))

    def recovered(self, cam, downtime_seconds):
        self.recovered_calls.append((cam, downtime_seconds))

    def connectivity_warning(self, n_down, total):
        self.conn_warn_calls.append((n_down, total))


# --- debounce: up -> down ---

def test_down_alert_fires_at_debounce_threshold(tmp_path):
    state, notifier, cfg = {"cameras": {}}, MockNotifier(), _cfg(tmp_path, down=2)
    cam, now = _cam(), _now()

    # First failure: streak=1, below threshold
    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    assert notifier.down_calls == []
    assert state["cameras"]["Cam1"]["status"] == "up"

    # Second failure: streak=2, at threshold -> fires
    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    assert len(notifier.down_calls) == 1
    assert state["cameras"]["Cam1"]["status"] == "down"


def test_down_alert_does_not_fire_before_threshold(tmp_path):
    state, notifier, cfg = {"cameras": {}}, MockNotifier(), _cfg(tmp_path, down=3)
    cam, now = _cam(), _now()

    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    assert notifier.down_calls == []
    assert state["cameras"]["Cam1"]["status"] == "up"


def test_down_alert_does_not_double_fire(tmp_path):
    state, notifier, cfg = {"cameras": {}}, MockNotifier(), _cfg(tmp_path, down=1)
    cam, now = _cam(), _now()

    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    assert len(notifier.down_calls) == 1


# --- debounce: down -> up ---

def test_recovered_fires_at_debounce_threshold(tmp_path):
    state, notifier, cfg = {"cameras": {}}, MockNotifier(), _cfg(tmp_path, down=1, up=2)
    cam, now = _cam(), _now()

    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    assert state["cameras"]["Cam1"]["status"] == "down"

    # First OK: streak=1, below threshold
    _update_camera(cam, FeedState.UP, None, "ok", now, state, cfg, notifier)
    assert notifier.recovered_calls == []
    assert state["cameras"]["Cam1"]["status"] == "down"

    # Second OK: streak=2, at threshold -> fires
    _update_camera(cam, FeedState.UP, None, "ok", now, state, cfg, notifier)
    assert len(notifier.recovered_calls) == 1
    assert state["cameras"]["Cam1"]["status"] == "up"


# --- streak resets ---

def test_fail_streak_resets_on_up(tmp_path):
    state, notifier, cfg = {"cameras": {}}, MockNotifier(), _cfg(tmp_path, down=5)
    cam, now = _cam(), _now()

    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    assert state["cameras"]["Cam1"]["fail_streak"] == 2

    _update_camera(cam, FeedState.UP, None, "ok", now, state, cfg, notifier)
    assert state["cameras"]["Cam1"]["fail_streak"] == 0
    assert state["cameras"]["Cam1"]["ok_streak"] == 1


def test_ok_streak_resets_on_fail(tmp_path):
    state, notifier, cfg = {"cameras": {}}, MockNotifier(), _cfg(tmp_path, down=1, up=5)
    cam, now = _cam(), _now()

    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    _update_camera(cam, FeedState.UP, None, "ok", now, state, cfg, notifier)
    _update_camera(cam, FeedState.UP, None, "ok", now, state, cfg, notifier)
    assert state["cameras"]["Cam1"]["ok_streak"] == 2

    _update_camera(cam, FeedState.DOWN_PLACARD, 5, "x", now, state, cfg, notifier)
    assert state["cameras"]["Cam1"]["ok_streak"] == 0
    assert state["cameras"]["Cam1"]["fail_streak"] == 1


# --- INACCESSIBLE ---

def test_inaccessible_leaves_status_unchanged(tmp_path):
    state, notifier, cfg = {"cameras": {}}, MockNotifier(), _cfg(tmp_path, down=1)
    cam, now = _cam(), _now()

    _update_camera(cam, FeedState.INACCESSIBLE, None, "members", now, state, cfg, notifier)
    assert state["cameras"]["Cam1"]["status"] == "up"
    assert state["cameras"]["Cam1"]["fail_streak"] == 0
    assert notifier.down_calls == []


# --- incident log ---

def test_incident_logged_on_down(tmp_path):
    state, notifier, cfg = {"cameras": {}}, MockNotifier(), _cfg(tmp_path, down=1)
    cam, now = _cam(), _now()

    _update_camera(cam, FeedState.DOWN_PLACARD, 7, "x", now, state, cfg, notifier)

    lines = (tmp_path / "incidents.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "down"
    assert rec["camera"] == "Cam1"
    assert rec["phash_distance"] == 7


def test_incident_logged_on_recovery(tmp_path):
    state, notifier, cfg = {"cameras": {}}, MockNotifier(), _cfg(tmp_path, down=1, up=1)
    cam, now = _cam(), _now()

    _update_camera(cam, FeedState.DOWN_PLACARD, 7, "x", now, state, cfg, notifier)
    _update_camera(cam, FeedState.UP, None, "ok", now, state, cfg, notifier)

    lines = (tmp_path / "incidents.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["event"] == "recovered"


# --- run_cycle: all-down suppression ---

def _run_cfg(tmp_path, all_down_fraction=0.75, down=1, up=1):
    return {
        "frames_dir": str(tmp_path / "frames"),
        "all_down_fraction": all_down_fraction,
        "placard_threshold": 10,
        "yt_dlp": "yt-dlp",
        "resolve_timeout_seconds": 30,
        "grab_timeout_seconds": 30,
        "down_debounce_cycles": down,
        "up_debounce_cycles": up,
        "incident_log": str(tmp_path / "incidents.jsonl"),
    }


def test_run_cycle_suppresses_when_all_down(tmp_path):
    cameras = [_cam(f"cam{i}") for i in range(4)]
    notifier = MockNotifier()

    with patch("vrfmon.monitor.capture") as mc, patch("vrfmon.monitor.classify") as mcl:
        mc.return_value = CaptureResult(CaptureStatus.RESOLVE_FAILED, None, "offline")
        mcl.return_value = (FeedState.DOWN_PLACARD, 5)
        run_cycle(cameras, _run_cfg(tmp_path), None, {"cameras": {}}, notifier)

    # 4/4 down (100%) >= 75% -> suppressed, connectivity warning fires
    assert len(notifier.conn_warn_calls) == 1
    assert notifier.down_calls == []


def test_run_cycle_does_not_suppress_below_fraction(tmp_path):
    cameras = [_cam(f"cam{i}") for i in range(4)]
    notifier = MockNotifier()

    with patch("vrfmon.monitor.capture") as mc, patch("vrfmon.monitor.classify") as mcl:
        mc.return_value = CaptureResult(CaptureStatus.GOT_FRAME, "/tmp/f.jpg", "ok")
        # 2 of 4 down = 50%, below 75% threshold -> no suppression
        mcl.side_effect = [
            (FeedState.DOWN_PLACARD, 5),
            (FeedState.DOWN_PLACARD, 5),
            (FeedState.UP, 15),
            (FeedState.UP, 15),
        ]
        run_cycle(cameras, _run_cfg(tmp_path), None, {"cameras": {}}, notifier)

    assert notifier.conn_warn_calls == []
    assert len(notifier.down_calls) == 2


def test_run_cycle_no_suppression_with_fewer_than_3_cameras(tmp_path):
    # Guard: len(cameras) >= 3 must be false to skip suppression with only 2 cameras
    cameras = [_cam("cam0"), _cam("cam1")]
    notifier = MockNotifier()

    with patch("vrfmon.monitor.capture") as mc, patch("vrfmon.monitor.classify") as mcl:
        mc.return_value = CaptureResult(CaptureStatus.RESOLVE_FAILED, None, "offline")
        mcl.return_value = (FeedState.DOWN_PLACARD, 5)
        run_cycle(cameras, _run_cfg(tmp_path), None, {"cameras": {}}, notifier)

    # Both cameras down but < 3 cameras -> suppression skipped, per-camera alerts fire
    assert notifier.conn_warn_calls == []
    assert len(notifier.down_calls) == 2


def test_run_cycle_fraction_boundary_at(tmp_path):
    # Exactly at the fraction boundary: len(not_up) >= fraction * len(cameras)
    # 3 cameras, fraction=0.75: need >= 2.25, so 3 down triggers (3 >= 2.25)
    cameras = [_cam(f"cam{i}") for i in range(3)]
    notifier = MockNotifier()

    with patch("vrfmon.monitor.capture") as mc, patch("vrfmon.monitor.classify") as mcl:
        mc.return_value = CaptureResult(CaptureStatus.RESOLVE_FAILED, None, "offline")
        mcl.return_value = (FeedState.DOWN_PLACARD, 5)
        run_cycle(cameras, _run_cfg(tmp_path, all_down_fraction=0.75), None, {"cameras": {}}, notifier)

    assert len(notifier.conn_warn_calls) == 1
    assert notifier.down_calls == []
