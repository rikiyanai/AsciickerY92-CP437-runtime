#!/usr/bin/env python3
"""Standalone isometric tile prototype."""

from __future__ import annotations

import argparse
from typing import Sequence

Point = tuple[int, int]

DEFAULT_HEIGHTS: tuple[tuple[int, ...], ...] = (
    (0, 0, 0),
    (0, 1, 1),
    (1, 1, 2),
)

TOP_FILLS = (" ", ".", "o", "O")
LEFT_FILL = ":"
RIGHT_FILL = ";"
EDGE_FILL = "#"

FLAT_FILLS = (".", "o", "O", "#")


def parse_heights(text: str) -> tuple[tuple[int, ...], ...]:
    rows = []
    for raw_row in text.split(";"):
        row = tuple(int(cell.strip()) for cell in raw_row.split(",") if cell.strip())
        if row:
            rows.append(row)
    if not rows:
        raise ValueError("height map must not be empty")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("height rows must all have the same width")
    if any(cell < 0 for row in rows for cell in row):
        raise ValueError("heights must be non-negative")
    return tuple(rows)


def project2d(grid_x: int, grid_y: int, tile_width: int, tile_height: int) -> Point:
    half_w = tile_width // 2
    half_h = tile_height // 2
    iso_x = (grid_x - grid_y) * half_w
    iso_y = (grid_x + grid_y) * half_h
    return iso_x, iso_y


def project3d(grid_x: int, grid_y: int, height: int, tile_width: int, tile_height: int, elevation_step: int) -> Point:
    iso_x, iso_y = project2d(grid_x, grid_y, tile_width, tile_height)
    return iso_x, iso_y - height * elevation_step


def _top_fill(height: int) -> str:
    return TOP_FILLS[min(height, len(TOP_FILLS) - 1)]


def _put(canvas: list[list[str]], x: int, y: int, char: str) -> None:
    if 0 <= y < len(canvas) and 0 <= x < len(canvas[0]) and char != " ":
        canvas[y][x] = char


def _point_on_segment(px: float, py: float, start: Point, end: Point) -> bool:
    x1, y1 = start
    x2, y2 = end
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > 1e-6:
        return False
    dot = (px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)
    if dot < 0:
        return False
    squared_len = (x2 - x1) ** 2 + (y2 - y1) ** 2
    return dot <= squared_len


def _point_in_polygon(px: float, py: float, polygon: Sequence[Point]) -> bool:
    inside = False
    for idx, current in enumerate(polygon):
        nxt = polygon[(idx + 1) % len(polygon)]
        if _point_on_segment(px, py, current, nxt):
            return True
        x1, y1 = current
        x2, y2 = nxt
        intersects = ((y1 > py) != (y2 > py)) and (
            px < (x2 - x1) * (py - y1) / (y2 - y1 + 1e-12) + x1
        )
        if intersects:
            inside = not inside
    return inside


def _draw_line(canvas: list[list[str]], start: Point, end: Point, char: str) -> None:
    x1, y1 = start
    x2, y2 = end
    dx = abs(x2 - x1)
    dy = abs(y2 - y1)

    if dy == 0:
        # Horizontal segment: draw every pixel.
        sx = 1 if x2 > x1 else -1
        x = x1
        while True:
            _put(canvas, x, y1, char)
            if x == x2:
                break
            x += sx
        return

    if dx <= 2 * dy:
        # Standard Bresenham — slope ≤ 2:1, at most 2 chars per row.
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy
        while True:
            _put(canvas, x1, y1, char)
            if x1 == x2 and y1 == y2:
                break
            err2 = 2 * err
            if err2 > -dy:
                err -= dy
                x1 += sx
            if err2 < dx:
                err += dx
                y1 += sy
    else:
        # Flat line (slope > 2:1): one char per row at the interpolated position.
        # Avoids the ////\\\\ blobs that Bresenham produces for very flat diagonals.
        sy = 1 if y2 > y1 else -1
        for i in range(dy + 1):
            y = y1 + i * sy
            x = round(x1 + (x2 - x1) * i / dy)
            _put(canvas, x, y, char)


