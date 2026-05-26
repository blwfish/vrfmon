"""Poll loop: capture + classify each camera, debounce, alert on transitions."""
import datetime
import os
import re
import time

from .capture import capture
from .classify import classify, FeedState
from .db import log_incident, log_poll
from .state import append_incident, save_state


def _now():
    return datetime.datetime.now().astimezone()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "cam"


def _camera_entry(state, name):
    return state["cameras"].setdefault(name, {
        "status": "up",
        "since": _iso(_now()),
        "fail_streak": 0,
        "ok_streak": 0,
        "down_kind": None,
        "last_state": None,
        "last_distance": None,
        "last_check": None,
    })


def run_cycle(cameras, cfg, reference_hash, state, notifier, db=None):
    os.makedirs(cfg["frames_dir"], exist_ok=True)

    results = {}
    for cam in cameras:
        frame_path = os.path.join(cfg["frames_dir"], _safe(cam["name"]) + ".jpg")
        try:
            cap = capture(cam["url"], frame_path, cfg)
            feed_state, distance = classify(cap, reference_hash, cfg["placard_threshold"])
            results[cam["name"]] = (feed_state, distance, cap.detail)
        except Exception as e:
            results[cam["name"]] = (FeedState.ERROR, None, repr(e))

    now = _now()
    ts = _iso(now)
    print(f"[{ts}] cycle:")
    for cam in cameras:
        fs, dist, _ = results[cam["name"]]
        d = "" if dist is None else f" d={dist}"
        print(f"  {cam['name']}: {fs.value}{d}")

    not_up = [n for n, (fs, _, _) in results.items()
              if fs not in (FeedState.UP, FeedState.INACCESSIBLE)]
    suppressed = len(cameras) >= 3 and len(not_up) >= cfg["all_down_fraction"] * len(cameras)

    if suppressed:
        # A near-total outage almost always means the monitor's own uplink,
        # not VRF. Warn once and skip state updates, so we don't later emit a
        # storm of false RECOVERED alerts when our own network returns.
        if db is not None:
            for cam in cameras:
                fs, dist, detail = results[cam["name"]]
                entry = _camera_entry(state, cam["name"])
                log_poll(db, ts, cam["name"], fs, dist, detail,
                         entry["fail_streak"], entry["ok_streak"], suppressed=True)
            db.commit()
        notifier.connectivity_warning(len(not_up), len(cameras))
        return

    for cam in cameras:
        feed_state, distance, detail = results[cam["name"]]
        _update_camera(cam, feed_state, distance, detail, now, state, cfg, notifier, db)

    if db is not None:
        db.commit()


def _update_camera(cam, feed_state, distance, detail, now, state, cfg, notifier, db=None):
    entry = _camera_entry(state, cam["name"])
    entry["last_state"] = feed_state.value
    entry["last_distance"] = distance
    entry["last_check"] = _iso(now)
    ts = _iso(now)

    if feed_state is not FeedState.INACCESSIBLE:
        if feed_state is FeedState.UP:
            entry["ok_streak"] += 1
            entry["fail_streak"] = 0
        else:
            entry["fail_streak"] += 1
            entry["ok_streak"] = 0

        if entry["status"] == "up" and entry["fail_streak"] >= cfg["down_debounce_cycles"]:
            entry["status"] = "down"
            entry["since"] = ts
            entry["down_kind"] = feed_state.value
            incident = {
                "ts": ts,
                "camera": cam["name"],
                "event": "down",
                "kind": feed_state.value,
                "phash_distance": distance,
                "detail": detail,
                "flaky": bool(cam.get("flaky", False)),
            }
            append_incident(cfg["incident_log"], incident)
            if db is not None:
                log_incident(db, incident)
            notifier.down(cam, feed_state.value, entry["since"])

        elif entry["status"] == "down" and entry["ok_streak"] >= cfg["up_debounce_cycles"]:
            down_since = datetime.datetime.fromisoformat(entry["since"])
            downtime = (now - down_since).total_seconds()
            entry["status"] = "up"
            entry["since"] = ts
            incident = {
                "ts": ts,
                "camera": cam["name"],
                "event": "recovered",
                "kind": entry["down_kind"],
                "phash_distance": distance,
                "downtime_seconds": round(downtime),
                "flaky": bool(cam.get("flaky", False)),
            }
            append_incident(cfg["incident_log"], incident)
            if db is not None:
                log_incident(db, incident)
            notifier.recovered(cam, downtime)
            entry["down_kind"] = None

    if db is not None:
        log_poll(db, ts, cam["name"], feed_state, distance, detail,
                 entry["fail_streak"], entry["ok_streak"], suppressed=False)


def run(cameras, cfg, reference_hash, state, notifier, db=None, once=False):
    while True:
        try:
            run_cycle(cameras, cfg, reference_hash, state, notifier, db)
        except Exception as e:
            print(f"[cycle error] {e!r}")
        save_state(state, cfg["state_file"])
        if once:
            break
        time.sleep(cfg["poll_interval_seconds"])
