#!/usr/bin/env python3
"""
OSM Visual Proof Contract v1 — FL-3851

Front-door tool that produces a self-documenting artifact proving pixel-to-world/source
ownership for any OSM pipeline run. Launches asciiid in MCP mode, captures a clean
top-down frame, and produces:

  frame.png              — clean 3D render (no UI)
  frame.camera.json      — real render matrices (from C)
  frame.meta.json        — full proof metadata
  frame.terrain_grid.bin — compact per-cell truth (binary)
  frame.composite.png    — 4-panel: A3D | satellite | OSM normal | annotated
  frame.verdicts.json    — pass/fail verdicts

Usage:
  python3 scripts/pipeline/capture_proof.py \\
    --run-root assets/meshes/osm_runs/sbu_visual_fix_20260510 \\
    --preset full-map \\
    --with-satellite \\
    --with-normal-map \\
    --output /tmp/capture_sbu_20260510
"""

import argparse
import re
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Matrix helpers — match the C-side EditorWorldToScreen (asciiid.cpp:5279-5291)
#   tm[0] = +cos(yaw)/rx
#   tm[1] = -sin(yaw)*sin(pitch)/ry
#   tm[2] = 0
#   tm[3] = 0
#   tm[4] = +sin(yaw)/rx
#   tm[5] = +cos(yaw)*sin(pitch)/ry
#   tm[6] = 0
#   tm[7] = 0
#   tm[8] = 0
#   tm[9] = +cos(pitch)*z_scale/ry
#   tm[10] = +2./0xffff
#   tm[11] = 0
#   tm[12] = -(pos_x*tm[0] + pos_y*tm[4] + pos_z*tm[8])
#   tm[13] = -(pos_x*tm[1] + pos_y*tm[5] + pos_z*tm[9])
#   tm[14] = -1.0
#   tm[15] = 1.0
# ---------------------------------------------------------------------------

def mat4_mul_vec4(tm, v):
    """Multiply 4x4 column-major matrix by 4-vector."""
    x = v[0]; y = v[1]; z = v[2]; w = v[3]
    return [
        tm[0]*x + tm[4]*y + tm[8]*z  + tm[12]*w,
        tm[1]*x + tm[5]*y + tm[9]*z  + tm[13]*w,
        tm[2]*x + tm[6]*y + tm[10]*z + tm[14]*w,
        tm[3]*x + tm[7]*y + tm[11]*z + tm[15]*w,
    ]


def world_to_pixel(tm, wx, wy, wz, display_w, display_h):
    """World coordinates to pixel (match C EditorWorldToScreen)."""
    r = mat4_mul_vec4(tm, [wx, wy, wz, 1.0])
    if abs(r[3]) < 1e-12:
        return None
    ndc_x = r[0] / r[3]
    ndc_y = r[1] / r[3]
    px = (ndc_x + 1.0) * 0.5 * display_w
    py = (1.0 - ndc_y) * 0.5 * display_h
    return (px, py)


def pixel_to_world(inv_tm, px, py, display_w, display_h, z=0.0):
    """Pixel to world coordinates at given z plane."""
    ndc_x = px / display_w * 2.0 - 1.0
    ndc_y = 1.0 - py / display_h * 2.0
    # Solve for world x,y using inverse matrix
    r = mat4_mul_vec4(inv_tm, [ndc_x, ndc_y, 0.0, 1.0])
    if abs(r[3]) < 1e-12:
        return None
    wx = r[0] / r[3]
    wy = r[1] / r[3]
    wz = r[2] / r[3]
    return (wx, wy, wz)


def invert_4x4(m):
    """Simple 4x4 matrix inversion (Gauss-Jordan)."""
    # Augment with identity
    a = [list(m[i:i+4]) for i in range(0, 16, 4)]
    for i in range(4):
        a[i] = a[i] + ([1.0 if j == i else 0.0 for j in range(4)])
    # Forward elimination
    for i in range(4):
        pivot = a[i][i]
        if abs(pivot) < 1e-12:
            # Find row with non-zero pivot
            for k in range(i+1, 4):
                if abs(a[k][i]) > 1e-12:
                    a[i], a[k] = a[k], a[i]
                    pivot = a[i][i]
                    break
            else:
                return None
        for j in range(8):
            a[i][j] /= pivot
        for k in range(4):
            if k != i:
                factor = a[k][i]
                for j in range(8):
                    a[k][j] -= factor * a[i][j]
    inv = []
    for i in range(4):
        inv.extend(a[i][4:8])
    return inv


# ---------------------------------------------------------------------------
# asciiid batch helper
# ---------------------------------------------------------------------------

