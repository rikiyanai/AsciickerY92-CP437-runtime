#!/usr/bin/env python3
"""Measure each canonical road's clear corridor from airborne LiDAR (FL-4934).

OFFLINE EVIDENCE COMPILER. It consumes the canonical World IR road centrelines
and the TMG point-cloud sheets (FL-4929) and emits, per road feature, the
width of ground that is free of walls, fences, parked vehicles and hedges. It
does not decide which roads use the value; compile_surface_stamp_plan.py owns
that rule and the width precedence.

Method (the FL-4934 probe, fixed there before implementation):

* 0.5 m grid. Ground = mean class-2 z per cell, gaps filled from the nearest
  ground cell within 1.5 m. A cell is an OBSTACLE when any non-ground return
  lies 0.5-2.5 m above its ground, or when it has no ground within 1.5 m
  (inside a building or wall). The upper limit keeps overhead wires, tree
  canopy and eaves from closing the corridor.
* Every 4 m along the centreline (2 m clear of each end), cast perpendicular
  rays both ways in 0.25 m steps out to 15 m and stop at the first obstacle.
  A station whose centre point is itself an obstacle (a parked car on the
  centreline) is skipped.
* Per road: the median corridor over stations, plus p25 and the station count.

The LAS reader is the one compile_lidar_roof_field.py already owns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "pipeline"))

SCHEMA = "y92.lidar_road_corridor.v1"
GRID_M = 0.5
GROUND_FILL_M = 1.5
OBSTACLE_BAND_M = (0.5, 2.5)
STATION_SPACING_M = 4.0
END_CLEARANCE_M = 2.0
RAY_STEP_M = 0.25
RAY_MAX_M = 15.0
MIN_STATIONS = 3


def obstacle_grid(X, Y, Z, C, bounds):
    """Boolean obstacle raster plus its origin, from arrays in the road frame."""
    import numpy as np
    from scipy import ndimage

    x0, y0, x1, y1 = bounds
    nx, ny = int((x1 - x0) / GRID_M) + 1, int((y1 - y0) / GRID_M) + 1
    ix = ((X - x0) / GRID_M).astype(np.int64)
    iy = ((Y - y0) / GRID_M).astype(np.int64)
    inside = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    g = inside & (C == 2)
    gsum = np.zeros((ny, nx)); gcnt = np.zeros((ny, nx))
    np.add.at(gsum, (iy[g], ix[g]), Z[g]); np.add.at(gcnt, (iy[g], ix[g]), 1)
    ground = np.where(gcnt > 0, gsum / np.maximum(gcnt, 1), np.nan)
    missing = np.isnan(ground)
    if missing.all():
        raise SystemExit("no class-2 ground inside the road bounds; wrong sheets or CRS")
    dist, (ri, rj) = ndimage.distance_transform_edt(missing, return_indices=True)
    filled = ground[ri, rj]
    filled[dist > GROUND_FILL_M / GRID_M] = np.nan
    o = inside & (C != 2)
    h = Z[o] - filled[iy[o], ix[o]]
    hit = (h >= OBSTACLE_BAND_M[0]) & (h <= OBSTACLE_BAND_M[1])
    obstacle = np.isnan(filled)
    obstacle[iy[o][hit], ix[o][hit]] = True
    return obstacle, (x0, y0)


def corridor_stations(line, obstacle, origin):
    """Clear corridor widths (m) at stations along a shapely LineString."""
    x0, y0 = origin
    ny, nx = obstacle.shape

    def blocked(x, y):
        i, j = int((y - y0) / GRID_M), int((x - x0) / GRID_M)
        return not (0 <= i < ny and 0 <= j < nx) or bool(obstacle[i, j])

    def clear(px, py, ux, uy):
        d = 0.0
        while d < RAY_MAX_M:
            d += RAY_STEP_M
            if blocked(px + ux * d, py + uy * d):
                return d - RAY_STEP_M
        return RAY_MAX_M

    # FL-4934 v2: keep each side separately. Left is the +normal side of the
    # line direction, which is shapely's positive offset_curve side.
    sides = []
    length = line.length
    s = END_CLEARANCE_M
    while s < length - END_CLEARANCE_M:
        p = line.interpolate(s)
        q = line.interpolate(min(length, s + 0.5))
        tx, ty = q.x - p.x, q.y - p.y
        tn = math.hypot(tx, ty) or 1.0
        ux, uy = -ty / tn, tx / tn
        if not blocked(p.x, p.y):
            sides.append((s, clear(p.x, p.y, ux, uy), clear(p.x, p.y, -ux, -uy)))
        s += STATION_SPACING_M
    return sides


def compile_corridors(world_ir: Path, laz_paths: list[Path]) -> dict:
    import numpy as np
    from pyproj import Transformer
    from shapely.geometry import LineString

    from compile_lidar_roof_field import _load_las_arrays, file_sha256

    doc = json.loads(Path(world_ir).read_text(encoding="utf-8"))
    roads = []
    for feat in doc.get("canonical_features", []):
        if feat.get("semantic_class") != "road":
            continue
        geom = feat.get("geometry") or {}
        if geom.get("kind") != "polyline" or geom.get("frame_id") != "epsg32654":
            continue
        coords = [(float(p[0]), float(p[1])) for p in geom.get("coordinates", [])]
        if len(coords) >= 2:
            roads.append((str(feat["feature_id"]), LineString(coords)))
    if not roads:
        raise SystemExit(f"{world_ir}: no EPSG:32654 road polylines")

    # FL-4934 v3: canonical footprints are the horizontal authority on this
    # map, so the strip measured here must stop at them. v2 let the corridor
    # run under eaves (above the 2.5 m band) and into the footprint edge band:
    # 177 of 190 m2 of its building overlap lay within 1 m of a footprint edge.
    footprints = []
    for feat in doc.get("canonical_features", []):
        geom = feat.get("geometry") or {}
        if (feat.get("semantic_class") == "building_footprint" and geom.get("kind") == "polygon"
                and geom.get("frame_id") == "epsg32654"):
            from shapely.geometry import Polygon
            footprints.append(Polygon([(float(p[0]), float(p[1])) for p in geom["coordinates"]]).buffer(0))

    X6677, Y6677, Z, C, *_ = _load_las_arrays(laz_paths)
    to_utm = Transformer.from_crs("EPSG:6677", "EPSG:32654", always_xy=True)
    X, Y = to_utm.transform(X6677, Y6677)
    pad = RAY_MAX_M + 5.0
    xs = [b for _, g in roads for b in g.bounds[0::2]]
    ys = [b for _, g in roads for b in g.bounds[1::2]]
    bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    keep = (X >= bounds[0]) & (X <= bounds[2]) & (Y >= bounds[1]) & (Y <= bounds[3])
    obstacle, origin = obstacle_grid(X[keep], Y[keep], np.asarray(Z)[keep], np.asarray(C)[keep], bounds)
    if footprints:
        from shapely import contains_xy
        from shapely.ops import unary_union
        ny, nx = obstacle.shape
        gx, gy = np.meshgrid(origin[0] + (np.arange(nx) + 0.5) * GRID_M,
                             origin[1] + (np.arange(ny) + 0.5) * GRID_M)
        obstacle |= contains_xy(unary_union(footprints), gx, gy)

    rows = []
    for fid, line in sorted(roads):
        sides = corridor_stations(line, obstacle, origin)
        row = {"feature_id": fid, "length_m": round(line.length, 2), "stations": len(sides)}
        if len(sides) >= MIN_STATIONS:
            totals = [l + r for _, l, r in sides]
            row["corridor_median_m"] = round(float(np.median(totals)), 2)
            row["corridor_p25_m"] = round(float(np.percentile(totals, 25)), 2)
            row["left_median_m"] = round(float(np.median([l for _, l, _ in sides])), 2)
            row["right_median_m"] = round(float(np.median([r for _, _, r in sides])), 2)
            # FL-4934 v4: the stations themselves, so the consumer can follow
            # local narrowing instead of painting one width along the road.
            row["stations_s_left_right_m"] = [[round(s, 2), round(l, 2), round(r, 2)] for s, l, r in sides]
        rows.append(row)
    return {
        "schema": SCHEMA,
        "compiler": "scripts/pipeline/compile_lidar_road_corridor.py",
        "world_ir": str(world_ir),
        "world_ir_sha256": hashlib.sha256(Path(world_ir).read_bytes()).hexdigest(),
        "tiles": [Path(p).name for p in laz_paths],
        "tile_sha256": [file_sha256(Path(p)) for p in laz_paths],
        "horizontal_crs": "EPSG:6677 sheets reprojected to EPSG:32654",
        "method": {
            "grid_m": GRID_M, "ground_fill_m": GROUND_FILL_M,
            "obstacle_band_m": list(OBSTACLE_BAND_M),
            "footprint_obstacles": "canonical building_footprint polygons rasterised as obstacles (cell centres)",
            "station_spacing_m": STATION_SPACING_M, "end_clearance_m": END_CLEARANCE_M,
            "ray_step_m": RAY_STEP_M, "ray_max_m": RAY_MAX_M, "min_stations": MIN_STATIONS,
            "statistic": ("median clear corridor over stations; left/right medians per side, "
                          "left = positive offset_curve side of the line direction"),
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--world-ir", type=Path, required=True, help="canonical.world_ir.json with road polylines")
    ap.add_argument("--laz", type=Path, action="append", required=True, help="LAS sheet; repeat per sheet")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    doc = compile_corridors(args.world_ir, args.laz)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    measured = [r for r in doc["rows"] if "corridor_median_m" in r]
    print(f"[road-corridor] roads={len(doc['rows'])} measured={len(measured)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
