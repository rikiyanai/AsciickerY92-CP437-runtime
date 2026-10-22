"""FL-4708 authored Y8 placement contract for the retained tree successor."""

from __future__ import annotations

import importlib.util
import json
import hashlib
import math
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER_PATH = ROOT / "scripts/compile_fl4708_y8_tree_placements.py"
SOURCE = ROOT / "godot_project/assets/maps/game_map_y8.a3d"
REDUCED_TWIN = ROOT / "assets/a3d/game_map_y8_original_game_map.a3d"
MANIFEST = ROOT / "godot_project/assets/world_mesh/fl4708_y8_tree_instances.v1.json"
CATALOG = ROOT / "godot_project/assets/world_partition/fl4789_chunk_catalog.v1.json"
LANDMARKS = ROOT / "godot_project/assets/world_mesh/authored_landmarks.json"

REMOVED_FL4131_SHAPE_LAB_MESHES = {
    "fl4131_shape_lab_sphere.akm",
    "fl4131_shape_lab_triangle_pyramid.akm",
    "fl4131_shape_lab_skull_like.akm",
}
REMOVED_FL4131_SHAPE_LAB_INSTANCE_NAMES = {
    "fl4131_original_game_sphere",
    "fl4131_original_game_triangle_pyramid",
    "fl4131_original_game_skull_like",
    "fl4131_shape_lab_sphere",
    "fl4131_shape_lab_triangle_pyramid",
    "fl4131_shape_lab_skull_like",
}
REMOVED_FL4131_SHAPE_LAB_SOURCE_IDS = {101285, 101286, 101287}
REMOVED_STONE_MESH_COUNTS = {
    "rock-1.akm": 531,
    "brick-1.akm": 106,
}
# The elected canonical source now feeds the retained products directly. This
# digest covers every authored mesh record, including the richer map's fixtures.
SURVIVING_Y8_MESH_RECORDS_SHA256 = (
    "1e29269b87bf6a95bf92ec8e117a7b34cae383e754e800d2b35791afec116ec9"
)
# The castle uses canonical A3D terrain rather than a landmark mound mesh.
# The digest and membership assertion retain the castle interior and bridge,
# so either disappearing still fails instead of passing on a re-pinned hash.
APPROVED_LANDMARK_HASHES = {
    "godot_project/assets/world_mesh/authored_landmarks.json": (
        "712a5000aaf38d668aff052e16a866d72029a50a2f7944263676c3f8570a8dd6"
    ),
    "godot_project/assets/world_mesh/lighthouse_tower_faces.json": (
        "6c40cb2f8cbf1b03a903982546770f697b0654ff8d1e6350c753d244410e3da3"
    ),
    "godot_project/assets/world_mesh/pieta_statue_mossy_faces.json": (
        "2dbb74f16938f3bdcb8ef8741d75dbf810926a14b71df15d56589ef4a107ca2a"
    ),
    "godot_project/assets/world_mesh/female_hand_statue_mossy_faces.json": (
        "3713e462530a4ea016c6cb74d381cc6b74a8d90a65448e1ef7178e97dfd15ccb"
    ),
}


