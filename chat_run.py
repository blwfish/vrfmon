#!/usr/bin/env python3
"""VRF live chat logger.

Records the YouTube live chat for every camera in cameras.yaml to a SQLite
database (config.yaml: chat_db_file), keeping a rolling window of history
(config.yaml: chat_retention_hours). Independent of the uptime monitor
(run.py/monitor.py) - a stream being "down" on camera and its chat still
being reachable (or vice versa) are unrelated conditions.

Usage:
  python chat_run.py
"""
import os
import threading
import time

import yaml

from vrfmon.chatcapture import ChatWorker
from vrfmon.chatlog import open_db, prune_older_than


def _prune_loop(db, db_lock, cfg, stop):
    while not stop.is_set():
        stop.wait(cfg["chat_prune_interval_seconds"])
        if stop.is_set():
            break
        cutoff = int(time.time()) - cfg["chat_retention_hours"] * 3600
        with db_lock:
            deleted = prune_older_than(db, cutoff)
        if deleted:
            print(f"[chat] pruned {deleted} message(s) older than "
                  f"{cfg['chat_retention_hours']}h")


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    with open("cameras.yaml") as f:
        cameras = [c for c in (yaml.safe_load(f).get("cameras") or []) if c.get("url")]

    if not cameras:
        print("No cameras configured - add entries to cameras.yaml.")
        return

    db = open_db(cfg["chat_db_file"])
    db_lock = threading.Lock()
    stop = threading.Event()

    print(f"VRF Chat Logger - {len(cameras)} camera(s), "
          f"{cfg['chat_retention_hours']}h retention, db={cfg['chat_db_file']}")

    workers = [ChatWorker(cam, cfg, db, db_lock) for cam in cameras]
    threads = [threading.Thread(target=w.run_forever, name=f"chat:{w.camera['name']}", daemon=True)
               for w in workers]
    prune_thread = threading.Thread(
        target=_prune_loop, args=(db, db_lock, cfg, stop), name="chat:prune", daemon=True)

    for t in threads:
        t.start()
    prune_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        stop.set()
        for w in workers:
            w.stop()
        for t in threads:
            t.join(timeout=15)
        db.close()


if __name__ == "__main__":
    main()
