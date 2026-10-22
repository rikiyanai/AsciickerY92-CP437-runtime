#!/usr/bin/env python3
"""Paint A3D terrain cells from satellite imagery classification.

Fetches ESRI satellite tiles for the map's bounding box, classifies each
pixel into terrain material categories (water/grass/dirt/stone/sand), and
paints A3D cells that are still at the default grass material.  Building
walls/roofs are preserved via the ground-cell height check.

Usage:
  # Full pipeline run:
  python3 scripts/pipeline/satellite_terrain_painter.py \\
      --map output.a3d --metadata terrain_metadata.json --zoom 18

  # Dry run (classify and report, do not write):
  python3 scripts/pipeline/satellite_terrain_painter.py \\
      --map output.a3d --metadata terrain_metadata.json --dry-run

  # Override tile budget for large maps:
  python3 scripts/pipeline/satellite_terrain_painter.py \\
      --map output.a3d --metadata terrain_metadata.json --max-tiles 200

  # Force through snow/cloud gate:
  python3 scripts/pipeline/satellite_terrain_painter.py \\
      --map output.a3d --metadata terrain_metadata.json --force

Runs AFTER the OSM postprocessor.  OSM features already painted take priority;
the satellite painter only modifies cells still at MAT_GRASS (1).

Exit codes: 0 = OK, 1 = error, 2 = aborted (snow/cloud or tile failure)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import struct
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from osm_to_cell import load_run_params, latlon_to_world, world_to_cell
from satellite_tiles import (
    fetch_area, latlon_to_tile, ping_tile_source, tile_bounds, TILE_SIZE,
)
from satellite_classify import (
    classify_image, detect_unusable_imagery,
    MAT_GRASS, MAT_STONE, MAT_SAND, MAT_NAMES,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
A3D_EDIT_ROOT = PROJECT_ROOT / "docs" / "agent" / "cli-anything"

# ---------------------------------------------------------------------------
# Dynamic module loading (same pattern as sbu_satellite_style_postprocess.py)
# ---------------------------------------------------------------------------


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fmt = _load_module("a3d_format", PROJECT_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py")
a3d_edit = _load_module("a3d_edit", A3D_EDIT_ROOT / "a3d_edit.py")

PATCH_SIZE = 188
MATERIAL_SIZE = 512
MATERIAL_COUNT = 256
PATCH_WORLD = float(fmt.VISUAL_CELLS)  # 8.0
SATELLITE_STONE_PAINT_MAT = 2
SATELLITE_STONE_PAINT_SHADE = 0
SATELLITE_PAVEMENT_SOURCE_MATS = {MAT_STONE, MAT_SAND}
SATELLITE_ROAD_PAINT_MAT = 4
# OSM road ways are centerlines with widths, while satellite classification
# sees the real asphalt/concrete footprint.  Extend the road owner into nearby
# satellite pavement so roads are filled surfaces instead of thin outlines.
SATELLITE_ROAD_INFLUENCE_PAD = 12.0
PAINT_MAT_NAMES = {
    0: "water",
    1: "grass",
    2: "pavement",
    3: "stone",
    4: "road",
    5: "building",
}

# ---------------------------------------------------------------------------
# Metadata validation
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ["scene_lat", "scene_lon", "content_scale", "terrain_shift", "content_bounds"]


def validate_metadata(meta: dict) -> None:
    """Assert all required projection fields exist with sensible ranges.

    Raises ValueError with clear message on first failure.
    """
    for field in REQUIRED_FIELDS:
        if field not in meta:
            raise ValueError(f"terrain_metadata.json missing required field: {field}")
    if not (-85.0 < meta["scene_lat"] < 85.0):
        raise ValueError(f"scene_lat={meta['scene_lat']} outside valid range (-85, 85)")
    if not (-180.0 < meta["scene_lon"] < 180.0):
        raise ValueError(f"scene_lon={meta['scene_lon']} outside valid range (-180, 180)")
    if meta["content_scale"] <= 0:
        raise ValueError(f"content_scale={meta['content_scale']} must be positive")
    ts = meta["terrain_shift"]
    if "x" not in ts or "y" not in ts:
        raise ValueError("terrain_shift must have x and y fields")
    cb = meta["content_bounds"]
    for k in ("min_x", "min_y", "max_x", "max_y"):
        if k not in cb:
            raise ValueError(f"content_bounds missing field: {k}")


def _dist_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    sx = ax + t * dx
    sy = ay + t * dy
    return math.hypot(px - sx, py - sy)


def _load_road_influence_features(map_path: Path) -> list[dict]:
    features_path = Path(f"{map_path}.features.json")
    if not features_path.exists():
        return []
    with features_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    roads = []
    for feature in data.get("features", []):
        if feature.get("kind") != "road":
            continue
        vertices = feature.get("world_vertices") or []
        if len(vertices) < 2:
            continue
        points = [(float(x), float(y)) for x, y in vertices]
        width = max(0.0, float(feature.get("width") or 0.0))
        roads.append({
            "id": feature.get("id"),
            "points": points,
            "radius": width * 0.5 + SATELLITE_ROAD_INFLUENCE_PAD,
        })
    return roads


def _cell_near_road(cx: int, cy: int, road_features: list[dict]) -> bool:
    wx = float(cx) + 0.5
    wy = float(cy) + 0.5
    for road in road_features:
        points = road["points"]
        radius = road["radius"]
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            if _dist_to_segment(wx, wy, ax, ay, bx, by) <= radius:
                return True
    return False

# ---------------------------------------------------------------------------
# Ground-cell height check
# NOTE: duplicated from sbu_satellite_style_postprocess.py:265 -- keep in sync
# ---------------------------------------------------------------------------


def is_ground_cell(patch, vy: int, vx: int, max_ground_height: int = 640) -> bool:
    """Check if a visual cell is at ground level (not a building wall/roof).

    The postprocessor's _vary_ground_heights() raises ground from baseline 128
    to ~288 (range 128-576) and protects building vertices > 640.  So after
    post-processing, ground cells have heights <= 576 and buildings >= 640.
    NOTE: duplicated from sbu_satellite_style_postprocess.py -- keep in sync.
    """
    hx = vx * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
    hy = vy * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
    for dy in range(2):
        for dx in range(2):
            nx = min(fmt.HEIGHT_CELLS, hx + dx)
            ny = min(fmt.HEIGHT_CELLS, hy + dy)
            if patch.height[ny][nx] > max_ground_height:
                return False
    return True

# ---------------------------------------------------------------------------
# A3D I/O (same pattern as sbu_satellite_style_postprocess.py)
# ---------------------------------------------------------------------------


def _load_a3d(path: Path):
    with path.open("rb") as f:
        header = fmt.A3DHeader.from_file(f)
        patches = [fmt.A3DPatch.from_file(f) for _ in range(header.num_patches)]
        materials = f.read(MATERIAL_COUNT * MATERIAL_SIZE)
        if len(materials) != MATERIAL_COUNT * MATERIAL_SIZE:
            raise RuntimeError(f"truncated material section in {path}")
        raw_fmt_version = struct.unpack("<i", f.read(4))[0]
        inst_fmt = -raw_fmt_version if raw_fmt_version < 0 else raw_fmt_version
        inst_count = struct.unpack("<i", f.read(4))[0]
        instances = [fmt.A3DInstance.from_file(f, format_version=inst_fmt) for _ in range(inst_count)]
        player_start = None
        if raw_fmt_version <= -4:
            has_ps_raw = f.read(4)
            if len(has_ps_raw) == 4 and struct.unpack("<i", has_ps_raw)[0]:
                player_start = fmt.A3DPlayerStart.from_file(f)
        enemy_gens = []
        enemy_raw = f.read(4)
        if len(enemy_raw) == 4:
            enemy_count = struct.unpack("<i", enemy_raw)[0]
            enemy_gens = [fmt.A3DEnemyGen.from_file(f) for _ in range(enemy_count)]
        markers = []
        marker_raw = f.read(4)
        if len(marker_raw) == 4:
            marker_count = struct.unpack("<i", marker_raw)[0]
            markers = [fmt.A3DMinimapMarker.from_file(f) for _ in range(marker_count)]
    return header, patches, materials, raw_fmt_version, instances, player_start, enemy_gens, markers


def _build_pre_bytes(header, patches, materials: bytes) -> bytes:
    from io import BytesIO
    out = BytesIO()
    header.num_patches = len(patches)
    header.write(out)
    for patch in patches:
        patch.write(out)
    out.write(materials)
    return out.getvalue()


def _write_a3d(path: Path, header, patches, materials,
               raw_fmt_version, instances, player_start, enemy_gens, markers):
    pre = _build_pre_bytes(header, patches, materials)
    a3d_edit.write_a3d_sections(path, pre, raw_fmt_version, instances,
                                player_start, enemy_gens, markers)

# ---------------------------------------------------------------------------
# Safe A3D write with backup + atomic rename
# ---------------------------------------------------------------------------


def safe_write_a3d(original_path: Path, header, patches, materials,
                   raw_fmt_version, instances, player_start, enemy_gens, markers):
    """Write A3D with named backup.  a3d_edit handles atomic write internally."""
    backup_path = original_path.parent / (original_path.name + ".pre-satellite")

    # Named backup (skip if already exists from prior run).
    if not backup_path.exists():
        shutil.copy2(original_path, backup_path)
        print(f"Backup: {backup_path}")

    # a3d_edit.write_a3d_sections does its own atomic write (tmp + rename).
    _write_a3d(original_path, header, patches, materials,
               raw_fmt_version, instances, player_start, enemy_gens, markers)

# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def _get_bounds_latlon(params: dict) -> tuple[float, float, float, float]:
    """Convert content_bounds world coords to lat/lon bounding box."""
    meta_path = Path(params["metadata_path"])
    meta = json.loads(meta_path.read_text())
    cb = meta["content_bounds"]

    from osm_projection import osm_project_inverse

    scene_lat = params["scene_lat"]
    scene_lon = params["scene_lon"]
    content_scale = params["content_scale"]
    shift_x = params["shift_x"]
    shift_y = params["shift_y"]

    x_m_min = (cb["min_x"] - shift_x) / content_scale
    y_m_min = (cb["min_y"] - shift_y) / content_scale
    x_m_max = (cb["max_x"] - shift_x) / content_scale
    y_m_max = (cb["max_y"] - shift_y) / content_scale

    lat_min, lon_min = osm_project_inverse(x_m_min, y_m_min, scene_lat, scene_lon)
    lat_max, lon_max = osm_project_inverse(x_m_max, y_m_max, scene_lat, scene_lon)

    if lat_min > lat_max:
        lat_min, lat_max = lat_max, lat_min
    if lon_min > lon_max:
        lon_min, lon_max = lon_max, lon_min

    return lat_min, lon_min, lat_max, lon_max


def run_pipeline(map_path: Path, metadata_path: Path, zoom: int,
                 max_tiles: int, dry_run: bool, force: bool) -> int:
    """Main pipeline entry point.  Returns exit code."""

    # 1. Load and validate metadata.
    meta = json.loads(metadata_path.read_text())
    validate_metadata(meta)
    print(f"Metadata OK: scene=({meta['scene_lat']:.6f}, {meta['scene_lon']:.6f}) "
          f"scale={meta['content_scale']}")

    # Load projection params via osm_to_cell (includes calibration offsets).
    run_dir = metadata_path.parent
    params = load_run_params(run_dir)

    # Content bounds for cell-in-bounds check.
    cb = meta["content_bounds"]
    bounds_min_x = cb["min_x"]
    bounds_min_y = cb["min_y"]
    bounds_max_x = cb["max_x"]
    bounds_max_y = cb["max_y"]

    # 2. Convert bounds to lat/lon.
    lat_min, lon_min, lat_max, lon_max = _get_bounds_latlon(params)
    print(f"Bounds: lat=[{lat_min:.6f}, {lat_max:.6f}] lon=[{lon_min:.6f}, {lon_max:.6f}]")

    # 3. Ping tile source.
    print("Verifying tile source...")
    if not ping_tile_source():
        print("ERROR: ESRI tile source is not responding", file=sys.stderr)
        return 1
    print("Tile source OK")

    # 4. Fetch tiles.
    cache_dir = run_dir / "satellite_cache"
    try:
        successes, failures = fetch_area(
            lat_min, lon_min, lat_max, lon_max,
            zoom, cache_dir, max_tiles
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    total_tiles = len(successes) + len(failures)
    if total_tiles == 0:
        print("ERROR: No tiles in bounding box", file=sys.stderr)
        return 1

    fail_ratio = len(failures) / total_tiles
    if fail_ratio >= 0.8:
        print(f"ERROR: {len(failures)}/{total_tiles} tiles failed (>= 80%), aborting",
              file=sys.stderr)
        return 2

    # 5. Snow/cloud pre-scan.
    from PIL import Image

    flagged_tiles = 0
    for z, x, y, path in successes:
        img = Image.open(path).convert("RGB")
        unusable, reason = detect_unusable_imagery(img)
        if unusable:
            flagged_tiles += 1
            print(f"  Tile z={z} x={x} y={y}: {reason}")

    if total_tiles > 0 and flagged_tiles / len(successes) > 0.5:
        if force:
            print(f"WARNING: {flagged_tiles}/{len(successes)} tiles flagged, "
                  f"continuing due to --force")
        else:
            print(f"ABORTED: {flagged_tiles}/{len(successes)} tiles flagged as "
                  f"snow/cloud/shadow. Use --force to override.", file=sys.stderr)
            return 2

    # 6. Classify pixels and accumulate per-cell votes.
    print("\nClassifying pixels and mapping to cells...")
    cell_votes: dict[tuple[int, int], Counter] = {}
    pixel_stats: Counter = Counter()

    for tile_idx, (z, tx, ty, path) in enumerate(successes):
        img = Image.open(path).convert("RGB")
        materials_2d = classify_image(img)
        tb = tile_bounds(z, tx, ty)  # lat_min, lon_min, lat_max, lon_max
        t_lat_min, t_lon_min, t_lat_max, t_lon_max = tb

        h, w = materials_2d.shape

        # Compute cell footprint of one pixel (pixels are ~2x2 cells at zoom 18).
        px0_lat, px0_lon = t_lat_max, t_lon_min
        px1_lat = t_lat_max - (1.0 / h) * (t_lat_max - t_lat_min)
        px1_lon = t_lon_min + (1.0 / w) * (t_lon_max - t_lon_min)
        wx0, wy0 = latlon_to_world(px0_lat, px0_lon, params)
        wx1, wy1 = latlon_to_world(px1_lat, px1_lon, params)
        cells_per_px_x = max(1, int(math.ceil(abs(wx1 - wx0))))
        cells_per_px_y = max(1, int(math.ceil(abs(wy1 - wy0))))

        for py in range(h):
            for px in range(w):
                mat_id = int(materials_2d[py, px])
                pixel_stats[mat_id] += 1

                # Skip grass pixels (default, no vote needed).
                if mat_id == MAT_GRASS:
                    continue

                # Pixel position to lat/lon via linear interpolation within tile.
                frac_x = (px + 0.5) / w
                frac_y = (py + 0.5) / h
                lat = t_lat_max - frac_y * (t_lat_max - t_lat_min)
                lon = t_lon_min + frac_x * (t_lon_max - t_lon_min)

                # Lat/lon to world coords (with calibration).
                wx, wy = latlon_to_world(lat, lon, params)

                # Check content bounds.
                if not (bounds_min_x <= wx <= bounds_max_x and
                        bounds_min_y <= wy <= bounds_max_y):
                    continue

                # Vote for all cells in this pixel's footprint (~2x2 cells).
                base_cx, base_cy = world_to_cell(wx, wy)
                for dy in range(cells_per_px_y):
                    for dx in range(cells_per_px_x):
                        key = (base_cx + dx, base_cy + dy)
                        if key not in cell_votes:
                            cell_votes[key] = Counter()
                        cell_votes[key][mat_id] += 1

        if (tile_idx + 1) % 3 == 0 or tile_idx == len(successes) - 1:
            print(f"  Processed {tile_idx + 1}/{len(successes)} tiles, "
                  f"{len(cell_votes)} candidate cells")

    # 7. Apply majority vote.
    #    - Material > 50% of votes -> paint that material
    #    - No material > 50% -> leave as grass
    #    - Exact tie -> priority: stone > dirt > sand > water
    TIE_PRIORITY = {3: 0, 2: 1, 4: 2, 0: 3}  # stone, dirt, sand, water

    paint_decisions: dict[tuple[int, int], int] = {}
    for (cx, cy), votes in cell_votes.items():
        total_v = sum(votes.values())
        best_mat = None
        best_count = 0
        for mat_id, count in votes.items():
            if count > best_count:
                best_mat = mat_id
                best_count = count
            elif count == best_count and best_mat is not None:
                # Tie-break by priority.
                if TIE_PRIORITY.get(mat_id, 99) < TIE_PRIORITY.get(best_mat, 99):
                    best_mat = mat_id

        if best_mat is not None and best_count > total_v * 0.5:
            paint_decisions[(cx, cy)] = best_mat

    print(f"\nVote results: {len(paint_decisions)} cells to paint "
          f"(from {len(cell_votes)} candidate cells)")

    if dry_run:
        _print_statistics(pixel_stats, paint_decisions, successes, failures, flagged_tiles)
        print("\nDry run: no A3D modifications made.")
        return 0

    # 8. Load A3D and apply paint.
    if not map_path.exists():
        print(f"ERROR: {map_path} not found", file=sys.stderr)
        return 1

    print(f"\nLoading A3D: {map_path}")
    header, patches, mat_data, raw_fmt_version, instances, player_start, enemy_gens, markers = \
        _load_a3d(map_path)

    patches_by_xy = {(p.x, p.y): p for p in patches}
    road_influence_features = _load_road_influence_features(map_path)
    if road_influence_features:
        print(f"Loaded {len(road_influence_features)} OSM road feature(s) for satellite road fill.")
    else:
        print("No OSM road feature sidecar found; satellite pavement will stay non-road pavement.")

    cells_painted = Counter()
    cells_skipped_building = 0
    cells_skipped_osm = 0
    cells_skipped_no_patch = 0

    for (cx, cy), mat_id in paint_decisions.items():
        px = math.floor(cx / PATCH_WORLD)
        py = math.floor(cy / PATCH_WORLD)
        patch = patches_by_xy.get((px, py))
        if patch is None:
            cells_skipped_no_patch += 1
            continue

        lx = int(max(0, min(fmt.VISUAL_CELLS - 1, cx - px * int(PATCH_WORLD))))
        ly = int(max(0, min(fmt.VISUAL_CELLS - 1, cy - py * int(PATCH_WORLD))))

        # OSM features take priority, except dirt->water corrections.
        current_mat = patch.visual[ly][lx] & 0xFF  # mask off shade/elevation bits
        road_owned = (
            mat_id in SATELLITE_PAVEMENT_SOURCE_MATS
            and _cell_near_road(cx, cy, road_influence_features)
        )
        if current_mat != MAT_GRASS:
            # Allow satellite to correct dirt cells to water (OSM often misses ponds).
            from satellite_classify import MAT_WATER, MAT_DIRT
            # Allow road-owned satellite pavement to overwrite prior tan pavement
            # from parking/plaza/footway fills.  Buildings are still protected by
            # the ground-cell gate below.
            if not (
                current_mat == MAT_DIRT and mat_id == MAT_WATER
                or road_owned and current_mat == SATELLITE_STONE_PAINT_MAT
            ):
                cells_skipped_osm += 1
                continue

        # Preserve building walls/roofs.
        if not is_ground_cell(patch, ly, lx):
            cells_skipped_building += 1
            continue

        # Paint: remap classifier mat_ids to palette mat_ids.
        # FL-3838/FL-3853: untagged satellite pavement must not become road
        # grey unless it is near an OSM road centerline.  That road influence
        # lets satellite pixels fill the real carriageway instead of leaving a
        # thin OSM outline inside tan pavement.
        if road_owned:
            shade_elev = patch.visual[ly][lx] & 0xFF00
            patch.visual[ly][lx] = shade_elev | SATELLITE_ROAD_PAINT_MAT
            paint_mat = SATELLITE_ROAD_PAINT_MAT
        elif mat_id in SATELLITE_PAVEMENT_SOURCE_MATS:
            paint_mat = SATELLITE_STONE_PAINT_MAT
            paint_shade = SATELLITE_STONE_PAINT_SHADE
            patch.visual[ly][lx] = (paint_shade << 8) | paint_mat
        else:
            paint_mat = mat_id
            shade_elev = patch.visual[ly][lx] & 0xFF00
            patch.visual[ly][lx] = shade_elev | mat_id
        cells_painted[paint_mat] += 1

    total_painted = sum(cells_painted.values())
    print(f"\nPainted {total_painted} cells:")
    for mat_id in sorted(cells_painted.keys()):
        print(f"  {PAINT_MAT_NAMES.get(mat_id, MAT_NAMES.get(mat_id, f'id={mat_id}')):8s}: {cells_painted[mat_id]}")
    print(f"Skipped: {cells_skipped_osm} (OSM priority), "
          f"{cells_skipped_building} (building), "
          f"{cells_skipped_no_patch} (no patch)")

    if total_painted == 0:
        print("\nNo cells changed. Skipping A3D write.")
        return 0

    # 9. Write A3D with backup.
    print(f"\nWriting A3D: {map_path}")
    safe_write_a3d(map_path, header, patches, mat_data,
                   raw_fmt_version, instances, player_start, enemy_gens, markers)
    print("A3D written.")

    _print_statistics(pixel_stats, paint_decisions, successes, failures, flagged_tiles)
    return 0


def _print_statistics(pixel_stats, paint_decisions, successes, failures, flagged_tiles):
    """Print summary statistics."""
    total_px = sum(pixel_stats.values())
    print(f"\n--- Statistics ---")
    print(f"Tiles: {len(successes)} fetched, {len(failures)} failed, "
          f"{flagged_tiles} flagged (snow/cloud)")
    if total_px > 0:
        print(f"Pixel classification ({total_px} total):")
        for mat_id in sorted(MAT_NAMES.keys()):
            count = pixel_stats.get(mat_id, 0)
            pct = count / total_px * 100
            print(f"  {MAT_NAMES[mat_id]:8s}: {count:8d} ({pct:5.1f}%)")
    print(f"Paint decisions: {len(paint_decisions)} cells")
    if paint_decisions:
        decision_counts = Counter(paint_decisions.values())
        for mat_id in sorted(decision_counts.keys()):
            print(f"  {MAT_NAMES.get(mat_id, f'id={mat_id}'):8s}: {decision_counts[mat_id]}")


def main():
    parser = argparse.ArgumentParser(
        description="Paint A3D terrain cells from satellite imagery classification"
    )
    parser.add_argument("--map", type=Path, required=True, help="Input A3D map file")
    parser.add_argument("--metadata", type=Path, required=True,
                        help="terrain_metadata.json")
    parser.add_argument("--zoom", type=int, default=18,
                        help="Satellite tile zoom level (default: 18)")
    parser.add_argument("--max-tiles", type=int, default=100,
                        help="Maximum tiles to fetch (default: 100)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify and report without modifying A3D")
    parser.add_argument("--force", action="store_true",
                        help="Override snow/cloud gate")
    args = parser.parse_args()

    if not args.metadata.exists():
        print(f"ERROR: {args.metadata} not found", file=sys.stderr)
        return 1

    return run_pipeline(
        map_path=args.map,
        metadata_path=args.metadata,
        zoom=args.zoom,
        max_tiles=args.max_tiles,
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())