def _fill_polygon(canvas: list[list[str]], polygon: Sequence[Point], fill_char: str) -> None:
    if not polygon:
        return
    min_x = max(min(x for x, _ in polygon), 0)
    max_x = min(max(x for x, _ in polygon), len(canvas[0]) - 1)
    min_y = max(min(y for _, y in polygon), 0)
    max_y = min(max(y for _, y in polygon), len(canvas) - 1)
    # Skip the apex row (min_y): at a sharp diamond tip the pixel-center test
    # includes cells adjacent to the vertex that lie outside the visible edge.
    # Always write — including space — so later tiles in painter's order correctly
    # erase shared-edge chars from earlier tiles, preventing \\\ / /// artifacts.
    for y in range(min_y + 1, max_y + 1):
        for x in range(min_x, max_x + 1):
            if _point_in_polygon(x + 0.5, y + 0.5, polygon):
                if 0 <= y < len(canvas) and 0 <= x < len(canvas[0]):
                    canvas[y][x] = fill_char


def _segment_char(start: Point, end: Point) -> str:
    if start[1] == end[1]:
        return "_"
    if (end[0] - start[0]) * (end[1] - start[1]) > 0:
        return "\\"
    return "/"


def _paint_polygon(
    canvas: list[list[str]],
    polygon: Sequence[Point],
    fill_char: str,
    edge_mask: Sequence[bool] | None = None,
) -> None:
    if not polygon:
        return
    _fill_polygon(canvas, polygon, fill_char)
    for idx, start in enumerate(polygon):
        end = polygon[(idx + 1) % len(polygon)]
        if edge_mask is not None and not edge_mask[idx]:
            continue
        _draw_line(canvas, start, end, _segment_char(start, end))


def _iter_tiles(heights: Sequence[Sequence[int]]) -> list[tuple[int, int, int]]:
    tiles: list[tuple[int, int, int]] = []
    for grid_y, row in enumerate(heights):
        for grid_x, height in enumerate(row):
            tiles.append((grid_x, grid_y, height))
    return tiles


def _height_at(heights: Sequence[Sequence[int]], grid_x: int, grid_y: int) -> int:
    if 0 <= grid_y < len(heights) and 0 <= grid_x < len(heights[grid_y]):
        return heights[grid_y][grid_x]
    return 0


def render_flat(
    heights: Sequence[Sequence[int]],
    padding_x: int = 2,
    padding_y: int = 1,
) -> list[str]:
    """Flat staggered-diamond map: 1 char per tile, no 3D geometry."""
    tiles = _iter_tiles(heights)
    # iso projection: x = gx - gy, y = gx + gy (integer, no scale factor)
    projected = [(4 * (gx - gy), gx + gy, h) for gx, gy, h in tiles]

    min_sx = min(sx for sx, _, _ in projected)
    max_sx = max(sx for sx, _, _ in projected)
    min_sy = min(sy for _, sy, _ in projected)
    max_sy = max(sy for _, sy, _ in projected)

    width = max_sx - min_sx + padding_x * 2 + 1
    height = max_sy - min_sy + padding_y * 2 + 1
    ox = padding_x - min_sx
    oy = padding_y - min_sy

    canvas: list[list[str]] = [[" " for _ in range(width)] for _ in range(height)]
    for sx, sy, h in projected:
        char = FLAT_FILLS[min(h, len(FLAT_FILLS) - 1)]
        canvas[sy + oy][sx + ox] = char

    return ["".join(row).rstrip() for row in canvas]


