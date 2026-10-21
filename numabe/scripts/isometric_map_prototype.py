#!/usr/bin/env python3
"""Standalone flat isometric map prototype backed by real A3D terrain data."""

from __future__ import annotations

import argparse
import curses
import importlib.util
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAP = "assets/a3d/game_map_y8.a3d"
DEFAULT_SOURCE_WIDTH = 256
DEFAULT_SOURCE_HEIGHT = 256
HUD_ROWS = 2
PADDING_X = 2
PADDING_Y = 1
ZOOM_STEPS = [1, 2, 3, 4, 6, 8, 10, 12, 14, 16, 20, 24, 32, 40, 48, 64, 78, 96, 128]

# Matches the repo's minimap material language closely enough for overview use.
MATERIAL_GLYPHS = {
    0: "~",  # water
    1: ".",  # grass
    2: ":",  # dirt
    3: "#",  # stone
    4: "%",  # road / pale ground
    5: "*",  # blood
    6: "~",  # mud / wet
    7: "o",  # cobblestone
    8: ";",  # gravel
}
DEFAULT_GLYPH = "?"
MATERIAL_STYLE_KIND = {
    0: "water",
    1: "grass",
    2: "dirt",
    3: "stone",
    4: "road",
    5: "blood",
    6: "water",
    7: "stone",
    8: "stone",
}


class Bucket:
    __slots__ = ("material_counts", "total_count")

    def __init__(self) -> None:
        self.material_counts: dict[int, int] = defaultdict(int)
        self.total_count = 0

    def add(self, material_id: int) -> None:
        self.material_counts[material_id] += 1
        self.total_count += 1

    def dominant_material(self) -> int:
        return max(self.material_counts.items(), key=lambda item: (item[1], -item[0]))[0]


@dataclass(frozen=True)
class MapData:
    map_path: Path
    patches: list
    materials: list
    visual_cells: int
    min_cell_x: int
    min_cell_y: int
    max_cell_x: int
    max_cell_y: int
    full_width: int
    full_height: int
    center_x: int
    center_y: int


@dataclass(frozen=True)
class ProjectedCell:
    x: int
    y: int
    char: str
    material_id: int | None


