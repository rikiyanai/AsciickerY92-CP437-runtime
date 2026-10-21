"""Contracts for the operator-requested P1 submerged hand fixture."""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORLD_PATH = ROOT / "godot_project/scripts/World.gd"
PRODUCT_PATH = ROOT / "godot_project/render/product/cell_owner_product.glsl"
BAKER_PATH = ROOT / "scripts/bake_akm_to_static_world_faces.py"
A3D_PATH = ROOT / "godot_project/assets/maps/game_map_y8.a3d"
HAND_AKM_PATH = ROOT / "assets/meshes/incoming/female_hand.akm"
HAND_FACES_PATH = (
    ROOT / "godot_project/assets/world_mesh/female_hand_statue_mossy_faces.json"
)


def _load_baker():
    spec = importlib.util.spec_from_file_location("static_face_baker", BAKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_negative_y_up_is_a_proper_half_turn_for_positions_and_normals() -> None:
    baker = _load_baker()
    assert baker._up_axis_remap(2.0, -7.0, 3.0, "-y") == (2.0, 7.0, -3.0)
    assert baker._up_axis_remap(0.0, -1.0, 0.0, "-y") == (0.0, 1.0, -0.0)
    source = BAKER_PATH.read_text()
    assert 'choices=["+y", "-y", "+z", "-z", "+x", "-x"]' in source


def test_hand_is_one_retained_fixture_at_the_exact_p1_player_xz() -> None:
    source = WORLD_PATH.read_text()
    assert "STATIC_WORLD_FEMALE_HAND_ANCHOR_XZ := Vector2(127.110000610352, -3.92300009727478)" in source
    assert "STATIC_WORLD_FEMALE_HAND_SINK := 5.5" in source
    assert "STATIC_WORLD_FEMALE_HAND_FACE_COUNT := 8082" in source
    assert "hand_faces != STATIC_WORLD_FEMALE_HAND_FACE_COUNT" in source
    assert "FEMALE_HAND_PUBLICATION_FAILED" in source
    assert "--mode statue --target-size 30.0 --moss-threshold 0.15 --up-axis -y" in source
    assert source.count('register_asset("female_hand_statue"') == 1
    assert source.count('add_instance(\n\t\t\t"female_hand_statue"') == 1
    assert "_append_static_world_female_hand(face_rows, loader, scene_db)" in source

    # The visible ASCII world owns this fixture through retained faces. A direct
    # GLB/PackedScene/MeshInstance owner would be a second, non-rendered path.
    hand_block = source.split("func _append_static_world_female_hand(", 1)[1]
    hand_block = hand_block.split("\nfunc ", 1)[0]
    for forbidden in (".glb", "PackedScene", "MeshInstance", "load("):
        assert forbidden not in hand_block


def test_baked_hand_matches_pieta_scale_and_material_grammar() -> None:
    assert HAND_AKM_PATH.exists()
    assert HAND_FACES_PATH.exists()
    doc = json.loads(HAND_FACES_PATH.read_text())
    assert doc["source"] == "female_hand.akm"
    assert doc["mode"] == "statue"
    assert doc["face_count"] == 8082
    assert len(doc["rows"]) == doc["face_count"] * 16
    assert doc["mats"] == {"rock": 210, "moss": 211}
    assert abs(float(doc["dimensions_xyz"][1]) - 30.0) < 0.001
    assert 12.0 < float(doc["dimensions_xyz"][0]) < 17.0
    assert 8.0 < float(doc["dimensions_xyz"][2]) < 13.0

    materials = [int(doc["rows"][i]) for i in range(3, len(doc["rows"]), 16)]
    moss_fraction = materials.count(211) / len(materials)
    assert set(materials) == {210, 211}
    assert 0.05 < moss_fraction < 0.60


def test_hand_world_bounds_cross_the_authoritative_p1_water_surface() -> None:
    # Reuse the repository's byte-level A3D parser—the same ownership oracle
    # used by the geometry parity gate—rather than copying a terrain height.
    sys.path.insert(0, str(ROOT / "scripts"))
    from fl4359_geometry_parity_gate import Heightfield, WATER_Y

    doc = json.loads(HAND_FACES_PATH.read_text())
    rows = doc["rows"]
    local_heights = [float(rows[i]) for i in range(1, len(rows), 4)]
    terrain = Heightfield(A3D_PATH)
    x, z = 127.110000610352, -3.92300009727478
    ground_y = terrain.height(x, z)
    assert ground_y is not None
    cell = terrain.cell_from_world(x, z)
    assert terrain.cell_is_water(cell)

    anchor_y = float(ground_y) - 5.5
    world_min_y = anchor_y + min(local_heights)
    world_max_y = anchor_y + max(local_heights)
    assert abs(float(ground_y) - 0.0) < 0.001
    assert world_min_y < WATER_Y < world_max_y
    assert abs(world_min_y - (-5.5)) < 0.001
    assert abs(world_max_y - 24.5) < 0.001


def test_direct_and_reflected_hand_share_one_static_rock_moss_paint_owner() -> None:
    product = _expand_includes(PRODUCT_PATH.read_text())
    assert "int product_static_rock_moss_proxy_material(" in product
    assert product.count("product_static_rock_moss_proxy_material(") == 3
    assert "bool q3_probe_payload_apply_static_rock_moss_material(" in product
    assert "q3_probe_payload_apply_pieta_terrain_material" not in product
    assert "product_pieta_terrain_proxy_material" not in product
