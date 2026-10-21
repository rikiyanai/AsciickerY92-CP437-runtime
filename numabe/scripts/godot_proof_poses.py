#!/usr/bin/env python3
"""Compatibility reader for the Godot proof-pose registry.

FL-4781: config/godot_proof_pose_registry.json is the single mutable proof/pose
owner. This module preserves the legacy TEST_LOCATIONS API for existing proof
runners, but it no longer owns the pose table.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class GodotProofPose:
    player_pos: str | None
    default_yaws: str | None = None
    operator_locked: bool = False
    source: str = ""


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "godot_proof_pose_registry.json"


def _format_player_position(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"invalid player_position in {REGISTRY_PATH}: {value!r}")
    return ",".join(str(part) for part in value)


def _load_legacy_poses() -> tuple[str, str, dict[str, GodotProofPose]]:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    defaults = data.get("defaults", {})
    default_yaws = str(defaults.get("legacy_default_yaws", "0,30,60,90,120,150,180,210,240,270,300,330"))
    default_location_set = str(defaults.get("legacy_default_location_set", "flat_default,elevated_grass"))
    poses: dict[str, GodotProofPose] = {}
    for row in data.get("poses", []):
        legacy_name = row.get("legacy_location_name")
        if not legacy_name:
            continue
        poses[str(legacy_name)] = GodotProofPose(
            player_pos=_format_player_position(row.get("player_position")),
            default_yaws=row.get("default_yaws"),
            operator_locked=bool(row.get("operator_locked", False)),
            source=str(row.get("source", "")),
        )
    if not poses:
        raise ValueError(f"{REGISTRY_PATH} has no legacy_location_name pose rows")
    return default_yaws, default_location_set, poses


DEFAULT_YAWS, DEFAULT_LOCATION_SET, GODOT_PROOF_POSES = _load_legacy_poses()

TEST_LOCATIONS: dict[str, str | None] = {
    name: pose.player_pos for name, pose in GODOT_PROOF_POSES.items()
}


def pose_for_location(name: str) -> GodotProofPose:
    return GODOT_PROOF_POSES[name]


def resolve_yaws_for_location(
    location_name: str,
    requested_yaws: str | None,
    *,
    allow_pose_yaw_override: bool = False,
) -> str:
    pose = pose_for_location(location_name)
    if requested_yaws:
        if pose.operator_locked and pose.default_yaws and requested_yaws != pose.default_yaws and not allow_pose_yaw_override:
            raise SystemExit(
                "OPERATOR_PROOF_POSE_YAW_MISMATCH\n"
                f"location={location_name}\n"
                f"requested_yaws={requested_yaws}\n"
                f"required_yaws={pose.default_yaws}\n"
                f"source={pose.source}\n"
                "pass --allow-pose-yaw-override for diagnostic captures only"
            )
        return requested_yaws
    return pose.default_yaws or DEFAULT_YAWS
