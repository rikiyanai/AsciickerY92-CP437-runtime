"""Proof that approved retained stone bakes carry topology without changing faces."""

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BAKER_PATH = ROOT / "scripts/bake_akm_to_static_world_faces.py"

CASES = (
    {
        "asset": ROOT / "godot_project/assets/world_mesh/female_hand_statue_mossy_faces.json",
        "source": ROOT / "assets/meshes/incoming/female_hand.akm",
        "mode": "statue",
        "target_size": 30.0,
        "moss_threshold": 0.15,
        "up_axis": "-y",
    },
    {
        "asset": ROOT / "godot_project/assets/world_mesh/mossy_rock_faces.json",
        "source": ROOT / "assets/meshes/incoming/mossy_rock.akm",
        "mode": "rock",
        "target_size": 2.4,
        "moss_threshold": None,
        "up_axis": "+y",
    },
)


def _load_baker():
    spec = importlib.util.spec_from_file_location("static_face_baker", BAKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _without_topology(rows):
    return [value for index, value in enumerate(rows) if index % 16 not in (11, 15)]


def _role_counts(rows):
    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for packed in rows[11::16]:
        packed = int(packed)
        assert 0 <= packed < 64
        for edge in range(3):
            counts[(packed >> (edge * 2)) & 3] += 1
    return counts


def test_approved_stone_bakes_preserve_geometry_and_materials(tmp_path):
    baker = _load_baker()
    for case in CASES:
        existing = json.loads(case["asset"].read_text())
        output = tmp_path / case["asset"].name
        baker.bake(
            case["source"], output, case["target_size"], 0.82,
            case["mode"], moss_threshold=case["moss_threshold"],
            up_axis=case["up_axis"], stone_topology=True,
        )
        regenerated = json.loads(output.read_text())
        assert _without_topology(existing["rows"]) == _without_topology(regenerated["rows"])
        assert existing["face_count"] == regenerated["face_count"]
        assert existing["mats"] == regenerated["mats"]
        assert existing["dimensions_xyz"] == regenerated["dimensions_xyz"]


def test_approved_stone_bakes_publish_marker_and_real_roles():
    for case in CASES:
        doc = json.loads(case["asset"].read_text())
        assert doc["stone_topology"] == {
            "version": 1,
            "marker": 4359,
            "edge_roles": "2bits-per-edge: smooth/convex/concave/boundary",
        }
        assert set(doc["rows"][15::16]) == {4359.0}
        counts = _role_counts(doc["rows"])
        assert sum(counts[role] for role in (1, 2, 3)) > 0
