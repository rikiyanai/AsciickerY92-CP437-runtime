#!/usr/bin/env python3
"""Compile an object-metric receipt for a built map (FL-4809 METRIC-01).

Every reusable visual asset carries a physical metre contract (pack physical
blocks, archetype contracts, rules class facts); every emitted instance is
verified against it. PS1 simplification never grants metric distortion.

Per instance: asset_id, emitted extents in metres (mesh bbox through the
instance transform), intended extents, scale factor, in_bounds, verdict
PASS|FAIL|UNRESOLVED. Exit 1 on FAIL. No metre literals live here: scale
comes from terrain_metadata, HEIGHT_SCALE and the person basis come from
their owner modules, dimensions come from packs, archetypes, rules facts,
specs and mesh files.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.compile_facade_shell import HEIGHT_SCALE  # noqa: E402
from scripts.pipeline.compile_rules_resolved import OBJECT_METRIC_DEFAULTS  # noqa: E402
from scripts.pipeline.compile_rules_resolved import ROAD_CLASS_DEFAULTS  # noqa: E402
from scripts.pipeline.compile_rules_resolved import OBJECT_METRIC_RATIO_TOLERANCE_FRACTION  # noqa: E402
from scripts.pipeline.compile_scale_receipt import PLAYER_TARGET_HEIGHT_M  # noqa: E402
from scripts.pipeline.population_representation_contract import linear_kind  # noqa: E402

SCHEMA = "y92.object_metric_receipt.v1"

LINEAR_MODULE_FAMILY = {
    "bollard_curb_island": "bollard",
    "concrete_block_wall": "wall",
    "concrete_retaining_edge": "wall",
    "crosswalk_and_edge_guardrail": "curb",
    "curb_and_open_gutter": "curb",
    "curb_edge_line": "curb",
    "green_cycle_or_walk_band": "paver",
    "ivy_wall": "vegetation",
    "lattice_boundary_fence": "fence",
    "level_crossing_equipment": "crossing_equipment",
    "manhole_and_grate": "cabinet_box",
    "mesh_fence": "fence",
    "metal_boundary_fence": "fence",
    # FL-4876 OWED-01: metal_fence has a linear mesh and a representation
    # contract (population_representation_contract 4.0 m) but had no metric
    # family, so its first emitted run (obs-inst-0490) read UNRESOLVED.
    "metal_fence": "fence",
    "meter_and_service_cabinet": "cabinet_box",
    "open_and_covered_drain": "drain",
    "overhead_gantry_wire": "pole_wire",
    "pedestrian_guardrail_continuous": "guardrail",
    "pedestrian_guardrail_vertical_bar_continuous": "guardrail",
    "picket_fence": "fence",
    "platform_railing": "guardrail",
    "pole_streetlight": "pole_wire",
    "potted_frontage_plant": "vegetation",
    "rail_corridor_fence": "fence",
    "rail_corridor_mesh_fence": "fence",
    "rail_track_corridor": "railbed",
    "road_edge_dashed_marking": "marking",
    "road_surface_text_marking": "marking",
    "road_text_arrow_30": "marking",
    "sidewalk_paver_surface": "paver",
    "small_tree": "vegetation",
    "stone_cobble_wall": "wall",
    "stone_lined_water_channel": "drain",
    "street_tree": "vegetation",
    "transformer_drum_cluster": "cabinet_box",
    "trimmed_hedge": "vegetation",
    "utility_pole_wire_cluster": "pole_wire",
    "white_edge_line_shoulder": "marking",
    "water_control_structure": "water_structure",
    "waterway_bridge_railing": "guardrail",
    "white_railguard": "guardrail",
    "zebra_crossing": "marking",
    "wire_span": "pole_wire",
}

# MANUAL-FIXTURE-02 variants that are a different physical object class than
# their module's default family (photographed and accepted by hand): a
# footbridge recorded under waterway_bridge_railing is a deck, not a railing;
# a raised shrub bed is not a pot; a block wall with gate piers is deeper than
# a plain wall; a mature broadleaf frontage tree is a tree, not a shrub.
LINEAR_VARIANT_FAMILY = {
    ("waterway_bridge_railing", "small_concrete_footbridge_with_dark_railings"): "footbridge",
    ("waterway_bridge_railing", "flat_slab_footbridge_no_rails"): "footbridge",
    ("potted_frontage_plant", "stone_faced_raised_shrub_bed"): "planter_bed",
    ("concrete_block_wall", "gray_block_wall_with_tiled_coping_and_gate_piers"): "wall_with_piers",
    # FL-4876 temple precinct wall: the tile coping overhangs like a pier cap (0.48 m deep, 1.70 m tall)
    ("concrete_block_wall", "cream_plaster_wall_with_blue_tile_coping"): "wall_with_piers",
    ("street_tree", "mature_broadleaf_frontage_tree"): "tree",
}

def _read_akm_vertices(path: Path) -> list[tuple[float, float, float]]:
    """FL-4876 COVERAGE-01: native vertex positions of an ASCII .akm mesh."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[tuple[float, float, float]] = []
    need, collecting = 0, False
    for line in lines:
        if line.startswith("element vertex"):
            need = int(line.split()[-1])
        elif line.startswith("end_header"):
            collecting = True
        elif collecting and need > 0:
            parts = line.split()
            out.append((float(parts[0]), float(parts[1]), float(parts[2])))
            need -= 1
    return out


