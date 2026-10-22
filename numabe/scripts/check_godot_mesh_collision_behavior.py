#!/usr/bin/env python3
"""Fail-closed analyzer for the GODOT-T07 headed mesh-collision route.

The route receipt is produced by the real headed Godot Colyseus client.  This
module only interprets that receipt; it never treats source presence, triangle
counts, or a debug transform as collision evidence.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


SCHEMA = "godot.mesh_collision_behavior.receipt.v1"
FEATURE_ID = "GODOT-T07"
ROUTE_IDENTITY = "godot_colyseus_authored_mesh_collision_behavior_v1"
HISTORICAL_MAP = "assets/a3d/game_map_y8.a3d"
SHIPPING_MAP = "godot_project/assets/maps/game_map_y8.a3d"

DEFAULT_TARGET = "wall"

# GODOT-T07 TARGET TABLE (FL-4740).  One row per collision target; the receipt
# selects a row through `target_id` (top level or inside `mesh`).  A receipt
# without that field is read as the historical `wall` row, so every pre-FL-4740
# receipt is interpreted exactly as before.
#
# `center` + `normal` are the SIGNED DISTANCE plane, measured from the target's
# CENTRE rather than its blocking face, so a block keeps the start sign and a
# pass-through flips it.  `band` is the endpoint bound: the target's own
# footprint plus a ~1 unit actor-radius and snapshot-quantization margin.
#
# The `pieta` row mirrors the server-owned obstacle
# `colyseus_server/src/sim/world.ts` STATIC_WORLD_OBSTACLES id "pieta_statue":
# box centre (133.0, -74.679), half-extents (10.47, 10.72), so the +z blocking
# face is z = -63.959 and the -z face is z = -85.399.  No authored .akm face
# index owns it, hence face -1.
TARGETS: dict[str, dict[str, Any]] = {
    "wall": {
        "shape": "authored_mesh_face",
        "mesh_name": "sac_and_math_color.akm",
        "mesh_path": "assets/meshes/sac_and_math_color.akm",
        "face": 1793,
        "center": (-4.09, -39.77),
        "normal": (0.988, -0.153),
        "half": (0.0, 0.0),
        "front_from": (1.838, -40.688),
        "front_to": (-6.066, -39.464),
        "reverse_from": (-10.018, -38.852),
        "reverse_to": (-2.114, -40.076),
        "band_x": (-7.0, -1.0),
        "band_z": (-45.0, -33.0),
        "control_from": (0.0, 0.0),
        "control_to": (0.0, 0.0),
    },
    "pieta": {
        "shape": "server_static_world_obstacle_box",
        "mesh_name": "pieta_statue_mossy_faces.json",
        "mesh_path": "godot_project/assets/world_mesh/pieta_statue_mossy_faces.json",
        "face": -1,
        "center": (133.0, -74.679),
        "normal": (0.0, 1.0),
        "half": (10.47, 10.72),
        # FL-4740: approach length is a TICK BUDGET, not a geometry choice.
        # The first headed pieta run stopped ~1.4-2.1 units short of both faces.
        # That was not terrain: advanceWorld applies no slope or step limit to
        # horizontal motion (the uphill branch is an unbounded Math.max,
        # colyseus_server/src/sim/world.ts ~:2916) and stops horizontally only
        # on a null terrain sample or staticWorldObstacleAt.  Replaying both
        # legs against the shipping game_map_y8.a3d with the server's own
        # advanceWorld blocks at both faces exactly, after 36 and 38 ticks,
        # while the driver's 64-frame press delivered only ~29 server ticks.
        # These shortened legs reach the face in 17 and 19 ticks.
        "front_from": (133.0, -60.0),
        "front_to": (133.0, -72.0),
        "reverse_from": (133.0, -90.0),
        "reverse_to": (133.0, -72.0),
        "band_x": (121.53, 144.47),
        "band_z": (-86.399, -62.959),
        "control_from": (110.0, -72.0),
        "control_to": (118.0, -72.0),
    },
}

# Default-target aliases.  These names are the existing public surface of this
# module (scripts/tests/test_godot_mesh_collision_behavior.py binds to them);
# they are derived from the table above so there is still one owner per value.
MESH_PATH = TARGETS[DEFAULT_TARGET]["mesh_path"]
MESH_NAME = TARGETS[DEFAULT_TARGET]["mesh_name"]
MESH_FACE = TARGETS[DEFAULT_TARGET]["face"]
WALL_CENTER = TARGETS[DEFAULT_TARGET]["center"]
WALL_NORMAL = TARGETS[DEFAULT_TARGET]["normal"]
WALL_FRONT_FROM = TARGETS[DEFAULT_TARGET]["front_from"]
WALL_FRONT_TO = TARGETS[DEFAULT_TARGET]["front_to"]
WALL_REVERSE_FROM = TARGETS[DEFAULT_TARGET]["reverse_from"]
WALL_REVERSE_TO = TARGETS[DEFAULT_TARGET]["reverse_to"]

# Thresholds are target-independent: they are actor-scale and snapshot-scale
# facts, not geometry facts, so the pieta box reuses them unchanged.
MIN_BLOCKED_SIGNED_DISTANCE = 1.0
MIN_CROSSED_SIGNED_DISTANCE = 0.20
SIGNED_DISTANCE_TOLERANCE = 0.20
MIN_CONTROL_DISPLACEMENT = 0.25
MIN_APPROACH_INTENDED_DISPLACEMENT = 0.25
REQUIRED_SIDES = ("front", "reverse")


def target_row(target: str) -> dict[str, Any]:
    """Return the geometry row for ``target``, falling back to the wall row."""
    return TARGETS.get(target, TARGETS[DEFAULT_TARGET])


def expected_poses(target: str = DEFAULT_TARGET) -> dict[str, tuple[tuple[float, float], tuple[float, float]]]:
    row = target_row(target)
    return {
        "front": (row["front_from"], row["front_to"]),
        "reverse": (row["reverse_from"], row["reverse_to"]),
    }


def receipt_target(receipt: dict[str, Any]) -> str:
    """Resolve the receipt's target id, fail-open to the historical wall row."""
    candidate = receipt.get("target_id")
    if not isinstance(candidate, str):
        mesh = receipt.get("mesh")
        candidate = mesh.get("target_id") if isinstance(mesh, dict) else None
    return candidate if isinstance(candidate, str) and candidate in TARGETS else DEFAULT_TARGET


