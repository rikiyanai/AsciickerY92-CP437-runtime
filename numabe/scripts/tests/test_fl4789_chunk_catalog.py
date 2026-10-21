from __future__ import annotations

import importlib.util
import math
import json
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER_PATH = ROOT / "scripts/compile_fl4789_chunk_catalog.py"
ACTOR_ATLAS_COMPILER_PATH = (
    ROOT / "scripts/compile_fl4789_actor_atlas_catalog.py"
)
CATALOG_PATH = (
    ROOT / "godot_project/assets/world_partition/fl4789_chunk_catalog.v1.json"
)
ACTOR_ATLAS_CATALOG_PATH = (
    ROOT / "godot_project/assets/xp_sprites/fl4789_actor_atlas_catalog.v1.json"
)
MAP_PATH = ROOT / "godot_project/assets/maps/game_map_y8.a3d"
TREE_MANIFEST_PATH = (
    ROOT / "godot_project/assets/world_mesh/fl4708_y8_tree_instances.v1.json"
)


def _load_compiler():
    spec = importlib.util.spec_from_file_location("fl4789_chunk_compiler", COMPILER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_actor_atlas_compiler():
    spec = importlib.util.spec_from_file_location(
        "fl4789_actor_atlas_compiler", ACTOR_ATLAS_COMPILER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_catalog_is_current_and_deterministic() -> None:
    compiler = _load_compiler()
    first = compiler.compile_catalog(compiler.DEFAULT_MAP, compiler.DEFAULT_TREE_MANIFEST)
    second = compiler.compile_catalog(compiler.DEFAULT_MAP, compiler.DEFAULT_TREE_MANIFEST)
    assert first == second
    assert first.payload_bytes == second.payload_bytes
    assert first.payload_bytes == (ROOT / "godot_project/assets/world_partition/fl4789_chunk_assets.v1.bin").read_bytes()
    assert CATALOG_PATH.read_text() == compiler.render_catalog(first)
    assert len(first["header"]["catalog_hash"]) == 64


def test_canonical_source_fixture_set_reaches_every_catalog_owner() -> None:
    catalog = json.loads(CATALOG_PATH.read_text())
    manifest = json.loads(TREE_MANIFEST_PATH.read_text())
    retained_stones = {"rock-1.akm", "brick-1.akm"}
    assert manifest["source_world_records"] == 1285
    assert manifest["source_tree_records"] == 179
    assert manifest["source_other_akm_records"] == 904
    assert retained_stones <= {
        row["source_mesh_name"] for row in manifest["records"] + manifest["other_akm_records"]
    }
    placements = [
        row for chunk in catalog["chunks"] for row in chunk["asset_instances"]
    ]
    assert catalog["header"]["fixture_placement_count"] == 904
    assert len(placements) == 1230
    assert retained_stones <= {
        row.get("source_mesh_name") for row in placements
        if row.get("source_kind") == "authored_fixture"
    }
    assert retained_stones <= {
        row["mesh_name"] for row in catalog["header"]["fixture_mesh_sources"]
    }


def test_chunk_union_reproduces_every_authored_patch_and_tree() -> None:
    compiler = _load_compiler()
    catalog = json.loads(CATALOG_PATH.read_text())
    data = MAP_PATH.read_bytes()
    patch_count = struct.unpack_from("<I", data, 8)[0]
    source_patch_rows: dict[tuple[int, int], str] = {}
    for source_index in range(patch_count):
        offset = 16 + source_index * 188
        raw = data[offset : offset + 188]
        key = struct.unpack_from("<ii", raw, 0)
        source_patch_rows[key] = __import__("hashlib").sha256(raw).hexdigest()

    catalog_patch_rows: dict[tuple[int, int], str] = {}
    tree_ids: list[int] = []
    tree_slots: list[int] = []
    total_tree_faces = 0
    for chunk in catalog["chunks"]:
        assert chunk["retained_static_face_count"] == sum(
            row["face_count"] for row in chunk["asset_instances"]
        )
        for patch in chunk["terrain_patches"]:
            key = tuple(patch["patch_coord"])
            assert key not in catalog_patch_rows
            catalog_patch_rows[key] = patch["record_sha256"]
        for row in chunk["asset_instances"]:
            source_id = int(row["instance_or_source_id"])
            assert int(row["source_instance_id"]) == source_id
            assert row["chunk_id"] == chunk["chunk_id"]
            total_tree_faces += int(row["face_count"])
            if row["source_kind"] in ("compiled_meadow", "authored_fixture"):
                # Fixtures and understory follow the migrated authored tree
                # slots, followed by the 3 synthesised lab-grove anchors.
                assert row["registry_slot"] >= 182
                continue
            # The authored prefix is unchanged. Understory has its own count;
            # it cannot masquerade as replacement tree geometry.
            mesh_name = str(row["source_mesh_name"])
            assert mesh_name in compiler.CANONICAL_TREE_MESHES
            assert row["authored_face_count"] == compiler._tree_face_count(mesh_name)
            assert row["face_count"] == row["authored_face_count"] + row["root_ecology_face_count"]
            assert row["blas_leaf_count"] == row["face_count"]
            assert row["face_count"] <= 32768
            tree_ids.append(source_id)
            tree_slots.append(int(row["registry_slot"]))

    assert catalog_patch_rows == source_patch_rows
    assert len(tree_ids) == len(set(tree_ids)) == 182
    assert sorted(tree_slots) == list(range(182))
    assert sum(row["source_kind"] == "authored_y8" for chunk in catalog["chunks"] for row in chunk["asset_instances"]) == 179
    assert sum(row["source_kind"] == "lab_grove" for chunk in catalog["chunks"] for row in chunk["asset_instances"]) == 3
    assert total_tree_faces == catalog["union_receipt"]["retained_static_face_count"]
    assert max(
        chunk["retained_static_face_count"] for chunk in catalog["chunks"]
    ) < 0xFFFF


def test_catalog_contains_immutable_content_records_and_binary_payload() -> None:
    compiler = _load_compiler()
    catalog = json.loads(CATALOG_PATH.read_text())
    payload_path = ROOT / catalog["header"]["asset_payload"]["path"]
    payload = payload_path.read_bytes()
    header = compiler.PAYLOAD_HEADER_STRUCT.unpack_from(payload, 0)
    magic, version, header_bytes, content_count, registry_base, registry_count, fixed_count = header
    assert magic == compiler.PAYLOAD_MAGIC
    assert version == compiler.PAYLOAD_VERSION
    assert header_bytes == compiler.PAYLOAD_HEADER_BYTES
    assert content_count == len(catalog["asset_records"])
    assert registry_base == 0
    assert registry_count == sum(len(c["asset_instances"]) for c in catalog["chunks"])
    assert fixed_count == catalog["header"]["semantic_object_registry"]["fixed_instance_reserved_count"]
    assert len(payload) == catalog["header"]["asset_payload"]["byte_length"]
    assert __import__("hashlib").sha256(payload).hexdigest() == catalog["header"]["asset_payload"]["sha256"]
    keys = [record["content_key"] for record in catalog["asset_records"]]
    assert len(keys) == len(set(keys))
    for record in catalog["asset_records"]:
        face_count = int(record["face_count"])
        rows_start = int(record["rows_offset"])
        rows_end = rows_start + int(record["rows_length"])
        node_start = int(record["blas_node_offset"])
        node_end = node_start + int(record["blas_node_length"])
        ids_start = int(record["blas_id_offset"])
        ids_end = ids_start + int(record["blas_id_length"])
        assert record["rows_length"] == face_count * compiler.FLOATS_PER_FACE * 4
        assert record["blas_node_length"] == (32 if face_count == 1 else (face_count - 1) * 32)
        assert record["blas_id_length"] == face_count * 4
        assert record["blas_leaf_count"] == face_count
        assert 0 <= rows_start < rows_end <= len(payload)
        assert rows_end <= node_start <= node_end <= ids_start <= ids_end <= len(payload)
        assert __import__("hashlib").sha256(payload[rows_start:rows_end]).hexdigest() == record["rows_sha256"]
        assert __import__("hashlib").sha256(payload[node_start:node_end]).hexdigest() == record["blas_node_sha256"]
        assert __import__("hashlib").sha256(payload[ids_start:ids_end]).hexdigest() == record["blas_id_sha256"]
        assert __import__("hashlib").sha256(
            struct.pack("<i", face_count) + payload[rows_start:rows_end]
        ).hexdigest() == record["content_hash"]
        assert record["payload_offset"] == rows_start
        assert record["payload_length"] == rows_end - rows_start + node_end - node_start + ids_end - ids_start
        assert __import__("hashlib").sha256(
            payload[rows_start:rows_end] + payload[node_start:node_end] + payload[ids_start:ids_end]
        ).hexdigest() == record["payload_sha256"]
        assert record["source_instance_ids"]
        assert record["chunk_ids"]
        assert all(a <= b for a, b in zip(record["local_aabb_min"], record["local_aabb_max"]))


def test_registry_slots_content_keys_and_world_flatten_parity() -> None:
    compiler = _load_compiler()
    catalog = json.loads(CATALOG_PATH.read_text())
    compiled = compiler.compile_catalog(
        compiler.DEFAULT_MAP, compiler.DEFAULT_TREE_MANIFEST
    )
    assert compiled == catalog
    placements = [
        row for chunk in catalog["chunks"] for row in chunk["asset_instances"]
    ]
    by_slot = {int(row["registry_slot"]): row for row in placements}
    assert sorted(by_slot) == list(range(len(placements)))
    assert len({row["instance_identity"] for row in placements}) == len(placements)
    assert all(by_slot[i]["source_kind"] != "compiled_meadow" for i in range(182))
    content_keys = {row["content_key"] for row in catalog["asset_records"]}
    assert {row["content_key"] for row in placements} <= content_keys
    registry = catalog["header"]["semantic_object_registry"]
    assert registry["authored_registry_slot_range"] == [0, 178]
    assert registry["lab_registry_slot_range"] == [179, 181]
    assert registry["semantic_object_id_formula"] == "base_instance_count + registry_slot"

    # Published tree geometry must be the authored .akm mesh standing at its
    # authored A3D placement. The decisive check is end to end: take a nonzero-yaw
    # authored placement, rebuild every published world corner from the compiled
    # local rows plus the runtime's own origin/yaw/scale flattening, and require
    # it to agree with the raw A3D instance transform applied directly to the
    # authored mesh vertices. Nothing about the intermediate representation is
    # assumed; only the authored world result is asserted.
    manifest = json.loads(TREE_MANIFEST_PATH.read_text())
    manifest_by_id = {
        int(row["source_instance_index"]): row for row in manifest["records"]
    }
    placement = next(
        row for row in placements
        if row["source_kind"] == "authored_y8" and abs(float(row["yaw_radians"])) > 0.1
    )
    source_id = int(placement["instance_or_source_id"])
    transform = manifest_by_id[source_id]["authored_transform_column_major_f64"]
    mesh = compiler.load_tree_mesh(str(placement["source_mesh_name"]))
    local_rows = compiler.build_tree_local_rows(
        {
            "source_mesh_name": placement["source_mesh_name"],
            "authored_transform_column_major_f64": transform,
            "yaw_radians": placement["yaw_radians"],
            "procedural_scale": placement["procedural_scale"],
            "instance_or_source_id": source_id,
        }
    )
    world_rows = compiler.transform_tree_rows(
        local_rows,
        placement["world_xz"],
        placement["authored_godot_y"],
        placement["yaw_radians"],
        placement["procedural_scale"],
    )
    world_min, world_max = compiler._rows_aabb(world_rows)
    assert all(a >= b for a, b in zip(world_min, placement["world_aabb_min"]))
    assert all(a <= b for a, b in zip(world_max, placement["world_aabb_max"]))
    assert len(world_rows) // 16 == mesh["face_count"] == int(placement["authored_face_count"])
    record = next(row for row in catalog["asset_records"] if row["content_key"] == placement["content_key"])
    prefix = compiled.payload_bytes[record["rows_offset"]:record["rows_offset"] + len(local_rows) * 4]
    assert prefix == compiler._rows_bytes(local_rows)

    center_x, center_y = catalog["header"]["center_offset_source_cells"]
    for face_index, triangle in enumerate(mesh["triangles"]):
        for corner, vertex_index in enumerate(triangle):
            vx, vy, vz = mesh["positions"][vertex_index]
            a3d_x = transform[0] * vx + transform[4] * vy + transform[8] * vz + transform[12]
            a3d_y = transform[1] * vx + transform[5] * vy + transform[9] * vz + transform[13]
            a3d_z = transform[2] * vx + transform[6] * vy + transform[10] * vz + transform[14]
            # A3D X/Y are horizontal cells; A3D Z is 1/16-cell height steps.
            expected = (
                a3d_x - float(center_x),
                a3d_z / 16.0,
                a3d_y - float(center_y),
            )
            base = face_index * 16 + corner * 4
            for axis in range(3):
                assert abs(world_rows[base + axis] - expected[axis]) < 1.0e-3
    # The retained quad ABI repeats an authored triangle's last corner.
    for face_index in range(mesh["face_count"]):
        base = face_index * 16
        assert world_rows[base + 8:base + 11] == world_rows[base + 12:base + 15]


def test_every_compiled_tree_draws_authored_akm_geometry_and_materials() -> None:
    compiler = _load_compiler()
    catalog = compiler.compile_catalog(compiler.DEFAULT_MAP, compiler.DEFAULT_TREE_MANIFEST)
    payload = catalog.payload_bytes
    admitted_materials = {
        compiler.TREE_TRUNK_MAT,
        compiler.TREE_CANOPY_CONTOUR_MAT,
        compiler.TREE_CANOPY_INTERIOR_MAT,
        compiler.TREE_CANOPY_UNDERSIDE_MAT,
    }
    mesh_sources = {
        row["mesh_name"]: row
        for row in catalog["header"]["tree_mesh_sources"]
    }
    assert mesh_sources
    assert set(mesh_sources) <= set(compiler.CANONICAL_TREE_MESHES)
    for mesh_name, row in mesh_sources.items():
        source_path = ROOT / row["path"]
        assert source_path.is_file()
        assert __import__("hashlib").sha256(source_path.read_bytes()).hexdigest() == row["sha256"]
        assert row["face_count"] == compiler._tree_face_count(mesh_name)
        assert sum(row["material_face_counts"].values()) == row["face_count"]
        # An asset painted entirely bark (the two bare old-tree assets) has no
        # canopy at all; every asset with foliage must still carry a trunk.
        assert row["material_face_counts"][str(compiler.TREE_TRUNK_MAT)] > 0

    placements = [
        row for chunk in catalog["chunks"] for row in chunk["asset_instances"]
    ]
    face_count_by_mesh = {
        name: compiler._tree_face_count(name) for name in mesh_sources
    }
    for placement in placements:
        if placement["source_kind"] in ("compiled_meadow", "authored_fixture"):
            continue
        assert int(placement["authored_face_count"]) == face_count_by_mesh[
            str(placement["source_mesh_name"])
        ]

    for record in catalog["asset_records"]:
        if set(record["source_kinds"]) & {"compiled_meadow", "authored_fixture"}:
            continue
        face_count = int(record["face_count"])
        authored_count = compiler._tree_face_count(record["source_mesh_names"][0])
        assert face_count >= authored_count
        assert len(record["source_mesh_names"]) == 1
        rows = struct.unpack_from(
            f"<{face_count * compiler.FLOATS_PER_FACE}f",
            payload,
            int(record["rows_offset"]),
        )
        materials = {
            int(rows[offset + 3])
            for offset in range(0, authored_count * compiler.FLOATS_PER_FACE, compiler.FLOATS_PER_FACE)
        }
        # No root decoration may replace an authored tree face.
        assert materials <= admitted_materials
        assert 244 not in materials and 245 not in materials
        shades = {
            rows[offset + 7] for offset in range(0, len(rows), compiler.FLOATS_PER_FACE)
        }
        assert all(0.0 < shade <= 1.0 for shade in shades)


# FL-4359 (operator 2026-09-09): the retained meadow face count before the patch
# rewrite, when each site grew three 0.8-2.2 unit standing blade cards. The lane
# budget allows the ground-following patch mats at most 25% more retained faces
# than that; a denser mat that needs a bigger budget is a separate decision.
# FL-4359 grass-fields S9: re-baselined for batched upright field cards
# (design /tmp/claude-design-grass-fields-20260909.md sect. B4).
MEADOW_FACE_BUDGET = 63000
# The patch mat's authored crest band, in world units above its planted root.
MEADOW_PATCH_MIN_HEIGHT = 0.10
MEADOW_PATCH_MAX_HEIGHT = 0.60


def _meadow_world_faces(compiler, catalog, payload):
    """Yield (local rows, world rows, face offset) for every retained meadow face."""
    assets = {a["content_key"]: a for a in catalog["asset_records"]}
    for placement in [p for c in catalog["chunks"] for p in c["asset_instances"]
                      if p["source_kind"] == "compiled_meadow"]:
        asset = assets[placement["content_key"]]
        rows = list(struct.unpack_from(
            f'<{asset["face_count"] * 16}f', payload, asset["rows_offset"]))
        world = compiler.transform_tree_rows(
            rows, placement["world_xz"], placement["authored_godot_y"],
            placement["yaw_radians"], placement["procedural_scale"])
        for offset in range(0, len(world), 16):
            yield placement, rows, world, offset


def _meadow_faces_with_clumps(compiler, catalog, payload):
    for placement, rows, world, offset in _meadow_world_faces(
            compiler, catalog, payload):
        face_index = offset // 16
        clump_index = int(placement["vegetation_face_clumps"][face_index])
        clump = placement["vegetation_clumps"][clump_index]
        yield placement, clump_index, clump, rows, world, offset


def test_meadow_is_upright_field_cards_batched_by_clump() -> None:
    """Every retained meadow clump is a set of distinct tapered blade faces."""
    compiler = _load_compiler()
    catalog = json.loads(CATALOG_PATH.read_text())
    payload = (ROOT / catalog["header"]["asset_payload"]["path"]).read_bytes()
    decoded = compiler._decode_map(compiler.DEFAULT_MAP)
    faces_by_clump: dict[tuple[str, int], int] = {}
    roots_by_clump: dict[tuple[str, int], list[tuple[float, float]]] = {}
    heights: list[float] = []
    for placement, clump_index, clump, rows, world, offset in _meadow_faces_with_clumps(
            compiler, catalog, payload):
        assert int(world[offset + 3]) == compiler.MEADOW_BLADE_MAT
        key = (str(placement["instance_identity"]), clump_index)
        faces_by_clump[key] = faces_by_clump.get(key, 0) + 1
        root = [rows[offset + 7] + placement["world_xz"][0],
                rows[offset + 11] + placement["authored_godot_y"],
                rows[offset + 15] + placement["world_xz"][1]]
        roots_by_clump.setdefault(key, []).append((root[0], root[2]))
        root_ground = compiler._sample_capture_ground(decoded, root[0], root[2])
        assert root_ground is not None and root_ground[1] in (0, 1, 3)
        assert root_ground[0] > 49 / 16
        assert math.dist((root[0], root[2]),
                         (float(clump["root"][0]), float(clump["root"][2]))) \
            <= compiler.MEADOW_CLUMP_RADIUS + compiler.MEADOW_BLADE_WIDTH
        points = [world[offset + c:offset + c + 3] for c in (0, 4, 8, 12)]
        endpoint_grounds = [compiler._sample_capture_ground(decoded, end[0], end[2])
                            for end in points[:2]]
        assert all(end_ground is not None for end_ground in endpoint_grounds)
        planted_ground = sum(float(end_ground[0]) for end_ground in endpoint_grounds) / 2.0
        assert abs(root[1] - planted_ground - 0.02) < 0.001
        # Both planted corners must remain terrain-supported after the blade's
        # own root is admitted.
        for end, end_ground in zip(points[:2], endpoint_grounds):
            assert end_ground[0] > 49 / 16
            assert abs(end[1] - end_ground[0] - 0.02) < 0.001, end
            assert abs(end_ground[0] - planted_ground) <= compiler.MEADOW_BLADE_MAX_STEP + 1e-6
        base_width = math.dist((points[0][0], points[0][2]),
                               (points[1][0], points[1][2]))
        tip_width = math.dist((points[2][0], points[2][2]),
                              (points[3][0], points[3][2]))
        assert base_width > 0.0
        assert 0.0 < tip_width < base_width * 0.2
        assert abs(tip_width / base_width - 0.1) < 1e-4
        for top in points[2:]:
            height = top[1] - root[1]
            assert 0.0 < height <= float(clump["height"]) + 1e-5, height
            assert abs(height - float(clump["height"])) < 1e-4
            heights.append(height)
    assert len(faces_by_clump) > 5000
    counts = list(faces_by_clump.values())
    assert min(counts) >= 1 and max(counts) == compiler.MEADOW_BLADES_PER_CLUMP
    assert sum(count == compiler.MEADOW_BLADES_PER_CLUMP for count in counts) \
        > len(counts) * 0.70
    assert all(
        len(set((round(x, 4), round(z, 4)) for x, z in roots)) == count
        and all(math.dist(a, b) > compiler.MEADOW_BLADE_WIDTH
                for i, a in enumerate(roots) for b in roots[:i])
        for key, roots in roots_by_clump.items()
        for count in [faces_by_clump[key]]
    )
    assert len({round(h, 2) for h in heights}) > 20


def test_meadow_face_budget_and_flower_sparsity() -> None:
    """The blade budget holds and flowers remain clump flags on blade geometry."""
    catalog = json.loads(CATALOG_PATH.read_text())
    meadow_faces = sum(
        int(p["face_count"]) for c in catalog["chunks"]
        for p in c["asset_instances"] if p["source_kind"] == "compiled_meadow")
    assert 0 < meadow_faces <= MEADOW_FACE_BUDGET, meadow_faces
    compiler = _load_compiler()
    payload = (ROOT / catalog["header"]["asset_payload"]["path"]).read_bytes()
    by_material: dict[int, int] = {}
    for _placement, _rows, world, offset in _meadow_world_faces(
            compiler, catalog, payload):
        material = int(world[offset + 3])
        by_material[material] = by_material.get(material, 0) + 1
    assert set(by_material) == {compiler.MEADOW_BLADE_MAT}
    clumps = [clump for placement in [p for c in catalog["chunks"]
                                      for p in c["asset_instances"]
                                      if p["source_kind"] == "compiled_meadow"]
              for clump in placement["vegetation_clumps"]]
    flowers = [clump for clump in clumps
               if int(clump["flags"]) & compiler.MEADOW_FLOWER_FLAG]
    assert flowers and len(flowers) < len(clumps) * 0.20
    assert all(compiler.MEADOW_FLOWER_SPECIES_BASE <= int(clump["species"]) \
               < compiler.MEADOW_SPECIES_COUNT for clump in flowers)


def test_meadow_flower_flags_keep_blade_material_and_geometry() -> None:
    """A flower flag never creates a second meadow geometry family."""
    compiler = _load_compiler()
    catalog = json.loads(CATALOG_PATH.read_text())
    payload = (ROOT / catalog["header"]["asset_payload"]["path"]).read_bytes()
    flagged = 0
    for placement in [p for c in catalog["chunks"] for p in c["asset_instances"]
                      if p["source_kind"] == "compiled_meadow"]:
        flagged_indices = {
            i for i, clump in enumerate(placement["vegetation_clumps"])
            if int(clump["flags"]) & compiler.MEADOW_FLOWER_FLAG
        }
        flagged += len(flagged_indices)
        for face_index, clump_index in enumerate(placement["vegetation_face_clumps"]):
            if int(clump_index) not in flagged_indices:
                continue
            asset = next(a for a in catalog["asset_records"]
                         if a["content_key"] == placement["content_key"])
            rows = list(struct.unpack_from(
                f'<{asset["face_count"] * 16}f', payload, asset["rows_offset"]))
            assert int(rows[face_index * 16 + 3]) == compiler.MEADOW_BLADE_MAT
    assert flagged > 100


def _pearson(pairs) -> float:
    pairs = list(pairs)
    if len(pairs) < 3:
        return 0.0
    mean_a = sum(p[0] for p in pairs) / len(pairs)
    mean_b = sum(p[1] for p in pairs) / len(pairs)
    numerator = sum((a - mean_a) * (b - mean_b) for a, b in pairs)
    denominator = math.sqrt(
        sum((a - mean_a) ** 2 for a, _ in pairs)
        * sum((b - mean_b) ** 2 for _, b in pairs)
    )
    return numerator / denominator if denominator > 1e-12 else 0.0


def _published_patch_crests(compiler, catalog, payload) -> dict:
    """Return the authored crest for each clump root."""
    crests: dict[tuple[float, float], float] = {}
    for placement in [p for c in catalog["chunks"] for p in c["asset_instances"]
                      if p["source_kind"] == "compiled_meadow"]:
        for clump in placement["vegetation_clumps"]:
            key = (round(float(clump["root"][0]), 4),
                   round(float(clump["root"][2]), 4))
            crests[key] = float(clump["height"])
    return crests


def test_meadow_clump_field_is_world_anchored_and_locally_smooth() -> None:
    """FL-4359/T1: randomness lives at the clump, not at the site.

    Falsifier for the Ghost of Tsushima intake's named naive version
    (MEDIA.md sect. 9.4, "random height values"): a field that does not correlate
    between neighbouring positions is per-site randomness wearing a field's name,
    and a field that correlates across the whole map is a global gradient, not a
    clump.
    """
    compiler = _load_compiler()
    span = compiler.MEADOW_CLUMP_FIELD_SPAN
    samples = [
        compiler._meadow_clump_field(x * 0.53, z * 0.53)
        for x in range(-60, 60)
        for z in range(-60, 60)
    ]
    assert all(0.0 <= value <= 1.0 for value in samples)
    mean = sum(samples) / len(samples)
    assert 0.40 < mean < 0.60, mean
    spread = math.sqrt(sum((v - mean) ** 2 for v in samples) / len(samples))
    assert spread > 0.15, spread
    # Deterministic and keyed only on world position: no seed argument exists.
    assert compiler._meadow_clump_field(12.5, -3.25) == \
        compiler._meadow_clump_field(12.5, -3.25)
    for distance, floor_value in ((0.5, 0.90), (2.0, 0.60)):
        near = _pearson(
            (compiler._meadow_clump_field(x * 0.53, z * 0.53),
             compiler._meadow_clump_field(x * 0.53 + distance, z * 0.53))
            for x in range(-40, 40) for z in range(-40, 40))
        assert near > floor_value, (distance, near)
    far = _pearson(
        (compiler._meadow_clump_field(x * 0.53, z * 0.53),
         compiler._meadow_clump_field(x * 0.53 + span * 8.0, z * 0.53))
        for x in range(-40, 40) for z in range(-40, 40))
    assert abs(far) < 0.25, far


def test_one_clump_value_drives_height_value_and_direction_together() -> None:
    """FL-4359/T1: height, ink value and direction move together, per clump."""
    compiler = _load_compiler()
    catalog = json.loads(CATALOG_PATH.read_text())
    payload = (ROOT / catalog["header"]["asset_payload"]["path"]).read_bytes()
    crests = _published_patch_crests(compiler, catalog, payload)
    assert len(crests) > 1000, len(crests)

    traits = [compiler.meadow_clump_traits(x, z) for x, z in crests]
    # Height and the authored shade lane come from the SAME field value, so they
    # can never disagree. Independent per-site draws would sit near zero here.
    assert _pearson((t["rise"], t["shade"]) for t in traits) > 0.99
    # Direction is the same value again, so a clump combs one way.
    assert _pearson((t["field"], t["heading"]) for t in traits) > 0.99
    assert all(
        abs(math.hypot(t["lean_x"], t["lean_z"]) - 1.0) < 1e-9 for t in traits)
    # The published clump metadata carries the same field that drives the
    # retained blade heights; each face then reads its parent clump index.
    assert _pearson(
        (height, compiler._meadow_clump_field(key[0], key[1]))
        for key, height in crests.items()) > 0.99

    # Spatial autocorrelation of the SHIPPED crest heights: neighbouring sites
    # share a tendency, distant sites do not. A per-site draw fails the first
    # assertion; a global gradient fails the second.
    buckets: dict[tuple[int, int], list] = {}
    for key in crests:
        buckets.setdefault((int(key[0] // 3), int(key[1] // 3)), []).append(key)
    neighbours = []
    for key in crests:
        bucket_x, bucket_z = int(key[0] // 3), int(key[1] // 3)
        closest = None
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for other in buckets.get((bucket_x + dx, bucket_z + dz), ()):
                    if other == key:
                        continue
                    gap = math.hypot(other[0] - key[0], other[1] - key[1])
                    if gap < 3.0 and (closest is None or gap < closest[0]):
                        closest = (gap, other)
        if closest is not None:
            neighbours.append((crests[key], crests[closest[1]]))
    assert len(neighbours) > 500, len(neighbours)
    assert _pearson(neighbours) > 0.3, _pearson(neighbours)

    ordered = sorted(crests)
    distant = [
        (crests[ordered[i]], crests[ordered[-1 - i]])
        for i in range(len(ordered) // 3)
        if math.hypot(ordered[i][0] - ordered[-1 - i][0],
                      ordered[i][1] - ordered[-1 - i][1]) > 40.0
    ]
    assert len(distant) > 200, len(distant)
    assert abs(_pearson(distant)) < 0.25, _pearson(distant)


def test_meadow_patches_author_no_per_site_colour() -> None:
    """FL-4359/T1: the clump moves VALUE only; hue has one owner, the ground.

    Intake sect. 16.4 item 1 names "a per-blade random hue" as the wrong
    application of sect. 9.4. The structural guarantee here is stronger than a
    colour comparison: a retained meadow face carries no colour lane at all.
    Lane 3 is the material, lanes 7/11/15 are each blade's local root, and
    every other lane is a vertex coordinate, so there is nowhere for a per-site
    hue to live.
    """
    compiler = _load_compiler()
    catalog = json.loads(CATALOG_PATH.read_text())
    payload = (ROOT / catalog["header"]["asset_payload"]["path"]).read_bytes()
    blades = 0
    roots: set[tuple[float, float, float]] = set()
    for placement, _clump_index, _clump, rows, world, offset in _meadow_faces_with_clumps(
            compiler, catalog, payload):
        material = int(world[offset + 3])
        assert material == compiler.MEADOW_BLADE_MAT
        root = (rows[offset + 7], rows[offset + 11], rows[offset + 15])
        # Every blade reports a distinct planted root in these lanes. The
        # material lane remains constant, so root ownership cannot be a hue.
        roots.add((round(root[0], 4), round(root[1], 4), round(root[2], 4)))
        blades += 1
    assert blades > 1000
    assert len(roots) == blades


def test_root_ecology_has_distinct_provenance_and_terrain_support() -> None:
    compiler = _load_compiler()
    catalog = compiler.compile_catalog(compiler.DEFAULT_MAP, compiler.DEFAULT_TREE_MANIFEST)
    decoded = compiler._decode_map(compiler.DEFAULT_MAP)
    records = {r["content_key"]: r for r in catalog["asset_records"]}
    total_moss = total_flowers = 0
    for placement in [p for c in catalog["chunks"] for p in c["asset_instances"]]:
        if placement["source_kind"] in ("compiled_meadow", "authored_fixture"):
            continue
        record = records[placement["content_key"]]
        rows = list(struct.unpack_from(f'<{record["face_count"] * 16}f', catalog.payload_bytes, record["rows_offset"]))
        split = placement["authored_face_count"] * 16
        expected = compiler.build_root_ecology_rows(placement, rows[:split], decoded)
        assert rows[split:] == expected
        assert len(expected) == placement["root_ecology_face_count"] * 16
        world = compiler.transform_tree_rows(expected, placement["world_xz"], placement["authored_godot_y"],
                                             placement["yaw_radians"], placement["procedural_scale"])
        for offset in range(0, len(world), 16):
            material = int(world[offset + 3])
            assert material in (244, 245)
            total_moss += material == 244
            total_flowers += material == 245
            points = [world[offset + c:offset + c + 3] for c in (0, 4, 8, 12)]
            for x, y, z in points:
                if world[offset + 15] == 4709.0:
                    # Attached moss must remain inside the trunk's lower region.
                    assert material == 244
                    assert y >= placement["root_base_world"][1] - 0.04
                    assert placement["world_aabb_min"][0] - 0.04 <= x <= placement["world_aabb_max"][0] + 0.04
                else:
                    ground = compiler._sample_capture_ground(decoded, x, z)
                    assert ground is not None and ground[1] in (0, 1, 3)
                    assert ground[0] > 49 / 16
                    assert 0.025 <= y - ground[0] <= 0.85
    assert total_moss > 0 and total_flowers > 0
    assert total_flowers < total_moss # flower and mushroom faces stay sparse


def test_runtime_consumes_catalog_not_pre_catalog_tree_manifest() -> None:
    world = (ROOT / "godot_project/scripts/World.gd").read_text()
    partition = (ROOT / "godot_project/scripts/world/WorldPartition.gd").read_text()
    scene_db = (ROOT / "godot_project/render/scene/SceneGeometryDB.gd").read_text()
    compositor = (
        ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
    ).read_text()
    assert "FL4789_CHUNK_CATALOG_RESOURCE" in world
    assert "fl4789_chunk_catalog.v1.json" in world
    assert "FL4708_Y8_TREE_INSTANCES_RESOURCE" not in world
    assert "fl4708_y8_tree_instances.v1.json" not in world
    assert "_process_world_partition_rebuild()" in world
    assert "FL4789_VSB_STATIC_FACE_DOMAIN_MAX := 0xFFFF" in world
    assert "FL4789_RESIDENCY_RADIUS_CHUNKS" not in world
    assert "WorldPartitionScript.DEFAULT_RADIUS_CHUNKS" in world
    assert "const DEFAULT_RADIUS_CHUNKS := 3" in partition
    assert (
        "_scene_geometry_asset_payload_cache" in world
        or "_install_fl4789_compiled_tree_registry" in world
    )
    assert "set_shared_asset_payload_cache" in world or "admit_compiled_asset" in world
    assert (
        "_fl4789_tree_row_cache" in world
        or "_install_fl4789_compiled_tree_registry" in world
    )
    assert "_shared_asset_payload_cache" in scene_db
    assert "_queue_gpu_generation_retirement" in compositor
    assert "_actor_atlas_image_cache" in compositor
    assert "all_requested_layers_present" in compositor
    assert "_scene_geometry_build_thread" not in world
    assert "all_asset_records" in partition
    assert (
        "_preload_fl4789_authored_tree_assets" in world
        or "_install_fl4789_compiled_tree_registry" in world
    )
    assert "queue_scene_geometry_residency" in compositor
    assert "replace_resident_instances" in scene_db
    crossing = world.split("func _process_world_partition_rebuild", 1)[1].split(
        "func _publish_partitioned_static_world_mesh", 1
    )[0]
    assert "scene_db.build" not in crossing
    assert "build_authoritative_corner_height_image" not in crossing
    assert "build_authoritative_mat_image" not in crossing
    assert "_publish_partitioned_static_world_residency_delta" in crossing


def test_actor_atlas_catalog_has_stable_ids_and_profile_residency() -> None:
    compiler = _load_actor_atlas_compiler()
    catalog = compiler.compile_catalog(
        compiler.DEFAULT_PROFILES, compiler.DEFAULT_SIDECARS
    )
    assert ACTOR_ATLAS_CATALOG_PATH.read_text() == compiler.render_catalog(catalog)
    assert catalog["$schema"] == "fl4789_actor_atlas_catalog.v1"
    sources = catalog["sources"]
    assert [row["source_id"] for row in sources] == list(range(len(sources)))
    paths = [row["atlas_path"] for row in sources]
    assert paths == sorted(paths)
    residency = catalog["residency_by_profile"]
    idle = residency["normal_player.idle_walk.default.unmounted.normal_sword"]
    attack = residency["normal_player.attack.default.unmounted.normal_sword"]
    death = residency["normal_player.death.default.unmounted.normal_sword"]
    assert idle == attack == death
    resident_paths = {paths[source_id] for source_id in idle}
    assert "res://assets/xp_sprites/player-body.png" in resident_paths
    assert "res://assets/xp_sprites/attack-body.png" in resident_paths
    assert "res://assets/xp_sprites/player-weapon-sword.png" in resident_paths
    sekiro_ids = catalog["residency_scopes"][compiler.SEKIRO_SCOPE_NAME]
    sekiro_paths = {paths[source_id] for source_id in sekiro_ids}
    assert len(sekiro_paths) == 16
    assert "res://assets/xp_sprites/attack-0011.png" in sekiro_paths
    assert "res://assets/xp_sprites/player-shield-regular.png" in sekiro_paths
    assert "res://assets/xp_sprites/plydie-0000.png" in sekiro_paths
    assert {
        f"res://assets/xp_sprites/item-{kind}.png"
        for kind in ("armor", "helmet", "shield", "sword")
    }.issubset(sekiro_paths)


def test_actor_atlas_render_callback_has_no_file_decode_or_layer_sort() -> None:
    compositor = (
        ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
    ).read_text()
    ensure = compositor.split("func _ensure_actor_atlas", 1)[1].split(
        "func _upload_glyph_coverage", 1
    )[0]
    prefetch = compositor.split(
        "func _prefetch_fl4789_actor_atlas_sources", 1
    )[1].split("func publish_actor_sprite_rows_for_frame", 1)[0]
    assert ".load(" not in ensure
    assert "paths.sort" not in ensure
    assert "image.load(path)" in prefetch
    assert "_actor_atlas_catalog_paths.append(path)" in prefetch
    sealed_gate = prefetch.index("if _actor_atlas_residency_sealed")
    gameplay_decode = prefetch.index("image.load(path)")
    assert sealed_gate < gameplay_decode
    assert "sealed actor atlas rejected gameplay growth" in prefetch

    prewarm = compositor.split("func prewarm_actor_atlas_scope", 1)[1].split(
        "func _collect_fl4789_actor_atlas_row_facts", 1
    )[0]
    assert "image.load(path)" in prewarm
    assert "_actor_atlas_residency_sealed = true" in prewarm


def test_partition_crossing_publishes_only_a_residency_delta() -> None:
    world = (ROOT / "godot_project/scripts/World.gd").read_text()
    compositor = (
        ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
    ).read_text()
    scene_db = (ROOT / "godot_project/render/scene/SceneGeometryDB.gd").read_text()
    crossing = world.split(
        "func _publish_partitioned_static_world_residency_delta", 1
    )[1].split("\nfunc ", 1)[0]
    apply_delta = compositor.split(
        "func _apply_pending_scene_geometry_residency", 1
    )[1].split("# FL-4431 activation", 1)[0]
    assert "queue_scene_geometry_residency" in crossing
    assert "build_authoritative_corner_height_image" not in crossing
    assert "build_authoritative_mat_image" not in crossing
    assert "SceneGeometryDBScript.new" not in crossing
    assert "face_rows" not in crossing
    assert "replace_resident_instances" in apply_delta
    assert "_vsb_heightfield_cache_revision = -1" not in apply_delta
    assert "_vsb_water_cache_revision = -1" not in apply_delta
    assert "full_scene_rebuild=false" in apply_delta
    assert "var _asset_revision" in scene_db
    assert "_vsb_semantic_static_asset_cache" in scene_db
    aabb_refresh = scene_db.split(
        "func _refresh_instance_world_aabb", 1
    )[1].split("# TLAS build", 1)[0]
    assert "flatten_instance_rows" not in aabb_refresh
    assert "for corner_index in range(8)" in aabb_refresh
