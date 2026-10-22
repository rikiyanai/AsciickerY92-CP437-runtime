"""FL-4798: authored fixtures of a non-Y8 map reach the compiled FL-4789 catalog.

The SBU OSM map carries 90 mesh instances: 53 trees the tree lane already
admits and 37 fixtures (street lamps, benches, planters, a torus) it declined.
These tests pin the fixture lane's contract: the manifest keeps every row, the
catalog compiles fixtures as their own registry family at the authored basis,
each face carries the authored-colour paint lane, the default Y8 compile is
untouched by the new options, and World resolves the catalog per map.
"""
from __future__ import annotations

import importlib.util
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_COMPILER = ROOT / "scripts/compile_fl4708_y8_tree_placements.py"
CATALOG_COMPILER = ROOT / "scripts/compile_fl4789_chunk_catalog.py"
SBU_MAP = ROOT / "godot_project/assets/maps/sbu_carto_scale225_20260601.a3d"
SBU_MANIFEST = ROOT / "godot_project/assets/world_mesh/sbu_carto_scale225_20260601.fl4708_tree_instances.v1.json"
SBU_CATALOG = ROOT / "godot_project/assets/world_partition/sbu_carto_scale225_20260601.fl4789_chunk_catalog.v1.json"
SBU_PAYLOAD = ROOT / "godot_project/assets/world_partition/sbu_carto_scale225_20260601.fl4789_chunk_assets.v1.bin"
WORLD_GD = ROOT / "godot_project/scripts/World.gd"
A3D_CODEC = ROOT / "scripts/fl4588/a3d_document_codec.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _a3d_instances() -> list[dict]:
    codec = _load("fl4798_a3d_codec", A3D_CODEC)
    document = codec.parse_document(SBU_MAP.read_bytes())
    rows = []
    for row in document.instances:
        if row.get("kind") != "mesh":
            continue
        tm = row["tm"]
        if isinstance(tm, (bytes, bytearray)):
            tm = list(struct.unpack("<16d", tm))
        mesh = row["mesh"]
        rows.append({"mesh": mesh.decode() if isinstance(mesh, bytes) else str(mesh), "tm": [float(v) for v in tm]})
    return rows


def test_sbu_manifest_keeps_every_authored_instance_against_its_own_terrain() -> None:
    compiler = _load("fl4798_manifest_compiler", MANIFEST_COMPILER)
    manifest = compiler.compile_manifest(SBU_MAP, SBU_MAP)
    assert manifest["runtime_terrain_path"].endswith("sbu_carto_scale225_20260601.a3d")
    assert manifest["runtime_terrain_matches_source"] is True
    assert manifest["runtime_consumption_blocked"] is False
    assert manifest["source_tree_records"] == 53
    assert manifest["source_other_akm_records"] == 37
    assert manifest["source_tree_records"] + manifest["source_other_akm_records"] == len(_a3d_instances())
    fixture_meshes = sorted({row["source_mesh_name"] for row in manifest["other_akm_records"]})
    assert fixture_meshes == ["bench.akm", "planter.akm", "street_lamp.akm", "torus.akm"]
    # The tracked manifest is this compile.
    assert json.loads(SBU_MANIFEST.read_text()) == json.loads(json.dumps(manifest))


def test_tracked_y8_catalog_carries_its_authored_fixtures() -> None:
    compiler = _load("fl4798_catalog_compiler_default", CATALOG_COMPILER)
    tracked = json.loads((ROOT / "godot_project/assets/world_partition/fl4789_chunk_catalog.v1.json").read_text())
    header = tracked["header"]
    registry = header["semantic_object_registry"]
    lo, hi = registry["fixture_registry_slot_range"]
    hosted = header["fixtures_hosted_by_nearest_chunk"]
    manifest = json.loads((ROOT / "godot_project/assets/world_mesh/fl4708_y8_tree_instances.v1.json").read_text())
    # Every authored non-tree row compiles; the elected canonical Y8 source is
    # intact (179 trees plus 904 fixtures, including rock/brick rows).
    assert hi - lo + 1 == manifest["source_other_akm_records"] == 904
    assert 0 < len(hosted) < 904
    placements = [p for chunk in tracked["chunks"] for p in chunk["asset_instances"] if p["source_kind"] == compiler.FIXTURE_SOURCE_KIND]
    assert sum(1 for p in placements if p["hosted_by_nearest_chunk"]) == len(hosted)
    assert {p["source_mesh_name"] for p in placements if p["hosted_by_nearest_chunk"]} >= {"ship-1.akm", "bridge-2.akm"}
    assert registry["authored_registry_slot_range"] == [0, 178]
    assert registry["lab_registry_slot_range"] == [179, 181]
    assert lo == 182
    meshes = {m["mesh_name"] for m in header["fixture_mesh_sources"]}
    assert {"house-3.akm", "bridge-1.akm", "bridge-2.akm", "bush-1.akm", "fence.akm"} <= meshes
    assert {"rock-1.akm", "brick-1.akm"} <= meshes
    import inspect
    params = inspect.signature(compiler.compile_catalog).parameters
    assert params["include_fixtures"].default is True
    assert params["lab_grove"].default is True
    assert params["meadow"].default is True


def _fixture_rows(compiler, catalog, payload: bytes, placement: dict) -> list[float]:
    record = next(r for r in catalog["asset_records"] if r["content_key"] == placement["content_key"])
    start = record["rows_offset"]
    count = record["face_count"] * compiler.FLOATS_PER_FACE
    return list(struct.unpack_from(f"<{count}f", payload, start))