def find_asciiid_binary():
    """Find the asciiid binary."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", ".run", "asciiid"),
        ".run/asciiid",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    raise FileNotFoundError("Cannot find asciiid binary. Build with: make -f makefile_asciiid_mac")


def run_batch(commands, timeout=120):
    """Run asciiid in MCP mode with given commands."""
    binary = find_asciiid_binary()
    stdin_text = "\n".join(commands) + "\n"
    proc = subprocess.run(
        [binary, "--mcp"],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
    )
    stdout = proc.stdout
    stderr = proc.stderr
    # Filter MCP lines
    mcp_lines = [l for l in stdout.split("\n") if "[MCP]" in l]
    return {"stdout": stdout, "stderr": stderr, "mcp": mcp_lines, "returncode": proc.returncode}


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

VERDICT_CHECKS = [
    "capture_contains_ui_pixels",
    "road_fill_coverage_low",
    "road_material_equals_pavement",
    "material_visual_contrast",
    "building_edge_has_grass",
    "building_material_wrong_role",
    "osm_feature_no_painted_cells",
    "topology_diff_suspicious",
    "feature_polygon_missing",
    "fixture_below_terrain",
]


def run_verdicts(features, buildings, building_bake, fixtures, topology,
                 terrain_cells, materials, capture_path, camera, carto_features=None, carto_stamp=None):
    """Run all verdict checks and return results."""
    results = []

    # Normalize buildings/fixtures/topology — they may be lists or dicts
    if isinstance(buildings, list):
        buildings_wrapper = {"buildings": buildings}
    else:
        buildings_wrapper = buildings if buildings else {"buildings": []}

    if isinstance(fixtures, list):
        fixtures_wrapper = {"fixtures": fixtures}
    else:
        fixtures_wrapper = fixtures if fixtures else {"fixtures": []}

    if isinstance(topology, dict):
        topology_data = topology
    elif isinstance(topology, list) and topology:
        topology_data = topology[0]
    else:
        topology_data = None

    # --- capture_contains_ui_pixels ---
    # Defense-in-depth: check known UI regions for ImGui colors
    ui_check = check_capture_ui_pixels(capture_path)
    results.append(ui_check)

    # --- feature_polygon_missing ---
    missing = []
    for f in features.get("features", []):
        if f.get("vertex_count", 0) > 0 and not f.get("world_vertices"):
            missing.append(f["id"])
    results.append({
        "name": "feature_polygon_missing",
        "result": "FAIL" if missing else "PASS",
        "count": len(missing),
        "details": missing[:10] if missing else [],
    })

    # --- road_fill_coverage_low ---
    road_mat_ids = {4}  # road/grey material
    road_features = [f for f in features.get("features", [])
                     if f.get("kind") in ("road", "residential", "secondary", "tertiary",
                                           "primary", "motorway", "trunk", "service")]
    low_fill = check_road_fill_coverage_low(road_features, terrain_cells, road_mat_ids, carto_features, carto_stamp)
    results.append(low_fill)

    # --- road_material_equals_pavement ---
    road_eq_pavement = check_road_material_equals_pavement(features)
    results.append(road_eq_pavement)

    # --- material_visual_contrast ---
    visual_contrast = check_material_visual_contrast(materials)
    results.append(visual_contrast)

    # --- building_edge_has_grass ---
    grass_check = check_building_edge_grass(buildings_wrapper, terrain_cells)
    results.append(grass_check)

    # --- building_material_wrong_role ---
    wrong_role = check_building_material_wrong_role(buildings_wrapper, terrain_cells)
    results.append(wrong_role)

    # --- osm_feature_no_painted_cells ---
    no_cells = check_osm_feature_no_painted_cells(features, terrain_cells, carto_features, carto_stamp)
    results.append(no_cells)

    # --- topology_diff_suspicious ---
    if topology_data:
        topo_check = check_topology_diff_suspicious(topology_data)
        results.append(topo_check)
    else:
        results.append({
            "name": "topology_diff_suspicious",
            "result": "SKIP",
            "reason": "no topology_instance.json",
        })

    # --- fixture_below_terrain ---
    if fixtures_wrapper.get("fixtures"):
        fix_check = check_fixture_below_terrain(fixtures_wrapper)
        results.append(fix_check)
    else:
        results.append({
            "name": "fixture_below_terrain",
            "result": "SKIP",
            "reason": "no fixture_instances.json",
        })

    # Summary
    fails = sum(1 for r in results if r.get("result") == "FAIL")
    warns = sum(1 for r in results if r.get("result") == "WARN")
    passes = sum(1 for r in results if r.get("result") == "PASS")
    skips = sum(1 for r in results if r.get("result") == "SKIP")
    verdict = "FAIL" if fails > 0 else ("WARN" if warns > 0 else "PASS")

    return {
        "verdict": verdict,
        "checks": results,
        "summary": f"{fails} FAIL, {warns} WARN, {passes} PASS, {skips} SKIP",
    }


def check_capture_ui_pixels(capture_path):
    """Check if the capture contains ImGui UI panel colors."""
    # ImGui default panel color is ~0x2B2B2B (dark gray)
    # The left sidebar is ~300px wide, top bar ~20px
    # This is a heuristic — the primary guarantee is the capture point before ImGui
    try:
        from PIL import Image
        img = Image.open(capture_path)
        px = img.load()
        w, h = img.size
        # Check top bar region
        top_ui_pixels = 0
        for x in range(min(100, w)):
            for y in range(min(20, h)):
                r, g, b = px[x, y][:3]
                # ImGui dark theme: near-uniform dark gray
                if abs(r - g) < 15 and abs(g - b) < 15 and r < 60 and r > 20:
                    top_ui_pixels += 1
        top_ui_fraction = top_ui_pixels / max(1, min(100, w) * min(20, h))
        # Check left sidebar region
        left_ui_pixels = 0
        sidebar_w = min(300, w)
        for x in range(sidebar_w):
            for y in range(30, min(200, h)):
                r, g, b = px[x, y][:3]
                if abs(r - g) < 15 and abs(g - b) < 15 and r < 80 and r > 20:
                    left_ui_pixels += 1
        left_ui_fraction = left_ui_pixels / max(1, sidebar_w * min(170, h - 30))

        has_ui = top_ui_fraction > 0.3 or left_ui_fraction > 0.3
        return {
            "name": "capture_contains_ui_pixels",
            "result": "FAIL" if has_ui else "PASS",
            "top_ui_fraction": round(top_ui_fraction, 3),
            "left_ui_fraction": round(left_ui_fraction, 3),
        }
    except ImportError:
        return {
            "name": "capture_contains_ui_pixels",
            "result": "SKIP",
            "reason": "PIL not available",
        }


def _feature_fully_inside_cell_bounds(feature, stamp_summary):
    if not stamp_summary:
        return True
    cell_bounds = stamp_summary.get("cell_bounds")
    world_bounds = feature.get("world_bounds")
    if not cell_bounds or not world_bounds:
        return True
    min_x, min_y, max_x, max_y = cell_bounds
    return (
        world_bounds.get("min_x", min_x) >= min_x and
        world_bounds.get("max_x", max_x) <= max_x and
        world_bounds.get("min_y", min_y) >= min_y and
        world_bounds.get("max_y", max_y) <= max_y
    )


def check_road_fill_coverage_low(road_features, terrain_cells, road_mat_ids, carto_features=None, carto_stamp=None):
    """Check if road features have adequate fill coverage."""
    if carto_features:
        road_like = {
            "road_primary",
            "road_secondary",
            "road_tertiary",
            "road_residential",
        }
        low_fill = []
        skipped_out_of_bounds = 0
        for f in carto_features:
            if f.get("feature_kind") not in road_like:
                continue
            if not _feature_fully_inside_cell_bounds(f, carto_stamp):
                skipped_out_of_bounds += 1
                continue
            if int(f.get("painted_cell_count", 0)) <= 0:
                low_fill.append({
                    "feature_id": f.get("feature_id"),
                    "kind": f.get("feature_kind"),
                    "painted_cell_count": int(f.get("painted_cell_count", 0)),
                    "osm_id": f.get("osm_id"),
                })
        return {
            "name": "road_fill_coverage_low",
            "result": "FAIL" if low_fill else "PASS",
            "count": len(low_fill),
            "source": "carto_features_jsonl",
            "skipped_out_of_bounds": skipped_out_of_bounds,
            "details": low_fill[:10],
        }

    if not road_features:
        return {"name": "road_fill_coverage_low", "result": "PASS", "count": 0}
    low_fill = []
    for rf in road_features:
        bounds = rf.get("cell_bounds", {})
        if not bounds:
            continue
        cx0, cx1 = bounds["min_x"], bounds["max_x"]
        cy0, cy1 = bounds["min_y"], bounds["max_y"]
        total_cells = max(1, (cx1 - cx0) * (cy1 - cy0))
        road_cells = 0
        for c in terrain_cells:
            if cx0 <= c["cx"] <= cx1 and cy0 <= c["cy"] <= cy1:
                if c["mat_id"] in road_mat_ids:
                    road_cells += 1
        coverage = road_cells / total_cells if total_cells > 0 else 0
        if coverage < 0.1:  # threshold: <10% fill is low
            low_fill.append({
                "feature_id": rf.get("id"),
                "kind": rf.get("kind"),
                "coverage": round(coverage, 3),
                "total_cells": total_cells,
                "road_cells": road_cells,
            })
    return {
        "name": "road_fill_coverage_low",
        "result": "FAIL" if low_fill else "PASS",
        "count": len(low_fill),
        "details": low_fill[:10],
    }


def check_road_material_equals_pavement(features):
    """Check if road and pavement features share the same material ID."""
    road_mats = set()
    pave_mats = set()
    for f in features.get("features", []):
        if f.get("kind") in ("road", "residential", "secondary", "tertiary",
                              "primary", "motorway", "trunk", "service"):
            road_mats.add(f.get("material_id"))
        if f.get("kind") in ("parking", "plaza", "footway", "pavement"):
            pave_mats.add(f.get("material_id"))
    shared = road_mats & pave_mats
    return {
        "name": "road_material_equals_pavement",
        "result": "WARN" if shared else "PASS",
        "shared_material_ids": list(shared),
    }


def _material_average_rgb(materials, mat_id, channel="bg"):
    """Average one material's dumped palette channel across all shade cells."""
    if not isinstance(materials, dict):
        return None
    for mat in materials.get("materials", []):
        if mat.get("id") != mat_id:
            continue
        values = []
        for shade in mat.get("shade", []):
            for cell in shade.get("cells", []):
                rgb = cell.get(channel)
                if isinstance(rgb, list) and len(rgb) == 3:
                    values.append(rgb)
        if not values:
            return None
        return [sum(v[i] for v in values) / len(values) for i in range(3)]
    return None