def _load_inspector_module():
    script_path = Path(__file__).with_name("inspect_a3d.py")
    spec = importlib.util.spec_from_file_location("inspect_a3d_mod", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_map(path_text: str) -> Path:
    raw = Path(path_text)
    if raw.is_absolute():
        return raw
    direct = Path.cwd() / raw
    if direct.exists():
        return direct.resolve()
    return (Path(__file__).resolve().parent.parent / raw).resolve()


def _material_glyph(materials, material_id: int) -> str:
    if material_id in MATERIAL_GLYPHS:
        return MATERIAL_GLYPHS[material_id]
    fallback = DEFAULT_GLYPH
    if not (0 <= material_id < len(materials)):
        return fallback
    try:
        glyph_code = materials[material_id].shade[0][8].gl
    except Exception:
        return fallback
    if 33 <= glyph_code <= 126:
        return chr(glyph_code)
    return fallback


def _load_map_data(map_path: Path) -> MapData:
    inspect_mod = _load_inspector_module()
    a3d = inspect_mod.read_a3d(str(map_path))
    patches = a3d["patches"]
    materials = a3d["materials"]
    if not patches:
        raise ValueError(f"{map_path} has no terrain patches")

    visual_cells = inspect_mod.VISUAL_CELLS
    min_cell_x = min(patch.x for patch in patches) * visual_cells
    min_cell_y = min(patch.y for patch in patches) * visual_cells
    max_cell_x = max(patch.x for patch in patches) * visual_cells + visual_cells - 1
    max_cell_y = max(patch.y for patch in patches) * visual_cells + visual_cells - 1
    full_width = max_cell_x - min_cell_x + 1
    full_height = max_cell_y - min_cell_y + 1
    return MapData(
        map_path=map_path,
        patches=patches,
        materials=materials,
        visual_cells=visual_cells,
        min_cell_x=min_cell_x,
        min_cell_y=min_cell_y,
        max_cell_x=max_cell_x,
        max_cell_y=max_cell_y,
        full_width=full_width,
        full_height=full_height,
        center_x=(min_cell_x + max_cell_x) // 2,
        center_y=(min_cell_y + max_cell_y) // 2,
    )


def _project_tile_cells(
    tile_entries: dict[tuple[int, int], tuple[str, int | None]],
    padding_x: int = PADDING_X,
    padding_y: int = PADDING_Y,
) -> tuple[list[ProjectedCell], int, int]:
    projected = [
        (4 * (grid_x - grid_y), grid_x + grid_y, char, material_id)
        for (grid_x, grid_y), (char, material_id) in tile_entries.items()
        if char != " "
    ]
    if not projected:
        return [], 0, 0

    min_sx = min(sx for sx, _, _, _ in projected)
    max_sx = max(sx for sx, _, _, _ in projected)
    min_sy = min(sy for _, sy, _, _ in projected)
    max_sy = max(sy for _, sy, _, _ in projected)

    width = max_sx - min_sx + padding_x * 2 + 2
    height = max_sy - min_sy + padding_y * 2 + 1
    ox = padding_x - min_sx
    oy = padding_y - min_sy

    cells: list[ProjectedCell] = []
    for sx, sy, char, material_id in projected:
        x = sx + ox
        y = sy + oy
        if x + 1 < width:
            cells.append(ProjectedCell(x=x, y=y, char=char, material_id=material_id))
            cells.append(ProjectedCell(x=x + 1, y=y, char=char, material_id=material_id))
        else:
            cells.append(ProjectedCell(x=x, y=y, char=char, material_id=material_id))
    return cells, width, height


def _cells_to_rows(cells: list[ProjectedCell], width: int, height: int) -> list[str]:
    if not cells or width <= 0 or height <= 0:
        return []
    canvas: list[list[str]] = [[" " for _ in range(width)] for _ in range(height)]
    for cell in cells:
        if 0 <= cell.y < height and 0 <= cell.x < width:
            canvas[cell.y][cell.x] = cell.char
    return ["".join(row).rstrip() for row in canvas]


def _auto_bucket_size(source_width: int, source_height: int, max_columns: int, max_rows: int) -> int:
    # Flat iso output spans roughly 4 * (w + h) columns and (w + h) rows.
    width_limit = max(1, (max_columns - 4) // 4)
    height_limit = max(1, max_rows - 2)
    tile_sum_limit = max(1, min(width_limit, height_limit))
    return max(1, math.ceil((source_width + source_height) / tile_sum_limit))


def _visible_tile_rect(term_cols: int, term_rows: int, aspect_width: int, aspect_height: int) -> tuple[int, int]:
    map_rows = max(1, term_rows - HUD_ROWS)
    sum_limit_rows = max(2, map_rows - (2 * PADDING_Y - 1))
    sum_limit_cols = max(2, (term_cols - (2 * PADDING_X - 6)) // 4)
    tile_sum = max(2, min(sum_limit_rows, sum_limit_cols))
    total_aspect = max(1, aspect_width + aspect_height)
    tiles_x = max(1, round(tile_sum * aspect_width / total_aspect))
    tiles_y = max(1, tile_sum - tiles_x)
    if tiles_x + tiles_y > tile_sum:
        tiles_y = max(1, tile_sum - tiles_x)
    if tiles_x + tiles_y < tile_sum:
        tiles_y += tile_sum - (tiles_x + tiles_y)
    return tiles_x, tiles_y


def _browser_source_size(
    term_cols: int,
    term_rows: int,
    bucket_size: int,
    aspect_width: int,
    aspect_height: int,
) -> tuple[int, int, int, int]:
    tiles_x, tiles_y = _visible_tile_rect(term_cols, term_rows, aspect_width, aspect_height)
    return tiles_x * bucket_size, tiles_y * bucket_size, tiles_x, tiles_y


def _fit_bucket_for_overview(map_data: MapData, term_cols: int, term_rows: int) -> int:
    tiles_x, tiles_y = _visible_tile_rect(term_cols, term_rows, map_data.full_width, map_data.full_height)
    return max(
        1,
        math.ceil(map_data.full_width / tiles_x),
        math.ceil(map_data.full_height / tiles_y),
    )


def _zoom_step(bucket_size: int, direction: int) -> int:
    steps = sorted(set(ZOOM_STEPS + [bucket_size]))
    index = steps.index(bucket_size)
    next_index = max(0, min(len(steps) - 1, index + direction))
    return steps[next_index]


def _pan_step(source_span: int, bucket_size: int) -> int:
    return max(bucket_size, source_span // 4)


def _init_browser_colors() -> dict[str, int]:
    pairs = {
        "bg": 1,
        "hud": 2,
        "help": 3,
        "grass": 4,
        "water": 5,
        "road": 6,
        "dirt": 7,
        "stone": 8,
        "blood": 9,
        "center": 10,
        "unknown": 11,
    }
    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass

    if curses.COLORS >= 256:
        bg = 16       # fixed xterm-256 black, not theme slot 0
        fg_hud = 255
        fg_help = 81
        fg_grass = 41
        fg_water = 39
        fg_road = 220
        fg_dirt = 179
        fg_stone = 250
        fg_blood = 196
        fg_center = 201
        fg_unknown = 252
    else:
        bg = curses.COLOR_BLACK
        fg_hud = curses.COLOR_WHITE
        fg_help = curses.COLOR_CYAN
        fg_grass = curses.COLOR_GREEN
        fg_water = curses.COLOR_BLUE
        fg_road = curses.COLOR_YELLOW
        fg_dirt = curses.COLOR_YELLOW
        fg_stone = curses.COLOR_WHITE
        fg_blood = curses.COLOR_RED
        fg_center = curses.COLOR_MAGENTA
        fg_unknown = curses.COLOR_WHITE

    curses.init_pair(pairs["bg"], fg_hud, bg)
    curses.init_pair(pairs["hud"], fg_hud, bg)
    curses.init_pair(pairs["help"], fg_help, bg)
    curses.init_pair(pairs["grass"], fg_grass, bg)
    curses.init_pair(pairs["water"], fg_water, bg)
    curses.init_pair(pairs["road"], fg_road, bg)
    curses.init_pair(pairs["dirt"], fg_dirt, bg)
    curses.init_pair(pairs["stone"], fg_stone, bg)
    curses.init_pair(pairs["blood"], fg_blood, bg)
    curses.init_pair(pairs["center"], fg_center, bg)
    curses.init_pair(pairs["unknown"], fg_unknown, bg)
    return {name: curses.color_pair(pair_id) for name, pair_id in pairs.items()}


def _cell_attr(material_id: int | None, color_attrs: dict[str, int]) -> int:
    if material_id is None:
        return color_attrs["center"] | curses.A_BOLD
    kind = MATERIAL_STYLE_KIND.get(material_id, "unknown")
    if kind == "road":
        return color_attrs["road"] | curses.A_BOLD
    if kind == "water":
        return color_attrs["water"] | curses.A_BOLD
    if kind == "grass":
        return color_attrs["grass"]
    if kind == "blood":
        return color_attrs["blood"] | curses.A_BOLD
    if kind == "dirt":
        return color_attrs["dirt"]
    if kind == "stone":
        return color_attrs["stone"]
    return color_attrs["unknown"]


def _crop_bounds(
    min_x: int,
    min_y: int,
    max_x: int,
    max_y: int,
    center_x: int | None,
    center_y: int | None,
    source_width: int | None,
    source_height: int | None,
) -> tuple[int, int, int, int]:
    if source_width is None or source_height is None:
        return min_x, min_y, max_x, max_y

    if center_x is None:
        center_x = (min_x + max_x) // 2
    if center_y is None:
        center_y = (min_y + max_y) // 2

    half_w = source_width // 2
    half_h = source_height // 2
    crop_min_x = center_x - half_w
    crop_min_y = center_y - half_h
    crop_max_x = crop_min_x + source_width - 1
    crop_max_y = crop_min_y + source_height - 1

    if crop_min_x < min_x:
        crop_max_x += min_x - crop_min_x
        crop_min_x = min_x
    if crop_min_y < min_y:
        crop_max_y += min_y - crop_min_y
        crop_min_y = min_y
    if crop_max_x > max_x:
        crop_min_x -= crop_max_x - max_x
        crop_max_x = max_x
    if crop_max_y > max_y:
        crop_min_y -= crop_max_y - max_y
        crop_max_y = max_y

    crop_min_x = max(min_x, crop_min_x)
    crop_min_y = max(min_y, crop_min_y)
    return crop_min_x, crop_min_y, crop_max_x, crop_max_y


def render_map(
    map_data: MapData,
    bucket_size: int | None,
    max_columns: int,
    max_rows: int,
    center_x: int | None,
    center_y: int | None,
    source_width: int | None,
    source_height: int | None,
) -> tuple[list[str], str, list[ProjectedCell]]:
    crop_min_x, crop_min_y, crop_max_x, crop_max_y = _crop_bounds(
        map_data.min_cell_x,
        map_data.min_cell_y,
        map_data.max_cell_x,
        map_data.max_cell_y,
        center_x,
        center_y,
        source_width,
        source_height,
    )
    crop_width = crop_max_x - crop_min_x + 1
    crop_height = crop_max_y - crop_min_y + 1

    step = bucket_size or _auto_bucket_size(crop_width, crop_height, max_columns, max_rows)
    out_width = math.ceil(crop_width / step)
    out_height = math.ceil(crop_height / step)

    buckets: dict[tuple[int, int], Bucket] = {}
    for patch in map_data.patches:
        patch_origin_x = patch.x * map_data.visual_cells
        patch_origin_y = patch.y * map_data.visual_cells
        patch_max_x = patch_origin_x + map_data.visual_cells - 1
        patch_max_y = patch_origin_y + map_data.visual_cells - 1
        if patch_max_x < crop_min_x or patch_origin_x > crop_max_x:
            continue
        if patch_max_y < crop_min_y or patch_origin_y > crop_max_y:
            continue

        for local_y in range(map_data.visual_cells):
            world_y = patch_origin_y + local_y
            if world_y < crop_min_y or world_y > crop_max_y:
                continue
            for local_x in range(map_data.visual_cells):
                world_x = patch_origin_x + local_x
                if world_x < crop_min_x or world_x > crop_max_x:
                    continue
                bucket_x = (world_x - crop_min_x) // step
                bucket_y = (world_y - crop_min_y) // step
                key = (bucket_x, bucket_y)
                bucket = buckets.get(key)
                if bucket is None:
                    bucket = Bucket()
                    buckets[key] = bucket
                bucket.add(patch.visual[local_y][local_x])

    tile_entries: dict[tuple[int, int], tuple[str, int | None]] = {}
    for bucket_y in range(out_height):
        for bucket_x in range(out_width):
            bucket = buckets.get((bucket_x, bucket_y))
            if bucket is None or bucket.total_count == 0:
                continue
            material_id = bucket.dominant_material()
            tile_entries[(bucket_x, bucket_y)] = (_material_glyph(map_data.materials, material_id), material_id)

    tile_entries[(out_width // 2, out_height // 2)] = ("+", None)
    cells, canvas_width, canvas_height = _project_tile_cells(tile_entries)
    rows = _cells_to_rows(cells, canvas_width, canvas_height)
    hud = (
        f"src {map_data.full_width}x{map_data.full_height} cells  "
        f"crop x[{crop_min_x},{crop_max_x}] y[{crop_min_y},{crop_max_y}] ({crop_width}x{crop_height})  "
        f"bucket {step}  "
        f"1 tile = {step}x{step} src cells  "
        f"tiles {out_width}x{out_height}"
    )
    return rows, hud, cells


def _run_browser(map_data: MapData, initial_bucket_size: int | None, center_x: int | None, center_y: int | None) -> int:
    def _inner(stdscr) -> int:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        color_attrs = _init_browser_colors()
        stdscr.bkgd(" ", color_attrs["bg"])

        state_center_x = map_data.center_x if center_x is None else center_x
        state_center_y = map_data.center_y if center_y is None else center_y
        term_rows, term_cols = stdscr.getmaxyx()
        state_bucket = initial_bucket_size or _fit_bucket_for_overview(map_data, term_cols, term_rows)

        while True:
            term_rows, term_cols = stdscr.getmaxyx()
            source_width, source_height, tiles_x, tiles_y = _browser_source_size(
                term_cols,
                term_rows,
                state_bucket,
                map_data.full_width,
                map_data.full_height,
            )
            rows, base_hud, cells = render_map(
                map_data=map_data,
                bucket_size=state_bucket,
                max_columns=term_cols,
                max_rows=max(1, term_rows - HUD_ROWS),
                center_x=state_center_x,
                center_y=state_center_y,
                source_width=source_width,
                source_height=source_height,
            )
            hud = (
                f"{map_data.map_path.name} {term_cols}x{term_rows} "
                f"z{state_bucket} 1t={state_bucket}src view {tiles_x}x{tiles_y} "
                f"ctr {state_center_x},{state_center_y}"
            )
            help_line = "q quit  +/- zoom  arrows/hjkl pan  0 overview-fit  c recenter"

            stdscr.erase()
            if term_rows >= 1:
                stdscr.addnstr(0, 0, hud, max(0, term_cols - 1), color_attrs["hud"] | curses.A_BOLD)
            map_rows = max(0, term_rows - HUD_ROWS)
            for cell in cells:
                screen_y = 1 + cell.y
                if 1 <= screen_y < term_rows - 1 and 0 <= cell.x < term_cols - 1:
                    try:
                        stdscr.addch(screen_y, cell.x, cell.char, _cell_attr(cell.material_id, color_attrs))
                    except curses.error:
                        pass
            if term_rows >= 2:
                stdscr.addnstr(term_rows - 1, 0, help_line, max(0, term_cols - 1), color_attrs["help"])
            stdscr.refresh()

            key = stdscr.getch()
            if key in (ord("q"), 27):
                return 0
            if key in (curses.KEY_RESIZE, -1):
                continue
            if key in (ord("+"), ord("=")):
                state_bucket = _zoom_step(state_bucket, -1)
                continue
            if key == ord("-"):
                state_bucket = _zoom_step(state_bucket, 1)
                continue
            if key == ord("0"):
                state_bucket = _fit_bucket_for_overview(map_data, term_cols, term_rows)
                state_center_x = map_data.center_x
                state_center_y = map_data.center_y
                continue
            if key == ord("c"):
                state_center_x = map_data.center_x
                state_center_y = map_data.center_y
                continue

            pan = _pan_step(max(source_width, source_height), state_bucket)
            if key in (curses.KEY_LEFT, ord("h")):
                state_center_x -= pan
            elif key in (curses.KEY_RIGHT, ord("l")):
                state_center_x += pan
            elif key in (curses.KEY_UP, ord("k")):
                state_center_y -= pan
            elif key in (curses.KEY_DOWN, ord("j")):
                state_center_y += pan

        return 0

    return curses.wrapper(_inner)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", default=DEFAULT_MAP, help="A3D map to render.")
    parser.add_argument(
        "--bucket-size",
        type=int,
        default=None,
        help="Source visual cells per rendered tile. Default: auto-fit to max columns/rows.",
    )
    parser.add_argument("--max-columns", type=int, default=160, help="Target terminal width for auto-fit.")
    parser.add_argument("--max-rows", type=int, default=48, help="Target terminal height for auto-fit.")
    parser.add_argument("--center-x", type=int, default=None, help="Crop center X in source visual-cell coordinates.")
    parser.add_argument("--center-y", type=int, default=None, help="Crop center Y in source visual-cell coordinates.")
    parser.add_argument(
        "--source-width",
        type=int,
        default=DEFAULT_SOURCE_WIDTH,
        help=f"Crop width in source visual cells (default: {DEFAULT_SOURCE_WIDTH}).",
    )
    parser.add_argument(
        "--source-height",
        type=int,
        default=DEFAULT_SOURCE_HEIGHT,
        help=f"Crop height in source visual cells (default: {DEFAULT_SOURCE_HEIGHT}).",
    )
    parser.add_argument("--full-map", action="store_true", help="Render the whole source map instead of the default centered crop.")
    parser.add_argument("--browser", action="store_true", help="Interactive browser with resize-aware redraw and persistent zoom.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    map_data = _load_map_data(_resolve_map(args.map))
    if args.browser:
        return _run_browser(
            map_data=map_data,
            initial_bucket_size=args.bucket_size,
            center_x=args.center_x,
            center_y=args.center_y,
        )
    source_width = None if args.full_map else args.source_width
    source_height = None if args.full_map else args.source_height
    rows, hud, _cells = render_map(
        map_data=map_data,
        bucket_size=args.bucket_size,
        max_columns=args.max_columns,
        max_rows=args.max_rows,
        center_x=args.center_x,
        center_y=args.center_y,
        source_width=source_width,
        source_height=source_height,
    )
    print(hud)
    print("\n".join(row for row in rows if row.strip()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
