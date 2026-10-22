"""FL-4798: the compiled map-spawn manifest keeps every authored item, sprite and enemy generator."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "scripts/compile_fl4798_map_spawns.py"
Y8_SOURCE = ROOT / "godot_project/assets/maps/game_map_y8.a3d"
Y8_RUNTIME = Y8_SOURCE
Y8_OUT = ROOT / "godot_project/assets/world_spawns/game_map_y8.fl4798_map_spawns.v1.json"
SBU = ROOT / "godot_project/assets/maps/sbu_carto_scale225_20260601.a3d"
SBU_OUT = ROOT / "godot_project/assets/world_spawns/sbu_carto_scale225_20260601.fl4798_map_spawns.v1.json"


def _load():
    spec = importlib.util.spec_from_file_location("fl4798_spawns", COMPILER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_prototype_table_matches_upstream_item_proto_indexes() -> None:
    c = _load()
    assert len(c.ITEM_PROTOTYPES) == 41
    assert c.ITEM_PROTOTYPES[3][2] == "Alpha Sword" and c.ITEM_PROTOTYPES[9][2] == "Crossbow"
    assert c.ITEM_PROTOTYPES[15][2] == "Light Helmet" and c.ITEM_PROTOTYPES[21][2] == "Meat"
    assert c.ITEM_PROTOTYPES[33][2] == "Wine" and c.ITEM_PROTOTYPES[34][2] == "Healing Potion"
    assert c.ITEM_PROTOTYPES[40][2] == "Unidentified Grey Potion"


def test_y8_manifest_keeps_every_authored_spawn_record() -> None:
    c = _load()
    m = c.compile_spawns(Y8_SOURCE, Y8_RUNTIME)
    assert m["counts"]["items"] == 154 and m["counts"]["sprites"] == 48 and m["counts"]["enemy_generators"] == 26
    assert m["counts"]["items_by_prototype"]["Wine"] == 19 and m["counts"]["sprites_by_name"]["fire.xp"] == 11
    assert all(row["prototype"] is not None for row in m["items"])
    assert m["runtime_center_offset"] == [-56.0, 176.0]
    # Coordinate contract: A3D pos -> Godot, checked on the first campfire sprite (A3D 267.35, -170.72, 358.13).
    fire = next(s for s in m["sprites"] if s["sprite"] == "fire.xp")
    assert math.isclose(fire["world_xz"][0], 267.35308837890625 + 56.0, abs_tol=1e-6)
    assert math.isclose(fire["world_xz"][1], -170.71981811523438 - 176.0, abs_tol=1e-6)
    assert math.isclose(fire["authored_godot_y"], 358.126220703125 / 16.0, abs_tol=1e-6)
    gen = m["enemy_generators"][0]
    assert gen["alive_max"] == 1 and gen["equipment"]["sword"] == 10
    assert all(g["ground_y"] is not None for g in m["enemy_generators"])
    assert json.loads(Y8_OUT.read_text()) == json.loads(json.dumps(m))
    assert m["source_a3d_path"] == m["runtime_terrain_path"] == "godot_project/assets/maps/game_map_y8.a3d"
    assert m["source_a3d_sha256"] == m["runtime_terrain_sha256"]
    assert m["counts"]["player_start"] is False


def test_sbu_manifest_carries_start_and_markers_only() -> None:
    c = _load()
    m = c.compile_spawns(SBU, SBU)
    assert m["counts"] == {**m["counts"], "items": 0, "sprites": 0, "enemy_generators": 0, "minimap_markers": 15, "player_start": True}
    start = m["player_start"]
    assert math.isclose(start["world_xz"][0], 477.4295349121094 - 488.0, abs_tol=1e-6)
    assert math.isclose(start["authored_godot_y"], 448.71478271484375 / 16.0, abs_tol=1e-6)
    assert start["ground_y"] is not None and abs(start["authored_godot_y"] - start["ground_y"]) < 2.0
    assert {mk["name"] for mk in m["minimap_markers"]} >= {"Earth_Space_Sciences"}
    assert json.loads(SBU_OUT.read_text()) == json.loads(json.dumps(m))
