#!/usr/bin/env python3
"""Resolve final terrain support for planned population instances.

This stage runs after the terrain lattice is final and before mechanical
lowering.  It is the only population stage allowed to sample A3D terrain.
Lowerers consume the recorded raw bake Z and never query the world to make a
placement decision of their own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.pipeline.dispatch_population_plan import OWNERS, require_owner
from scripts.pipeline.probe_instance_terrain_z import load_height_grid, terrain_z


SCHEMA = "y92.population_ground_plan.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _world_xy(pose: dict, metadata: dict) -> tuple[float, float]:
    scale = float(metadata["content_scale"])
    return (
        (float(pose["E"]) - float(metadata["origin_easting"])) * scale
        + float(metadata.get("offset_x", 0.0)),
        (float(pose["N"]) - float(metadata["origin_northing"])) * scale
        + float(metadata.get("offset_y", 0.0)),
    )


def _sample(pose: dict, metadata: dict, grid: dict, subject: str) -> float:
    world_x, world_y = _world_xy(pose, metadata)
    value = terrain_z(grid, world_x, world_y)
    if value is None:
        raise ValueError(
            f"population subject {subject} has no terrain support at "
            f"world ({world_x:.3f}, {world_y:.3f})")
    return round(float(value), 6)


def compile_grounded_dispatch(*, dispatch_dir: Path,
                              terrain_metadata_path: Path,
                              terrain_map_path: Path,
                              out_dir: Path) -> dict[str, Path]:
    dispatch_dir = Path(dispatch_dir)
    terrain_metadata_path = Path(terrain_metadata_path)
    terrain_map_path = Path(terrain_map_path)
    out_dir = Path(out_dir)
    metadata = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
    grid = load_height_grid(terrain_map_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}
    total_placements = 0
    total_logical = 0
    source_hashes: dict[str, str] = {}

    for owner in OWNERS:
        source_path = dispatch_dir / f"population_plan.{owner}.json"
        document = json.loads(source_path.read_text(encoding="utf-8"))
        source_hash = _sha256(source_path)
        source_hashes[owner] = source_hash
        if owner != "terminal":
            require_owner(document, owner)
            for carrier in document.get("carriers", []):
                for placement in carrier.get("placements", []):
                    placement_id = str(placement["placement_id"])
                    placement["ground_bake_z"] = _sample(
                        placement["resolved_pose"], metadata, grid,
                        placement_id)
                    total_placements += 1
                    for logical in placement.get("logical_realizations", []):
                        pose = logical.get("pose") or placement["resolved_pose"]
                        logical["ground_bake_z"] = _sample(
                            pose, metadata, grid,
                            str(logical.get("logical_instance_id")
                                or placement_id))
                        total_logical += 1
        document["grounding"] = {
            "schema": SCHEMA,
            "source_dispatch_sha256": source_hash,
            "terrain_metadata_sha256": _sha256(terrain_metadata_path),
            "terrain_map_sha256": _sha256(terrain_map_path),
            "terrain_state": "final_after_surface_building_walkability",
        }
        output_path = out_dir / f"population_plan.{owner}.json"
        output_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        outputs[owner] = output_path

    manifest = {
        "schema": SCHEMA,
        "inputs": {
            "dispatch_dir": str(dispatch_dir),
            "source_dispatch_sha256_by_owner": source_hashes,
            "terrain_metadata": str(terrain_metadata_path),
            "terrain_metadata_sha256": _sha256(terrain_metadata_path),
            "terrain_map": str(terrain_map_path),
            "terrain_map_sha256": _sha256(terrain_map_path),
        },
        "outputs": {owner: str(path) for owner, path in outputs.items()},
        "counts": {
            "grounded_placements": total_placements,
            "grounded_logical_instances": total_logical,
        },
    }
    (out_dir / "population_ground_plan.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    return outputs


def require_grounded(plan: dict, expected_owner: str) -> dict:
    dispatch = require_owner(plan, expected_owner)
    grounding = plan.get("grounding")
    if not isinstance(grounding, dict) or grounding.get("schema") != SCHEMA:
        raise ValueError(
            f"{expected_owner} lowerer requires a {SCHEMA} plan")
    for carrier in plan.get("carriers", []):
        for placement in carrier.get("placements", []):
            if placement.get("ground_bake_z") is None:
                raise ValueError(
                    f"placement {placement.get('placement_id')} has no "
                    "upstream terrain support")
            if expected_owner == "linear":
                for logical in placement.get("logical_realizations", []):
                    if logical.get("ground_bake_z") is None:
                        raise ValueError(
                            f"logical instance "
                            f"{logical.get('logical_instance_id')} has no "
                            "upstream terrain support")
    return dispatch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dispatch-dir", type=Path, required=True)
    parser.add_argument("--terrain-metadata", type=Path, required=True)
    parser.add_argument("--terrain-map", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    compile_grounded_dispatch(
        dispatch_dir=args.dispatch_dir,
        terrain_metadata_path=args.terrain_metadata,
        terrain_map_path=args.terrain_map,
        out_dir=args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