def _rgb_distance(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def check_material_visual_contrast(materials):
    """Verify the actual dumped palette separates OSM visual roles."""
    role_ids = {
        "grass": 1,
        "pavement": 2,
        "land": 3,
        "road": 4,
        "building": 5,
    }
    averages = {role: _material_average_rgb(materials, mat_id) for role, mat_id in role_ids.items()}
    missing = [role for role, rgb in averages.items() if rgb is None]
    if missing:
        return {
            "name": "material_visual_contrast",
            "result": "SKIP",
            "reason": "missing material palette data",
            "missing_roles": missing,
        }

    checks = [
        ("road_vs_pavement", "road", "pavement", 45.0),
        ("pavement_vs_land", "pavement", "land", 30.0),
        ("grass_vs_land", "grass", "land", 55.0),
        ("building_vs_grass", "building", "grass", 45.0),
        ("building_vs_road", "building", "road", 35.0),
    ]
    failures = []
    distances = {}
    for name, left, right, minimum in checks:
        distance = _rgb_distance(averages[left], averages[right])
        distances[name] = round(distance, 2)
        if distance < minimum:
            failures.append({
                "check": name,
                "distance": round(distance, 2),
                "minimum": minimum,
                "left_rgb": [round(v, 1) for v in averages[left]],
                "right_rgb": [round(v, 1) for v in averages[right]],
            })

    building = averages["building"]
    tone_failures = []
    if building[2] > building[0] or building[2] > building[1]:
        tone_failures.append("building_blue_or_purple_bias")
    if building[0] - building[1] > 40:
        tone_failures.append("building_too_red")
    if tone_failures:
        failures.append({
            "check": "building_warm_beige_tone",
            "failures": tone_failures,
            "building_rgb": [round(v, 1) for v in building],
        })

    return {
        "name": "material_visual_contrast",
        "result": "FAIL" if failures else "PASS",
        "averages": {role: [round(v, 1) for v in rgb] for role, rgb in averages.items()},
        "distances": distances,
        "failures": failures,
    }


def check_building_edge_grass(buildings, terrain_cells):
    """Check for grass (mat_id==1) cells on building perimeters."""
    if not buildings or not terrain_cells:
        return {"name": "building_edge_has_grass", "result": "SKIP", "reason": "no data"}
    grass_matches = []
    GRASS_MAT = 1
    for b in buildings.get("buildings", []):
        bake_bbox = b.get("bake_bbox", {})
        if not bake_bbox:
            continue
        bx0, bx1 = bake_bbox.get("min_x", 0), bake_bbox.get("max_x", 0)
        by0, by1 = bake_bbox.get("min_y", 0), bake_bbox.get("max_y", 0)
        # Check perimeter cells (edges of the bake bounding box)
        edge_cells = []
        for c in terrain_cells:
            cx, cy = c["cx"], c["cy"]
            on_edge = ((cx == bx0 or cx == bx1 - 1) and by0 <= cy < by1) or \
                      ((cy == by0 or cy == by1 - 1) and bx0 <= cx < bx1)
            if on_edge and c["mat_id"] == GRASS_MAT:
                edge_cells.append({"cx": cx, "cy": cy})
        if edge_cells:
            grass_matches.append({
                "building": b.get("name", b.get("mesh", "unknown")),
                "cells": edge_cells[:20],
            })
    return {
        "name": "building_edge_has_grass",
        "result": "FAIL" if grass_matches else "PASS",
        "count": len(grass_matches),
        "details": grass_matches[:10],
    }


def check_building_material_wrong_role(buildings, terrain_cells):
    """Check if building cells have wrong material ID."""
    if not buildings or not terrain_cells:
        return {"name": "building_material_wrong_role", "result": "SKIP", "reason": "no data"}
    mismatches = []
    for b in buildings.get("buildings", []):
        expected_mat = b.get("material_id")
        if expected_mat is None:
            continue
        bake_bbox = b.get("bake_bbox", {})
        if not bake_bbox:
            continue
        bx0, bx1 = bake_bbox.get("min_x", 0), bake_bbox.get("max_x", 0)
        by0, by1 = bake_bbox.get("min_y", 0), bake_bbox.get("max_y", 0)
        wrong = []
        for c in terrain_cells:
            if bx0 <= c["cx"] < bx1 and by0 <= c["cy"] < by1:
                if c["mat_id"] != expected_mat:
                    wrong.append({"cx": c["cx"], "cy": c["cy"],
                                   "expected": expected_mat, "got": c["mat_id"]})
        if wrong:
            mismatches.append({
                "building": b.get("name", b.get("mesh", "unknown")),
                "wrong_cells": wrong[:50],
                "total_wrong": len(wrong),
            })
    return {
        "name": "building_material_wrong_role",
        "result": "FAIL" if mismatches else "PASS",
        "count": len(mismatches),
        "details": mismatches[:10],
    }


def check_osm_feature_no_painted_cells(features, terrain_cells, carto_features=None, carto_stamp=None):
    """Check if any OSM features have zero painted cells in their bounds."""
    if carto_features:
        missing = []
        occluded = []
        skipped_out_of_bounds = 0
        for f in carto_features:
            if not _feature_fully_inside_cell_bounds(f, carto_stamp):
                skipped_out_of_bounds += 1
                continue
            if int(f.get("painted_cell_count", 0)) <= 0:
                detail = {
                    "feature_id": f.get("feature_id"),
                    "kind": f.get("feature_kind"),
                    "material_role": f.get("material_role"),
                    "osm_id": f.get("osm_id"),
                }
                if _carto_feature_occluded_by_later_paint(f, carto_features):
                    occluded.append(detail)
                else:
                    missing.append(detail)
        return {
            "name": "osm_feature_no_painted_cells",
            # In the Carto owner raster, a zero-count feature is valid when a
            # later z-order feature fully covers it. Report those as provenance;
            # warn only when no later painted owner accounts for the zero cells.
            "result": "WARN" if missing else "PASS",
            "count": len(missing),
            "occluded_count": len(occluded),
            "source": "carto_features_jsonl",
            "skipped_out_of_bounds": skipped_out_of_bounds,
            "details": missing[:20],
            "occluded_details": occluded[:20],
        }

    if not features or not terrain_cells:
        return {"name": "osm_feature_no_painted_cells", "result": "SKIP", "reason": "no data"}
    empty = []
    for f in features.get("features", []):
        mat_id = f.get("material_id")
        bounds = f.get("cell_bounds", {})
        if mat_id is None or not bounds:
            continue
        cx0, cx1 = bounds["min_x"], bounds["max_x"]
        cy0, cy1 = bounds["min_y"], bounds["max_y"]
        found = False
        for c in terrain_cells:
            if cx0 <= c["cx"] <= cx1 and cy0 <= c["cy"] <= cy1:
                if c["mat_id"] == mat_id:
                    found = True
                    break
        if not found:
            empty.append({
                "feature_id": f.get("id"),
                "kind": f.get("kind"),
                "material_id": mat_id,
            })
    return {
        "name": "osm_feature_no_painted_cells",
        "result": "FAIL" if empty else "PASS",
        "count": len(empty),
        "details": empty[:20],
    }


def _carto_feature_occluded_by_later_paint(feature, all_features):
    bounds = _feature_bounds_tuple(feature)
    if not bounds:
        return False
    z_order = int(feature.get("z_order") or 0)
    for other in all_features:
        if other is feature:
            continue
        if int(other.get("painted_cell_count", 0)) <= 0:
            continue
        other_z_order = int(other.get("z_order") or 0)
        same_role_same_layer = (
            other_z_order == z_order
            and other.get("material_role") == feature.get("material_role")
        )
        if other_z_order <= z_order and not same_role_same_layer:
            continue
        if _bounds_intersect(bounds, _feature_bounds_tuple(other)):
            return True
    return False


def check_topology_diff_suspicious(topology):
    """Check if topology mesh changed suspiciously few cells."""
    expected = topology.get("expected_cells_changed")
    actual = topology.get("cells_changed")
    pipeline_verdict = topology.get("verdict")
    if expected is None or actual is None:
        return {
            "name": "topology_diff_suspicious",
            "result": "SKIP",
            "reason": "missing expected_cells_changed or cells_changed",
        }
    if pipeline_verdict == "OK":
        suspicious = False
    elif pipeline_verdict in {"WARN", "FAIL"}:
        suspicious = True
    else:
        suspicious = actual < expected * 0.5
    return {
        "name": "topology_diff_suspicious",
        "result": "WARN" if suspicious else "PASS",
        "expected_cells_changed": expected,
        "cells_changed": actual,
        "total_cells": topology.get("total_cells"),
        "changed_pct": topology.get("changed_pct"),
        "source": topology.get("source"),
        "pipeline_verdict": pipeline_verdict,
        "low_change_ok_reason": topology.get("low_change_ok_reason"),
    }


def check_fixture_below_terrain(fixtures):
    """Check if any fixture is below terrain at its position."""
    if not fixtures:
        return {"name": "fixture_below_terrain", "result": "SKIP"}
    below = []
    for fix in fixtures.get("fixtures", []):
        world_z = fix.get("world_z")
        terrain_z = fix.get("terrain_z")
        if world_z is not None and terrain_z is not None and world_z < terrain_z:
            below.append({
                "name": fix.get("name"),
                "world_z": world_z,
                "terrain_z": terrain_z,
            })
    return {
        "name": "fixture_below_terrain",
        "result": "WARN" if below else "PASS",
        "count": len(below),
        "details": below[:10],
    }


# ---------------------------------------------------------------------------
# Composite image
# ---------------------------------------------------------------------------

def _camera_world_bounds(camera):
    tm = camera.get("tm", [])
    display_w, display_h = camera.get("display_size", [0, 0])
    if not tm or not display_w or not display_h:
        return None
    inv_tm = invert_4x4(tm)
    if not inv_tm:
        return None
    corners = []
    for px, py in ((0, 0), (display_w, 0), (display_w, display_h), (0, display_h)):
        wc = pixel_to_world(inv_tm, px, py, display_w, display_h, z=0)
        if wc:
            corners.append(wc)
    if not corners:
        return None
    return (
        min(p[0] for p in corners),
        min(p[1] for p in corners),
        max(p[0] for p in corners),
        max(p[1] for p in corners),
    )


def _bounds_intersect(a, b):
    if not a or not b:
        return False
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 <= bx1 and ax1 >= bx0 and ay0 <= by1 and ay1 >= by0


def _feature_bounds_tuple(feature):
    bounds = feature.get("world_bounds") or {}
    if not bounds:
        return None
    return (
        float(bounds.get("min_x", 0.0)),
        float(bounds.get("min_y", 0.0)),
        float(bounds.get("max_x", 0.0)),
        float(bounds.get("max_y", 0.0)),
    )


def _pixel_bounds(points):
    if not points:
        return None
    return {
        "min_x": round(min(p[0] for p in points), 2),
        "min_y": round(min(p[1] for p in points), 2),
        "max_x": round(max(p[0] for p in points), 2),
        "max_y": round(max(p[1] for p in points), 2),
    }


def _project_world_vertices(vertices, camera, *, z=0.0, max_vertices=256):
    tm = camera.get("tm", [])
    display_w, display_h = camera.get("display_size", [1600, 900])
    if len(tm) != 16 or not vertices:
        return []
    projected = []
    for point in vertices[:max_vertices]:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        pix = world_to_pixel(tm, float(point[0]), float(point[1]), z, display_w, display_h)
        if pix:
            projected.append([round(pix[0], 2), round(pix[1], 2)])
    return projected


def build_visible_feature_metadata(features, carto_features, camera):
    """Project visible OSM/Carto features into capture pixel space."""
    visible = []
    world_bounds = _camera_world_bounds(camera)
    if not camera or not world_bounds:
        return visible

    def add_feature(feature, *, source, id_key, kind_key, role_key=None):
        bounds = _feature_bounds_tuple(feature)
        if not _bounds_intersect(bounds, world_bounds):
            return
        vertices = feature.get("world_vertices") or []
        pixel_vertices = _project_world_vertices(vertices, camera)
        pixel_bounds = _pixel_bounds(pixel_vertices)
        if not pixel_bounds and bounds:
            corners = [
                [bounds[0], bounds[1]],
                [bounds[2], bounds[1]],
                [bounds[2], bounds[3]],
                [bounds[0], bounds[3]],
            ]
            pixel_bounds = _pixel_bounds(_project_world_vertices(corners, camera))
        visible.append({
            "source": source,
            "id": feature.get(id_key),
            "kind": feature.get(kind_key),
            "material_role": feature.get(role_key) if role_key else feature.get("material_name"),
            "material_id": feature.get("material_id"),
            "osm_id": feature.get("osm_id"),
            "osm_type": feature.get("osm_type"),
            "osm_tags": feature.get("source_tags") or feature.get("osm_tags") or {},
            "painted_cell_count": feature.get("painted_cell_count"),
            "z_order": feature.get("z_order"),
            "world_bounds": feature.get("world_bounds"),
            "world_vertices": vertices[:256],
            "pixel_bounds": pixel_bounds,
            "pixel_vertices": pixel_vertices,
            "latlon_centroid": feature.get("latlon_centroid"),
            "latlon_bounds": feature.get("latlon_bounds"),
        })

    for feature in features.get("features", []) if isinstance(features, dict) else []:
        add_feature(feature, source="features_json", id_key="id", kind_key="kind")
    for feature in carto_features or []:
        add_feature(
            feature,
            source="carto_features_jsonl",
            id_key="feature_id",
            kind_key="feature_kind",
            role_key="material_role",
        )
    visible.sort(key=lambda f: (str(f.get("source")), int(f.get("z_order") or 0), str(f.get("id"))))
    return visible


def _crop_carto_reference(source_path, stamp_summary, camera, target_size):
    """Crop a run-local Carto reference image to the captured world bbox."""
    from PIL import Image

    img = Image.open(source_path).convert("RGB")
    cell_bounds = stamp_summary.get("cell_bounds")
    world_bounds = _camera_world_bounds(camera)
    if not cell_bounds or not world_bounds:
        return img.resize(target_size, Image.LANCZOS)

    min_x, min_y, _, _ = cell_bounds
    bx0, by0, bx1, by1 = world_bounds
    ix0 = max(0, min(img.width, int(math.floor(bx0 - min_x))))
    iy0 = max(0, min(img.height, int(math.floor(by0 - min_y))))
    ix1 = max(0, min(img.width, int(math.ceil(bx1 - min_x))))
    iy1 = max(0, min(img.height, int(math.ceil(by1 - min_y))))
    if ix1 <= ix0 or iy1 <= iy0:
        return img.resize(target_size, Image.LANCZOS)
    return img.crop((ix0, iy0, ix1, iy1)).resize(target_size, Image.LANCZOS)


def create_composite(frame_path, output_path, satellite_path=None, osm_path=None, *, camera=None, stamp_summary=None, labels_path=None):
    """Create 4-panel composite: A3D | optional satellite | OSM | annotated."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[capture_proof] PIL not available — skipping composite image")
        return False

    frame = Image.open(frame_path)
    w, h = frame.size

    # 2x2 grid
    canvas_w = w * 2
    canvas_h = h * 2
    canvas = Image.new("RGB", (canvas_w, canvas_h), (30, 30, 30))

    # Panel 1: A3D clean render (top-left)
    canvas.paste(frame, (0, 0))

    # Panel 2: Satellite (top-right) — explicitly mark when not requested.
    satellite_label = "Satellite"
    if satellite_path and os.path.isfile(satellite_path):
        sat = Image.open(satellite_path).resize((w, h), Image.LANCZOS)
        canvas.paste(sat, (w, 0))
    else:
        placeholder = Image.new("RGB", (w, h), (50, 50, 60))
        draw = ImageDraw.Draw(placeholder)
        satellite_label = "Satellite Not Requested"
        draw.text((w//2 - 90, h//2 - 10), satellite_label, fill=(180, 180, 180))
        canvas.paste(placeholder, (w, 0))

    # Panel 3: OSM normal map (bottom-left) — placeholder if not available
    if osm_path and os.path.isfile(osm_path):
        if camera and stamp_summary:
            osm_img = _crop_carto_reference(osm_path, stamp_summary, camera, (w, h))
        else:
            osm_img = Image.open(osm_path).resize((w, h), Image.LANCZOS)
        canvas.paste(osm_img, (0, h))
    else:
        placeholder = Image.new("RGB", (w, h), (50, 50, 60))
        draw = ImageDraw.Draw(placeholder)
        draw.text((w//2 - 60, h//2 - 10), "No OSM Tile", fill=(180, 180, 180))
        canvas.paste(placeholder, (0, h))

    # Panel 4: Annotated overlay (bottom-right) — prefer the run-local label proof.
    if labels_path and os.path.isfile(labels_path):
        if camera and stamp_summary:
            annotated = _crop_carto_reference(labels_path, stamp_summary, camera, (w, h))
        else:
            annotated = Image.open(labels_path).convert("RGB").resize((w, h), Image.LANCZOS)
    else:
        annotated = frame.copy()
    canvas.paste(annotated, (w, h))

    # Add labels
    draw = ImageDraw.Draw(canvas)
    labels = ["A3D Clean Render", satellite_label, "OSM Normal Map", "Annotated"]
    positions = [(10, 10), (w + 10, 10), (10, h + 10), (w + 10, h + 10)]
    for label, pos in zip(labels, positions):
        draw.rectangle([pos[0], pos[1], pos[0] + len(label) * 8 + 20, pos[1] + 30],
                        fill=(0, 0, 0, 128))
        draw.text((pos[0] + 5, pos[1] + 5), label, fill=(255, 255, 255))

    canvas.save(output_path)
    print(f"[capture_proof] Composite saved: {output_path}")
    return True


# ---------------------------------------------------------------------------
# Main capture flow
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="OSM Visual Proof Capture v1")
    parser.add_argument("--run-root", required=True,
                        help="Path to OSM run directory (e.g. assets/meshes/osm_runs/sbu_visual_fix_20260510)")
    parser.add_argument("--map", help="A3D map path (auto-discovered from run-root)")
    parser.add_argument("--preset", default="full-map",
                        choices=["full-map", "bbox"],
                        help="View preset (default: full-map)")
    parser.add_argument("--bbox", nargs=4, type=float,
                        help="Bounding box: min_x min_y max_x max_y (for --preset bbox)")
    parser.add_argument("--with-satellite", action="store_true",
                        help="Include satellite tile in composite")
    parser.add_argument("--with-normal-map", action="store_true",
                        help="Include OSM normal map tile in composite")
    parser.add_argument("--exact-terrain", action="store_true",
                        help="Disable asciiid overview LOD for focused visual signoff captures")
    parser.add_argument("--output", required=True,
                        help="Output directory")
    return parser.parse_args()


def auto_discover(run_root):
    """Discover files in run root by convention."""
    run_root = Path(run_root)
    discovered = {}

    # The proof target is the final map users open, not the staged handoff.
    for name in ["output.a3d", "output_staged.a3d", "output_terrain_only.a3d"]:
        candidate = run_root / name
        if candidate.exists():
            discovered["map"] = str(candidate)
            break

    # Metadata
    candidate = run_root / "terrain_metadata.json"
    if candidate.exists():
        discovered["metadata"] = str(candidate)

    # Features
    candidate = run_root / "output_terrain_only.a3d.features.json"
    if candidate.exists():
        discovered["features"] = str(candidate)

    # Buildings
    candidate = run_root / "building_instances.json"
    if candidate.exists():
        discovered["buildings"] = str(candidate)

    # Building bake summary
    candidate = run_root / "building_bake_summary.json"
    if candidate.exists():
        discovered["building_bake"] = str(candidate)

    # Fixtures
    candidate = run_root / "fixture_instances.json"
    if candidate.exists():
        discovered["fixtures"] = str(candidate)

    # Topology
    candidate = run_root / "topology_instance.json"
    if candidate.exists():
        discovered["topology"] = str(candidate)

    candidate = run_root / "run_trace.jsonl"
    if candidate.exists():
        discovered["run_trace"] = str(candidate)

    # OSM Carto stamp sidecars. These are the run-local normal-map/reference
    # truth produced by osm_carto_stamper.py.
    for candidate in [
        run_root / "output_terrain_only.a3d.carto_stamp.json",
        run_root / "output.a3d.carto_stamp.json",
    ]:
        if candidate.exists():
            discovered["carto_stamp"] = str(candidate)
            try:
                stamp = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                stamp = {}
            for key in (
                "carto_source_png",
                "carto_classified_png",
                "carto_features_jsonl",
                "carto_cells_bin",
                "carto_roles_json",
                "carto_topology_json",
                "carto_labels_png",
            ):
                value = stamp.get(key)
                if value and Path(value).exists():
                    discovered[key] = value
            break

    return discovered


def build_terrain_cells_from_grid(mcp_output, materials, camera):
    """Parse QUERY_TERRAIN_GRID output and build terrain cell list."""
    cells = []
    in_grid = False
    gw, gh = 0, 0
    cx, cy, scale = 0, 0, 1
    gy = 0

    # Material role lookup from materials
    mat_roles = {}
    if materials:
        for m in materials.get("materials", []):
            mid = m.get("id")
            # Heuristic: guess role from RGB values
            shade0 = m.get("shade", [])
            if shade0:
                cells0 = shade0[0].get("cells", [])
                if cells0:
                    c = cells0[0]
                    fg = c.get("fg", [0, 0, 0])
                    mat_roles[mid] = _guess_material_role(fg)

    for line in mcp_output:
        if line.startswith("[TERRAIN_GRID_START]"):
            in_grid = True
            parts = line.split()
            for p in parts:
                if p.startswith("w="):
                    gw = int(p.split("=")[1])
                elif p.startswith("h="):
                    gh = int(p.split("=")[1])
                elif p.startswith("cx="):
                    cx = float(p.split("=")[1])
                elif p.startswith("cy="):
                    cy = float(p.split("=")[1])
                elif p.startswith("scale="):
                    scale = float(p.split("=")[1])
            gy = 0
            continue
        if line.startswith("[TERRAIN_GRID_END]"):
            in_grid = False
            continue
        if in_grid:
            tokens = line.strip().split()
            for gx, tok in enumerate(tokens):
                if "," in tok:
                    parts = tok.split(",")
                    mat_id = int(parts[0]) if parts[0] != "-1" else -1
                    height = int(parts[1]) if len(parts) > 1 else 0
                    wx = cx + (gx - gw / 2) * scale
                    wy = cy + (gy - gh / 2) * scale
                    cells.append({
                        "cx": int(wx),
                        "cy": int(wy),
                        "mat_id": mat_id,
                        "height": height,
                        "material_role": mat_roles.get(mat_id, f"mat_{mat_id}"),
                    })
            gy += 1
    return cells


def _guess_material_role(fg_rgb):
    """Guess material role from foreground RGB."""
    r, g, b = fg_rgb
    if g > r and g > b and g > 80:
        return "grass"
    if r > 180 and g > 120 and g < 180 and b < 120:
        return "building/brick"
    if abs(r - g) < 30 and abs(g - b) < 30 and r < 160 and r > 80:
        return "road/grey"
    if r > g and g > b and r > 100:
        return "pavement/sand"
    if b > r and b > g:
        return "water"
    return "unknown"


def compute_osm_tile_url(lat, lon, zoom=17):
    """Compute OSM tile URL for given lat/lon."""
    return f"https://tile.openstreetmap.org/{zoom}/{_lon_to_tile_x(lon, zoom)}/{_lat_to_tile_y(lat, zoom)}.png"


def _lon_to_tile_x(lon, zoom):
    n = 2 ** zoom
    return int((lon + 180.0) / 360.0 * n)


def _lat_to_tile_y(lat, zoom):
    import math
    n = 2 ** zoom
    lat_rad = lat * math.pi / 180.0
    return int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)


def compute_google_maps_url(camera, features):
    """Emit Google Maps URL for manual comparison."""
    osm = camera.get("osm", {})
    if not osm.get("valid"):
        return None
    lat = osm.get("scene_lat", 0)
    lon = osm.get("scene_lon", 0)
    zoom = 17  # reasonable default
    return f"https://www.google.com/maps/@{lat:.7f},{lon:.7f},{zoom}z"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Discover files
    discovered = auto_discover(args.run_root)
    map_path = args.map or discovered.get("map")
    if not map_path:
        print(f"[capture_proof] ERROR: No A3D map found in {args.run_root}", file=sys.stderr)
        sys.exit(1)

    print(f"[capture_proof] Map: {map_path}")
    for k, v in discovered.items():
        if k != "map":
            print(f"[capture_proof]   {k}: {v}")

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Prepare batch commands
    commands = []
    commands.append(f"LOAD_MAP {map_path}")

    if args.preset == "full-map":
        commands.append("SET_TOPDOWN_VIEW FULL")
    elif args.preset == "bbox":
        if not args.bbox or len(args.bbox) != 4:
            print("[capture_proof] ERROR: --preset bbox requires --bbox min_x min_y max_x max_y", file=sys.stderr)
            sys.exit(1)
        commands.append(f"SET_TOPDOWN_VIEW BBOX {args.bbox[0]} {args.bbox[1]} {args.bbox[2]} {args.bbox[3]}")

    # Proof captures must show the rendered map, not the editor patch/visual-cell
    # grid overlay. This process exits after capture, so no restore is needed.
    commands.append("SET_GRID 0")
    if args.exact_terrain:
        commands.append("SET_TERRAIN_OVERVIEW 0")
    commands.append("DUMP_MATERIAL_TABLE 0 1 2 3 4 5")
    commands.append(f"CAPTURE_CLEAN_FRAME_AND_QUIT {args.output}")

    print(f"[capture_proof] Running asciiid batch with {len(commands)} commands...")
    result = run_batch(commands, timeout=120)

    if result["returncode"] != 0:
        print(f"[capture_proof] WARNING: asciiid exit code {result['returncode']}")

    # Print MCP output
    for line in result["mcp"]:
        print(f"  {line}")

    # Load camera.json
    camera_path = os.path.join(args.output, "frame.camera.json")
    camera = {}
    if os.path.isfile(camera_path):
        with open(camera_path) as f:
            camera = json.load(f)
        print(f"[capture_proof] Camera loaded: pos=({camera.get('pos',[0,0,0])})")
    else:
        print("[capture_proof] WARNING: frame.camera.json not found")

    frame_png = os.path.join(args.output, "frame.png")
    if not os.path.isfile(frame_png):
        verdicts = {
            "verdict": "INFRA_FAIL",
            "summary": "asciiid capture did not produce frame.png",
            "checks": [
                {
                    "name": "capture_frame_exists",
                    "result": "FAIL",
                    "severity": "FAIL",
                    "details": {
                        "path": frame_png,
                        "asciiid_returncode": result["returncode"],
                    },
                }
            ],
        }
        verdicts_path = os.path.join(args.output, "frame.verdicts.json")
        with open(verdicts_path, "w") as f:
            json.dump(verdicts, f, indent=2)
        print(f"[capture_proof] ERROR: {verdicts['summary']}: {frame_png}", file=sys.stderr)
        print(f"[capture_proof] Verdicts saved: {verdicts_path}")
        return 2

    # Parse DUMP_MATERIAL_TABLE from MCP output
    materials = None
    # Look for the JSON block between DUMP_MATERIAL_TABLE command and next command
    in_json = False
    json_lines = []
    brace_depth = 0
    for line in result["stdout"].split("\n"):
        if "Received command: DUMP_MATERIAL_TABLE" in line:
            in_json = True
            json_lines = []
            brace_depth = 0
            continue
        if in_json:
            if line.strip().startswith("[") and "Received command:" in line:
                in_json = False
                break
            # Count braces to find end of JSON object
            for ch in line:
                if ch == '{':
                    brace_depth += 1
                elif ch == '}':
                    brace_depth -= 1
            json_lines.append(line)
            if brace_depth == 0 and len(json_lines) > 1:
                break
    if json_lines:
        json_text = "\n".join(json_lines)
        try:
            materials = json.loads(json_text)
            print(f"[capture_proof] Materials: {len(materials.get('materials', []))} entries")
        except json.JSONDecodeError as e:
            sanitized = []
            for line in json_lines:
                if '"glyph_char":' in line and ', "flags"' in line:
                    line = re.sub(r'"glyph_char": ".*", "flags"', '"glyph_char": "", "flags"', line)
                sanitized.append(line)
            try:
                materials = json.loads("\n".join(sanitized))
                print(f"[capture_proof] Materials: {len(materials.get('materials', []))} entries (glyph chars sanitized)")
            except json.JSONDecodeError:
                print(f"[capture_proof] WARNING: Failed to parse material JSON: {e}")

    # Load features
    features = {}
    features_path = discovered.get("features")
    if features_path and os.path.isfile(features_path):
        with open(features_path) as f:
            features = json.load(f)
        print(f"[capture_proof] Features: {features.get('feature_count', 0)} entries")

    # Load run-local Carto proof sidecars when available. These sidecars encode
    # the semantic OSM reference map and per-feature painted-cell counts from
    # the same stamper that owns final surface paint.
    carto_stamp = {}
    carto_features = []
    carto_stamp_path = discovered.get("carto_stamp")
    if carto_stamp_path and os.path.isfile(carto_stamp_path):
        with open(carto_stamp_path, encoding="utf-8") as f:
            carto_stamp = json.load(f)
        print(f"[capture_proof] Carto stamp: {carto_stamp_path}")
    carto_features_path = discovered.get("carto_features_jsonl")
    if carto_features_path and os.path.isfile(carto_features_path):
        with open(carto_features_path, encoding="utf-8") as f:
            carto_features = [json.loads(line) for line in f if line.strip()]
        print(f"[capture_proof] Carto features: {len(carto_features)} entries")

    # Load buildings
    buildings = []
    buildings_path = discovered.get("buildings")
    if buildings_path and os.path.isfile(buildings_path):
        with open(buildings_path) as f:
            buildings_data = json.load(f)
        if isinstance(buildings_data, list):
            buildings = buildings_data
        else:
            buildings = buildings_data.get("buildings", [])
        print(f"[capture_proof] Buildings: {len(buildings)} entries")

    # Load building bake summary
    building_bake = {}
    bake_path = discovered.get("building_bake")
    if bake_path and os.path.isfile(bake_path):
        with open(bake_path) as f:
            building_bake = json.load(f)

    # Load fixtures
    fixtures = []
    fixtures_path = discovered.get("fixtures")
    if fixtures_path and os.path.isfile(fixtures_path):
        with open(fixtures_path) as f:
            fixtures_data = json.load(f)
        if isinstance(fixtures_data, list):
            fixtures = fixtures_data
        else:
            fixtures = fixtures_data.get("fixtures", [])

    # Load topology
    topology = {}
    topology_path = discovered.get("topology")
    if topology_path and os.path.isfile(topology_path):
        with open(topology_path) as f:
            topology_data = json.load(f)
        if isinstance(topology_data, list):
            topology = topology_data[0] if topology_data else {}
        else:
            topology = topology_data

    run_trace_path = discovered.get("run_trace")
    if run_trace_path and os.path.isfile(run_trace_path):
        topology_diff = None
        with open(run_trace_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "terrain_diff" and event.get("label") == "topology-bake":
                    topology_diff = event
        if topology_diff:
            topology = dict(topology or {})
            topology["cells_changed"] = topology_diff.get("changed")
            topology["total_cells"] = topology_diff.get("total")
            topology["changed_pct"] = topology_diff.get("pct")
            topology["verdict"] = topology_diff.get("verdict")
            topology["low_change_ok_reason"] = topology_diff.get("low_change_ok_reason")
            topology["source"] = run_trace_path
            # The E2E runner treats this as suspicious below roughly half a percent.
            topology["expected_cells_changed"] = max(1, int(float(topology_diff.get("total", 0)) * 0.005))

    # Query terrain grid (if camera is available)
    terrain_cells = []
    if camera:
        # Compute world bounds from camera
        pos = camera.get("pos", [0, 0, 0])
        tm = camera.get("tm", [0]*16)
        display_w, display_h = camera.get("display_size", [1600, 900])
        inv_tm = invert_4x4(tm)

        if inv_tm:
            # Get world coords at corners of the viewport
            corners_px = [(0, 0), (display_w, 0), (display_w, display_h), (0, display_h)]
            world_corners = []
            for px, py in corners_px:
                wc = pixel_to_world(inv_tm, px, py, display_w, display_h, z=0)
                if wc:
                    world_corners.append(wc)

            if world_corners:
                min_wx = min(w[0] for w in world_corners)
                max_wx = max(w[0] for w in world_corners)
                min_wy = min(w[1] for w in world_corners)
                max_wy = max(w[1] for w in world_corners)
                cx = (min_wx + max_wx) / 2
                cy = (min_wy + max_wy) / 2
                span = max(max_wx - min_wx, max_wy - min_wy)
                scale = max(1.0, span / 64)  # 64x64 grid
                gw = gh = 64

                # Query terrain grid
                grid_commands = [f"LOAD_MAP {map_path}",
                                 f"QUERY_TERRAIN_GRID {cx:.1f} {cy:.1f} {gw} {gh} {scale:.1f}",
                                 "QUIT"]
                print(f"[capture_proof] Querying terrain grid {gw}x{gh} at ({cx:.1f},{cy:.1f}) scale={scale:.1f}...")
                grid_result = run_batch(grid_commands, timeout=60)
                terrain_cells = build_terrain_cells_from_grid(
                    grid_result["stdout"].split("\n"), materials, camera)
                print(f"[capture_proof] Terrain cells: {len(terrain_cells)}")
        else:
            print("[capture_proof] WARNING: Could not invert matrix")

    # Run verdicts
    print("[capture_proof] Running verdicts...")
    verdicts = run_verdicts(features, buildings, building_bake, fixtures,
                            topology, terrain_cells, materials,
                            frame_png, camera, carto_features, carto_stamp)
    print(f"[capture_proof] Verdict: {verdicts['verdict']} ({verdicts['summary']})")

    # Write verdicts
    verdicts_path = os.path.join(args.output, "frame.verdicts.json")
    with open(verdicts_path, "w") as f:
        json.dump(verdicts, f, indent=2)
    print(f"[capture_proof] Verdicts saved: {verdicts_path}")

    # Write meta.json
    meta = {
        "tool": "capture_proof.py",
        "version": "1.0",
        "fl_ticket": "FL-3851",
        "run_root": str(Path(args.run_root).resolve()),
        "camera": camera,
        "projection": {
            "tm": camera.get("tm", []),
            "display_size": camera.get("display_size", []),
            "viewport_px": camera.get("viewport_px", []),
            "dpi_scale": camera.get("dpi_scale", []),
        },
        "google_maps_url": compute_google_maps_url(camera, features),
        "verdicts": verdicts,
        "material_legend": features.get("_material_legend", {}),
        "material_palette": materials or {},
        "visible_features": build_visible_feature_metadata(features, carto_features, camera),
        "features_count": features.get("feature_count", len(features.get("features", []))),
        "buildings_count": len(buildings) if isinstance(buildings, list) else len(buildings.get("buildings", [])),
        "terrain_cells_count": len(terrain_cells),
        "carto_stamp": carto_stamp,
        "carto_features_count": len(carto_features),
    }
    meta_path = os.path.join(args.output, "frame.meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"[capture_proof] Meta saved: {meta_path}")

    # Write terrain grid (binary)
    if terrain_cells:
        grid_bin_path = os.path.join(args.output, "frame.terrain_grid.bin")
        with open(grid_bin_path, "wb") as f:
            for c in terrain_cells:
                f.write(struct.pack("<hh", c["cx"], c["cy"]))
                f.write(struct.pack("<BBBB", c["mat_id"] & 0xFF,
                                     (c["height"] >> 8) & 0xFF,
                                     c["height"] & 0xFF, 0))
        print(f"[capture_proof] Terrain grid binary: {grid_bin_path}")

    # Create composite image
    if os.path.isfile(frame_png):
        composite_path = os.path.join(args.output, "frame.composite.png")
        create_composite(
            frame_png,
            composite_path,
            osm_path=discovered.get("carto_source_png") if args.with_normal_map else None,
            camera=camera,
            stamp_summary=carto_stamp,
            labels_path=discovered.get("carto_labels_png"),
        )

    # Print verdict summary
    print(f"\n[capture_proof] VERDICT: {verdicts['verdict']}")
    for check in verdicts.get("checks", []):
        name = check.get("name", "?")
        result = check.get("result", "?")
        count = check.get("count", "-")
        marker = "✓" if result == "PASS" else ("⚠" if result == "WARN" else "✗")
        print(f"  {marker} {name}: {result}" + (f" ({count})" if count and count != "-" else ""))

    return 0 if verdicts["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
