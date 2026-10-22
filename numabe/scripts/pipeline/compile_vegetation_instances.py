#!/usr/bin/env python3
"""Compile OSM vegetation into reusable Y9 AKM mesh instances.

FL-4787 vegetation lane.

WHY THIS EXISTS
===============
``natural=tree`` is already extracted by the cartographic stamper: the run's
``*.carto_features.jsonl`` carries one record per tree with an OSM node id and
a world centroid. Until now those records were only painted as flat 2-D dots in
the terrain visual map, and the only thing that ever placed real tree meshes was
``scripts/adhoc/2026-06-01-Patch-current-OSM-output-map-...py`` -- an adhoc
script hard-wired to a single run directory. That is why a rebaked map loses
every tree.

This module makes vegetation a pipeline product with a real owner. It reads the
same evidence the stamper already wrote, matches each record to an existing Y9
vegetation AKM, and emits instances in the same schema the fixture append
already consumes. It creates no new mesh and no new format.

ASSET REUSE
===========
Meshes are chosen from the existing Y9 library only. Per-mesh scale factors are
carried over from the adhoc script, where they were measured against the actual
AKM bounds (``tree-1`` is ~44.6 local units tall, ``tree-3`` ~8.3, so a single
uniform scale would make them wildly inconsistent).

PROVENANCE
==========
Position is ``measured`` -- it is the OSM node's own coordinate. Species, mesh
choice, rotation and size jitter are ``synthesized``: OSM carries no species for
these nodes, so the choice is deterministic from the OSM id and is style only.
Vegetation never becomes collision, walkability, or spawn truth.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent

PROV_MEASURED = "measured"
PROV_SYNTHESIZED = "synthesized"

# Per-mesh scale, measured against real AKM bounds. Local AKM heights differ by
# more than 5x between tree meshes, so these are not interchangeable.
TREE_MESH_TRANSFORMS: dict[str, dict[str, float]] = {
    "osm-tree-pole.akm": {"xy": 4.00, "z": 35.40},
    "tree-1.akm":        {"xy": 0.36, "z": 4.85},
    "tree-2.akm":        {"xy": 0.30, "z": 7.00},
    "tree-3.akm":        {"xy": 0.95, "z": 26.00},
    "old-tree-1.akm":    {"xy": 0.28, "z": 7.55},
    "old-tree-2.akm":    {"xy": 0.32, "z": 7.70},
}

# Deterministic rotation of the canopy families. osm-tree-pole is excluded from
# the default mix: it is a branchless pole and reads as a post, not a tree.
CANOPY_MESHES = ["tree-1.akm", "tree-2.akm", "tree-3.akm",
                 "old-tree-1.akm", "old-tree-2.akm"]

VEGETATION_ROLES = {"tree"}


def _mesh_transform(wx: float, wy: float, wz: float, *,
                    scale_xy: float, scale_z: float, angle_rad: float) -> list[float]:
    """Column-major 4x4; translation lives in [12..14] to match world.cpp."""
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    return [
        scale_xy * c, -scale_xy * s, 0.0, 0.0,
        scale_xy * s,  scale_xy * c, 0.0, 0.0,
        0.0,           0.0,          scale_z, 0.0,
        float(wx),     float(wy),    float(wz), 1.0,
    ]


def _osm_int(osm_id) -> int:
    try:
        return int(str(osm_id).strip().split("/")[-1])
    except (TypeError, ValueError):
        return 0


def load_vegetation_features(features_path: Path) -> list[dict]:
    """Read the stamper's per-feature sidecar and keep vegetation points."""
    out: list[dict] = []
    with features_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            role = rec.get("material_role") or rec.get("feature_kind")
            if role not in VEGETATION_ROLES:
                continue
            centroid = rec.get("world_centroid") or rec.get("world_point")
            if not centroid or len(centroid) < 2:
                continue
            out.append(rec)
    return out


def compile_instances(
    features: list[dict],
    *,
    meshes_available: set[str] | None = None,
    spawn_z: float = 0.0,
) -> tuple[list[dict], dict]:
    """Turn vegetation records into A3D instance dicts.

    Z is left at ``spawn_z``; the canonical pipeline's FL-1144 terrain probe
    rewrites transform[14] with the live terrain height at append time, so this
    compiler must not invent an elevation of its own.
    """
    meshes = [m for m in CANOPY_MESHES
              if meshes_available is None or m in meshes_available]
    if not meshes:
        raise SystemExit("no Y9 vegetation meshes available to match against")

    instances: list[dict] = []
    per_mesh: dict[str, int] = {}

    for i, rec in enumerate(sorted(features, key=lambda r: str(r.get("osm_id")))):
        wx, wy = rec["world_centroid"][0], rec["world_centroid"][1]
        osm_id = _osm_int(rec.get("osm_id"))

        mesh_name = meshes[osm_id % len(meshes)]
        scales = TREE_MESH_TRANSFORMS[mesh_name]
        size_jitter = 0.90 + float(osm_id % 5) * 0.04
        angle = (osm_id % 360) * math.pi / 180.0

        instances.append({
            "mesh_name": mesh_name,
            "inst_name": f"osm_tree_{i:03d}_node_{rec.get('osm_id')}",
            "transform": _mesh_transform(
                wx, wy, spawn_z,
                scale_xy=scales["xy"] * size_jitter,
                scale_z=scales["z"] * size_jitter,
                angle_rad=angle,
            ),
            "flags": 3,
            "story_id": -1,
            "provenance": {
                "position": PROV_MEASURED,
                "position_source": f"osm {rec.get('osm_type')}/{rec.get('osm_id')} natural=tree",
                "mesh_choice": PROV_SYNTHESIZED,
                "mesh_choice_rule": "deterministic from OSM node id over the existing Y9 canopy family",
            },
        })
        per_mesh[mesh_name] = per_mesh.get(mesh_name, 0) + 1

    return instances, {"per_mesh": per_mesh, "total": len(instances)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--features", type=Path, required=True,
                    help="*.carto_features.jsonl from the run")
    ap.add_argument("--meshes-dir", type=Path, default=PROJECT_ROOT / "assets" / "meshes",
                    help="directory holding the Y9 AKM library")
    ap.add_argument("--out", type=Path, required=True, help="output vegetation_instances.json")
    ap.add_argument("--summary", type=Path, help="write a JSON receipt here")
    args = ap.parse_args(argv)

    available = None
    if args.meshes_dir and args.meshes_dir.is_dir():
        available = {p.name for p in args.meshes_dir.glob("*.akm")}
        missing = [m for m in CANOPY_MESHES if m not in available]
        if missing:
            print(f"[vegetation] note: not in {args.meshes_dir}: {missing}")

    features = load_vegetation_features(args.features)
    instances, stats = compile_instances(features, meshes_available=available)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(instances, indent=2) + "\n", encoding="utf-8")

    receipt = {
        "features_read": len(features),
        "instances": stats["total"],
        "per_mesh": stats["per_mesh"],
        "features_path": str(args.features),
        "out": str(args.out),
        "note": "transform[14] is a placeholder; the FL-1144 terrain probe sets live Z at append",
    }
    if args.summary:
        args.summary.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print(f"[vegetation] {len(features)} OSM vegetation features -> {stats['total']} instances")
    print(f"[vegetation] per mesh: {stats['per_mesh']}")
    print(f"[vegetation] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
