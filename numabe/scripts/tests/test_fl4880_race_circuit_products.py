"""FL-4880 shipped race products stay paired with the authored A3D."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / "godot_project/assets/maps/race_circuit_y8.a3d"
SPAWNS = ROOT / "godot_project/assets/world_spawns/race_circuit_y8.fl4798_map_spawns.v1.json"
INSTANCES = ROOT / "godot_project/assets/world_mesh/race_circuit_y8.fl4708_tree_instances.v1.json"


def test_race_instance_product_is_an_explicit_current_empty_manifest() -> None:
    terrain_sha = hashlib.sha256(MAP.read_bytes()).hexdigest()
    manifest = json.loads(INSTANCES.read_text(encoding="utf-8"))

    assert manifest["$schema"] == "fl4708_y8_tree_instances.v1"
    assert manifest["records"] == []
    assert manifest["other_akm_records"] == []
    assert manifest["runtime_consumption_blocked"] is False
    assert manifest["source_a3d_path"] == "godot_project/assets/maps/race_circuit_y8.a3d"
    assert manifest["runtime_terrain_path"] == manifest["source_a3d_path"]
    assert manifest["source_a3d_sha256"] == terrain_sha
    assert manifest["runtime_terrain_sha256"] == terrain_sha
    assert manifest["source_world_format_version"] == -4


def test_race_spawn_product_uses_the_same_current_authored_map() -> None:
    terrain_sha = hashlib.sha256(MAP.read_bytes()).hexdigest()
    product = json.loads(SPAWNS.read_text(encoding="utf-8"))

    assert product["source_a3d_path"] == "godot_project/assets/maps/race_circuit_y8.a3d"
    assert product["runtime_terrain_path"] == product["source_a3d_path"]
    assert product["source_a3d_sha256"] == terrain_sha
    assert product["runtime_terrain_sha256"] == terrain_sha
    markers = product["minimap_markers"]
    route = [row for row in markers if row["name"].startswith("race_path_")]
    gates = [row for row in markers if row["name"].startswith("race_gate_")]
    grid = [row for row in markers if row["name"].startswith("race_grid_")]
    assert len(route) == 11
    assert len(gates) == 11
    assert len(grid) == 4
    # FL-4880 2026-10-09 operator "widen it": the shipped circuit is 16 wide.
    assert route[0]["label"].startswith("route.v1|profile=road|width=16.000")
    assert product["counts"]["minimap_markers"] == 26