EXPECTED_POSES = expected_poses(DEFAULT_TARGET)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _vec3(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, dict):
        values = (value.get("x"), value.get("y"), value.get("z"))
    elif isinstance(value, (list, tuple)) and len(value) == 3:
        values = tuple(value)
    else:
        return None
    return tuple(float(part) for part in values) if all(_finite(part) for part in values) else None


def _vec2(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict):
        values = (value.get("x"), value.get("z"))
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        values = tuple(value)
    else:
        return None
    return tuple(float(part) for part in values) if all(_finite(part) for part in values) else None


def _distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(len(a))))


def _near(a: tuple[float, ...], b: tuple[float, ...], tolerance: float = 0.01) -> bool:
    return len(a) == len(b) and _distance(a, b) <= tolerance


def _xz(value: tuple[float, float, float]) -> tuple[float, float]:
    return value[0], value[2]


def _signed_wall_distance(position: tuple[float, float, float], target: str = DEFAULT_TARGET) -> float:
    x, z = _xz(position)
    row = target_row(target)
    center, normal = row["center"], row["normal"]
    return (x - center[0]) * normal[0] + (z - center[1]) * normal[1]


def _inside_wall_band(position: tuple[float, float, float], target: str = DEFAULT_TARGET) -> bool:
    x, z = _xz(position)
    # The target's projected bounds, with a one-unit actor-radius and
    # snapshot-quantization margin.  This keeps the verdict tied to the target's
    # own footprint while allowing an endpoint just beyond its exact vertices.
    row = target_row(target)
    band_x, band_z = row["band_x"], row["band_z"]
    return band_x[0] <= x <= band_x[1] and band_z[0] <= z <= band_z[1]


def _fail(reason: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "feature_id": FEATURE_ID,
        "route_identity": ROUTE_IDENTITY,
        "outcome": "setup_failure",
        "pass": False,
        "reason": reason,
        "acceptance": [],
    }
    if details:
        result.update(details)
    return result


