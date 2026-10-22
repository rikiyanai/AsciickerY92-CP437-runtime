#!/usr/bin/env python3
"""Compile airborne LiDAR into a measured roof-elevation field for one building.

This is an OFFLINE EVIDENCE COMPILER, not a runtime owner and not a second map
pipeline.  It consumes a LAZ/LAS tile plus the authoritative OSM footprint that
the existing pipeline already produced, and emits a compact, provenance-bearing
roof field that ``bake_osm_building_footprints.py`` can lower into the existing
A3D height vertices.

Division of authority (FL-4787):

* The OSM footprint remains the sole HORIZONTAL authority.  This tool never
  invents, simplifies or re-derives footprint geometry; it only samples inside
  one that was given to it.
* LiDAR becomes the ROOF-ELEVATION authority for the building, replacing the
  blosm mesh extent that ``osm.py:784`` currently mislabels ``is_measured``.
* The field is expressed as **metres above local ground**, never as an absolute
  datum elevation.  Lowering re-references it to whatever ground the terrain
  already has, so NAVD88/geoid never leaks into the game world and a terrain
  change cannot silently sink or float the building.

The emitted field answers exactly one question::

    roof_height_at(world_x, world_y) -> metres above local ground

evaluated on a lattice aligned to the A3D height-vertex grid (world origin,
2.0 world-unit step).  Because the lattice is defined in WORLD coordinates and
not per-patch, a vertex shared by two patches resolves to the same value from
either side, so the lowering cannot open cracks at patch seams.

The field is deliberately NOT a mesh, NOT a new geometry format, and NOT a
second semantic-map IR.  It is evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "pipeline"))
if str(PROJECT_ROOT) not in sys.path:
    # geo_frames (the Numabe frame owner) imports scripts.pipeline.world_ir.
    sys.path.insert(1, str(PROJECT_ROOT))

from osm_projection import latlon_to_world, world_to_latlon  # noqa: E402

# A3D height vertices sit every PATCH_WORLD / HEIGHT_CELLS = 8 / 4 world units.
# Mirrors addons/io_asciicker/scene/a3d_format.py; the lattice must stay aligned
# to that grid or the lowering would interpolate instead of hitting vertices.
HEIGHT_STEP_WORLD = 2.0

PROVENANCE_MEASURED = "measured"
PROVENANCE_DERIVED = "derived"  # nearest-plane extrapolation to the footprint rim

# FL-4930 NUMABE-FRAME-01. The tool was written for the SBU run, whose
# terrain_metadata.json carries a lat/lon scene anchor for osm_projection. The
# Numabe World IR runs write y92.classic_base_projection.v1 instead: an affine
# map from EPSG:32654 metres, world = (utm - origin) * content_scale + offset,
# owned by scripts/pipeline/compile_world_frame.py over geo_frames' Kruger
# series. Both frames are accepted; neither is reinterpreted as the other.
AFFINE_FRAME_KIND = "epsg32654_affine"
AFFINE_FRAME_SCHEMA = "y92.classic_base_projection.v1"
# World IR geometry frames this tool can place in the affine run frame.
WORLD_IR_UTM_FRAME = "epsg32654"
WORLD_IR_RUN_FRAME = "plateau_run_projection"


# --------------------------------------------------------------------------
# geometry helpers (duplicated deliberately: this tool must not import the
# baker, which would create a cycle between evidence and lowering)
# --------------------------------------------------------------------------

def point_in_polygon(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if (ay > py) != (by > py):
            t = (py - ay) / (by - ay)
            if px < ax + t * (bx - ax):
                inside = not inside
    return inside


def _distance_to_segment(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_boundary(px: float, py: float, poly: list[tuple[float, float]]) -> float:
    n = len(poly)
    return min(
        _distance_to_segment(px, py, poly[i][0], poly[i][1], poly[(i + 1) % n][0], poly[(i + 1) % n][1])
        for i in range(n)
    )


def signed_inset_depth(px: float, py: float, poly: list[tuple[float, float]]) -> float:
    """Positive distance inside the polygon, negative outside."""
    d = distance_to_boundary(px, py, poly)
    return d if point_in_polygon(px, py, poly) else -d


def signed_inset_depth_array(px, py, poly: list[tuple[float, float]]):
    """signed_inset_depth over numpy arrays: the same formula, edge by edge.

    FL-4930: the per-point Python loop was fine for one SBU building on a
    4 pts/m2 tile. The Numabe sheets carry 36-50 pts/m2 and the AOI has about
    1,360 source footprints, so the loop dominated the run. The parity test
    pins this to the scalar function.
    """
    import numpy as np

    px = np.asarray(px, dtype=float)
    py = np.asarray(py, dtype=float)
    inside = np.zeros(px.shape, dtype=bool)
    dist = np.full(px.shape, np.inf)
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        # ray casting, identical branch structure to point_in_polygon
        if ay != by:
            crosses = (ay > py) != (by > py)
            t = (py - ay) / (by - ay)
            inside ^= crosses & (px < ax + t * (bx - ax))
        dx, dy = bx - ax, by - ay
        if dx == 0.0 and dy == 0.0:
            seg = np.hypot(px - ax, py - ay)
        else:
            u = np.clip(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
            seg = np.hypot(px - (ax + u * dx), py - (ay + u * dy))
        dist = np.minimum(dist, seg)
    return np.where(inside, dist, -dist)


def polygon_area(poly: list[tuple[float, float]]) -> float:
    a = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def load_scene_params(terrain_metadata: Path) -> dict:
    meta = json.loads(terrain_metadata.read_text())
    if meta.get("projection_kind") == AFFINE_FRAME_KIND:
        if meta.get("schema") != AFFINE_FRAME_SCHEMA:
            raise SystemExit(
                f"{terrain_metadata}: projection_kind {AFFINE_FRAME_KIND} needs schema "
                f"{AFFINE_FRAME_SCHEMA}, got {meta.get('schema')!r}"
            )
        return {
            "frame_kind": AFFINE_FRAME_KIND,
            "content_scale": float(meta["content_scale"]),
            "origin_easting": float(meta["origin_easting"]),
            "origin_northing": float(meta["origin_northing"]),
            "offset_x": float(meta["offset_x"]),
            "offset_y": float(meta["offset_y"]),
        }
    shift = meta.get("terrain_shift") or {}
    params = {
        "scene_lat": float(meta["scene_lat"]),
        "scene_lon": float(meta["scene_lon"]),
        "content_scale": float(meta["content_scale"]),
        "shift_x": float(shift.get("x", 0.0)),
        "shift_y": float(shift.get("y", 0.0)),
    }
    return params


def load_footprint(building_specs: Path, source: str) -> tuple[list[tuple[float, float]], dict]:
    data = json.loads(building_specs.read_text())
    if isinstance(data, dict):
        data = data.get("instances", data)
    matches = [s for s in data if s.get("source") == source]
    if not matches:
        raise SystemExit(f"no building spec with source={source!r} in {building_specs}")
    if len(matches) > 1:
        raise SystemExit(f"{len(matches)} specs share source={source!r}; refusing to guess")
    spec = matches[0]
    raw = spec.get("footprint") or spec.get("bake_footprint")
    if not raw or len(raw) < 3:
        raise SystemExit(f"spec {source!r} has no usable footprint")
    poly = [(float(p[0]), float(p[1])) for p in raw]
    return poly, spec


def world_to_lonlat(scene: dict, wx: float, wy: float) -> tuple[float, float]:
    """World XY -> EPSG:4326 (lon, lat) through the frame the run declared."""
    if scene.get("frame_kind") == AFFINE_FRAME_KIND:
        from scripts.pipeline.geo_frames import utm_inverse

        e = (wx - scene["offset_x"]) / scene["content_scale"] + scene["origin_easting"]
        n = (wy - scene["offset_y"]) / scene["content_scale"] + scene["origin_northing"]
        return utm_inverse(e, n)
    lat, lon = world_to_latlon(wx, wy, scene["scene_lat"], scene["scene_lon"],
                               scene["content_scale"], scene["shift_x"], scene["shift_y"])
    return lon, lat


_RUN_FRAME_NOTE = re.compile(
    r"\* (?P<scale>[0-9.]+) \+ offset \(origin (?P<e>[0-9.]+),(?P<n>[0-9.]+)\)"
)


def _run_frame_matches(frame_record: dict, scene: dict) -> bool:
    """The PLATEAU ingest states its run projection only in the frame note."""
    m = _RUN_FRAME_NOTE.search(str(frame_record.get("note", "")))
    if not m:
        return False
    return (
        math.isclose(float(m["scale"]), scene["content_scale"], rel_tol=0, abs_tol=1e-9)
        and math.isclose(float(m["e"]), scene["origin_easting"], rel_tol=0, abs_tol=1e-6)
        and math.isclose(float(m["n"]), scene["origin_northing"], rel_tol=0, abs_tol=1e-6)
    )


def load_world_ir_footprints(paths: list[Path], scene: dict) -> list[tuple[str, list[tuple[float, float]], dict]]:
    """Every building_footprint feature in the given World IR documents, in world XY.

    FL-4930: the footprint is still the horizontal authority and is only
    re-expressed in the run frame, never edited. A feature whose frame this
    tool cannot place exactly is refused rather than guessed.
    """
    if scene.get("frame_kind") != AFFINE_FRAME_KIND:
        raise SystemExit(f"--world-ir needs a {AFFINE_FRAME_SCHEMA} terrain metadata frame")
    s, oe, on = scene["content_scale"], scene["origin_easting"], scene["origin_northing"]
    ox, oy = scene["offset_x"], scene["offset_y"]
    out: dict[str, tuple[list[tuple[float, float]], dict]] = {}
    for path in paths:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        frames = doc.get("coordinate_frames") or {}
        for feat in doc.get("canonical_features", []):
            if feat.get("semantic_class") != "building_footprint":
                continue
            geom = feat.get("geometry") or {}
            fid = str(feat.get("feature_id", ""))
            if geom.get("kind") != "polygon":
                raise SystemExit(f"{path}: {fid} geometry kind {geom.get('kind')!r} is not a polygon")
            frame_id = geom.get("frame_id")
            coords = [(float(p[0]), float(p[1])) for p in geom.get("coordinates", [])]
            if frame_id == WORLD_IR_UTM_FRAME:
                poly = [((e - oe) * s + ox, (n - on) * s + oy) for e, n in coords]
            elif frame_id == WORLD_IR_RUN_FRAME:
                if not _run_frame_matches(frames.get(frame_id) or {}, scene):
                    raise SystemExit(f"{path}: frame {frame_id} does not match the run frame")
                poly = coords
            else:
                raise SystemExit(f"{path}: {fid} is in unsupported frame {frame_id!r}")
            if len(poly) > 1 and poly[0] == poly[-1]:
                poly = poly[:-1]
            if len(poly) < 3:
                raise SystemExit(f"{path}: {fid} has no usable footprint")
            source = fid.split(":", 2)[2] if fid.count(":") >= 2 else fid
            if source in out:
                raise SystemExit(f"two World IR footprints share source {source!r}; refusing to guess")
            out[source] = (poly, {"inst_name": None, "feature_id": fid, "world_ir": str(path)})
    return [(src, poly, meta) for src, (poly, meta) in sorted(out.items())]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# LiDAR reduction
# --------------------------------------------------------------------------

_LAS_CACHE: dict[str, tuple] = {}


def _las_paths(laz_path) -> list[Path]:
    """One tile path, or several sheets that together cover the footprints."""
    if isinstance(laz_path, (str, Path)):
        return [Path(laz_path)]
    return [Path(p) for p in laz_path]


def _load_las_arrays(laz_path) -> tuple:
    """Read the LAZ/LAS tiles once and reuse them for every building in the run.

    The tile is ~100 MB and decoding it dominates runtime, so a whole-run
    compile must not re-read it per building. The cache is keyed by resolved
    path and holds plain numpy arrays, never the laspy object.

    FL-4930: several sheets are concatenated in the order given. The Numabe
    AOI spans five TMG sheets that tile without overlap (FL-4929 receipt), so
    concatenation neither drops nor duplicates a return.
    """
    import numpy as np
    import laspy

    paths = _las_paths(laz_path)
    key = "|".join(str(p.resolve()) for p in paths)
    hit = _LAS_CACHE.get(key)
    if hit is not None:
        return hit

    parts = []
    for path in paths:
        las = laspy.read(str(path))
        parts.append((
            np.asarray(las.x),
            np.asarray(las.y),
            np.asarray(las.z),
            np.asarray(las.classification),
            np.asarray(las.return_number),
            np.asarray(las.number_of_returns),
        ))
    cols = [np.concatenate([p[k] for p in parts]) if len(parts) > 1 else parts[0][k] for k in range(6)]
    arrays = (*cols, int(cols[0].size))
    _LAS_CACHE[key] = arrays
    return arrays


def compile_roof_field(
    laz_path: Path,
    poly_world: list[tuple[float, float]],
    scene: dict,
    *,
    source_crs: str,
    ground_ring_inner_m: float = 3.0,
    ground_ring_outer_m: float = 12.0,
    footprint_erode_m: float = 1.0,
    sample_radius_m: float = 1.5,
    min_samples: int = 4,
    min_roof_returns_floor: int = 25,
    roof_returns_per_m2: float = 0.5,
    tier_bandwidth_m: float = 0.6,
    tier_min_fraction: float = 0.02,
    footprint_authority: str = "OSM footprint from the existing pipeline; unmodified",
) -> dict:
    import numpy as np
    import laspy
    from pyproj import Transformer

    # --- footprint in every frame we need -------------------------------
    to_src = Transformer.from_crs("EPSG:4326", source_crs, always_xy=True)
    poly_src = [tuple(to_src.transform(*world_to_lonlat(scene, x, y))) for x, y in poly_world]

    area_m2 = polygon_area(poly_src)
    xs = [p[0] for p in poly_src]
    ys = [p[1] for p in poly_src]
    pad = ground_ring_outer_m + 5.0
    bx0, bx1 = min(xs) - pad, max(xs) + pad
    by0, by1 = min(ys) - pad, max(ys) + pad

    X, Y, Z, cls, ret, nret, total_points = _load_las_arrays(laz_path)

    box = (X >= bx0) & (X <= bx1) & (Y >= by0) & (Y <= by1)
    if not np.any(box):
        raise SystemExit("footprint bbox selects no LiDAR points; wrong tile or wrong CRS")
    X, Y, Z, cls, ret, nret = X[box], Y[box], Z[box], cls[box], ret[box], nret[box]

    classes_present = sorted(int(c) for c in np.unique(cls))

    # --- inset depth for every candidate point --------------------------
    depth = signed_inset_depth_array(X, Y, poly_src)

    # --- ground: class 2 in a ring OUTSIDE the footprint ----------------
    ring = (depth <= -ground_ring_inner_m) & (depth >= -ground_ring_outer_m) & (cls == 2)
    ground_n = int(np.count_nonzero(ring))
    if ground_n < 50:
        raise SystemExit(f"only {ground_n} class-2 ground returns in ring; refusing to estimate ground")
    ground_elev = float(np.median(Z[ring]))

    # --- roof: non-ground, single return, inside the eroded footprint ---
    # Gate on class-2 vs non-class-2, NOT on class 6: this tile carries no
    # class-6 building points at all (FL-4787 gate-design correction).
    single = ret == nret

    # Evidence gate. A fixed erosion plus a fixed count rejects small
    # buildings for being small rather than for lacking evidence: a 1.0 m
    # erosion removes most of a shed's footprint, and a shed legitimately
    # carries far fewer returns than a lecture hall. Scale the requirement to
    # the footprint and relax the erosion before giving up, then record which
    # erosion actually produced the accepted evidence.
    min_roof_returns = max(min_roof_returns_floor,
                           min(100, int(area_m2 * roof_returns_per_m2)))
    erode_used = footprint_erode_m
    roof_mask = None
    roof_n = 0
    for candidate_erode in (footprint_erode_m, footprint_erode_m * 0.5, 0.0):
        mask = (depth >= candidate_erode) & (cls != 2) & single
        n = int(np.count_nonzero(mask))
        if n >= min_roof_returns:
            roof_mask, roof_n, erode_used = mask, n, candidate_erode
            break
        if n > roof_n:
            roof_mask, roof_n, erode_used = mask, n, candidate_erode
    if roof_mask is None or roof_n < min_roof_returns:
        raise SystemExit(
            f"only {roof_n} roof returns inside footprint "
            f"(area {area_m2:.0f} m2 needs {min_roof_returns}); insufficient evidence"
        )
    RX, RY, RZ = X[roof_mask], Y[roof_mask], Z[roof_mask]
    roof_rel = RZ - ground_elev

    # --- lattice aligned to the A3D height-vertex grid, in WORLD units --
    wxs = [p[0] for p in poly_world]
    wys = [p[1] for p in poly_world]
    i0 = int(math.floor(min(wxs) / HEIGHT_STEP_WORLD)) - 1
    i1 = int(math.ceil(max(wxs) / HEIGHT_STEP_WORLD)) + 1
    j0 = int(math.floor(min(wys) / HEIGHT_STEP_WORLD)) - 1
    j1 = int(math.ceil(max(wys) / HEIGHT_STEP_WORLD)) + 1

    r2 = sample_radius_m * sample_radius_m

    samples: dict[tuple[int, int], tuple[float, int]] = {}
    for j in range(j0, j1 + 1):
        for i in range(i0, i1 + 1):
            wx = i * HEIGHT_STEP_WORLD
            wy = j * HEIGHT_STEP_WORLD
            if signed_inset_depth(wx, wy, poly_world) < 0.0:
                continue
            sx, sy = to_src.transform(*world_to_lonlat(scene, wx, wy))
            d2 = (RX - sx) ** 2 + (RY - sy) ** 2
            sel = d2 <= r2
            n = int(np.count_nonzero(sel))
            if n < min_samples:
                continue
            samples[(i, j)] = (float(np.median(roof_rel[sel])), n)

    if not samples:
        raise SystemExit("no lattice vertex inside the footprint gathered enough roof returns")

    # ------------------------------------------------------------------
    # Edge fill.  Roof returns are sampled from a footprint eroded by
    # footprint_erode_m (metres) to keep wall and facade returns out of the
    # roof statistic, but the baker writes height vertices much closer to the
    # boundary.  Without a fill those edge vertices would silently fall back
    # to the scalar height and reinstate the flat mesa around the rim.
    #
    # A roof does extend to the building edge, so extending the nearest
    # measured plane outward is the physically right answer -- but it is an
    # EXTRAPOLATION, not an observation, so these vertices are labelled
    # derived and counted separately.  Nearest-neighbour on the lattice keeps
    # it deterministic and therefore still seam-safe.
    # ------------------------------------------------------------------
    measured_keys = list(samples.keys())
    mk = np.array(measured_keys, dtype=float)
    filled: dict[tuple[int, int], tuple[float, int]] = {}
    for j in range(j0, j1 + 1):
        for i in range(i0, i1 + 1):
            if (i, j) in samples:
                continue
            wx = i * HEIGHT_STEP_WORLD
            wy = j * HEIGHT_STEP_WORLD
            if signed_inset_depth(wx, wy, poly_world) < 0.0:
                continue
            d2 = (mk[:, 0] - i) ** 2 + (mk[:, 1] - j) ** 2
            src = measured_keys[int(np.argmin(d2))]
            filled[(i, j)] = (samples[src][0], 0)

    # --- tier detection on the lattice medians --------------------------
    vals = np.array(sorted(v for v, _ in samples.values()))
    tiers = _detect_tiers(vals, tier_bandwidth_m, tier_min_fraction)

    # --- snap each vertex to its tier -----------------------------------
    cells = []
    region_members: dict[int, list[float]] = {k: [] for k in range(len(tiers))}
    all_cells = dict(samples)
    all_cells.update(filled)
    for (i, j), (v, n) in sorted(all_cells.items()):
        k = min(range(len(tiers)), key=lambda t: abs(tiers[t] - v))
        residual = abs(tiers[k] - v)
        if residual > max(tier_bandwidth_m * 2.0, 1.5):
            k = -1  # keep the raw median; do not force it onto a tier
        region_members.setdefault(k, []).append(v)
        cells.append(
            {
                "i": i,
                "j": j,
                "roof_m": round(tiers[k] if k >= 0 else v, 3),
                "raw_m": round(v, 3),
                "region": k,
                "n": n,
                # n == 0 marks an edge vertex carrying the nearest measured
                # plane rather than its own observation.
                "provenance_class": PROVENANCE_MEASURED if n else PROVENANCE_DERIVED,
            }
        )

    regions = []
    for k, tier in enumerate(tiers):
        mem = region_members.get(k) or []
        if not mem:
            continue
        arr = np.array(mem)
        regions.append(
            {
                "region": k,
                "elevation_m_above_ground": round(float(tier), 3),
                "vertex_count": int(arr.size),
                "vertex_fraction": round(float(arr.size) / len(cells), 4),
                "residual_rms_m": round(float(np.sqrt(np.mean((arr - tier) ** 2))), 3),
                "estimator": "median of lattice-vertex medians within tier bandwidth",
            }
        )
    unsnapped = len(region_members.get(-1) or [])

    spread = float(np.percentile(vals, 95) - np.percentile(vals, 5))
    return {
        "grid": {
            "frame": "a3d_world",
            "origin_x": 0.0,
            "origin_y": 0.0,
            "step": HEIGHT_STEP_WORLD,
            "lookup": "nearest lattice vertex; value is metres above local ground",
        },
        "cells": cells,
        "measured_vertices": len(samples),
        "derived_edge_vertices": len(filled),
        "roof_regions": regions,
        "unsnapped_vertices": unsnapped,
        "ground": {
            "estimator": f"median of class-2 returns in a {ground_ring_inner_m}-{ground_ring_outer_m} m ring outside the footprint",
            "sample_count": ground_n,
            "elevation": round(ground_elev, 3),
            "units": "metres, source vertical datum",
        },
        "roof_samples": {
            "estimator": "non-class-2 single returns inside the eroded footprint",
            "sample_count": roof_n,
            "density_per_m2": round(roof_n / area_m2, 3) if area_m2 else None,
            "footprint_erode_m": footprint_erode_m,
            "sample_radius_m": sample_radius_m,
            "min_samples_per_vertex": min_samples,
            "footprint_erode_m_used": erode_used,
            "min_roof_returns_required": min_roof_returns,
        },
        "uncertainty": {
            "tier_bandwidth_m": tier_bandwidth_m,
            "p5_p95_spread_m": round(spread, 3),
            "note": "spread is the real four-tier structure of the building, not sensor noise; "
                    "per-region residual_rms_m is the per-plane uncertainty",
        },
        "footprint": {
            "source_area_m2": round(area_m2, 1),
            "vertex_count": len(poly_world),
            "authority": footprint_authority,
        },
        "tile": {
            "classes_present": classes_present,
            "points_total": total_points,
            "points_in_bbox": int(X.size),
        },
    }


def _detect_tiers(vals, bandwidth: float, min_fraction: float) -> list[float]:
    """1-D mode seeking on sorted values; returns tier elevations, ascending."""
    import numpy as np

    if vals.size == 0:
        return []
    lo, hi = float(vals[0]), float(vals[-1])
    if hi - lo < bandwidth:
        return [float(np.median(vals))]
    step = bandwidth / 4.0
    grid = np.arange(lo - bandwidth, hi + bandwidth + step, step)
    dens = np.array([np.count_nonzero(np.abs(vals - g) <= bandwidth) for g in grid], dtype=float)
    peaks = []
    for idx in range(1, len(grid) - 1):
        if dens[idx] >= dens[idx - 1] and dens[idx] > dens[idx + 1]:
            peaks.append(idx)
    if not peaks:
        return [float(np.median(vals))]
    # merge peaks closer than the bandwidth, keep the denser one
    merged = []
    for idx in peaks:
        if merged and grid[idx] - grid[merged[-1]] < bandwidth:
            if dens[idx] > dens[merged[-1]]:
                merged[-1] = idx
        else:
            merged.append(idx)
    tiers = []
    threshold = max(1.0, min_fraction * vals.size)
    for idx in merged:
        if dens[idx] < threshold:
            continue
        near = vals[np.abs(vals - grid[idx]) <= bandwidth]
        if near.size:
            tiers.append(float(np.median(near)))
    # Two distinct density peaks can still reduce to nearly the same median
    # (a broad shoulder sampled twice).  Collapse them: a tier is a plane, and
    # two "planes" less than one bandwidth apart are one plane.
    collapsed: list[float] = []
    for t in sorted(tiers):
        if collapsed and t - collapsed[-1] < bandwidth:
            near = vals[np.abs(vals - 0.5 * (t + collapsed[-1])) <= bandwidth]
            collapsed[-1] = float(np.median(near)) if near.size else collapsed[-1]
        else:
            collapsed.append(t)
    return [round(t, 3) for t in collapsed] or [float(np.median(vals))]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--laz", type=Path, required=True, action="append",
                    help="LAZ/LAS tile covering the footprint; repeat for adjacent sheets")
    ap.add_argument("--building-specs", type=Path, help="building_instances.json from the run")
    ap.add_argument("--world-ir", type=Path, action="append",
                    help="World IR document whose building_footprint features are the footprints "
                         "(repeatable; needs a y92.classic_base_projection.v1 terrain metadata)")
    ap.add_argument("--source", help='OSM source id, e.g. "way/54722289"')
    ap.add_argument("--terrain-metadata", type=Path, required=True, help="terrain_metadata.json from the run")
    ap.add_argument("--out", type=Path, required=True, help="output roof-field JSON")
    ap.add_argument("--source-crs", default="EPSG:6347", help="horizontal CRS of the LiDAR tile")
    ap.add_argument("--vertical-datum", default="NAVD88 (Geoid18)")
    ap.add_argument("--dataset", default="", help="human-readable dataset name for provenance")
    ap.add_argument("--acquisition", default="", help="acquisition date, ISO-8601")
    ap.add_argument("--sample-radius-m", type=float, default=1.5)
    ap.add_argument("--tier-bandwidth-m", type=float, default=0.6)
    ap.add_argument("--no-hash", action="store_true", help="skip the tile sha256 (large files)")
    ap.add_argument("--all", action="store_true",
                    help="compile every building in --building-specs or --world-ir; --out becomes a directory "
                         "and receives roof_field_summary.json")
    args = ap.parse_args(argv)

    scene = load_scene_params(args.terrain_metadata)

    if bool(args.all) == bool(args.source):
        raise SystemExit("pass exactly one of --source <osm id> or --all")
    if bool(args.building_specs) == bool(args.world_ir):
        raise SystemExit("pass exactly one of --building-specs or --world-ir")

    if args.world_ir:
        # FL-4930: footprints straight from World IR, keyed by source id.
        ir_rows = load_world_ir_footprints(args.world_ir, scene)
        ir_index = {src: (poly, meta) for src, poly, meta in ir_rows}
        all_targets = [src for src, _, _ in ir_rows]

        def footprint_for(source: str):
            if source not in ir_index:
                raise SystemExit(f"no World IR building_footprint with source={source!r}")
            return ir_index[source]
    else:
        specs = json.loads(args.building_specs.read_text())
        if isinstance(specs, dict):
            specs = specs.get("instances", specs)
        all_targets = [s.get("source") for s in specs if s.get("source")]

        def footprint_for(source: str):
            return load_footprint(args.building_specs, source)

    if args.all:
        targets = all_targets
        if not targets:
            raise SystemExit("no building with a source id in the given footprints")
        args.out.mkdir(parents=True, exist_ok=True)
    else:
        targets = [args.source]

    laz_paths = list(args.laz)
    tile_hashes = None if args.no_hash else [file_sha256(p) for p in laz_paths]
    if len(laz_paths) == 1:
        # One tile keeps the original scalar record shape.
        tile_name: object = laz_paths[0].name
        tile_hash: object = None if tile_hashes is None else tile_hashes[0]
        dataset_default = laz_paths[0].stem
    else:
        tile_name = [p.name for p in laz_paths]
        tile_hash = tile_hashes
        dataset_default = "+".join(p.stem for p in laz_paths)

    ok = 0
    failed: list[tuple[str, str]] = []
    summary_rows: list[dict] = []
    for source in targets:
        try:
            poly, spec = footprint_for(source)
            field = compile_roof_field(
                laz_paths,
                poly,
                scene,
                source_crs=args.source_crs,
                sample_radius_m=args.sample_radius_m,
                tier_bandwidth_m=args.tier_bandwidth_m,
                **({"footprint_authority":
                    f"World IR geometry of {spec['feature_id']}; re-expressed in the run frame, unmodified"}
                   if args.world_ir else {}),
            )
        except SystemExit as exc:
            # A single building that the tile cannot support must not abort a
            # whole-run compile. The caller keeps its legacy height and the
            # reason is reported.
            if not args.all:
                raise
            failed.append((source, str(exc)))
            summary_rows.append({"source": source, "status": "skipped", "reason": str(exc)})
            print(f"[roof-field] SKIP {source}: {exc}")
            continue

        doc = {
            "schema": "asciicker.roof_field/v1",
            "height_model": {"kind": "lidar_roof_field"},
            "provenance_class": PROVENANCE_MEASURED,
            "building": {"source": source, "inst_name": spec.get("inst_name")},
            "source": {
                "dataset": args.dataset or dataset_default,
                "tile": tile_name,
                "tile_sha256": tile_hash,
                "acquisition": args.acquisition,
                "horizontal_crs": args.source_crs,
                "vertical_datum": args.vertical_datum,
            },
            "scene": scene,
            "lowering": {
                "rule": "bake = terrain_ground_bake(wx,wy) + roof_m * content_scale * HEIGHT_SCALE",
                "units": "roof_m is metres above LOCAL ground; the terrain supplies the datum",
                "grid_alignment": "lattice shares the A3D height-vertex grid, so patch-seam "
                                  "duplicates receive identical values",
                "note": "the field never carries an absolute source datum into the game world",
            },
            "compiled_at": datetime.now(timezone.utc).isoformat(),
            "compiler": "scripts/pipeline/compile_lidar_roof_field.py",
        }
        doc.update(field)

        if args.all:
            out_path = args.out / (source.replace("/", "_") + ".roof_field.json")
        else:
            out_path = args.out
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(doc, indent=2) + "\n")

        regions = doc["roof_regions"]
        tiers = ", ".join(f"{r['elevation_m_above_ground']:.2f}" for r in regions)
        print(f"[roof-field] {source} ({spec.get('inst_name')}): "
              f"{len(doc['cells'])} verts, {len(regions)} regions [{tiers}] m  "
              f"ground {doc['ground']['elevation']:.2f} ({doc['ground']['sample_count']} class-2)")
        measured = sorted(c["raw_m"] for c in doc["cells"] if c["provenance_class"] == PROVENANCE_MEASURED)
        summary_rows.append({
            "source": source,
            "status": "compiled",
            "footprint_area_m2": doc["footprint"]["source_area_m2"],
            "measured_vertices": doc["measured_vertices"],
            "derived_edge_vertices": doc["derived_edge_vertices"],
            "roof_sample_count": doc["roof_samples"]["sample_count"],
            "roof_density_per_m2": doc["roof_samples"]["density_per_m2"],
            "ground_sample_count": doc["ground"]["sample_count"],
            "measured_roof_m": {
                "p10": round(measured[int(0.10 * (len(measured) - 1))], 3),
                "median": round(measured[(len(measured) - 1) // 2], 3),
                "p90": round(measured[int(0.90 * (len(measured) - 1))], 3),
            },
            "roof_regions_m": [r["elevation_m_above_ground"] for r in regions],
        })
        ok += 1

    print(f"[roof-field] compiled {ok}/{len(targets)} buildings -> {args.out}")
    if failed:
        print(f"[roof-field] {len(failed)} skipped:")
        for source, why in failed:
            print(f"[roof-field]   {source}: {why}")
    if args.all:
        # FL-4930: a whole-run compile leaves one machine-readable account of
        # every target, compiled or skipped, instead of only console lines.
        summary = {
            "schema": "asciicker.roof_field_summary/v1",
            "compiler": "scripts/pipeline/compile_lidar_roof_field.py",
            "tiles": tile_name,
            "tile_sha256": tile_hash,
            "horizontal_crs": args.source_crs,
            "vertical_datum": args.vertical_datum,
            "footprints": ([str(p) for p in args.world_ir] if args.world_ir else str(args.building_specs)),
            "scene": scene,
            "targets": len(targets),
            "compiled": ok,
            "skipped": len(failed),
            "rows": summary_rows,
        }
        summary_path = args.out / "roof_field_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
        print(f"[roof-field] summary -> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
