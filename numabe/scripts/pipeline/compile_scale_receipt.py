#!/usr/bin/env python3
"""Compile a real player-scale calibration receipt for the direct backend.

FL-4815 owns compilation.  This replaces the hardcoded
``scale_receipt_passed: False`` in ``scripts/sbu_e2e_run.py``: every check
below compares quantities from INDEPENDENT sources, so a wrong world-units
scale fails at least one leg.  No check compares WORLD_UNITS_PER_METRE to
itself.

Legs (independent source in parentheses):
1. route_length_agreement (Valhalla pedestrian route vs compiled corridor).
2. avatar_corridor_plausibility (anthropometric bounds + compiled blockage).
3. vertical_range_agreement (GSI DEM5A source range vs compiled bake range).
4. anchor_metrology (OSM anchor EPSG32654 chords vs y9_world chords).
5. gravity_level_record (accepted FL-4812 gravity alignment, orientation only).
6. projected_proportion (built .a3d instances + .akm mesh extents + sprite
   sheet parse, checked against the quoted content scale; recorded under the
   top-level "projected_proportion" key, folded into overall pass).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402

SCHEMA = "y92.scale_receipt.v1"

# Tolerance rationale, all quoted from frozen evidence, none tuned to pass:
# - Valhalla reports integer metres; allow 2 m or 1 %, whichever is larger.
ROUTE_LENGTH_ABS_TOL_M = 2.0
ROUTE_LENGTH_REL_TOL = 0.01
# - Classic avatar radius must stay within a plausible human envelope.
#   engine/physics.cpp:1051-1054 derives the radius from lattice constants;
#   these bounds are anthropometric, not render constants.
HUMAN_RADIUS_MIN_M = 0.30
HUMAN_RADIUS_MAX_M = 0.50
# - Compiled DEM range must agree with the GSI source range within the DEM
#   half-cell uncertainty bound quoted by dem5a_ingest_receipt.json
#   (uncertainty_bounds_m [0, 1.56]).
DEM_RANGE_TOL_M = 1.56
MIN_SOURCE_RANGE_M = 1.0
# - Anchor chords: each endpoint carries isotropic sigma 2/3 m
#   (world_ir_ingest_osm_extract + anchor_set.json uncertainty records);
#   allow both endpoint sigmas plus 0.05 m affine rounding.
ANCHOR_SIGMA_M = 2.0 / 3.0
ANCHOR_ROUND_M = 0.05
ANCHOR_CHORD_TOL_M = 2 * ANCHOR_SIGMA_M + ANCHOR_ROUND_M
ANCHOR_MIN_PAIRS = 4
ANCHOR_MAX_PAIRS = 32
# - Gravity alignment threshold quoted from the FL-4812 solve receipt.
GRAVITY_MAX_DEG = 2.5
# - Leg 6 (projected_proportion) renderer constants, each with its owner:
#   engine/render/render_scene.cpp:596-601: non-DBL terminal scale 1.5.
RENDER_NONDBL_SCALE = 1.5
#   engine/terrain.h:54,60,66: HEIGHT_SCALE 16, HEIGHT_CELLS 4, VISUAL_CELLS 8.
HEIGHT_SCALE_N = 16
HEIGHT_CELLS_N = 4
VISUAL_CELLS_N = 8
#   engine/sprite.cpp:1638: sprite projection zoom 2/3.
SPRITE_PROJ_ZOOM = 2.0 / 3.0
#   scripts/pipeline/geo_frames.py:86-94: CLASSIC_STANDING_ROWS 8 above the
#   foot reference, 1.8 m target, WORLD_UNITS_PER_METRE derived from them.
SPRITE_STANDING_ROWS_QUOTED = 8
SPRITE_FOOT_REF_ROWS = 1
PLAYER_TARGET_HEIGHT_M = 1.8
# - Leg 6 tolerances, none tuned to pass:
#   roof_height_m priors are metre-quantum (observed 6.0); a 0.25 m authoring
#   quantum on a 6 m mass is 4.2 %, plus bake rounding: allow 5 %.
PROBE_REL_TOL = 0.05
MIN_PROBE_MASSES = 100
#   Implied player height must read as an adult standing height.
PLAYER_HEIGHT_MIN_M = 1.6
PLAYER_HEIGHT_MAX_M = 2.0
#   A house must read 2-4x the standing player (about 1/3); outside this the
#   player reads as a giant (ratio < 2) or a miniature (ratio > 4).
HOUSE_RATIO_MIN = 2.0
HOUSE_RATIO_MAX = 4.0
#   A 4.5 m residential street must read several player-widths wide.
ROAD_WIDTH_M = 4.5
ROAD_RATIO_MIN = 3.0
ROAD_RATIO_MAX = 7.0
#   Cross-map house-ratio agreement within about one storey
#   (3 m ~= 13.3 rows ~= 1.66 player ratios at the quoted scale).
CROSSMAP_RATIO_TOL = 1.5


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_route_length(walk: dict) -> dict:
    route = walk.get("route", {})
    length_m = route.get("length_m")
    reported_m = route.get("source_reported_length_m")
    check = {
        "id": "route_length_agreement",
        "got_m": length_m,
        "expected_m": reported_m,
        "source": "valhalla_pedestrian_route trip.summary.length vs compiled corridor",
    }
    if not isinstance(length_m, (int, float)) or not isinstance(
        reported_m, (int, float)
    ):
        check["verdict"] = False
        check["reason"] = "non-numeric route length"
        return check
    tol = max(ROUTE_LENGTH_ABS_TOL_M, ROUTE_LENGTH_REL_TOL * reported_m)
    check["tolerance_m"] = tol
    check["residual_m"] = abs(length_m - reported_m)
    check["verdict"] = check["residual_m"] <= tol
    return check


def _check_avatar_corridor(walk: dict, wupm: float) -> dict:
    route = walk.get("route", {})
    counts = walk.get("counts", {})
    radius_m = route.get("classic_human_radius_m")
    check = {
        "id": "avatar_corridor_plausibility",
        "got_radius_m": radius_m,
        "radius_bounds_m": [HUMAN_RADIUS_MIN_M, HUMAN_RADIUS_MAX_M],
        "post_blocked_samples": counts.get("post_blocked_samples"),
        "missing_samples": counts.get("missing_samples"),
        "corridor_samples": counts.get("corridor_samples"),
        "source": "anthropometric envelope + compiled corridor blockage",
    }
    if not isinstance(radius_m, (int, float)) or not isinstance(wupm, (int, float)):
        check["verdict"] = False
        check["reason"] = "non-numeric avatar radius or scale"
        return check
    check["verdict"] = bool(
        HUMAN_RADIUS_MIN_M <= radius_m <= HUMAN_RADIUS_MAX_M
        and counts.get("post_blocked_samples") == 0
        and counts.get("missing_samples") == 0
        and isinstance(counts.get("corridor_samples"), int)
        and counts["corridor_samples"] > 0
    )
    return check


def _check_vertical_range(dem: dict) -> dict:
    elevation = dem.get("elevation", {})
    compiled_max = elevation.get("compiled_relative_max_m")
    compiled_min = elevation.get("compiled_relative_min_m")
    source_range = elevation.get("source_range_m")
    check = {
        "id": "vertical_range_agreement",
        "got_compiled_range_m": None,
        "expected_source_range_m": source_range,
        "tolerance_m": DEM_RANGE_TOL_M,
        "source": "gsi_dem5a source range vs compiled bake range",
    }
    if not all(
        isinstance(v, (int, float))
        for v in (compiled_max, compiled_min, source_range)
    ):
        check["verdict"] = False
        check["reason"] = "non-numeric elevation range"
        return check
    compiled_range = compiled_max - compiled_min
    check["got_compiled_range_m"] = compiled_range
    check["residual_m"] = abs(compiled_range - source_range)
    check["verdict"] = bool(
        source_range >= MIN_SOURCE_RANGE_M
        and check["residual_m"] <= DEM_RANGE_TOL_M
    )
    return check


def _check_anchor_metrology(anchor_set: dict, wupm: float) -> dict:
    anchors = [
        row
        for row in anchor_set.get("anchors", [])
        if row.get("inside_aoi")
        and isinstance(row.get("epsg32654"), list)
        and isinstance(row.get("y9_world"), list)
    ]
    check = {
        "id": "anchor_metrology",
        "anchor_pairs": 0,
        "max_residual_m": None,
        "tolerance_m": ANCHOR_CHORD_TOL_M,
        "source": "osm anchor EPSG32654 chords vs y9_world chords",
    }
    pairs = min(len(anchors) - 1, ANCHOR_MAX_PAIRS)
    if (
        not isinstance(wupm, (int, float))
        or wupm <= 0
        or pairs < ANCHOR_MIN_PAIRS
    ):
        check["verdict"] = False
        check["reason"] = "insufficient anchors or non-numeric scale"
        return check
    worst = 0.0
    for first, second in zip(anchors[:pairs], anchors[1 : pairs + 1]):
        ex, ey = first["epsg32654"][:2]
        fx, fy = second["epsg32654"][:2]
        ax, ay = first["y9_world"][:2]
        bx, by = second["y9_world"][:2]
        d_epsg = math.dist((ex, ey), (fx, fy))
        d_world = math.dist((ax, ay), (bx, by)) / wupm
        worst = max(worst, abs(d_epsg - d_world))
    check["anchor_pairs"] = pairs
    check["max_residual_m"] = worst
    check["verdict"] = bool(worst <= ANCHOR_CHORD_TOL_M)
    return check


def _check_gravity_level(camera_registration: dict) -> dict:
    stages = camera_registration.get("stages", {})
    gravity = stages.get("gravity_alignment", {})
    gate = gravity.get("gate", {})
    check = {
        "id": "gravity_level_record",
        "gate_pass": gate.get("pass"),
        "threshold_deg": GRAVITY_MAX_DEG,
        "source": "fl4812 gravity alignment (orientation evidence only)",
    }
    readings = [
        v
        for key in ("max", "max_deg", "median", "median_deg")
        if isinstance(gravity.get(key), (int, float))
        for v in [gravity[key]]
    ]
    check["readings_deg"] = readings
    check["verdict"] = bool(gate.get("pass") is True)
    return check


def _projection_rates() -> dict:
    """Cells/rows per world unit at zoom 1 from the renderer owners.

    engine/render/render_scene.cpp:596-611: non-DBL terminal scale 1.5 folds
    into zoom, ds = 2*zoom/VISUAL_CELLS. Lines 826-840: tm[0] = cosyaw*ds
    (screen x = world * tm[0] * HEIGHT_CELLS, see tm[12]) and
    tm[9] = cos30/HEIGHT_SCALE*ds*HEIGHT_CELLS (screen rows per raw z-step).
    """
    zoom_eff = 1.0 * RENDER_NONDBL_SCALE
    ds = 2.0 * zoom_eff / VISUAL_CELLS_N
    cells_per_world = ds * HEIGHT_CELLS_N
    rows_per_raw = math.cos(math.radians(30.0)) / HEIGHT_SCALE_N * ds * HEIGHT_CELLS_N
    rows_per_world = rows_per_raw * HEIGHT_SCALE_N
    return {
        "zoom_eff": zoom_eff,
        "ds": ds,
        "cells_per_world_unit_horizontal": cells_per_world,
        "rows_per_raw_z_step": rows_per_raw,
        "rows_per_world_unit_vertical": rows_per_world,
    }


def _parse_sprite_frame_dims(sprite_path: Path) -> dict:
    """Frame grid of a Classic sprite sheet (engine/sprite.cpp:1224-1341).

    L0 row 0 holds the angle digit then per-animation frame counts;
    fr_num_x = projs * anim_sum columns, fr_num_y = angles rows.
    """
    import gzip
    import struct

    raw = gzip.decompress(sprite_path.read_bytes())
    _version, _layers, width, height = struct.unpack_from("<4i", raw, 0)
    layer0 = 16

    def digit(col: int, row: int = 0) -> int:
        glyph = struct.unpack_from(
            "<I", raw, layer0 + (col * height + row) * 10
        )[0]
        return glyph - 48 if 48 <= glyph <= 57 else -1

    angles = digit(0)
    anim_lens: list[int] = []
    if angles > 0:
        projs = 2
        for col in range(1, width):
            length = digit(col)
            if length > 0:
                anim_lens.append(length)
            else:
                break
    else:
        projs = 1
        angles = 1
        for col in range(1, width):
            length = digit(col)
            if length > 0:
                anim_lens.append(length)
            else:
                break
    if not anim_lens:
        anim_lens = [1]
    fr_num_x = projs * sum(anim_lens)
    fr_num_y = angles
    return {
        "sheet_width": width,
        "sheet_height": height,
        "angles": angles,
        "projs": projs,
        "anim_lens": anim_lens,
        "fr_num_x": fr_num_x,
        "fr_num_y": fr_num_y,
        "frame_width_cells": width // fr_num_x,
        "frame_height_rows": height // fr_num_y,
    }


def _akm_extents(akm_path: Path) -> dict:
    """Min/max of x/y/z over the ASCII-PLY vertices of a .akm mesh."""
    lines = akm_path.read_text(encoding="utf-8").splitlines()
    vcount = 0
    props: list[str] = []
    cur = 0
    in_vertex = False
    for idx, line in enumerate(lines):
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "element" and parts[1] == "vertex":
            vcount = int(parts[2])
            in_vertex = True
        elif parts[0] == "element":
            in_vertex = False
        elif parts[0] == "property" and in_vertex:
            props.append(parts[-1])
        elif parts[0] == "end_header":
            cur = idx + 1
            break
    ix, iy, iz = props.index("x"), props.index("y"), props.index("z")
    need = max(ix, iy, iz)
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for line in lines[cur : cur + vcount]:
        toks = line.split()
        xs.append(float(toks[ix]))
        ys.append(float(toks[iy]))
        zs.append(float(toks[iz]))
    _ = need
    return {
        "x_extent": max(xs) - min(xs),
        "y_extent": max(ys) - min(ys),
        "z_extent": max(zs) - min(zs),
    }


def _load_a3d_reader():
    """Load the owned scripts/inspect_a3d.py reader without executing it."""
    import importlib.util

    mod_path = REPO_ROOT / "scripts" / "inspect_a3d.py"
    spec = importlib.util.spec_from_file_location("inspect_a3d_owner", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot find A3D reader at {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _trailing_number(text: str) -> str | None:
    import re

    match = re.search(r"(\d+)\s*$", text or "")
    return match.group(1) if match else None


def _probe_baked_masses(
    map_path: Path, mesh_root: Path, buildings: list[dict]
) -> dict:
    """Baked height in raw z-steps per building mass.

    Each building row links to its realised .a3d instance by the trailing
    numeric id (facade instances carry lower_fixtures_rule-facade-<wayid>,
    masses carry building_mass_way_<wayid>). Baked height is the mesh z
    extent times the instance z column (compile_facade_shell bakes
    transform row2 z = HEIGHT_SCALE, so mesh units come out in raw steps).
    """
    reader = _load_a3d_reader()
    doc = reader.read_a3d(str(map_path))
    index: dict[str, list] = {}
    for inst in doc["instances"]:
        for key in (inst.inst_name or "", inst.mesh_name or ""):
            num = _trailing_number(key)
            if num:
                index.setdefault(num, []).append(inst)
    extents: dict[str, dict] = {}
    per_mass: list[float] = []
    baked_raws: list[float] = []
    matched = 0
    for row in buildings:
        roof = row.get("roof_height_m")
        if not isinstance(roof, (int, float)) or roof <= 0:
            continue
        num = _trailing_number(
            str(row.get("inst_name") or "")
        ) or _trailing_number(str(row.get("feature_id") or ""))
        if num is None:
            continue
        cands = [
            inst
            for inst in index.get(num, [])
            if (inst.mesh_name or "").lower().endswith(".akm")
        ]
        if not cands:
            continue
        inst = cands[0]
        mesh_name = Path(inst.mesh_name).name
        if mesh_name not in extents:
            mesh_path = mesh_root / mesh_name
            if not mesh_path.is_file():
                continue
            extents[mesh_name] = _akm_extents(mesh_path)
        baked_raw = extents[mesh_name]["z_extent"] * abs(inst.transform[10])
        per_mass.append(baked_raw / roof)
        baked_raws.append(baked_raw)
        matched += 1
    per_mass.sort()
    baked_raws.sort()
    median = per_mass[len(per_mass) // 2] if per_mass else None
    return {
        "masses": len(buildings),
        "matched": matched,
        "median_raw_per_metre": median,
        "median_baked_raw": baked_raws[len(baked_raws) // 2] if baked_raws else None,
    }


def _probe_y8_house(
    y8_map_path: Path, y8_mesh_root: Path, rates: dict
) -> dict:
    """Projected size of the y8 reference house-3.akm instance."""
    reader = _load_a3d_reader()
    doc = reader.read_a3d(str(y8_map_path))
    hits = [
        inst
        for inst in doc["instances"]
        if Path(inst.mesh_name or "").name == "house-3.akm"
    ]
    if not hits:
        return {"instances": 0}
    ext = _akm_extents(y8_mesh_root / "house-3.akm")
    raws: list[float] = []
    widths: list[float] = []
    for inst in hits:
        t = inst.transform
        raws.append(ext["z_extent"] * abs(t[10]))
        # Screen columns depend on world x/y only: project the footprint
        # corners through the instance x/y columns and take the span.
        ex, ey = ext["x_extent"] / 2.0, ext["y_extent"] / 2.0
        span = max(
            abs(ex * t[0] + ey * t[4]), abs(ex * t[0] - ey * t[4])
        ) * 2.0 * rates["cells_per_world_unit_horizontal"]
        widths.append(span)
    # Reference instance: the full-height 39.15 z-scale placement the task
    # contract pins (263 raw, ~21 rows, ~23 cells), i.e. max baked height.
    order = sorted(range(len(hits)), key=lambda k: raws[k])
    ref = order[-1]
    return {
        "instances": len(hits),
        "baked_raw": raws[ref],
        "rows": raws[ref] * rates["rows_per_raw_z_step"],
        "width_cells": widths[ref],
    }


def _check_projected_proportion(
    metadata: dict,
    building_instances_path: Path | None,
    map_path: Path | None,
    mesh_root: Path | None,
    y8_map_path: Path,
    y8_mesh_root: Path,
    sprite_path: Path,
) -> dict:
    """Leg 6: on-screen proportions derived arithmetically, never headed."""
    leg: dict = {
        "id": "projected_proportion",
        "sources": {
            "renderer_scale": "engine/render/render_scene.cpp:596-601",
            "projection_matrix": "engine/render/render_scene.cpp:826-840",
            "sprite_bbox": "engine/sprite.cpp:1632-1645",
            "human_collision": "engine/physics.cpp:1050-1060",
            "height_constants": "engine/terrain.h:54,60,66",
            "content_scale": "scripts/pipeline/geo_frames.py:86-94",
            "sprite_sheet": str(sprite_path),
        },
    }
    wupm = metadata.get("content_scale")
    if not isinstance(wupm, (int, float)) or wupm <= 0:
        leg["verdict"] = False
        leg["reason"] = "non-numeric content scale"
        return leg
    missing = [
        name
        for name, path in (
            ("building_instances", building_instances_path),
            ("map", map_path),
            ("mesh_root", mesh_root),
        )
        if path is None or not Path(path).exists()
    ]
    if missing or not y8_map_path.exists() or not sprite_path.exists():
        leg["verdict"] = False
        leg["reason"] = f"missing inputs: {missing}"
        return leg
    rates = _projection_rates()
    leg["rates"] = rates
    rows_per_metre = wupm * rates["rows_per_world_unit_vertical"]
    cells_per_metre = wupm * rates["cells_per_world_unit_horizontal"]
    leg["rows_per_metre"] = rows_per_metre
    leg["cells_per_metre"] = cells_per_metre
    frame = _parse_sprite_frame_dims(sprite_path)
    leg["sprite_frame"] = frame
    player_rows = frame["frame_height_rows"] - SPRITE_FOOT_REF_ROWS
    player_cells = int(round(frame["frame_width_cells"] * SPRITE_PROJ_ZOOM))
    leg["player_rows"] = player_rows
    leg["player_cells"] = player_cells
    # Operator map scale (2026-10-13): the map is OPERATOR_MAP_SCALE x larger than
    # the sprite calibration, so the run's wupm is sprite_wupm * k.  Recover k from
    # the run itself (1.0 for pre-operator runs) and judge the sprite in sprite
    # metres (player_rows / (rows_per_metre / k)), the house and road bands in map
    # metres (bands x k).  Without this a deliberate 1.5x map reads as a 1.2 m
    # player and fails every band the sprite-relative checks were calibrated on.
    operator_k = wupm / GF.SPRITE_WORLD_UNITS_PER_METRE
    leg["operator_map_scale"] = operator_k
    implied_m = player_rows / (rows_per_metre / operator_k)
    leg["player_implied_height_m"] = implied_m
    buildings = json.loads(Path(building_instances_path).read_text(encoding="utf-8"))
    probe = _probe_baked_masses(Path(map_path), Path(mesh_root), buildings)
    leg["mass_probe"] = probe
    comparisons: list[dict] = []
    expected_raw_per_m = wupm * HEIGHT_SCALE_N
    if probe["median_raw_per_metre"] is None or probe["matched"] < MIN_PROBE_MASSES:
        comparisons.append({
            "id": "baked_mass_scale",
            "verdict": False,
            "reason": "insufficient matched masses",
        })
        median_rows = None
    else:
        median = probe["median_raw_per_metre"]
        residual = abs(median - expected_raw_per_m) / expected_raw_per_m
        comparisons.append({
            "id": "baked_mass_scale",
            "got_raw_per_metre": median,
            "expected_raw_per_metre": expected_raw_per_m,
            "tolerance_rel": PROBE_REL_TOL,
            "residual_rel": residual,
            "verdict": bool(residual <= PROBE_REL_TOL),
        })
        median_rows = (
            probe["median_baked_raw"] * rates["rows_per_raw_z_step"]
            if probe["median_baked_raw"] is not None
            else None
        )
    six_m_rows = 6.0 * rows_per_metre
    leg["six_metre_mass_rows"] = six_m_rows
    comparisons.append({
        "id": "player_height",
        "got_m": implied_m,
        "band_m": [PLAYER_HEIGHT_MIN_M, PLAYER_HEIGHT_MAX_M],
        "verdict": bool(PLAYER_HEIGHT_MIN_M <= implied_m <= PLAYER_HEIGHT_MAX_M),
    })
    numabe_ratio = (median_rows / player_rows) if median_rows else None
    leg["numabe_house_rows"] = median_rows
    leg["numabe_house_player_ratio"] = numabe_ratio
    comparisons.append({
        "id": "numabe_house_proportion",
        "got_ratio": numabe_ratio,
        "band": [HOUSE_RATIO_MIN * operator_k, HOUSE_RATIO_MAX * operator_k],
        "verdict": bool(
            numabe_ratio is not None
            and HOUSE_RATIO_MIN * operator_k <= numabe_ratio <= HOUSE_RATIO_MAX * operator_k
        ),
    })
    y8 = _probe_y8_house(y8_map_path, y8_mesh_root, rates)
    leg["y8_house"] = y8
    y8_ratio = (y8["rows"] / player_rows) if y8.get("rows") else None
    leg["y8_house_player_ratio"] = y8_ratio
    comparisons.append({
        "id": "y8_house_proportion",
        "got_ratio": y8_ratio,
        "band": [HOUSE_RATIO_MIN, HOUSE_RATIO_MAX],
        "verdict": bool(
            y8_ratio is not None
            and HOUSE_RATIO_MIN <= y8_ratio <= HOUSE_RATIO_MAX
        ),
    })
    road_cells = ROAD_WIDTH_M * cells_per_metre
    road_ratio = road_cells / player_cells
    leg["road_cells"] = road_cells
    leg["road_player_ratio"] = road_ratio
    comparisons.append({
        "id": "road_proportion",
        "got_ratio": road_ratio,
        "band": [ROAD_RATIO_MIN * operator_k, ROAD_RATIO_MAX * operator_k],
        "verdict": bool(
            ROAD_RATIO_MIN * operator_k <= road_ratio <= ROAD_RATIO_MAX * operator_k
        ),
    })
    if numabe_ratio is not None and y8_ratio is not None:
        # Y8 is not operator-scaled, so remove k from the Numabe ratio first.
        gap = abs(numabe_ratio / operator_k - y8_ratio)
        cross: dict = {
            "id": "crossmap_agreement",
            "gap_ratio_points": gap,
            "tolerance_ratio_points": CROSSMAP_RATIO_TOL,
        }
        if gap <= CROSSMAP_RATIO_TOL:
            cross["verdict"] = True
        else:
            cross["verdict"] = False
            probe_ok = comparisons[0].get("verdict") is True
            player_ok = comparisons[1].get("verdict") is True
            if not probe_ok:
                cross["likely_responsible_term"] = (
                    "mass rendering (baked raw/m disagrees with quoted scale)"
                )
            elif not player_ok:
                cross["likely_responsible_term"] = (
                    "sprite rendering (implied player height off target)"
                )
            else:
                cross["likely_responsible_term"] = (
                    "content (different building heights between the maps), "
                    "not the projection chain"
                )
        comparisons.append(cross)
    else:
        comparisons.append({"id": "crossmap_agreement", "verdict": False})
    leg["comparisons"] = comparisons
    leg["verdict"] = all(item.get("verdict") is True for item in comparisons)
    return leg


def compile_scale_receipt(
    *,
    route_walkability_receipt_path: Path,
    dem_receipt_path: Path,
    anchor_set_path: Path,
    terrain_metadata_path: Path,
    camera_registration_path: Path,
    out_path: Path,
    building_instances_path: Path | None = None,
    map_path: Path | None = None,
    mesh_root: Path | None = None,
    y8_map_path: Path | None = None,
    y8_mesh_root: Path | None = None,
    player_sprite_path: Path | None = None,
) -> dict:
    """Cross-check compiled scale against independent sources; write receipt."""
    run_root = terrain_metadata_path.parent
    if building_instances_path is None:
        building_instances_path = run_root / "building_instances.json"
    if map_path is None:
        map_path = run_root / "output.a3d"
    if mesh_root is None:
        mesh_root = run_root / "meshes"
    if y8_map_path is None:
        y8_map_path = REPO_ROOT / "godot_project" / "assets" / "maps" / "game_map_y8.a3d"
    if y8_mesh_root is None:
        y8_mesh_root = REPO_ROOT / "assets" / "meshes"
    if player_sprite_path is None:
        player_sprite_path = REPO_ROOT / "assets" / "sprites" / "player-body.xp"
    inputs = {
        "route_walkability_receipt": route_walkability_receipt_path,
        "dem_receipt": dem_receipt_path,
        "anchor_set": anchor_set_path,
        "terrain_metadata": terrain_metadata_path,
        "camera_registration": camera_registration_path,
    }
    walk = json.loads(route_walkability_receipt_path.read_text(encoding="utf-8"))
    dem = json.loads(dem_receipt_path.read_text(encoding="utf-8"))
    anchor_set = json.loads(anchor_set_path.read_text(encoding="utf-8"))
    metadata = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
    camera_registration = json.loads(
        camera_registration_path.read_text(encoding="utf-8")
    )
    wupm = metadata.get("content_scale")

    checks = [
        _check_route_length(walk),
        _check_avatar_corridor(walk, wupm),
        _check_vertical_range(dem),
        _check_anchor_metrology(anchor_set, wupm),
        _check_gravity_level(camera_registration),
    ]
    leg6 = _check_projected_proportion(
        metadata,
        building_instances_path,
        map_path,
        mesh_root,
        y8_map_path,
        y8_mesh_root,
        player_sprite_path,
    )
    receipt = {
        "schema": SCHEMA,
        "stage": "executed_scale_cross_check",
        "world_units_per_metre_quoted": wupm,
        "inputs": {
            key: {
                "path": str(path.resolve()),
                "sha256": _sha256(path),
            }
            for key, path in inputs.items()
        },
        "checks": checks,
        "projected_proportion": leg6,
        "pass": all(check.get("verdict") is True for check in checks)
        and leg6.get("verdict") is True,
    }
    out_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--route-walkability-receipt", type=Path, required=True)
    parser.add_argument("--dem-receipt", type=Path, required=True)
    parser.add_argument("--anchor-set", type=Path, required=True)
    parser.add_argument("--terrain-metadata", type=Path, required=True)
    parser.add_argument("--camera-registration", type=Path, required=True)
    parser.add_argument("--building-instances", type=Path, default=None)
    parser.add_argument("--map", type=Path, default=None)
    parser.add_argument("--mesh-root", type=Path, default=None)
    parser.add_argument("--y8-map", type=Path, default=None)
    parser.add_argument("--y8-mesh-root", type=Path, default=None)
    parser.add_argument("--player-sprite", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = compile_scale_receipt(
        route_walkability_receipt_path=args.route_walkability_receipt,
        dem_receipt_path=args.dem_receipt,
        anchor_set_path=args.anchor_set,
        terrain_metadata_path=args.terrain_metadata,
        camera_registration_path=args.camera_registration,
        building_instances_path=args.building_instances,
        map_path=args.map,
        mesh_root=args.mesh_root,
        y8_map_path=args.y8_map,
        y8_mesh_root=args.y8_mesh_root,
        player_sprite_path=args.player_sprite,
        out_path=args.out,
    )
    print(f"SCALE RECEIPT {'PASS' if receipt['pass'] else 'FAIL'}: {args.out}")
    for check in receipt["checks"]:
        print(f"  {check['id']}: {'pass' if check.get('verdict') else 'FAIL'}")
    leg = receipt["projected_proportion"]
    print(
        f"  projected_proportion: "
        f"{'pass' if leg.get('verdict') else 'FAIL'}"
    )
    for item in leg.get("comparisons", []):
        mark = "pass" if item.get("verdict") else "FAIL"
        detail = item.get("got_ratio", item.get("got_m", item.get("got_raw_per_metre", "")))
        print(f"    {item['id']}: {mark} got={detail}")
    return 0 if receipt["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
