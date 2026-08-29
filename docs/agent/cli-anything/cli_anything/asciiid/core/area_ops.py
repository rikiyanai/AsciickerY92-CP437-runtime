"""Area operations: polygon-based terrain and instance manipulation."""

import math

from cli_anything.asciiid.core import markers, terrain, world, instances
from cli_anything.asciiid.core.markers import point_in_polygon


def _polygon_from_markers() -> list[tuple[float, float]]:
    """Get the current markers as a 2D polygon (XY only).

    Returns:
        List of (x, y) tuples from placed markers.

    Raises:
        ValueError: If fewer than 3 markers are placed.
    """
    mks = markers.list_all()
    if len(mks) < 3:
        raise ValueError(f"Need at least 3 markers for a polygon, got {len(mks)}")
    return [(m.x, m.y) for m in mks]


def _bbox(polygon: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Compute axis-aligned bounding box of a polygon.

    Returns:
        (min_x, min_y, max_x, max_y)
    """
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def fill_material(polygon: list[tuple[float, float]], mat_id: int) -> dict:
    """Set material for all terrain cells inside a polygon.

    Args:
        polygon: List of (x, y) vertices.
        mat_id: Material ID (0-255).

    Returns:
        Dict with count of modified cells.
    """
    min_x, min_y, max_x, max_y = _bbox(polygon)
    cells = []
    for cy in range(int(math.floor(min_y)), int(math.ceil(max_y)) + 1):
        for cx in range(int(math.floor(min_x)), int(math.ceil(max_x)) + 1):
            if point_in_polygon(cx + 0.5, cy + 0.5, polygon):
                cells.append((cx, cy))

    if not cells:
        return {"status": "empty", "count": 0}

    return terrain.batch_set_cells(mat_id, cells)


def elevate(polygon: list[tuple[float, float]], delta_h: int) -> dict:
    """Raise or lower terrain height for cells inside a polygon.

    Args:
        polygon: List of (x, y) vertices.
        delta_h: Height delta to add.

    Returns:
        Dict with vertex count.
    """
    min_x, min_y, max_x, max_y = _bbox(polygon)
    cells = []
    for cy in range(int(math.floor(min_y)), int(math.ceil(max_y)) + 1):
        for cx in range(int(math.floor(min_x)), int(math.ceil(max_x)) + 1):
            if point_in_polygon(cx + 0.5, cy + 0.5, polygon):
                cells.append((cx, cy))

    if not cells:
        return {"status": "empty", "vertices": 0}

    return terrain.batch_elev_delta(delta_h, cells)


def select_instances(polygon: list[tuple[float, float]]) -> list[dict]:
    """Find all instances with XY position inside a polygon.

    Args:
        polygon: List of (x, y) vertices.

    Returns:
        List of instance dicts that are inside the polygon.
    """
    all_insts = world.list_instances()
    result = []
    for inst in all_insts:
        x = inst.get("x")
        y = inst.get("y")
        if x is not None and y is not None and point_in_polygon(x, y, polygon):
            result.append(inst)
    return result


def transform_instances(polygon: list[tuple[float, float]],
                        sx: float = 1.0, sy: float = 1.0, sz: float = 1.0) -> dict:
    """Scale all instances inside a polygon.

    Args:
        polygon: List of (x, y) vertices.
        sx, sy, sz: Scale factors.

    Returns:
        Dict with count of scaled instances.
    """
    selected = select_instances(polygon)
    count = 0
    for inst in selected:
        idx = inst.get("idx")
        if idx is not None:
            instances.scale(idx, sx, sy, sz)
            count += 1
    return {"status": "scaled", "count": count}


def delete_instances(polygon: list[tuple[float, float]],
                     mesh_filter: str | None = None) -> dict:
    """Delete all instances inside a polygon.

    Deletes in reverse index order to avoid index shifting issues.

    Args:
        polygon: List of (x, y) vertices.
        mesh_filter: Optional mesh name filter (substring match).

    Returns:
        Dict with count of deleted instances.
    """
    selected = select_instances(polygon)
    if mesh_filter:
        selected = [i for i in selected if mesh_filter in i.get("name", "")]

    # Delete in reverse order so indices remain valid
    selected.sort(key=lambda i: i.get("idx", 0), reverse=True)
    count = 0
    for inst in selected:
        idx = inst.get("idx")
        if idx is not None:
            instances.delete(idx)
            count += 1
    return {"status": "deleted", "count": count}
