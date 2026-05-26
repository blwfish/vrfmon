"""SQLite store for per-cycle poll results and incidents."""
import json
import os
import sqlite3


_SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    id             INTEGER PRIMARY KEY,
    ts             TEXT    NOT NULL,
    camera         TEXT    NOT NULL,
    feed_state     TEXT    NOT NULL,
    phash_distance INTEGER,
    detail         TEXT,
    fail_streak    INTEGER,
    ok_streak      INTEGER,
    suppressed     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS polls_camera_ts ON polls(camera, ts);
CREATE INDEX IF NOT EXISTS polls_ts        ON polls(ts);

CREATE TABLE IF NOT EXISTS incidents (
    id               INTEGER PRIMARY KEY,
    ts               TEXT    NOT NULL,
    camera           TEXT    NOT NULL,
    event            TEXT    NOT NULL,
    kind             TEXT,
    phash_distance   INTEGER,
    detail           TEXT,
    downtime_seconds INTEGER,
    flaky            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS incidents_camera_ts ON incidents(camera, ts);
CREATE INDEX IF NOT EXISTS incidents_ts        ON incidents(ts);
"""


def open_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def log_poll(conn, ts, camera, feed_state, phash_distance, detail,
             fail_streak, ok_streak, suppressed=False):
    state_val = feed_state.value if hasattr(feed_state, "value") else feed_state
    conn.execute(
        "INSERT INTO polls(ts, camera, feed_state, phash_distance, detail,"
        " fail_streak, ok_streak, suppressed) VALUES (?,?,?,?,?,?,?,?)",
        (ts, camera, state_val, phash_distance, detail,
         fail_streak, ok_streak, int(suppressed)),
    )


def log_incident(conn, record):
    conn.execute(
        "INSERT INTO incidents(ts, camera, event, kind, phash_distance, detail,"
        " downtime_seconds, flaky) VALUES (?,?,?,?,?,?,?,?)",
        (record["ts"], record["camera"], record["event"], record.get("kind"),
         record.get("phash_distance"), record.get("detail"),
         record.get("downtime_seconds"), int(record.get("flaky", False))),
    )


def migrate_from_jsonl(conn, jsonl_path):
    """Import incidents.jsonl into the DB. Skips silently if table already has rows."""
    if not os.path.exists(jsonl_path):
        return
    if conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] > 0:
        return
    imported = 0
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                log_incident(conn, json.loads(line))
                imported += 1
            except (json.JSONDecodeError, KeyError):
                pass
    conn.commit()
    print(f"[db] migrated {imported} incidents from {jsonl_path}")
