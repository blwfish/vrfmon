#!/usr/bin/env python3
"""VRF camera monitor - prototype entry point.

Usage:
  python run.py          run continuously
  python run.py --once   run a single poll cycle and exit

Set SLACK_WEBHOOK_URL in the environment to post alerts to Slack; otherwise
alerts print to the console as copy-pasteable Slack-formatted blocks.
"""
import os
import sys

import yaml

from vrfmon.classify import load_reference_hash
from vrfmon.monitor import run
from vrfmon.notify import Notifier
from vrfmon.state import load_state


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

    reference_hash = load_reference_hash(cfg["reference_placard"])
    state = load_state(cfg["state_file"])
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    notifier = Notifier(webhook_url=webhook)

    mode = "Slack webhook" if webhook else "console"
    print(f"VRF Monitor - {len(cameras)} camera(s), {mode} mode, "
          f"every {cfg['poll_interval_seconds']}s")

    run(cameras, cfg, reference_hash, state, notifier, once="--once" in sys.argv)


if __name__ == "__main__":
    main()
