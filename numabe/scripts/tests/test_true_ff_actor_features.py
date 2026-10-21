"""Offline contract tests for the actor extension of the single True FF bank.

These tests import the two compiler modules only.  They never launch Godot and
never infer actor identity from a projected or screen-space value.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"
ACTOR_DB = ROOT / "godot_project/render/scene/geometry/actors/ActorPoseGeometryDB.gd"
ADAPTER = FEATURES / "TrueFFActorFeatures.gd"
SOURCE_CONTRACT = ROOT / "godot_project/render/core/contracts/source/render_source_contract.glslinc"
HUNTER = ROOT / "godot_project/assets/actor_mesh/hunter_rig_poses.json"
# FL-4907 XBOT_52_BONE_OCTAHEDRON_RIG_20261002: the X Bot is no longer a
# per-pose row product; its 52 rig parts are rigid banks (true_ff_rigid_features).


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bank = _module("true_ff_feature_bank_actor_test", ROOT / "scripts/true_ff_feature_bank.py")
actor = _module("true_ff_actor_features_test", ROOT / "scripts/true_ff_actor_features.py")


@pytest.fixture(scope="module")
def shipped_compiled():
    """Compile the real Hunter product once through the production interface."""
    return actor.compile_actor_feature_sources([HUNTER], bank, ROOT)


def _triangle_rows(offset: float = 0.0, parts: tuple[int, ...] = (0, 1)) -> list[float]:
    rows: list[float] = []
    for face, part in enumerate(parts):
        x = offset + float(face) * 2.0
        rows.extend([
            x, 0.0, 0.0, 0.0,
            x + 1.0, 0.0, 0.0, 0.0,
            x, 1.0, 0.0, 0.0,
            x, 1.0, 0.0, float(part),
        ])
        rows[face * 16 + 15] = float(part)
    return rows


def _source_certificates(product):
    """Attach explicit source certificates to in-memory product fixtures."""
    product.setdefault("geometry_contract", {})["face_part_identity"] = (
        "row 3 w = base bone index * 8192 + triangle ordinal inside the body"
    )
    for pose in product["poses"]:
        pose["content_sha256"] = hashlib.sha256(
            struct.pack(f'<{len(pose["face_rows"])}f', *pose["face_rows"])
        ).hexdigest()
    product["content_sha256"] = hashlib.sha256(
        json.dumps(product, sort_keys=True).encode()
    ).hexdigest()
    return product


@pytest.mark.parametrize("owner", ["product", "pose"])
@pytest.mark.parametrize("invalid", [None, "", "0" * 63, "g" * 64, 123])
def test_invalid_source_certificate_rejected_before_bank_build(tmp_path, monkeypatch, owner, invalid):
    product = _source_certificates({"actor_kind": "fixture", "poses": [
        {"pose_id": "first", "face_count": 2, "face_rows": _triangle_rows()},
        {"pose_id": "second", "face_count": 2, "face_rows": _triangle_rows(.1)},
    ]})
    record = product if owner == "product" else product["poses"][1]
    if invalid is None:
        del record["content_sha256"]
    else:
        record["content_sha256"] = invalid
    path = tmp_path / "bad_source_certificate.json"
    path.write_text(json.dumps(product), encoding="utf-8")
    def forbidden_build(*args, **kwargs):
        pytest.fail("invalid source certificate reached bank generation")
    monkeypatch.setattr(actor, "_build_pose_bank", forbidden_build)
    result = actor.compile_actor_feature_sources([path], bank, ROOT)
    assert result["banks"] == {}
    assert result["manifest"]["products"] == {}
    assert result["manifest"]["rejections"] == [
        {"product": str(path), "reason": f"{owner}_source_digest_invalid"}
    ]


def test_source_certificate_alias_matches_actor_database(tmp_path):
    product = _source_certificates({"actor_kind": "fixture", "poses": [
        {"pose_id": "first", "face_count": 2, "face_rows": _triangle_rows()},
    ]})
    product["content_hash"] = product.pop("content_sha256")
    pose = product["poses"][0]
    pose["content_hash"] = pose.pop("content_sha256")
    path = tmp_path / "source_certificate_alias.json"
    path.write_text(json.dumps(product), encoding="utf-8")
    result = actor.compile_actor_feature_sources([path], bank, ROOT)
    assert result["manifest"]["rejections"] == []
    compiled = result["manifest"]["products"]["fixture"]
    assert compiled["product_content_sha256"] == product["content_hash"]
    assert compiled["poses"]["first"]["pose_source_hash"] == pose["content_hash"]


def test_shipped_source_certificate_preflight_preserves_manifest_values():
    manifest = json.loads((ROOT / "godot_project/assets/world_partition/"
                           "true_ff_actor_feature_sources.v1.json").read_text())
    count = 0
    assert set(manifest["products"]) == {"hunter"}
    for kind, path in (("hunter", HUNTER),):
        source = json.loads(path.read_text())
        compiled = manifest["products"][kind]
        assert actor._source_digest(source, "product") == compiled["product_content_sha256"]
        for pose in actor._pose_records(source):
            assert actor._source_digest(pose, "pose") == \
                compiled["poses"][pose["pose_id"]]["pose_source_hash"]
            count += 1
    assert count == 93


def test_source_certificate_keeps_runtime_precedence_and_case():
    # The runtime uses the primary field when present, even if invalid; do
    # not silently rescue it with a different alias or normalize its spelling.
    with pytest.raises(actor.ActorTopologyError, match="pose_source_digest_invalid"):
        actor._source_digest({"content_sha256": "", "content_hash": "a" * 64}, "pose")
    assert actor._source_digest({"content_sha256": "A" * 64}, "pose") == "A" * 64


def test_extension_uses_the_existing_mesh_bank_and_emits_actor_manifest():
    result = actor.compile_actor_feature_sources([HUNTER], bank)
    assert not result["manifest"]["rejections"]
    product = result["manifest"]["products"]["hunter"]
    assert product["pose_count"] == 93
    assert product["feature_count"] > 0
    assert len(result["banks"]) == 91
    assert all(entry["stable_identity"] == actor.ACTOR_FEATURE_IDENTITY
               for entry in product["poses"].values())
    for digest, compiled in result["banks"].items():
        assert compiled["content_hash"] == digest
        assert compiled["actor_feature_identity"] == actor.ACTOR_FEATURE_IDENTITY


def test_edge_stable_ids_survive_pose_geometry_changes(tmp_path: Path):
    products = {
        "schema": "asciicker.actor_mesh.rig_animation_rows.v3",
        "actor_kind": "synthetic",
        "poses": [
            {"pose_id": "pose_0", "face_count": 2, "face_rows": _triangle_rows()},
            {"pose_id": "pose_1", "face_count": 2, "face_rows": _triangle_rows(0.125)},
        ],
    }
    # Exercise the product path without introducing a command-line/compiler
    # surface; the temporary JSON remains a pytest fixture only.
    # ``compile_actor_feature_sources`` intentionally accepts files so callers
    # can share the product loader with the production asset route.
    path = tmp_path / "actor_features_fixture.json"
    path.write_text(json.dumps(_source_certificates(products)), encoding="utf-8")
    compiled = actor.compile_actor_feature_sources([path], bank, ROOT)
    assert not compiled["manifest"]["rejections"]
    poses = compiled["manifest"]["products"]["synthetic"]["poses"]
    first = compiled["banks"][poses["pose_0"]["content_hash"]]
    second = compiled["banks"][poses["pose_1"]["content_hash"]]
    first_ids = [struct.unpack_from("<4I", first["features"], i + 48)[2]
                 for i in range(0, len(first["features"]), 64)]
    second_ids = [struct.unpack_from("<4I", second["features"], i + 48)[2]
                  for i in range(0, len(second["features"]), 64)]
    assert set(first_ids) == set(second_ids)
    assert len(first_ids) == len(set(first_ids))


def test_actor_bank_digest_includes_address_layout_version_and_signature():
    rows = _triangle_rows()
    first = actor._build_pose_bank(
        rows, 2, "synthetic", bank, address_signature="layout-a"
    )
    second = actor._build_pose_bank(
        rows, 2, "synthetic", bank, address_signature="layout-b"
    )
    assert first["content_hash"] != second["content_hash"]
    assert first["address_layout_version"] == actor.ACTOR_ADDRESS_LAYOUT_VERSION


def test_actor_bank_digest_includes_product_identity():
    rows = _triangle_rows()
    a = actor._build_pose_bank(rows, 2, "kind_a", bank)
    b = actor._build_pose_bank(rows, 2, "kind_b", bank)
    assert a["features"] != b["features"]
    assert a["content_hash"] != b["content_hash"]


def test_actor_bank_digest_includes_serialized_tree_order():
    rows = _triangle_rows(parts=tuple(range(12)))
    triangles = bank.triangles_from_rows(rows, 12)
    features, edges, _ = bank.mesh_features(triangles)
    root = actor._build_reference_tree(features, bank)
    assert root.children
    a = actor._build_pose_bank(rows, 12, "fixture", bank, root_nodes=root)
    root.children.reverse()
    b = actor._build_pose_bank(rows, 12, "fixture", bank, root_nodes=root)
    assert a["features"] != b["features"]
    assert a["canonical_address_signature"] != b["canonical_address_signature"]
    assert a["content_hash"] != b["content_hash"]


def test_actor_quad_split_has_distinct_source_edge_addresses():
    rows = [0., 0., 0., 207., 1., 0., 0., 0.,
            1., 1., 0., 0., 0., 1., 0., 17.]
    result = actor._build_pose_bank(rows, 1, "quad", bank)
    assert result["feature_count"] == 5
    assert len(set(result["canonical_addresses"])) == 5


def test_source_orientation_restores_arc_endpoint_correspondence():
    triangles = bank.triangles_from_rows(_triangle_rows(), 2)
    features, _, _ = bank.mesh_features(triangles)
    snapshots = {id(f): (f.p0, f.p1, f.k0, f.k1, f.fn0, f.fn1, f.vn0, f.vn1)
                 for f in features}
    bank.build_chains(features)
    expected = {id(f): {f.k0: f.a0, f.k1: f.a1} for f in features}
    # Force a reversed traversal in one fixture edge to cover both branches.
    f = features[0]
    f.p0, f.p1, f.k0, f.k1 = f.p1, f.p0, f.k1, f.k0
    f.a0, f.a1 = f.a1, f.a0
    actor._restore_source_orientation(features, snapshots)
    for f in features:
        assert f.a0 == expected[id(f)][f.k0]
        assert f.a1 == expected[id(f)][f.k1]


def test_shipped_hunter_product_compiles_with_one_address_layout(shipped_compiled):
    """The production compile path owns one canonical layout per product."""
    result = shipped_compiled
    assert bank.ABI["NODE_BYTES"] == 64
    assert bank.ABI["FACE_BYTES"] == 16
    assert bank.ABI["FEATURE_BYTES"] == 64
    assert result["manifest"]["p0_actor_kind"] == 128
    manifest = result["manifest"]
    assert manifest["schema"] == actor.ACTOR_FEATURE_MANIFEST_SCHEMA
    assert manifest["identity"] == actor.ACTOR_FEATURE_IDENTITY
    assert manifest["address_layout_version"] == actor.ACTOR_ADDRESS_LAYOUT_VERSION
    assert manifest["rejections"] == []
    assert set(manifest["products"]) == {"hunter"}
    assert len(result["banks"]) > 0
    for product in manifest["products"].values():
        assert product["address_layout_version"] == actor.ACTOR_ADDRESS_LAYOUT_VERSION
        assert product["canonical_address_signature"]
        assert product["topology_signature"]
        assert all(
            pose["canonical_address_signature"] == product["canonical_address_signature"]
            for pose in product["poses"].values()
        )
    assert all(
        compiled["address_layout_version"] == actor.ACTOR_ADDRESS_LAYOUT_VERSION
        for compiled in result["banks"].values()
    )


@pytest.mark.parametrize(
    "actor_kind,product_path,require_dynamic_classes",
    [("hunter", HUNTER, False)],
)
def test_production_pose_banks_preserve_feature_face_and_node_addresses(
    actor_kind: str, product_path: Path, require_dynamic_classes: bool, shipped_compiled,
):
    """Real shipped products retain addresses while geometry/classes remain live."""
    compiled = shipped_compiled
    manifest = compiled["manifest"]
    assert not manifest["rejections"]
    product = manifest["products"][actor_kind]
    poses = list(product["poses"].values())
    first = poses[0]
    first_bank = compiled["banks"][first["content_hash"]]
    def class_signature(value):
        return tuple(
            struct.unpack_from("<I", value["features"], offset + 48)[0]
            for offset in range(0, len(value["features"]), 64)
        )
    if require_dynamic_classes:
        second = next(
            pose for pose in poses[1:]
            if pose["content_hash"] != first["content_hash"]
            and class_signature(compiled["banks"][pose["content_hash"]]) != class_signature(first_bank)
        )
    else:
        second = next(pose for pose in poses[1:] if pose["content_hash"] != first["content_hash"])
    second_bank = compiled["banks"][second["content_hash"]]
    first_ids = [
        struct.unpack_from("<I", first_bank["features"], offset + 56)[0]
        for offset in range(0, len(first_bank["features"]), 64)
    ]
    second_ids = [
        struct.unpack_from("<I", second_bank["features"], offset + 56)[0]
        for offset in range(0, len(second_bank["features"]), 64)
    ]
    first_faces = list(struct.iter_unpack("<4I", first_bank["faces"]))
    second_faces = list(struct.iter_unpack("<4I", second_bank["faces"]))
    assert first_ids == second_ids
    assert first_faces == second_faces
    assert first_bank["canonical_address_signature"] == second_bank["canonical_address_signature"]
    assert first_bank["canonical_addresses"] == second_bank["canonical_addresses"]
    assert first_bank["output_addresses"] == second_bank["output_addresses"]
    assert len(first_bank["output_addresses"]) == len(set(first_bank["output_addresses"]))

    # Node spheres/cones are pose-refitted, but child order, feature ranges,
    # and leaf face ranges remain the reference tree's topology.
    first_nodes = list(struct.iter_unpack("<4f4f4I4I", first_bank["nodes"]))
    second_nodes = list(struct.iter_unpack("<4f4f4I4I", second_bank["nodes"]))
    def node_topology(row):
        # flags (index 12) are live classification/bypass facts; all ranges,
        # child order, level and face spans are retained topology.
        return (row[8], row[9], row[10], row[11], row[13], row[14], row[15])
    assert [node_topology(row) for row in first_nodes] == [node_topology(row) for row in second_nodes]
    assert any(first[:8] != second[:8] for first, second in zip(first_nodes, second_nodes))
    assert first_bank["node_count"] == second_bank["node_count"]
    assert first_bank["feature_count"] == second_bank["feature_count"]
    first_classes = [
        struct.unpack_from("<I", first_bank["features"], offset + 48)[0]
        for offset in range(0, len(first_bank["features"]), 64)
    ]
    second_classes = [
        struct.unpack_from("<I", second_bank["features"], offset + 48)[0]
        for offset in range(0, len(second_bank["features"]), 64)
    ]
    if require_dynamic_classes:
        assert first_classes != second_classes

    def endpoints(raw):
        return [
            (tuple(row[0:3]), tuple(row[4:7]), int(row[14]))
            for row in struct.iter_unpack("<4f4f4I4I", raw)
        ]

    # Stable IDs and output order are the address correspondence; endpoints
    # are allowed to deform, but every corresponding feature remains finite and
    # source-directed rather than being rematched by rounded pose geometry.
    first_endpoints, second_endpoints = endpoints(first_bank["features"]), endpoints(second_bank["features"])
    assert len(first_endpoints) == len(second_endpoints) == len(first_ids)
    assert all(
        all(math.isfinite(value) for value in (*a[0], *a[1], *b[0], *b[1]))
        and a[2] == b[2]
        for a, b in zip(first_endpoints, second_endpoints)
    )

    # The output endpoints still follow each pose's first-seen source edge
    # direction.  This catches build_chains reversing an edge without swapping
    # its arc endpoints and endpoint normals as one unit.
    source_product = json.loads(product_path.read_text(encoding="utf-8"))
    source_cache = {}
    def source_endpoints(pose_entry, bank_value):
        pose = next(
            item for item in actor._pose_records(source_product)
            if str(item.get("pose_id", "")) == pose_entry["pose_id"]
        )
        count = int(pose["face_count"])
        rows = actor._load_rows(source_product, pose, ROOT, source_cache)
        triangles = bank.triangles_from_rows(rows, count)
        features, tri_edges, _normals = bank.mesh_features(triangles)
        addresses, _faces = actor._edge_layout(rows, triangles, tri_edges, features)
        by_address = {
            addresses[int(feature.local)]: (
                tuple(feature.p0),
                tuple(feature.p1),
                tuple(bank.oct_encode(normal) for normal in (
                    feature.fn0, feature.fn1, feature.vn0, feature.vn1
                )),
            )
            for feature in features
        }
        expected = [by_address[address] for address in bank_value["output_addresses"]]
        return expected

    first_source = source_endpoints(first, first_bank)
    second_source = source_endpoints(second, second_bank)
    assert [(a[0], a[1]) for a in first_endpoints] == [a[:2] for a in first_source]
    assert [(a[0], a[1]) for a in second_endpoints] == [a[:2] for a in second_source]
    assert [
        tuple(struct.unpack_from("<4I", first_bank["features"], offset + 32))
        for offset in range(0, len(first_bank["features"]), 64)
    ] == [a[2] for a in first_source]
    assert [
        tuple(struct.unpack_from("<4I", second_bank["features"], offset + 32))
        for offset in range(0, len(second_bank["features"]), 64)
    ] == [a[2] for a in second_source]

    # Every refitted sphere conservatively contains every endpoint in its
    # retained feature range (no union-of-poses bound is used).
    for node in second_nodes:
        centre, radius = node[0:3], node[3]
        first_feature, feature_count = int(node[10]), int(node[11])
        for feature_index in range(first_feature, first_feature + feature_count):
            p0, p1, _sid = second_endpoints[feature_index]
            assert all(
                sum((point[j] - centre[j]) ** 2 for j in range(3)) <= (radius + 1e-4) ** 2
                for point in (p0, p1)
            )


def test_topology_change_rejects_the_product_without_fake_continuity(tmp_path: Path):
    product = {
        "schema": "asciicker.actor_mesh.rig_animation_rows.v3",
        "actor_kind": "synthetic",
        "poses": [
            {"pose_id": "pose_0", "face_count": 1, "face_rows": _triangle_rows()[:16]},
            {"pose_id": "pose_1", "face_count": 2, "face_rows": _triangle_rows()},
        ],
    }
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(_source_certificates(product)), encoding="utf-8")
    result = actor.compile_actor_feature_sources([path], bank, ROOT)
    assert result["banks"] == {}
    assert result["manifest"]["products"] == {}
    assert result["manifest"]["rejections"] == [
        {"product": str(path), "reason": "topology_varies_between_poses"}
    ]


def test_equal_counts_do_not_certify_actor_corner_correspondence(tmp_path: Path):
    # Both poses have two triangles, five welded edges and the same part
    # words. But face 1 changes which edge of face 0 it shares. A count check
    # cannot certify the source adjacency used by persistent contour phase.
    def rows(second):
        vertices = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.), (1., 1., 0.))
        result = []
        for part, corners in enumerate(((0, 1, 2), second)):
            a, b, c = (vertices[i] for i in corners)
            result.extend((*a, 207., *b, 0., *c, 0., *c, float(part)))
        return result
    first, second = rows((1, 3, 2)), rows((0, 3, 2))
    a = actor._build_pose_bank(first, 2, "fixture", bank)
    b = actor._build_pose_bank(second, 2, "fixture", bank)
    assert a["feature_count"] == b["feature_count"] == 5
    product = {"actor_kind": "fixture", "poses": [
        {"pose_id": "first", "face_count": 2, "face_rows": first},
        {"pose_id": "second", "face_count": 2, "face_rows": second},
    ]}
    path = tmp_path / "changed_adjacency.json"
    path.write_text(json.dumps(_source_certificates(product)), encoding="utf-8")
    result = actor.compile_actor_feature_sources([path], bank, ROOT)
    assert not result["manifest"]["products"], "equal counts admitted changed source topology"
    assert result["manifest"]["rejections"][0]["reason"] == "topology_varies_between_poses"


def test_every_shipped_pose_preserves_its_corner_partition(shipped_compiled):
    # Exercise the same loader, triangle split and partition used by the
    # compiler over all shipped poses. No bank regeneration or Godot run.
    compiled = shipped_compiled
    manifest = compiled["manifest"]
    examined = 0
    for product_entry in manifest["products"].values():
        product = json.loads((ROOT / product_entry["product_path"]).read_text())
        reference = None
        cache = {}
        for pose in actor._pose_records(product):
            count = int(pose["face_count"])
            rows = actor._load_rows(product, pose, ROOT, cache)
            partition = actor._corner_incidence(bank.triangles_from_rows(rows, count), bank)
            if reference is None:
                reference = partition
                encoded = json.dumps([actor._part_words(rows, count), partition],
                                     separators=(",", ":")).encode()
                assert product_entry["topology_signature"] == hashlib.sha256(encoded).hexdigest()
            assert partition == reference, (product_entry["product_path"], pose["pose_id"])
            examined += 1
    assert examined == 93


def test_corner_partition_preserves_shared_vertices_under_affine_motion():
    triangles = [(((0., 0., 0.), (1., 0., 0.), (0., 1., 0.)), 207, 0),
                 (((1., 0., 0.), (1., 1., 0.), (0., 1., 0.)), 207, 1)]
    moved = [(tuple((3 + 2*x + y, -2 + 3*y, 7 + z) for x, y, z in points), mat, face)
             for points, mat, face in triangles]
    assert actor._corner_incidence(triangles, bank) == actor._corner_incidence(moved, bank)
    # Contact at only one vertex changes connectivity without changing edges.
    disconnected = [(triangles[0][0], 207, 0),
                    (((0., 0., 0.), (-1., 0., 0.), (0., -1., 0.)), 207, 1)]
    separated = [disconnected[0],
                 (tuple((x, y, z + 1) for x, y, z in disconnected[1][0]), 207, 1)]
    assert actor._corner_incidence(disconnected, bank) != actor._corner_incidence(separated, bank)


def test_adapter_and_actor_db_pin_read_only_source_shape_and_p0_identity():
    adapter = ADAPTER.read_text(encoding="utf-8")
    db = ACTOR_DB.read_text(encoding="utf-8")
    assert f'const MANIFEST_SCHEMA := "{actor.ACTOR_FEATURE_MANIFEST_SCHEMA}"' in adapter
    assert f'const IDENTITY_SCHEMA := "{actor.ACTOR_FEATURE_IDENTITY}"' in adapter
    assert "func actor_p0_object_id(actor_id: int) -> int:" in adapter
    assert "return (ACTOR_POSE_KIND << 24) | actor_id" in adapter
    assert '"content_hash": content_hash' in adapter
    assert '"basis": Basis(Vector3.UP, float(yaw_value))' in adapter
    assert '"scale": 1.0' in adapter
    assert '"origin": root_value as Vector3' in adapter
    assert '"semantic_object_id": semantic_object_id' in adapter
    assert "func true_ff_feature_source_facts() -> Array[Dictionary]:" in db
    # DB's emitted part-word law did not change. The FF bank address layout
    # is a different contract; do not rename the shared source fact to v2.
    assert '"stable_part_identity": "actor_part_edge_v1"' in db
    assert "pose_content_hash" in db
    contract = SOURCE_CONTRACT.read_text(encoding="utf-8")
    assert "const uint RENDER_SOURCE_ACTOR_POSE_KIND = 128u;" in contract
    assert "return (RENDER_SOURCE_ACTOR_POSE_KIND << 24u) | actor_id;" in contract


def test_adapter_residency_is_product_address_identity_not_selected_pose_or_kind():
    """Source gate; not a substitute for live actor/feature-row coverage."""
    adapter = ADAPTER.read_text(encoding="utf-8")
    assert "_topology_by_kind[kind] = JSON.stringify([IDENTITY_SCHEMA, kind," in adapter
    assert "_resident_banks[digest] = bank_value" in adapter
    assert "_resident_banks.has(String(source[\"content_hash\"]))" in adapter
    assert '"topology_identity": topology_identity' in adapter
    assert "_resident_banks[actor_kind]" not in adapter
    assert "actor_product_pose_map_invalid" in adapter
    assert "actor_product_resident_digest_invalid" in adapter
    assert "actor_p0_object_id_duplicate" in adapter
    assert '"deformation_bound_basis": "selected_pose_refit"' in adapter
    assert '"deformation_bounds": Vector2.ZERO' in adapter
    assert 'result["missing_pose_ids"] = missing' in adapter
    assert 'result["ready"] = not rejection_reasons.has("*")' in adapter
    assert not __import__("re").search(r"\bvar\s+\w+\s*:=", adapter)
