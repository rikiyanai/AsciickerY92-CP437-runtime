from __future__ import annotations

import json
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "godot_mesh_collision_behavior_checker",
    ROOT / "scripts" / "check_godot_mesh_collision_behavior.py",
)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)
SCHEMA_PATH = ROOT / "scripts" / "schemas" / "godot_mesh_collision_behavior.receipt.v1.schema.json"


def _position(x: float, z: float) -> list[float]:
    return [x, 0.0, z]


def _row(start: tuple[float, float], end: tuple[float, float], side: str) -> dict:
    start_3 = _position(*start)
    end_3 = _position(*end)
    return {
        "side": side,
        "input_events": [
            {"kind": "InputEventKey", "key": "D", "pressed": True},
            {"kind": "InputEventKey", "key": "D", "pressed": False},
        ],
        "start_requested_xz": list(CHECKER.WALL_FRONT_FROM if side == "front" else CHECKER.WALL_REVERSE_FROM),
        "target_requested_xz": list(CHECKER.WALL_FRONT_TO if side == "front" else CHECKER.WALL_REVERSE_TO),
        "start_authoritative_position": start_3,
        "end_authoritative_position": end_3,
        "intended_displacement": [end_3[0] - start_3[0], 0.0, end_3[2] - start_3[2]],
        "actual_displacement": [end_3[0] - start_3[0], 0.0, end_3[2] - start_3[2]],
        "end_signed_wall_distance": CHECKER._signed_wall_distance(tuple(end_3)),
        "authority_snapshot_count": 42,
        "direct_transform_mutation": False,
        "contact_fact": {
            "available": False,
            "absence_reason": "colyseus_actor_schema_exposes_no_contact_collider_or_normal_field",
        },
    }


def _receipt(front: dict | None = None, reverse: dict | None = None, control_motion: float = 1.0) -> dict:
    control_start = _position(0.0, 0.0)
    control_end = _position(control_motion, 0.0)
    return {
        "schema": CHECKER.SCHEMA,
        "feature_id": CHECKER.FEATURE_ID,
        "route_identity": CHECKER.ROUTE_IDENTITY,
        "source": {"git_head": "571b89fc12fc74698d0657ef6b434332e0430b1a", "worktree_diff_sha256": "a" * 64},
        "target": {"headed": True, "scene": "res://scenes/Main.tscn", "mode": "colyseus-client"},
        "map": {"runtime_path": "res://assets/maps/game_map_y8.a3d", "runtime_sha256": "c" * 64},
        "mesh": {
            "name": CHECKER.MESH_NAME,
            "source_path": CHECKER.MESH_PATH,
            "source_sha256": "b" * 64,
            "face": CHECKER.MESH_FACE,
            "wall_center": list(CHECKER.WALL_CENTER),
            "wall_normal": list(CHECKER.WALL_NORMAL),
        },
        "authority": {
            "kind": "colyseus_server",
            "snapshot_position_field": "actors[*].position",
            "input_path": "InputEventKey->InputMap->player._process_colyseus_intent_only->Main.notify_local_intent",
        },
        "runtime_mesh_observation": {
            "front": {"runtime_mesh_node_present": False, "runtime_mesh_collision_present": False},
            "reverse": {"runtime_mesh_node_present": False, "runtime_mesh_collision_present": False},
        },
        "choreographies": {
            "front": front if front is not None else _row((1.838, -40.688), (-5.0, -39.8), "front"),
            "reverse": reverse if reverse is not None else _row((-10.018, -38.852), (-3.0, -39.8), "reverse"),
        },
        "free_space_control": {
            "input_events": [{"kind": "InputEventKey", "key": "A", "pressed": True}],
            "start_authoritative_position": control_start,
            "end_authoritative_position": control_end,
            "actual_displacement": [control_motion, 0.0, 0.0],
            "direct_transform_mutation": False,
        },
    }


