"""Tests for vrfmon/state.py — persistence and incident log."""
import json
import os
import pytest

from vrfmon.state import append_incident, load_state, save_state


def test_load_state_missing_file(tmp_path):
    state = load_state(str(tmp_path / "nope.json"))
    assert state == {"cameras": {}}


def test_load_state_corrupt_file(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json")
    state = load_state(str(p))
    assert state == {"cameras": {}}


def test_load_state_valid_file(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"cameras": {"cam1": {"status": "up"}}}))
    state = load_state(str(p))
    assert state["cameras"]["cam1"]["status"] == "up"


def test_load_state_injects_cameras_key_if_absent(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({}))
    state = load_state(str(p))
    assert "cameras" in state


def test_save_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    data = {"cameras": {"cam": {"status": "down"}}}
    save_state(data, str(p))
    with open(p) as f:
        loaded = json.load(f)
    assert loaded == data


def test_save_state_no_tmp_file_left_behind(tmp_path):
    p = tmp_path / "state.json"
    save_state({"cameras": {}}, str(p))
    assert not os.path.exists(str(p) + ".tmp")
    assert p.exists()


def test_append_incident_creates_file_and_appends(tmp_path):
    p = tmp_path / "incidents.jsonl"
    append_incident(str(p), {"event": "down", "camera": "cam1"})
    append_incident(str(p), {"event": "recovered", "camera": "cam1"})
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "down"
    assert json.loads(lines[1])["event"] == "recovered"