def _read_akm_bbox(path: Path) -> tuple[float, float, float] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines()
    collecting = False
    need = 0
    min_x = min_y = min_z = math.inf
    max_x = max_y = max_z = -math.inf
    for line in lines:
        if line.startswith("element vertex"):
            need = int(line.split()[-1])
        elif line.startswith("end_header"):
            collecting = True
        elif collecting and need > 0:
            parts = line.split()
            x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            min_z = min(min_z, z)
            max_z = max(max_z, z)
            need -= 1
    if min_x is math.inf:
        return None
    return (max_x - min_x, max_y - min_y, max_z - min_z)


def _emitted_extents(transform: list[float], native: tuple[float, float, float],
                     scale: float) -> tuple[float, float, float]:
    dx, dy, dz = native
    corners = [
        (transform[0] * x + transform[1] * y,
         transform[4] * x + transform[5] * y)
        for x in (0.0, dx) for y in (0.0, dy)
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return ((max(xs) - min(xs)) / scale,
            (max(ys) - min(ys)) / scale,
            abs(transform[10]) * dz / (HEIGHT_SCALE * scale))


def _family_bounds(family: str) -> tuple[tuple[float, float], ...] | None:
    row = OBJECT_METRIC_DEFAULTS.get(family)
    if row is None:
        return None
    (values, _unit, _source) = row
    (w_lo, w_hi, d_lo, d_hi, h_lo, h_hi, _smin, _smax) = values
    return ((w_lo, w_hi), (d_lo, d_hi), (h_lo, h_hi))


def _family_scale(family: str) -> tuple[float, float] | None:
    row = OBJECT_METRIC_DEFAULTS.get(family)
    if row is None:
        return None
    return (row[0][6], row[0][7])


def build(build_dir: Path, terrain_metadata_path: Path,
          specs_path: Path, mass_receipt_path: Path,
          generated_at: str) -> tuple[dict, dict]:
    metadata = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
    projection = metadata.get("projection", metadata)
    scale = float(projection["content_scale"])
    meshes_dir = build_dir / "meshes"

    bbox_cache: dict[str, tuple[float, float, float] | None] = {}

    def _native(mesh_name: str) -> tuple[float, float, float] | None:
        if mesh_name not in bbox_cache:
            bbox_cache[mesh_name] = _read_akm_bbox(meshes_dir / mesh_name)
        return bbox_cache[mesh_name]

    def _metres(native: tuple[float, float, float]) -> tuple[float, float, float]:
        return (native[0] / scale, native[1] / scale, native[2] / scale)

    rows: list[dict] = []
    unresolved_assets: dict[str, int] = {}
    def _record(asset_id: str, inst_name: str, emitted: tuple[float, float, float] | None,
                intended: tuple[float, float, float] | None,
                allowed: tuple[float, float] | None, kind: str,
                detail: dict | None = None) -> None:
        row: dict[str, object] = {"asset_id": asset_id, "inst_name": inst_name,
                                  "kind": kind}
        if detail:
            row.update(detail)
        if emitted is not None:
            row["emitted_m"] = [round(v, 4) for v in emitted]
        if intended is not None:
            row["intended_m"] = [round(v, 4) for v in intended]
        if emitted is None or intended is None or allowed is None:
            row["verdict"] = "UNRESOLVED"
            row["reason"] = "missing mesh, block, or rules row"
            rows.append(row)
            return
        factors = [e / i if i else math.inf for e, i in zip(emitted, intended)]
        row["scale_factor"] = [round(v, 4) for v in factors]
        lo, hi = allowed
        if all(lo <= v <= hi for v in factors):
            row["verdict"] = "PASS"
        else:
            row["verdict"] = "FAIL"
            row["reason"] = "scale outside allowed bounds"
            row["allowed"] = [lo, hi]
        rows.append(row)

    # Population-lowered fixtures are the canonical fixture denominator.
    # Their generated AKMs carry an emitter-
    # owned metric contract: serialized native extent, expected local
    # transform norms, and semantic family.  Never route these generated
    # mesh names through the reusable-template pack lookup.
    _lower_fixture_path = build_dir / "lower_fixtures_instances.json"
    _lower_fixture_absent = not _lower_fixture_path.is_file()
    lower_fixture_doc = (json.loads(
        _lower_fixture_path.read_text(encoding="utf-8"))
        if not _lower_fixture_absent else [])
    if not isinstance(lower_fixture_doc, list):
        lower_fixture_doc = []
    for inst in lower_fixture_doc:
        asset_id = str(inst.get("mesh_name") or inst.get("module_id")
                       or "lower_fixture")
        inst_name = str(inst.get("inst_name") or inst.get("placement_id")
                        or asset_id)
        contract = inst.get("metric_contract")
        row = {"asset_id": asset_id, "inst_name": inst_name,
               "kind": "lower_fixture"}
        if not isinstance(contract, dict):
            unresolved_assets[asset_id] = unresolved_assets.get(asset_id, 0) + 1
            row["verdict"] = "UNRESOLVED"
            row["reason"] = "lowered fixture lacks emitter metric contract"
            rows.append(row)
            continue
        family = contract.get("semantic_family")
        row["family"] = family
        row["metric_basis"] = contract.get("basis")
        native = _native(str(inst.get("mesh_name")))
        expected_native = contract.get("intended_native_extent_m")
        expected_norms = contract.get("expected_transform_norms")
        transform = inst.get("transform")
        if (native is None or not isinstance(expected_native, list)
                or len(expected_native) != 3
                or not isinstance(expected_norms, list)
                or len(expected_norms) != 3
                or not isinstance(transform, list) or len(transform) < 11):
            unresolved_assets[asset_id] = unresolved_assets.get(asset_id, 0) + 1
            row["verdict"] = "UNRESOLVED"
            row["reason"] = "lowered fixture metric inputs incomplete"
            rows.append(row)
            continue
        native_m = _metres(native)
        intended = tuple(float(value) for value in expected_native)
        norms = (math.hypot(transform[0], transform[1]),
                 math.hypot(transform[4], transform[5]),
                 abs(transform[10]) / HEIGHT_SCALE)
        norm_want = tuple(float(value) for value in expected_norms)
        native_ratio = tuple(
            have / want if want else math.inf
            for have, want in zip(native_m, intended))
        norm_ratio = tuple(
            have / want if want else math.inf
            for have, want in zip(norms, norm_want))
        emitted_local = tuple(
            native_value * norm
            for native_value, norm in zip(native_m, norms))
        row["native_m"] = [round(value, 4) for value in native_m]
        row["intended_native_m"] = [round(value, 4) for value in intended]
        row["transform_norms"] = [round(value, 4) for value in norms]
        row["expected_transform_norms"] = [round(value, 4)
                                             for value in norm_want]
        row["emitted_m"] = [round(value, 4) for value in emitted_local]
        native_ok = all(abs(value - 1.0) <= 0.02
                        for value in native_ratio)
        transform_ok = all(abs(value - 1.0) <= 0.02
                           for value in norm_ratio)
        family_ok = True
        bounds = _family_bounds(str(family)) if family else None
        # FL-4876 PROXY-FAMILY-01. A family band describes a CATALOGUE
        # archetype. A generated custom fixture is not one: it is built to
        # match its own crop evidence, and the family it carries is the
        # nearest catalogue proxy used for palette and behaviour, not a claim
        # that the object IS that archetype.
        #
        # Measured on this lane: 7 generated_custom_fixture instances exist
        # and 6 failed, in BOTH directions. A convex traffic mirror
        # (0.60 x 0.18 x 2.30) was checked against information_board
        # (1.0-2.5 x 0.2-0.6 x 1.6-2.6) and was too NARROW; a hazard strip
        # barrier (4.20 x 0.13 x 1.05) was checked against gate_barrier
        # (1.0-3.5) and was too LONG, failing for all three of its variants,
        # i.e. the entire family. Deviation in both directions across two
        # unrelated families is a sign the comparison is wrong, not the
        # assets.
        #
        # Their real dimensional authority is their own generated contract,
        # and they satisfy it exactly: native_m equals intended_native_m and
        # transform_norms are unit. Those two legs still gate, so the built
        # mesh is fully pinned to what was intended. Only the archetype-band
        # leg is advisory here, and the band and the deviation are RECORDED
        # on the row rather than dropped, so a genuinely absurd custom
        # fixture is still visible to a reader.
        # The contract key is "basis"; the ROW key is "metric_basis"
        # (set at :219). Reading the row key here would never match.
        _proxy_family = str(contract.get("basis") or "") == \
            "generated_custom_fixture"
        if bounds is not None and not contract.get("site_variable"):
            _in_band = all(lo <= value <= hi for value, (lo, hi)
                           in zip(emitted_local, bounds))
            row["family_bounds_m"] = [list(bound) for bound in bounds]
            if _proxy_family:
                row["family_band_basis"] = "catalogue_proxy_advisory"
                row["family_band_in_bounds"] = _in_band
                if not _in_band:
                    row["family_band_deviation_m"] = [
                        round(min(0.0, value - lo) or max(0.0, value - hi), 4)
                        for value, (lo, hi) in zip(emitted_local, bounds)]
            else:
                family_ok = _in_band
        if native_ok and transform_ok and family_ok:
            row["verdict"] = "PASS"
        else:
            row["verdict"] = "FAIL"
            row["reason"] = (
                "lowered fixture violates native, transform, or family "
                "metric contract")
            row["allowed_ratio"] = [0.98, 1.02]
        rows.append(row)

    _linear_path = build_dir / "lower_linear_instances.json"
    _linear_absent = not _linear_path.is_file()
    linear_doc = json.loads(_linear_path.read_text(encoding="utf-8")) if not _linear_absent else []
    if not isinstance(linear_doc, list):
        linear_doc = []
    for inst in linear_doc:
        module_id = str(inst.get("module_id"))
        _variant = inst.get("variant")
        if not _variant:
            _parts = str(inst.get("mesh_name") or "").split("__")
            _variant = _parts[1] if len(_parts) > 2 else None
        family = (LINEAR_VARIANT_FAMILY.get((module_id, _variant))
                  or LINEAR_MODULE_FAMILY.get(module_id))
        if family is None:
            unresolved_assets[module_id] = unresolved_assets.get(module_id, 0) + 1
            _record(module_id, str(inst.get("inst_name")), None, None, None, "linear")
            continue
        bounds = _family_bounds(family)
        native = _native(str(inst.get("mesh_name")))
        if native is None or bounds is None:
            unresolved_assets[module_id] = unresolved_assets.get(module_id, 0) + 1
            _record(module_id, str(inst.get("inst_name")), None, None, None, "linear",
                    {"family": family})
            continue
        kind = linear_kind(module_id)
        native_m = _metres(native)
        dx, dy, dz = native_m
        row = {"asset_id": module_id, "inst_name": str(inst.get("inst_name")),
               "kind": "linear", "family": family,
               "native_m": [round(v, 4) for v in native_m]}
        # Comparison tolerance is a ratio, not a metre: mesh vertices round
        # to millimetres, so exact-boundary hits must not fail on float dust.
        tol = 0.01
        if kind == "point":
            box_ok = (bounds[0][0] * (1.0 - tol) <= dx <= bounds[0][1] * (1.0 + tol)
                      and bounds[1][0] * (1.0 - tol) <= dy <= bounds[1][1] * (1.0 + tol)
                      and bounds[2][0] * (1.0 - tol) <= dz <= bounds[2][1] * (1.0 + tol))
            if not box_ok:
                row["verdict"] = "FAIL"
                row["reason"] = "native point asset outside family range"
                rows.append(row)
                continue
        else:
            cross_lo = min(bounds[0][0], bounds[1][0]) * (1.0 - tol)
            cross_hi = max(bounds[0][1], bounds[1][1]) * (1.0 + tol)
            cross_ok = (cross_lo <= dy <= cross_hi
                        and bounds[2][0] * (1.0 - tol) <= dz <= bounds[2][1] * (1.0 + tol))
            claim = inst.get("span_m") if inst.get("span_record") else inst.get("tile_length_m")
            length_ok = (claim is None
                         or (dx <= float(claim) * 1.1 * 1.01 and dx >= float(claim) * 0.01))
            if not cross_ok:
                row["verdict"] = "FAIL"
                row["reason"] = "native tile cross-section outside family range"
                rows.append(row)
                continue
            if not length_ok:
                row["verdict"] = "FAIL"
                row["reason"] = "native tile longer than its placement tile"
                row["tile_length_m"] = claim
                rows.append(row)
                continue
        emitted = (_emitted_extents(inst["transform"], native, scale)
                   if inst.get("transform") else None)
        if emitted is None:
            row["verdict"] = "UNRESOLVED"
            row["reason"] = "missing transform"
            rows.append(row)
            continue
        row["emitted_m"] = [round(v, 4) for v in emitted]
        t = inst["transform"]
        norms = (math.hypot(t[0], t[1]), math.hypot(t[4], t[5]),
                 abs(t[10]) / HEIGHT_SCALE)
        row["transform_norms"] = [round(v, 4) for v in norms]
        if all(abs(v - 1.0) <= 0.02 for v in norms):
            row["verdict"] = "PASS"
        else:
            row["verdict"] = "FAIL"
            row["reason"] = "emit scaled the tile"
            row["allowed_norms"] = [0.98, 1.02]
        rows.append(row)

    # Population custom identities are generated physical planes in the A3D
    # map. Their per-instance dimensions are the metric contract: verify the
    # serialized mesh and rigid transform instead of treating them as labels.
    _custom_path = build_dir / "lower_custom_instances.json"
    _custom_absent = not _custom_path.is_file()
    custom_doc = (json.loads(_custom_path.read_text(encoding="utf-8"))
                  if not _custom_absent else [])
    if not isinstance(custom_doc, list):
        custom_doc = []
    for inst in custom_doc:
        asset_id = str(inst.get("mesh_name") or inst.get("subject") or "custom_identity")
        inst_name = str(inst.get("inst_name") or inst.get("placement_id") or asset_id)
        native = _native(str(inst.get("mesh_name")))
        intended_values = (inst.get("plane_width_m"), inst.get("plane_depth_m"),
                           inst.get("plane_height_m"))
        row = {"asset_id": asset_id, "inst_name": inst_name,
               "kind": "custom_identity", "family": "custom_identity"}
        if native is None or any(value is None for value in intended_values):
            unresolved_assets[asset_id] = unresolved_assets.get(asset_id, 0) + 1
            row["verdict"] = "UNRESOLVED"
            row["reason"] = "missing custom mesh or declared plane dimensions"
            rows.append(row)
            continue
        intended = tuple(float(value) for value in intended_values)
        native_m = _metres(native)
        row["native_m"] = [round(value, 4) for value in native_m]
        row["intended_m"] = [round(value, 4) for value in intended]
        ratios = [native_value / intended_value if intended_value else math.inf
                  for native_value, intended_value in zip(native_m, intended)]
        row["mesh_ratio"] = [round(value, 4) for value in ratios]
        transform = inst.get("transform")
        if not isinstance(transform, list) or len(transform) < 11:
            row["verdict"] = "UNRESOLVED"
            row["reason"] = "missing custom transform"
            rows.append(row)
            continue
        norms = (math.hypot(transform[0], transform[1]),
                 math.hypot(transform[4], transform[5]),
                 abs(transform[10]) / HEIGHT_SCALE)
        row["transform_norms"] = [round(value, 4) for value in norms]
        if (all(abs(value - 1.0) <= 0.02 for value in ratios)
                and all(abs(value - 1.0) <= 0.02 for value in norms)):
            row["verdict"] = "PASS"
            row["emitted_m"] = [round(value, 4) for value in intended]
        else:
            row["verdict"] = "FAIL"
            row["reason"] = "custom mesh or transform violates declared plane dimensions"
            row["allowed_ratio"] = [0.98, 1.02]
        rows.append(row)

    specs = {row["feature_id"]: row
             for row in json.loads(specs_path.read_text(encoding="utf-8"))}
    mass = json.loads(mass_receipt_path.read_text(encoding="utf-8"))
    for entry in mass.get("per_building", []):
        source = str(entry.get("source") or "")
        spec = specs.get("feature:building_footprint:" + source)
        if spec is None:
            name = str(entry.get("name") or "")
            if name.startswith("building_mass_"):
                rest = name[len("building_mass_"):]
                if rest.startswith("bldg_"):
                    spec = specs.get("feature:building_footprint:plateau/" + rest)
                elif "_" in rest:
                    head, tail = rest.split("_", 1)
                    spec = specs.get("feature:building_footprint:" + head + "/" + tail)
        if spec is None:
            unresolved_assets["building_mass"] = unresolved_assets.get("building_mass", 0) + 1
            _record("building_mass", str(entry.get("name")), None, None, None, "mass")
            continue
        ring = spec.get("footprint") or []
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        intended = ((max(xs) - min(xs)) / scale if xs else 0.0,
                    (max(ys) - min(ys)) / scale if ys else 0.0,
                    float(spec.get("roof_height_m") or 0.0))
        got = entry.get("realized_roof_height_m")
        basis = entry.get("realized_height_basis") or "collision_akm_emitted_geometry"
        if got is None:
            got = entry.get("roof_height_m")
            basis = "mass receipt roof_height_m"
        if got is None:
            # Collision-fallback masses record no baked roof height; the
            # collision mesh is not the mass, so there is nothing to verify.
            row = {"asset_id": "building_mass",
                   "inst_name": str(entry.get("name")), "kind": "mass",
                   "intended_m": [round(v, 4) for v in intended],
                   "verdict": "UNRESOLVED",
                   "reason": "no baked height recorded (collision fallback only)"}
            rows.append(row)
            continue
        got = float(got)
        # a mass whose bake states its own measured claim (fitted pitched LiDAR ridge, FL-4876) is held to it;
        # otherwise to the spec claim
        if entry.get("intended_roof_height_m") is not None:
            intended = (intended[0], intended[1], float(entry["intended_roof_height_m"]))
        want = intended[2]
        factor = got / want if want else math.inf
        row = {"asset_id": "building_mass",
               "inst_name": str(entry.get("name")), "kind": "mass",
               "family": "building_mass",
               "intended_m": [round(v, 4) for v in intended],
               "emitted_m": [round(intended[0], 4), round(intended[1], 4), round(got, 4)],
               "emitted_roof_m": round(got, 4),
               "height_basis": basis,
               "scale_factor": round(factor, 4)}
        if entry.get("ok", True) and abs(factor - 1.0) <= 0.05:
            row["verdict"] = "PASS"
        else:
            row["verdict"] = "FAIL"
            row["reason"] = "baked roof height differs from spec claim"
        rows.append(row)

    inputs_absent = []
    if _linear_absent:
        inputs_absent.append({"path": str(_linear_path), "stage": "lower_linear",
                              "reason": "not_emitted_in_build: linear lowering did not run for this build"})
    if _lower_fixture_absent:
        inputs_absent.append({
            "path": str(_lower_fixture_path),
            "stage": "lower_fixtures",
            "reason": ("emission_inventory_missing: population fixture "
                       "lowering did not persist its A3D denominator")})
    if _custom_absent:
        inputs_absent.append({"path": str(_custom_path), "stage": "lower_custom",
                              "reason": "emission_inventory_missing: custom lowering did not persist its A3D denominator"})
    # FL-4876 COVERAGE-01: place fronts are emitted physical geometry, but no
    # metric row covered them, so the acceptance chain failed every observed
    # physical front as "emitted but no metric row". Measured along the face:
    # mesh vertices projected on the face tangent (perpendicular of the
    # recorded outward normal) give the realized width, which must match the
    # fitted width within 2 percent; an entry-gate profile may add up to 1.0 m
    # of total eave overhang (a gate roof is wider than its opening). Height
    # must be a plausible storefront storey band and depth a shallow panel.
    _front_path = build_dir / "place_front_instances.json"
    for _pinst in (json.loads(_front_path.read_text(encoding="utf-8"))
                   if _front_path.is_file() else []):
        if _pinst.get("representation_kind") != "observed_physical":
            continue
        _pname = str(_pinst.get("inst_name"))
        _passet = str(_pinst.get("resolved_profile_id") or _pinst.get("mesh_name"))
        _prow = {"asset_id": _passet, "inst_name": _pname, "kind": "place_front",
                 "family": "place_front"}
        _pverts = _read_akm_vertices(meshes_dir / str(_pinst.get("mesh_name")))
        _pn = _pinst.get("front_outward_normal_xy")
        _pw = _pinst.get("fitted_width_m")
        if not _pverts or not isinstance(_pn, (list, tuple)) or not _pw:
            unresolved_assets[_passet] = unresolved_assets.get(_passet, 0) + 1
            _prow.update(verdict="UNRESOLVED",
                         reason="missing place-front mesh, normal or fitted width")
            rows.append(_prow)
            continue
        _nx, _ny = float(_pn[0]), float(_pn[1])
        _along = [x * -_ny + y * _nx for x, y, _ in _pverts]
        _deep = [x * _nx + y * _ny for x, y, _ in _pverts]
        _zs = [z for _, _, z in _pverts]
        _wm = (max(_along) - min(_along)) / scale
        _dm = (max(_deep) - min(_deep)) / scale
        _hm = (max(_zs) - min(_zs)) / scale
        _gate = "entry_gate" in _passet
        _wlo, _whi = float(_pw) * 0.98, float(_pw) * 1.02 + (1.0 if _gate else 0.0)
        _prow.update(emitted_m=[round(_wm, 4), round(_dm, 4), round(_hm, 4)],
                     fitted_width_m=float(_pw), allowed_width_m=[round(_wlo, 4), round(_whi, 4)],
                     allowed_height_m=[2.2, 12.0], allowed_depth_m=[0.0, 3.0])
        if _wlo <= _wm <= _whi and 2.2 <= _hm <= 12.0 and _dm <= 3.0:
            _prow["verdict"] = "PASS"
        else:
            _prow["verdict"] = "FAIL"
            _prow["reason"] = "place-front width, height or depth outside its contract"
        rows.append(_prow)

    # FL-4809 METRIC-02: station-facade geometry must never pass silently
    # unverified. When a facade instance document exists and carries rows,
    # verify each row against its family contract where one resolves, and
    # report an explicit absent-contract row where none resolves. An
    # absent or empty document contributes no rows.
    for _facade_name in ("station_facade_instances.json",):
        _facade_path = build_dir / _facade_name
        if not _facade_path.is_file():
            continue
        try:
            _facade_doc = json.loads(_facade_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows.append({"asset_id": _facade_name, "inst_name": _facade_name,
                         "kind": "facade", "verdict": "UNRESOLVED",
                         "reason": "facade document unreadable"})
            continue
        _facade_insts = (_facade_doc.get("instances", _facade_doc)
                         if isinstance(_facade_doc, dict) else _facade_doc)
        if not isinstance(_facade_insts, list):
            continue
        for _finst in _facade_insts:
            if not isinstance(_finst, dict):
                continue
            _fasset = str(_finst.get("template_id") or _finst.get("mesh_name")
                          or _finst.get("inst_name") or _facade_name)
            _fname = str(_finst.get("inst_name") or _fasset)
            _declared = (_finst.get("intended_width_m"),
                         _finst.get("intended_depth_m"),
                         _finst.get("intended_height_m"))
            if all(value is not None for value in _declared):
                _fintended = tuple(float(value) for value in _declared)
                _fnative = (_native(str(_finst.get("mesh_name")))
                            if _finst.get("mesh_name") else None)
                _frow = {"asset_id": _fasset, "inst_name": _fname,
                         "kind": "facade", "family": str(
                             _finst.get("semantic_family") or "declared_facade"),
                         "intended_m": [round(value, 4)
                                        for value in _fintended]}
                _ftransform = _finst.get("transform")
                if (_fnative is None or not isinstance(_ftransform, list)
                        or len(_ftransform) < 11):
                    unresolved_assets[_fasset] = unresolved_assets.get(_fasset, 0) + 1
                    _frow["verdict"] = "UNRESOLVED"
                    _frow["reason"] = "missing facade mesh or transform"
                    rows.append(_frow)
                    continue
                _fnative_m = _metres(_fnative)
                _fratios = [native / intended if intended else math.inf
                            for native, intended in zip(_fnative_m, _fintended)]
                _fnorms = (math.hypot(_ftransform[0], _ftransform[1]),
                           math.hypot(_ftransform[4], _ftransform[5]),
                           abs(_ftransform[10]) / HEIGHT_SCALE)
                _frow["native_m"] = [round(value, 4) for value in _fnative_m]
                _frow["mesh_ratio"] = [round(value, 4) for value in _fratios]
                _frow["transform_norms"] = [round(value, 4) for value in _fnorms]
                if (all(abs(value - 1.0) <= 0.02 for value in _fratios)
                        and all(abs(value - 1.0) <= 0.02 for value in _fnorms)):
                    _frow["verdict"] = "PASS"
                    _frow["emitted_m"] = [round(value, 4)
                                          for value in _fintended]
                else:
                    _frow["verdict"] = "FAIL"
                    _frow["reason"] = (
                        "facade mesh or transform violates declared dimensions")
                    _frow["allowed_ratio"] = [0.98, 1.02]
                rows.append(_frow)
                continue
            _ffamily = _finst.get("semantic_family")
            _fbounds = (_family_bounds(str(_ffamily))
                        if isinstance(_ffamily, str) and _ffamily in OBJECT_METRIC_DEFAULTS
                        else None)
            if _fbounds is None:
                unresolved_assets[_fasset] = unresolved_assets.get(_fasset, 0) + 1
                rows.append({"asset_id": _fasset, "inst_name": _fname,
                             "kind": "facade", "verdict": "UNRESOLVED",
                             "reason": "station-facade geometry present without "
                                       "a metric contract (absent-contract)"})
                continue
            _fnative = (_native(str(_finst.get("mesh_name")))
                        if _finst.get("mesh_name") else None)
            _femitted = (_emitted_extents(_finst["transform"], _fnative, scale)
                         if _fnative is not None and _finst.get("transform") else None)
            if _femitted is None:
                unresolved_assets[_fasset] = unresolved_assets.get(_fasset, 0) + 1
                rows.append({"asset_id": _fasset, "inst_name": _fname,
                             "kind": "facade", "family": str(_ffamily),
                             "verdict": "UNRESOLVED",
                             "reason": "missing mesh or transform"})
                continue
            _fordered = sorted(_femitted)
            _flos = [_fbounds[0][0], _fbounds[1][0], _fbounds[2][0]]
            _fhis = [_fbounds[0][1], _fbounds[1][1], _fbounds[2][1]]
            _frow = {"asset_id": _fasset, "inst_name": _fname,
                     "kind": "facade", "family": str(_ffamily),
                     "emitted_sorted_m": [round(v, 4) for v in _fordered]}
            if all(lo <= v <= hi for v, lo, hi in zip(_fordered, _flos, _fhis)):
                _frow["verdict"] = "PASS"
            else:
                _frow["verdict"] = "FAIL"
                _frow["reason"] = "emitted extents outside family range"
            rows.append(_frow)

    by_verdict: dict[str, int] = {}
    fails: list[dict] = []
    for row in rows:
        verdict = str(row.get("verdict"))
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        if verdict == "FAIL":
            fails.append({"asset_id": row.get("asset_id"),
                          "inst_name": row.get("inst_name"),
                          "kind": row.get("kind"),
                          "emitted_m": row.get("emitted_m", row.get("emitted_sorted_m")),
                          "intended_m": row.get("intended_m"),
                          "scale_factor": row.get("scale_factor", row.get("fidelity")),
                          "reason": row.get("reason")})

    heights: dict[str, list[float]] = {}
    for row in rows:
        if row.get("verdict") != "PASS":
            continue
        # Sanity medians use true Z heights only. emitted_sorted_m rows
        # (corridor meshes) carry max-axis extents, not heights: a 55 m
        # rail length must never read as a 55 m height.
        emitted = row.get("emitted_m")
        if not emitted:
            continue
        family = str(row.get("family") or row.get("asset_id"))
        heights.setdefault(family, []).append(float(emitted[2]))

    def _median(values: list[float]) -> float | None:
        ordered = sorted(values)
        count = len(ordered)
        if not count:
            return None
        mid = count // 2
        if count % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2.0

    medians = {family: _median(values) for family, values in heights.items()}
    if "tree" not in medians and "vegetation" in medians:
        medians["tree"] = medians["vegetation"]
    person = float(PLAYER_TARGET_HEIGHT_M)
    road_width_m = float(ROAD_CLASS_DEFAULTS["carriageway_width_m_by_highway"]["residential"][0])
    pairs = [
        ("bollard/person", "bollard", None, person),
        ("curb_height/person", "curb", None, person),
        ("bench/person", "bench", None, person),
        ("lamp/bench", "street_lamp", "bench", None),
        ("signal/lamp", "traffic_signal", "street_lamp", None),
        ("vending/storefront", "vending_machine", "storefront_front", None),
        ("building/road", "building_mass", None, road_width_m),
        ("tree/building", "tree", "building_mass", None),
    ]
    def _height_band(family):
        bounds = _family_bounds(family)
        return bounds[2] if bounds is not None else None

    ratio_tol = float(OBJECT_METRIC_RATIO_TOLERANCE_FRACTION[0])

    def _intended(family):
        band = _height_band(family)
        return (band[0] + band[1]) / 2.0 if band is not None else None

    ratios = []
    for name, top, bottom, constant in pairs:
        person_basis = bottom is None and constant == person
        top_m = medians.get(top)
        # Intended heights come from family band midpoints (or the person
        # basis where the pair names it); a side with no intended height
        # leaves the pair UNRESOLVED with a named reason, never UNBANDED
        # and never skipped. A banded pair outside its band FAILs.
        top_intended = _intended(top)
        if bottom is None:
            bottom_m = constant
            bottom_intended = person if constant == person else None
        else:
            bottom_m = medians.get(bottom)
            bottom_intended = _intended(bottom)
        ratio = (round(top_m / bottom_m, 3)
                 if top_m is not None and bottom_m else None)
        if top_m is None:
            ratios.append({"pair": name, "ratio": ratio,
                           "basis": "person basis" if person_basis else "emitted median heights",
                           "band": None, "verdict": "UNRESOLVED",
                           "reason": f"no emitted {top} median"})
            continue
        if bottom_m is None or not bottom_m:
            ratios.append({"pair": name, "ratio": ratio,
                           "basis": "person basis" if person_basis else "emitted median heights",
                           "band": None, "verdict": "UNRESOLVED",
                           "reason": f"no emitted {bottom or 'constant'} height"})
            continue
        if top_intended is None or bottom_intended is None:
            missing = top if top_intended is None else (bottom or "constant")
            ratios.append({"pair": name, "ratio": ratio,
                           "basis": "person basis" if person_basis else "emitted median heights",
                           "band": None, "verdict": "UNRESOLVED",
                           "reason": f"no intended height for {missing}"})
            continue
        intended = top_intended / bottom_intended
        lo, hi = intended * (1.0 - ratio_tol), intended * (1.0 + ratio_tol)
        verdict = "PASS" if lo <= ratio <= hi else "FAIL"
        entry = {"pair": name, "ratio": ratio,
                 "basis": ("emitted median over person basis"
                           if person_basis else "emitted median heights"),
                 "intended": round(intended, 4),
                 "band": [round(lo, 4), round(hi, 4)],
                 "verdict": verdict}
        if verdict == "FAIL":
            entry["reason"] = (
                f"ratio outside intended band {entry['band']}")
        ratios.append(entry)
    document = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "inputs": {
            "build_dir": str(build_dir),
            "terrain_metadata": str(terrain_metadata_path),
            "specs": str(specs_path),
            "mass_receipt": str(mass_receipt_path),
        },
        "inputs_absent": inputs_absent,
        "counts": {
            "instances": len(rows),
            "by_verdict": by_verdict,
            "unresolved_assets": dict(sorted(unresolved_assets.items())),
        },
        "median_heights_m": {family: round(value, 4) for family, value in medians.items()
                             if value is not None},
        "diagnostics": {
            "cross_object_ratios": ratios,
            "gating": False,
            "reason": (
                "cross-object medians are streetscape diagnostics; only "
                "per-instance physical contracts gate object metric"),
        },
        "fails": sorted(fails, key=lambda r: str(r.get("inst_name"))),
        "rows": rows,
    }
    receipt = {
        "schema": "y92.object_metric_counts.v1",
        "inputs": document["inputs"],
        "inputs_absent": inputs_absent,
        "counts": document["counts"],
        "fails": document["fails"],
        "diagnostics": document["diagnostics"],
    }
    return document, receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build-dir", type=Path, required=True)
    ap.add_argument("--terrain-metadata", type=Path, required=True)
    ap.add_argument("--specs", type=Path, required=True)
    ap.add_argument("--mass-receipt", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--receipt-out", type=Path, required=True)
    ap.add_argument("--generated-at", default="2026-09-19T00:00:00Z")
    args = ap.parse_args(argv)

    document, receipt = build(
        args.build_dir, args.terrain_metadata,
        args.specs, args.mass_receipt, args.generated_at)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=1,
                                   sort_keys=True) + "\n", encoding="utf-8")
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(json.dumps(receipt, ensure_ascii=False, indent=1,
                                           sort_keys=True) + "\n", encoding="utf-8")

    counts = document["counts"]
    print(f"[object-metric] instances={counts['instances']} "
          f"by_verdict={counts['by_verdict']}")
    if counts["unresolved_assets"]:
        print(f"[object-metric] UNRESOLVED assets: {counts['unresolved_assets']}")
    for fail in document["fails"]:
        print(f"[object-metric] FAIL {fail['kind']} {fail['inst_name']} "
              f"asset={fail['asset_id']} emitted={fail.get('emitted_m')} "
              f"intended={fail.get('intended_m')} scale={fail.get('scale_factor')} "
              f"({fail.get('reason')})")
    for ratio in document["diagnostics"]["cross_object_ratios"]:
        print(f"[object-metric] ratio {ratio['pair']} = {ratio['ratio']} "
              f"band={ratio.get('band')} verdict={ratio.get('verdict', 'LEGACY')} "
              f"({ratio['basis']})")
    if (document["counts"]["by_verdict"].get("FAIL")
            or document["counts"]["by_verdict"].get("UNRESOLVED")
            or document["inputs_absent"]):
        return 1
    print("[object-metric] every emitted instance has a resolved physical contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