def test_both_sides_block_and_free_space_motion_is_required_for_pass() -> None:
    # A blocked fixture must end outside the canonical wall band with a retained
    # positive signed distance; only the free-space control is allowed to move.
    receipt = _receipt()
    for side, row in receipt["choreographies"].items():
        row["end_authoritative_position"] = _position(-2.5 if side == "front" else -6.5, -40.0)
        row["actual_displacement"] = [
            row["end_authoritative_position"][0] - row["start_authoritative_position"][0],
            0.0,
            row["end_authoritative_position"][2] - row["start_authoritative_position"][2],
        ]
        row["end_signed_wall_distance"] = CHECKER._signed_wall_distance(tuple(row["end_authoritative_position"]))
    verdict = CHECKER.check_receipt(receipt)
    assert verdict["outcome"] == "pass"
    assert verdict["pass"] is True


def test_missing_collision_provenance_is_rejected_fail_closed() -> None:
    receipt = _receipt()
    del receipt["runtime_mesh_observation"]
    verdict = CHECKER.check_receipt(receipt)
    assert verdict["outcome"] == "setup_failure"
    assert "runtime_mesh_observation_missing" in verdict["contract_failures"]


def test_malformed_wall_geometry_is_rejected_without_throwing() -> None:
    receipt = _receipt()
    receipt["mesh"]["wall_center"] = None
    verdict = CHECKER.check_receipt(receipt)
    assert verdict["outcome"] == "setup_failure"
    assert "canonical_wall_geometry_missing" in verdict["contract_failures"]


def test_one_sided_collision_cannot_be_promoted_to_pass() -> None:
    receipt = _receipt()
    receipt["choreographies"]["front"]["end_signed_wall_distance"] = 1.4
    receipt["choreographies"]["front"]["end_authoritative_position"] = _position(-2.5, -40.0)
    start = receipt["choreographies"]["front"]["start_authoritative_position"]
    end = receipt["choreographies"]["front"]["end_authoritative_position"]
    receipt["choreographies"]["front"]["actual_displacement"] = [end[i] - start[i] for i in range(3)]
    receipt["choreographies"]["front"]["end_signed_wall_distance"] = CHECKER._signed_wall_distance(tuple(end))
    verdict = CHECKER.check_receipt(receipt)
    assert verdict["outcome"] == "setup_failure"
    assert verdict["reason"] == "collision_outcome_inconclusive"


def test_no_motion_control_cannot_be_a_false_pass() -> None:
    receipt = _receipt(control_motion=0.0)
    for row in receipt["choreographies"].values():
        row["end_signed_wall_distance"] = 1.4
        row["end_authoritative_position"] = _position(-2.5 if row["side"] == "front" else -6.5, -40.0)
        row["actual_displacement"] = [
            row["end_authoritative_position"][0] - row["start_authoritative_position"][0],
            0.0,
            row["end_authoritative_position"][2] - row["start_authoritative_position"][2],
        ]
    verdict = CHECKER.check_receipt(receipt)
    assert verdict["outcome"] == "setup_failure"
    assert "free_space_control_no_motion" in verdict["contract_failures"]


def test_direct_transform_mutation_cannot_be_authority_evidence() -> None:
    receipt = _receipt()
    receipt["choreographies"]["front"]["direct_transform_mutation"] = True
    verdict = CHECKER.check_receipt(receipt)
    assert verdict["outcome"] == "setup_failure"
    assert "front_direct_transform_mutation_not_false" in verdict["contract_failures"]


def test_pass_through_from_both_sides_is_a_measured_regression() -> None:
    verdict = CHECKER.check_receipt(_receipt())
    assert verdict["outcome"] == "regression"
    assert verdict["pass"] is False
    assert "passed_through" in verdict["reason"]


def test_receipt_schema_requires_authoritative_route_truth() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["route_identity"]["const"] == CHECKER.ROUTE_IDENTITY
    required = set(schema["required"])
    assert {"authority", "choreographies", "free_space_control", "runtime_mesh_observation"} <= required
