"""World operations: load, save, merge, inspect."""

import re
from cli_anything.asciiid.core import editor
from cli_anything.asciiid.core.session import update_session


def load_map(path: str = "") -> dict:
    """Load an .a3d world file.

    Args:
        path: Path to .a3d file. Empty string loads the default map.

    Returns:
        Dict with status and loaded path.
    """
    cmd = f"LOAD_MAP {path}" if path else "LOAD_MAP"
    lines = editor.send(cmd, timeout=300.0)
    loaded = path or "assets/a3d/game_map_y8.a3d"
    update_session(loaded_map=loaded, modified=False)
    return {"status": "loaded", "path": loaded, "response": lines}


def save_map(path: str) -> dict:
    """Save the current world to an .a3d file.

    Args:
        path: Output path for the .a3d file.

    Returns:
        Dict with status and saved path.
    """
    lines = editor.send(f"SAVE {path}", timeout=30.0)
    update_session(loaded_map=path, modified=False)
    return {"status": "saved", "path": path, "response": lines}


def list_instances() -> list[dict]:
    """List all mesh/sprite instances in the world.

    Returns:
        List of instance dicts with name, id, position fields.
    """
    lines = editor.send("LIST_INSTANCES", timeout=15.0)
    instances = []
    for line in lines:
        # Parse: "name id x y z" or similar format
        parts = line.split()
        if len(parts) >= 4:
            inst = {"raw": line}
            inst["name"] = parts[0]
            try:
                inst["x"] = float(parts[-3])
                inst["y"] = float(parts[-2])
                inst["z"] = float(parts[-1])
            except (ValueError, IndexError):
                pass
            instances.append(inst)
        elif line.strip():
            instances.append({"raw": line})
    return instances


def render() -> dict:
    """Render the current view to base64-encoded ASCII cells.

    Returns:
        Dict with width, height, format, data keys.
    """
    proc = editor.get_process()
    return proc.send_render(timeout=30.0)


def dump_matrix() -> list[str]:
    """Dump material shade tables as JSON.

    Returns:
        List of response lines containing the JSON dump.
    """
    return editor.send("DUMP_MATRIX", timeout=10.0)


def debug_axis() -> dict:
    """Place debug axis cubes at origin.

    Returns:
        Dict with status and response lines.
    """
    lines = editor.send("DEBUG_AXIS", timeout=10.0)
    return {"status": "placed", "response": lines}


def echo(text: str) -> str:
    """Send an echo command (connectivity test).

    Returns:
        The echoed text.
    """
    lines = editor.send(f"ECHO {text}", timeout=30.0)
    return lines[0] if lines else ""