def _check_common(receipt: dict[str, Any], target: str = DEFAULT_TARGET) -> list[str]:
    row = target_row(target)
    failures: list[str] = []
    if receipt.get("schema") != SCHEMA:
        failures.append("schema_missing_or_mismatched")
    if receipt.get("feature_id") != FEATURE_ID:
        failures.append("feature_id_missing_or_mismatched")
    if receipt.get("route_identity") != ROUTE_IDENTITY:
        failures.append("route_identity_missing_or_mismatched")
    source = receipt.get("source")
    if (
        not isinstance(source, dict)
        or not source.get("git_head")
        or not _sha256(source.get("worktree_diff_sha256"))
    ):
        failures.append("source_provenance_missing")
    authority = receipt.get("authority")
    if not isinstance(authority, dict) or authority.get("kind") != "colyseus_server":
        failures.append("colyseus_authority_missing")
    if not isinstance(authority, dict) or authority.get("snapshot_position_field") != "actors[*].position":
        failures.append("authoritative_position_field_missing")
    if not isinstance(authority, dict) or authority.get("input_path") != "InputEventKey->InputMap->player._process_colyseus_intent_only->Main.notify_local_intent":
        failures.append("real_input_path_provenance_missing")
    target = receipt.get("target")
    if (
        not isinstance(target, dict)
        or target.get("scene") != "res://scenes/Main.tscn"
        or target.get("headed") is not True
        or target.get("mode") != "colyseus-client"
    ):
        failures.append("headed_shipping_scene_identity_missing")
    map_info = receipt.get("map")
    if (
        not isinstance(map_info, dict)
        or map_info.get("runtime_path") != "res://assets/maps/game_map_y8.a3d"
        or not _sha256(map_info.get("runtime_sha256"))
    ):
        failures.append("shipping_map_identity_missing")
    mesh = receipt.get("mesh")
    if not isinstance(mesh, dict):
        failures.append("historical_mesh_identity_missing")
    else:
        face = mesh.get("face", None)
        if mesh.get("name") != row["mesh_name"] or not isinstance(face, (int, float)) or int(face) != row["face"]:
            failures.append("historical_mesh_face_identity_missing")
        if mesh.get("source_path") != row["mesh_path"] or not _sha256(mesh.get("source_sha256")):
            failures.append("historical_mesh_provenance_missing")
        center = _vec2(mesh.get("wall_center"))
        normal = _vec2(mesh.get("wall_normal"))
        if center is None or normal is None or not _near(center, row["center"]) or not _near(normal, row["normal"]):
            failures.append("canonical_wall_geometry_missing")
    return failures


def _check_choreography(side: str, row: Any, target: str = DEFAULT_TARGET) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    if not isinstance(row, dict):
        return [f"{side}_choreography_missing"], {"side": side, "result": "unavailable"}
    poses = expected_poses(target)
    start = _vec3(row.get("start_authoritative_position"))
    end = _vec3(row.get("end_authoritative_position"))
    intended = _vec3(row.get("intended_displacement"))
    actual = _vec3(row.get("actual_displacement"))
    expected_from, expected_to = poses[side]
    requested_from = _vec2(row.get("start_requested_xz"))
    requested_to = _vec2(row.get("target_requested_xz"))
    if start is not None and not _near(_xz(start), expected_from, 0.08):
        failures.append(f"{side}_start_authoritative_pose_mismatch")
    if requested_from is None or not _near(requested_from, expected_from, 0.01):
        failures.append(f"{side}_requested_start_pose_mismatch")
    if requested_to is None or not _near(requested_to, expected_to, 0.01):
        failures.append(f"{side}_requested_target_pose_mismatch")
    if start is None:
        failures.append(f"{side}_start_authoritative_position_missing")
    if end is None:
        failures.append(f"{side}_end_authoritative_position_missing")
    if intended is None or _distance(intended, (0.0, 0.0, 0.0)) < MIN_APPROACH_INTENDED_DISPLACEMENT:
        failures.append(f"{side}_intended_displacement_missing_or_zero")
    if actual is None:
        failures.append(f"{side}_actual_displacement_missing")
    if not isinstance(row.get("input_events"), list) or not row["input_events"]:
        failures.append(f"{side}_input_choreography_missing")
    if row.get("direct_transform_mutation") is not False:
        failures.append(f"{side}_direct_transform_mutation_not_false")
    if row.get("authority_snapshot_count", 0) < 2:
        failures.append(f"{side}_authoritative_snapshot_window_missing")
    if start is not None and end is not None and actual is not None:
        computed = tuple(end[i] - start[i] for i in range(3))
        if _distance(computed, actual) > 0.15:
            failures.append(f"{side}_actual_displacement_does_not_match_positions")
    signed = row.get("end_signed_wall_distance")
    if not _finite(signed):
        failures.append(f"{side}_end_wall_distance_missing")
    elif end is not None and abs(float(signed) - _signed_wall_distance(end, target)) > SIGNED_DISTANCE_TOLERANCE:
        failures.append(f"{side}_end_wall_distance_does_not_match_position")
    contact = row.get("contact_fact")
    if not isinstance(contact, dict) or contact.get("available") is not False or not contact.get("absence_reason"):
        failures.append(f"{side}_contact_absence_fact_missing")
    start_signed = _signed_wall_distance(start, target) if start is not None else math.nan
    row["start_signed_wall_distance"] = start_signed
    row["wall_band_observed"] = _inside_wall_band(end, target) if end is not None else False
    # The wall normal has a fixed orientation in the canonical evidence.  A
    # pass-through is therefore an authoritative endpoint on the opposite
    # signed side, not merely an endpoint close to the face.  This also handles
    # a server that continues beyond the historical face instead of stopping
    # inside its projected vertex bounds.
    row["pass_through_observed"] = bool(
        _finite(start_signed)
        and _finite(signed)
        and abs(float(start_signed)) >= MIN_CROSSED_SIGNED_DISTANCE
        and abs(float(signed)) >= MIN_CROSSED_SIGNED_DISTANCE
        and float(start_signed) * float(signed) < 0.0
        and row["wall_band_observed"]
    )
    row["blocked_observed"] = bool(
        _finite(start_signed)
        and _finite(signed)
        and abs(float(start_signed)) >= MIN_BLOCKED_SIGNED_DISTANCE
        and abs(float(signed)) >= MIN_BLOCKED_SIGNED_DISTANCE
        and float(start_signed) * float(signed) > 0.0
        and row["wall_band_observed"]
    )
    row["result"] = "pass_through" if row["pass_through_observed"] else "blocked" if row["blocked_observed"] else "inconclusive"
    return failures, row


