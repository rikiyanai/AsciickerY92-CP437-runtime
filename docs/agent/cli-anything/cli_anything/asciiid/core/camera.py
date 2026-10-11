"""Camera control: get/set position and orientation."""

import re

from cli_anything.asciiid.core import editor


def get() -> dict:
    """Get current camera state.

    Returns:
        Dict with x, y, z, yaw, pitch, zoom fields.
    """
    lines = editor.send("GET_CAMERA", timeout=5.0)
    result = {"x": 0, "y": 0, "z": 0, "yaw": 0, "pitch": 0, "zoom": 0}
    for line in lines:
        pos_match = re.search(
            r"pos=([-+]?\d*\.?\d+),([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)",
            line,
        )
        if pos_match:
            result["x"] = float(pos_match.group(1))
            result["y"] = float(pos_match.group(2))
            result["z"] = float(pos_match.group(3))

        parts = line.split()
        # Parse key=value or positional format
        for part in parts:
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.lower().strip()
                if k == "pos":
                    continue
                try:
                    result[k] = float(v)
                except (ValueError, KeyError):
                    pass
            else:
                try:
                    val = float(part)
                    # Assign positionally: x, y, z, yaw, pitch, zoom
                    for key in ("x", "y", "z", "yaw", "pitch", "zoom"):
                        if result[key] == 0:
                            result[key] = val
                            break
                except ValueError:
                    pass
    result["response"] = lines
    return result


def set(x: float, y: float, z: float, yaw: float,
        pitch: float = 60.0) -> dict:
    """Set camera position and orientation.

    Args:
        x, y, z: World position.
        yaw: Horizontal rotation in degrees.
        pitch: Vertical angle (30-90 degrees).

    Returns:
        Dict with status.
    """
    lines = editor.send(
        f"SET_CAMERA {x} {y} {z} {yaw} {pitch}", timeout=5.0
    )
    return {
        "status": "set",
        "x": x, "y": y, "z": z,
        "yaw": yaw, "pitch": pitch,
        "response": lines,
    }


def focus_origin() -> dict:
    """Jump camera to world origin (0, 0, 0) with yaw=45.

    Returns:
        Dict with status.
    """
    lines = editor.send("FOCUS_ORIGIN", timeout=5.0)
    return {"status": "focused", "x": 0, "y": 0, "z": 0, "yaw": 45, "response": lines}
