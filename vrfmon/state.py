"""Runtime state persistence and the append-only incident log."""
import json
import os


def load_state(path):
    state = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            state = {}
    state.setdefault("cameras", {})
    return state


def save_state(state, path):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def append_incident(path, record):
    """Append one transition as a JSON line. This file is the dataset for
    after-the-fact analysis (e.g. correlating a flaky camera with train times)."""
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
