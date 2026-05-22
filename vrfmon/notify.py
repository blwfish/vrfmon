"""Alert delivery: Slack webhook if configured, otherwise a Slack-shaped
console block that can be copy-pasted into Slack by hand."""
import time

import requests

_REASON = {
    "down_placard": 'showing the "feed currently down" placard',
    "down_offline": "YouTube live stream offline",
    "error": "monitor could not capture a frame",
}

_CONN_WARN_INTERVAL = 600  # seconds; throttle repeated connectivity warnings


def fmt_duration(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


class Notifier:
    def __init__(self, webhook_url=None):
        self.webhook_url = webhook_url
        self._last_conn_warn = 0.0

    def _deliver(self, text):
        if self.webhook_url:
            try:
                resp = requests.post(self.webhook_url, json={"text": text}, timeout=10)
                resp.raise_for_status()
                return
            except Exception as e:
                print(f"[notify] webhook delivery failed ({e!r}); message follows:")
        self._console(text)

    @staticmethod
    def _console(text):
        bar = "-" * 60
        print(f"\n{bar}\n[Slack message - copy below]\n{bar}\n{text}\n{bar}\n")

    def down(self, cam, kind, since):
        flaky = " (known flaky camera)" if cam.get("flaky") else ""
        self._deliver(
            f"*VRF Monitor - DOWN*\n"
            f"*{cam['name']}*{flaky} is down - {_REASON.get(kind, kind)}.\n"
            f"Down since {since}.\n"
            f"<{cam['url']}|open stream>"
        )

    def recovered(self, cam, downtime_seconds):
        self._deliver(
            f"*VRF Monitor - RECOVERED*\n"
            f"*{cam['name']}* is live again.\n"
            f"Downtime: {fmt_duration(downtime_seconds)}."
        )

    def connectivity_warning(self, n_down, total):
        now = time.time()
        if now - self._last_conn_warn < _CONN_WARN_INTERVAL:
            return
        self._last_conn_warn = now
        self._deliver(
            f"*VRF Monitor - CHECK CONNECTIVITY*\n"
            f"{n_down} of {total} cameras look down this cycle - most likely the "
            f"monitor's own network, not VRF. Per-camera alerts suppressed."
        )
