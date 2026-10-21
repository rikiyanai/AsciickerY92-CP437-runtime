#!/usr/bin/env python3
"""Mechanically lower dispatcher-owned custom asset specifications."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.compile_custom_asset_plan import SCHEMA as ASSET_PLAN_SCHEMA
from scripts.pipeline.compile_facade_shell import (
    HEIGHT_SCALE,
    _load_a3d_edit,
)
from scripts.pipeline.compile_population_ground_plan import require_grounded
from scripts.pipeline.population_asset_contract import load_asset_plan
from scripts.pipeline.world_ir import WorldIRStore
from scripts.pipeline.world_ir import schema as S


LANE = "lower_custom"
# FL-4876 SIGN-WALL-01: total planar move (face slide + wall shift) a facade
# plane may take from its planned anchor; coherence's tolerance is 1.5 m.
WALL_RESIDUAL_BUDGET_M = 1.45


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:120]


def lower_population_plan(
    *, plan_path: Path, asset_plan_path: Path, world_ir_path: Path,
    population_asset_plan_path: Path,
    terrain_metadata_path: Path,
    map_path: Path | None = None,
    meshes_out: Path, output_root: Path, scale: float,
) -> dict:
    plan_path = Path(plan_path)
    asset_plan_path = Path(asset_plan_path)
    population_asset_plan_path = Path(population_asset_plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    dispatch = require_grounded(plan, "custom")
    asset_plan = json.loads(asset_plan_path.read_text(encoding="utf-8"))
    if asset_plan.get("schema") != ASSET_PLAN_SCHEMA:
        raise ValueError(
            f"custom lowerer requires {ASSET_PLAN_SCHEMA}, got "
            f"{asset_plan.get('schema')!r}")
    expected_plan_sha = (asset_plan.get("inputs") or {}).get(
        "custom_population_plan_sha256")
    source_dispatch_sha = (plan.get("grounding") or {}).get(
        "source_dispatch_sha256")
    if expected_plan_sha != source_dispatch_sha:
        raise ValueError("custom asset plan was compiled from another dispatch")
    asset_by_placement = {
        str(row["placement_id"]): row for row in asset_plan.get("rows", [])}
    if len(asset_by_placement) != len(asset_plan.get("rows", [])):
        raise ValueError("custom asset plan contains duplicate placement ids")
    population_assets, population_by_placement, _ = load_asset_plan(
        population_asset_plan_path, meshes_out)
    if ((population_assets.get("inputs") or {}).get("dispatch_sha256") or {}).get(
            "custom") != source_dispatch_sha:
        raise ValueError(
            "population asset plan was authored from another custom dispatch")

    from scripts.pipeline.plan_spatial_provenance import verify as verify_plan
    verified = verify_plan(
        plan, world_ir_path,
        terrain_metadata_path=terrain_metadata_path,
        world_units_per_metre=scale)
    spatial_provenance = {
        "canonical_world_ir_sha256": verified.get(
            "canonical_world_ir_sha256"),
        "world_units_per_metre": verified.get("world_units_per_metre"),
    }
    if terrain_metadata_path is not None:
        spatial_provenance["terrain_metadata_sha256"] = verified.get(
            "terrain_metadata_sha256")

    store = WorldIRStore.load(Path(world_ir_path))
    canonical_ids = {row["feature_id"] for row in store.features}
    metadata = json.loads(Path(terrain_metadata_path).read_text(
        encoding="utf-8"))
    origin_e = float(metadata["origin_easting"])
    origin_n = float(metadata["origin_northing"])
    offset_x = float(metadata.get("offset_x", 0.0))
    offset_y = float(metadata.get("offset_y", 0.0))
    from scripts.pipeline.extruded_wall import Terrain
    from scripts.pipeline.compile_sign_planes import (
        MASS_WALL_BASIS, mass_wall_ids, paneled_buildings, wall_distance_m)
    wall_terrain = (Terrain(map_path) if map_path is not None and Path(map_path).is_file()
                    else None)
    # footprint rings (world units) and the hosts whose visible wall is the footprint face
    rings_en = {}
    for row in store.features:
        geom = row.get("geometry") or {}
        if row.get("semantic_class") == "building_footprint" and geom.get("kind") == "polygon":
            ring = [q[:2] for q in geom["coordinates"]]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]
            rings_en[row["feature_id"]] = ring
    rings_world = {bid: [((e - origin_e) * scale + offset_x, (n - origin_n) * scale + offset_y) for e, n in ring]
                   for bid, ring in rings_en.items()}
    mass_ids = mass_wall_ids(rings_en, Path(meshes_out), paneled_buildings(Path(output_root)))

    def world_xy(easting: float, northing: float) -> tuple[float, float]:
        return ((easting - origin_e) * scale + offset_x,
                (northing - origin_n) * scale + offset_y)

    def ground_z(placement: dict, height_m: float) -> float:
        return (float(placement["ground_bake_z"])
                + max(0.0, height_m) * scale * HEIGHT_SCALE)

    meshes_out = Path(meshes_out)
    output_root = Path(output_root)
    meshes_out.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    instances = []
    seen = set()
    attached = 0
    with_text = 0

    for carrier in plan.get("carriers", []):
        for placement in carrier.get("placements", []):
            placement_id = str(placement["placement_id"])
            if placement_id in seen:
                raise ValueError(
                    f"custom placement appears twice: {placement_id}")
            seen.add(placement_id)
            specification = asset_by_placement.get(placement_id)
            if specification is None:
                raise ValueError(
                    f"custom placement {placement_id} has no asset specification")
            pose = placement["resolved_pose"]
            frame = placement["resolved_frame"]
            tangent = frame["tangent"]
            normal = frame["normal"]
            parent = placement["planned_parent_feature_id"]
            if parent not in canonical_ids:
                raise ValueError(
                    f"custom placement {placement_id} has unknown parent "
                    f"{parent!r}")
            width = float(specification["width_m"])
            height = float(specification["height_m"])
            depth = float(specification["depth_m"])
            exact_dimensions = (placement.get("asset_contract") or {}).get(
                "exact_dimensions_m")
            if exact_dimensions != {
                    "width_m": width, "depth_m": depth,
                    "height_m": height}:
                raise ValueError(
                    f"custom placement {placement_id} asset plan differs "
                    "from its dispatched exact physical contract")
            text = specification.get("block_text")
            authored_asset = population_by_placement.get(placement_id)
            if authored_asset is None or authored_asset.get("owner") != "custom":
                raise ValueError(
                    f"custom placement {placement_id} has no preauthored AKM")
            mesh_name = str(authored_asset["mesh_name"])
            rendered_text = authored_asset.get("rendered_text")
            if rendered_text != text:
                raise ValueError(
                    f"custom placement {placement_id} text differs from its "
                    "preauthored AKM")
            with_text += int(bool(rendered_text))
            easting, northing = float(pose["E"]), float(pose["N"])
            world_x, world_y = world_xy(easting, northing)
            tx, ty = float(tangent[0]), float(tangent[1])
            nx, ny = float(normal[0]), float(normal[1])
            # FL-4876 SIGN-WALL-01: a facade plane stands on the extruded wall
            # it faces, which the FL-4814 bake rule leaves 0.5-1.7 m inside
            # the canonical face (build_reg_29: 59/59 planes floating). The
            # shift is measured on the baked map and capped so that, with the
            # FACE-SPAN-01 slide, the plane stays within spatial coherence's
            # 1.5 m attachment tolerance of its planned anchor.
            # FL-4876 CARD-FIX-01: the wall is the one the player SEES (compile_sign_planes.wall_distance_m):
            # the footprint face for a host drawn by a mass mesh or carrying a facade panel, the terrain wall
            # otherwise. build_reg_87 moved all 15 slabs to the terrain wall foot, 0.2-0.6 m inside mass-mesh hosts.
            wall_shift_m = 0.0
            wall_basis = None
            if (wall_terrain is not None
                    and (placement.get("attachment") or {}).get("anchor_kind") == "building_face"):
                slide = abs(float((((specification.get("dimension_derivation") or {})
                                    .get("face_fit") or {}).get("slide_m")) or 0.0))
                budget = math.sqrt(max(0.0, WALL_RESIDUAL_BUDGET_M ** 2 - slide ** 2))
                d_vis, wall_basis, _host = wall_distance_m(
                    wall_terrain, rings_world, mass_ids, world_x, world_y, nx, ny, scale)
                # the slab's back face rests on the visible wall (its centre stands depth/2 out), so none of the
                # board is inside the wall that hides it
                if d_vis is not None and wall_basis.startswith(MASS_WALL_BASIS):
                    wall_shift_m = max(-budget, min(budget, d_vis)) + depth / 2.0
                elif d_vis is not None and d_vis < 0.0:
                    wall_shift_m = max(d_vis, -budget) + depth / 2.0
                world_x += nx * wall_shift_m * scale
                world_y += ny * wall_shift_m * scale
            transform = [
                tx, ty, 0.0, 0.0,
                nx, ny, 0.0, 0.0,
                0.0, 0.0, HEIGHT_SCALE, 0.0,
                world_x, world_y,
                ground_z(placement, float(placement.get("height_m") or 0.0)),
                1.0,
            ]
            attachment = placement.get("attachment") or {}
            is_attached = attachment.get("anchor_kind") == "building_face"
            attached += int(is_attached)
            instance_name = _sanitize(f"{LANE}_{placement_id}")
            instance = {
                "placement_id": placement_id,
                "subject": specification["label"],
                "semantic_family": specification["semantic_family"],
                "feature_id": placement["planned_feature_id"],
                "parent": parent,
                "attachment_id": attachment.get("attachment_id"),
                "mesh_name": mesh_name,
                "inst_name": instance_name,
                "transform": transform,
                "flags": 3,
                "story_id": -1,
                "facade_attached": is_attached,
                "plane_width_m": width,
                "wall_shift_m": round(wall_shift_m, 3),
                "wall_basis": wall_basis,
                "plane_depth_m": depth,
                "plane_height_m": height,
                "block_text": rendered_text,
                "rendered_text": rendered_text,
                "asset_plan_schema": ASSET_PLAN_SCHEMA,
                "E": easting,
                "N": northing,
            }
            instances.append(instance)
            store.add_representation(S.generated_representation(
                representation_id=f"rep:{LANE}:{_sanitize(placement_id)}",
                feature_id=placement["planned_feature_id"],
                target="mesh_asset",
                product={
                    "mesh": mesh_name,
                    "instance": instance_name,
                    "transform": transform,
                    "placement_id": placement_id,
                    "asset_plan_schema": ASSET_PLAN_SCHEMA,
                },
                lowering={
                    "compiler": "scripts/pipeline/lower_plan_custom.py",
                    "provenance": "mechanical_from_custom_asset_plan",
                },
                authority_claim=S.AUTHORITY_PRESENTATION_ONLY))

    missing_specs = sorted(set(asset_by_placement) - seen)
    if missing_specs:
        raise ValueError(
            f"custom asset plan contains undispatched placements: {missing_specs}")
    store.assert_valid()
    store.save(output_root / f"{LANE}.world_ir.json")
    instances_path = output_root / f"{LANE}_instances.json"
    instances_path.write_text(
        json.dumps(instances, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")

    if map_path is not None:
        editor = _load_a3d_edit()
        pre, version, existing, start, generators, markers = \
            editor.read_a3d_sections(map_path)
        additions = [editor.a3d_format.A3DInstance(**{
            key: row[key] for key in
            ("mesh_name", "inst_name", "transform", "flags", "story_id")
        }) for row in instances]
        if ({row.inst_name for row in existing}
                & {row.inst_name for row in additions}):
            raise ValueError("duplicate custom instances")
        editor.write_a3d_sections(
            map_path, pre, version, [*existing, *additions], start,
            generators, markers)

    receipt = {
        "lane": LANE,
        "dispatch": dispatch,
        "spatial_provenance": spatial_provenance,
        "asset_plan": {
            "path": str(asset_plan_path),
            "sha256": _sha256(asset_plan_path),
            "schema": ASSET_PLAN_SCHEMA,
        },
        "population_asset_plan": {
            "path": str(population_asset_plan_path),
            "sha256": _sha256(population_asset_plan_path),
        },
        "mechanical_accounting": {
            "planned_placements": dispatch["placements_for_owner"],
            "consumed_placements": len(seen),
            "emitted_instances": len(instances),
            "semantic_validation_owner":
                "compile_spatial_coherence_receipt.py",
        },
        "instances_emitted": len(instances),
        "with_block_text": with_text,
        "blank_sign_planes": len(instances) - with_text,
        "attached_to_building_face": attached,
        "unattached_count": len(instances) - attached,
        "asset_specification_owner": "compile_custom_asset_plan.py",
    }
    (output_root / f"{LANE}_lowering_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--asset-plan", type=Path, required=True)
    parser.add_argument("--population-asset-plan", type=Path, required=True)
    parser.add_argument("--world-ir", type=Path, required=True)
    parser.add_argument("--terrain-metadata", type=Path, required=True)
    parser.add_argument("--map-path", type=Path)
    parser.add_argument("--meshes-out", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scale", type=float, required=True)
    args = parser.parse_args(argv)
    lower_population_plan(
        plan_path=args.plan, asset_plan_path=args.asset_plan,
        population_asset_plan_path=args.population_asset_plan,
        world_ir_path=args.world_ir,
        terrain_metadata_path=args.terrain_metadata,
        map_path=args.map_path,
        meshes_out=args.meshes_out, output_root=args.output_root,
        scale=args.scale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
