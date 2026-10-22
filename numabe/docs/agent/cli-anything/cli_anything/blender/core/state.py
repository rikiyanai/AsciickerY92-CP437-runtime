"""Session state — tracks the current blend file and preferences."""

import json
import os

STATE_PATH = os.path.expanduser("~/.cli-anything-blender-state.json")


def load():
    """Load session state from disk."""
    if os.path.isfile(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save(state):
    """Persist session state to disk."""
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def get_blend_file():
    """Return current blend file path, or None."""
    return load().get("blend_file")


def set_blend_file(path):
    """Set current blend file path."""
    st = load()
    st["blend_file"] = os.path.abspath(path) if path else None
    save(st)


def clear():
    """Clear session state."""
    if os.path.isfile(STATE_PATH):
        os.unlink(STATE_PATH)
