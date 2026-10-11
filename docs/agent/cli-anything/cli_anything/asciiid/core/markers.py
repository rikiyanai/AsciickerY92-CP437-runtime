"""Marker operations: place, list, delete, clear coordinate markers."""

import re
from dataclasses import dataclass

from cli_anything.asciiid.core import editor


@dataclass
class Marker:
    id: int
    x: float
    y: float
    z: float


def place(x: float, y: float, z: float) -> Marker:
    """Place a coordinate marker at the given position.

    Returns:
        Marker dataclass with id and position.
    """
    lines = editor.command(f"PLACE_MARKER {x} {y} {z}")
    # Parse: "Success: Marker <id> at <x>,<y>,<z>"
    for line in lines:
        m = re.search(r"Marker (\d+) at", line)
        if m:
            return Marker(id=int(m.group(1)), x=x, y=y, z=z)
    return Marker(id=0, x=x, y=y, z=z)


def list_all() -> list[Marker]:
    """List all placed markers.

    Returns:
        List of Marker dataclasses.
    """
    lines = editor.command("LIST_MARKERS")
    markers = []
    for line in lines:
        m = re.search(r"Marker (\d+): ([-\d.]+),([-\d.]+),([-\d.]+)", line)
        if m:
            markers.append(Marker(
                id=int(m.group(1)),
                x=float(m.group(2)),
                y=float(m.group(3)),
                z=float(m.group(4)),
            ))
    return markers


def delete(marker_id: int) -> dict:
    """Delete a marker by ID.

    Returns:
        Dict with status and response.
    """
    lines = editor.command(f"DELETE_MARKER {marker_id}")
    success = any("Success" in l for l in lines)
    return {"status": "deleted" if success else "error", "id": marker_id, "response": lines}


def clear() -> dict:
    """Clear all markers.

    Returns:
        Dict with status and count of cleared markers.
    """
    lines = editor.command("CLEAR_MARKERS")
    count = 0
    for line in lines:
        m = re.search(r"Cleared (\d+)", line)
        if m:
            count = int(m.group(1))
    return {"status": "cleared", "count": count, "response": lines}


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting algorithm for point-in-polygon test.

    Args:
        x, y: Test point coordinates.
        polygon: List of (x, y) vertices defining the polygon.

    Returns:
        True if the point is inside the polygon.
    """
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside
