"""Terrain operations: height editing, probing, grid control, polygon painting."""

from cli_anything.asciiid.core import editor


def set_height(height: int) -> dict:
    """Set terrain height for all existing patches.

    Args:
        height: Height value (0-65535 uint16 range).

    Returns:
        Dict with status and response.
    """
    lines = editor.send(f"SET_TERRAIN_HEIGHT {height}", timeout=10.0)
    return {"status": "set", "height": height, "response": lines}


def probe(x: float, y: float) -> dict:
    """Query terrain height at a position.

    Args:
        x: World X coordinate.
        y: World Y coordinate.

    Returns:
        Dict with x, y, height fields.
    """
    lines = editor.send(f"PROBE_TERRAIN {x} {y}", timeout=5.0)
    result = {"x": x, "y": y, "height": None, "response": lines}
    for line in lines:
        try:
            # Parse height from response
            parts = line.split()
            for part in parts:
                try:
                    result["height"] = float(part)
                    break
                except ValueError:
                    continue
        except Exception:
            pass
    return result


def set_grid(alpha: float) -> dict:
    """Set grid overlay visibility.

    Args:
        alpha: Grid opacity (0.0 = hidden, 1.0 = fully visible).

    Returns:
        Dict with status.
    """
    alpha = max(0.0, min(1.0, alpha))
    lines = editor.send(f"SET_GRID {alpha}", timeout=5.0)
    return {"status": "set", "grid_alpha": alpha, "response": lines}


def paint_terrain_poly(mat_id: int, vertices: list) -> dict:
    """Paint terrain material within a convex polygon.

    Args:
        mat_id: Material ID (0-255).
        vertices: List of (x, y) tuples defining the polygon (3-32 vertices).

    Returns:
        Dict with status, mat_id, vertex_count, and response.

    Raises:
        ValueError: If mat_id or vertex count is out of range.
    """
    if not (0 <= mat_id <= 255):
        raise ValueError(f"mat_id must be 0-255, got {mat_id}")
    if len(vertices) < 3:
        raise ValueError(f"Need at least 3 vertices, got {len(vertices)}")
    if len(vertices) > 32:
        raise ValueError(f"Maximum 32 vertices, got {len(vertices)}")

    n = len(vertices)
    coords = " ".join(f"{x} {y}" for x, y in vertices)
    cmd = f"PAINT_TERRAIN_POLY {mat_id} {n} {coords}"
    lines = editor.send(cmd, timeout=10.0)
    return {
        "status": "painted",
        "mat_id": mat_id,
        "vertex_count": n,
        "response": lines,
    }