def test_sbu_catalog_compiles_every_fixture_at_its_authored_basis() -> None:
    compiler = _load("fl4798_catalog_compiler", CATALOG_COMPILER)
    catalog = json.loads(SBU_CATALOG.read_text())
    payload = SBU_PAYLOAD.read_bytes()
    header = catalog["header"]
    assert header["source_map_path"].endswith("sbu_carto_scale225_20260601.a3d")
    assert header["fixture_placement_count"] == 37
    assert header["lab_grove_included"] is False
    assert header["fixture_material"]["material_id"] == compiler.AUTHORED_FIXTURE_MAT
    registry = header["semantic_object_registry"]
    lo, hi = registry["fixture_registry_slot_range"]
    assert hi - lo + 1 == 37
    assert registry["authored_registry_slot_range"] == [0, 52]
    assert registry["meadow_registry_slot_range"][0] == hi + 1
    placements = [p for chunk in catalog["chunks"] for p in chunk["asset_instances"] if p["source_kind"] == compiler.FIXTURE_SOURCE_KIND]
    assert len(placements) == 37
    assert sorted(p["registry_slot"] for p in placements) == list(range(lo, hi + 1))
    center_x, center_y = header["center_offset_source_cells"]
    by_mesh = {}
    for p in placements:
        by_mesh.setdefault(p["source_mesh_name"], 0)
        by_mesh[p["source_mesh_name"]] += 1
    assert by_mesh == {"street_lamp.akm": 29, "bench.akm": 5, "planter.akm": 2, "torus.akm": 1}
    # Placements are keyed back to the A3D world rows by their authored origin
    # (A3D X/Y minus the centre offset -> Godot X/Z, A3D Z / 16 -> Godot Y).
    def _key(x: float, y: float, z: float) -> tuple:
        return (round(x, 3), round(y, 3), round(z, 3))
    authored = {
        _key(r["tm"][12] - center_x, r["tm"][14] / compiler.A3D_HEIGHT_SCALE, r["tm"][13] - center_y): r
        for r in _a3d_instances()
    }
    matched = {}
    for p in placements:
        key = _key(p["world_xz"][0], p["authored_godot_y"], p["world_xz"][1])
        assert key in authored, (p["source_mesh_name"], key)
        assert authored[key]["mesh"] == p["source_mesh_name"]
        assert math.isclose(p["yaw_radians"], math.atan2(authored[key]["tm"][1], authored[key]["tm"][0]), abs_tol=1e-6)
        assert p["procedural_scale"] == 1.0
        assert p["root_ecology_face_count"] == 0
        matched[p["registry_slot"]] = authored[key]
    assert len(matched) == 37
    # Face-level contract on one bench: material, colour lane, 4708 tag, and
    # world parity with the authored basis applied to the mesh vertices.
    bench = next(p for p in placements if p["source_mesh_name"] == "bench.akm")
    rows = _fixture_rows(compiler, catalog, payload, bench)
    mesh = compiler.load_fixture_mesh("bench.akm")
    assert len(rows) == mesh["face_count"] * compiler.FLOATS_PER_FACE
    materials = {int(rows[i]) for i in range(3, len(rows), 16)}
    assert materials == {compiler.AUTHORED_FIXTURE_MAT}
    tags = {math.floor(abs(rows[i])) for i in range(15, len(rows), 16)}
    assert tags == {4708}
    colours = {int(rows[i]) for i in range(11, len(rows), 16)}
    assert len(colours) >= 1 and all(0 <= c <= 0xFFFFFF for c in colours)
    world = compiler.transform_tree_rows(rows, bench["world_xz"], bench["authored_godot_y"], bench["yaw_radians"], 1.0)
    tm = matched[bench["registry_slot"]]["tm"]
    i0 = mesh["triangles"][0][0]
    p = mesh["positions"][i0]
    expected_x = tm[0] * p[0] + tm[4] * p[1] + tm[8] * p[2] + tm[12] - center_x
    expected_y = (tm[2] * p[0] + tm[6] * p[1] + tm[10] * p[2] + tm[14]) / compiler.A3D_HEIGHT_SCALE
    expected_z = tm[1] * p[0] + tm[5] * p[1] + tm[9] * p[2] + tm[13] - center_y
    assert math.isclose(world[0], expected_x, abs_tol=2e-3)
    assert math.isclose(world[1], expected_y, abs_tol=2e-3)
    assert math.isclose(world[2], expected_z, abs_tol=2e-3)


def test_world_resolves_the_catalog_and_payload_per_loaded_map() -> None:
    source = WORLD_GD.read_text()
    assert "static func fl4789_catalog_resource_for_map(map_path: String) -> String:" in source
    assert "static func fl4789_payload_resource_for_catalog(catalog: Dictionary) -> String:" in source
    install = source[source.index("func _install_static_world_mesh("):source.index("func _process_world_partition_rebuild(")]
    assert 'fl4789_catalog_resource_for_map(loaded_map_path)' in install
    assert 'FileAccess.get_file_as_string(FL4789_CHUNK_CATALOG_RESOURCE)' not in install
    registry = source[source.index("func _install_fl4789_compiled_tree_registry("):source.index("func _fl4789_active_object_ids(")]
    assert "fl4789_payload_resource_for_catalog(catalog)" in registry
    assert '"authored_fixture":' in registry