def render_scene(
    heights: Sequence[Sequence[int]],
    tile_width: int = 12,
    tile_height: int = 2,
    elevation_step: int = 1,
    padding_x: int = 4,
    padding_y: int = 2,
) -> list[str]:
    tiles = _iter_tiles(heights)
    half_w = tile_width // 2
    half_h = tile_height // 2
    projected = []
    for grid_x, grid_y, tile_z in tiles:
        center_x, top_y = project3d(grid_x, grid_y, tile_z, tile_width, tile_height, elevation_step)
        south_neighbor = _height_at(heights, grid_x, grid_y + 1)
        east_neighbor = _height_at(heights, grid_x + 1, grid_y)
        expose_left = max(tile_z - south_neighbor, 0)
        expose_right = max(tile_z - east_neighbor, 0)
        top = (
            (center_x, top_y),
            (center_x + half_w, top_y + half_h),
            (center_x, top_y + tile_height),
            (center_x - half_w, top_y + half_h),
        )
        left = ()
        right = ()
        if expose_left:
            left = (
                top[3],
                top[2],
                (top[2][0], top[2][1] + expose_left * elevation_step),
                (top[3][0], top[3][1] + expose_left * elevation_step),
            )
        if expose_right:
            right = (
                top[1],
                top[2],
                (top[2][0], top[2][1] + expose_right * elevation_step),
                (top[1][0], top[1][1] + expose_right * elevation_step),
            )
        projected.append((grid_x, grid_y, tile_z, top, left, right))

    all_points = [
        point
        for _, _, _, top, left, right in projected
        for polygon in (top, left, right)
        for point in polygon
    ]
    min_x = min(x for x, _ in all_points)
    max_x = max(x for x, _ in all_points)
    min_y = min(y for _, y in all_points)
    max_y = max(y for _, y in all_points)

    width = max_x - min_x + padding_x * 2 + 1
    height = max_y - min_y + padding_y * 2 + 1
    offset_x = padding_x - min_x
    offset_y = padding_y - min_y
    canvas: list[list[str]] = [[" " for _ in range(width)] for _ in range(height)]

    ordered_tiles = sorted(projected, key=lambda tile: (tile[0] + tile[1], tile[2], tile[1], tile[0]))
    for _, _, tile_z, top, left, right in ordered_tiles:
        shifted_top = tuple((x + offset_x, y + offset_y) for x, y in top)
        shifted_left = tuple((x + offset_x, y + offset_y) for x, y in left)
        shifted_right = tuple((x + offset_x, y + offset_y) for x, y in right)
        _paint_polygon(canvas, shifted_left, LEFT_FILL)
        _paint_polygon(canvas, shifted_right, RIGHT_FILL)
        _paint_polygon(canvas, shifted_top, _top_fill(tile_z))

    return ["".join(row).rstrip() for row in canvas]


def render_text(
    heights: Sequence[Sequence[int]],
    tile_width: int = 12,
    tile_height: int = 2,
    elevation_step: int = 1,
) -> str:
    rows = render_scene(
        heights,
        tile_width=tile_width,
        tile_height=tile_height,
        elevation_step=elevation_step,
    )
    return "\n".join(row for row in rows if row.strip())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--heights",
        default=";".join(",".join(str(cell) for cell in row) for row in DEFAULT_HEIGHTS),
        help="Semicolon-separated rows of comma-separated heights. Example: 0,1,0;1,2,1;2,3,2",
    )
    parser.add_argument("--tile-width", type=int, default=12)
    parser.add_argument("--tile-height", type=int, default=2)
    parser.add_argument("--elevation-step", type=int, default=1)
    parser.add_argument("--mode", choices=["flat", "iso"], default="flat")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    heights = parse_heights(args.heights)
    if args.mode == "flat":
        rows = render_flat(heights)
        print("\n".join(row for row in rows if row.strip()))
    else:
        print(
            render_text(
                heights,
                tile_width=args.tile_width,
                tile_height=args.tile_height,
                elevation_step=args.elevation_step,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
