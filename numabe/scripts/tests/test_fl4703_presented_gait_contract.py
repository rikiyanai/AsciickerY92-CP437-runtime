"""Source and product contracts for FL-4703 presented actor gait.

These checks stay offline. They do not instantiate the Godot scene or claim a
headed/runtime result; the small GDScript harness remains the executable
consumer check. The Python layer pins the handoff between the presentation law,
the authored distance curves, and the two pose consumers so an axis or lifecycle
regression is caught before a capture is attempted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORLD = (ROOT / "godot_project/scripts/World.gd").read_text(encoding="utf-8")
GAIT = (ROOT / "godot_project/scripts/visual/ActorLocomotionPresentation.gd").read_text(
    encoding="utf-8"
)
POSE_DB = (ROOT / "godot_project/render/scene/geometry/actors/ActorPoseGeometryDB.gd").read_text(
    encoding="utf-8"
)
RIG = (ROOT / "godot_project/scripts/visual/ActorRigPresentationAdapter.gd").read_text(
    encoding="utf-8"
)
STACK = (ROOT / "godot_project/scripts/visual/ActorLayerStack.gd").read_text(
    encoding="utf-8"
)
BAKE = (ROOT / "scripts/bake_xbot_rig_poses.py").read_text(encoding="utf-8")
PRODUCT_PATH = ROOT / "godot_project/assets/actor_mesh/xbot_rig_poses.json"
GODOT_HARNESS = (ROOT / "godot_project/scripts/tests/test_presented_actor_gait.gd").read_text(
    encoding="utf-8"
)


def _function(source: str, name: str) -> str:
    start = source.index(f"func {name}(")
    end = source.find("\nfunc ", start + 1)
    return source[start:] if end < 0 else source[start:end]


def test_world_feeds_presented_yaw_into_directional_gait() -> None:
    body = _function(WORLD, "_present_replicated_actors_and_gait")
    call = re.search(r"_actor_locomotion\.sample\((.*?)\)\n", body, re.S)
    assert call, "World must sample the shared locomotion cursor"
    # Directional selection is relative to the yaw that was actually presented
    # this frame. Omitting this argument silently falls back to world yaw 0.
    assert "node.get(\"facing_yaw\")" in call.group(1)


def test_directional_basis_is_actor_local_not_world_forward() -> None:
    assert "Vector2(sin(yaw), cos(yaw))" in GAIT
    assert "Vector2(cos(yaw), -sin(yaw))" in GAIT
    assert "direction.dot(Vector2(sin(yaw), cos(yaw)))" in GAIT
    assert "direction.dot(Vector2(cos(yaw), -sin(yaw)))" in GAIT


def test_authored_bake_has_all_directional_clips_and_distance_curves() -> None:
    clips = re.search(r"CLIPS = \{(.*?)\n\}", BAKE, re.S)
    assert clips, "the offline compiler must declare its source clip table"
    clip_table = clips.group(1)
    for clip_id in ("walk_left", "walk_right", "run_left", "run_right", "walk_back", "run_back"):
        assert f'"{clip_id}"' in clip_table
    assert "math.hypot(shift.x, shift.y)" in BAKE
    assert 'markers["cycle_distance_world"]' in BAKE
    assert 'markers["distance_phase"]' in BAKE
    # The archived Blender coordinate convention maps positive local X to the
    # authored left strafe and negative local X to the right strafe.
    assert 'selector += "_left" if side > 0.0 else "_right"' in GAIT


def test_product_contains_directional_distance_markers_after_bake() -> None:
    product = json.loads(PRODUCT_PATH.read_text(encoding="utf-8"))
    clips = product.get("animation_clips", {})
    for clip_id in ("walk_left", "walk_right", "run_left", "run_right", "walk_back", "run_back"):
        assert clip_id in clips
        markers = clips[clip_id].get("markers", {})
        frame_count = len(clips[clip_id].get("frame_pose_ids", []))
        assert float(markers.get("cycle_distance_world", 0.0)) > 0.05
        distances = markers.get("distance_phase", [])
        assert len(distances) == frame_count + 1
        assert distances[0] == 0.0
        assert distances[-1] == 1.0
        assert all(a <= b for a, b in zip(distances, distances[1:]))


def test_distance_curve_inversion_admits_the_final_loop_frame() -> None:
    body = _function(POSE_DB, "_resolve_pose_selector")
    gait = body.split('"gait":', 1)[1].split('"windup":', 1)[0]
    # N frame IDs have N+1 boundaries. The final marker is a sentinel, so the
    # current bound reaches bin N-1 and stops before that sentinel. Phase 1.0
    # itself wraps to frame zero. Keep this as a small arithmetic contract; the
    # strict native runner owns the actual PoseDB selector invocation.
    assert "index + 1 < distances.size() - 1" in gait
    for frame_count in (2, 3, 10, 15):
        index = 0
        boundary_count = frame_count + 1
        while index + 1 < boundary_count - 1:
            index += 1
        assert index == frame_count - 1


def test_gait_lifecycle_and_server_state_ownership_are_explicit() -> None:
    world_gait = _function(WORLD, "_present_replicated_actors_and_gait")
    assert "_replicated_actor_laws.erase(key)" in world_gait
    assert "_actor_locomotion.retain_keys(live_gaits)" in world_gait
    assert "place_presented_actor(node" in world_gait
    assert "actor.position =" not in world_gait
    assert "actor.velocity =" not in world_gait
    assert "func clear()" in GAIT
    assert "func retain_keys(keys: Dictionary)" in GAIT
    assert "distance > WARP_DISTANCE" in GAIT
    assert "elapsed < 0.0" in GAIT
    assert "_presented_facing_yaw" in STACK
    assert "_last_actor_state.facing_yaw =" not in STACK


def test_pose_consumers_use_shared_presented_gait_phase() -> None:
    assert 'locomotion.get("selector", action)' in RIG
    assert 'locomotion.get("phase", state.feedback.get("gait_phase", 0.0))' in RIG
    assert 'instance["animation_progress"] = locomotion.get("phase"' in RIG
    assert "set_local_presentation_motion" in WORLD


def test_existing_headless_harness_covers_rate_stop_warp_and_immutability() -> None:
    assert "for frames in [10, 60, 144]" in GODOT_HARNESS
    assert "stationary root freezes gait" in GODOT_HARNESS
    assert "teleport rebases" in GODOT_HARNESS
    assert "presentation yaw cannot mutate server mirror" in GODOT_HARNESS