def _load_compiler():
    spec = importlib.util.spec_from_file_location("fl4708_tree_placements", COMPILER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_instance_record_bytes(source: Path) -> list[tuple[int, str, str, bytes]]:
    data = source.read_bytes()
    header_size = int.from_bytes(data[4:8], "little")
    patch_count = int.from_bytes(data[8:12], "little")
    offset = header_size + patch_count * 188 + 256 * 512
    raw_format = struct.unpack_from("<i", data, offset)[0]
    format_version = -raw_format if raw_format < 0 else raw_format
    instance_count = struct.unpack_from("<i", data, offset + 4)[0]
    offset += 8
    records: list[tuple[int, str, str, bytes]] = []
    for index in range(instance_count):
        start = offset
        discriminator = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        if discriminator >= 0:
            mesh_len = discriminator
            mesh_name = data[offset : offset + mesh_len].decode("utf-8")
            offset += mesh_len
            inst_name_len = struct.unpack_from("<i", data, offset)[0]
            offset += 4
            inst_name = data[offset : offset + inst_name_len].decode("utf-8")
            offset += inst_name_len
            offset += 128 + 8
            records.append((index, mesh_name, inst_name, data[start:offset]))
            continue
        if discriminator == -1:
            inst_name_len = struct.unpack_from("<i", data, offset)[0]
            offset += 4 + inst_name_len + 12 + 4 + 8 + 16 + 8
            continue
        if discriminator == -2:
            if format_version < 3:
                offset += 4 + 12 + 4 + 8
            else:
                offset += 16 + 12 + 4 + 8
            continue
        raise AssertionError(f"unknown A3D instance discriminator {discriminator} at {index}")
    return records


def test_generated_manifest_preserves_every_authored_tree_transform() -> None:
    compiler = _load_compiler()
    expected = compiler.compile_manifest(SOURCE)
    actual = json.loads(MANIFEST.read_text())

    assert actual == expected
    assert actual["source_a3d_path"] == "godot_project/assets/maps/game_map_y8.a3d"
    assert actual["source_world_format_version"] == 4
    assert actual["source_world_records"] == 1285
    assert actual["source_tree_records"] == 179
    assert actual["source_other_akm_records"] == 904
    assert len(actual["records"]) == actual["source_tree_records"]
    assert len(actual["other_akm_records"]) == actual["source_other_akm_records"]
    assert actual["runtime_terrain_path"] == "godot_project/assets/maps/game_map_y8.a3d"
    assert actual["runtime_terrain_matches_source"] is True
    assert actual["runtime_consumption_blocked"] is False
    bridge = actual["terrain_bridge"]
    assert bridge["source_patch_count"] == 5372
    assert bridge["runtime_patch_count"] == 5372
    assert bridge["patch_topology_matches"] is True
    assert bridge["matching_height_patch_count"] == 5372
    assert bridge["all_height_patches_match"] is True
    assert bridge["tree_anchor_coverage"] == 179
    assert bridge["runtime_center_offset"] == [-56.0, 176.0]

    for record in actual["records"]:
        tm = record["authored_transform_column_major_f64"]
        godot = record["godot_transform"]
        assert record["provenance"] == "AUTHORED_Y8"
        assert record["tree_identity"] == "y8-instance-%d" % record["source_instance_index"]
        assert godot == {
            "basis_x": tm[0:3],
            "basis_y": tm[4:7],
            "basis_z": tm[8:11],
            "origin": tm[12:15],
        }
        placement = record["runtime_placement"]
        assert placement["world_xz"] == [tm[12] + 56.0, tm[13] - 176.0]
        assert placement["authored_godot_y"] == tm[14] / 16.0
        assert placement["vertical_rule"] == (
            "EXACT_AUTHORED_Y_ON_IDENTICAL_TERRAIN_HEIGHTFIELD"
        )
        assert math.isfinite(placement["procedural_scale"])
        assert placement["procedural_scale"] > 0.0

    for record in actual["other_akm_records"]:
        tm = record["authored_transform_column_major_f64"]
        assert record["provenance"] == "AUTHORED_Y8"
        assert record["is_tree"] is False
        assert record["godot_transform"]["origin"] == tm[12:15]


def test_reduced_twin_is_not_a_product_source() -> None:
    """The retired reduced derivative remains identifiable as a legacy control."""
    legacy_rows = _source_instance_record_bytes(REDUCED_TWIN)
    actual = json.loads(MANIFEST.read_text())
    assert len(legacy_rows) == 322
    assert actual["source_a3d_path"] != "assets/a3d/game_map_y8_original_game_map.a3d"


def test_canonical_source_rows_and_approved_landmarks() -> None:
    actual = json.loads(MANIFEST.read_text())
    catalog = json.loads(CATALOG.read_text())
    raw_mesh_records = _source_instance_record_bytes(SOURCE)

    assert len(raw_mesh_records) == 1083
    indexed_record_digest = hashlib.sha256()
    source_indices = set()
    for index, mesh_name, inst_name, raw in raw_mesh_records:
        indexed_record_digest.update(struct.pack("<i", index))
        indexed_record_digest.update(raw)
        source_indices.add(index)
        assert mesh_name not in REMOVED_FL4131_SHAPE_LAB_MESHES
        assert inst_name not in REMOVED_FL4131_SHAPE_LAB_INSTANCE_NAMES
    assert indexed_record_digest.hexdigest() == SURVIVING_Y8_MESH_RECORDS_SHA256

    manifest_rows = actual["records"] + actual["other_akm_records"]
    assert {row["source_instance_index"] for row in manifest_rows} == source_indices
    for row in manifest_rows:
        source_index = int(row["source_instance_index"])
        assert row["instance_identity"] == f"y8-instance-{source_index}"
        assert row["source_mesh_name"] not in REMOVED_FL4131_SHAPE_LAB_MESHES
        assert row["source_instance_name"] not in REMOVED_FL4131_SHAPE_LAB_INSTANCE_NAMES

    catalog_placements = [
        placement
        for chunk in catalog["chunks"]
        for placement in chunk["asset_instances"]
    ]
    for placement in catalog_placements:
        assert placement.get("source_mesh_name") not in REMOVED_FL4131_SHAPE_LAB_MESHES
        assert int(placement["instance_or_source_id"]) not in REMOVED_FL4131_SHAPE_LAB_SOURCE_IDS
    assert not [
        mesh
        for mesh in catalog["header"].get("fixture_mesh_sources", [])
        if mesh["mesh_name"] in REMOVED_FL4131_SHAPE_LAB_MESHES
    ]

    landmarks = json.loads(LANDMARKS.read_text())
    assert landmarks["schema"] == "authored_landmarks.v1"
    assert {
        "lighthouse",
        "pieta_statue",
        "female_hand",
        "cyan_pedestal",
        "castle_interior",
        "castle_bridge",
    } <= set(landmarks["placements"])
    for rel_path, expected_hash in APPROVED_LANDMARK_HASHES.items():
        assert hashlib.sha256((ROOT / rel_path).read_bytes()).hexdigest() == expected_hash


def test_manifest_remains_data_only_until_the_current_retained_owner_consumes_it() -> None:
    source = COMPILER_PATH.read_text()
    assert ".instantiate(" not in source
    assert ".add_child(" not in source
    assert "UNRESOLVED_FL4708_SOURCE" in source
