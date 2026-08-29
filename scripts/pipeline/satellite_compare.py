#!/usr/bin/env python3
"""Compare A3D terrain materials against satellite pixel classification.

Probes both the A3D map and satellite imagery at the same coordinates,
producing a side-by-side comparison to identify painting mismatches.

Usage:
  # Compare a single world coordinate:
  python3 scripts/pipeline/satellite_compare.py --world 1032 1032

  # Compare a rectangular region (world coords):
  python3 scripts/pipeline/satellite_compare.py --region 900 900 1100 1100

  # Compare with visual output (PNG side-by-side):
  python3 scripts/pipeline/satellite_compare.py --region 900 900 1100 1100 --output /tmp/compare.png

  # Only show mismatches:
  python3 scripts/pipeline/satellite_compare.py --region 900 900 1100 1100 --mismatches-only

  # Suggest material changes (aggregated by region):
  python3 scripts/pipeline/satellite_compare.py --region 900 900 1100 1100 --suggest

Requires cached satellite tiles (run satellite_tiles.py or satellite_terrain_painter.py first).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from osm_to_cell import load_run_params, latlon_to_world, world_to_latlon, world_to_cell
from satellite_tiles import latlon_to_tile, tile_bounds, TILE_SIZE
from satellite_classify import (
    classify_pixel, classify_image, detect_unusable_imagery,
    MAT_WATER, MAT_GRASS, MAT_DIRT, MAT_STONE, MAT_SAND,
    MAT_NAMES, MAT_COLORS, CLASSIFIERS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = "sbu_sac_scale075_topo3_clean_20260508"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fmt = _load_module("a3d_format", PROJECT_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py")

PATCH_WORLD = float(fmt.VISUAL_CELLS)  # 8.0
MATERIAL_SIZE = 512
MATERIAL_COUNT = 256


def load_a3d_patches(path: Path):
    """Load A3D and return patches indexed by (px, py)."""
    import struct
    with path.open("rb") as f:
        header = fmt.A3DHeader.from_file(f)
        patches = [fmt.A3DPatch.from_file(f) for _ in range(header.num_patches)]
    return {(p.x, p.y): p for p in patches}


def is_ground_cell(patch, vy: int, vx: int, max_ground_height: int = 640) -> bool:
    """Check if a visual cell is at ground level (not a building wall/roof).
    Post-processed ground heights are 128-576; buildings are 640+.
    NOTE: duplicated from satellite_terrain_painter.py -- keep in sync."""
    hx = vx * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
    hy = vy * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
    for dy in range(2):
        for dx in range(2):
            nx = min(fmt.HEIGHT_CELLS, hx + dx)
            ny = min(fmt.HEIGHT_CELLS, hy + dy)
            if patch.height[ny][nx] > max_ground_height:
                return False
    return True


def get_cell_info(patches_by_xy: dict, cx: int, cy: int) -> tuple[int, bool] | None:
    """Read material ID and ground status at cell (cx, cy). Returns (mat_id, is_ground) or None."""
    px = math.floor(cx / PATCH_WORLD)
    py = math.floor(cy / PATCH_WORLD)
    patch = patches_by_xy.get((px, py))
    if patch is None:
        return None
    lx = int(max(0, min(fmt.VISUAL_CELLS - 1, cx - px * int(PATCH_WORLD))))
    ly = int(max(0, min(fmt.VISUAL_CELLS - 1, cy - py * int(PATCH_WORLD))))
    mat = patch.visual[ly][lx] & 0xFF
    ground = is_ground_cell(patch, ly, lx)
    return mat, ground


def get_satellite_pixel(lat: float, lon: float, zoom: int, cache_dir: Path):
    """Read satellite pixel at lat/lon from cached tiles.

    Returns (r, g, b, hsv_h, hsv_s, hsv_v, classified_mat_id) or None if tile not cached.
    """
    tx, ty = latlon_to_tile(lat, lon, zoom)
    tile_path = cache_dir / str(zoom) / str(ty) / f"{tx}.jpg"
    if not tile_path.exists():
        return None

    img = Image.open(tile_path).convert("RGB")
    hsv_img = img.convert("HSV")

    # Pixel position within tile.
    tb = tile_bounds(zoom, tx, ty)
    t_lat_min, t_lon_min, t_lat_max, t_lon_max = tb

    frac_x = (lon - t_lon_min) / (t_lon_max - t_lon_min)
    frac_y = (t_lat_max - lat) / (t_lat_max - t_lat_min)

    px = int(max(0, min(TILE_SIZE - 1, frac_x * TILE_SIZE)))
    py = int(max(0, min(TILE_SIZE - 1, frac_y * TILE_SIZE)))

    r, g, b = img.getpixel((px, py))
    h, s, v = hsv_img.getpixel((px, py))
    mat_id = classify_pixel(h, s, v)

    return r, g, b, h, s, v, mat_id


def compare_point(cx: int, cy: int, patches_by_xy: dict, params: dict,
                  zoom: int, cache_dir: Path) -> dict | None:
    """Compare A3D material vs satellite classification at a single cell."""
    info = get_cell_info(patches_by_xy, cx, cy)
    if info is None:
        return None
    a3d_mat, ground = info

    lat, lon = world_to_latlon(float(cx) + 0.5, float(cy) + 0.5, params)
    sat = get_satellite_pixel(lat, lon, zoom, cache_dir)
    if sat is None:
        return None

    r, g, b, h, s, v, sat_mat = sat
    return {
        "cx": cx, "cy": cy,
        "lat": lat, "lon": lon,
        "a3d_mat": a3d_mat,
        "a3d_name": MAT_NAMES.get(a3d_mat, f"id={a3d_mat}"),
        "sat_mat": sat_mat,
        "sat_name": MAT_NAMES.get(sat_mat, f"id={sat_mat}"),
        "rgb": (r, g, b),
        "hsv": (h, s, v),
        "match": a3d_mat == sat_mat,
        "ground": ground,
    }


def compare_region(x_min: int, y_min: int, x_max: int, y_max: int,
                   patches_by_xy: dict, params: dict,
                   zoom: int, cache_dir: Path) -> list[dict]:
    """Compare all cells in a rectangular region."""
    results = []
    total = (x_max - x_min) * (y_max - y_min)
    checked = 0
    for cy in range(y_min, y_max):
        for cx in range(x_min, x_max):
            r = compare_point(cx, cy, patches_by_xy, params, zoom, cache_dir)
            if r is not None:
                results.append(r)
            checked += 1
            if checked % 5000 == 0:
                print(f"  Compared {checked}/{total} cells...")
    return results


def render_comparison(results: list[dict], x_min: int, y_min: int,
                      x_max: int, y_max: int, output_path: Path,
                      patches_by_xy: dict, params: dict,
                      zoom: int, cache_dir: Path):
    """Render a side-by-side PNG: A3D materials | satellite classification | satellite RGB."""
    w = x_max - x_min
    h = y_max - y_min
    scale = max(1, 512 // max(w, h))  # scale up if small
    sw, sh = w * scale, h * scale

    # Build indexed lookups.
    a3d_grid = {}
    sat_grid = {}
    rgb_grid = {}
    for r in results:
        key = (r["cx"] - x_min, r["cy"] - y_min)
        a3d_grid[key] = r["a3d_mat"]
        sat_grid[key] = r["sat_mat"]
        rgb_grid[key] = r["rgb"]

    # Panel 1: A3D materials.
    a3d_img = np.zeros((sh, sw, 3), dtype=np.uint8)
    for (gx, gy), mat_id in a3d_grid.items():
        color = MAT_COLORS.get(mat_id, (128, 128, 128))
        a3d_img[gy*scale:(gy+1)*scale, gx*scale:(gx+1)*scale] = color

    # Panel 2: Satellite classification.
    sat_img = np.zeros((sh, sw, 3), dtype=np.uint8)
    for (gx, gy), mat_id in sat_grid.items():
        color = MAT_COLORS.get(mat_id, (128, 128, 128))
        sat_img[gy*scale:(gy+1)*scale, gx*scale:(gx+1)*scale] = color

    # Panel 3: Satellite RGB.
    rgb_img = np.zeros((sh, sw, 3), dtype=np.uint8)
    for (gx, gy), rgb in rgb_grid.items():
        rgb_img[gy*scale:(gy+1)*scale, gx*scale:(gx+1)*scale] = rgb

    # Combine side by side with labels.
    label_h = 24
    gap = 4
    total_w = sw * 3 + gap * 2
    total_h = sh + label_h
    canvas = np.zeros((total_h, total_w, 3), dtype=np.uint8)
    canvas[label_h:label_h+sh, 0:sw] = a3d_img
    canvas[label_h:label_h+sh, sw+gap:sw*2+gap] = sat_img
    canvas[label_h:label_h+sh, sw*2+gap*2:sw*3+gap*2] = rgb_img

    out = Image.fromarray(canvas)
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except Exception:
        font = ImageFont.load_default()
    draw.text((4, 2), "A3D Materials", fill=(255, 255, 255), font=font)
    draw.text((sw + gap + 4, 2), "Satellite Class.", fill=(255, 255, 255), font=font)
    draw.text((sw*2 + gap*2 + 4, 2), "Satellite RGB", fill=(255, 255, 255), font=font)

    out.save(output_path)
    print(f"Comparison image: {output_path}")


def suggest_changes(results: list[dict]) -> list[dict]:
    """Analyze mismatches and suggest material changes."""
    # Group mismatches by (a3d_mat, sat_mat) transition.
    transitions: dict[tuple[int, int], list[dict]] = {}
    for r in results:
        if r["match"]:
            continue
        key = (r["a3d_mat"], r["sat_mat"])
        if key not in transitions:
            transitions[key] = []
        transitions[key].append(r)

    suggestions = []
    for (a3d_mat, sat_mat), cells in sorted(transitions.items(), key=lambda x: -len(x[1])):
        # Find bounding box of affected cells.
        xs = [c["cx"] for c in cells]
        ys = [c["cy"] for c in cells]

        # Compute avg satellite RGB for this group.
        avg_r = sum(c["rgb"][0] for c in cells) / len(cells)
        avg_g = sum(c["rgb"][1] for c in cells) / len(cells)
        avg_b = sum(c["rgb"][2] for c in cells) / len(cells)

        suggestions.append({
            "from_mat": MAT_NAMES.get(a3d_mat, f"id={a3d_mat}"),
            "from_id": a3d_mat,
            "to_mat": MAT_NAMES.get(sat_mat, f"id={sat_mat}"),
            "to_id": sat_mat,
            "cell_count": len(cells),
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
            "avg_rgb": (int(avg_r), int(avg_g), int(avg_b)),
            "sample_latlon": (cells[0]["lat"], cells[0]["lon"]),
        })

    return suggestions


def main():
    parser = argparse.ArgumentParser(
        description="Compare A3D terrain materials against satellite pixel classification"
    )
    parser.add_argument("--world", nargs=2, type=float, metavar=("WX", "WY"),
                        help="Single world coordinate to probe")
    parser.add_argument("--region", nargs=4, type=int,
                        metavar=("X_MIN", "Y_MIN", "X_MAX", "Y_MAX"),
                        help="Rectangular region in world coords")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output comparison PNG")
    parser.add_argument("--mismatches-only", action="store_true",
                        help="Only show mismatched cells")
    parser.add_argument("--suggest", action="store_true",
                        help="Suggest material changes based on mismatches")
    parser.add_argument("--zoom", type=int, default=18)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--map", type=Path, default=None, help="A3D map file")
    args = parser.parse_args()

    run_dir = args.run_dir or (PROJECT_ROOT / "assets" / "meshes" / "osm_runs" / DEFAULT_RUN)
    map_path = args.map or (run_dir / "output.a3d")
    cache_dir = run_dir / "satellite_cache"

    if not map_path.exists():
        print(f"ERROR: {map_path} not found", file=sys.stderr)
        return 1
    if not cache_dir.exists():
        print(f"ERROR: {cache_dir} not found. Run satellite_tiles.py first.", file=sys.stderr)
        return 1

    params = load_run_params(run_dir)
    patches_by_xy = load_a3d_patches(map_path)
    print(f"Loaded {len(patches_by_xy)} patches from {map_path}")

    if args.world:
        wx, wy = args.world
        cx, cy = int(math.floor(wx)), int(math.floor(wy))
        r = compare_point(cx, cy, patches_by_xy, params, args.zoom, cache_dir)
        if r is None:
            print(f"No data at ({cx}, {cy})")
            return 1
        match_str = "MATCH" if r["match"] else "MISMATCH"
        ground_str = "GROUND" if r["ground"] else "BUILDING"
        print(f"Cell ({cx}, {cy})  lat={r['lat']:.7f} lon={r['lon']:.7f}  [{ground_str}]")
        print(f"  A3D:       {r['a3d_name']} (id={r['a3d_mat']})")
        print(f"  Satellite: {r['sat_name']} (id={r['sat_mat']})  "
              f"RGB=({r['rgb'][0]},{r['rgb'][1]},{r['rgb'][2]})  "
              f"HSV=({r['hsv'][0]},{r['hsv'][1]},{r['hsv'][2]})")
        print(f"  -> {match_str}")
        return 0

    if args.region:
        x_min, y_min, x_max, y_max = args.region
        total = (x_max - x_min) * (y_max - y_min)
        print(f"Comparing region ({x_min},{y_min}) to ({x_max},{y_max}): {total} cells")

        results = compare_region(x_min, y_min, x_max, y_max,
                                 patches_by_xy, params, args.zoom, cache_dir)

        ground_cells = [r for r in results if r["ground"]]
        building_cells = [r for r in results if not r["ground"]]
        ground_matches = sum(1 for r in ground_cells if r["match"])
        ground_mismatches = sum(1 for r in ground_cells if not r["match"])
        bldg_mismatches = sum(1 for r in building_cells if not r["match"])
        matches = sum(1 for r in results if r["match"])
        mismatches = sum(1 for r in results if not r["match"])
        print(f"\nResults: {len(results)} cells compared "
              f"({len(ground_cells)} ground, {len(building_cells)} building)")
        print(f"  Matches:    {matches} ({matches/max(1,len(results))*100:.1f}%)")
        print(f"  Mismatches: {mismatches} ({mismatches/max(1,len(results))*100:.1f}%)")
        print(f"    Ground mismatches:   {ground_mismatches}")
        print(f"    Building mismatches: {bldg_mismatches} (expected, not actionable)")

        # Breakdown by transition type.
        if mismatches > 0:
            trans = Counter()
            for r in results:
                if not r["match"]:
                    trans[(r["a3d_name"], r["sat_name"])] += 1
            print(f"\nMismatch breakdown:")
            for (a3d, sat), count in trans.most_common(20):
                print(f"  {a3d:8s} -> {sat:8s}: {count:6d} cells")

        if args.output:
            render_comparison(results, x_min, y_min, x_max, y_max,
                              args.output, patches_by_xy, params, args.zoom, cache_dir)

        if args.suggest:
            suggestions = suggest_changes(results)
            if suggestions:
                print(f"\n--- Suggested Changes ({len(suggestions)} groups) ---")
                for s in suggestions:
                    print(f"\n  {s['from_mat']} -> {s['to_mat']}: {s['cell_count']} cells")
                    print(f"    Region: ({s['bbox'][0]},{s['bbox'][1]}) to ({s['bbox'][2]},{s['bbox'][3]})")
                    print(f"    Avg satellite RGB: {s['avg_rgb']}")
                    print(f"    Sample: lat={s['sample_latlon'][0]:.6f} lon={s['sample_latlon'][1]:.6f}")
            else:
                print("\nNo changes suggested (all materials match).")

        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
