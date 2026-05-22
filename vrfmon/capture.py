"""Resolve a YouTube live URL and grab a single frame."""
import os
import subprocess
from dataclasses import dataclass
from enum import Enum


class CaptureStatus(Enum):
    GOT_FRAME = "got_frame"
    RESOLVE_FAILED = "resolve_failed"  # yt-dlp found no live stream -> camera offline
    GRAB_FAILED = "grab_failed"        # stream resolved but no frame came back
    MEMBERS_ONLY = "members_only"      # gated stream -> not an outage, can't read


@dataclass
class CaptureResult:
    status: CaptureStatus
    frame_path: str | None
    detail: str


def _env():
    # Homebrew's bin is not on PATH inside the sandbox; yt-dlp/ffmpeg live there.
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


def _last_line(text, fallback):
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return lines[-1] if lines else fallback


def resolve_stream(url, yt_dlp="yt-dlp", timeout=30, cookies_from_browser=None):
    """Return (hls_url, detail). hls_url is None when no live stream is found."""
    cmd = [yt_dlp, "-g", "--no-warnings"]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd.append(url)
    try:
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout, env=_env(),
        )
    except subprocess.TimeoutExpired:
        return None, "yt-dlp timed out"
    except FileNotFoundError:
        return None, f"yt-dlp not found ({yt_dlp})"
    if r.returncode != 0:
        return None, _last_line(r.stderr, "yt-dlp failed")
    urls = [l for l in r.stdout.splitlines() if l.strip()]
    if not urls:
        return None, "yt-dlp returned no stream URL"
    return urls[0], "ok"


def grab_frame(stream_url, out_path, ffmpeg="ffmpeg", timeout=30):
    """Return (ok, detail). Writes one JPEG frame to out_path on success."""
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-rw_timeout", "20000000",
             "-i", stream_url, "-frames:v", "1", "-update", "1",
             "-q:v", "2", out_path],
            capture_output=True, text=True, timeout=timeout, env=_env(),
        )
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out"
    except FileNotFoundError:
        return False, f"ffmpeg not found ({ffmpeg})"
    if r.returncode != 0 or not os.path.exists(out_path):
        return False, _last_line(r.stderr, "ffmpeg failed")
    return True, "ok"


def classify_resolve_error(detail):
    """Single source of truth for interpreting a failed yt-dlp resolve.

    Matches yt-dlp's error text: a members-only gate is not an outage and
    must not be reported as a down camera.
    """
    if "members" in (detail or "").lower():
        return CaptureStatus.MEMBERS_ONLY
    return CaptureStatus.RESOLVE_FAILED


def capture(url, out_path, cfg):
    stream_url, detail = resolve_stream(
        url, cfg["yt_dlp"], cfg["resolve_timeout_seconds"],
        cfg.get("cookies_from_browser"),
    )
    if stream_url is None:
        return CaptureResult(classify_resolve_error(detail), None, detail)
    ok, detail = grab_frame(stream_url, out_path, cfg["ffmpeg"], cfg["grab_timeout_seconds"])
    if not ok:
        return CaptureResult(CaptureStatus.GRAB_FAILED, None, detail)
    return CaptureResult(CaptureStatus.GOT_FRAME, out_path, "ok")
