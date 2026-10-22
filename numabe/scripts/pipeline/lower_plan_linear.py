#!/usr/bin/env python3
"""Lower population-plan placements for continuous/linear domains into Classic geometry.

Lane: NUMABE-LOWER-LINEAR-01. Consumes a dispatcher-owned linear slice only.
The dispatcher, not this lowerer, assigns placements to the five linear
domains (road_cross_section, property_frontage, rail_edge, utility_network,
vegetation). Every received placement already contains its exact logical
instances, resolved poses, topology, and physical envelopes. This module only
materializes asset-manifest references and resolved transforms into A3D.

- Continuous runs become the exact preplanned segment or point realizations.
- Wire topology arrives as explicit endpoint pairs ordered by canonical
  carrier chainage; this lowerer never joins neighboring placements.
- Carriageway/sidewalk surface modules arrive as pre-authored manifest assets.
  Terrain-cell paint stays with the surface-plan stamper; this lowerer never
  repaints a cell.

Asset geometry, placement length, attachment, span connectivity, and
orientation are immutable upstream inputs.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.compile_facade_shell import HEIGHT_SCALE, _load_a3d_edit
from scripts.pipeline.compile_population_ground_plan import require_grounded
from scripts.pipeline.population_asset_contract import load_asset_plan
from scripts.pipeline.world_ir import WorldIRStore
from scripts.pipeline.world_ir import schema as S

LANE = "lower_linear"

EVIDENCE_PROVENANCE = {
    "observation": S.PROVENANCE_MEASURED,
    "osm": S.PROVENANCE_DERIVED,
    "rule": S.PROVENANCE_SYNTHESIZED,
}

def lower_population_plan(
    *,
    plan_path: Path,
    asset_plan_path: Path,
    world_ir_path: Path,
    terrain_metadata_path: Path,
    map_path: Path | None,
    meshes_out: Path,
    output_root: Path,
    scale: float,
    rules_path: Path | None = None,
) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    dispatch = require_grounded(plan, "linear")
    population_assets, _, asset_by_logical = load_asset_plan(
        asset_plan_path, meshes_out)
    source_dispatch_sha = (plan.get("grounding") or {}).get(
        "source_dispatch_sha256")
    if ((population_assets.get("inputs") or {}).get("dispatch_sha256") or {}).get(
            "linear") != source_dispatch_sha:
        raise ValueError(
            "population asset plan was authored from another linear dispatch")
    # Spatial generation pin (REVIEW v4 re-anchor law): fail closed when
    # the plan was compiled against different bytes than these inputs.
    from scripts.pipeline.plan_spatial_provenance import verify as _verify_plan_world
    _verified_pin = _verify_plan_world(
        plan, world_ir_path,
        terrain_metadata_path=terrain_metadata_path,
        rules_path=rules_path,
        world_units_per_metre=scale)
    _verified_echo = {
        "canonical_world_ir_sha256": _verified_pin.get("canonical_world_ir_sha256"),
        "world_units_per_metre": _verified_pin.get("world_units_per_metre"),
    }
    if terrain_metadata_path is not None:
        _verified_echo["terrain_metadata_sha256"] = _verified_pin.get(
            "terrain_metadata_sha256")
    if rules_path is not None:
        _verified_echo["rules_sha256"] = _verified_pin.get("rules_sha256")
    meta = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
    origin_e = float(meta["origin_easting"])
    origin_n = float(meta["origin_northing"])
    offset_x = float(meta.get("offset_x", 0.0))
    offset_y = float(meta.get("offset_y", 0.0))
    z_mode = "ground_plan"

    store = WorldIRStore.load(world_ir_path)
    _canonical_fids = {f["feature_id"] for f in store.features}

    def _resolve_parents(placement):
        """Echo the canonical parent selected before lowering."""
        parent = placement.get("planned_parent_feature_id")
        if parent not in _canonical_fids:
            raise ValueError(
                f"linear placement {placement.get('placement_id')} has an "
                f"unknown planned parent {parent!r}")
        return [parent], "dispatch"
    meshes_out.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    instances: list[dict] = []
    consumed: set[str] = set()
    by_module: Counter = Counter()
    segments = 0
    spans = 0
    akm_files: set[str] = set()
    mesh_cache: dict[str, str] = {}

    def world_xy(e, n):
        return ((e - origin_e) * scale + offset_x, (n - origin_n) * scale + offset_y)

    def base_z(placement, logical, height_m):
        ground = logical.get("ground_bake_z", placement["ground_bake_z"])
        return (float(ground)
                + float(height_m or 0.0) * scale * HEIGHT_SCALE)

    def mesh_for(logical):
        logical_id = str(logical["logical_instance_id"])
        row = asset_by_logical.get(logical_id)
        if row is None or row.get("owner") != "linear":
            raise ValueError(
                f"linear logical instance {logical_id} has no preauthored AKM")
        stem = str(row["mesh_name"])
        mesh_cache[logical_id] = stem
        akm_files.add(stem)
        return stem

    emitted_logical_ids: set[str] = set()

    def emit_logical(placement, carrier, logical, mesh, x, y, z, yaw_deg,
                     extra=None):
        logical_id = str(logical["logical_instance_id"])
        if logical_id in emitted_logical_ids:
            raise ValueError(f"logical instance emitted twice: {logical_id}")
        emitted_logical_ids.add(logical_id)
        source_ids = [str(value) for value in
                      logical.get("source_placement_ids", [])]
        if not source_ids:
            raise ValueError(f"logical instance {logical_id} has no sources")
        consumed.update(source_ids)
        placement_id = placement["placement_id"]
        module_id = placement["module_id"]
        evidence = placement.get("evidence", {}) or {}
        provenance = EVIDENCE_PROVENANCE.get(
            evidence.get("kind"), S.PROVENANCE_SYNTHESIZED)
        parent = _resolve_parents(placement)[0][0]
        yaw = math.radians(float(yaw_deg))
        c, s = math.cos(yaw), math.sin(yaw)
        transform = [c, s, 0.0, 0.0, -s, c, 0.0, 0.0,
                     0.0, 0.0, HEIGHT_SCALE, 0.0, x, y, z, 1.0]
        inst_name = "linear_" + re.sub(
            r"[^A-Za-z0-9_.-]", "_", logical_id)
        pose = logical.get("pose") or placement["resolved_pose"]
        row = {
            "placement_id": placement_id,
            "source_placement_ids": source_ids,
            "logical_instance_id": logical_id,
            "logical_instance_kind": logical["kind"],
            "module_id": module_id,
            "variant": placement.get("variant"),
            "carrier_id": carrier["carrier_id"],
            "feature_id": placement["planned_feature_id"],
            "parent": parent,
            "mesh_name": mesh,
            "inst_name": inst_name,
            "transform": transform,
            "flags": 3,
            "story_id": -1,
            "E": float(pose["E"]),
            "N": float(pose["N"]),
            "height_m": placement.get("height_m", 0.0),
            "yaw_deg": float(yaw_deg),
            "evidence_kind": evidence.get("kind"),
            "attachment_id": (placement.get("attachment") or {}).get(
                "attachment_id"),
            "provenance": {"position": provenance, "identity": provenance,
                           "collision": "none"},
        }
        if extra:
            row.update(extra)
        instances.append(row)
        by_module[module_id] += 1
        store.add_representation(S.generated_representation(
            representation_id="rep:linear:" + re.sub(
                r"[^A-Za-z0-9_.-]", "_", logical_id),
            feature_id=placement["planned_feature_id"],
            target="mesh_asset",
            product={
                "mesh": mesh,
                "instance": inst_name,
                "transform": transform,
                "logical_instance_id": logical_id,
                "source_placement_ids": source_ids,
                "module_id": module_id,
                **(extra or {}),
            },
            lowering={
                "compiler": "scripts/pipeline/lower_plan_linear.py",
                "lane": LANE,
                "plan": str(plan_path),
                "placement_id": placement_id,
                "evidence_kind": evidence.get("kind"),
                "provenance": provenance,
            },
            authority_claim="presentation_only"))
        return row

    run_placements_consumed = 0
    run_total_length_m = 0.0
    run_segments_emitted = 0
    plan_run_total_length_m = 0.0
    plan_rule_run_total_length_m = 0.0
    plan_run_count = 0
    for carrier in plan["carriers"]:
        groups: dict[str, list[dict]] = {}
        run_groups: dict[str, list[dict]] = {}
        for placement in carrier["placements"]:
            run = placement.get("run")
            if run is not None:
                plan_run_count += 1
                plan_run_total_length_m += float(run.get("length_m", 0.0))
                if (placement.get("evidence", {}) or {}).get("kind") == "rule":
                    plan_rule_run_total_length_m += float(run.get("length_m", 0.0))
            module_id = placement.get("module_id")
            if run is not None:
                run_groups.setdefault(module_id, []).append(placement)
            else:
                groups.setdefault(module_id, []).append(placement)
        for module_id, rows in run_groups.items():
            for placement in rows:
                placement_id = placement["placement_id"]
                logical_instances = placement.get("logical_realizations")
                if not isinstance(logical_instances, list) or not logical_instances:
                    raise ValueError(
                        f"run placement {placement_id} has no upstream logical "
                        "realizations")
                run_length_m = float(
                    placement["asset_physical_envelope"][
                        "longitudinal_extent_m"])
                consumed.add(placement_id)
                run_placements_consumed += 1
                run_total_length_m += run_length_m
                for logical in logical_instances:
                    pose = logical.get("pose")
                    if not isinstance(pose, dict):
                        raise ValueError(
                            f"run logical instance "
                            f"{logical.get('logical_instance_id')} has no pose")
                    envelope = logical.get("physical_envelope") or {}
                    if float(envelope.get(
                            "max_longitudinal_overhang_m", -1.0)) != 0.0:
                        raise ValueError(
                            f"logical instance "
                            f"{logical.get('logical_instance_id')} permits "
                            "asset overhang")
                    tile_length_m = float(
                        logical.get("length_m")
                        or envelope.get("longitudinal_extent_m") or 0.0)
                    if tile_length_m <= 0.0:
                        raise ValueError(
                            f"logical instance "
                            f"{logical.get('logical_instance_id')} has no "
                            "physical extent")
                    x, y = world_xy(float(pose["E"]), float(pose["N"]))
                    z = base_z(
                        placement, logical, placement.get("height_m", 0.0))
                    mesh = mesh_for(logical)
                    emit_logical(
                        placement, carrier, logical, mesh, x, y, z,
                        float(pose.get("yaw_deg", 0.0)),
                        {"run_record": True,
                         "run_id": placement_id,
                         "tile_length_m": tile_length_m,
                         "run_length_m": run_length_m,
                         "plan_run_length_m": float(
                             placement["run"]["length_m"])})
                    run_segments_emitted += 1

        for module_id, rows in groups.items():
            for placement in rows:
                logical_instances = placement.get("logical_realizations")
                if not isinstance(logical_instances, list):
                    raise ValueError(
                        f"linear placement {placement.get('placement_id')} "
                        "has no upstream logical realizations")
                for logical in logical_instances:
                    kind = logical.get("kind")
                    pose = logical.get("pose")
                    if kind in {"point", "unit", "hardware"}:
                        if not isinstance(pose, dict):
                            raise ValueError(
                                f"logical instance {logical.get('logical_instance_id')} "
                                "has no resolved pose")
                        x, y = world_xy(float(pose["E"]), float(pose["N"]))
                        height_above_ground = (0.0 if kind == "hardware"
                                               else placement.get("height_m", 0.0))
                        z = base_z(placement, logical, height_above_ground)
                        yaw_deg = float(pose.get("yaw_deg", 0.0))
                    if kind == "point":
                        mesh = mesh_for(logical)
                        emit_logical(placement, carrier, logical, mesh,
                                     x, y, z, yaw_deg)
                    elif kind == "unit":
                        envelope = logical.get("physical_envelope") or {}
                        if float(envelope.get(
                                "max_longitudinal_overhang_m", -1.0)) != 0.0:
                            raise ValueError(
                                f"logical instance {logical.get('logical_instance_id')} "
                                "permits asset overhang")
                        length_m = float(logical["length_m"])
                        mesh = mesh_for(logical)
                        emit_logical(
                            placement, carrier, logical, mesh, x, y, z,
                            yaw_deg,
                            {"segment_m": length_m,
                             "length_basis": logical["length_basis"]})
                        segments += 1
                    elif kind == "hardware":
                        hardware = logical.get("hardware")
                        height_m = float(logical["height_m"])
                        stem = mesh_for(logical)
                        emit_logical(
                            placement, carrier, logical, stem, x, y, z,
                            yaw_deg,
                            {"hardware": hardware,
                             "hardware_height_m": height_m})
                    elif kind == "span":
                        start = logical["start"]
                        end = logical["end"]
                        length_m = float(logical["length_m"])
                        midpoint_e = (float(start["E"]) + float(end["E"])) / 2.0
                        midpoint_n = (float(start["N"]) + float(end["N"])) / 2.0
                        x, y = world_xy(midpoint_e, midpoint_n)
                        z = base_z(placement, logical, 0.0)
                        nominal = float(logical["nominal_height_m"])
                        stem = mesh_for(logical)
                        source_ids = logical["source_placement_ids"]
                        emit_logical(
                            placement, carrier, logical, stem, x, y, z,
                            float(logical["yaw_deg"]),
                            {"span_record": True,
                             "span_from": source_ids[0],
                             "span_to": source_ids[1],
                             "span_m": length_m,
                             "span_nominal_height_m": nominal,
                             "span_min_height_m": float(
                                 logical["minimum_height_m"]),
                             "topology_basis": logical["topology_basis"]})
                        spans += 1
                    else:
                        raise ValueError(
                            f"unknown upstream logical kind {kind!r} for "
                            f"{logical.get('logical_instance_id')}")

    for row in instances:
        if not (meshes_out / row["mesh_name"]).stat().st_size:
            raise ValueError(f"empty asset: {row['mesh_name']}")
    if len({r["inst_name"] for r in instances}) != len(instances):
        raise ValueError("duplicate instance names")
    accounted = set(consumed)
    owned_ids = [pl["placement_id"] for c in plan["carriers"]
                 for pl in c["placements"]]
    missing = [pid for pid in owned_ids if pid not in accounted]
    if missing:
        raise ValueError(
            f"{len(missing)} owned placements have no emitted logical instance")
    # Structural stamping: every emitted row carries its plan identity.
    # A placement missing from the plan is an error, never a default.
    _cc_by_id = {c.get("carrier_id"): c.get("carrier_class", "")
                 for c in plan.get("carriers", [])}
    _ev_by_pid = {}
    for _carrier in plan.get("carriers", []):
        for _placement in _carrier.get("placements", []):
            _ev_by_pid[_placement.get("placement_id")] = (
                _carrier.get("carrier_class", ""),
                ((_placement.get("evidence", {}) or {}).get("kind", "rule")))
    for row in instances:
        _pid = row.get("placement_id")
        if _pid not in _ev_by_pid:
            raise ValueError(
                f"structural stamping miss: {row.get('inst_name')} "
                f"placement {_pid!r} not in plan")
        _cc, _ev = _ev_by_pid[_pid]
        row["carrier_class"] = _cc if _cc else _cc_by_id.get(
            row.get("carrier_id"), "")
        row["evidence_kind"] = _ev

    store.assert_valid()
    store.save(output_root / f"{LANE}.world_ir.json")
    instances_path = output_root / f"{LANE}_instances.json"
    instances_path.write_text(json.dumps(instances, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")

    if map_path is not None:
        editor = _load_a3d_edit()
        pre, version, existing, start, gens, markers = editor.read_a3d_sections(map_path)
        additions = [editor.a3d_format.A3DInstance(
            **{key: row[key] for key in ("mesh_name", "inst_name", "transform", "flags", "story_id")})
            for row in instances]
        if {r.inst_name for r in existing} & {r.inst_name for r in additions}:
            raise ValueError("duplicate linear instances against map")
        editor.write_a3d_sections(map_path, pre, version, [*existing, *additions],
                                  start, gens, markers)
        actual = editor.read_a3d_sections(map_path)[2]
        if not {r.inst_name for r in additions} <= {r.inst_name for r in actual}:
            raise ValueError("linear instance readback failed")

    for row in instances:
        if not (meshes_out / row["mesh_name"]).stat().st_size:
            raise ValueError("empty linear asset")
    receipt = {
        "spatial_provenance": _verified_echo,
        "lane": LANE,
        "dispatch": dispatch,
        "stage": "lowered",
        "z_mode": z_mode,
        "origin": {"easting": origin_e, "northing": origin_n,
                   "offset_x": offset_x, "offset_y": offset_y},
        "scale": scale,
        "mechanical_accounting": {
            "planned_placements": dispatch["placements_for_owner"],
            "consumed_placements": len(consumed),
            "emitted_instances": len(instances),
            "semantic_validation_owner": "compile_spatial_coherence_receipt.py",
        },
        "counts": {
            "placements_assigned": dispatch["placements_for_owner"],
            "placements_consumed": len(consumed),
            "instances_emitted": len(instances),
            "akm_files_written": 0,
            "akm_files_referenced": len(akm_files),
            "continuous_run_segments": segments,
            "span_segments": spans,
            "placements_per_module": dict(sorted(by_module.items())),
            "logical_instances_emitted": len(emitted_logical_ids),
        },
        "runs": {
            "run_placements_consumed": run_placements_consumed,
            "run_segments_emitted": run_segments_emitted,
            "run_total_length_m": round(run_total_length_m, 3),
            "plan_run_count": plan_run_count,
            "plan_run_total_length_m": round(plan_run_total_length_m, 3),
            "plan_rule_run_total_length_m": round(plan_rule_run_total_length_m, 3),
        },
        "population_asset_plan": str(Path(asset_plan_path).resolve()),
    }
    (output_root / f"{LANE}_lowering_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[{LANE}] consumed={len(consumed)} instances={len(instances)} "
          f"akms={len(akm_files)} segments={segments} spans={spans} "
          f"runs={run_placements_consumed} run_segs={run_segments_emitted} "
          f"run_m={round(run_total_length_m, 1)}")
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lower linear-domain plan placements to Classic geometry.")
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--asset-plan", type=Path, required=True)
    ap.add_argument("--world-ir", type=Path, required=True)
    ap.add_argument("--terrain-metadata-path", type=Path, required=True)
    ap.add_argument("--map-path", type=Path, default=None)
    ap.add_argument("--meshes-out", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--scale", type=float, required=True)
    ap.add_argument("--rules-path", type=Path, default=None)
    args = ap.parse_args(argv)
    lower_population_plan(
        plan_path=args.plan, asset_plan_path=args.asset_plan,
        world_ir_path=args.world_ir,
        terrain_metadata_path=args.terrain_metadata_path,
        map_path=args.map_path,
        meshes_out=args.meshes_out, output_root=args.output_root,
        scale=args.scale, rules_path=args.rules_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
