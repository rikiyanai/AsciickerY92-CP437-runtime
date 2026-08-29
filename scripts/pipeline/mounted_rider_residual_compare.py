#!/usr/bin/env python3
"""Compare mounted residual cells against a shifted rider reference."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import mounted_rider_offset as mro


RESET = "\033[0m"
TRANSPARENT_BG = semantic_transparent = mro.semantic_dict.TRANSPARENT_BG


def _cp437_char(glyph: int) -> str:
    if glyph in (0, 32):
        return " "
    try:
        return bytes([glyph & 0xFF]).decode("cp437")
    except Exception:
        return "?"


def _transparent_cell() -> tuple[int, tuple[int, int, int], tuple[int, int, int]]:
    return (0, (0, 0, 0), TRANSPARENT_BG)


def _frame_grid(
    xp: mro.XPFile,
    layout: mro.FrameLayout,
    *,
    angle: int,
    anim_index: int,
    frame_index: int,
    proj: int,
    layer_index: int,
) -> list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]]:
    grid = [[_transparent_cell() for _ in range(layout.frame_width)] for _ in range(layout.frame_height)]
    for cell in mro.frame_cells(
        xp,
        layout,
        angle=angle,
        anim_index=anim_index,
        frame_index=frame_index,
        proj=proj,
        layer_index=layer_index,
    ):
        grid[cell.y][cell.x] = (cell.glyph, cell.fg, cell.bg)
    return grid


def _place_grid(
    source: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
    *,
    out_width: int,
    out_height: int,
    dx: int,
    dy: int,
) -> list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]]:
    out = [[_transparent_cell() for _ in range(out_width)] for _ in range(out_height)]
    for y, row in enumerate(source):
        for x, cell in enumerate(row):
            tx = x + dx
            ty = y + dy
            if 0 <= tx < out_width and 0 <= ty < out_height and cell != _transparent_cell():
                out[ty][tx] = cell
    return out


def _subtract_grid(
    mounted: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
    base: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
) -> list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]]:
    height = len(mounted)
    width = len(mounted[0]) if mounted else 0
    out = [[_transparent_cell() for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            mounted_cell = mounted[y][x]
            base_cell = base[y][x]
            if mounted_cell == _transparent_cell():
                continue
            if mounted_cell == base_cell:
                continue
            out[y][x] = mounted_cell
    return out


def _classify_surface_cells(
    mounted: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
    base: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
    shifted_rider: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
) -> dict[str, list[dict[str, object]]]:
    buckets: dict[str, list[dict[str, object]]] = {
        "mount_rear_surface": [],
        "mount_front_surface": [],
        "rider_visible_match": [],
        "unresolved_shared_exact": [],
        "unresolved_mount_delta": [],
    }
    height = len(mounted)
    width = len(mounted[0]) if mounted else 0
    transparent = _transparent_cell()
    for y in range(height):
        for x in range(width):
            mounted_cell = mounted[y][x]
            if mounted_cell == transparent:
                continue
            base_cell = base[y][x]
            rider_cell = shifted_rider[y][x]
            base_visible = base_cell != transparent
            rider_visible = rider_cell != transparent
            bucket = "unresolved_mount_delta"
            if rider_visible and mounted_cell == rider_cell and not base_visible:
                bucket = "rider_visible_match"
            elif rider_visible and base_visible and mounted_cell == rider_cell and mounted_cell != base_cell:
                bucket = "rider_visible_match"
            elif rider_visible and base_visible and mounted_cell == rider_cell and mounted_cell == base_cell:
                bucket = "unresolved_shared_exact"
            elif rider_visible and mounted_cell != rider_cell:
                bucket = "mount_front_surface"
            elif not rider_visible and base_visible and mounted_cell == base_cell:
                bucket = "mount_rear_surface"
            glyph, fg, bg = mounted_cell
            buckets[bucket].append(
                {
                    "row": y,
                    "col": x,
                    "glyph_id": int(glyph),
                    "glyph_char": _cp437_char(int(glyph)),
                    "fg_rgb": list(fg),
                    "bg_rgb": list(bg),
                }
            )
    return buckets


def _grid_to_visible_cells(
    grid: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
) -> list[mro.VisibleCell]:
    cells: list[mro.VisibleCell] = []
    for y, row in enumerate(grid):
        for x, (glyph, fg, bg) in enumerate(row):
            if (glyph, fg, bg) != _transparent_cell():
                cells.append(mro.VisibleCell(x=x, y=y, glyph=glyph, fg=fg, bg=bg))
    return cells


def _render_side_by_side(
    left: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
    right: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
    *,
    left_label: str,
    right_label: str,
) -> str:
    left_h = len(left)
    right_h = len(right)
    height = max(left_h, right_h)
    left_w = len(left[0]) if left else 0
    right_w = len(right[0]) if right else 0
    gap = "    "

    def fg(rgb: tuple[int, int, int]) -> str:
        return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"

    def bg(rgb: tuple[int, int, int]) -> str:
        return f"\033[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"

    def fmt(cell: tuple[int, tuple[int, int, int], tuple[int, int, int]]) -> str:
        glyph, fg_rgb, bg_rgb = cell
        bg_seq = "\033[49m" if bg_rgb == TRANSPARENT_BG else bg(bg_rgb)
        ch = _cp437_char(glyph)
        if ch == " ":
            return bg_seq + " " + RESET
        return bg_seq + fg(fg_rgb) + ch + RESET

    lines = [left_label.ljust(left_w) + gap + right_label]
    for y in range(height):
        left_row = left[y] if y < left_h else [_transparent_cell() for _ in range(left_w)]
        right_row = right[y] if y < right_h else [_transparent_cell() for _ in range(right_w)]
        lines.append("".join(fmt(cell) for cell in left_row) + gap + "".join(fmt(cell) for cell in right_row))
    return "\n".join(lines)


def build_compare_report(
    *,
    player_path: Path,
    mount_base_path: Path,
    mounted_path: Path,
    angle: int,
    anim_index: int,
    frame_index: int,
    proj: int,
    rider_layer: str | int,
    mount_layer: int,
    min_dx: int,
    max_dx: int,
    min_dy: int,
    max_dy: int,
) -> dict[str, object]:
    player_xp = mro._load_xp(player_path)
    mount_base_xp = mro._load_xp(mount_base_path)
    mounted_xp = mro._load_xp(mounted_path)
    player_layout = mro._parse_layout(player_xp)
    mount_base_layout = mro._parse_layout(mount_base_xp)
    mounted_layout = mro._parse_layout(mounted_xp)

    rider_report = mro.build_report(
        player_path,
        mounted_path,
        anim_index=anim_index,
        frame_index=frame_index,
        proj=proj,
        layer=rider_layer,
        min_dx=min_dx,
        max_dx=max_dx,
        min_dy=min_dy,
        max_dy=max_dy,
    )
    rider_layer_index = int(rider_report["layer_used"])
    rider_offset = rider_report["per_angle"][angle]

    mount_base_cells = mro.frame_cells(
        mount_base_xp,
        mount_base_layout,
        angle=angle,
        anim_index=anim_index,
        frame_index=frame_index,
        proj=proj,
        layer_index=mount_layer,
    )
    mounted_visual_cells = mro.frame_cells(
        mounted_xp,
        mounted_layout,
        angle=angle,
        anim_index=anim_index,
        frame_index=frame_index,
        proj=proj,
        layer_index=mount_layer,
    )
    mount_offset = mro.best_offset(
        mount_base_cells,
        mounted_visual_cells,
        min_dx=min_dx,
        max_dx=max_dx,
        min_dy=min_dy,
        max_dy=max_dy,
    )

    mount_base_grid = _frame_grid(
        mount_base_xp,
        mount_base_layout,
        angle=angle,
        anim_index=anim_index,
        frame_index=frame_index,
        proj=proj,
        layer_index=mount_layer,
    )
    mounted_visual_grid = _frame_grid(
        mounted_xp,
        mounted_layout,
        angle=angle,
        anim_index=anim_index,
        frame_index=frame_index,
        proj=proj,
        layer_index=mount_layer,
    )
    player_reference_grid = _frame_grid(
        player_xp,
        player_layout,
        angle=angle,
        anim_index=anim_index,
        frame_index=frame_index,
        proj=proj,
        layer_index=rider_layer_index,
    )

    aligned_mount_base = _place_grid(
        mount_base_grid,
        out_width=mounted_layout.frame_width,
        out_height=mounted_layout.frame_height,
        dx=int(mount_offset["dx"]),
        dy=int(mount_offset["dy"]),
    )
    shifted_player = _place_grid(
        player_reference_grid,
        out_width=mounted_layout.frame_width,
        out_height=mounted_layout.frame_height,
        dx=int(rider_offset["dx"]),
        dy=int(rider_offset["dy"]),
    )
    residual = _subtract_grid(mounted_visual_grid, aligned_mount_base)
    classified_cells = _classify_surface_cells(
        mounted_visual_grid,
        aligned_mount_base,
        shifted_player,
    )

    residual_cells = _grid_to_visible_cells(residual)
    shifted_player_cells = _grid_to_visible_cells(shifted_player)
    residual_vs_player = mro.score_offset(shifted_player_cells, residual_cells, dx=0, dy=0)
    residual_by_xy = {(cell.x, cell.y): (cell.glyph, cell.fg, cell.bg) for cell in residual_cells}
    shifted_by_xy = {(cell.x, cell.y): (cell.glyph, cell.fg, cell.bg) for cell in shifted_player_cells}
    unexplained_residual = sum(
        1 for xy, cell in residual_by_xy.items() if shifted_by_xy.get(xy) != cell
    )

    return {
        "player": str(player_path),
        "mount_base": str(mount_base_path),
        "mounted": str(mounted_path),
        "angle": angle,
        "anim_index": anim_index,
        "frame_index": frame_index,
        "proj": proj,
        "rider_layer_used": rider_layer_index,
        "mount_layer_used": mount_layer,
        "rider_offset": rider_offset,
        "mount_base_offset": mount_offset,
        "classified_cells": classified_cells,
        "classification_counts": {
            bucket: len(cells)
            for bucket, cells in classified_cells.items()
        },
        "residual_compare": {
            "matches": int(residual_vs_player["matches"]),
            "overlaps": int(residual_vs_player["overlaps"]),
            "mismatches": int(residual_vs_player["mismatches"]),
            "shifted_player_cells": int(residual_vs_player["reference_cells"]),
            "residual_cells": int(residual_vs_player["target_cells"]),
            "coverage": float(residual_vs_player["coverage"]),
            "unexplained_residual_cells": unexplained_residual,
            "classified_unresolved_cells": len(classified_cells["unresolved_shared_exact"])
            + len(classified_cells["unresolved_mount_delta"]),
        },
        "base_vs_mounted_ansi": _render_side_by_side(
            aligned_mount_base,
            mounted_visual_grid,
            left_label=f"{mount_base_path.name} aligned",
            right_label=f"{mounted_path.name} layer{mount_layer}",
        ),
        "residual_vs_player_ansi": _render_side_by_side(
            residual,
            shifted_player,
            left_label=f"{mounted_path.name} residual",
            right_label=f"{player_path.name} shifted",
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Subtract aligned mount base cells from a mounted frame and compare the residual to a shifted rider reference.",
    )
    parser.add_argument("--player", default="player-0100.xp")
    parser.add_argument("--mount-base", default="wolfie.xp")
    parser.add_argument("--mounted", default="wolfie-0100.xp")
    parser.add_argument("--angle", type=int, default=0)
    parser.add_argument("--anim-index", type=int, default=0)
    parser.add_argument("--frame-index", type=int, default=0)
    parser.add_argument("--proj", type=int, default=0)
    parser.add_argument("--rider-layer", default="auto")
    parser.add_argument("--mount-layer", type=int, default=2)
    parser.add_argument("--min-dx", type=int, default=-4)
    parser.add_argument("--max-dx", type=int, default=8)
    parser.add_argument("--min-dy", type=int, default=-4)
    parser.add_argument("--max-dy", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_compare_report(
        player_path=mro._resolve_path(args.player),
        mount_base_path=mro._resolve_path(args.mount_base),
        mounted_path=mro._resolve_path(args.mounted),
        angle=args.angle,
        anim_index=args.anim_index,
        frame_index=args.frame_index,
        proj=args.proj,
        rider_layer=args.rider_layer,
        mount_layer=args.mount_layer,
        min_dx=args.min_dx,
        max_dx=args.max_dx,
        min_dy=args.min_dy,
        max_dy=args.max_dy,
    )

    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 0

    print(
        f"angle={report['angle']} anim_index={report['anim_index']} frame_index={report['frame_index']} "
        f"proj={report['proj']} rider_layer={report['rider_layer_used']} mount_layer={report['mount_layer_used']}"
    )
    print(
        f"rider_offset=({report['rider_offset']['dx']},{report['rider_offset']['dy']}) "
        f"base_mount_offset=({report['mount_base_offset']['dx']},{report['mount_base_offset']['dy']})"
    )
    print()
    print("PASS 1: aligned base mount vs mounted composite")
    print(report["base_vs_mounted_ansi"])
    print()
    print("PASS 2: residual vs shifted rider reference")
    print(report["residual_vs_player_ansi"])
    print()
    metrics = report["residual_compare"]
    print(
        "RESIDUAL METRICS "
        f"matches={metrics['matches']}/{metrics['shifted_player_cells']} "
        f"overlaps={metrics['overlaps']} mismatches={metrics['mismatches']} "
        f"residual_cells={metrics['residual_cells']} unexplained_residual_cells={metrics['unexplained_residual_cells']} "
        f"classified_unresolved_cells={metrics['classified_unresolved_cells']}"
    )
    print(f"classification_counts={report['classification_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
