"""Offline rigid-part source tests. No renderer launch or visual claim."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = load("true_ff_rigid_features")
B = load("true_ff_feature_bank")


def face(x=0):
    return [x, 0, 0, 207, x + 1, 0, 0, 1, x, 1, 0, 0, x, 1, 0, 0]


def test_part_split_preserves_geometry_and_register_mesh_lane():
    rows = face() + face(2) + face(4)
    parts = R.split_parts(rows, [2, 1, 2])
    assert list(parts) == [2, 1]
    assert len(parts[2]) == 32 and len(parts[1]) == 16
    assert parts[2][11] == parts[2][27] == 2.0
    assert parts[1][11] == 1.0
    assert rows[11] == 0  # input belongs to the geometry owner
    for part, data in parts.items():
        bank = B.build_mesh_bank(data, len(data) // 16)
        assert bank["content_hash"] == B.content_hash_of(data, len(data) // 16)
        assert bank["feature_count"] > 0


@pytest.mark.parametrize("parts", [[0], [-1, 0], [0, R.MAX_PARTS]])
def test_invalid_part_publication_rejected(parts):
    with pytest.raises(ValueError):
        R.split_parts(face() * 2, parts)


def test_authored_part_ranges_cannot_overlap_or_leave_holes():
    doc = {"rows": face() * 2, "face_count": 2,
           "parts": [{"face_start": 0, "face_count": 1}]}
    with pytest.raises(ValueError, match="unowned"):
        R.part_indices(doc, 2, 1)
    doc["parts"].append({"face_start": 0, "face_count": 2})
    with pytest.raises(ValueError, match="overlapping"):
        R.part_indices(doc, 2, 1)


def test_production_meshes_have_multiple_independently_moving_parts():
    sources = list(R.rigid_sources(ROOT / "godot_project"))
    tags = {row["source"] for row in sources}
    for name in ("airplane_airframe_faces.json", "race_car_faces.json", "longbow_weapon.json"):
        assert sum(name in tag for tag in tags) > 1
    for row in sources:
        assert len(row["rows"]) == row["face_count"] * 16
        assert len({row["rows"][at + 11] for at in range(0, len(row["rows"]), 16)}) == 1


def test_runtime_adapter_retains_meshes_and_only_reads_current_transforms():
    source = (ROOT / "godot_project/render/ascii/features/TrueFFRigidFeatures.gd").read_text()
    changes = source.split("func changes(", 1)[1].split("func withdraw(", 1)[0]
    descriptors = source.split("func _descriptor_sources(", 1)[1].split("func _representation(", 1)[0]
    assert "_mesh_revision != int(_rigid.mesh_revision)" in changes
    assert "instance.poses[int(part)]" in descriptors
    assert "build_mesh_bank" not in descriptors and "rows.slice" not in descriptors
    assert "VSB_DYNAMIC_KIND_IDS" in descriptors
    assert '"missing_rigid_parts"' in source


def test_rigid_address_shift_uses_static_owner_not_variant_inference():
    """Source regression for the operator's 20261001-173058 parse failure.

    This is not a GDScript semantic compiler: gdparse also accepts the broken
    initializer. Pin the static constant dependency that removes that defect.
    """
    source = (ROOT / "godot_project/render/ascii/features/TrueFFRigidFeatures.gd").read_text()
    assert 'const RigidGeometry := preload("res://render/scene/geometry/dynamic/rigid/RigidMoverGeometry.gd")' in source
    assert "var index: int = (first >> RigidGeometry.INSTANCE_SHIFT) & 0x3ff" in source
    assert "rigid.INSTANCE_SHIFT" not in source


def test_active_missing_banks_are_required_sources_not_silent_geometry_deletion():
    """Pins adapter control flow only; runtime bank admission remains unexecuted."""
    source = (ROOT / "godot_project/render/ascii/features/TrueFFRigidFeatures.gd").read_text()
    retain = source.split("func _retain_meshes(", 1)[1].split("func resident_hashes(", 1)[0]
    assert retain.index("retained[part] = digest") < retain.index("Bank.read_bank(digest)")
    assert "_parts[String(mesh_key)] = retained" in retain
    descriptors = source.split("func _descriptor_sources(", 1)[1].split("func _representation(", 1)[0]
    missing = descriptors.split("if _missing.has(part_key):", 1)[1].split("var source_identity", 1)[0]
    assert "missing[source_key] = parts[part]" in missing
    assert "instance_valid = false" in missing
    assert '"content_hash": parts[part]' in descriptors
    assert '"active_missing_rigid_parts": _active_missing' in source
    assert '"ready": _prepared and not _rejections.has("*")' in source


def test_retained_movers_publish_current_bounds_and_keep_small_nonzero_transforms():
    source = (ROOT / "godot_project/render/ascii/features/TrueFFRigidFeatures.gd").read_text()
    assert "if determinant == 0.0:" in source
    assert "is_zero_approx" not in source
    assert '"deformation_bound_basis": "rigid_part_transform"' in source
    assert '"deformation_bounds": Vector2.ZERO' in source
    assert "func deformation_bounds() -> Dictionary:" in source
    assert "func resident_hashes() -> Array[String]:" in source
    assert "rigid_p0_object_id_duplicate" in source
    assert "source_id != int(instance.get(\"source_id\", 0))" in source
    assert not __import__("re").search(r"\bvar\s+\w+\s*:=", source)
