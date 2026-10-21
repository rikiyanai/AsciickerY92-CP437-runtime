#!/usr/bin/env python3
"""Build the shipped authored race circuit from the flat terrain and route recipe.

The legacy flat race asset ends after its material bank and therefore has no
editable world section.  This builder adds an empty canonical v4 world section
to a copied terrain document, applies the normal map-editor delta through the
single writer, and compiles the normal spawn/race product.  The source flat map
is never modified.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "godot_project/assets/maps/car_race_flat.a3d"
TARGET = ROOT / "godot_project/assets/maps/race_circuit_y8.a3d"
RECIPE = ROOT / "config/race_circuit_y8.map_edits.json"
PRODUCT = ROOT / "godot_project/assets/world_spawns/race_circuit_y8.fl4798_map_spawns.v1.json"
INSTANCE_PRODUCT = ROOT / "godot_project/assets/world_mesh/race_circuit_y8.fl4708_tree_instances.v1.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build() -> dict:
    raw = SOURCE.read_bytes()
    if raw[:4] != b"AS3D":
        raise RuntimeError("race circuit template is not A3D")
    header_size, patch_count = struct.unpack_from("<II", raw, 4)
    # A3DPatch is the canonical 188-byte record documented by a3d_format.py.
    terrain_end = header_size + patch_count * 188 + 256 * 512
    if len(raw) != terrain_end:
        raise RuntimeError("race circuit template unexpectedly contains a world section")

    # v4: version, instance count, player-start-present, enemy count, marker count.
    TARGET.write_bytes(raw + struct.pack("<iiiii", -4, 0, 0, 0, 0))
    edits = _load("race_circuit_map_edits", ROOT / "scripts/apply_map_edits.py")
    receipt = edits.apply_delta(
        TARGET,
        json.loads(RECIPE.read_text(encoding="utf-8")),
        runtime_terrain=TARGET,
    )
    compiler = _load("race_circuit_spawn_compiler", ROOT / "scripts/compile_fl4798_map_spawns.py")
    product = compiler.compile_spawns(TARGET, TARGET)
    PRODUCT.write_text(json.dumps(product, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    terrain_sha = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    empty_instances = {
        "$schema": "fl4708_y8_tree_instances.v1",
        "records": [],
        "other_akm_records": [],
        "source_a3d_path": str(TARGET.relative_to(ROOT)),
        "source_a3d_sha256": terrain_sha,
        "runtime_terrain_path": str(TARGET.relative_to(ROOT)),
        "runtime_terrain_sha256": terrain_sha,
        "source_terrain_sha256": terrain_sha,
        "runtime_terrain_matches_source": True,
        "runtime_consumption_blocked": False,
        "runtime_consumption_blocker": "",
        "runtime_coordinate_contract": "A3D centered world_xz; no authored tree or fixture instances",
        "source_tree_records": 0,
        "source_other_akm_records": 0,
        "source_world_records": 0,
        "source_world_format_version": -4,
        "terrain_bridge": {
            "runtime_center_offset": product["runtime_center_offset"],
            "all_height_patches_match": True,
            "patch_topology_matches": True,
            "authored_minus_current_ground_y_min": 0.0,
            "authored_minus_current_ground_y_mean": 0.0,
            "authored_minus_current_ground_y_max": 0.0,
            "tree_anchor_coverage": 0,
        },
    }
    INSTANCE_PRODUCT.write_text(
        json.dumps(empty_instances, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "map": str(TARGET.relative_to(ROOT)),
        "product": str(PRODUCT.relative_to(ROOT)),
        "instance_product": str(INSTANCE_PRODUCT.relative_to(ROOT)),
        "markers": receipt["markers_after"],
        "canonical_roundtrip": receipt["canonical_verdict"]["roundtrip_stable"],
    }


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True))