def check_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return the machine verdict without throwing on malformed evidence."""
    if not isinstance(receipt, dict):
        return _fail("receipt_root_not_object")
    # FL-4740: every geometry law below is read from the receipt's own target
    # row.  A receipt with no target id is the historical wall run.
    target = receipt_target(receipt)
    failures = _check_common(receipt, target)
    sides = receipt.get("choreographies")
    if not isinstance(sides, dict):
        return _fail("choreographies_missing", {"contract_failures": failures, "target_id": target})
    checked: dict[str, Any] = {}
    for side in REQUIRED_SIDES:
        side_failures, checked_row = _check_choreography(side, sides.get(side), target)
        failures.extend(side_failures)
        checked[side] = checked_row
    control = receipt.get("free_space_control")
    if not isinstance(control, dict):
        failures.append("free_space_control_missing")
    else:
        control_start = _vec3(control.get("start_authoritative_position"))
        control_end = _vec3(control.get("end_authoritative_position"))
        control_actual = _vec3(control.get("actual_displacement"))
        if control_start is None or control_end is None or control_actual is None:
            failures.append("free_space_authoritative_positions_missing")
        elif _distance(control_start, control_end) < MIN_CONTROL_DISPLACEMENT:
            failures.append("free_space_control_no_motion")
        if not isinstance(control.get("input_events"), list) or not control["input_events"]:
            failures.append("free_space_input_choreography_missing")
        if control.get("direct_transform_mutation") is not False:
            failures.append("free_space_direct_transform_mutation_not_false")
        if control_start is not None and control_end is not None and control_actual is not None:
            computed_control = tuple(control_end[index] - control_start[index] for index in range(3))
            if _distance(computed_control, control_actual) > 0.15:
                failures.append("free_space_actual_displacement_does_not_match_positions")
    runtime_mesh = receipt.get("runtime_mesh_observation")
    if not isinstance(runtime_mesh, dict):
        failures.append("runtime_mesh_observation_missing")
    else:
        for side in REQUIRED_SIDES:
            facts = runtime_mesh.get(side)
            if not isinstance(facts, dict):
                failures.append(f"{side}_runtime_mesh_observation_missing")
            elif "runtime_mesh_node_present" not in facts or "runtime_mesh_collision_present" not in facts:
                failures.append(f"{side}_runtime_mesh_presence_facts_missing")
    if failures:
        return _fail("contract_failure", {"contract_failures": failures, "choreographies": checked, "target_id": target})
    if all(checked[side]["blocked_observed"] for side in REQUIRED_SIDES) and _distance(
        _vec3(control["start_authoritative_position"]), _vec3(control["end_authoritative_position"])
    ) >= MIN_CONTROL_DISPLACEMENT:
        return {
            **receipt,
            "target_id": target,
            "outcome": "pass",
            "pass": True,
            "reason": "both_sides_blocked_and_free_space_moved",
            "choreographies": checked,
            "acceptance": [
                {"requirement": "front approach blocked", "result": "pass"},
                {"requirement": "reverse approach blocked", "result": "pass"},
                {"requirement": "free-space control moved", "result": "pass"},
            ],
        }
    if all(checked[side]["pass_through_observed"] for side in REQUIRED_SIDES) and _distance(
        _vec3(control["start_authoritative_position"]), _vec3(control["end_authoritative_position"])
    ) >= MIN_CONTROL_DISPLACEMENT:
        return {
            **receipt,
            "target_id": target,
            "outcome": "regression",
            "pass": False,
            "reason": "authoritative_colyseus_player_passed_through_historical_mesh_from_both_sides",
            "choreographies": checked,
            "acceptance": [
                {"requirement": "front approach blocked", "result": "fail", "falsifier": "front pass-through observed"},
                {"requirement": "reverse approach blocked", "result": "fail", "falsifier": "reverse pass-through observed"},
                {"requirement": "free-space control moved", "result": "pass"},
            ],
        }
    return _fail("collision_outcome_inconclusive", {"choreographies": checked, "target_id": target})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        verdict = _fail(f"receipt_unreadable:{exc}")
    else:
        verdict = check_receipt(receipt)
    output = json.dumps(verdict, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0 if verdict.get("pass") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
