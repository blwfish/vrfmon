"""Per-camera live chat capture: run yt-dlp's live_chat subtitle downloader
as a subprocess, tail its output file as it grows, and insert parsed rows
into the chat database. Runs forever, restarting yt-dlp with backoff
whenever the stream isn't live or the process exits.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse

from .chatlog import insert_message, parse_action


def _env():
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "cam"


def video_id_from_url(url):
    query = parse_qs(urlparse(url).query)
    return (query.get("v") or [None])[0]


class ChatWorker:
    """Owns one yt-dlp subprocess for one camera's chat and tails its output."""

    def __init__(self, camera, cfg, db, db_lock):
        self.camera = camera
        self.cfg = cfg
        self.db = db
        self.db_lock = db_lock
        self.video_id = video_id_from_url(camera["url"])
        self._proc = None
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()

    def run_forever(self):
        backoff = self.cfg["chat_restart_backoff_seconds"]
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception as e:
                print(f"[chat:{self.camera['name']}] worker error: {e!r}")
            if self._stop.is_set():
                break
            time.sleep(backoff)

    def _run_once(self):
        tmp_dir = tempfile.mkdtemp(prefix="vrfchat_")
        out_template = os.path.join(tmp_dir, "chat.%(ext)s")
        out_path = os.path.join(tmp_dir, "chat.live_chat.json")
        part_path = out_path + ".part"
        stderr_path = os.path.join(tmp_dir, "stderr.log")
        yt_dlp = self.cfg["yt_dlp"]
        cmd = [
            yt_dlp, "--no-warnings", "--skip-download",
            "--write-subs", "--sub-langs", "live_chat", "--sub-format", "json",
            "-o", out_template, self.camera["url"],
        ]
        try:
            with open(stderr_path, "w") as stderr_f:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=stderr_f, env=_env(),
                )
        except FileNotFoundError:
            print(f"[chat:{self.camera['name']}] yt-dlp not found ({yt_dlp})")
            self._cleanup(tmp_dir)
            return

        offset = 0
        pending = ""
        try:
            while self._proc.poll() is None and not self._stop.is_set():
                time.sleep(self.cfg["chat_tail_interval_seconds"])
                offset, pending = self._drain(part_path, offset, pending)
            offset, pending = self._drain(part_path, offset, pending)
            offset, pending = self._drain(out_path, offset, pending)
        finally:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            detail = self._last_error_line(stderr_path)
            self._cleanup(tmp_dir)

        if detail:
            print(f"[chat:{self.camera['name']}] yt-dlp: {detail}")

    @staticmethod
    def _last_error_line(stderr_path):
        try:
            with open(stderr_path, "r", errors="replace") as f:
                lines = [l for l in f if l.strip()]
        except OSError:
            return None
        error_lines = [l.strip() for l in lines if "ERROR" in l]
        return error_lines[-1] if error_lines else None

    def _drain(self, path, offset, pending):
        """Read any bytes appended to `path` since `offset`, insert full lines."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
        except (FileNotFoundError, OSError):
            return offset, pending

        if not chunk:
            return offset, pending

        pending += chunk
        lines = pending.split("\n")
        pending = lines.pop()  # last element may be an incomplete line
        for line in lines:
            self._ingest_line(line)
        return offset, pending

    def _ingest_line(self, line):
        line = line.strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(f"[chat:{self.camera['name']}] skipping unparsable line: {line[:120]!r}")
            return

        actions = (obj.get("replayChatItemAction") or {}).get("actions") or []
        ingest_epoch = int(time.time())
        rows = [parse_action(a, self.camera["name"], self.video_id, ingest_epoch) for a in actions]
        rows = [r for r in rows if r is not None]
        if not rows:
            return
        with self.db_lock:
            for row in rows:
                insert_message(self.db, row)
            self.db.commit()

    def _cleanup(self, tmp_dir):
        try:
            for name in os.listdir(tmp_dir):
                os.remove(os.path.join(tmp_dir, name))
            os.rmdir(tmp_dir)
        except OSError:
            pass
